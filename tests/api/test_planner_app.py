"""App integration with temporary SQLite, typed fake results, no provider/tool I/O."""
from dataclasses import asdict
from datetime import datetime, timedelta, timezone

from fastapi.testclient import TestClient
import pytest
from sqlalchemy import func, select

from veriforge.api.app import create_app
from veriforge.api.planner import PlannerPolicy
from veriforge.contracts import AttemptResult, Experiment
from veriforge.core import planner_resources as resources, planner_rounds as rounds, service
from veriforge.core.database import (
    Attempt, Campaign, PlannerCleanup, PlannerIntent, PlannerModelCall, PlannerReservation,
    PlannerRound, PlannerState, RunEvent,
)
from veriforge.core.planner_experiments import resolve_attempt_experiment
from veriforge.planner.models import InvestigationLimits, ProviderCompletion

AT = datetime(2026, 10, 30, 12, tzinfo=timezone.utc)


def policy():
    return PlannerPolicy(enabled=True, requested_model="server-test-model", limits=InvestigationLimits(
        max_rounds=5, max_total_cycles=80, max_cycles_per_experiment=8, max_wall_seconds=60.0,
        max_execution_seconds=2.0, max_model_seconds=2.0))


def client(db, **options):
    return TestClient(create_app(db[0], initialize_catalog=False, **options))


def plan():
    return Experiment(experiment_id="app-seed", fixture_id="fifo_correct", objective_ids=("FIFO_BEHAVIOR",),
        budget={"max_cycles": 8, "wall_time_seconds": 2.0},
        operations=({"op": "reset"}, {"op": "write", "value": 17}, {"op": "read"})).model_dump(mode="json")


def result(session, attempt):
    record = resolve_attempt_experiment(session, attempt)
    return AttemptResult(attempt_id="synthetic-checker", experiment_id=record.wire_id,
        execution_status="completed", verdict="pass_within_scope", usage={"cycles": 3},
        identity={**record.identity, "environment_hash": "b" * 64},
        uncertainties=("Synthetic test result; no simulator executed",)).model_dump(mode="json")


def attached(db):
    c = client(db, planner_policy=policy(), planner_control=lambda: "RUNNING", planner_clock=lambda: AT)
    response = c.post("/api/planner-seeds", json={"schema_version": "planner.seed.v1", "project_id": "fifo_correct", "plan": plan()},
        headers={"Idempotency-Key": "app-seed"})
    assert response.status_code == 200, response.text
    campaign_id = response.json()["campaign_id"]
    with db[1].begin() as session:
        assert session.get(PlannerState, campaign_id) is None
        attempt = session.scalar(select(Attempt).where(Attempt.campaign_id == campaign_id))
        lease, _, _ = service.claim(session, attempt.id)
        assert service.finalize(session, attempt.id, lease, result(session, attempt))
    response = c.post(f"/api/campaigns/{campaign_id}/planner", json={"schema_version": "planner.attach.v1"})
    assert response.status_code == 200, response.text
    with db[1].begin() as session:
        row = rounds.advance(session, campaign_id, at=AT)
        round_id = row.id
    return campaign_id, round_id


def cancel(c, campaign_id):
    return c.post(f"/api/campaigns/{campaign_id}/cancel", headers={"Idempotency-Key": "cancel"})


def snapshot(db, campaign_id, round_id):
    with db[1]() as session:
        campaign, state, row = session.get(Campaign, campaign_id), session.get(PlannerState, campaign_id), session.get(PlannerRound, round_id)
        call, cleanup = session.get(PlannerModelCall, round_id), session.get(PlannerCleanup, round_id)
        return dict(campaign=(campaign.execution_status, campaign.verdict, campaign.version),
            state=(state.phase, state.generation), round=(row.status, row.lease, row.finished_at), outcome=call.outcome,
            cleanup=(cleanup.local_state, cleanup.owner_token, cleanup.execution_lease) if cleanup else None,
            resource=asdict(resources.safe_snapshot(session)),
            reservations=[(r.id, r.reserved_tokens, r.reserved_cycles, r.reserved_seconds, r.reserved_cost_usd)
                for r in session.scalars(select(PlannerReservation).where(PlannerReservation.campaign_id == campaign_id).order_by(PlannerReservation.id))],
            events=session.scalar(select(func.count()).select_from(RunEvent).where(RunEvent.campaign_id == campaign_id)))


def test_default_router_is_mounted_disabled_and_health_is_not_provider_readiness(db):
    c = client(db)
    health = c.get("/api/health").json()
    assert health["planner_api"] == "1" and health["planner_admission_enabled"] is False
    assert health["model"] == "not_integrated" and health["execution"] == "separate_worker"
    assert health["investigation_api"] == health["evidence_api"] == "1"
    assert not {"provider_ready", "runtime_ready", "planner_runtime_ready"} & health.keys()
    response = c.post("/api/planner-seeds", json={"schema_version": "planner.seed.v1", "project_id": "fifo_correct", "plan": plan()},
        headers={"Idempotency-Key": "disabled"})
    assert response.status_code == 503
    with db[1]() as session:
        assert session.scalar(select(func.count()).select_from(Campaign)) == 0
        assert session.scalar(select(func.count()).select_from(PlannerIntent)) == 0


def test_configured_policy_still_needs_explicit_running_control_and_health_is_snapshot(db):
    configured = policy()
    c = client(db, planner_policy=configured, planner_clock=lambda: AT)
    object.__setattr__(configured, "enabled", False)
    health = c.get("/api/health").json()
    assert health["planner_admission_enabled"] is True and health["model"] == "not_integrated"
    response = c.post("/api/planner-seeds", json={"schema_version": "planner.seed.v1", "project_id": "fifo_correct", "plan": plan()},
        headers={"Idempotency-Key": "no-explicit-control"})
    assert response.status_code == 503


