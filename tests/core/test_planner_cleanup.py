"""Synthetic local-task lifecycle evidence; no provider or live DB."""
from dataclasses import replace
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
import importlib.util
from pathlib import Path
from types import SimpleNamespace

from alembic import command
from alembic.config import Config
import pytest
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql
from sqlalchemy.exc import IntegrityError
from sqlalchemy.schema import CreateTable

from veriforge.contracts import Experiment
from veriforge.core import planner_cleanup as cleanup, planner_rounds as rounds, service
from veriforge.core.catalog import seed_catalog
from veriforge.core.database import (Attempt, Base, PlannerCleanup, PlannerModelCall, PlannerRound,
    PlannerState, make_engine, session_factory)
from veriforge.core.planner_experiments import resolve_attempt_experiment
from veriforge.planner.models import InvestigationLimits

AT = datetime(2026, 10, 29, 15, tzinfo=timezone.utc)


@pytest.fixture
def db(tmp_path):
    engine = make_engine("sqlite:///" + (tmp_path / "cleanup.db").as_posix())
    config = Config("alembic.ini")
    with engine.begin() as connection:
        config.attributes["connection"] = connection
        command.upgrade(config, "head")
    sessions = session_factory(engine)
    with sessions.begin() as session:
        seed_catalog(session)
        experiment = Experiment(experiment_id="seed", fixture_id="fifo_correct", objective_ids=("FIFO_BEHAVIOR",),
            budget={"max_cycles": 8, "wall_time_seconds": 2.0}, operations=({"op": "reset"}, {"op": "read"}))
        parent = service.create_campaign(session, "fifo_correct", experiment.model_dump(mode="json"), "cleanup-test", completion_policy="sequential")
        service.start(session, parent.id)
        attempt = session.scalar(sa.select(Attempt).where(Attempt.campaign_id == parent.id))
        token, _, _ = service.claim(session, attempt.id)
        record = resolve_attempt_experiment(session, attempt)
        service.finalize(session, attempt.id, token, dict(attempt_id="synthetic-checker", experiment_id=record.wire_id,
            execution_status="completed", verdict="pass_within_scope", identity={**record.identity, "environment_hash": "b" * 64}, usage={"cycles": 2}))
        rounds.attach_planner(session, parent.id, limits=InvestigationLimits(max_total_cycles=80, max_wall_seconds=60,
            max_cycles_per_experiment=8, max_execution_seconds=2, max_model_seconds=2), requested_model="cleanup-fake", at=AT)
        round_id = rounds.advance(session, parent.id, at=AT).id
        lease = rounds.claim_model_round(session, round_id, at=AT)
    yield engine, sessions, lease
    engine.dispose()


def register(db):
    with db[1].begin() as session:
        return cleanup.record_pending(session, db[2], at=AT)


def test_restart_pending_unknown_and_late_exact_owner_ack(db):
    ticket = register(db)
    with db[1]() as session:
        view = cleanup.read_cleanup(session, ticket.round_id)
        assert view.local_state == "pending"
        assert not hasattr(view, "owner_token")
        assert cleanup.unresolved_cleanup(session) == (view,)
    with db[1].begin() as session:
        assert cleanup.mark_owner_lost(session, ticket.round_id, at=AT + timedelta(days=1))
    with db[1]() as session:
        assert cleanup.read_cleanup(session, ticket.round_id).local_state == "unknown"
        assert session.get(PlannerModelCall, ticket.round_id).outcome is None
    with db[1].begin() as session:
        assert cleanup.acknowledge_settled(session, ticket, proof="cooperative_tasks_settled", at=AT + timedelta(days=2))
    with db[1].begin() as session:
        assert cleanup.acknowledge_settled(session, ticket, proof="cooperative_tasks_settled", at=AT + timedelta(days=3))
        assert cleanup.unresolved_cleanup(session) == ()
        assert cleanup.read_cleanup(session, ticket.round_id).settled_at == AT + timedelta(days=2)


def test_cancel_revokes_execution_but_original_cleanup_ack_survives(db):
    ticket = register(db)
    with db[1].begin() as session:
        before = rounds.totals(session, ticket.campaign_id)
        rounds.cancel_investigation(session, ticket.campaign_id, at=AT + timedelta(seconds=1))
        generation = session.get(PlannerState, ticket.campaign_id).generation
        revoked = session.get(PlannerRound, ticket.round_id).lease
        outcome = session.get(PlannerModelCall, ticket.round_id).outcome
        assert revoked != ticket.execution_lease
    with db[1].begin() as session:
        assert cleanup.acknowledge_settled(session, ticket, proof="cooperative_tasks_settled", at=AT + timedelta(seconds=2))
        assert session.get(PlannerState, ticket.campaign_id).generation == generation
        assert session.get(PlannerState, ticket.campaign_id).phase == "cancelled"
        assert session.get(PlannerRound, ticket.round_id).lease == revoked
        assert session.get(PlannerModelCall, ticket.round_id).outcome == outcome == "remote_outcome_unknown"
        assert rounds.totals(session, ticket.campaign_id) == before


