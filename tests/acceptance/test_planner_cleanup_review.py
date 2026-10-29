"""Independent synthetic cleanup fences. No database, migration, provider or job."""
from dataclasses import asdict, replace
from datetime import datetime, timedelta, timezone
import hashlib
import importlib.util
from pathlib import Path
from types import SimpleNamespace

import pytest
import sqlalchemy as sa
from sqlalchemy.orm.attributes import set_committed_value

from veriforge.core import database, planner_cleanup as cleanup
from veriforge.core.planner_rounds import ModelLease

AT = datetime(2026, 10, 30, tzinfo=timezone.utc)
BODY = b"synthetic committed request"


class MemorySession:
    """Session-shaped fixture; flush defaults only, no SQL/connection/durability."""
    def __init__(self, rows=None):
        self.rows = rows if rows is not None else {}
        self.flushes = 0

    def get(self, cls, key):
        return self.rows.get((cls, key))

    def add(self, row):
        self.rows[(type(row), row.round_id)] = row

    def flush(self):
        self.flushes += 1
        for (cls, key), row in self.rows.items():
            if cls is database.PlannerCleanup:
                if row.owner_token is None:
                    row.owner_token = "synthetic-original-owner"
                if row.local_state is None:
                    row.local_state = "pending"

    def refresh(self, row):
        pass

    def scalars(self, query):
        # Query predicate separately checked against generated SQL below.
        return [key for (cls, key), row in self.rows.items()
                if cls is database.PlannerCleanup and row.local_state in ("pending", "unknown")]


@pytest.fixture
def state(monkeypatch):
    campaign = SimpleNamespace(id="campaign-review", execution_status="running")
    lease = ModelLease(campaign.id, "round-review", 1, "execution-token", "synthetic-model",
        hashlib.sha256(BODY).hexdigest(), "c" * 64, 1234, BODY, {"scope": "synthetic"}, 1.0, "meta")
    session = MemorySession({
        (database.PlannerState, campaign.id): SimpleNamespace(phase="model_running", generation=1,
            deadline_at=AT + timedelta(seconds=30), requested_model=lease.requested_model, strategy="meta"),
        (database.PlannerRound, lease.round_id): SimpleNamespace(id=lease.round_id, campaign_id=campaign.id,
            generation=1, lease=lease.lease, status="running"),
        (database.PlannerModelCall, lease.round_id): SimpleNamespace(started_at=AT,
            deadline_at=AT + timedelta(seconds=10), request_hash=lease.request_hash,
            request_body=BODY, schema_hash=lease.schema_hash, requested_model=lease.requested_model,
            input_allowance=lease.input_allowance, context=lease.context, outcome="remote_outcome_unknown"),
    })
    monkeypatch.setattr(cleanup.service, "lock", lambda current, campaign_id: campaign)
    return SimpleNamespace(session=session, lease=lease, campaign=campaign)


@pytest.mark.parametrize("field,value", [
    ("generation", 2), ("lease", "wrong"), ("request_hash", "f" * 64),
    ("request_body", b"substituted"), ("schema_hash", "f" * 64),
    ("requested_model", "other"), ("context", {"scope": "substituted"}),
    ("input_allowance", 4321), ("strategy", "mock_planner"),
])
def test_original_call_fences_reject_changed_lease(state, field, value):
    assert cleanup.record_pending(state.session, replace(state.lease, **{field: value}), at=AT) is None
    assert state.session.get(database.PlannerCleanup, state.lease.round_id) is None


@pytest.mark.parametrize("generation", [True, 1.0])
def test_registration_generation_rejects_numeric_aliases_before_grant(state, generation):
    try:
        ticket = cleanup.record_pending(state.session, replace(state.lease, generation=generation), at=AT)
    except ValueError:
        return
    assert ticket is None, "Malformed generation granted an owner ticket which acknowledge_settled rejects"


@pytest.mark.parametrize("generation", [True, 1.0, -1])
def test_invalid_generation_never_locks_reads_adds_or_flushes(state, monkeypatch, generation):
    def forbidden(*args, **kwargs):
        pytest.fail("Invalid generation reached locking or session mutation")
    monkeypatch.setattr(cleanup.service, "lock", forbidden)
    monkeypatch.setattr(state.session, "get", forbidden)
    monkeypatch.setattr(state.session, "add", forbidden)
    monkeypatch.setattr(state.session, "flush", forbidden)
    with pytest.raises(ValueError):
        cleanup.record_pending(state.session, replace(state.lease, generation=generation), at=AT)


@pytest.mark.parametrize("field,value", [("generation", 9), ("owner_token", "other-owner"),
    ("execution_lease", "other-lease"), ("request_hash", "f" * 64),
    ("campaign_id", "other-campaign"), ("round_id", "other-round")])
def test_ack_requires_exact_original_cleanup_ticket(state, field, value):
    ticket = cleanup.record_pending(state.session, state.lease, at=AT)
    assert not cleanup.acknowledge_settled(state.session, replace(ticket, **{field: value}),
        proof="cooperative_tasks_settled", at=AT)
    assert cleanup.read_cleanup(state.session, ticket.round_id).local_state == "pending"


