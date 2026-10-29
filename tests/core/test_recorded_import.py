"""Synthetic authored admission into temporary SQLite, never operational data.

The fixed native loader is mocked explicitly in composition/CLI tests. Its real
reviewed byte pins are covered separately by preparation tests; no private native
bundle is loaded or imported by this file.
"""
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path

from alembic import command
from alembic.config import Config
import pytest
import sqlalchemy as sa
from sqlalchemy.exc import OperationalError

from veriforge.contracts.engineering import EvidenceReference
from veriforge.core import recorded_import as importer
from veriforge.core.database import (Project, Investigation, InvestigationVersion, InvestigationArtifact,
    InvestigationExecutionLink, make_engine, session_factory)
from veriforge.core.engineering_catalog import create_engineering_project
from veriforge.system_investigation import (AddressBinding, RegisterIntent, SourceArtifact,
    bundle_files, compare_registers, records)

AT = datetime(2026, 10, 30, tzinfo=timezone.utc)
PATHS = {key: Path("synthetic-storage") / key for key in (
    "bundle_root", "xdc", "netlist", "board", "generator", "logical_mapping", "native_receipt")}


def encode(model):
    return json.dumps(model.model_dump(mode="json"), sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()


@pytest.fixture
def admitted():
    """Three synthetic firmware facts test storage only, not actual board import."""
    blobs = []

    def add(name, raw, ref=None):
        ref = ref or EvidenceReference(artifact_id=name, revision_id="synthetic-native-revision",
                                        sha256=hashlib.sha256(raw).hexdigest())
        blobs.append(records.ArtifactBytes(reference=ref, data=raw))
        return ref

    sources = [SourceArtifact(reference=add(name, raw), data=raw, origin="synthetic_fixture")
        for name, raw in (("synthetic-header", b"#define FLAG_ADDR 0x40000000u\n"),
                          ("synthetic-rtl", b"case (addr)\n8'h00: flag <= write_data;\ndefault: ;\nendcase\n"))]
    intent = RegisterIntent(byte_base=0x40000000, bindings=(AddressBinding(binding_id="flag",
        header_macro="FLAG_ADDR", rtl_signal="flag", expected_address=0x40000000),))
    report = compare_registers(*sources, intent)
    files = bundle_files(report)
    proofs = {proof.artifact.artifact_id: proof.artifact for row in report.native_records for proof in row.fact.provenance.sources}
    add("reviewed-intent", files["intent.json"], proofs["reviewed-intent"])
    config_ref = add("synthetic-configuration", b'{"scope":"synthetic authored storage test; no tool execution"}')
    revision = records.EngineeringProjectRevision(project_id=importer.PROJECT_SPEC.project_id,
        revision_id=importer.prepare_import.REVISION_ID, source_refs=tuple(proofs.values()) + (config_ref,),
        configuration_hash=config_ref.sha256)
    project_ref = add("synthetic-project", encode(revision))
    manifest_ref = add("synthetic-manifest", files["manifest.json"])
    bridge_ref = add("synthetic-bridge", files["report.json"])
    attachments = []
    for fact in report.manifest.facts:
        add(fact.fact_id, files["native/" + fact.native_record.artifact_id + ".json"], fact.native_record)
        attachments.append(records.NativeAttachment(fact_id=fact.fact_id, domain=fact.domain,
            native_schema_id=fact.native_schema_id, reference=fact.native_record))
    for relation in report.manifest.relations:
        for ref in relation.evidence:
            add(ref.artifact_id, files["comparisons/" + ref.artifact_id + ".json"], ref)
    record = records.EngineeringInvestigation(investigation_id=importer.prepare_import.INVESTIGATION_ID,
        project_id=revision.project_id, revision_id=revision.revision_id, project_revision_ref=project_ref,
        manifest_ref=manifest_ref, bridge_report_ref=bridge_ref, native_records=tuple(attachments), attempts=())
    authority = records.create_admission_authority(project_id=record.project_id, revision_id=record.revision_id,
        project_revision_ref=project_ref, manifest_ref=manifest_ref, bridge_report_ref=bridge_ref, attempts=())
    return records.admit_investigation(record, tuple(blobs), authority=authority)


def migrate(engine, revision=importer.SUPPORTED_REVISION):
    config = Config("alembic.ini")
    with engine.begin() as connection:
        config.attributes["connection"] = connection
        command.upgrade(config, revision)


@pytest.fixture
def db(tmp_path):
    engine = make_engine("sqlite:///" + (tmp_path / "recorded.db").as_posix())
    migrate(engine)
    yield engine, session_factory(engine)
    engine.dispose()


@pytest.fixture
def fixed_loader(monkeypatch, admitted):
    calls = []

    def authored_loader(**paths):
        calls.append(paths)
        assert paths == PATHS
        return admitted

    monkeypatch.setattr(importer.prepare_import, "prepare_corrected_case", authored_loader)
    return calls


def counts(db):
    with db[0].connect() as connection:
        # Includes every migration-owned table, so unintended dispatch is visible.
        return {name: connection.execute(sa.text(f'SELECT COUNT(*) FROM "{name}"')).scalar_one()
                for name in sa.inspect(connection).get_table_names()}


def import_once(db, key="synthetic-import-1"):
    with db[1].begin() as session:
        return importer.import_corrected_case(session, idempotency_key=key, at=AT, **PATHS)


def test_atomic_exact_storage_idempotency_readback_and_no_dispatch(db, admitted, fixed_loader):
    before = counts(db)
    first = import_once(db)
    assert first["database"] == "pending_caller_commit"
    assert first["content_sha256"] == hashlib.sha256(records.serialize_investigation(admitted)).hexdigest()
    assert first == import_once(db)
    assert len(fixed_loader) == 2
    assert first["new_execution"] == "not_run" and first["graph_verdicts"] == ["inconclusive"]
    after = counts(db)
    expected_changes = {"projects": 1, "investigations": 1, "investigation_versions": 1,
                        "investigation_artifacts": len(admitted.artifacts)}
    assert {name: after[name] - before[name] for name in after if after[name] != before[name]} == expected_changes
    with db[1]() as session:
        project = session.get(Project, importer.PROJECT_SPEC.project_id)
        assert project.kind == "engineering_recorded" and project.contract_id is None
        assert project.data["domains"] == list(importer.PROJECT_SPEC.domains)
        version = session.get(InvestigationVersion, first["db_version_id"])
        assert version.content == records.serialize_investigation(admitted)
        stored = session.scalars(sa.select(InvestigationArtifact)).all()
        by_identity = {(b.artifact_id, b.native_revision_id, b.native_attempt_id): b for b in stored}
        for blob in admitted.artifacts:
            ref = blob.reference
            actual = by_identity[ref.artifact_id, ref.revision_id, ref.attempt_id]
            assert actual.data == blob.data and actual.sha256 == ref.sha256
        assert session.scalar(sa.select(sa.func.count()).select_from(InvestigationExecutionLink)) == 0


def test_caller_rollback_after_success_removes_both_project_and_evidence(db, fixed_loader):
    before = counts(db)
    with pytest.raises(RuntimeError):
        with db[1].begin() as session:
            importer.import_corrected_case(session, idempotency_key="outer-rollback", at=AT, **PATHS)
            raise RuntimeError("caller rollback")
    assert counts(db) == before


@pytest.mark.parametrize("failure_stage", ["persistence", "readback"])
def test_error_rolls_back_entire_unit_even_if_caller_catches_and_commits(db, fixed_loader, monkeypatch, failure_stage):
    before = counts(db)
    target = "persist_investigation" if failure_stage == "persistence" else "version_view"

    def fail(*args, **kwargs):
        raise ValueError("PRIVATE_SOURCE_NOT_A_DIAGNOSTIC")

    monkeypatch.setattr(importer.investigations, target, fail)
    with db[1].begin() as session:
        with pytest.raises(importer.RecordedImportError) as caught:
            importer.import_corrected_case(session, idempotency_key="failed-unit", at=AT, **PATHS)
        assert "PRIVATE_SOURCE" not in str(caught.value)
    assert counts(db) == before


def test_readback_digest_disagreement_rolls_back(db, fixed_loader, monkeypatch):
    original = importer.investigations.version_view

    def changed(*args):
        return {**original(*args), "content_hash": "0" * 64}

    monkeypatch.setattr(importer.investigations, "version_view", changed)
    with pytest.raises(importer.RecordedImportError, match="stored_admission_disagrees"):
        import_once(db)
    assert counts(db)["projects"] == counts(db)["investigations"] == 0


def test_existing_project_conflict_is_immutable_and_leaves_no_import(db, fixed_loader):
    with db[1].begin() as session:
        create_engineering_project(session, importer.PROJECT_SPEC.model_copy(update={"name": "Different immutable name"}))
    before = counts(db)
    with pytest.raises(importer.RecordedImportError, match="immutable_content_conflict"):
        import_once(db)
    assert counts(db) == before


def test_existing_idempotency_key_with_other_content_conflicts(db, fixed_loader):
    # Establish a real synthetic import, then use a changed key-bound request hash
    # via a separate immutable investigation row, never mutate accepted history.
    with db[1].begin() as session:
        create_engineering_project(session, importer.PROJECT_SPEC)
        session.add(Investigation(project_id=importer.PROJECT_SPEC.project_id,
            create_key="occupied", request_hash="f" * 64, created_at=AT))
    before = counts(db)
    with pytest.raises(importer.RecordedImportError, match="immutable_content_conflict"):
        import_once(db, "occupied")
    assert counts(db) == before


def test_refuses_no_transaction_unsealed_or_bad_arguments_without_writes(db, admitted):
    with db[1]() as session:
        with pytest.raises(importer.RecordedImportError, match="caller_transaction_required"):
            importer._persist_prepared(session, admitted, idempotency_key="a", at=AT)
    for key, at in (("", AT), ("x" * 129, AT), ("a", AT.replace(tzinfo=None))):
        with db[1].begin() as session:
            with pytest.raises(importer.RecordedImportError):
                importer._persist_prepared(session, admitted, idempotency_key=key, at=at)
    with db[1].begin() as session:
        with pytest.raises(importer.RecordedImportError):
            importer._persist_prepared(session, {}, idempotency_key="a", at=AT)
    assert counts(db)["projects"] == 0


def test_old_schema_refused_before_preparing_any_input(tmp_path, monkeypatch):
    engine = make_engine("sqlite:///" + (tmp_path / "old.db").as_posix())
    migrate(engine, "12u2resources")

    def never(**kwargs):
        pytest.fail("old schema must be refused before input preparation")

    monkeypatch.setattr(importer.prepare_import, "prepare_corrected_case", never)
    try:
        with session_factory(engine).begin() as session:
            with pytest.raises(importer.RecordedImportError, match="unsupported_schema_revision"):
                importer.import_corrected_case(session, idempotency_key="old", at=AT, **PATHS)
        with engine.connect() as connection:
            assert connection.execute(sa.text("SELECT version_num FROM alembic_version")).scalar_one() == "12u2resources"
            assert connection.execute(sa.text("SELECT COUNT(*) FROM projects")).scalar_one() == 0
    finally:
        engine.dispose()


def test_future_revision_and_missing_shape_refused(db):
    with db[0].begin() as connection:
        connection.execute(sa.text("UPDATE alembic_version SET version_num='future-unknown'"))
    with db[1].begin() as session:
        with pytest.raises(importer.RecordedImportError, match="unsupported_schema_revision"):
            importer.require_supported_schema(session)
    with db[0].begin() as connection:
        connection.execute(sa.text("UPDATE alembic_version SET version_num='13engineeringprojects'"))
        connection.execute(sa.text("DROP TABLE investigation_execution_links"))
    with db[1].begin() as session:
        with pytest.raises(importer.RecordedImportError, match="required_import_table_missing"):
            importer.require_supported_schema(session)


def cli_args(commit=False):
    args = ["--case", "u7-corrected-v1"]
    for role, path in PATHS.items():
        args += ["--" + role.replace("_", "-"), str(path)]
    if commit:
        args += ["--commit", "--idempotency-key", "synthetic-cli-1"]
    return args


def test_cli_default_does_not_open_database_and_reports_stable_digest(fixed_loader, monkeypatch, capsys):
    monkeypatch.setattr(importer, "_configured_engine", lambda: pytest.fail("preparation opened a DB"))
    assert importer.main(cli_args()) == 0
    first = json.loads(capsys.readouterr().out)
    assert first["database"] == "not_opened" and "db_version_id" not in first
    assert importer.main(cli_args()) == 0
    assert json.loads(capsys.readouterr().out) == first


def test_cli_commit_is_explicit_idempotent_and_only_claimed_after_commit(db, fixed_loader, monkeypatch, capsys):
    monkeypatch.setenv("VERIFORGE_DATABASE_URL", str(db[0].url))
    assert importer.main(cli_args(True)) == 0
    first = json.loads(capsys.readouterr().out)
    assert first["database"] == "committed"
    assert importer.main(cli_args(True)) == 0
    assert json.loads(capsys.readouterr().out) == first
    assert counts(db)["investigations"] == 1


@pytest.mark.parametrize("config", [None, "postgresql+psycopg://SECRET:PASS@invalid.example/database", "not-a-url-SECRET"])
def test_cli_configuration_and_driver_failures_never_print_secret_url_or_input_paths(config, fixed_loader, monkeypatch, capsys):
    if config is None:
        monkeypatch.delenv("VERIFORGE_DATABASE_URL", raising=False)
    else:
        monkeypatch.setenv("VERIFORGE_DATABASE_URL", config)

    def fail(*args, **kwargs):
        raise OperationalError("SECRET SQL", {}, RuntimeError("SECRET DRIVER PATH"))

    monkeypatch.setattr(importer, "create_engine", fail)
    assert importer.main(cli_args(True)) == 2
    output = capsys.readouterr().out
    assert json.loads(output)["status"] == "rejected"
    assert "SECRET" not in output and "synthetic-storage" not in output and "invalid.example" not in output


def test_cli_never_creates_missing_sqlite_database(tmp_path, fixed_loader, monkeypatch, capsys):
    path = tmp_path / "must-not-be-created.db"
    monkeypatch.setenv("VERIFORGE_DATABASE_URL", "sqlite:///" + path.as_posix())
    assert importer.main(cli_args(True)) == 2
    assert json.loads(capsys.readouterr().out)["code"] == "existing_database_required"
    assert not path.exists()


def test_cli_bad_flags_and_missing_commit_key_have_safe_diagnostics(capsys):
    assert importer.main(["--unknown-private-path", "PRIVATE_PATH"]) == 2
    assert "PRIVATE_PATH" not in capsys.readouterr().out
    assert importer.main(cli_args() + ["--commit"]) == 2
    assert json.loads(capsys.readouterr().out)["stage"] == "arguments"


def test_cli_commit_failure_rollback_and_safe_diagnostic(db, fixed_loader, monkeypatch, capsys):
    original = session_factory(db[0])

    class FailingSessions:
        def begin(self):
            from contextlib import contextmanager

            @contextmanager
            def transaction():
                with original.begin() as session:
                    yield session
                    raise OperationalError("PRIVATE_COMMIT_SQL", {}, RuntimeError("PRIVATE_CONNECTION"))
            return transaction()

    monkeypatch.setattr(importer, "_configured_engine", lambda: db[0])
    monkeypatch.setattr(importer, "session_factory", lambda engine: FailingSessions())
    assert importer.main(cli_args(True)) == 2
    output = capsys.readouterr().out
    assert "committed" not in output and "PRIVATE" not in output
    assert counts(db)["projects"] == counts(db)["investigations"] == 0
