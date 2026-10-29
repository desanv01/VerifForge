"""Trusted preparer tests; temporary files and mocked stores, no operational DB."""
from contextlib import contextmanager
from dataclasses import replace
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import runpy
from types import MappingProxyType, SimpleNamespace

import pytest

from veriforge.system_investigation import native_tool_import as importer, records

ROOT = Path(__file__).resolve().parents[2]
AT = datetime(2026, 10, 30, tzinfo=timezone.utc)


def sha(data):
    return hashlib.sha256(data).hexdigest()


@pytest.fixture
def portable(tmp_path, monkeypatch):
    # Reuse only raw synthetic bytes from the earlier owner fixture, never its
    # admission/metadata arranger. Production imports no test module or code.
    fixture = runpy.run_path(str(Path(__file__).with_name("test_native_tool_records.py")))
    def forbidden(*args, **kwargs):
        pytest.fail("Production preparer must not invoke the test arranger")
    fixture["bundle"] = forbidden
    raw = fixture["synthetic_riscv"]()
    directory = tmp_path / "extracted"
    directory.mkdir()
    for name, data in raw.items():
        path = directory / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
    spec = replace(importer._CASES["riscv-40920450439"], receipt_hash=sha(raw["receipt-sha256.json"]), source_manifest_hash=sha(raw["source-manifest.json"]))
    # Synthetic authority is test-only and cannot be selected through the CLI.
    monkeypatch.setattr(importer, "_CASES", MappingProxyType({spec.family: spec}))
    return directory, raw, spec


def prepare(portable, **changes):
    options = dict(family=portable[2].family, bundle_root=portable[0], project_id="recorded-project", revision_id="recorded-revision")
    options.update(changes)
    return importer.prepare_native_bundle(**options)


def test_portable_prepare_uses_product_builder_preserves_bytes_and_is_deterministic(portable):
    first, second = prepare(portable), prepare(portable)
    assert records.serialize_investigation(first.admitted) == records.serialize_investigation(second.admitted)
    summary = importer.preparation_summary(first)
    assert summary["new_execution"] == "not_run" and summary["artifacts"] <= 256
    assert summary["project_id"] == "recorded-project"
    report = first.admitted.native_report
    by_name = {binding.original_relative_path: binding for binding in report.selection.selected if binding.provenance == "producer"}
    blobs = {records._key(item.reference): item.data for item in first.admitted.artifacts}
    assert all(blobs[records._key(binding.ref)] == portable[1][name] for name, binding in by_name.items())
    assert [case.verdict for case in report.cases] == ["pass_within_scope", "design_mismatch", "pass_within_scope"]
    assert not hasattr(first.admitted, "attempt_results")
    assert all(not hasattr(first, name) for name in ("bundle_root", "database_url"))


@pytest.mark.parametrize("mode", ["extra", "missing", "changed", "duplicate_receipt", "oversize", "traversal", "family", "identity"])
def test_directory_inventory_arguments_and_hashes_fail_closed_without_private_diagnostics(portable, mode):
    directory, raw, spec = portable
    changes = {}
    if mode == "extra": (directory / "PRIVATE_CANARY.txt").write_bytes(b"SECRET_INPUT")
    elif mode == "missing": (directory / "control/result.json").unlink()
    elif mode == "changed": (directory / "control/result.json").write_bytes(b"PRIVATE_CANARY")
    elif mode == "duplicate_receipt":
        path = directory / "receipt-sha256.json"
        path.write_bytes(b'{"x":"a","x":"b"}')
    elif mode == "oversize": (directory / "too-big").write_bytes(b"x" * (1048576 + 1))
    elif mode == "traversal": changes["bundle_root"] = directory / ".." / "extracted"
    elif mode == "family": changes["family"] = "private-unsupported-family"
    else: changes["project_id"] = "../PRIVATE_CANARY"
    with pytest.raises(importer.NativeImportError) as captured:
        prepare(portable, **changes)
    assert "PRIVATE_CANARY" not in str(captured.value) and str(directory) not in str(captured.value)


