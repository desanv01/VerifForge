"""Supervisor quarantine of a lost process: synthetic SQLite state only."""
from dataclasses import asdict
from datetime import datetime, timedelta, timezone

from alembic import command
from alembic.config import Config
import pytest
import sqlalchemy as sa

from veriforge.contracts import Experiment
from veriforge.core import planner_cleanup as cleanup, planner_resources as resources, planner_rounds as rounds, service
from veriforge.core.catalog import seed_catalog
from veriforge.core.database import Attempt, PlannerCleanup, PlannerModelCall, PlannerRound, PlannerState, make_engine, session_factory
from veriforge.core.planner_experiments import resolve_attempt_experiment
from veriforge.planner.models import InvestigationLimits, ProviderCompletion

AT = datetime(2026, 10, 30, tzinfo=timezone.utc)


def queued(sessions, key):
    with sessions.begin() as session:
        plan = Experiment(experiment_id=key, fixture_id='fifo_correct', objective_ids=('FIFO_BEHAVIOR',),
            budget={'max_cycles': 8, 'wall_time_seconds': 2}, operations=({'op': 'reset'},))
        parent = service.create_campaign(session, 'fifo_correct', plan.model_dump(mode='json'), key, completion_policy='sequential')
        service.start(session, parent.id)
        attempt = session.scalar(sa.select(Attempt).where(Attempt.campaign_id == parent.id))
        lease, _, _ = service.claim(session, attempt.id)
        record = resolve_attempt_experiment(session, attempt)
        service.finalize(session, attempt.id, lease, dict(attempt_id='synthetic-owner-loss-seed', experiment_id=record.wire_id,
            execution_status='completed', verdict='pass_within_scope', identity={**record.identity, 'environment_hash': 'b' * 64}, usage={'cycles': 1}))
        rounds.attach_planner(session, parent.id, limits=InvestigationLimits(max_total_cycles=80, max_wall_seconds=60,
            max_cycles_per_experiment=8, max_execution_seconds=2, max_model_seconds=2), requested_model='fake-owner-loss-model', strategy='meta', at=AT)
        return rounds.advance(session, parent.id, at=AT).id


@pytest.fixture
def db(tmp_path):
    engine = make_engine('sqlite:///' + (tmp_path / 'owner.db').as_posix())
    config = Config('alembic.ini')
    with engine.begin() as connection:
        config.attributes['connection'] = connection
        command.upgrade(config, 'head')
    sessions = session_factory(engine)
    with sessions.begin() as session: seed_catalog(session)
    round_id = queued(sessions, 'lost-owner')
    with sessions.begin() as session:
        grant = resources.claim_provider_slot(session, round_id, incarnation=resources.new_incarnation(), role=resources.ROLE, at=AT, control='RUNNING')
    yield engine, sessions, grant
    engine.dispose()


def observe(db, **changes):
    grant = db[2]
    values = dict(expected_fence=grant.fence, expected_round_id=grant.lease.round_id,
        expected_incarnation=grant.incarnation, owner_loss_observed=True, role=resources.SUPERVISOR_ROLE,
        at=AT + timedelta(milliseconds=100), control='RUNNING')
    values.update(changes)
    with db[1].begin() as session:
        return resources.quarantine_lost_owner(session, **values)


def test_exact_supervisor_observation_quarantines_only_and_preserves_charges(db):
    grant = db[2]
    with db[1]() as session: before = rounds.totals(session, grant.lease.campaign_id)
    assert observe(db) is True
    with db[1].begin() as session:
        snapshot = resources.safe_snapshot(session)
        assert snapshot.state == 'unknown' and snapshot.fence == grant.fence + 1
        assert session.get(PlannerCleanup, grant.lease.round_id).local_state == 'unknown'
        assert session.get(PlannerRound, grant.lease.round_id).status == 'uncertain'
        assert session.get(PlannerModelCall, grant.lease.round_id).outcome == 'remote_outcome_unknown'
        assert rounds.totals(session, grant.lease.campaign_id) == before
        assert not resources.admission_allowed(session, role=resources.ROLE, at=AT + timedelta(days=10), control='RUNNING')
        assert grant.incarnation not in repr(asdict(snapshot))
        assert grant.cleanup_ticket.owner_token not in repr(snapshot)
    assert observe(db) is False


