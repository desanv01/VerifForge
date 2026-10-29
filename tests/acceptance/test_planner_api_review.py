"""Independent synthetic review: temporary SQLite/TestClient, no provider/tools."""
from datetime import datetime, timezone
from hashlib import sha256
import json

from alembic import command
from alembic.config import Config
from fastapi import FastAPI
from fastapi.testclient import TestClient
from fastapi.responses import JSONResponse
import pytest
import sqlalchemy as sa

from veriforge.api.planner import PlannerPolicy, planner_router
from veriforge.contracts import AttemptResult, Experiment
from veriforge.core import service
from veriforge.core.catalog import seed_catalog
from veriforge.core.database import (Attempt, Campaign, PlannerState, PlannerRound,
    PlannerModelCall, PlannerCleanup, Project, make_engine, session_factory)
from veriforge.core.planner_experiments import resolve_attempt_experiment
from veriforge.planner.models import InvestigationLimits

AT = datetime(2026, 10, 30, 10, tzinfo=timezone.utc)
ATTACH = {"schema_version": "planner.attach.v1"}
CANARY = "SYNTHETIC_PRIVATE_CANARY"


@pytest.fixture
def store(tmp_path):
    engine = make_engine("sqlite:///" + (tmp_path / "independent.db").as_posix())
    config = Config("alembic.ini")
    with engine.begin() as connection:
        config.attributes["connection"] = connection
        command.upgrade(config, "13engineeringprojects")
    sessions = session_factory(engine)
    with sessions.begin() as session:
        seed_catalog(session)
    yield engine, sessions
    engine.dispose()


def policy(**changes):
    values = dict(enabled=True, requested_model="review-server-model", limits=InvestigationLimits(
        max_cycles_per_experiment=10, max_total_cycles=20, max_execution_seconds=3,
        max_wall_seconds=9, max_rounds=2), max_seed_artifact_bytes=1024)
    return PlannerPolicy(**{**values, **changes})


def client(store, *, selected=None, control=lambda: "RUNNING", now=lambda: AT):
    # Isolate this reviewed router from concurrent main-owned App integration.
    # Use the same safe exception mapping required by the router contract.
    app = FastAPI()
    @app.exception_handler(service.NotFound)
    async def missing(request, error):
        return JSONResponse({"detail": "Not found"}, status_code=404)
    @app.exception_handler(service.Conflict)
    async def conflict(request, error):
        return JSONResponse({"detail": str(error)}, status_code=409)
    app.include_router(planner_router(store[1], policy=selected or policy(), control=control, now=now))
    return TestClient(app)


def payload():
    experiment = Experiment(experiment_id="independent-wire-seed", fixture_id="fifo_correct",
        objective_ids=("FIFO_BEHAVIOR",), budget={"max_cycles": 10, "wall_time_seconds": 3,
        "max_artifact_bytes": 1024}, operations=({"op": "reset"}, {"op": "write", "value": 5}, {"op": "read"}))
    return {"schema_version": "planner.seed.v1", "project_id": "fifo_correct", "plan": experiment.model_dump(mode="json")}


def seed(c, body=None, key="review-key"):
    return c.post("/api/planner-seeds", json=payload() if body is None else body, headers={"Idempotency-Key": key})


def snapshot(store):
    with store[0].connect() as connection:
        metadata = sa.MetaData()
        metadata.reflect(connection)
        return {name: sorted((tuple(map(repr, row)) for row in connection.execute(table.select()).all()))
                for name, table in metadata.tables.items()}


def settle(store, c, verdict="pass_within_scope"):
    response = seed(c)
    assert response.status_code == 200, response.text
    identifier = response.json()["campaign_id"]
    with store[1].begin() as session:
        attempt = session.scalar(sa.select(Attempt).where(Attempt.campaign_id == identifier))
        lease, _, _ = service.claim(session, attempt.id)
        experiment = resolve_attempt_experiment(session, attempt)
        failure = None
        if verdict in {"design_mismatch", "property_failure"}:
            failure = {"requirement_id": "FIFO_BEHAVIOR", "failure_class": "synthetic_review_failure",
                "first_cycle": 1, "expected": "synthetic expected", "observed": "synthetic observed",
                "signature_hash": sha256(b"independent authored failure").hexdigest()}
        result = AttemptResult(attempt_id="synthetic-native-attempt", experiment_id=experiment.wire_id,
            execution_status="completed", verdict=verdict,
            identity={**experiment.identity, "environment_hash": "d" * 64}, usage={"cycles": 1},
            failure_signature=failure,
            uncertainties=("Authored synthetic checker result; no simulator/provider executed",))
        assert service.finalize(session, attempt.id, lease, result.model_dump(mode="json"))
    return identifier


