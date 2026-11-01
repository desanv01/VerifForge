"""Portable source/file checks. No registration, installation or validator run."""
import ast
import base64
import csv
import hashlib
import io
import json
from pathlib import Path
import socket
import sqlite3
import stat
import subprocess
from types import SimpleNamespace
import zipfile

import pytest

from veriforge.native_validator_registration import registration as reg

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
ASSETS = Path(reg.__file__).resolve().parent
PREFIX = "src/veriforge/native_validator_registration/"


@pytest.fixture(autouse=True)
def no_external_operations(monkeypatch):
    def forbidden(*args, **kwargs): pytest.fail("No process/network/database operation authorized")
    monkeypatch.setattr(subprocess, "run", forbidden)
    monkeypatch.setattr(subprocess, "Popen", forbidden)
    monkeypatch.setattr(socket, "create_connection", forbidden)
    monkeypatch.setattr(sqlite3, "connect", forbidden)


def test_exact_inert_source_asset_vector():
    raw = (HERE/"asset-hashes.json").read_bytes()
    assert hashlib.sha256(raw).hexdigest() == "1bfc0b1accf1a315224b6c91670e42e8ed1c9624a57dc56aa89e9bae1e039966"
    pins = json.loads(raw)
    actual = {PREFIX+p.relative_to(ASSETS).as_posix() for p in ASSETS.rglob("*")
        if p.is_file() and "__pycache__" not in p.parts and p.suffix not in (".pyc", ".pyo")}
    assert len(pins) == 137 and actual == set(pins)
    for name, expected in pins.items():
        data = (ASSETS/name.removeprefix(PREFIX)).read_bytes()
        assert hashlib.sha256(data).hexdigest() == expected
        assert not name.endswith((".pyd", ".dll", ".so", ".exe", ".whl", ".db", ".sqlite"))
        assert b"C:/Users/" not in data and b"C:\\Users\\" not in data


def test_package_import_and_templates_are_inert_assets():
    # Retained legacy code is data beneath this package, never a replacement for
    # active veriforge modules. Templates are parsed only after inert rendering.
    initializer = ast.parse((ASSETS/"__init__.py").read_bytes())
    assert len(initializer.body) == 1 and isinstance(initializer.body[0], ast.Expr)
    assert isinstance(initializer.body[0].value, ast.Constant)
    tree = ast.parse((ASSETS/"registration.py").read_bytes())
    imports = {alias.name.split(".")[0] for node in ast.walk(tree) if isinstance(node, ast.Import) for alias in node.names}
    assert not imports & {"subprocess", "socket", "httpx", "pip", "veriforge"}
    guard = (ASSETS/"tree_guard.template.py").read_text()
    for name in ("worker.template.py", "policy.template.py"):
        rendered = (ASSETS/name).read_text().replace("@TREE_GUARD@", guard)
        for token in ("MANIFEST_SHA", "LEGACY_HASH", "CURRENT_HASH", "LEGACY_MANIFEST_SHA", "LEGACY_WORKER_SHA", "ACTIVE_MANIFEST_SHA"):
            rendered = rendered.replace("@"+token+"@", "0"*64)
        ast.parse(rendered, filename=name)


def host_gate(**changes):
    options = dict(bits=64, implementation="cpython", version="3.12.13", operating_system="win32", machine="AMD64", isolated=1)
    options.update(changes)
    tree = ast.parse((ASSETS/"registration.py").read_bytes())
    function = next(node for node in tree.body if isinstance(node, ast.FunctionDef) and node.name == "register")
    guards = []
    for node in function.body:
        if isinstance(node, ast.Assign) and any(isinstance(target, ast.Name) and target.id == "root" for target in node.targets): break
        if isinstance(node, ast.Expr) and isinstance(node.value, ast.Call) and isinstance(node.value.func, ast.Name) and node.value.func.id == "require": guards.append(node)
    assert len(guards) == 3
    namespace = dict(require=reg.require, Path=Path, recipe=dict(python="3.12.13"),
        platform=SimpleNamespace(python_version=lambda: options["version"], machine=lambda: options["machine"]),
        sys=SimpleNamespace(flags=SimpleNamespace(isolated=options["isolated"], no_site=1, dont_write_bytecode=1),
            platform=options["operating_system"], implementation=SimpleNamespace(name=options["implementation"]),
            maxsize=2**(options["bits"]-1)-1, executable="native-test-python", _base_executable="native-test-python"))
    exec(compile(ast.fix_missing_locations(ast.Module(body=guards, type_ignores=[])), "data-only-target-gates", "exec"), namespace)


