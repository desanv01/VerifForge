"""Independent synthetic SQLite/HTTP review, executed only after owner freeze.

Trusted fixture arrangers provide valid starting evidence; no actual bundle or
native/tool job runs. Metadata-created temporary SQLite permits explicit storage
fault injection; this does not claim migration/SQL-trigger verification.
"""
from datetime import datetime, timezone
from hashlib import sha256
import importlib.util
from pathlib import Path

from fastapi import FastAPI
from fastapi.responses import JSONResponse
from fastapi.testclient import TestClient
import pytest
import sqlalchemy as sa
from sqlalchemy.orm import Session

from veriforge.api.investigations import investigations_router
from veriforge.contracts import Experiment
from veriforge.core import investigations as store, service
from veriforge.core.catalog import seed_catalog
from veriforge.core.database import (Base, Project, Campaign, Attempt, Investigation, InvestigationVersion,
    InvestigationArtifact, InvestigationExecutionLink, make_engine, session_factory)
from veriforge.core.engineering_catalog import EngineeringProjectSpec, create_engineering_project
from veriforge.system_investigation import records
from veriforge.system_investigation import native_tool_records as native

AT = datetime(2026, 10, 30, tzinfo=timezone.utc)
ROOT = Path(__file__).resolve().parents[2]


def trusted_helper(path, name):
    spec = importlib.util.spec_from_file_location(name, ROOT / path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture(scope="module")
def helpers():
    return (trusted_helper("tests/system_investigation/test_native_tool_records.py", "persistence_pure_seed"),
            trusted_helper("tests/core/test_investigations.py", "persistence_legacy_seed"))


@pytest.fixture
def db(tmp_path):
    engine = make_engine("sqlite:///" + (tmp_path / "independent-native-store.db").as_posix())
    Base.metadata.create_all(engine)
    sessions = session_factory(engine)
    with sessions.begin() as session:
        seed_catalog(session)
        create_engineering_project(session, EngineeringProjectSpec(project_id="recorded-project",
            name="Independent synthetic recorded evidence", domains=("riscv", "firmware", "system")))
    yield engine, sessions
    engine.dispose()


@pytest.fixture
def bundle(helpers):
    return helpers[0].bundle(helpers[0].synthetic_riscv())


@pytest.fixture
def client(db):
    app = FastAPI()

    @app.exception_handler(service.NotFound)
    async def missing(request, error):
        return JSONResponse({"detail": "Not found"}, status_code=404)

    @app.exception_handler(service.Conflict)
    async def conflict(request, error):
        return JSONResponse({"detail": str(error)}, status_code=409)

    app.include_router(investigations_router(db[1]))
    with TestClient(app) as c:
        yield c


def persist(db, bundle, key="independent-native", **options):
    record, blobs, authority = bundle
    with db[1].begin() as session:
        return store.persist_investigation(session, record, blobs, authority=authority,
            idempotency_key=key, at=AT, **options)


def url(row):
    return f"/api/investigations/{row['investigation_id']}/versions/{row['id']}"


def counts(db):
    with db[0].connect() as connection:
        return {table: connection.execute(sa.text(f'SELECT COUNT(*) FROM "{table}"')).scalar_one()
                for table in sa.inspect(connection).get_table_names()}


def test_v1_default_and_sql_filtered_cursor_pages_with_interleaved_native_roots(db, bundle, helpers, client, request):
    root_ids = iter(("a-native", "c-native", "e-legacy", "g-native"))

    def assign_ids(session, context, instances):
        for item in session.new:
            if type(item) is Investigation and item.id is None:
                item.id = next(root_ids)

    sa.event.listen(Session, "before_flush", assign_ids)
    request.addfinalizer(lambda: sa.event.remove(Session, "before_flush", assign_ids))
    a = persist(db, bundle, "a")
    c = persist(db, bundle, "c")
    legacy_bundle = helpers[1].valid_bundle(project="recorded-project")
    old = persist(db, legacy_bundle, "e")
    g = persist(db, bundle, "g")
    path = "/api/projects/recorded-project/investigations"
    default = client.get(path, params={"limit": 1}).json()
    assert [item["id"] for item in default["items"]] == [old["investigation_id"]]
    assert default["next_cursor"] is None
    found, cursor = [], ""
    for _ in range(4):
        page = client.get(path, params={"admission_schema": "v2", "limit": 1, "after": cursor}).json()
        assert page["items"], "filtering after limit must not return an empty intermediate page"
        found.extend(item["id"] for item in page["items"])
        cursor = page["next_cursor"]
        if cursor is None:
            break
    assert found == [a["investigation_id"], c["investigation_id"], g["investigation_id"]]
    assert [item["id"] for item in client.get(path, params={"admission_schema": "all"}).json()["items"]] == ["a-native", "c-native", "e-legacy", "g-native"]
    assert client.get(f"/api/investigations/{a['investigation_id']}").status_code == 404
    old_detail = client.get(url(old)).json()
    assert "bridge_report" in old_detail and old_detail["execution_contexts"] == []
    assert "native_report" not in old_detail and "report_kind" not in old_detail
    with db[1]() as session:
        assert session.get(InvestigationVersion, old["id"]).content == records.serialize_investigation(
            records.admit_investigation(legacy_bundle[0], legacy_bundle[1], authority=legacy_bundle[2]))


@pytest.mark.parametrize("failure", ["changed", "unreadable"])
def test_fresh_validator_required_for_every_native_read_and_before_matching_etag(db, bundle, client, helpers, monkeypatch, failure):
    row = persist(db, bundle)
    old = persist(db, helpers[1].valid_bundle(project="recorded-project"), "legacy")
    detail = client.get(url(row))
    assert detail.status_code == 200
    etag = detail.headers["etag"]
    blob_id = client.get(url(row) + "/artifacts").json()["items"][0]["id"]
    views = ("/api/projects/recorded-project/investigations?admission_schema=v2",
        f"/api/investigations/{row['investigation_id']}?admission_schema=v2",
        f"/api/investigations/{row['investigation_id']}/versions?admission_schema=v2",
        url(row), url(row) + "/artifacts", url(row) + f"/artifacts/{blob_id}/download")
    real_hash = store._installed_native_validator_hash
    calls = []

    def fresh():
        calls.append(True)
        return real_hash()

    monkeypatch.setattr(store, "_installed_native_validator_hash", fresh)
    for view in views:
        before = len(calls)
        assert client.get(view).status_code == 200
        assert len(calls) > before

    def unavailable():
        raise ValueError("SYNTHETIC_PRIVATE_SOURCE_PATH")

    monkeypatch.setattr(store, "_installed_native_validator_hash", unavailable if failure == "unreadable" else lambda: "f" * 64)
    for view in views:
        response = client.get(view, headers={"If-None-Match": etag})
        assert response.status_code == 409 and "PRIVATE_SOURCE_PATH" not in response.text
    assert client.get(url(old)).status_code == 200
    before = counts(db)
    with pytest.raises(service.Conflict):
        persist(db, bundle)
    assert counts(db) == before


def test_native_id_collision_never_resolves_local_attempt_or_execution_origin(db, bundle, client, monkeypatch):
    producer_id = bundle[2].pins.locators[0].native_attempt_id
    with db[1].begin() as session:
        plan = Experiment(experiment_id="local-collision", fixture_id="fifo_correct",
            objective_ids=("FIFO_BEHAVIOR",), operations=({"op": "reset"},))
        c = service.create_campaign(session, "fifo_correct", plan.model_dump(mode="json"), "local-collision")
        session.add(Attempt(id=producer_id, campaign_id=c.id))
    before = counts(db)
    original_get = Session.get

    def guarded_get(self, entity, *args, **kwargs):
        assert entity is not Attempt, "external native ID caused a local Attempt lookup"
        return original_get(self, entity, *args, **kwargs)

    monkeypatch.setattr(Session, "get", guarded_get)
    monkeypatch.setattr(store, "_db_context", lambda *a, **k: pytest.fail("native DB context lookup"))
    monkeypatch.setattr(store, "_origin", lambda *a, **k: pytest.fail("native execution origin lookup"))
    row = persist(db, bundle)
    assert client.get(url(row)).status_code == 200
    after = counts(db)
    allowed = {"investigations", "investigation_versions", "investigation_artifacts"}
    assert all(before[name] == after[name] for name in before if name not in allowed)
    assert after["investigation_execution_links"] == 0


@pytest.mark.parametrize("corruption", ["bytes", "role", "identity_key", "envelope"])
def test_storage_corruption_cannot_hide_behind_etag_or_idempotency(db, bundle, client, corruption):
    row = persist(db, bundle)
    etag = client.get(url(row)).headers["etag"]
    with db[1].begin() as session:
        if corruption == "envelope":
            session.execute(sa.update(InvestigationVersion).where(InvestigationVersion.id == row["id"]).values(
                content=b"{}", content_hash=sha256(b"{}").hexdigest()))
        else:
            blob = session.scalar(sa.select(InvestigationArtifact).where(InvestigationArtifact.version_id == row["id"]))
            changes = {"data": b"CORRUPTED", "size_bytes": 9} if corruption == "bytes" else {
                "role" if corruption == "role" else "identity_key": "not-a-role" if corruption == "role" else "f" * 64}
            session.execute(sa.update(InvestigationArtifact).where(InvestigationArtifact.id == blob.id).values(**changes))
    assert client.get(url(row), headers={"If-None-Match": etag}).status_code == 409
    assert client.get(url(row) + "/artifacts").status_code == 409
    with pytest.raises(service.Conflict):
        persist(db, bundle)


def test_even_fk_coherent_campaign_only_execution_link_is_corruption(db, bundle, client):
    row = persist(db, bundle)
    with db[1].begin() as session:
        # Deliberately bypass public campaign admission for a corruption fixture.
        # No Attempt/Experiment or dispatch needed; all declared FKs stay enabled.
        contract = session.get(Project, "fifo_correct").contract_id
        campaign = Campaign(project_id="recorded-project", contract_id=contract, create_key="planted-context",
            request_hash="a" * 64, plan={}, identity={})
        session.add(campaign)
        session.flush()
        session.add(InvestigationExecutionLink(version_id=row["id"], project_id="recorded-project",
            campaign_id=campaign.id, attempt_id=None, experiment_id=None, result_hash=None,
            association_hash="b" * 64, content=b"{}"))
    assert client.get(url(row)).status_code == 409


def test_immutable_original_revision_idempotency_append_and_outer_rollback(db, bundle, client):
    row = persist(db, bundle)
    assert persist(db, bundle) == row
    with db[1]() as session:
        original = session.get(InvestigationVersion, row["id"]).content
    second = persist(db, bundle, "appended", investigation_id=row["investigation_id"])
    assert second["ordinal"] == 2
    with db[1]() as session:
        assert session.get(InvestigationVersion, row["id"]).content == original
    before = counts(db)
    with pytest.raises(RuntimeError):
        with db[1].begin() as session:
            store.persist_investigation(session, bundle[0], bundle[1], authority=bundle[2],
                idempotency_key="rolled-back", at=AT, investigation_id=row["investigation_id"])
            raise RuntimeError("independent outer rollback")
    assert counts(db) == before
    # Advance the associated engineering revision, preserving every producer byte
    # and original native ID. Old version reads must never substitute the latest
    # project-revision association.
    admitted = records.admit_investigation(bundle[0], bundle[1], authority=bundle[2])
    blobs = {store._key(item.reference): item for item in bundle[1]}

    def replace(reference, value):
        raw = records._encode(value)
        ref = reference.model_copy(update={"sha256": sha256(raw).hexdigest()})
        blobs[store._key(ref)] = records.ArtifactBytes(reference=ref, data=raw)
        return ref

    revision_id = "independent-new-associated-revision"
    project_ref = replace(bundle[0].project_revision_ref, admitted.project_revision.model_copy(update={"revision_id": revision_id}))
    manifest_ref = replace(bundle[0].manifest_ref, admitted.manifest.model_copy(update={"revision_id": revision_id}))
    selected = tuple(binding.model_copy(update={"ref": manifest_ref,
        "size_bytes": len(blobs[store._key(manifest_ref)].data)}) if binding.ref == bundle[0].manifest_ref else binding
        for binding in admitted.native_report.selection.selected)
    report_ref = replace(bundle[0].report_ref, admitted.native_report.model_copy(update={"manifest_ref": manifest_ref,
        "selection": admitted.native_report.selection.model_copy(update={"selected": selected})}))
    updated = bundle[0].model_copy(update={"revision_id": revision_id, "project_revision_ref": project_ref,
                                          "manifest_ref": manifest_ref, "report_ref": report_ref})
    authority = native.create_external_admission_authority(project_id=updated.project_id, revision_id=revision_id,
        project_revision_ref=project_ref, manifest_ref=manifest_ref, report_kind=updated.report_kind,
        report_ref=report_ref, locators=bundle[2].pins.locators,
        validator_implementation_hash=bundle[2].pins.validator_implementation_hash)
    newer = persist(db, (updated, tuple(blobs.values()), authority), "new-associated-revision",
                    investigation_id=row["investigation_id"])
    assert newer["ordinal"] == 3
    assert client.get(url(newer)).json()["project_revision"]["revision_id"] == revision_id
    detail = client.get(url(row)).json()
    assert detail["project_revision"]["revision_id"] == bundle[0].revision_id
    assert detail["revision_hash"] == bundle[0].project_revision_ref.sha256
    versions = f"/api/investigations/{row['investigation_id']}/versions"
    first_page = client.get(versions, params={"admission_schema": "v2", "limit": 1}).json()
    assert first_page["items"][0]["id"] == row["id"] and first_page["next_cursor"] == 1
    assert client.get(versions, params={"admission_schema": "v2", "after": 1}).json()["items"][0]["id"] == second["id"]


@pytest.mark.parametrize("mixed", ["v1", "firmware"])
def test_history_cannot_switch_schema_or_native_report_family(db, bundle, helpers, tmp_path, mixed):
    row = persist(db, bundle)
    other = helpers[1].valid_bundle(project="recorded-project") if mixed == "v1" else helpers[0].bundle(helpers[0].synthetic_firmware(tmp_path), "firmware")
    before = counts(db)
    with pytest.raises(service.Conflict, match="family"):
        persist(db, other, "mixed", investigation_id=row["investigation_id"])
    assert counts(db) == before


def test_selectors_resource_ownership_and_read_only_router(db, bundle, client):
    first = persist(db, bundle)
    second = persist(db, bundle, "second")
    for path in ("/api/projects/recorded-project/investigations", f"/api/investigations/{first['investigation_id']}",
                 f"/api/investigations/{first['investigation_id']}/versions"):
        assert client.get(path, params={"admission_schema": "engineering.admission.v2"}).status_code == 422
    assert client.get(f"/api/investigations/{first['investigation_id']}/versions/{second['id']}").status_code == 404
    blob = client.get(url(second) + "/artifacts").json()["items"][0]
    assert client.get(url(first) + f"/artifacts/{blob['id']}/download").status_code == 404
    assert client.post(url(first), json={}).status_code == 405
    assert all(route.methods <= {"GET", "HEAD"} for route in client.app.routes if getattr(route, "path", "").startswith("/api/"))
    with db[1].begin() as session:
        session.execute(sa.update(Project).where(Project.id == "recorded-project").values(owner="foreign"))
    assert client.get(url(first)).json() == {"detail": "Not found"}


def test_exact_download_bytes_and_distinct_v2_payload(db, bundle, client):
    row = persist(db, bundle)
    detail = client.get(url(row)).json()
    assert detail["schema_version"] == "engineering.admission.v2" and detail["execution_status"] == "not_run"
    assert "native_report" in detail and "bridge_report" not in detail and "execution_contexts" not in detail
    originals = {(b.reference.artifact_id, b.reference.revision_id, b.reference.attempt_id): b.data for b in bundle[1]}
    items = client.get(url(row) + "/artifacts").json()["items"]
    for item in (items[0], items[-1]):
        response = client.get(url(row) + f"/artifacts/{item['id']}/download")
        assert response.content == originals[item["artifact_id"], item["revision_id"], item["attempt_id"]]
        assert response.headers["content-disposition"] == f'attachment; filename="{item["sha256"]}.bin"'
        assert response.headers["cache-control"] == "no-store" and response.headers["x-content-type-options"] == "nosniff"


def test_health_advertises_only_fixed_capabilities_without_evidence_or_source_scan(db, monkeypatch):
    from veriforge.api.app import create_app

    def forbidden(*args, **kwargs):
        pytest.fail("health attempted evidence/source revalidation")

    monkeypatch.setattr(store, "_installed_native_validator_hash", forbidden)
    monkeypatch.setattr(records, "admit_investigation", forbidden)
    monkeypatch.setattr(records, "read_investigation", forbidden)
    before = counts(db)
    with TestClient(create_app(db[0], initialize_catalog=False)) as c:
        response = c.get("/api/health")
    assert response.status_code == 200
    data = response.json()
    assert data["investigation_api"] == data["evidence_api"] == "1"
    assert data["investigation_admission_schemas"] == ["engineering.admission.v1", "engineering.admission.v2"]
    assert data["investigation_report_kinds"] == ["riscv.recorded-report.v1", "firmware.recorded-report.v1"]
    assert data["model"] == "not_integrated" and data["execution"] == "separate_worker"
    assert not any("ready" in key for key in data)
    assert counts(db) == before