@pytest.mark.parametrize("field,value", [("generation", 999), ("round_id", "wrong"), ("request_hash", "f" * 64),
    ("execution_lease", "revoked"), ("owner_token", "other-owner"), ("campaign_id", "other-campaign")])
def test_forged_ticket_cannot_ack(db, field, value):
    ticket = register(db)
    with db[1].begin() as session:
        assert not cleanup.acknowledge_settled(session, replace(ticket, **{field: value}), proof="cooperative_tasks_settled", at=AT)
        assert cleanup.read_cleanup(session, ticket.round_id).local_state == "pending"


def test_registration_commit_rollback_duplicates_and_revocation(db):
    with pytest.raises(RuntimeError):
        with db[1].begin() as session:
            assert cleanup.record_pending(session, db[2], at=AT)
            raise RuntimeError("Crash before ownership commit, before IO")
    with db[1]() as session:
        assert cleanup.unresolved_cleanup(session) == ()
    assert register(db)
    assert register(db) is None
    with db[1].begin() as session:
        rounds.cancel_investigation(session, db[2].campaign_id, at=AT)
        assert cleanup.record_pending(session, db[2], at=AT) is None


@pytest.mark.parametrize("variant", ["generation", "lease", "expired", "paused"])
def test_registration_requires_current_bound_call(db, variant):
    lease, at, control = db[2], AT, "RUNNING"
    if variant == "generation": lease = replace(lease, generation=999)
    if variant == "lease": lease = replace(lease, lease="wrong")
    if variant == "expired": at += timedelta(seconds=10)
    if variant == "paused": control = "PAUSED"
    with db[1].begin() as session:
        assert cleanup.record_pending(session, lease, at=at, control=control) is None
        assert cleanup.unresolved_cleanup(session) == ()


@pytest.mark.parametrize("generation", [True, 1.0, -1])
def test_registration_rejects_non_integer_or_negative_generation_before_write(db, generation):
    with db[1].begin() as session:
        with pytest.raises(ValueError, match="integer generation"):
            cleanup.record_pending(session, replace(db[2], generation=generation), at=AT)
        assert not session.new
        assert cleanup.unresolved_cleanup(session) == ()
    assert register(db) is not None


def test_no_elapsed_time_or_wrong_proof_settles_cleanup(db):
    ticket = register(db)
    with db[1].begin() as session:
        rounds.recover_rounds(session, at=AT + timedelta(days=5))
        assert cleanup.read_cleanup(session, ticket.round_id).local_state == "pending"
        for proof in ("timeout", "heartbeat_expired", "wrapper_done", True, None):
            with pytest.raises(ValueError):
                cleanup.acknowledge_settled(session, ticket, proof=proof, at=AT + timedelta(days=6))
        assert cleanup.mark_owner_lost(session, ticket.round_id, at=AT + timedelta(days=6))
        assert not cleanup.mark_owner_lost(session, ticket.round_id, at=AT + timedelta(days=7))
        assert cleanup.read_cleanup(session, ticket.round_id).local_state == "unknown"


def test_ticket_generation_is_strict_and_identity_immutable_in_orm(db):
    ticket = register(db)
    with db[1].begin() as session:
        with pytest.raises(ValueError):
            cleanup.acknowledge_settled(session, replace(ticket, generation=True), proof="cooperative_tasks_settled", at=AT)
    with pytest.raises(ValueError, match="immutable"):
        with db[1].begin() as session:
            session.get(PlannerCleanup, ticket.round_id).execution_lease = "rewritten"


def test_backwards_timestamps_rejected(db):
    ticket = register(db)
    with db[1].begin() as session:
        with pytest.raises(ValueError): cleanup.mark_owner_lost(session, ticket.round_id, at=AT - timedelta(seconds=1))
        with pytest.raises(ValueError): cleanup.acknowledge_settled(session, ticket, proof="cooperative_tasks_settled", at=AT - timedelta(seconds=1))
        cleanup.mark_owner_lost(session, ticket.round_id, at=AT + timedelta(seconds=2))
        with pytest.raises(ValueError): cleanup.acknowledge_settled(session, ticket, proof="cooperative_tasks_settled", at=AT + timedelta(seconds=1))


