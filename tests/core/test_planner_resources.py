"""Temporary SQLite races/fences and offline PG DDL; no provider or broker."""
from concurrent.futures import ThreadPoolExecutor
from dataclasses import asdict, replace
from datetime import datetime, timedelta, timezone
import importlib.util
from pathlib import Path
from threading import Barrier
from types import SimpleNamespace

from alembic import command
from alembic.config import Config
import pytest
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql
from sqlalchemy.exc import IntegrityError
from sqlalchemy.schema import CreateTable

from veriforge.contracts import Experiment
from veriforge.core import planner_cleanup as cleanup, planner_resources as resources, planner_rounds as rounds, service
from veriforge.core.catalog import seed_catalog
from veriforge.core.database import (Attempt, Base, PlannerCleanup, PlannerModelCall, PlannerRound,
    PlannerRuntimeSlot, PlannerState, make_engine, session_factory)
from veriforge.core.planner_experiments import resolve_attempt_experiment
from veriforge.planner.models import InvestigationLimits
from veriforge.planner.models import ProviderCompletion

AT = datetime(2026, 10, 30, tzinfo=timezone.utc)
ROLE = resources.ROLE


@pytest.fixture
def db(tmp_path):
    engine = make_engine('sqlite:///' + (tmp_path / 'resources.db').as_posix())
    config = Config('alembic.ini')
    with engine.begin() as connection:
        config.attributes['connection'] = connection
        command.upgrade(config, 'head')
    sessions = session_factory(engine)
    with sessions.begin() as session: seed_catalog(session)
    yield engine, sessions
    engine.dispose()


def queued(db, key):
    with db[1].begin() as session:
        experiment = Experiment(experiment_id=key, fixture_id='fifo_correct', objective_ids=('FIFO_BEHAVIOR',),
            budget={'max_cycles': 8, 'wall_time_seconds': 2}, operations=({'op': 'reset'},))
        parent = service.create_campaign(session, 'fifo_correct', experiment.model_dump(mode='json'), key, completion_policy='sequential')
        service.start(session, parent.id)
        attempt = session.scalar(sa.select(Attempt).where(Attempt.campaign_id == parent.id))
        token, _, _ = service.claim(session, attempt.id)
        record = resolve_attempt_experiment(session, attempt)
        service.finalize(session, attempt.id, token, dict(attempt_id='synthetic-checker', experiment_id=record.wire_id,
            execution_status='completed', verdict='pass_within_scope', identity={**record.identity, 'environment_hash': 'b' * 64}, usage={'cycles': 1}))
        rounds.attach_planner(session, parent.id, limits=InvestigationLimits(max_total_cycles=80, max_wall_seconds=60,
            max_cycles_per_experiment=8, max_execution_seconds=2, max_model_seconds=2), requested_model='fake-runtime', strategy='meta', at=AT)
        return rounds.advance(session, parent.id, at=AT).id


def claim(db, round_id, *, at=AT, incarnation=None, role=ROLE, control='RUNNING'):
    with db[1].begin() as session:
        return resources.claim_provider_slot(session, round_id, incarnation=incarnation or resources.new_incarnation(), role=role, at=at, control=control)


def test_cross_campaign_race_grants_one_and_preserves_losing_round(db):
    ids = [queued(db, key) for key in ('first', 'second')]
    barrier = Barrier(2)
    def compete(round_id):
        barrier.wait()
        return claim(db, round_id)
    with ThreadPoolExecutor(max_workers=2) as pool: grants = list(pool.map(compete, ids))
    assert sum(grant is not None for grant in grants) == 1
    winner = next(grant for grant in grants if grant)
    with db[1].begin() as session:
        assert resources.validate_provider_slot(session, winner, role=ROLE, at=AT, control='RUNNING')
        assert session.scalar(sa.select(sa.func.count()).select_from(PlannerCleanup)) == 1
        loser = next(round_id for round_id in ids if round_id != winner.lease.round_id)
        assert session.get(PlannerRound, loser).status == 'queued'
        assert resources.safe_snapshot(session).fence == 1


def test_partial_claim_failure_rolls_back_even_when_outer_transaction_commits(db, monkeypatch):
    round_id = queued(db, 'partial')
    monkeypatch.setattr(cleanup, 'record_pending', lambda *args, **kwargs: None)
    assert claim(db, round_id) is None
    with db[1]() as session:
        assert session.get(PlannerRound, round_id).status == 'queued'
        assert session.get(PlannerModelCall, round_id).started_at is None
        assert session.get(PlannerState, session.get(PlannerRound, round_id).campaign_id).phase == 'round_queued'
        assert resources.safe_snapshot(session).fence == 0
        assert session.get(PlannerCleanup, round_id) is None