@pytest.mark.parametrize("mode", ["disabled", "paused", "naive", "clock_error", "late_pause"])
def test_both_mutations_fail_closed_without_any_persisted_change(store, mode):
    healthy = client(store)
    identifier = settle(store, healthy)
    controls = iter(["RUNNING", "PAUSED"])

    def broken_clock():
        raise OSError(CANARY)

    def blocked():
        return client(store, selected=PlannerPolicy() if mode == "disabled" else policy(),
            control=(lambda: next(controls)) if mode == "late_pause" else lambda: "PAUSED" if mode == "paused" else "RUNNING",
            now=broken_clock if mode == "clock_error" else lambda: AT.replace(tzinfo=None) if mode == "naive" else AT)

    for operation in ("seed", "attach"):
        controls = iter(["RUNNING", "PAUSED"])
        before = snapshot(store)
        c = blocked()
        response = seed(c, key="blocked") if operation == "seed" else c.post(f"/api/campaigns/{identifier}/planner", json=ATTACH)
        assert response.status_code == 503 and CANARY not in response.text
        assert snapshot(store) == before


@pytest.mark.parametrize("edit", ["cycles", "seconds", "artifacts", "objective", "fixture", "extra_policy", "rtl_schema"])
def test_binding_and_server_caps_precede_create_and_start(store, edit, monkeypatch):
    body = payload()
    if edit in {"cycles", "seconds", "artifacts"}:
        field, value = {"cycles": ("max_cycles", 11), "seconds": ("wall_time_seconds", 4),
                        "artifacts": ("max_artifact_bytes", 1025)}[edit]
        body["plan"]["budget"][field] = value
    elif edit == "objective":
        body["plan"]["objective_ids"] = ["UNREVIEWED_REQUIREMENT"]
    elif edit == "fixture":
        body["plan"]["fixture_id"] = "fifo_reset_bug"
    elif edit == "extra_policy":
        body["limits"] = {"max_total_cycles": 100000}
    else:
        body["plan"]["schema_version"] = "arithmetic.v1"
    monkeypatch.setattr(service, "create_campaign", lambda *a, **kw: pytest.fail("rejected admission reached create"))
    monkeypatch.setattr(service, "start", lambda *a, **kw: pytest.fail("rejected admission reached start"))
    before = snapshot(store)
    assert seed(client(store), body).status_code in {409, 422}
    assert snapshot(store) == before


def test_seed_key_namespace_and_atomic_rollback_independent_of_manual_route(store, monkeypatch):
    c = client(store)
    with store[1].begin() as session:
        manual_id = service.create_campaign(session, "fifo_correct", payload()["plan"], "review-key").id
    first = seed(c)
    assert first.status_code == 200 and first.json()["campaign_id"] != manual_id
    with store[1]() as session:
        saved = session.get(Campaign, first.json()["campaign_id"])
        assert saved.create_key == "planner-seed.v1:" + sha256(b"review-key").hexdigest()
        assert session.get(PlannerState, saved.id) is None
    before = snapshot(store)
    assert seed(c).json() == first.json()
    assert snapshot(store) == before

    def failure(*args, **kwargs):
        raise service.Conflict("synthetic start rejection")

    monkeypatch.setattr(service, "start", failure)
    assert seed(c, key="must-rollback").status_code == 409
    assert snapshot(store) == before


@pytest.mark.parametrize("verdict", ["pass_within_scope", "design_mismatch", "property_failure", "timeout", "build_error", "inconclusive"])
def test_eligible_and_unavailable_settled_results_have_separate_attachment_gate(store, verdict):
    c = client(store)
    identifier = settle(store, c, verdict)
    before = snapshot(store)
    response = c.post(f"/api/campaigns/{identifier}/planner", json=ATTACH)
    if verdict in {"timeout", "build_error", "inconclusive"}:
        assert response.status_code == 409
        assert snapshot(store) == before
    else:
        assert response.status_code == 200
        assert response.json()["execution_status"] == "running"
        budget = c.get(f"/api/campaigns/{identifier}/planner/budget").json()
        assert budget["price_status"] == "unknown" and budget["reserved"]["cost"] is None
        assert budget["reserved"]["cycles"] == 10 and budget["reserved"]["seconds"] == "3.0"
        assert seed(c).json()["planner_attached"] is True