def test_symlink_files_are_rejected(portable, tmp_path):
    root = portable[0]
    target = tmp_path / "external"
    target.write_bytes(b"private external bytes")
    link = root / "linked"
    try:
        link.symlink_to(target)
    except OSError:
        pytest.skip("Platform does not permit test-host symlink creation")
    with pytest.raises(importer.NativeImportError, match="link_or_reparse"):
        prepare(portable)


def test_hardlink_files_are_rejected(portable, tmp_path):
    target = tmp_path / "external-hardlink"
    target.write_bytes(b"private external bytes")
    link = portable[0] / "linked"
    link.hardlink_to(target)
    with pytest.raises(importer.NativeImportError, match="single_regular"):
        prepare(portable)


def test_casefold_collision_is_rejected_even_on_case_insensitive_hosts(portable, monkeypatch):
    real_scandir = importer.os.scandir
    @contextmanager
    def collision(directory):
        with real_scandir(directory) as listing:
            items = list(listing)
        if Path(directory) == portable[0]:
            original = next(item for item in items if item.name == "control")
            items.append(SimpleNamespace(name="CONTROL", stat=original.stat))
        yield iter(items)
    monkeypatch.setattr(importer.os, "scandir", collision)
    with pytest.raises(importer.NativeImportError, match="path_collision"):
        prepare(portable)


def test_inventory_entry_count_is_bounded_without_following_fake_directories(portable, monkeypatch):
    info = portable[0].stat()
    @contextmanager
    def too_many(directory):
        yield (SimpleNamespace(name="directory-"+str(i), stat=lambda **kwargs: info) for i in range(8193))
    monkeypatch.setattr(importer.os, "scandir", too_many)
    with pytest.raises(importer.NativeImportError, match="entry_count_limit"):
        importer._snapshot(portable[0])


def test_inventory_actual_read_total_is_bounded(tmp_path, monkeypatch):
    directory = tmp_path / "bounded"
    directory.mkdir()
    (directory / "one").write_bytes(b"x")
    monkeypatch.setattr(importer.records, "MAX_TOTAL_BYTES", 10)
    monkeypatch.setattr(importer, "_read_regular", lambda path: b"x" * 11)
    with pytest.raises(importer.NativeImportError, match="total_read_limit"):
        importer._snapshot(directory)


def test_installed_checker_and_validator_changes_fail_closed(portable, monkeypatch):
    real_read = importer._read_regular
    def changed_validator(path):
        return b"changed installed implementation" if Path(path).name == "native_tool_records.py" else real_read(path)
    monkeypatch.setattr(importer, "_read_regular", changed_validator)
    with pytest.raises(importer.NativeImportError, match="unsupported_validator"):
        prepare(portable)
    installed_checker = (Path(importer.native.__file__).parents[1] / "riscv/checker.py").absolute()
    def changed_checker(path):
        return b"changed installed checker" if Path(path).absolute() == installed_checker else real_read(path)
    monkeypatch.setattr(importer, "_read_regular", changed_checker)
    with pytest.raises(importer.NativeImportError, match="installed_domain"):
        prepare(portable)


def test_snapshot_change_after_read_is_rejected(portable, monkeypatch):
    real_read = importer._read_regular
    changed = False
    def add_file(path):
        nonlocal changed
        result = real_read(path)
        if Path(path).is_relative_to(portable[0]) and not changed:
            changed = True
            (portable[0] / "added-during-read").write_bytes(b"changed")
        return result
    monkeypatch.setattr(importer, "_read_regular", add_file)
    with pytest.raises(importer.NativeImportError):
        prepare(portable)


def test_cli_default_does_not_open_database_or_run_tools(portable, monkeypatch, capsys):
    import sqlalchemy
    import subprocess
    def forbidden(*args, **kwargs): pytest.fail("Default preparation attempted a DB or process")
    monkeypatch.setattr(sqlalchemy, "create_engine", forbidden)
    monkeypatch.setattr(subprocess, "run", forbidden)
    monkeypatch.setattr(subprocess, "Popen", forbidden)
    args = ["--family", portable[2].family, "--bundle-root", str(portable[0]), "--project-id", "recorded-project", "--revision-id", "recorded-revision"]
    assert importer.main(args) == 0
    output = json.loads(capsys.readouterr().out)
    assert output["database"] == "not_opened" and output["status"] == "ok" and str(portable[0]) not in json.dumps(output)
    assert importer.main([*args, "--idempotency-key", "not-allowed"]) == 2
    assert importer.main([*args, "--apply"]) == 2
    assert "PRIVATE_CANARY" not in capsys.readouterr().out