def test_legacy_pending_and_unknown_cleanup_blocks_without_ttl(db):
    legacy, next_round = queued(db, 'legacy'), queued(db, 'next')
    with db[1].begin() as session:
        lease = rounds.claim_model_round(session, legacy, at=AT)
        ticket = cleanup.record_pending(session, lease, at=AT)
    assert claim(db, next_round) is None
    with db[1].begin() as session: cleanup.mark_owner_lost(session, legacy, at=AT + timedelta(days=1))
    assert claim(db, next_round, at=AT + timedelta(days=10)) is None
    with db[1].begin() as session:
        assert not resources.admission_allowed(session, role=ROLE, at=AT + timedelta(days=10), control='RUNNING')
        assert resources.safe_snapshot(session).state == 'idle'
        cleanup.acknowledge_settled(session, ticket, proof='cooperative_tasks_settled', at=AT + timedelta(days=11))
    # The stale campaign deadline still prevents acquisition after cleanup settles.
    assert claim(db, next_round, at=AT + timedelta(days=12)) is None


def test_exact_own_pending_allowed_but_unrelated_cleanup_revokes_validation(db):
    first, other = queued(db, 'own'), queued(db, 'other')
    grant = claim(db, first)
    with db[1].begin() as session:
        assert resources.validate_provider_slot(session, grant, role=ROLE, at=AT, control='RUNNING')
    with db[1].begin() as session:
        legacy = rounds.claim_model_round(session, other, at=AT)
        cleanup.record_pending(session, legacy, at=AT)
    with db[1].begin() as session:
        assert not resources.validate_provider_slot(session, grant, role=ROLE, at=AT, control='RUNNING')


def test_owner_loss_fences_old_grant_late_ack_then_fresh_owner(db):
    first, second = queued(db, 'lost'), queued(db, 'fresh')
    grant = claim(db, first)
    with db[1].begin() as session:
        assert resources.mark_owner_lost(session, grant, role=ROLE, at=AT + timedelta(milliseconds=100))
        snap = resources.safe_snapshot(session)
        assert snap.fence == 2 and snap.state == 'unknown'
        assert session.get(PlannerCleanup, first).local_state == 'unknown'
        assert session.get(PlannerModelCall, first).outcome == 'remote_outcome_unknown'
    with db[1].begin() as session:
        assert not resources.validate_provider_slot(session, grant, role=ROLE, at=AT + timedelta(milliseconds=200), control='RUNNING')
        assert not resources.release_settled_slot(session, expected_fence=2, expected_round_id=first, role=ROLE, at=AT + timedelta(days=10))
    assert claim(db, second) is None
    with db[1].begin() as session:
        assert cleanup.acknowledge_settled(session, grant.cleanup_ticket, proof='cooperative_tasks_settled', at=AT + timedelta(milliseconds=200))
    with db[1].begin() as session:
        assert resources.release_settled_slot(session, expected_fence=2, expected_round_id=first, role=ROLE, at=AT + timedelta(milliseconds=300))
    new = claim(db, second, at=AT + timedelta(milliseconds=400))
    assert new.fence == 4
    with db[1].begin() as session:
        assert not resources.mark_owner_lost(session, grant, role=ROLE, at=AT + timedelta(milliseconds=500))
        assert not resources.release_settled_slot(session, expected_fence=grant.fence, expected_round_id=first, role=ROLE, at=AT + timedelta(milliseconds=500))
        assert resources.validate_provider_slot(session, new, role=ROLE, at=AT + timedelta(milliseconds=500), control='RUNNING')


@pytest.mark.parametrize('kind', ['role', 'control', 'deadline', 'cancelled', 'generation'])
def test_role_control_deadline_and_current_association(db, kind):
    round_id = queued(db, kind)
    if kind == 'cancelled':
        with db[1].begin() as session: rounds.cancel_investigation(session, session.get(PlannerRound, round_id).campaign_id, at=AT)
    if kind == 'generation':
        with db[1].begin() as session: session.get(PlannerState, session.get(PlannerRound, round_id).campaign_id).generation += 1
    assert claim(db, round_id, role='rtl_worker' if kind == 'role' else ROLE,
        control='PAUSED' if kind == 'control' else 'RUNNING', at=AT + timedelta(seconds=61) if kind == 'deadline' else AT) is None
    with db[1]() as session: assert resources.safe_snapshot(session).fence == 0


