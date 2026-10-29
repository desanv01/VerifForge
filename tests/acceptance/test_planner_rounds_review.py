"""Independent transaction/crash tests: isolated SQLite and synthetic results only."""
from dataclasses import replace
from datetime import datetime, timedelta, timezone
import hashlib
import importlib.util
import json
from pathlib import Path
from types import SimpleNamespace

from alembic import command
from alembic.config import Config
import pytest
from sqlalchemy import MetaData, Table, func, select, text
from sqlalchemy.dialects import postgresql
from sqlalchemy.exc import IntegrityError
from sqlalchemy.schema import CreateTable

from veriforge.adapters import get_adapter, registered_adapters
from veriforge.contracts import AttemptResult, Experiment, canonical_hash
from veriforge.core import planner_rounds as rounds, service
from veriforge.core.database import (Base, Attempt, Campaign, Dispatch, ExperimentRecord, Finding,
    PlannerFeedback, PlannerIntent, PlannerModelCall, PlannerReservation, PlannerRound,
    PlannerState, make_engine, session_factory)
from veriforge.core.planner_experiments import record_experiment, resolve_attempt_experiment
from veriforge.planner.models import InvestigationLimits, ProviderCompletion

AT = datetime(2026, 10, 29, 16, tzinfo=timezone.utc)
ROOT = Path(__file__).resolve().parents[2]


def synthetic_result(session, attempt_id):
    record = resolve_attempt_experiment(session, session.get(Attempt, attempt_id))
    return AttemptResult(attempt_id="synthetic-review", experiment_id=record.wire_id,
        execution_status="completed", verdict="pass_within_scope",
        identity={**record.identity, "environment_hash": "a" * 64}, usage={"cycles": 3},
        uncertainties=("Synthetic review input; no simulator ran",)).model_dump(mode="json")


def completion():
    return ProviderCompletion(model="review-model", content=json.dumps({"proposal": {
        "kind": "experiment", "objective_ids": ["FIFO_BEHAVIOR"], "seed": 33,
        "operations": [{"op": "reset"}, {"op": "write", "value": 91}, {"op": "read"}]}}),
        usage={"input_tokens": 10, "output_tokens": 10}, finish_reason="stop")


def seed_historical_catalog(connection):
    # Revision9 predates Project.kind. Seed its actual reflected tables, without
    # invoking today's Project ORM or changing this historical migration target.
    metadata = MetaData()
    contracts = Table("contracts", metadata, autoload_with=connection)
    projects = Table("projects", metadata, autoload_with=connection)
    assert "kind" not in projects.c
    seen = set()
    for descriptor in registered_adapters():
        reviewed = descriptor.reviewed_contract()
        if reviewed.contract_id not in seen:
            connection.execute(contracts.insert().values(id=reviewed.contract_id, data=reviewed.model_dump(mode="json"),
                identity={"contract_hash": canonical_hash(reviewed), "checker_hash": reviewed.checker.sha256}))
            seen.add(reviewed.contract_id)
        schema_version = descriptor.experiment_schema()["properties"]["schema_version"]["const"]
        for fixture in descriptor.fixture_ids:
            source = descriptor.fixture_source(fixture)
            connection.execute(projects.insert().values(id=fixture, owner="local", contract_id=reviewed.contract_id,
                data={"id": fixture, "name": fixture.replace("_", " ").title(), "family": descriptor.target,
                    "support_level": "experimental", "contract_id": reviewed.contract_id,
                    "source_file_hash": source["source_file_hash"], "variant": source["variant"],
                    "adapter_id": descriptor.adapter_id, "experiment_schema_version": schema_version,
                    "trace_schema": descriptor.trace_schema, "trace_artifact": descriptor.trace_artifact}))