def test_portable_firmware_product_preparation_and_saved_program_metadata(tmp_path, monkeypatch):
    fixture = runpy.run_path(str(Path(__file__).with_name("test_native_tool_records.py")))
    raw = fixture["synthetic_firmware"](tmp_path / "synthetic-inputs")
    directory = tmp_path / "firmware-bundle"
    directory.mkdir()
    for name, data in raw.items():
        path = directory / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
    spec = replace(importer._CASES["firmware-40929665294"], receipt_hash=sha(raw["receipt-sha256.json"]), source_manifest_hash=sha(raw["source-manifest.json"]))
    monkeypatch.setattr(importer, "_CASES", MappingProxyType({spec.family: spec}))
    prepared = importer.prepare_native_bundle(family=spec.family, bundle_root=directory, project_id="recorded-project", revision_id="recorded-revision")
    cases = prepared.admitted.native_report.cases
    assert all(case.locator.execution_ref.attempt_id is None for case in cases)
    assert [case.mailbox.value for case in cases] == [1, 2, 1, 2, 1]
    assert cases[3].producer_phases == cases[4].producer_phases == ("simulator",)
    assert importer.preparation_summary(prepared)["report_kind"] == "firmware.recorded-report.v1"


class FakeSession:
    new = dirty = deleted = ()
    commits = 0
    nested = 0
    rolled_back = 0
    def in_transaction(self): return True
    def connection(self):
        return SimpleNamespace(dialect=SimpleNamespace(name="sqlite"), connection=SimpleNamespace(driver_connection=SimpleNamespace(in_transaction=True)))
    @contextmanager
    def begin_nested(self):
        self.nested += 1
        try:
            yield
        except Exception:
            self.rolled_back += 1
            raise


def test_apply_is_caller_owned_and_revalidates_before_accepted_store(portable, monkeypatch):
    from veriforge.core import investigations, recorded_import, engineering_catalog
    prepared = prepare(portable)
    calls = []
    monkeypatch.setattr(recorded_import, "require_supported_schema", lambda session: calls.append("schema"))
    monkeypatch.setattr(engineering_catalog, "get_engineering_project", lambda session, project: calls.append(("project", project)))
    def store(session, record, artifacts, **options):
        assert record == prepared.admitted.envelope.record and artifacts == prepared.admitted.artifacts
        assert options["idempotency_key"] == "native-key" and options["at"] == AT
        calls.append("store")
        return dict(investigation_id="db-parent", id="db-version")
    monkeypatch.setattr(investigations, "persist_investigation", store)
    monkeypatch.setattr(investigations, "version_view", lambda *args: dict(envelope=prepared.admitted.envelope.model_dump(mode="json"),
        native_report=prepared.admitted.native_report.model_dump(mode="json"), content_hash=sha(records.serialize_investigation(prepared.admitted))))
    session = FakeSession()
    result = importer.apply_prepared(session, prepared, idempotency_key="native-key", at=AT)
    assert calls == ["schema", ("project", "recorded-project"), "store"]
    assert result["database"] == "pending_caller_commit" and session.commits == 0 and session.nested == 1


def test_apply_readback_failure_rolls_back_nested_scope_without_committing(portable, monkeypatch):
    from veriforge.core import investigations, recorded_import, engineering_catalog
    prepared, session = prepare(portable), FakeSession()
    monkeypatch.setattr(recorded_import, "require_supported_schema", lambda session: None)
    monkeypatch.setattr(engineering_catalog, "get_engineering_project", lambda *args: None)
    monkeypatch.setattr(investigations, "persist_investigation", lambda *a, **k: dict(investigation_id="parent", id="version"))
    monkeypatch.setattr(investigations, "version_view", lambda *args: dict(envelope={}, content_hash="incorrect", native_report={}))
    with pytest.raises(importer.NativeImportError, match="stored_native_admission_differs"):
        importer.apply_prepared(session, prepared, idempotency_key="key", at=AT)
    assert session.rolled_back == 1 and session.commits == 0


