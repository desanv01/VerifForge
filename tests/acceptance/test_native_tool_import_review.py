"""Independent importer review with synthetic files and real temporary SQLite.

Only the test-host case catalog is repinned to authored raw fixture bytes. The
product assembler, pure admission, installed checks, migrations and storage run
unchanged; no actual retained artifact or native command is executed/modified.
"""
from dataclasses import replace
from datetime import datetime, timezone
from hashlib import sha256
import importlib.util
import json
from pathlib import Path
from types import MappingProxyType, SimpleNamespace

from alembic import command
from alembic.config import Config
import pytest
import sqlalchemy as sa
from sqlalchemy.exc import OperationalError
from sqlalchemy.orm import Session

from veriforge.core import investigations, recorded_import
from veriforge.core.database import InvestigationArtifact, InvestigationVersion, make_engine, session_factory
from veriforge.core.engineering_catalog import EngineeringProjectSpec, create_engineering_project
from veriforge.system_investigation import native_tool_import as importer, records

ROOT = Path(__file__).resolve().parents[2]
AT = datetime(2026, 10, 30, tzinfo=timezone.utc)
CANARY = "SYNTHETIC_PRIVATE_CANARY"


@pytest.fixture
def portable(tmp_path, monkeypatch):
    path = ROOT / "tests/system_investigation/test_native_tool_records.py"
    spec = importlib.util.spec_from_file_location("trusted_import_raw_seed", path)
    helper = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(helper)
    raw = helper.synthetic_riscv()
    directory = tmp_path / CANARY
    directory.mkdir()
    for name, data in raw.items():
        file = directory / name
        file.parent.mkdir(parents=True, exist_ok=True)
        file.write_bytes(data)
    case = replace(importer._CASES["riscv-40920450439"],
        receipt_hash=sha256(raw["receipt-sha256.json"]).hexdigest(),
        source_manifest_hash=sha256(raw["source-manifest.json"]).hexdigest())
    monkeypatch.setattr(importer, "_CASES", MappingProxyType({case.family: case}))
    return directory, raw, case


def prepare(portable, **options):
    return importer.prepare_native_bundle(**{**dict(family=portable[2].family, bundle_root=portable[0],
        project_id="review-native-project", revision_id="review-associated-revision"), **options})


@pytest.fixture
def db(tmp_path):
    engine = make_engine("sqlite:///" + (tmp_path / "native-import-review.db").as_posix())
    config = Config("alembic.ini")
    with engine.begin() as connection:
        config.attributes["connection"] = connection
        command.upgrade(config, "13engineeringprojects")
    sessions = session_factory(engine)
    with sessions.begin() as session:
        create_engineering_project(session, EngineeringProjectSpec(project_id="review-native-project",
            name="Authored native import test", domains=("riscv", "system")))
    yield engine, sessions
    engine.dispose()


def counts(db):
    with db[0].connect() as connection:
        return {name: connection.execute(sa.text(f'SELECT COUNT(*) FROM "{name}"')).scalar_one()
                for name in sa.inspect(connection).get_table_names()}


def apply(db, prepared, key="review-key", **options):
    with db[1].begin() as session:
        return importer.apply_prepared(session, prepared, idempotency_key=key, at=AT, **options)


def args(portable, applying=False):
    values = ["--family", portable[2].family, "--bundle-root", str(portable[0]), "--project-id", "review-native-project",
              "--revision-id", "review-associated-revision"]
    return values + (["--apply", "--idempotency-key", "cli-review-key"] if applying else [])


