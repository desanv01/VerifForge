"""Local temporary SQLite and synthetic typed seed results; no tools or provider."""
from datetime import datetime, timezone
from concurrent.futures import ThreadPoolExecutor
import json

from fastapi.testclient import TestClient
import pytest
from sqlalchemy import event, func, select, update
from sqlalchemy.exc import OperationalError

from veriforge.api.app import create_app
from veriforge.api.planner import PlannerPolicy
from veriforge.contracts import AttemptResult, Experiment
from veriforge.core import planner_rounds as rounds, service
from veriforge.core.database import (
    Attempt, Campaign, Dispatch, PlannerCleanup, PlannerIntent, PlannerModelCall,
    PlannerReservation, PlannerRound, PlannerState, Project,
)
from veriforge.core.planner_experiments import resolve_attempt_experiment
from veriforge.planner.models import InvestigationLimits, Pricing

AT = datetime(2026, 10, 30, 12, tzinfo=timezone.utc)
ATTACH = {"schema_version": "planner.attach.v1"}


def policy(**changes):
    values = dict(enabled=True, requested_model="server-model-1", limits=InvestigationLimits(
        max_rounds=5, max_total_cycles=80, max_cycles_per_experiment=8,
        max_wall_seconds=60.0, max_execution_seconds=2.0, max_model_seconds=2.0))
    values.update(changes)
    return PlannerPolicy(**values)


def client(db, *, policy=None, control=None, now=None):
    app = create_app(db[0], initialize_catalog=False,
        planner_policy=policy, planner_control=control, planner_clock=now)
    return TestClient(app)


def payload():
    experiment = Experiment(experiment_id="seed", fixture_id="fifo_correct", objective_ids=("FIFO_BEHAVIOR",),
        budget={"max_cycles": 8, "wall_time_seconds": 2.0},
        operations=({"op": "reset"}, {"op": "write", "value": 17}, {"op": "read"}))
    return {"schema_version": "planner.seed.v1", "project_id": "fifo_correct", "plan": experiment.model_dump(mode="json")}


def seed(c, body=None, key="seed"):
    return c.post("/api/planner-seeds", json=body or payload(), headers={"Idempotency-Key": key})


def counts(db):
    with db[1]() as session:
        return tuple(session.scalar(select(func.count()).select_from(model)) for model in
            (Campaign, Attempt, Dispatch, PlannerState, PlannerIntent, PlannerModelCall, PlannerReservation))


def settled(db, c, *, verdict="pass_within_scope"):
    response = seed(c)
    assert response.status_code == 200, response.text
    campaign_id = response.json()["campaign_id"]
    with db[1].begin() as session:
        attempt = session.scalar(select(Attempt).where(Attempt.campaign_id == campaign_id))
        lease, _, _ = service.claim(session, attempt.id)
        record = resolve_attempt_experiment(session, attempt)
        result = AttemptResult(attempt_id="synthetic-checker", experiment_id=record.wire_id,
            execution_status="completed", verdict=verdict,
            identity={**record.identity, "environment_hash": "b" * 64}, usage={"cycles": 3},
            uncertainties=("Synthetic typed result; no simulator executed",))
        assert service.finalize(session, attempt.id, lease, result.model_dump(mode="json"))
    return campaign_id


def configured(db, **options):
    return client(db, policy=policy(), control=lambda: "RUNNING", now=lambda: AT, **options)


def test_default_disabled_and_enabled_without_explicit_control_do_not_mutate(db):
    before = counts(db)
    for c in (client(db), client(db, policy=policy(), now=lambda: AT)):
        assert seed(c).status_code == 503
        assert counts(db) == before
    with pytest.raises(ValueError):
        PlannerPolicy(enabled=True)
    with pytest.raises(ValueError):
        policy(limits=InvestigationLimits(max_cost_usd="1"))