def test_cancelled_parent_keeps_exact_cleanup_ack_without_execution_authority(db):
    grant = claim(db, queued(db, 'cancel'))
    with db[1].begin() as session: rounds.cancel_investigation(session, grant.lease.campaign_id, at=AT)
    with db[1].begin() as session:
        cleanup.acknowledge_settled(session, grant.cleanup_ticket, proof='cooperative_tasks_settled', at=AT)
    with db[1].begin() as session:
        assert not resources.validate_provider_slot(session, grant, role=ROLE, at=AT, control='RUNNING')
        assert resources.release_settled_slot(session, expected_fence=grant.fence, expected_round_id=grant.lease.round_id, role=ROLE, at=AT)
        assert session.get(PlannerState, grant.lease.campaign_id).phase == 'cancelled'


def test_safe_snapshot_omits_incarnation_and_owner_credentials(db):
    grant = claim(db, queued(db, 'safe'))
    with db[1]() as session:
        snapshot = asdict(resources.safe_snapshot(session))
        assert not {'incarnation', 'owner_token', 'execution_lease', 'lease', 'cleanup_ticket'} & snapshot.keys()
        assert grant.incarnation not in repr(snapshot) and grant.cleanup_ticket.owner_token not in repr(grant)


def test_outer_rollback_and_same_incarnation_cannot_reuse_provisional_cleanup_fence(db):
    round_id = queued(db, 'rollback')
    incarnation = resources.new_incarnation()
    with pytest.raises(RuntimeError):
        with db[1].begin() as session:
            provisional = resources.claim_provider_slot(session, round_id, incarnation=incarnation, role=ROLE, at=AT, control='RUNNING')
            raise RuntimeError('Caller did not commit; no IO permitted')
    fresh = claim(db, round_id, incarnation=incarnation)
    assert provisional.fence == fresh.fence == 1
    assert provisional.cleanup_ticket.owner_token != fresh.cleanup_ticket.owner_token
    with db[1].begin() as session:
        assert not resources.validate_provider_slot(session, provisional, role=ROLE, at=AT, control='RUNNING')
        assert resources.validate_provider_slot(session, fresh, role=ROLE, at=AT, control='RUNNING')


def test_ownerloss_after_committed_proposal_preserves_known_outcome_and_reservations(db):
    grant = claim(db, queued(db, 'known'))
    with db[1].begin() as session:
        proposal = ProviderCompletion(model='fake-runtime', content='{"proposal":{"kind":"experiment","objective_ids":["FIFO_BEHAVIOR"],"seed":1,"operations":[{"op":"reset"}]}}', usage={'input_tokens': 1, 'output_tokens': 1}, finish_reason='stop')
        assert rounds.finish_model_round(session, grant.lease, proposal, at=AT + timedelta(milliseconds=100))
        known = session.get(PlannerModelCall, grant.lease.round_id).outcome
        reserved = rounds.totals(session, grant.lease.campaign_id)
    with db[1].begin() as session:
        assert resources.mark_owner_lost(session, grant, role=ROLE, at=AT + timedelta(milliseconds=200))
        assert session.get(PlannerModelCall, grant.lease.round_id).outcome == known
        assert session.get(PlannerRound, grant.lease.round_id).status == 'accepted'
        assert rounds.totals(session, grant.lease.campaign_id) == reserved
        assert not resources.admission_allowed(session, role=ROLE, at=AT + timedelta(milliseconds=200), control='RUNNING')


@pytest.mark.parametrize('field', ['fence_bool', 'fence_float', 'lease_generation', 'cleanup_generation', 'incarnation'])
def test_grant_fence_and_identity_cannot_use_numeric_aliases_or_substitute_owner(db, field):
    grant = claim(db, queued(db, field))
    if field == 'fence_bool': bad = replace(grant, fence=True)
    elif field == 'fence_float': bad = replace(grant, fence=1.0)
    elif field == 'lease_generation': bad = replace(grant, lease=replace(grant.lease, generation=True))
    elif field == 'cleanup_generation': bad = replace(grant, cleanup_ticket=replace(grant.cleanup_ticket, generation=True))
    else: bad = replace(grant, incarnation=resources.new_incarnation())
    with db[1].begin() as session:
        assert not resources.validate_provider_slot(session, bad, role=ROLE, at=AT, control='RUNNING')
        assert not resources.mark_owner_lost(session, bad, role=ROLE, at=AT)