def test_prepared_snapshot_is_immutable_and_apply_never_reopens_mutated_bundle(portable, db, monkeypatch):
    prepared = prepare(portable)
    wire = records.serialize_investigation(prepared.admitted)
    (portable[0] / "control/result.json").write_bytes(CANARY.encode())
    monkeypatch.setattr(importer, "_snapshot", lambda *a: pytest.fail("apply reopened the mutable input root"))
    before = counts(db)
    result = apply(db, prepared)
    assert result["database"] == "pending_caller_commit" and CANARY not in json.dumps(result)
    with db[1]() as session:
        version = session.get(InvestigationVersion, result["db_version_id"])
        assert version.content == wire
        stored = session.scalars(sa.select(InvestigationArtifact).where(InvestigationArtifact.version_id == version.id)).all()
        assert {(b.artifact_id, b.native_revision_id, b.native_attempt_id): b.data for b in stored} == {
            records._key(b.reference): b.data for b in prepared.admitted.artifacts}
    after = counts(db)
    assert all(after[name] == before[name] for name in before if name not in {
        "investigations", "investigation_versions", "investigation_artifacts"})
    with pytest.raises(TypeError):
        importer.PreparedNativeBundle()


def test_real_store_idempotency_append_and_changed_content_conflict(portable, db):
    prepared = prepare(portable)
    first = apply(db, prepared)
    before = counts(db)
    assert apply(db, prepared) == first
    assert counts(db) == before
    second = apply(db, prepared, "append", investigation_id=first["db_investigation_id"])
    assert second["db_investigation_id"] == first["db_investigation_id"]
    assert second["db_version_id"] != first["db_version_id"]
    changed = prepare(portable, revision_id="different-associated-revision")
    before = counts(db)
    with pytest.raises(importer.NativeImportError):
        apply(db, changed)
    assert counts(db) == before


def test_outer_rollback_does_not_commit_released_sqlite_savepoint(portable, db):
    prepared = prepare(portable)
    before = counts(db)
    with pytest.raises(RuntimeError):
        with db[1].begin() as session:
            importer.apply_prepared(session, prepared, idempotency_key="outer-rollback", at=AT)
            raise RuntimeError("authored caller rejection")
    assert counts(db) == before


@pytest.mark.parametrize("stage", ["persistence", "readback"])
def test_partial_storage_or_readback_error_rolls_back_even_if_caller_commits(portable, db, monkeypatch, stage):
    prepared = prepare(portable)
    before = counts(db)
    original_store = investigations.persist_investigation
    original_view = investigations.version_view

    def failed_store(*args, **kwargs):
        original_store(*args, **kwargs)
        raise OperationalError(CANARY, {}, RuntimeError(CANARY))

    def wrong_view(*args, **kwargs):
        return {**original_view(*args, **kwargs), "content_hash": "f" * 64}

    monkeypatch.setattr(investigations, "persist_investigation" if stage == "persistence" else "version_view",
                        failed_store if stage == "persistence" else wrong_view)
    with db[1].begin() as session:
        with pytest.raises(importer.NativeImportError) as error:
            importer.apply_prepared(session, prepared, idempotency_key="failed-unit", at=AT)
        assert CANARY not in str(error.value)
    assert counts(db) == before


def test_apply_never_creates_missing_engineering_project(portable, db):
    prepared = prepare(portable, project_id="missing-local-project")
    before = counts(db)
    with pytest.raises(importer.NativeImportError):
        apply(db, prepared)
    assert counts(db) == before


@pytest.mark.parametrize("part", ["native_tool_records.py", "native_tools.py", "checker.py"])
def test_installed_change_between_prepare_and_apply_rejects_before_store(portable, db, monkeypatch, part):
    prepared = prepare(portable)
    actual = importer._read_regular

    def changed(path):
        return CANARY.encode() if Path(path).name == part else actual(path)

    monkeypatch.setattr(importer, "_read_regular", changed)
    monkeypatch.setattr(investigations, "persist_investigation", lambda *a, **k: pytest.fail("stale implementation reached store"))
    before = counts(db)
    with pytest.raises(importer.NativeImportError) as error:
        apply(db, prepared)
    assert CANARY not in str(error.value) and counts(db) == before