@pytest.mark.parametrize("control,clock", [
    (lambda: "PAUSED", lambda: AT),
    (lambda: "RUNNING", lambda: AT.replace(tzinfo=None)),
    (lambda: (_ for _ in ()).throw(OSError("secret-control-error")), lambda: AT),
])
def test_fail_closed_control_or_clock_has_no_writes(db, control, clock):
    before = counts(db)
    response = seed(client(db, policy=policy(), control=control, now=clock))
    assert response.status_code == 503 and "secret-control-error" not in response.text
    assert counts(db) == before


@pytest.mark.parametrize("change", [
    {"endpoint": "https://untrusted.invalid"}, {"strategy": "mock_planner"},
    {"model": "client-model"}, {"pricing": {}}, {"command": "run"},
    {"schema_version": "planner.seed.v0"},
])
def test_public_seed_configuration_is_forbidden_without_mutation(db, change):
    c, body = configured(db), payload()
    body.update(change)
    before = counts(db)
    assert seed(c, body).status_code == 422
    assert counts(db) == before


@pytest.mark.parametrize("field,value", [
    ("max_cycles", 9), ("wall_time_seconds", 3.0), ("max_artifact_bytes", 10_485_761),
])
def test_seed_server_allowances_are_checked_before_create(db, field, value):
    c, body = configured(db), payload()
    body["plan"]["budget"][field] = value
    before = counts(db)
    assert seed(c, body).status_code == 409
    assert counts(db) == before


def test_seed_atomic_idempotent_manual_and_namespaced(db):
    c = configured(db)
    manual = c.post("/api/campaigns", json={"project_id": "fifo_correct", "plan": payload()["plan"]},
        headers={"Idempotency-Key": "seed"})
    assert manual.status_code == 201
    first, second = seed(c), seed(c)
    assert first.status_code == second.status_code == 200
    assert first.json() == second.json()
    assert first.json()["campaign_id"] != manual.json()["id"]
    assert first.json()["execution_status"] == "queued"
    assert first.json()["planner_attached"] is False
    assert counts(db) == (2, 1, 1, 0, 0, 0, 0)
    changed = payload()
    changed["plan"]["seed"] = 2
    assert seed(c, changed).status_code == 409
    assert counts(db) == (2, 1, 1, 0, 0, 0, 0)
    with db[1]() as session:
        campaign = session.get(Campaign, first.json()["campaign_id"])
        attempt = session.scalar(select(Attempt).where(Attempt.campaign_id == campaign.id))
        assert campaign.completion_policy == "sequential"
        assert resolve_attempt_experiment(session, attempt).origin == "manual"


def test_foreign_missing_and_unsupported_projects_fail_before_create(db):
    c = configured(db)
    with db[1].begin() as session:
        session.get(Project, "fifo_reset_bug").owner = "foreign"
        session.add(Project(id="recorded-only", kind="engineering_recorded",
            data={"id": "recorded-only", "name": "Recorded only", "description": "", "domains": ["firmware"]}))
    for project_id, expected in (("absent", 404), ("fifo_reset_bug", 404), ("multiplier_correct", 409),
                                 ("axil_registers_correct", 409), ("recorded-only", 409)):
        body = payload()
        body["project_id"] = project_id
        response = seed(c, body, key=project_id)
        assert response.status_code == expected, response.text
    assert counts(db) == (0, 0, 0, 0, 0, 0, 0)


