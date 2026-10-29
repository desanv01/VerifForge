"""Independent isolated SQLite/API evidence checks. No simulator/broker/Docker."""

import hashlib
import json
from contextlib import contextmanager
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from alembic.script import ScriptDirectory
from fastapi.testclient import TestClient
from sqlalchemy import select, text

from veriforge.api.app import create_app
from veriforge.contracts import ArtifactReference, AttemptResult, Experiment
from veriforge.core import artifacts, service
from veriforge.core.catalog import seed_catalog
from veriforge.core.database import Attempt, Campaign, EvidenceExport, make_engine, session_factory
from veriforge.rtl.oracle import expand


@pytest.fixture
def evidence_store(tmp_path):
    engine = make_engine("sqlite:///" + (tmp_path / "review.db").as_posix())
    config = Config("alembic.ini")
    with engine.begin() as connection:
        config.attributes["connection"] = connection
        command.upgrade(config, "head")
    sessions = session_factory(engine)
    with sessions.begin() as session:
        seed_catalog(session)
    root = tmp_path / "artifacts"

    def create(key="ordinary", seed=0):
        experiment = Experiment(experiment_id="independent-evidence", fixture_id="fifo_correct",
                                objective_ids=("FIFO_BEHAVIOR",), seed=seed,
                                operations=({"op": "reset"}, {"op": "write", "value": 37}, {"op": "read"}))
        with sessions.begin() as session:
            campaign = service.create_campaign(session, experiment.fixture_id, experiment.model_dump(mode="json"), key)
            service.start(session, campaign.id)
            attempt = session.scalar(select(Attempt).where(Attempt.campaign_id == campaign.id))
            lease, _, _ = service.claim(session, attempt.id)
            directory = root / attempt.id
            directory.mkdir(parents=True)
            files = {"experiment.json": experiment.model_dump_json().encode(),
                     "stimulus.json": json.dumps(expand(experiment)).encode(),
                     "execution.log": b"Synthetic independent API review; no simulator executed"}
            refs = []
            for index, (name, data) in enumerate(files.items()):
                (directory / name).write_bytes(data)
                refs.append(ArtifactReference(artifact_id=f"ref-{index}", path=name, size_bytes=len(data),
                                              sha256=hashlib.sha256(data).hexdigest(), media_type="text/plain"))
            result = AttemptResult(attempt_id="independent-adapter-id", experiment_id=experiment.experiment_id,
                                   execution_status="completed", verdict="pass_within_scope",
                                   identity={**campaign.identity, "environment_hash": "c" * 64}, artifacts=tuple(refs),
                                   uncertainties=("Synthetic checker record; no simulator executed",))
            (directory / "result.json").write_text(result.model_dump_json())
            assert service.finalize(session, attempt.id, lease, result.model_dump(mode="json"))
            return dict(campaign_id=campaign.id, attempt_id=attempt.id, directory=directory, result=result, experiment=experiment)

    @contextmanager
    def client():
        with TestClient(create_app(engine, artifact_root=root)) as instance:
            yield instance

    yield dict(sessions=sessions, root=root, create=create, client=client)
    engine.dispose()


@pytest.mark.parametrize("collision_with_source", [True, False])
def test_replay_cannot_reuse_existing_campaign_through_public_create_key_collision(evidence_store, collision_with_source):
    store = evidence_store
    replay_key = "independent-collision"
    # Public campaign creation accepts this key; internal replay must have a
    # separate authority/namespace instead of returning the existing source.
    collision_key = "replay:" + service.digest({"key": replay_key})
    source = store["create"](collision_key if collision_with_source else "ordinary")
    ordinary = source if collision_with_source else store["create"](collision_key)
    with store["client"]() as client:
        before = client.get(f"/api/campaigns/{source['campaign_id']}").json()
        ordinary_before = client.get(f"/api/campaigns/{ordinary['campaign_id']}").json()
        response = client.post(f"/api/attempts/{source['attempt_id']}/replay", headers={"Idempotency-Key": replay_key})
        assert response.status_code == 202, response.text
        assert response.json()["id"] != source["campaign_id"]
        assert response.json()["id"] != ordinary["campaign_id"]
        assert response.json()["execution_status"] == "queued"
        assert client.get(f"/api/campaigns/{source['campaign_id']}").json() == before
        assert client.get(f"/api/campaigns/{ordinary['campaign_id']}").json() == ordinary_before


def test_capability_marker_and_additive_migration_are_present(evidence_store):
    store = evidence_store
    scripts = ScriptDirectory.from_config(Config("alembic.ini"))
    with store["sessions"]() as session:
        revision = session.execute(text("SELECT version_num FROM alembic_version")).scalar_one()
        assert revision == scripts.get_current_head()
        assert "6bf022evidence" in {item.revision for item in scripts.walk_revisions()}
    with store["client"]() as client:
        health = client.get("/api/health").json()
        assert health["evidence_api"] == "1"
        assert health["evidence_adapters"] == ["rtl.arithmetic.v1", "rtl.fifo.v1"]


