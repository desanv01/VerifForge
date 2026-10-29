"""Independent App cancellation review: synthetic state, temporary SQLite only."""
from datetime import timedelta
import json

from fastapi.testclient import TestClient
import pytest
import sqlalchemy as sa

from veriforge.api.app import create_app
from veriforge.api.planner import PlannerPolicy
from veriforge.core import planner_rounds as rounds, planner_resources as resources, service
from veriforge.core.database import Attempt, Campaign, PlannerState, PlannerRound, PlannerModelCall
from veriforge.core.planner_experiments import resolve_attempt_experiment
from veriforge.contracts import AttemptResult
from veriforge.planner.models import ProviderCompletion

# Own independent authored fixture, not the owner's test harness or evidence.
from test_planner_api_review import store, AT, ATTACH, CANARY, payload, policy, snapshot, settle


def app_client(store, **options):
    return TestClient(create_app(store[0], initialize_catalog=False, **options))


def attached(store, *, running=False):
    c = app_client(store, planner_policy=policy(), planner_control=lambda: "RUNNING", planner_clock=lambda: AT)
    identifier = settle(store, c)
    assert c.post(f"/api/campaigns/{identifier}/planner", json=ATTACH).status_code == 200
    with store[1].begin() as session:
        row = rounds.advance(session, identifier, at=AT)
        assert row is not None
        round_id = row.id
    grant = None
    if running:
        with store[1].begin() as session:
            grant = resources.claim_provider_slot(session, round_id, incarnation=resources.new_incarnation(),
                role=resources.ROLE, at=AT, control="RUNNING")
            assert grant is not None
    return identifier, round_id, grant


def cancel(c, identifier):
    return c.post(f"/api/campaigns/{identifier}/cancel", headers={"Idempotency-Key": "independent-cancel"})


def test_mounted_default_disabled_and_injected_health_is_not_runtime_readiness(store):
    before = snapshot(store)
    c = app_client(store)
    health = c.get("/api/health").json()
    assert health["planner_api"] == "1" and health["planner_admission_enabled"] is False
    assert health["model"] == "not_integrated" and health["execution"] == "separate_worker"
    assert health["investigation_api"] == health["evidence_api"] == "1"
    assert not any("ready" in key for key in health)
    assert c.post("/api/planner-seeds", json=payload(), headers={"Idempotency-Key": "disabled"}).status_code == 503
    assert c.get("/api/campaigns/absent/planner").status_code == 404
    assert snapshot(store) == before
    configured = policy()
    c = app_client(store, planner_policy=configured, planner_clock=lambda: AT)
    object.__setattr__(configured, "enabled", False)
    assert c.get("/api/health").json()["planner_admission_enabled"] is True
    assert c.post("/api/planner-seeds", json=payload(), headers={"Idempotency-Key": "stopped"}).status_code == 503
    assert snapshot(store) == before


@pytest.mark.parametrize("running", [False, True])
def test_cancel_ignores_disabled_paused_admission_and_preserves_authority_quarantine(store, running):
    identifier, round_id, grant = attached(store, running=running)
    before = snapshot(store)
    with store[1]() as session:
        prior_generation = session.get(PlannerState, identifier).generation
    control_calls = []

    def control():
        control_calls.append(True)
        return "PAUSED"

    c = app_client(store, planner_policy=PlannerPolicy(), planner_control=control,
                   planner_clock=lambda: AT + timedelta(seconds=1))
    response = cancel(c, identifier)
    assert response.status_code == 200 and response.json()["execution_status"] == "cancelled"
    assert control_calls == []
    after = snapshot(store)
    for table in ("planner_cleanup", "planner_runtime_slots", "planner_reservations", "experiments",
                  "attempt_experiments", "investigation_versions"):
        assert after[table] == before[table]
    with store[1]() as session:
        state, row, call = session.get(PlannerState, identifier), session.get(PlannerRound, round_id), session.get(PlannerModelCall, round_id)
        assert state.phase == "cancelled" and state.generation == prior_generation + 1
        assert row.status == ("uncertain" if running else "cancelled")
        assert call.outcome == ("remote_outcome_unknown" if running else "not_invoked")
    assert cancel(c, identifier).status_code == 200
    assert snapshot(store) == after
    with store[1].begin() as session:
        assert rounds.advance(session, identifier, at=AT + timedelta(seconds=2)) is None
        assert rounds.claim_model_round(session, round_id, at=AT + timedelta(seconds=2)) is None
    if running:
        completion = ProviderCompletion(content=json.dumps({"proposal": {"kind": "stop", "reason": "Authored synthetic late stop"}}),
            model="review-server-model", usage={"input_tokens": 1, "output_tokens": 1}, finish_reason="stop")
        with store[1].begin() as session:
            assert not rounds.finish_model_round(session, grant.lease, completion, at=AT + timedelta(seconds=2))
        with store[1].begin() as session:
            assert not resources.validate_provider_slot(session, grant, role=resources.ROLE,
                at=AT + timedelta(seconds=2), control="RUNNING", phase="completion")
    assert snapshot(store) == after