def test_attach_only_after_settled_seed_exact_repeat_and_frozen_policy(db):
    c = configured(db)
    campaign_id = seed(c).json()["campaign_id"]
    path = f"/api/campaigns/{campaign_id}/planner"
    before = counts(db)
    assert c.post(path, json=ATTACH).status_code == 409
    assert counts(db) == before
    assert settled(db, c) == campaign_id
    first, repeat = c.post(path, json=ATTACH), c.post(path, json=ATTACH)
    assert first.status_code == repeat.status_code == 200
    assert first.json() == repeat.json()
    assert first.json()["strategy"] == "meta"
    assert first.json()["deadline_at"].startswith("2026-10-30T12:01:00")
    assert counts(db) == (1, 1, 1, 1, 1, 0, 1)
    budget = c.get(path + "/budget").json()
    assert budget["reserved"] == {"tokens": 0, "cycles": 8, "seconds": "2.0", "cost": None}
    assert budget["price_status"] == "unknown"
    for changed in (policy(requested_model="server-model-2"),
                    policy(pricing=Pricing(input_usd_per_million="1", output_usd_per_million="2")),
                    policy(limits=InvestigationLimits(max_total_cycles=81))):
        other = client(db, policy=changed, control=lambda: "RUNNING", now=lambda: AT)
        assert other.post(path, json=ATTACH).status_code == 409
    assert counts(db) == (1, 1, 1, 1, 1, 0, 1)


def test_attach_control_guard_and_forbidden_input_are_atomic(db):
    c = configured(db)
    campaign_id = settled(db, c)
    path = f"/api/campaigns/{campaign_id}/planner"
    before = counts(db)
    blocked = client(db, policy=policy(), control=lambda: "PAUSED", now=lambda: AT)
    assert blocked.post(path, json=ATTACH).status_code == 503
    for field in ("limits", "pricing", "model", "strategy", "endpoint", "command"):
        assert c.post(path, json={**ATTACH, field: "untrusted"}).status_code == 422
    assert counts(db) == before
    # Control is rechecked after locking and immediately before attachment.
    controls = iter(["RUNNING", "STOPPED"])
    racing = client(db, policy=policy(), control=lambda: next(controls), now=lambda: AT)
    assert racing.post(path, json=ATTACH).status_code == 503
    assert counts(db) == before


def test_owned_campaign_lookup_for_every_read_and_attach(db):
    c = configured(db)
    campaign_id = settled(db, c)
    path = f"/api/campaigns/{campaign_id}/planner"
    assert c.post(path, json=ATTACH).status_code == 200
    with db[1].begin() as session:
        session.get(Campaign, campaign_id).owner = "foreign"
    before = counts(db)
    for identifier in (campaign_id, "missing", "not-a-uuid"):
        root = f"/api/campaigns/{identifier}/planner"
        assert c.post(root, json=ATTACH).status_code == 404
        for suffix in ("", "/rounds", "/budget", "/cleanup"):
            response = c.get(root + suffix)
            assert response.status_code == 404 and response.json() == {"detail": "Not found"}
    assert counts(db) == before


def test_safe_exports_paginate_and_survive_disabled_admission(db):
    c = configured(db)
    campaign_id = settled(db, c)
    path = f"/api/campaigns/{campaign_id}/planner"
    assert c.post(path, json=ATTACH).status_code == 200
    # Deliberately sensitive synthetic persisted rows, never a provider invocation.
    with db[1].begin() as session:
        for ordinal in (3, 1, 2):
            row = PlannerRound(campaign_id=campaign_id, ordinal=ordinal, generation=ordinal, status="uncertain", lease="secret-lease")
            session.add(row)
            session.flush()
            session.add(PlannerModelCall(round_id=row.id, requested_model="server-model-1", request_body=b"secret-request",
                request_hash="a" * 64, schema_hash="b" * 64, input_allowance=100, context={"raw_response": "secret-response"}))
            session.flush()
            session.add(PlannerCleanup(round_id=row.id, campaign_id=campaign_id, generation=ordinal,
                execution_lease="secret-execution-lease", request_hash="a" * 64, owner_token="secret-owner", registered_at=AT))
    reader = client(db)
    for suffix in ("/rounds", "/cleanup"):
        first = reader.get(path + suffix, params={"limit": 2}).json()
        assert [item["ordinal"] for item in first["items"]] == [1, 2]
        assert first["next_cursor"] == 2
        second = reader.get(path + suffix, params={"limit": 2, "after": 2}).json()
        assert [item["ordinal"] for item in second["items"]] == [3] and second["next_cursor"] is None
        for query in ("limit=101", "limit=0", "after=-1"):
            assert reader.get(path + suffix + "?" + query).status_code == 422
        text = json.dumps(first)
        for forbidden in ("secret-", "request_body", "raw_response", "lease", "owner_token", "request_hash", "settlement_proof"):
            assert forbidden not in text
    assert reader.get(path).json()["admission_enabled"] is False
    assert reader.get(path + "/budget").json()["reserved"]["cost"] is None


