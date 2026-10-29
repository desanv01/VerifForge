"""Durability/recovery tests. Test adapter results are not real simulator evidence."""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta

import pytest
from alembic import command
from alembic.config import Config
from fastapi.testclient import TestClient

from veriforge.api.app import create_app
from veriforge.contracts import AttemptResult
from veriforge.core import service
from veriforge.core.database import Attempt, Campaign, Dispatch, now
from veriforge.core.dispatch import dispatch_pending
from veriforge.core.worker import execute_attempt


PLAN = {"experiment_id": "api-test", "fixture_id": "fifo_correct", "contract_id": "fifo-v1", "objective_ids": ["reset"], "operations": [{"op": "reset"}, {"op": "write", "value": 7}, {"op": "read"}]}


@pytest.fixture
def client(db):
    with TestClient(create_app(db[0])) as client:
        yield client


def create(client, key="create", plan=None):
    response = client.post("/api/campaigns", json={"project_id": "fifo_correct", "plan": plan or PLAN}, headers={"Idempotency-Key": key})
    assert response.status_code == 201, response.text
    return response.json()


def start(client, campaign_id):
    response = client.post(f"/api/campaigns/{campaign_id}/start", headers={"Idempotency-Key": "start"})
    assert response.status_code == 200, response.text
    return response.json()


def test_catalog_contract_and_reject_unsafe_payload(client):
    assert client.get("/api/health").json()["model"] == "not_integrated"
    projects = client.get("/api/projects").json()["items"]
    assert {p["id"] for p in projects if p["family"] == "fifo"} == {"fifo_correct", "fifo_reset_bug"}
    assert client.get("/api/contracts/fifo-v1").json()["review_status"] == "reviewed"
    assert client.post("/api/campaigns", json={"project_id": "fifo_correct", "plan": PLAN}).status_code == 422
    bad = {**PLAN, "command": "anything"}
    assert client.post("/api/campaigns", json={"project_id": "fifo_correct", "plan": bad}, headers={"Idempotency-Key": "unsafe"}).status_code == 422
    assert client.post("/api/campaigns", json={"project_id": "fifo_reset_bug", "plan": PLAN}, headers={"Idempotency-Key": "wrong-fixture"}).status_code == 422


def test_duplicate_create_start_restart_events(client, db):
    initial = create(client)
    assert create(client) == initial
    changed = {**PLAN, "seed": 2}
    conflict = client.post("/api/campaigns", json={"project_id": "fifo_correct", "plan": changed}, headers={"Idempotency-Key": "create"})
    assert conflict.status_code == 409
    queued = start(client, initial["id"])
    assert queued["execution_status"] == "queued" and queued["verdict"] is None
    assert start(client, initial["id"]) == queued
    assert len(queued["attempts"]) == 1
    with TestClient(create_app(db[0])) as restarted:
        assert restarted.get(f"/api/campaigns/{initial['id']}").json() == queued
        assert len(restarted.get("/api/campaigns").json()["items"]) == 1
        events = restarted.get(f"/api/campaigns/{initial['id']}/events").json()["items"]
        assert [e["sequence"] for e in events] == [1, 2]
        assert restarted.get(f"/api/campaigns/{initial['id']}/events?after=1").json()["items"] == events[1:]


def test_broker_failure_retains_intent_and_duplicate_delivery(client, db, tmp_path):
    campaign = start(client, create(client)["id"])
    attempt_id = campaign["attempts"][0]["id"]
    class FailingBroker:
        def enqueue(self, attempt_id):
            raise ConnectionError("secret broker credential must not persist")
    assert dispatch_pending(db[1], FailingBroker()) == {"sent": 0, "failed": 1}
    with db[1]() as session:
        intent = session.get(Dispatch, attempt_id)
        assert intent.sent_at is None and intent.error == "ConnectionError"
    class RecordingBroker:
        def enqueue(self, value):
            assert value == attempt_id
    assert dispatch_pending(db[1], RecordingBroker()) == {"sent": 1, "failed": 0}
    calls = []
    def test_adapter(experiment, output, *, cancel_event):
        calls.append(experiment)
        return AttemptResult(attempt_id="adapter-test", experiment_id=experiment.experiment_id, execution_status="completed", verdict="unsupported", uncertainties=("Test adapter; no simulator executed",))
    assert execute_attempt(attempt_id, sessions=db[1], runner=test_adapter, artifact_root=tmp_path)["accepted"]
    assert not execute_attempt(attempt_id, sessions=db[1], runner=test_adapter, artifact_root=tmp_path)["accepted"]
    assert len(calls) == 1
    result = client.get(f"/api/campaigns/{campaign['id']}").json()
    assert result["execution_status"] == "completed" and result["verdict"] == "unsupported"
    assert not result["findings"]