@pytest.mark.parametrize("commit_fails", [False, True])
@pytest.mark.parametrize("dispose_fails", [False, True])
def test_cli_explicit_apply_reports_commit_only_after_transaction_exit(portable, monkeypatch, capsys, commit_fails, dispose_fails):
    from veriforge.core import recorded_import, database
    prepared = prepare(portable)
    events = []
    def dispose():
        events.append("disposed")
        if dispose_fails:
            raise RuntimeError("PRIVATE_DISPOSAL_DIAGNOSTIC")
    engine = SimpleNamespace(dispose=dispose)
    @contextmanager
    def transaction():
        events.append("begin")
        yield object()
        if commit_fails:
            raise RuntimeError("PRIVATE_DRIVER_DIAGNOSTIC")
        events.append("commit")
    monkeypatch.setattr(recorded_import, "_configured_engine", lambda: engine)
    monkeypatch.setattr(database, "session_factory", lambda value: SimpleNamespace(begin=transaction))
    monkeypatch.setattr(importer, "prepare_native_bundle", lambda **kwargs: prepared)
    def apply(session, value, **options):
        assert options["idempotency_key"] == "explicit-key" and value is prepared
        events.append("apply")
        return dict(database="pending_caller_commit")
    monkeypatch.setattr(importer, "apply_prepared", apply)
    args = ["--family", portable[2].family, "--bundle-root", str(portable[0]), "--project-id", "recorded-project", "--revision-id", "recorded-revision", "--apply", "--idempotency-key", "explicit-key"]
    status = importer.main(args)
    captured = capsys.readouterr()
    output = json.loads(captured.out)
    assert events == (["begin", "apply", "disposed"] if commit_fails else ["begin", "apply", "commit", "disposed"])
    assert status == (2 if commit_fails else 0)
    assert output["status"] == ("rejected" if commit_fails else "ok")
    assert "PRIVATE_DRIVER_DIAGNOSTIC" not in json.dumps(output)
    assert output.get("database") == (None if commit_fails else "committed")
    assert "PRIVATE_DISPOSAL_DIAGNOSTIC" not in captured.out + captured.err
    if dispose_fails:
        assert json.loads(captured.err) == dict(status="warning", stage="cleanup", code="engine_disposal_failed",
            database="commit_not_confirmed" if commit_fails else "committed")
    else:
        assert captured.err == ""


@pytest.mark.parametrize("mode", ["transaction", "dirty", "time", "key", "unsealed"])
def test_apply_refuses_untrusted_or_unbounded_caller_before_store(portable, monkeypatch, mode):
    from veriforge.core import investigations
    prepared, session = prepare(portable), FakeSession()
    args = dict(idempotency_key="key", at=AT)
    if mode == "transaction": session.in_transaction = lambda: False
    elif mode == "dirty": session.dirty = (object(),)
    elif mode == "time": args["at"] = AT.replace(tzinfo=None)
    elif mode == "key": args["idempotency_key"] = ""
    else: prepared = object()
    monkeypatch.setattr(investigations, "persist_investigation", lambda *a, **k: pytest.fail("Refused admission reached store"))
    with pytest.raises(importer.NativeImportError):
        importer.apply_prepared(session, prepared, **args)


@pytest.mark.parametrize("family,env_name", [("riscv-40920450439", "VERIFORGE_TEST_RISCV_BUNDLE"), ("firmware-40929665294", "VERIFORGE_TEST_FIRMWARE_BUNDLE")])
def test_optional_exact_retained_bundle_read_only(family, env_name):
    import os
    location = os.environ.get(env_name)
    if not location:
        pytest.skip("Set explicit inert bundle path to run private-byte preparation audit")
    prepared = importer.prepare_native_bundle(family=family, bundle_root=location, project_id="recorded-project", revision_id="recorded-revision")
    assert importer.preparation_summary(prepared)["artifacts"] in (102, 199)
    assert len(records.serialize_investigation(prepared.admitted)) <= 262144