def test_changed_source_and_injected_failure_roll_back_seed(db, monkeypatch):
    c = configured(db)
    with db[1].begin() as session:
        project = session.get(Project, "fifo_correct")
        original = dict(project.data)
        project.data = {**original, "source_file_hash": "0" * 64}
    before = counts(db)
    assert seed(c).status_code == 409
    assert counts(db) == before
    with db[1].begin() as session:
        session.get(Project, "fifo_correct").data = original
    def fail_start(*args):
        raise service.Conflict("Injected start failure")
    monkeypatch.setattr(service, "start", fail_start)
    assert seed(c).status_code == 409
    assert counts(db) == before


def test_policy_is_detached_and_known_pricing_preserves_zero(db):
    configured_policy = policy(pricing=Pricing(input_usd_per_million="1", output_usd_per_million="2"))
    c = client(db, policy=configured_policy, control=lambda: "RUNNING", now=lambda: AT)
    # Even a host bypassing frozen Pydantic assignment cannot mutate the snapshot.
    object.__setattr__(configured_policy, "requested_model", "mutated-host-model")
    campaign_id = settled(db, c)
    path = f"/api/campaigns/{campaign_id}/planner"
    assert c.post(path, json=ATTACH).json()["requested_model"] == "server-model-1"
    budget = c.get(path + "/budget").json()
    assert budget["price_status"] == "known" and budget["reserved"]["cost"] == "0"


def test_concurrent_seed_duplicates_queue_one_attempt(db):
    c = configured(db)
    with ThreadPoolExecutor(max_workers=2) as pool:
        responses = list(pool.map(lambda _: seed(c), range(2)))
    assert [response.status_code for response in responses] == [200, 200]
    assert responses[0].json() == responses[1].json()
    assert counts(db) == (1, 1, 1, 0, 0, 0, 0)


def test_seed_second_control_guard_and_malformed_request_have_no_writes(db):
    controls = iter(["RUNNING", "STOPPED"])
    c = client(db, policy=policy(), control=lambda: next(controls), now=lambda: AT)
    before = counts(db)
    assert seed(c).status_code == 503
    assert counts(db) == before
    c = configured(db)
    for body, headers in (({}, {"Idempotency-Key": "x"}), (payload(), {})):
        assert c.post("/api/planner-seeds", json=body, headers=headers).status_code == 422
    assert counts(db) == before


@pytest.mark.parametrize("verdict", ["timeout", "build_error", "inconclusive"])
def test_unavailable_seed_is_not_eligible_and_is_not_a_design_defect(db, verdict):
    c = configured(db)
    campaign_id = settled(db, c, verdict=verdict)
    before = counts(db)
    assert c.post(f"/api/campaigns/{campaign_id}/planner", json=ATTACH).status_code == 409
    assert counts(db) == before
    with db[1]() as session:
        assert session.get(Campaign, campaign_id).verdict is None


def test_attach_rejects_changed_source_and_tighter_server_seed_allowance(db):
    c = configured(db)
    campaign_id = settled(db, c)
    path = f"/api/campaigns/{campaign_id}/planner"
    tighter = client(db, policy=policy(limits=InvestigationLimits(max_cycles_per_experiment=7)),
        control=lambda: "RUNNING", now=lambda: AT)
    before = counts(db)
    assert tighter.post(path, json=ATTACH).status_code == 409
    with db[1].begin() as session:
        project = session.get(Project, "fifo_correct")
        project.data = {**project.data, "source_file_hash": "0" * 64}
    assert c.post(path, json=ATTACH).status_code == 409
    assert counts(db) == before