@pytest.mark.parametrize("changes", [dict(bits=32), dict(implementation="pypy"), dict(version="3.12.12"),
    dict(operating_system="linux"), dict(machine="ARM64"), dict(isolated=0)])
def test_native_target_gate_is_data_only_and_closed(changes):
    host_gate()
    with pytest.raises(ValueError): host_gate(**changes)


def wheel(tmp_path, *, entries=None, corrupt_record=False):
    info = "portable_probe-1.0.dist-info"
    files = {"portable_probe/__init__.py": b"# synthetic, never imported\n",
        info+"/METADATA": b"Metadata-Version: 2.4\nName: portable-probe\nVersion: 1.0\nLicense-Expression: MIT\nLicense-File: LICENSE\n",
        info+"/WHEEL": b"Wheel-Version: 1.0\nTag: py3-none-any\nRoot-Is-Purelib: true\n",
        info+"/licenses/LICENSE": b"synthetic license fixture only\n"}
    files.update(entries or {})
    record = io.StringIO()
    writer = csv.writer(record, lineterminator="\n")
    for name, raw in files.items():
        digest = base64.urlsafe_b64encode(hashlib.sha256(raw).digest()).rstrip(b"=").decode()
        writer.writerow((name, "sha256="+digest, len(raw)))
    writer.writerow((info+"/RECORD", "", ""))
    files[info+"/RECORD"] = record.getvalue().encode()
    if corrupt_record: files["portable_probe/__init__.py"] += b"changed after RECORD"
    path = tmp_path/"portable_probe-1.0-py3-none-any.whl"
    with zipfile.ZipFile(path, "w") as archive:
        for name, raw in files.items(): archive.writestr(name, raw)
    spec = dict(name="portable-probe", filename=path.name, version="1.0", license_expression="MIT", sha256=reg.digest(path.read_bytes()))
    return path, spec


def test_synthetic_wheel_preserves_record_and_license(tmp_path):
    path, spec = wheel(tmp_path)
    runtime, selected, provenance = reg.wheel_contents(path, spec)
    assert set(runtime) == {"portable_probe/__init__.py", "portable_probe-1.0.dist-info/METADATA"}
    assert selected["portable_probe-1.0.dist-info/licenses/LICENSE"] == b"synthetic license fixture only\n"
    assert "portable_probe-1.0.dist-info/RECORD" in selected and len(provenance["license_files"]) == 1


def test_recomputed_archive_digest_does_not_waive_record_bytes(tmp_path):
    path, spec = wheel(tmp_path, corrupt_record=True)
    with pytest.raises(ValueError, match="RECORD differs"): reg.wheel_contents(path, spec)


@pytest.mark.parametrize("name", ["../escape.py", "portable_probe/CON.py", "portable_probe/x:stream.py", "root.pth", "sitecustomize.py", "portable_probe-1.0.data/scripts/entry"])
def test_coherently_hashed_unsafe_entries_are_refused(tmp_path, name):
    path, spec = wheel(tmp_path, entries={name: b"never executed"})
    with pytest.raises(ValueError): reg.wheel_contents(path, spec)
    assert not (tmp_path.parent/"escape.py").exists()


def test_exact_file_read_and_oversize_are_portable(tmp_path):
    source = tmp_path/"input.bin"
    source.write_bytes(b"abc")
    assert reg.read_file(source, 3) == b"abc"
    with pytest.raises(ValueError): reg.read_file(source, 2)