def test_cancel_revokes_lease_late_result_and_sse(client, db):
    campaign = start(client, create(client)["id"])
    attempt_id = campaign["attempts"][0]["id"]
    with db[1].begin() as session:
        lease, _, _ = service.claim(session, attempt_id)
    cancelled = client.post(f"/api/campaigns/{campaign['id']}/cancel", headers={"Idempotency-Key": "cancel"}).json()
    assert cancelled["execution_status"] == "cancelled" and cancelled["verdict"] is None
    with db[1].begin() as session:
        assert not service.authorized(session, attempt_id, lease)
        assert not service.finalize(session, attempt_id, lease, {"verdict": "pass_within_scope"})
    stream = client.get(f"/api/campaigns/{campaign['id']}/events/stream", headers={"Last-Event-ID": "2"})
    assert stream.status_code == 200
    assert "id: 3" in stream.text and "id: 4" in stream.text and "id: 2\n" not in stream.text
    assert "event: campaign.cancelled" in stream.text


def test_interrupted_worker_cannot_finalize(client, db):
    campaign = start(client, create(client)["id"])
    attempt_id = campaign["attempts"][0]["id"]
    with db[1].begin() as session:
        lease, _, _ = service.claim(session, attempt_id)
        session.get(Attempt, attempt_id).heartbeat_at = now() - timedelta(seconds=1000)
    with db[1].begin() as session:
        assert service.recover_stale(session) == 1
    with db[1].begin() as session:
        assert not service.finalize(session, attempt_id, lease, {})
    result = client.get(f"/api/campaigns/{campaign['id']}").json()
    assert result["execution_status"] == "interrupted" and result["verdict"] is None


def test_concurrent_start_has_one_attempt(client, db):
    campaign_id = create(client)["id"]
    def competing_start(_):
        with db[1].begin() as session:
            service.start(session, campaign_id)
    with ThreadPoolExecutor(max_workers=4) as workers:
        list(workers.map(competing_start, range(8)))
    result = client.get(f"/api/campaigns/{campaign_id}").json()
    assert len(result["attempts"]) == 1
    assert [e["sequence"] for e in client.get(f"/api/campaigns/{campaign_id}/events").json()["items"]] == [1, 2]


def test_worker_error_is_infrastructure_not_design(client, db, tmp_path):
    campaign = start(client, create(client)["id"])
    def broken_adapter(*args, **kwargs):
        raise RuntimeError("private detail")
    execute_attempt(campaign["attempts"][0]["id"], sessions=db[1], runner=broken_adapter, artifact_root=tmp_path)
    result = client.get(f"/api/campaigns/{campaign['id']}").json()
    assert result["execution_status"] == "infrastructure_error" and result["verdict"] is None
    assert "private detail" not in str(result)


def test_migration_downgrade_upgrade(db):
    config = Config("alembic.ini")
    with db[0].begin() as connection:
        config.attributes["connection"] = connection
        command.downgrade(config, "base")
        command.upgrade(config, "head")


@pytest.mark.parametrize("changed", ["source_hash", "checker_hash", "contract_hash", "experiment_hash"])
def test_finalization_rejects_forged_identity(client, db, changed):
    campaign = start(client, create(client)["id"])
    attempt_id = campaign["attempts"][0]["id"]
    with db[1].begin() as session:
        lease, _, _ = service.claim(session, attempt_id)
        identity = {**session.get(Campaign, campaign["id"]).identity, "environment_hash": "b" * 64}
    identity[changed] = "0" * 64
    result = AttemptResult(attempt_id=attempt_id, experiment_id=PLAN["experiment_id"], execution_status="completed", verdict="pass_within_scope", identity=identity)
    with pytest.raises(ValueError, match="identity differs"):
        with db[1].begin() as session:
            service.finalize(session, attempt_id, lease, result.model_dump(mode="json"))
    assert client.get(f"/api/campaigns/{campaign['id']}").json()["execution_status"] == "running"


def test_checked_finding_persists_once(client, db):
    campaign = start(client, create(client)["id"])
    attempt_id = campaign["attempts"][0]["id"]
    with db[1].begin() as session:
        lease, _, _ = service.claim(session, attempt_id)
        identity = {**session.get(Campaign, campaign["id"]).identity, "environment_hash": "b" * 64}
        # Explicitly synthetic result for persistence semantics, no simulator claim.
        result = AttemptResult(attempt_id=attempt_id, experiment_id=PLAN["experiment_id"], execution_status="completed", verdict="design_mismatch", identity=identity,
            failure_signature={"requirement_id": "FIFO_BEHAVIOR", "failure_class": "fifo.data", "first_cycle": 2, "expected": "7", "observed": "0", "signature_hash": "c" * 64},
            uncertainties=("Synthetic test result, not simulator evidence",))
        assert service.finalize(session, attempt_id, lease, result.model_dump(mode="json"))
    with db[1].begin() as session:
        assert not service.finalize(session, attempt_id, lease, result.model_dump(mode="json"))
    found = client.get(f"/api/campaigns/{campaign['id']}").json()
    assert len(found["findings"]) == 1
    assert found["execution_status"] == "completed" and found["verdict"] == "design_mismatch"
    assert client.get("/api/findings/" + found["findings"][0]["id"]).json()["result"]["failure_signature"]["first_cycle"] == 2