def test_queued_round_cancel_is_atomic_without_admission_or_running_control(db):
    campaign_id, round_id = attached(db)
    before = snapshot(db, campaign_id, round_id)
    # Restarted default-disabled App can cancel persisted planners on PAUSED control.
    c = client(db, planner_control=lambda: "PAUSED", planner_clock=lambda: AT + timedelta(seconds=1))
    response = cancel(c, campaign_id)
    assert response.status_code == 200, response.text
    assert response.json()["execution_status"] == "cancelled" and response.json()["verdict"] is None
    after = snapshot(db, campaign_id, round_id)
    assert after["state"] == ("cancelled", before["state"][1] + 1)
    assert after["round"][0] == "cancelled" and after["round"][1] != before["round"][1]
    assert after["outcome"] == "not_invoked"
    assert after["resource"] == before["resource"] and after["reservations"] == before["reservations"]
    assert cancel(c, campaign_id).status_code == 200
    assert snapshot(db, campaign_id, round_id) == after
    with db[1].begin() as session:
        assert rounds.claim_model_round(session, round_id, at=AT + timedelta(seconds=2)) is None
        assert rounds.advance(session, campaign_id, at=AT + timedelta(seconds=2)) is None


def test_running_round_cancel_refuses_exact_late_completion_and_preserves_cleanup_resource(db):
    campaign_id, round_id = attached(db)
    with db[1].begin() as session:
        grant = resources.claim_provider_slot(session, round_id, incarnation=resources.new_incarnation(),
            role=resources.ROLE, at=AT, control="RUNNING")
        assert grant is not None
    before = snapshot(db, campaign_id, round_id)
    assert before["cleanup"][0] == "pending" and before["resource"]["state"] == "active"
    response = cancel(client(db, planner_clock=lambda: AT + timedelta(seconds=1)), campaign_id)
    assert response.status_code == 200
    after = snapshot(db, campaign_id, round_id)
    assert after["state"][0] == "cancelled" and after["round"][0] == "uncertain"
    assert after["round"][1] != before["round"][1] and after["outcome"] == "remote_outcome_unknown"
    assert after["cleanup"] == before["cleanup"]
    assert after["resource"] == before["resource"]
    assert after["reservations"] == before["reservations"]
    completion = ProviderCompletion(content='{"proposal":{"kind":"stop","reason":"Synthetic late reply"}}',
        model="server-test-model", usage={"input_tokens": 1, "output_tokens": 1}, finish_reason="stop")
    with db[1].begin() as session:
        assert not rounds.finish_model_round(session, grant.lease, completion, at=AT + timedelta(seconds=2))
    with db[1].begin() as session:
        assert not resources.validate_provider_slot(session, grant, role=resources.ROLE,
            at=AT + timedelta(seconds=2), control="RUNNING", phase="completion")
    assert snapshot(db, campaign_id, round_id) == after


@pytest.mark.parametrize("clock", [lambda: AT.replace(tzinfo=None), lambda: None,
    lambda: (_ for _ in ()).throw(OSError("secret-clock-details"))])
def test_unusable_cancel_clock_is_fixed_safe_error_and_rolls_back(db, clock):
    campaign_id, round_id = attached(db)
    before = snapshot(db, campaign_id, round_id)
    response = cancel(client(db, planner_clock=clock), campaign_id)
    assert response.status_code == 503 and response.json() == {"detail": "Planner clock is unavailable"}
    assert snapshot(db, campaign_id, round_id) == before


def test_cancel_ownership_precedes_clock_and_foreign_state_detection(db):
    campaign_id, round_id = attached(db)
    with db[1].begin() as session:
        session.get(Campaign, campaign_id).owner = "foreign"
    def forbidden_clock():
        pytest.fail("Foreign campaign must not reach clock or planner mutation")
    c = client(db, planner_clock=forbidden_clock)
    before = snapshot(db, campaign_id, round_id)
    for identifier in (campaign_id, "missing", "invalid-id"):
        response = cancel(c, identifier)
        assert response.status_code == 404 and response.json() == {"detail": "Not found"}
    assert snapshot(db, campaign_id, round_id) == before


def test_legacy_manual_cancel_does_not_need_planner_clock_and_revokes_late_result(db):
    def unavailable_clock():
        pytest.fail("Legacy manual cancellation must not use planner clock")
    c = client(db, planner_clock=unavailable_clock)
    response = c.post("/api/campaigns", json={"project_id": "fifo_correct", "plan": plan()}, headers={"Idempotency-Key": "manual"})
    assert response.status_code == 201
    campaign_id = response.json()["id"]
    assert c.post(f"/api/campaigns/{campaign_id}/start", headers={"Idempotency-Key": "start"}).status_code == 200
    with db[1].begin() as session:
        attempt = session.scalar(select(Attempt).where(Attempt.campaign_id == campaign_id))
        attempt_id = attempt.id
        lease, _, _ = service.claim(session, attempt_id)
        late_result = result(session, attempt)
    assert cancel(c, campaign_id).json()["execution_status"] == "cancelled"
    with db[1].begin() as session:
        assert not service.finalize(session, attempt_id, lease, late_result)
        assert session.get(PlannerState, campaign_id) is None
        assert session.get(Attempt, attempt_id).lease != lease