def test_project_ownership_is_rechecked_after_attachment_lock(store):
    healthy = client(store)
    identifier = settle(store, healthy)
    moved = False

    def move_project_before_lock():
        nonlocal moved
        if not moved:
            # Deterministic race: committed ownership changes after initial
            # project lookup, before the campaign write lock/reload.
            with store[1].begin() as session:
                session.get(Project, "fifo_correct").owner = "foreign"
            moved = True
        return "RUNNING"

    response = client(store, control=move_project_before_lock).post(
        f"/api/campaigns/{identifier}/planner", json=ATTACH)
    assert response.status_code == 404, response.text
    with store[1]() as session:
        assert session.get(PlannerState, identifier) is None


def test_current_project_owner_and_frozen_policy_control_reattachment(store):
    c = client(store)
    identifier = settle(store, c)
    path = f"/api/campaigns/{identifier}/planner"
    initial = c.post(path, json=ATTACH)
    assert initial.status_code == 200
    before = snapshot(store)
    changed = client(store, selected=policy(requested_model="different-server-model"))
    assert changed.post(path, json=ATTACH).status_code == 409
    assert snapshot(store) == before
    assert c.post(path, json=ATTACH).json() == initial.json()
    with store[1].begin() as session:
        session.get(Project, "fifo_correct").owner = "foreign"
    before = snapshot(store)
    assert c.post(path, json=ATTACH).status_code == 404
    assert snapshot(store) == before


@pytest.mark.parametrize("suffix", ["/rounds", "/cleanup"])
def test_cursor_outside_database_integer_range_is_rejected_before_query(store, suffix):
    c = client(store)
    identifier = settle(store, c)
    path = f"/api/campaigns/{identifier}/planner"
    assert c.post(path, json=ATTACH).status_code == 200
    before = snapshot(store)
    response = c.get(path + suffix, params={"after": str(2**80), "limit": 1})
    assert response.status_code == 422
    assert snapshot(store) == before


def test_safe_bounded_projections_across_pages_and_foreign_campaigns(store):
    c = client(store)
    identifier = settle(store, c)
    path = f"/api/campaigns/{identifier}/planner"
    assert c.post(path, json=ATTACH).status_code == 200
    with store[1].begin() as session:
        for ordinal in (4, 1, 3, 2):
            r = PlannerRound(campaign_id=identifier, ordinal=ordinal, generation=ordinal, lease=CANARY)
            session.add(r)
            session.flush()
            session.add(PlannerModelCall(round_id=r.id, requested_model="review-server-model",
                request_body=CANARY.encode(), request_hash="a" * 64, schema_hash="b" * 64,
                input_allowance=100, context={"provider_response": CANARY}))
            session.flush()
            session.add(PlannerCleanup(round_id=r.id, campaign_id=identifier, generation=ordinal,
                execution_lease=CANARY, request_hash="a" * 64, owner_token=CANARY, registered_at=AT))
    disabled = client(store, selected=PlannerPolicy())
    for suffix in ("/rounds", "/cleanup"):
        first = disabled.get(path + suffix, params={"limit": 2}).json()
        second = disabled.get(path + suffix, params={"limit": 2, "after": first["next_cursor"]}).json()
        assert [r["ordinal"] for r in first["items"] + second["items"]] == [1, 2, 3, 4]
        assert second["next_cursor"] is None
        for query in ("limit=101", "limit=0", "after=-1", "after=1.5"):
            assert disabled.get(path + suffix + "?" + query).status_code == 422
    texts = "".join(disabled.get(path + suffix).text for suffix in ("", "/budget", "/rounds", "/cleanup"))
    for forbidden in (CANARY, "request_body", "provider_response", "owner_token", "settlement_proof", "lease"):
        assert forbidden not in texts
    before = snapshot(store)
    assert disabled.post(path, json=ATTACH).status_code == 503
    assert snapshot(store) == before
    with store[1].begin() as session:
        session.get(Campaign, identifier).owner = "foreign"
    for suffix in ("", "/budget", "/rounds", "/cleanup"):
        assert disabled.get(path + suffix).json() == {"detail": "Not found"}