@pytest.mark.parametrize('case', ['fence', 'round', 'incarnation', 'role', 'no_observation'])
def test_wrong_or_unobserved_owner_cas_does_not_touch_current_owner(db, case):
    change = {'fence': {'expected_fence': db[2].fence + 1}, 'round': {'expected_round_id': resources.new_incarnation()},
        'incarnation': {'expected_incarnation': resources.new_incarnation()}, 'role': {'role': resources.ROLE},
        'no_observation': {'owner_loss_observed': False, 'at': AT + timedelta(days=10)}}[case]
    assert observe(db, **change) is False
    with db[1]() as session:
        assert resources.safe_snapshot(session).state == 'active'
        assert resources.safe_snapshot(session).fence == db[2].fence
        assert session.get(PlannerCleanup, db[2].lease.round_id).local_state == 'pending'
        assert session.get(PlannerRound, db[2].lease.round_id).status == 'running'


@pytest.mark.parametrize('change', [{'expected_fence': True}, {'expected_fence': 1.0}, {'expected_fence': -1},
    {'owner_loss_observed': 1}, {'at': AT.replace(tzinfo=None)}, {'at': AT - timedelta(seconds=1)}])
def test_malformed_observation_and_backwards_time_do_not_mutate(db, change):
    with pytest.raises(ValueError): observe(db, **change)
    with db[1]() as session:
        assert resources.safe_snapshot(session).fence == db[2].fence
        assert session.get(PlannerCleanup, db[2].lease.round_id).local_state == 'pending'


def test_paused_control_quarantines_but_never_takes_over_or_settles(db):
    assert observe(db, control='PAUSED') is True
    other = queued(db[1], 'paused-next')
    with db[1].begin() as session:
        assert resources.claim_provider_slot(session, other, incarnation=resources.new_incarnation(), role=resources.ROLE,
            at=AT + timedelta(milliseconds=200), control='PAUSED') is None
        assert resources.claim_provider_slot(session, other, incarnation=resources.new_incarnation(), role=resources.ROLE,
            at=AT + timedelta(milliseconds=200), control='RUNNING') is None
        assert session.get(PlannerCleanup, db[2].lease.round_id).local_state == 'unknown'


def test_known_committed_completion_remains_known_after_supervisor_quarantine(db):
    grant = db[2]
    with db[1].begin() as session:
        completion = ProviderCompletion(model='fake-owner-loss-model', usage={'input_tokens': 1, 'output_tokens': 1}, finish_reason='stop',
            content='{"proposal":{"kind":"experiment","objective_ids":["FIFO_BEHAVIOR"],"seed":2,"operations":[{"op":"reset"}]}}')
        assert rounds.finish_model_round(session, grant.lease, completion, at=AT + timedelta(milliseconds=50))
        known = session.get(PlannerModelCall, grant.lease.round_id).outcome
        before = rounds.totals(session, grant.lease.campaign_id)
    assert observe(db) is True
    with db[1]() as session:
        assert session.get(PlannerModelCall, grant.lease.round_id).outcome == known
        assert session.get(PlannerRound, grant.lease.round_id).status == 'accepted'
        assert rounds.totals(session, grant.lease.campaign_id) == before
        assert session.get(PlannerCleanup, grant.lease.round_id).local_state == 'unknown'