def seed_historical_campaign(session, project_id, wire, key, *, completion_policy="sequential"):
    # Historical test data setup only: no Project ORM query on revision9.
    experiment = get_adapter("rtl.fifo.v1").validate_experiment(wire)
    assert experiment.fixture_id == project_id
    request_hash = service.digest({"project_id": project_id, "plan": wire})
    if completion_policy != "single":
        request_hash = service.digest({"request_hash": request_hash, "completion_policy": completion_policy})
    parent = Campaign(project_id=project_id, contract_id=experiment.contract_id,
        adapter_id="rtl.fifo.v1", completion_policy=completion_policy, create_key=key,
        request_hash=request_hash, plan=wire, identity=get_adapter("rtl.fifo.v1").expected_identity(experiment))
    session.add(parent)
    session.flush()
    record_experiment(session, parent, wire, "initial", origin="manual", initial=True)
    service.emit(session, parent, "campaign.created")
    return parent


@pytest.fixture
def db():
    engine = make_engine("sqlite:///:memory:")
    config = Config(str(ROOT / "alembic.ini"))
    with engine.begin() as connection:
        config.attributes["connection"] = connection
        command.upgrade(config, "9u2rounds")
        seed_historical_catalog(connection)
    sessions = session_factory(engine)
    with sessions.begin() as session:
        plan = Experiment(experiment_id="review-seed", fixture_id="fifo_correct",
            objective_ids=("FIFO_BEHAVIOR",), budget={"max_cycles": 8, "wall_time_seconds": 2.0},
            operations=({"op": "reset"}, {"op": "write", "value": 17}, {"op": "read"}))
        parent = seed_historical_campaign(session, plan.fixture_id, plan.model_dump(mode="json"), "review-seed", completion_policy="sequential")
        service.start(session, parent.id)
        attempt = session.scalar(select(Attempt).where(Attempt.campaign_id == parent.id))
        token, _, _ = service.claim(session, attempt.id)
        service.finalize(session, attempt.id, token, synthetic_result(session, attempt.id))
        parent_id = parent.id
        rounds.attach_planner(session, parent.id, limits=InvestigationLimits(max_rounds=4,
            max_total_cycles=80, max_cycles_per_experiment=8, max_wall_seconds=60.0,
            max_execution_seconds=2.0, max_model_seconds=2.0), requested_model="review-model", at=AT)
    yield engine, sessions, parent_id
    engine.dispose()


def counts(session):
    return tuple(session.scalar(select(func.count()).select_from(model)) for model in
        (ExperimentRecord, Attempt, Dispatch, PlannerRound, PlannerModelCall, PlannerReservation, PlannerFeedback, PlannerIntent))


def queue(db):
    with db[1].begin() as session:
        return rounds.advance(session, db[2], at=AT).id


def claim(db, round_id):
    with db[1].begin() as session:
        return rounds.claim_model_round(session, round_id, at=AT + timedelta(milliseconds=100))


def test_round_admission_crash_rolls_back_feedback_ledger_and_outbox_together(db):
    with db[1]() as session:
        before = counts(session)
    with pytest.raises(RuntimeError, match="crash"):
        with db[1].begin() as session:
            assert rounds.advance(session, db[2], at=AT)
            raise RuntimeError("crash before admission commit")
    with db[1]() as session:
        assert counts(session) == before
        assert rounds.totals(session, db[2])["cycles"] == 8
        assert session.get(PlannerState, db[2]).phase == "ready"
    first = queue(db)
    with db[1].begin() as session:
        assert rounds.advance(session, db[2], at=AT).id == first
        assert session.scalar(select(func.count()).select_from(PlannerReservation)) == 2


def test_claim_before_commit_crash_does_not_mark_remote_call_started(db):
    round_id = queue(db)
    with pytest.raises(RuntimeError):
        with db[1].begin() as session:
            assert rounds.claim_model_round(session, round_id, at=AT)
            raise RuntimeError("no transport invoked before commit")
    with db[1]() as session:
        assert session.get(PlannerRound, round_id).status == "queued"
        assert session.get(PlannerModelCall, round_id).started_at is None
    assert claim(db, round_id)