def test_growing_file_uses_unbuffered_bounded_read_and_closes(monkeypatch):
    info = SimpleNamespace(st_mode=stat.S_IFREG, st_size=1, st_dev=1, st_ino=2, st_mtime_ns=3)
    requests, opens = [], []
    class Stream(io.BytesIO):
        def fileno(self): return 123
        def read(self, size=-1):
            requests.append(size)
            assert 0 <= size <= 9
            return super().read(size)
    stream = Stream(b"x"*32)
    class File:
        def stat(self): return info
        def read_bytes(self): pytest.fail("unbounded Path.read_bytes")
        def open(self, mode, *, buffering):
            opens.append((mode, buffering))
            return stream
    monkeypatch.setattr(reg, "Path", lambda value: File())
    monkeypatch.setattr(reg, "no_links", lambda value: None)
    monkeypatch.setattr(reg.os, "fstat", lambda fd: info)
    with pytest.raises(ValueError): reg.read_file("synthetic-growing-file", limit=8)
    assert opens == [("rb", 0)] and requests == [2] and stream.closed


def test_source_snapshot_excludes_asset_namespace_and_rejects_source_drift(tmp_path):
    source = tmp_path/"src/veriforge/current.py"
    source.parent.mkdir(parents=True)
    source.write_bytes(b"# synthetic current source\n")
    retained = tmp_path/"src/veriforge/native_validator_registration/legacy/src/veriforge/old.py"
    retained.parent.mkdir(parents=True)
    retained.write_bytes(b"# inert legacy asset\n")
    pins = {"src/veriforge/current.py": reg.digest(source.read_bytes())}
    assert set(reg.source_snapshot(tmp_path, pins)) == set(pins)
    source.write_bytes(b"# changed\n")
    with pytest.raises(ValueError, match="baseline differs"): reg.source_snapshot(tmp_path, pins)


@pytest.mark.parametrize("damage", [False, True])
def test_completion_receipt_is_excluded_from_inventory_and_written_after_verification(damage):
    # Execute only receipt construction/verification/completion statements with
    # in-memory path/file fakes; this is not register() or runtime generation.
    tree = ast.parse((ASSETS/"registration.py").read_bytes())
    function = next(node for node in tree.body if isinstance(node, ast.FunctionDef) and node.name == "register")
    start = next(i for i, node in enumerate(function.body) if isinstance(node, ast.Assign)
        and any(isinstance(t, ast.Name) and t.id == "receipt" for t in node.targets))
    statements = function.body[start:start+3]
    assert len(statements) == 3 and all(not isinstance(node, ast.Return) for node in statements)
    generated, writes = {"src/veriforge/probe.py": b"synthetic source"}, []
    class File:
        def __init__(self, name): self.name = name
        def write_bytes(self, raw): writes.append((self.name, raw))
    class Output:
        def __truediv__(self, name): return File(name)
    def read_file(path):
        if isinstance(path, File): return b"modified" if damage else generated[path.name]
        return b"synthetic-recipe-input"
    namespace = dict(recipe=dict(schema="fixture.v1", head="a"*40), runtime={}, provenance={}, generated=generated,
        ASSETS=Path("assets"), read_file=read_file, digest=reg.digest, encoded=reg.encoded, require=reg.require, output=Output())
    code = compile(ast.fix_missing_locations(ast.Module(body=statements, type_ignores=[])), "receipt-data-only", "exec")
    if damage:
        with pytest.raises(ValueError, match="output bytes differ"): exec(code, namespace)
        assert writes == []
    else:
        exec(code, namespace)
        assert len(writes) == 1 and writes[0][0] == "COMPLETED.json"
        receipt = json.loads(writes[0][1])
        assert set(receipt["files"]) == set(generated) and "COMPLETED.json" not in receipt["files"]
        assert receipt["execution"] == "not_run" and receipt["operational_acceptance"] == "pending"