def test_download_uses_database_attempt_and_verified_byte_snapshot(evidence_store, monkeypatch):
    import veriforge.evidence

    store = evidence_store
    source = store["create"]()
    original_bytes = (source["directory"] / "stimulus.json").read_bytes()
    original = veriforge.evidence.portable_artifact_bytes

    def change_after_snapshot(*args, **kwargs):
        data = original(*args, **kwargs)
        (source["directory"] / "stimulus.json").write_bytes(b"replacement must not enter response")
        return data

    monkeypatch.setattr(veriforge.evidence, "portable_artifact_bytes", change_after_snapshot)
    with store["client"]() as client:
        response = client.get(f"/api/attempts/{source['attempt_id']}/artifacts/ref-1/download")
        assert response.status_code == 200 and response.content == original_bytes
        assert response.headers["cache-control"] == "no-store"
        assert response.headers["x-content-type-options"] == "nosniff"
        assert client.get("/api/attempts/independent-adapter-id/artifacts/ref-1/download").status_code == 404
        assert client.get(f"/api/attempts/{source['attempt_id']}/artifacts/ref-1/download").status_code == 409


def test_matching_disk_and_db_records_cannot_override_campaign_identity(evidence_store):
    store = evidence_store
    source = store["create"]()
    changed = source["result"].model_copy(update={"identity": source["result"].identity.model_copy(update={"checker_hash": "d" * 64})})
    (source["directory"] / "result.json").write_text(changed.model_dump_json())
    with store["sessions"].begin() as session:
        session.get(Attempt, source["attempt_id"]).result = changed.model_dump(mode="json")
    with store["client"]() as client:
        assert client.get(f"/api/attempts/{source['attempt_id']}/artifacts").status_code == 409
        assert client.post(f"/api/attempts/{source['attempt_id']}/replay", headers={"Idempotency-Key": "blocked"}).status_code == 409


def test_storage_root_ancestor_junction_policy_is_enforced(evidence_store, monkeypatch):
    store = evidence_store
    source = store["create"]()
    original = Path.is_junction
    monkeypatch.setattr(Path, "is_junction", lambda path: path == store["root"].parent or original(path))
    with store["client"]() as client:
        assert client.get(f"/api/attempts/{source['attempt_id']}/artifacts").status_code == 409


def test_hash_consistent_artifact_still_requires_portable_content_screening(evidence_store):
    store = evidence_store
    source = store["create"]()
    # Deliberately fake credential marker, never a real credential.
    data = b"MODEL_API_KEY=independent-fake-secret"
    (source["directory"] / "stimulus.json").write_bytes(data)
    refs = tuple(ref.model_copy(update={"sha256": hashlib.sha256(data).hexdigest(), "size_bytes": len(data)})
                 if ref.path == "stimulus.json" else ref for ref in source["result"].artifacts)
    changed = source["result"].model_copy(update={"artifacts": refs})
    (source["directory"] / "result.json").write_text(changed.model_dump_json())
    with store["sessions"].begin() as session:
        session.get(Attempt, source["attempt_id"]).result = changed.model_dump(mode="json")
    with store["client"]() as client:
        # Hash, size, disk/DB equality and campaign identity must pass first;
        # otherwise a 409 would not establish portable-content screening.
        assert client.get(f"/api/attempts/{source['attempt_id']}/artifacts").status_code == 200
        response = client.get(f"/api/attempts/{source['attempt_id']}/artifacts/ref-1/download")
        assert response.status_code == 409
        assert "independent-fake-secret" not in response.text


def test_existing_export_endpoints_recheck_attempt_ownership(evidence_store):
    store = evidence_store
    source = store["create"]()
    with store["client"]() as client:
        response = client.post(f"/api/attempts/{source['attempt_id']}/exports", headers={"Idempotency-Key": "export-owner"})
        export_id = response.json()["id"]
        with store["sessions"].begin() as session:
            session.get(Campaign, source["campaign_id"]).owner = "another-local-owner"
        assert client.get(f"/api/exports/{export_id}").status_code == 404
        assert client.get(f"/api/exports/{export_id}/download").status_code == 404
        assert client.post(f"/api/exports/{export_id}/cancel", headers={"Idempotency-Key": "cancel"}).status_code == 404


def test_export_source_tamper_between_validation_and_private_copy_never_publishes(evidence_store, monkeypatch):
    store = evidence_store
    source = store["create"]()
    original = artifacts.attempt_snapshot

    def replace_after_validation(*args, **kwargs):
        snapshot = original(*args, **kwargs)
        (source["directory"] / "stimulus.json").write_bytes(b"replacement before private snapshot")
        return snapshot

    with store["client"]() as client:
        export_id = client.post(f"/api/attempts/{source['attempt_id']}/exports", headers={"Idempotency-Key": "raced-copy"}).json()["id"]
        monkeypatch.setattr(artifacts, "attempt_snapshot", replace_after_validation)
        assert artifacts.execute_export(export_id, sessions=store["sessions"], root=store["root"])["failed"]
        status = client.get(f"/api/exports/{export_id}").json()
        assert status["status"] == "failed" and status["sha256"] is None
        assert client.get(f"/api/exports/{export_id}/download").status_code == 409
        assert not list((store["root"] / "exports").rglob("*.zip"))