def test_original_owner_late_ack_and_separate_release_never_restores_old_fence(db):
    grant = db[2]
    assert observe(db)
    with db[1].begin() as session:
        assert cleanup.acknowledge_settled(session, grant.cleanup_ticket, proof='cooperative_tasks_settled', at=AT + timedelta(milliseconds=200))
    with db[1].begin() as session:
        snapshot = resources.safe_snapshot(session)
        assert not resources.validate_provider_slot(session, grant, role=resources.ROLE, at=AT + timedelta(milliseconds=200), control='RUNNING', phase='completion')
        assert not resources.release_settled_slot(session, expected_fence=grant.fence, expected_round_id=grant.lease.round_id, role=resources.ROLE, at=AT + timedelta(milliseconds=200))
        assert resources.release_settled_slot(session, expected_fence=snapshot.fence, expected_round_id=grant.lease.round_id, role=resources.ROLE, at=AT + timedelta(milliseconds=200))
    other = queued(db[1], 'new-owner')
    with db[1].begin() as session:
        fresh = resources.claim_provider_slot(session, other, incarnation=resources.new_incarnation(), role=resources.ROLE, at=AT + timedelta(milliseconds=300), control='RUNNING')
        assert fresh and fresh.fence > snapshot.fence
    assert not observe(db, at=AT + timedelta(milliseconds=400))
    with db[1].begin() as session:
        assert resources.validate_provider_slot(session, fresh, role=resources.ROLE, at=AT + timedelta(milliseconds=400), control='RUNNING')


@pytest.mark.parametrize('field', ['generation', 'request_hash', 'execution_lease'])
def test_round_quarantine_rejects_wrong_persisted_identity_before_pause_mutation(db, field):
    grant = db[2]
    values = dict(campaign_id=grant.lease.campaign_id, generation=grant.lease.generation,
        request_hash=grant.lease.request_hash, execution_lease=grant.lease.lease, at=AT, control='PAUSED')
    values[field] = grant.lease.generation + 1 if field == 'generation' else 'different-persisted-value'
    with db[1].begin() as session:
        resources.lock_resource(session)
        assert not rounds.quarantine_model_round(session, grant.lease.round_id, **values)
    with db[1]() as session:
        assert session.get(PlannerRound, grant.lease.round_id).status == 'running'
        assert session.get(PlannerState, grant.lease.campaign_id).phase == 'model_running'


@pytest.mark.parametrize('settlement', ['cancelled', 'known_failure'])
def test_prior_terminal_outcomes_are_not_replaced_by_owner_loss(db, settlement):
    grant = db[2]
    with db[1].begin() as session:
        if settlement == 'cancelled': rounds.cancel_investigation(session, grant.lease.campaign_id, at=AT)
        else: rounds.fail_model_round(session, grant.lease, code='model_response_invalid', at=AT)
        old = (session.get(PlannerRound, grant.lease.round_id).status,
            session.get(PlannerModelCall, grant.lease.round_id).outcome,
            session.get(PlannerState, grant.lease.campaign_id).phase)
    assert observe(db)
    with db[1]() as session:
        assert (session.get(PlannerRound, grant.lease.round_id).status,
            session.get(PlannerModelCall, grant.lease.round_id).outcome,
            session.get(PlannerState, grant.lease.campaign_id).phase) == old


def test_quarantine_partial_failure_rolls_back_without_exposing_grants(db, monkeypatch):
    def forbidden_grant(*args, **kwargs): pytest.fail('Supervisor must not reconstruct original owner grant/ticket')
    monkeypatch.setattr(resources, 'ProviderGrant', forbidden_grant)
    monkeypatch.setattr(cleanup, 'CleanupTicket', forbidden_grant)
    original = rounds.quarantine_model_round
    def interrupted(*args, **kwargs): raise RuntimeError('Synthetic interruption after cleanup update')
    monkeypatch.setattr(rounds, 'quarantine_model_round', interrupted)
    grant = db[2]
    with db[1].begin() as session:
        with pytest.raises(RuntimeError):
            resources.quarantine_lost_owner(session, expected_fence=grant.fence, expected_round_id=grant.lease.round_id,
                expected_incarnation=grant.incarnation, owner_loss_observed=True, role=resources.SUPERVISOR_ROLE,
                at=AT, control='RUNNING')
    with db[1]() as session:
        assert resources.safe_snapshot(session).fence == grant.fence
        assert session.get(PlannerCleanup, grant.lease.round_id).local_state == 'pending'
    monkeypatch.setattr(rounds, 'quarantine_model_round', original)
    assert observe(db, at=AT) is True
