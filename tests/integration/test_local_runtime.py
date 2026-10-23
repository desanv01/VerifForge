"""Opt-in real HTTP→PostgreSQL→Valkey/RQ→Verilator acceptance.

Run inside the Compose API container. Never substitutes a fake runner.
"""
import os
import time
from uuid import uuid4

import httpx
import pytest

pytestmark = pytest.mark.skipif(
    not os.getenv("VERIFORGE_DATABASE_URL", "").startswith("postgresql"),
    reason="requires the running local Compose PostgreSQL/RQ/RTL stack",
)


@pytest.fixture
def client():
    with httpx.Client(base_url="http://127.0.0.1:8000/api", timeout=10) as client:
        assert client.get("/health").json()["status"] == "ok"
        yield client


def create(client, fixture):
    token = str(uuid4())
    payload = {"project_id": fixture, "plan": {
        "experiment_id": token, "fixture_id": fixture, "objective_ids": ["FIFO_BEHAVIOR"],
        "operations": [{"op": "reset"}, {"op": "write", "value": 17},
                       {"op": "reset"}, {"op": "write", "value": 42}, {"op": "read"}],
        "budget": {"wall_time_seconds": 120},
    }}
    response = client.post("/campaigns", json=payload, headers={"Idempotency-Key": token})
    assert response.status_code == 201, response.text
    repeat = client.post("/campaigns", json=payload, headers={"Idempotency-Key": token})
    assert repeat.status_code == 201, repeat.text
    assert repeat.json()["id"] == response.json()["id"]
    return response.json()["id"]


def start(client, campaign):
    response = client.post(f"/campaigns/{campaign}/start", headers={"Idempotency-Key": str(uuid4())})
    assert response.status_code == 200, response.text
    return response.json()


def settled(client, campaign, timeout=180):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        response = client.get(f"/campaigns/{campaign}")
        assert response.status_code == 200, response.text
        data = response.json()
        if data["execution_status"] not in {"queued", "running"}:
            return data
        time.sleep(1)
    pytest.fail(f"Campaign {campaign} did not settle within {timeout}s")


def duplicate_delivery(attempt_id):
    from veriforge.core.dispatch import RQDispatcher
    return RQDispatcher().queue.enqueue(
        "veriforge.core.worker.execute_attempt", attempt_id,
        job_timeout=180, result_ttl=300,
    )


def delivered(job, timeout=180):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        status = job.get_status(refresh=True)
        if status == "finished":
            return job.result
        assert status != "failed", job.exc_info
        time.sleep(1)
    pytest.fail(f"Duplicate queue delivery {job.id} did not finish")


@pytest.mark.parametrize("fixture,verdict", [
    ("fifo_correct", "pass_within_scope"), ("fifo_reset_bug", "design_mismatch"),
])
def test_checked_result_and_duplicate_delivery(client, fixture, verdict):
    campaign = create(client, fixture)
    first = start(client, campaign)
    repeated = start(client, campaign)
    assert [a["id"] for a in first["attempts"]] == [a["id"] for a in repeated["attempts"]]
    # Deliberate real duplicate broker delivery, handled by the durable claim.
    duplicate = duplicate_delivery(first["attempts"][0]["id"])
    data = settled(client, campaign)
    delivered(duplicate)
    assert data["execution_status"] == "completed", data
    assert data["verdict"] == verdict, data
    assert len(data["attempts"]) == 1
    result = data["attempts"][0]["result"]
    assert result["identity"] and result["tool_versions"] and result["trace"]
    assert result["artifacts"]
    assert bool(data["findings"]) == (verdict == "design_mismatch")
    events = client.get(f"/campaigns/{campaign}/events").json()["items"]
    assert [e["sequence"] for e in events] == list(range(1, len(events) + 1))
    assert sum(e["kind"] == "attempt.completed" for e in events) == 1
    print({"campaign_id": campaign, "attempt_id": first["attempts"][0]["id"], "verdict": verdict})


def test_cancel_is_durable_and_duplicate_cannot_revive(client):
    campaign = create(client, "fifo_correct")
    queued = start(client, campaign)
    response = client.post(f"/campaigns/{campaign}/cancel", headers={"Idempotency-Key": str(uuid4())})
    assert response.status_code == 200, response.text
    duplicate = duplicate_delivery(queued["attempts"][0]["id"])
    assert delivered(duplicate) == {"accepted": False}
    data = settled(client, campaign)
    assert data["execution_status"] == "cancelled"
    assert data["verdict"] is None
    assert data["findings"] == []
    assert data["attempts"][0]["execution_status"] == "cancelled"