def test_duplicate_non_grant_restart_inventory_and_cancelled_late_ack(state):
    ticket = cleanup.record_pending(state.session, state.lease, at=AT)
    assert cleanup.record_pending(state.session, state.lease, at=AT) is None
    row = state.session.get(database.PlannerRound, ticket.round_id)
    planner = state.session.get(database.PlannerState, ticket.campaign_id)
    call = state.session.get(database.PlannerModelCall, ticket.round_id)
    state.campaign.execution_status, row.status, row.lease = "cancelled", "cancelled", "revoked"
    planner.phase, planner.generation = "cancelled", 2
    # Fresh fixture session over retained state tests view semantics, not DB restart.
    fresh = MemorySession(state.session.rows)
    assert cleanup.unresolved_cleanup(fresh)[0].local_state == "pending"
    assert cleanup.mark_owner_lost(fresh, ticket.round_id, at=AT + timedelta(days=1))
    assert cleanup.unresolved_cleanup(MemorySession(fresh.rows))[0].local_state == "unknown"
    before = (row.status, row.lease, planner.phase, planner.generation, call.outcome)
    assert cleanup.acknowledge_settled(fresh, ticket, proof="cooperative_tasks_settled", at=AT + timedelta(days=2))
    assert (row.status, row.lease, planner.phase, planner.generation, call.outcome) == before
    assert cleanup.unresolved_cleanup(fresh) == ()
    assert cleanup.acknowledge_settled(fresh, ticket, proof="cooperative_tasks_settled", at=AT + timedelta(days=3))
    assert cleanup.read_cleanup(fresh, ticket.round_id).settled_at == AT + timedelta(days=2)
    view = asdict(cleanup.read_cleanup(fresh, ticket.round_id))
    assert "owner_token" not in view and "execution_lease" not in view


@pytest.mark.parametrize("proof", ["wrapper_done", "timeout", "heartbeat_expired", "remote_abort", True])
def test_elapsed_time_and_wrapper_completion_are_not_local_settlement_proof(state, proof):
    ticket = cleanup.record_pending(state.session, state.lease, at=AT)
    with pytest.raises(ValueError):
        cleanup.acknowledge_settled(state.session, ticket, proof=proof, at=AT + timedelta(days=30))
    assert cleanup.read_cleanup(state.session, ticket.round_id).local_state == "pending"


def test_orm_identity_and_monotonic_hooks_reject_rewrites_without_db():
    def row():
        record = database.PlannerCleanup(round_id="round", campaign_id="campaign", generation=1,
            execution_lease="lease", request_hash="a" * 64, owner_token="owner", registered_at=AT,
            local_state="unknown", owner_lost_at=AT, settled_at=None, settlement_proof=None)
        for column in database.PlannerCleanup.__table__.columns:
            set_committed_value(record, column.name, getattr(record, column.name))
        return record
    changed = row()
    changed.owner_token = "substitute"
    with pytest.raises(ValueError, match="immutable"):
        database._frozen_fields(None, None, changed)
    changed = row()
    changed.local_state, changed.owner_lost_at = "pending", None
    with pytest.raises(ValueError):
        database._cleanup_transition(None, None, changed)
    changed = row()
    changed.owner_lost_at = AT + timedelta(seconds=1)
    with pytest.raises(ValueError):
        database._cleanup_transition(None, None, changed)


def test_downgrade_history_guard_and_sql_fences_offline(monkeypatch):
    path = Path(__file__).resolve().parents[2] / "migrations/versions/10u2cleanup_durable_local_cleanup.py"
    spec = importlib.util.spec_from_file_location("review_cleanup_offline", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    actions, tables = [], []
    bind = SimpleNamespace(dialect=SimpleNamespace(name="sqlite"), scalar=lambda query: 1)
    monkeypatch.setattr(module, "op", SimpleNamespace(get_bind=lambda: bind,
        drop_table=lambda name: actions.append(("drop", name)), execute=lambda sql: actions.append(str(sql)),
        create_table=lambda name, *columns: tables.append((name, columns))))
    # Offline recording only; no Alembic command, engine, database or SQL execution.
    module.upgrade()
    assert len(tables) == 1 and tables[0][0] == "planner_cleanup"
    triggers = [sql for sql in actions if isinstance(sql, str)]
    assert len(triggers) == 3
    assert any("BEFORE DELETE" in sql and "RAISE(ABORT" in sql for sql in triggers)
    assert any("OLD.local_state = 'settled'" in sql and "OLD.local_state = 'unknown'" in sql and "NEW.owner_lost_at IS NULL" in sql for sql in triggers)
    assert any("BEFORE UPDATE OF round_id,campaign_id,generation,execution_lease,request_hash,owner_token,registered_at" in sql for sql in triggers)
    constraints = {str(item.sqltext) for item in tables[0][1] if isinstance(item, sa.CheckConstraint)}
    expected = {str(item.sqltext) for item in database.PlannerCleanup.__table__.constraints if isinstance(item, sa.CheckConstraint)}
    assert constraints == expected
    with pytest.raises(ValueError, match="retained cleanup"):
        module.downgrade()
    assert not any(isinstance(action, tuple) and action[0] == "drop" for action in actions)
    bind.scalar = lambda query: 0
    module.downgrade()
    assert actions[-1] == ("drop", "planner_cleanup")