def test_initial_provisioning_missing_fails_closed_under_concurrent_calls(db):
    round_id = queued(db, 'unprovisioned')
    # Simulate an incomplete isolated provisioning state. Production never lazily
    # seeds or resets the resource; the reviewed migration owns its one row.
    with db[0].begin() as connection:
        connection.execute(sa.text('DROP TRIGGER runtime_slot_no_delete'))
        connection.execute(sa.text('DELETE FROM planner_runtime_slots'))
    def attempt(_):
        try: claim(db, round_id)
        except service.Conflict: return 'blocked'
        return 'unexpected grant'
    with ThreadPoolExecutor(max_workers=2) as pool: assert list(pool.map(attempt, range(2))) == ['blocked', 'blocked']
    with db[1]() as session:
        assert session.get(PlannerRound, round_id).status == 'queued'
        assert session.scalar(sa.select(sa.func.count()).select_from(PlannerCleanup)) == 0


def test_seal_before_finalization_permits_completion_not_new_io(db):
    grant = claim(db, queued(db, 'sealed'))
    with db[1].begin() as session:
        cleanup.acknowledge_settled(session, grant.cleanup_ticket, proof='cooperative_tasks_settled', at=AT)
    with db[1].begin() as session:
        assert not resources.validate_provider_slot(session, grant, role=ROLE, at=AT, control='RUNNING')
        assert resources.validate_provider_slot(session, grant, role=ROLE, at=AT, control='RUNNING', phase='running')
        assert resources.validate_provider_slot(session, grant, role=ROLE, at=AT, control='RUNNING', phase='completion')
        assert rounds.fail_model_round(session, grant.lease, code='model_response_invalid', at=AT)
    with db[1].begin() as session:
        assert resources.release_settled_slot(session, expected_fence=grant.fence, expected_round_id=grant.lease.round_id, role=ROLE, at=AT)


@pytest.mark.parametrize('phase', ['pre_io', 'running', 'completion'])
def test_owner_lost_fence_rejects_all_phases_even_after_exact_late_ack(db, phase):
    grant = claim(db, queued(db, phase))
    with db[1].begin() as session: resources.mark_owner_lost(session, grant, role=ROLE, at=AT)
    with db[1].begin() as session: cleanup.acknowledge_settled(session, grant.cleanup_ticket, proof='cooperative_tasks_settled', at=AT)
    with db[1].begin() as session:
        assert not resources.validate_provider_slot(session, grant, role=ROLE, at=AT, control='RUNNING', phase=phase)


@pytest.mark.parametrize('wrong', ['grant', 'role'])
def test_pause_does_not_mutate_round_from_wrong_authority(db, wrong):
    grant = claim(db, queued(db, 'wrong-pause'))
    with db[1].begin() as session:
        assert not resources.validate_provider_slot(session, replace(grant, fence=999) if wrong == 'grant' else grant,
            role=ROLE if wrong == 'grant' else 'rtl_worker', at=AT, control='PAUSED')
    with db[1]() as session:
        assert session.get(PlannerRound, grant.lease.round_id).status == 'running'
        assert session.get(PlannerState, grant.lease.campaign_id).phase == 'model_running'


def test_exact_pause_revokes_round_before_foreign_cleanup_gate(db):
    first, other = queued(db, 'paused'), queued(db, 'foreign-pause')
    grant = claim(db, first)
    with db[1].begin() as session:
        lease = rounds.claim_model_round(session, other, at=AT)
        cleanup.record_pending(session, lease, at=AT)
    with db[1].begin() as session:
        assert not resources.validate_provider_slot(session, grant, role=ROLE, at=AT, control='PAUSED')
    with db[1]() as session:
        assert session.get(PlannerState, grant.lease.campaign_id).stop_reason == 'paused'
        assert session.get(PlannerRound, first).status == 'uncertain'
        assert session.get(PlannerModelCall, first).outcome == 'remote_outcome_unknown'
        assert session.get(PlannerCleanup, first).local_state == 'pending'
        assert resources.safe_snapshot(session).state == 'active'