def test_accepted_completion_crash_and_duplicate_have_one_admission(db):
    lease = claim(db, queue(db))
    with db[1]() as session:
        before, allowance = counts(session), rounds.totals(session, db[2])
    with pytest.raises(RuntimeError):
        with db[1].begin() as session:
            assert rounds.finish_model_round(session, lease, completion(), at=AT + timedelta(milliseconds=200))
            raise RuntimeError("crash before accepted experiment and dispatch commit")
    with db[1]() as session:
        assert counts(session) == before and rounds.totals(session, db[2]) == allowance
        assert session.get(PlannerRound, lease.round_id).status == "running"
    with db[1].begin() as session:
        assert rounds.finish_model_round(session, lease, completion(), at=AT + timedelta(milliseconds=300))
    with db[1]() as session:
        accepted, charged = counts(session), rounds.totals(session, db[2])
        assert charged["cycles"] == 16 and charged["seconds"] == 6
        assert session.get(Campaign, db[2]).verdict is None
    with db[1].begin() as session:
        assert not rounds.finish_model_round(session, lease, completion(), at=AT + timedelta(milliseconds=400))
    with db[1]() as session:
        assert counts(session) == accepted and rounds.totals(session, db[2]) == charged


def test_committed_claim_recovery_never_retries_uncertain_remote_call(db):
    lease = claim(db, queue(db))
    with db[1]() as session:
        before, reserved = counts(session), rounds.totals(session, db[2])
    with db[1].begin() as session:
        assert rounds.recover_rounds(session, at=AT + timedelta(seconds=3)) == 1
        assert not rounds.finish_model_round(session, lease, completion(), at=AT + timedelta(seconds=3))
        assert rounds.advance(session, db[2], at=AT + timedelta(seconds=4)) is None
        assert not rounds.pending_intents(session, at=AT + timedelta(seconds=4))
    with db[1]() as session:
        assert counts(session) == before and rounds.totals(session, db[2]) == reserved
        assert session.get(PlannerModelCall, lease.round_id).outcome == "remote_outcome_unknown"
        assert session.get(PlannerState, db[2]).stop_reason == "model_call_uncertain"


def test_exact_body_model_policy_and_forged_context_lease(db):
    lease = claim(db, queue(db))
    request = json.loads(lease.request_body)
    assert request["model"] == lease.requested_model == "review-model"
    assert hashlib.sha256(lease.request_body).hexdigest() == lease.request_hash
    forged = replace(lease, context={**lease.context, "correction": "forged context"})
    with db[1].begin() as session:
        assert not rounds.finish_model_round(session, forged, completion(), at=AT + timedelta(milliseconds=200))
        assert session.get(PlannerModelCall, lease.round_id).context == lease.context
        assert session.get(PlannerModelCall, lease.round_id).outcome is None
    with db[1].begin() as session:
        assert rounds.finish_model_round(session, lease, completion(), at=AT + timedelta(milliseconds=300))


@pytest.mark.parametrize("sql", [
    "UPDATE planner_model_calls SET context='{}'",
    "UPDATE planner_states SET deadline_at='2099-01-01'",
    "DELETE FROM planner_intents",
])
def test_raw_sql_cannot_rewrite_admitted_identity_or_erase_outbox(db, sql):
    queue(db)
    with pytest.raises(IntegrityError):
        with db[0].begin() as connection:
            connection.execute(text(sql))


@pytest.mark.parametrize("amount", ["NaN", "-0.1"])
def test_corrupt_ledger_after_admission_blocks_remote_claim(db, amount):
    round_id = queue(db)
    with db[0].begin() as connection:
        connection.execute(text("INSERT INTO planner_reservations(id,campaign_id,admission_key,reserved_tokens,reserved_cycles,reserved_seconds) VALUES ('corrupt',:campaign,'corrupt',0,0,:amount)"), {"campaign": db[2], "amount": amount})
    rejected = False
    try:
        with db[1].begin() as session:
            rejected = rounds.claim_model_round(session, round_id, at=AT + timedelta(milliseconds=100)) is None
    except ValueError:
        rejected = True
    assert rejected, "A corrupt immutable ledger must not yield a provider-call lease"