@pytest.mark.parametrize("running", [False, True])
@pytest.mark.parametrize("mode", ["naive", "missing", "throwing"])
def test_bad_cancellation_clock_rolls_back_all_tables_in_queued_and_running_states(store, running, mode):
    identifier, _, _ = attached(store, running=running)

    def clock():
        if mode == "throwing":
            raise OSError(CANARY)
        return None if mode == "missing" else AT.replace(tzinfo=None)

    before = snapshot(store)
    response = cancel(app_client(store, planner_clock=clock), identifier)
    assert response.status_code == 503 and response.json() == {"detail": "Planner clock is unavailable"}
    assert CANARY not in response.text
    assert snapshot(store) == before


def test_foreign_cancel_cannot_touch_clock_cleanup_or_slot(store):
    identifier, _, _ = attached(store, running=True)
    with store[1].begin() as session:
        session.get(Campaign, identifier).owner = "foreign"

    def forbidden_clock():
        pytest.fail("foreign cancellation reached the planner clock")

    before = snapshot(store)
    c = app_client(store, planner_clock=forbidden_clock)
    for target in (identifier, "missing", "not-a-valid-id"):
        response = cancel(c, target)
        assert response.status_code == 404 and response.json() == {"detail": "Not found"}
    assert snapshot(store) == before


@pytest.mark.parametrize("running", [False, True])
def test_legacy_cancel_needs_no_planner_clock_and_refuses_late_checker_result(store, running):
    def forbidden_clock():
        pytest.fail("legacy cancellation called the planner clock")

    c = app_client(store, planner_clock=forbidden_clock)
    response = c.post("/api/campaigns", json={"project_id": "fifo_correct", "plan": payload()["plan"]},
                      headers={"Idempotency-Key": "manual-review"})
    assert response.status_code == 201
    identifier = response.json()["id"]
    assert c.post(f"/api/campaigns/{identifier}/start", headers={"Idempotency-Key": "manual-start"}).status_code == 200
    lease = result = None
    if running:
        with store[1].begin() as session:
            attempt = session.scalar(sa.select(Attempt).where(Attempt.campaign_id == identifier))
            attempt_id = attempt.id
            lease, _, _ = service.claim(session, attempt_id)
            experiment = resolve_attempt_experiment(session, attempt)
            result = AttemptResult(attempt_id="synthetic-late-checker", experiment_id=experiment.wire_id,
                execution_status="completed", verdict="pass_within_scope", usage={"cycles": 1},
                identity={**experiment.identity, "environment_hash": "e" * 64},
                uncertainties=("Synthetic late result; no tool execution",)).model_dump(mode="json")
    before = snapshot(store)
    assert cancel(c, identifier).json()["execution_status"] == "cancelled"
    with store[1].begin() as session:
        assert session.get(PlannerState, identifier) is None
        if running:
            assert not service.finalize(session, attempt_id, lease, result)
    after = snapshot(store)
    for table in ("planner_cleanup", "planner_runtime_slots", "planner_reservations", "planner_states"):
        assert after[table] == before[table]