def test_actual_snapshot_byte_bound_cannot_use_stale_directory_size(tmp_path, monkeypatch):
    root = tmp_path / "snapshot"
    root.mkdir()
    (root / "entry").write_bytes(b"x")
    monkeypatch.setattr(importer.records, "MAX_TOTAL_BYTES", 2)
    monkeypatch.setattr(importer, "_read_regular", lambda *a: b"xxx")
    with pytest.raises(importer.NativeImportError, match="total_read_limit"):
        importer._snapshot(root)


def test_file_identity_change_during_descriptor_read_is_rejected(tmp_path, monkeypatch):
    file = tmp_path / "input"
    file.write_bytes(b"authored input")
    actual = importer.os.fstat
    calls = []

    def changed(descriptor):
        info = actual(descriptor)
        calls.append(True)
        if len(calls) == 2:
            return SimpleNamespace(st_dev=info.st_dev, st_ino=info.st_ino, st_mode=info.st_mode,
                st_size=info.st_size, st_mtime_ns=info.st_mtime_ns + 1)
        return info

    monkeypatch.setattr(importer.os, "fstat", changed)
    with pytest.raises(importer.NativeImportError, match="file_changed_during_read"):
        importer._read_regular(file)


def test_snapshot_rejects_external_hardlink_without_exposing_its_bytes(tmp_path):
    directory = tmp_path / "links"
    directory.mkdir()
    external = tmp_path / "outside"
    external.write_bytes(CANARY.encode())
    try:
        (directory / "alias").hardlink_to(external)
    except OSError:
        pytest.skip("test host cannot create a synthetic hardlink")
    with pytest.raises(importer.NativeImportError, match="single_regular_file") as error:
        importer._snapshot(directory)
    assert CANARY not in str(error.value) and str(external) not in str(error.value)


def test_default_cli_never_opens_database_or_runs_process_and_omits_paths(portable, monkeypatch, capsys):
    import sqlalchemy
    import subprocess

    def forbidden(*a, **k):
        pytest.fail("default import preparation performed DB/process I/O")

    monkeypatch.setattr(sqlalchemy, "create_engine", forbidden)
    monkeypatch.setattr(subprocess, "run", forbidden)
    monkeypatch.setattr(subprocess, "Popen", forbidden)
    assert importer.main(args(portable)) == 0
    data = capsys.readouterr().out
    assert json.loads(data)["database"] == "not_opened"
    assert CANARY not in data and str(portable[0]) not in data
    (portable[0] / "extra-private-file").write_bytes(CANARY.encode())
    assert importer.main(args(portable)) == 2
    assert CANARY not in capsys.readouterr().out


def test_cli_outer_commit_failure_is_sanitized_and_rolls_back_real_store(portable, db, monkeypatch, capsys, request):
    monkeypatch.setenv("VERIFORGE_DATABASE_URL", str(db[0].url))

    def failed_outer(session):
        if not session.in_nested_transaction():
            raise OperationalError(CANARY, {}, RuntimeError(CANARY))

    sa.event.listen(Session, "before_commit", failed_outer)
    request.addfinalizer(lambda: sa.event.remove(Session, "before_commit", failed_outer))
    before = counts(db)
    assert importer.main(args(portable, True)) == 2
    output = capsys.readouterr().out
    assert json.loads(output)["status"] == "rejected" and "committed" not in output and CANARY not in output
    assert counts(db) == before


def test_cli_disposal_error_does_not_escape_or_expose_private_driver_text(portable, db, monkeypatch, capsys):
    cli_engine = make_engine(str(db[0].url))
    actual_dispose = cli_engine.dispose

    def private_disposal_error():
        actual_dispose()
        raise RuntimeError(CANARY)

    monkeypatch.setattr(recorded_import, "_configured_engine", lambda: cli_engine)
    monkeypatch.setattr(cli_engine, "dispose", private_disposal_error)
    try:
        result = importer.main(args(portable, True))
        assert result in {0, 2}
        output = capsys.readouterr()
        assert CANARY not in output.out + output.err
        assert json.loads(output.out)["database"] == "committed"
        assert counts(db)["investigations"] == 1
    finally:
        actual_dispose()