def test_sqlite_composite_reservation_foreign_keys_reject_other_campaign(db):
    round_id = queue(db)
    with db[1].begin() as session:
        seed_record = session.scalar(select(ExperimentRecord).where(ExperimentRecord.campaign_id == db[2]))
        other = seed_historical_campaign(session, "fifo_correct", seed_record.plan, "other-review-parent", completion_policy="sequential")
        service.start(session, other.id)
        attempt = session.scalar(select(Attempt).where(Attempt.campaign_id == other.id))
        token, _, _ = service.claim(session, attempt.id)
        service.finalize(session, attempt.id, token, synthetic_result(session, attempt.id))
        rounds.attach_planner(session, other.id, limits=session.get(PlannerState, db[2]).policy,
            requested_model="review-model", at=AT)
        other_id = other.id
    with pytest.raises(IntegrityError):
        with db[0].begin() as connection:
            connection.execute(text("INSERT INTO planner_reservations(id,campaign_id,admission_key,round_id,reserved_tokens,reserved_cycles,reserved_seconds) VALUES ('wrong',:campaign,'wrong',:round,0,0,'1')"), {"campaign": other_id, "round": round_id})


@pytest.mark.parametrize("invalid", ["requirement", "cycles"])
def test_invalid_executor_result_cannot_commit_a_functional_finding(db, invalid):
    lease = claim(db, queue(db))
    with db[1].begin() as session:
        rounds.finish_model_round(session, lease, completion(), at=AT + timedelta(milliseconds=200))
        attempt = session.scalar(select(Attempt).where(Attempt.campaign_id == db[2], Attempt.execution_status == "queued"))
        attempt_id = attempt.id
        token, _, _ = service.claim(session, attempt_id)
    try:
        with db[1].begin() as session:
            payload = synthetic_result(session, attempt_id)
            payload.update(verdict="design_mismatch", failure_signature={
                "requirement_id": "UNREVIEWED" if invalid == "requirement" else "FIFO_BEHAVIOR",
                "failure_class": "fifo.data", "first_cycle": 2, "expected": "91", "observed": "0", "signature_hash": "b" * 64})
            if invalid == "cycles":
                payload["usage"]["cycles"] = 9  # admitted maximum is eight
            rounds.finish_execution_step(session, attempt_id, token, payload, at=AT + timedelta(milliseconds=300))
    except ValueError:
        pass  # Reject-and-rollback is also a valid fail-closed outcome.
    with db[1]() as session:
        assert session.scalar(select(func.count()).select_from(Finding)) == 0
        assert session.get(Attempt, attempt_id).verdict != "design_mismatch"


def test_postgres_migration_emits_six_tables_and_immutable_trigger_ddl(monkeypatch):
    path = ROOT / "migrations/versions/9u2rounds_durable_round_state.py"
    spec = importlib.util.spec_from_file_location("review_rounds_migration", path)
    migration = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(migration)
    created, statements = [], []
    recorder = SimpleNamespace(create_table=lambda name, *args: created.append(name),
        get_bind=lambda: SimpleNamespace(dialect=SimpleNamespace(name="postgresql")),
        execute=lambda statement: statements.append(str(statement)))
    monkeypatch.setattr(migration, "op", recorder)
    migration.upgrade()
    assert set(created) == {"planner_states", "planner_rounds", "planner_model_calls", "planner_reservations", "planner_feedback", "planner_intents"}
    assert sum("CREATE TRIGGER" in statement for statement in statements) == 12
    assert any("RETURNS trigger LANGUAGE plpgsql" in statement for statement in statements)
    for name in created:
        ddl = str(CreateTable(Base.metadata.tables[name]).compile(dialect=postgresql.dialect()))
        assert "FOREIGN KEY" in ddl
    assert "BYTEA" in str(CreateTable(Base.metadata.tables["planner_model_calls"]).compile(dialect=postgresql.dialect()))
