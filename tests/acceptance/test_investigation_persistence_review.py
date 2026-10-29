"""Independent temporary SQLite/in-process HTTP review; all native inputs synthetic."""
from concurrent.futures import ThreadPoolExecutor
import hashlib
import importlib.util
from pathlib import Path
import runpy
from types import SimpleNamespace

from fastapi.testclient import TestClient
import pytest
import sqlalchemy as sa
from sqlalchemy.exc import IntegrityError

from veriforge.api.app import create_app
from veriforge.core import investigations as store, service
from veriforge.core.catalog import seed_catalog
from veriforge.core.database import (Base, Investigation, InvestigationArtifact, InvestigationVersion,
    InvestigationExecutionLink, Attempt, Dispatch, make_engine, session_factory)
from veriforge.system_investigation import records

ROOT = Path(__file__).resolve().parents[2]
# Reuse only synthetic byte/context builders, never owner test assertions/suites.
BUILDERS = runpy.run_path(str(ROOT / "tests/core/test_investigations.py"))
bundle, execution_bundle, AT = BUILDERS["valid_bundle"], BUILDERS["execution_bundle"], BUILDERS["AT"]


def migration_module():
    spec = importlib.util.spec_from_file_location("review_u9_offline", ROOT / "migrations/versions/11u9investigations_durable_investigations.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture
def db(tmp_path, monkeypatch):
    engine = make_engine("sqlite:///" + (tmp_path / "review.db").as_posix())
    Base.metadata.create_all(engine)
    # No Alembic migration command. Capture delivered trigger DDL offline, then
    # install only those triggers in this disposable synthetic schema fixture.
    ddl = []
    migration = migration_module()
    monkeypatch.setattr(migration, "op", SimpleNamespace(create_index=lambda *a, **kw: None,
        create_table=lambda *a, **kw: None, get_bind=lambda: SimpleNamespace(dialect=engine.dialect),
        execute=lambda sql: ddl.append(str(sql))))
    migration.upgrade()
    with engine.begin() as conn:
        for statement in ddl:
            conn.execute(sa.text(statement))
    sessions = session_factory(engine)
    with sessions.begin() as session:
        seed_catalog(session)
    yield engine, sessions
    engine.dispose()


def persist(db, value=None, key="review", **kwargs):
    record, blobs, authority = value or bundle()
    with db[1].begin() as session:
        return store.persist_investigation(session, record, blobs, authority=authority,
            idempotency_key=key, at=AT, **kwargs)


def url(row):
    return f"/api/investigations/{row['investigation_id']}/versions/{row['id']}"


def counts(db):
    with db[1]() as session:
        return tuple(session.scalar(sa.select(sa.func.count()).select_from(cls))
            for cls in (Investigation, InvestigationVersion, InvestigationArtifact, InvestigationExecutionLink, Dispatch))


def test_concurrent_append_idempotency_and_failed_transaction_publish_no_partial_graph(db):
    first = persist(db)
    def append(index):
        return persist(db, bundle(revision="review-" + str(index)), key="append-" + str(index), investigation_id=first["investigation_id"])
    with ThreadPoolExecutor(max_workers=2) as pool:
        appended = list(pool.map(append, (2, 3)))
    assert {item["ordinal"] for item in appended} == {2, 3}
    assert persist(db) == first
    before = counts(db)
    with pytest.raises(service.Conflict):
        persist(db, bundle(revision="changed-same-key"))
    assert counts(db) == before
    with pytest.raises(RuntimeError):
        with db[1].begin() as session:
            record, blobs, authority = bundle(revision="rolled-back")
            store.persist_investigation(session, record, blobs, authority=authority, idempotency_key="rollback",
                at=AT, investigation_id=first["investigation_id"])
            raise RuntimeError("synthetic crash before transaction commit")
    assert counts(db) == before and before[-1] == 0


@pytest.mark.parametrize("malformation", ["copied_record", "copied_blob", "pre_admitted"])
def test_untrusted_constructor_objects_are_readmitted_before_publication(db, malformation):
    record, blobs, authority = bundle()
    if malformation == "copied_record":
        record = record.model_copy(update={"project_id": "other-project"})
    elif malformation == "copied_blob":
        blobs = (blobs[0].model_copy(update={"data": b"PRIVATE-BYTES-SUBSTITUTION"}), *blobs[1:])
    else:
        record = records.admit_investigation(record, blobs, authority=authority)
    with pytest.raises((ValueError, TypeError, AttributeError)):
        persist(db, (record, blobs, authority))
    assert counts(db) == (0, 0, 0, 0, 0)


@pytest.mark.parametrize("field", ["db_attempt_id", "experiment_record_id", "wire_experiment_id"])
def test_native_and_database_namespaces_cannot_be_substituted(db, field):
    value, native = execution_bundle(db)
    record, blobs, authority = value
    original = record.attempts[0]
    replacement = native.attempt_id if field == "db_attempt_id" else original.wire_experiment_id if field == "experiment_record_id" else original.experiment_record_id
    binding = original.model_copy(update={field: replacement})
    record = record.model_copy(update={"attempts": (binding,)})
    authority = records.create_admission_authority(project_id=record.project_id, revision_id=record.revision_id,
        project_revision_ref=record.project_revision_ref, manifest_ref=record.manifest_ref,
        bridge_report_ref=record.bridge_report_ref, attempts=record.attempts)
    with pytest.raises((ValueError, service.Conflict, service.NotFound)):
        persist(db, (record, blobs, authority))
    assert counts(db)[:4] == (0, 0, 0, 0)


@pytest.mark.parametrize("retained", ["exact", "equivalent", "missing"])
def test_recorded_origin_requires_exact_original_bytes_and_never_claims_new_execution(db, tmp_path, retained):
    value, native = execution_bundle(db)
    binding = value[0].attempts[0]
    root = tmp_path / "evidence"
    path = root / binding.db_attempt_id
    path.mkdir(parents=True)
    with db[1]() as session:
        from veriforge.core.planner_experiments import resolve_attempt_experiment
        experiment = resolve_attempt_experiment(session, session.get(Attempt, binding.db_attempt_id))
        import json
        (path / "experiment.json").write_text(json.dumps(experiment.plan))
    if retained != "missing":
        exact = next(blob.data for blob in value[1] if blob.reference == binding.result_ref)
        (path / "result.json").write_bytes(exact if retained == "exact" else native.model_dump_json(indent=2).encode())
    row = persist(db, value, artifact_root=root)
    with db[1]() as session:
        detail = store.version_view(session, row["investigation_id"], row["id"])
        assert detail["execution_status"] == "not_run" and detail["scope"] == "recorded_import"
        assert detail["execution_contexts"][0]["origin_at_admission"] == ("retained_execution_bytes" if retained == "exact" else "context_only")
        assert session.get(Attempt, binding.db_attempt_id).result["attempt_id"] == native.attempt_id != binding.db_attempt_id


def test_http_safe_download_identity_and_etag_cannot_hide_corruption(db):
    row, other = persist(db), persist(db, key="other")
    with TestClient(create_app(db[0], initialize_catalog=False)) as client:
        response = client.get(url(row))
        assert response.status_code == 200
        etag = response.headers["etag"]
        assert client.get(url(row), headers={"If-None-Match": etag}).status_code == 304
        items = client.get(url(row) + "/artifacts").json()["items"]
        source = next(item for item in items if item["artifact_id"] == "header")
        download = url(row) + f"/artifacts/{source['id']}/download"
        raw = client.get(download)
        assert raw.headers["content-type"] == "application/octet-stream"
        assert raw.headers["content-disposition"] == f'attachment; filename="{source["sha256"]}.bin"'
        assert raw.headers["cache-control"] == "no-store" and raw.headers["x-content-type-options"] == "nosniff"
        assert hashlib.sha256(raw.content).hexdigest() == source["sha256"]
        assert "PRIVATE_SOURCE_ONLY" not in response.text
        assert client.get(url(other) + f"/artifacts/{source['id']}/download").status_code == 404
        assert client.post(url(row), json={"execution_status": "completed"}).status_code == 405
        # Controlled storage corruption only in this disposable test DB.
        with db[0].begin() as conn:
            conn.execute(sa.text("DROP TRIGGER immutable_investigation_artifacts_update"))
            conn.execute(sa.update(InvestigationArtifact).where(InvestigationArtifact.id == source["id"]).values(data=b"!" * source["size_bytes"]))
        for endpoint in (url(row), url(row) + "/artifacts", download):
            failure = client.get(endpoint, headers={"If-None-Match": etag})
            assert failure.status_code == 409
            assert failure.json() == {"detail": "Investigation evidence is unavailable or changed"}
            assert "PRIVATE_SOURCE_ONLY" not in failure.text


def schema_only_version(db):
    # Synthetic SQL schema fixture only; never admitted native evidence.
    with db[1].begin() as session:
        parent = Investigation(project_id="fifo_correct", create_key="schema-only", request_hash="a" * 64)
        session.add(parent)
        session.flush()
        version = InvestigationVersion(investigation_id=parent.id, project_id=parent.project_id, ordinal=1,
            admission_key="schema", schema_version="schema", revision_id="schema", revision_hash="a" * 64,
            manifest_hash="b" * 64, content_hash="c" * 64, content=b"{}")
        session.add(version)
        session.flush()
        return version.id


def add_blob(db, version, number, data=b""):
    with db[1].begin() as session:
        session.add(InvestigationArtifact(version_id=version, artifact_id="quota-" + str(number),
            native_revision_id="synthetic", identity_key=hashlib.sha256(str(number).encode()).hexdigest(),
            sha256=hashlib.sha256(data).hexdigest(), size_bytes=len(data), media_type="application/octet-stream", role="source", data=data))


def test_concurrent_zero_byte_blobs_cannot_bypass_count_quota(db):
    version = schema_only_version(db)
    for index in range(255):
        add_blob(db, version, index)
    def race(index):
        try:
            add_blob(db, version, index)
            return "accepted"
        except IntegrityError:
            return "quota"
    with ThreadPoolExecutor(max_workers=2) as pool:
        assert sorted(pool.map(race, (255, 256))) == ["accepted", "quota"]


def test_sql_byte_quotas_and_immutable_version_are_enforced(db):
    version = schema_only_version(db)
    for index in range(16):
        add_blob(db, version, index, b"x" * 1_048_576)
    for payload in (b"x", b"x" * 1_048_577):
        with pytest.raises(IntegrityError):
            add_blob(db, version, 17, payload)
    with pytest.raises(IntegrityError):
        with db[0].begin() as conn:
            conn.execute(sa.update(InvestigationVersion).where(InvestigationVersion.id == version).values(content=b"[]"))


def test_offline_downgrade_checks_all_history_before_any_drop(db, monkeypatch):
    row = persist(db)
    module = migration_module()
    drops = []
    with db[0].connect() as conn:
        monkeypatch.setattr(module, "op", SimpleNamespace(get_bind=lambda: conn,
            drop_table=lambda *args, **kwargs: drops.append(args), drop_index=lambda *args, **kwargs: drops.append(args)))
        with pytest.raises(ValueError, match="retained investigation"):
            module.downgrade()
    assert drops == []
    with db[1]() as session:
        assert store.version_view(session, row["investigation_id"], row["id"])["scope"] == "recorded_import"


def test_cross_project_context_and_raw_composite_fk_cannot_reanchor_attempt(db):
    value, native = execution_bundle(db)
    binding = value[0].attempts[0]
    result_blob = next(blob for blob in value[1] if blob.reference == binding.result_ref)
    foreign_value = bundle(project="fifo_reset_bug", attempts=(binding,), extra=(result_blob,))
    with pytest.raises(service.Conflict):
        persist(db, foreign_value)
    foreign = persist(db, bundle(project="fifo_reset_bug"))
    with pytest.raises(IntegrityError):
        with db[1].begin() as session:
            session.add(InvestigationExecutionLink(version_id=foreign["id"], project_id="fifo_reset_bug",
                campaign_id=binding.campaign_id, attempt_id=binding.db_attempt_id,
                experiment_id=binding.experiment_record_id, result_hash="a" * 64,
                association_hash="b" * 64, content=b"{}"))
    with db[1]() as session:
        assert session.scalar(sa.select(sa.func.count()).select_from(InvestigationExecutionLink)) == 0


def test_changed_db_result_cannot_hide_behind_etag_or_safe_download(db):
    value, native = execution_bundle(db)
    row = persist(db, value)
    with TestClient(create_app(db[0], initialize_catalog=False)) as client:
        baseline = client.get(url(row))
        assert baseline.status_code == 200
        etag = baseline.headers["etag"]
        blob = client.get(url(row) + "/artifacts").json()["items"][0]
        with db[1].begin() as session:
            attempt = session.get(Attempt, value[0].attempts[0].db_attempt_id)
            attempt.result = {**attempt.result, "uncertainties": ["PRIVATE_CHANGED_RESULT_CONTEXT"]}
        for endpoint in (url(row), url(row) + f"/artifacts/{blob['id']}/download"):
            response = client.get(endpoint, headers={"If-None-Match": etag})
            assert response.status_code == 409
            assert response.json() == {"detail": "Investigation evidence is unavailable or changed"}
            assert "PRIVATE_CHANGED_RESULT_CONTEXT" not in response.text