@pytest.mark.parametrize("sql", ["DELETE FROM planner_cleanup", "UPDATE planner_cleanup SET owner_token='other'",
    "UPDATE planner_cleanup SET generation=999", "UPDATE planner_cleanup SET request_hash='changed'",
    "UPDATE planner_cleanup SET local_state='settled',settled_at=registered_at",  # NULL proof cannot bypass CHECK.
    "UPDATE planner_cleanup SET local_state='unknown',owner_lost_at='2000-01-01'"])
def test_sql_preserves_cleanup_history_and_time_checks(db, sql):
    register(db)
    with pytest.raises(IntegrityError):
        with db[0].begin() as connection: connection.execute(sa.text(sql))


def test_unknown_and_settled_history_cannot_be_rewritten(db):
    ticket = register(db)
    with db[1].begin() as session: cleanup.mark_owner_lost(session, ticket.round_id, at=AT + timedelta(seconds=1))
    for sql in ("UPDATE planner_cleanup SET local_state='pending',owner_lost_at=NULL", "UPDATE planner_cleanup SET owner_lost_at='2030-01-01'"):
        with pytest.raises(IntegrityError):
            with db[0].begin() as connection: connection.execute(sa.text(sql))
    with db[1].begin() as session: cleanup.acknowledge_settled(session, ticket, proof="cooperative_tasks_settled", at=AT + timedelta(seconds=2))
    with pytest.raises(IntegrityError):
        with db[0].begin() as connection: connection.execute(sa.text("UPDATE planner_cleanup SET settled_at='2030-01-01'"))
    with pytest.raises(ValueError):
        with db[1].begin() as session: session.get(PlannerCleanup, ticket.round_id).settled_at = AT + timedelta(seconds=3)


def test_downgrade_refuses_cleanup_history(db):
    register(db)
    config = Config("alembic.ini")
    with pytest.raises(ValueError, match="retained cleanup"):
        with db[0].begin() as connection:
            config.attributes["connection"] = connection
            command.downgrade(config, "9u2rounds")


def test_additive_migration_roundtrip_preserves_old_call(db):
    config = Config("alembic.ini")
    with db[0].begin() as connection:
        config.attributes["connection"] = connection
        command.downgrade(config, "9u2rounds")
        command.upgrade(config, "head")
    with db[1]() as session:
        assert session.get(PlannerModelCall, db[2].round_id).request_hash == db[2].request_hash
        assert session.get(PlannerRound, db[2].round_id).lease == db[2].lease
        assert rounds.totals(session, db[2].campaign_id)["cycles"] == 8
    assert register(db)


def test_concurrent_registration_grants_one_cleanup_ticket(db):
    with ThreadPoolExecutor(max_workers=3) as pool:
        tickets = list(pool.map(lambda _: register(db), range(3)))
    assert sum(ticket is not None for ticket in tickets) == 1
    ticket = next(ticket for ticket in tickets if ticket)
    def ack(_):
        with db[1].begin() as session:
            return cleanup.acknowledge_settled(session, ticket, proof="cooperative_tasks_settled", at=AT)
    with ThreadPoolExecutor(max_workers=3) as pool:
        assert all(pool.map(ack, range(3)))


def test_postgres_ddl_matches_model_and_emits_fences(monkeypatch):
    path = Path("migrations/versions/10u2cleanup_durable_local_cleanup.py")
    spec = importlib.util.spec_from_file_location("cleanup_migration", path)
    migration = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(migration)
    statements, created = [], []
    def create(name, *args):
        metadata = sa.MetaData()
        # Referenced definitions allow standalone DDL compilation only.
        sa.Table("planner_model_calls", metadata, sa.Column("round_id", sa.String(36), primary_key=True))
        sa.Table("planner_rounds", metadata, sa.Column("id", sa.String(36)), sa.Column("campaign_id", sa.String(36)))
        created.append(sa.Table(name, metadata, *args))
    monkeypatch.setattr(migration, "op", SimpleNamespace(create_table=create,
        get_bind=lambda: SimpleNamespace(dialect=SimpleNamespace(name="postgresql")), execute=lambda sql: statements.append(str(sql))))
    migration.upgrade()
    actual = str(CreateTable(created[0]).compile(dialect=postgresql.dialect()))
    expected = str(CreateTable(Base.metadata.tables["planner_cleanup"]).compile(dialect=postgresql.dialect()))
    normalize = lambda ddl: {line.strip().rstrip(",") for line in ddl.splitlines()}
    assert normalize(actual) == normalize(expected)
    assert sum("CREATE TRIGGER" in statement for statement in statements) == 3
    assert any("OLD.local_state = 'settled'" in statement and "RETURN NEW" in statement for statement in statements)