def test_validation_rejects_unknown_phase_and_exact_own_unknown_cleanup(db):
    grant = claim(db, queued(db, 'unknown'))
    with db[1].begin() as session:
        with pytest.raises(ValueError, match='phase'):
            resources.validate_provider_slot(session, grant, role=ROLE, at=AT, control='RUNNING', phase='retry')
    with db[1].begin() as session: cleanup.mark_owner_lost(session, grant.lease.round_id, at=AT)
    with db[1].begin() as session:
        for phase in ('pre_io', 'running', 'completion'):
            assert not resources.validate_provider_slot(session, grant, role=ROLE, at=AT, control='RUNNING', phase=phase)


@pytest.mark.parametrize('cleanup_state,phase,expected', [('settled', 'pre_io', False),
    ('settled', 'completion', True), ('unknown', 'pre_io', False), ('unknown', 'completion', False)])
def test_validation_reads_cleanup_after_campaign_lock_handoff(db, monkeypatch, cleanup_state, phase, expected):
    grant = claim(db, queued(db, 'handoff'))
    original_lock, changed = service.lock, []
    def handoff(session, campaign_id):
        campaign = original_lock(session, campaign_id)
        if not changed:
            changed.append(True)
            # Deterministic authority handoff in isolated SQLite, not an
            # executed PostgreSQL callback schedule or concurrency claim.
            if cleanup_state == 'settled':
                cleanup.acknowledge_settled(session, grant.cleanup_ticket, proof='cooperative_tasks_settled', at=AT)
            else:
                cleanup.mark_owner_lost(session, grant.lease.round_id, at=AT)
        return campaign
    monkeypatch.setattr(service, 'lock', handoff)
    with db[1].begin() as session:
        assert resources.validate_provider_slot(session, grant, role=ROLE, at=AT, control='RUNNING', phase=phase) is expected
        assert changed and session.get(PlannerCleanup, grant.lease.round_id).local_state == cleanup_state


def test_sql_fence_cannot_reset_delete_or_release_without_proof(db):
    grant = claim(db, queued(db, 'sql'))
    for sql in ('DELETE FROM planner_runtime_slots', 'UPDATE planner_runtime_slots SET fence=0',
        "UPDATE planner_runtime_slots SET incarnation='replacement'",
        "UPDATE planner_runtime_slots SET fence=fence+1,state='idle',incarnation=NULL,active_round_id=NULL,campaign_id=NULL,acquired_at=NULL,owner_lost_at=NULL"):
        with pytest.raises(IntegrityError):
            with db[0].begin() as connection: connection.execute(sa.text(sql))
    config = Config('alembic.ini')
    with pytest.raises(ValueError, match='retained runtime'):
        with db[0].begin() as connection:
            config.attributes['connection'] = connection
            command.downgrade(config, '11u9investigations')


def test_postgres_ddl_and_authority_row_lock_are_explicit(monkeypatch):
    spec = importlib.util.spec_from_file_location('runtime_migration', Path('migrations/versions/12u2resources_runtime_resource_fence.py'))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    metadata = sa.MetaData()
    for name in ('planner_states', 'planner_rounds', 'planner_model_calls', 'planner_cleanup', 'experiments'):
        Base.metadata.tables[name].to_metadata(metadata)
    tables, statements, seed = [], [], []
    def create(name, *args):
        table = sa.Table(name, metadata, *args)
        tables.append(table)
        return table
    monkeypatch.setattr(module, 'op', SimpleNamespace(create_table=create, bulk_insert=lambda table, rows: seed.extend(rows),
        get_bind=lambda: SimpleNamespace(dialect=SimpleNamespace(name='postgresql')), execute=lambda sql: statements.append(str(sql))))
    module.upgrade()
    normalize = lambda ddl: {line.strip().rstrip(',') for line in ddl.splitlines()}
    assert normalize(str(CreateTable(tables[0]).compile(dialect=postgresql.dialect()))) == normalize(str(CreateTable(PlannerRuntimeSlot.__table__).compile(dialect=postgresql.dialect())))
    assert seed[0]['resource'] == resources.RESOURCE and seed[0]['fence'] == 0
    assert any('NEW.fence = OLD.fence + 1' in sql and "local_state = 'settled'" in sql for sql in statements)
    calls = []
    fake = SimpleNamespace(get_bind=lambda: SimpleNamespace(dialect=SimpleNamespace(name='postgresql')),
        scalar=lambda query: calls.append(str(query.compile(dialect=postgresql.dialect()))) or object())
    resources._lock(fake)
    assert 'FOR UPDATE' in calls[0]