def test_exact_attach_repeat_does_not_restart_cancelled_planner(db):
    c = configured(db)
    campaign_id = settled(db, c)
    path = f"/api/campaigns/{campaign_id}/planner"
    assert c.post(path, json=ATTACH).status_code == 200
    with db[1].begin() as session:
        rounds.cancel_investigation(session, campaign_id, at=AT)
    before = counts(db)
    response = c.post(path, json=ATTACH)
    assert response.status_code == 200
    assert response.json()["phase"] == "cancelled"
    assert response.json()["execution_status"] == "cancelled"
    assert counts(db) == before


@pytest.mark.parametrize("stage", ["seed", "attach"])
def test_project_authority_lock_precedes_campaign_and_survives_final_control(db, stage):
    # SQLite takes a database-wide writer lock, so writer exclusion alone would
    # miss a PostgreSQL campaign-only row lock bug. Assert the actual owned
    # Project UPDATE occurs before every Campaign UPDATE as well.
    campaign_id = settled(db, configured(db)) if stage == "attach" else None
    locks, controls = [], 0

    def trace_locks(connection, cursor, statement, parameters, context, executemany):
        normalized = " ".join(statement.lower().split())
        if normalized.startswith("update projects set id=projects.id"):
            assert "projects.owner =" in normalized
            assert tuple(parameters) == ("fifo_correct", "local")
            locks.append("project")
        elif normalized.startswith("update campaigns set version=campaigns.version"):
            locks.append("campaign")

    def control():
        nonlocal controls
        controls += 1
        if controls == 2:
            assert locks and locks[0] == "project"
            # Exercise the final-control window on an independent connection.
            # This is SQLite exclusion evidence, not PostgreSQL row-lock proof.
            with db[0].connect() as peer:
                peer.exec_driver_sql("PRAGMA busy_timeout=1")
                with pytest.raises(OperationalError, match="database is locked"):
                    peer.execute(update(Project).where(Project.id == "fifo_correct").values(owner="foreign"))
        return "RUNNING"

    event.listen(db[0], "before_cursor_execute", trace_locks)
    try:
        c = client(db, policy=policy(), control=control, now=lambda: AT)
        response = seed(c) if stage == "seed" else c.post(
            f"/api/campaigns/{campaign_id}/planner", json=ATTACH)
        assert response.status_code == 200, response.text
        assert controls == 2 and locks[0] == "project" and "campaign" in locks
        assert locks.count("project") == 1
    finally:
        event.remove(db[0], "before_cursor_execute", trace_locks)
    # The owned-project guard is transactional, not a process-lifetime fence.
    with db[0].begin() as peer:
        assert peer.execute(update(Project).where(Project.id == "fifo_correct").values(owner="foreign")).rowcount == 1


def test_attach_changed_campaign_project_does_not_lock_second_project_or_admit(db, monkeypatch):
    c = configured(db)
    campaign_id = settled(db, c)
    before = counts(db)
    original_lock = service.lock

    def move_binding_before_campaign_lock(session, identifier):
        session.execute(update(Campaign).where(Campaign.id == identifier).values(project_id="fifo_reset_bug"))
        return original_lock(session, identifier)

    monkeypatch.setattr(service, "lock", move_binding_before_campaign_lock)
    response = c.post(f"/api/campaigns/{campaign_id}/planner", json=ATTACH)
    assert response.status_code == 409 and response.json() == {"detail": "Campaign project binding changed"}
    assert counts(db) == before
    with db[1]() as session:
        assert session.get(Campaign, campaign_id).project_id == "fifo_correct"
