"""Independent supervisor review: disposable SQLite, synthetic seed only.

No provider, broker, process-death detector or PostgreSQL execution is involved.
"""
from concurrent.futures import ThreadPoolExecutor
from dataclasses import asdict
from datetime import timedelta
from pathlib import Path
import runpy
from threading import Barrier

from alembic import command
from alembic.config import Config
import pytest

from veriforge.core import planner_cleanup as cleanup, planner_resources as resources, planner_rounds as rounds, service
from veriforge.core.catalog import seed_catalog
from veriforge.core.database import PlannerCleanup, PlannerModelCall, PlannerRound, PlannerRuntimeSlot, make_engine, session_factory
from veriforge.planner.models import ProviderCompletion

ROOT = Path(__file__).resolve().parents[2]
BUILDERS = runpy.run_path(str(ROOT / 'tests/core/test_planner_resources.py'))
AT, queued, claim = (BUILDERS[name] for name in ('AT', 'queued', 'claim'))
LATER = AT + timedelta(milliseconds=200)


@pytest.fixture
def owned(tmp_path):
    engine = make_engine('sqlite:///' + (tmp_path / 'supervisor-review.db').as_posix())
    config = Config(str(ROOT / 'alembic.ini'))
    with engine.begin() as connection:
        config.attributes['connection'] = connection
        command.upgrade(config, 'head')  # Disposable database, never operational.
    sessions = session_factory(engine)
    try:
        with sessions.begin() as session:
            seed_catalog(session)
        db = (engine, sessions)
        grant = claim(db, queued(db, 'independent-supervisor'))
        assert grant is not None
        yield sessions, grant
    finally:
        engine.dispose()


def observe(session, grant, **changes):
    values = dict(expected_fence=grant.fence, expected_round_id=grant.lease.round_id,
        expected_incarnation=grant.incarnation, owner_loss_observed=True,
        role=resources.SUPERVISOR_ROLE, at=LATER, control='RUNNING')
    return resources.quarantine_lost_owner(session, **(values | changes))


def state(session, grant):
    row = session.get(PlannerRound, grant.lease.round_id)
    owner = session.get(PlannerCleanup, grant.lease.round_id)
    return (asdict(resources.safe_snapshot(session)), row.status, row.lease,
        session.get(PlannerModelCall, row.id).outcome,
        owner.local_state, owner.settled_at, owner.settlement_proof,
        rounds.totals(session, grant.lease.campaign_id))


@pytest.mark.parametrize('changes', [
    {'expected_fence': True}, {'expected_fence': 1.0}, {'expected_fence': 0},
    {'owner_loss_observed': 1}, {'owner_loss_observed': None},
    {'expected_round_id': 'private-round-' * 4},
    {'expected_incarnation': 'private-incarnation'}, {'control': 'private-control'},
    {'at': AT.replace(tzinfo=None)}, {'at': AT - timedelta(microseconds=1)},
])
def test_rejected_inputs_leave_no_partial_state_or_credential_echo(owned, changes):
    sessions, grant = owned
    with sessions() as session:
        before = state(session, grant)
    with sessions.begin() as session:
        with pytest.raises(ValueError) as error:
            observe(session, grant, **changes)
        # Commit after rejection deliberately: no partial update may escape.
    assert grant.incarnation not in str(error.value)
    assert grant.cleanup_ticket.owner_token not in str(error.value)
    assert 'private-' not in str(error.value)
    with sessions() as session:
        assert state(session, grant) == before


@pytest.mark.parametrize('changes', [
    {'owner_loss_observed': False, 'at': AT + timedelta(days=365)},
    {'role': resources.ROLE}, {'expected_fence': 2},
    {'expected_incarnation': '00000000-0000-0000-0000-000000000000'},
    {'expected_round_id': 'different-round'},
])
def test_unobserved_or_stale_cas_cannot_mutate(owned, changes):
    sessions, grant = owned
    with sessions() as session:
        before = state(session, grant)
    with sessions.begin() as session:
        assert observe(session, grant, **changes) is False
    with sessions() as session:
        assert state(session, grant) == before


def test_global_lock_precedes_every_campaign_lock_and_no_owner_objects_created(owned, monkeypatch):
    sessions, grant = owned
    events = []
    global_lock, campaign_lock = resources._lock, service.lock
    def take_global(*args, **kwargs):
        result = global_lock(*args, **kwargs)
        events.append('global')
        return result
    def take_campaign(*args, **kwargs):
        assert events and events[0] == 'global'
        events.append('campaign')
        return campaign_lock(*args, **kwargs)
    def forbidden(*args, **kwargs):
        pytest.fail('Quarantine fabricated owner authority or settlement/release')
    monkeypatch.setattr(resources, '_lock', take_global)
    monkeypatch.setattr(service, 'lock', take_campaign)
    monkeypatch.setattr(resources, 'ProviderGrant', forbidden)
    monkeypatch.setattr(rounds, 'ModelLease', forbidden)
    monkeypatch.setattr(cleanup, 'CleanupTicket', forbidden)
    monkeypatch.setattr(cleanup, 'acknowledge_settled', forbidden)
    monkeypatch.setattr(resources, 'release_settled_slot', forbidden)
    with sessions.begin() as session:
        before = rounds.totals(session, grant.lease.campaign_id)
        assert observe(session, grant)
        snapshot = resources.safe_snapshot(session)
        assert snapshot.state == 'unknown' and snapshot.fence == grant.fence + 1
        assert snapshot.unresolved_cleanup == 1
        assert rounds.totals(session, grant.lease.campaign_id) == before
        assert grant.incarnation not in repr(asdict(snapshot))
        assert grant.cleanup_ticket.owner_token not in repr(snapshot)
    assert events[0] == 'global' and 'campaign' in events


def test_two_supervisors_same_cas_have_exactly_one_winner(owned):
    sessions, grant = owned
    barrier = Barrier(2)
    def compete(_):
        barrier.wait(timeout=10)
        with sessions.begin() as session:
            return observe(session, grant)
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(compete, range(2)))
    assert sorted(results) == [False, True]
    with sessions() as session:
        assert resources.safe_snapshot(session).fence == grant.fence + 1
        assert session.get(PlannerCleanup, grant.lease.round_id).local_state == 'unknown'


def test_original_ack_racing_quarantine_preserves_proof_and_requires_separate_release(owned):
    sessions, grant = owned
    barrier = Barrier(2)
    def compete(operation):
        barrier.wait(timeout=10)
        with sessions.begin() as session:
            if operation == 'ack':
                return cleanup.acknowledge_settled(session, grant.cleanup_ticket,
                    proof='cooperative_tasks_settled', at=LATER)
            return observe(session, grant)
    with ThreadPoolExecutor(max_workers=2) as pool:
        assert list(pool.map(compete, ('ack', 'quarantine'))) == [True, True]
    with sessions.begin() as session:
        snapshot = resources.safe_snapshot(session)
        owner = session.get(PlannerCleanup, grant.lease.round_id)
        assert owner.local_state == 'settled'
        assert owner.settlement_proof == 'cooperative_tasks_settled'
        assert snapshot.state == 'unknown' and snapshot.fence == grant.fence + 1
        assert not resources.validate_provider_slot(session, grant, role=resources.ROLE, at=LATER, control='RUNNING')
        assert not resources.release_settled_slot(session, expected_fence=grant.fence,
            expected_round_id=grant.lease.round_id, role=resources.ROLE, at=LATER)
    with sessions.begin() as session:
        assert resources.release_settled_slot(session, expected_fence=snapshot.fence,
            expected_round_id=grant.lease.round_id, role=resources.ROLE, at=LATER)
        assert resources.safe_snapshot(session).state == 'idle'


def test_stale_identity_map_refreshes_before_cas(owned):
    sessions, grant = owned
    with sessions() as stale:
        cached = stale.get(PlannerRuntimeSlot, resources.RESOURCE)
        assert cached.state == 'active'
        stale.commit()  # Retain identity map without retaining an SQLite read txn.
        with sessions.begin() as session:
            assert observe(session, grant)
        assert not observe(stale, grant)
        stale.commit()
        assert cached.state == 'unknown' and cached.fence == grant.fence + 1


@pytest.mark.parametrize('control', ['RUNNING', 'PAUSED', 'STOPPED'])
def test_settled_proof_is_never_downgraded_and_backdated_observation_rejected(owned, control):
    sessions, grant = owned
    with sessions.begin() as session:
        assert cleanup.acknowledge_settled(session, grant.cleanup_ticket,
            proof='cooperative_tasks_settled', at=LATER)
    with sessions.begin() as session:
        with pytest.raises(ValueError):
            observe(session, grant, at=AT, control=control)
    with sessions.begin() as session:
        assert observe(session, grant, control=control)
        owner = session.get(PlannerCleanup, grant.lease.round_id)
        assert owner.local_state == 'settled' and owner.settlement_proof == 'cooperative_tasks_settled'
        assert owner.owner_lost_at is None
        assert resources.safe_snapshot(session).state == 'unknown'


@pytest.mark.parametrize('outcome', ['accepted', 'failed', 'cancelled', 'uncertain'])
def test_known_terminal_outcomes_and_charges_survive_quarantine(owned, outcome):
    sessions, grant = owned
    with sessions.begin() as session:
        if outcome == 'accepted':
            completion = ProviderCompletion(model='fake-runtime', finish_reason='stop',
                usage={'input_tokens': 1, 'output_tokens': 1},
                content='{"proposal":{"kind":"experiment","objective_ids":["FIFO_BEHAVIOR"],"seed":2,"operations":[{"op":"reset"}]}}')
            assert rounds.finish_model_round(session, grant.lease, completion, at=AT)
        elif outcome == 'cancelled':
            rounds.cancel_investigation(session, grant.lease.campaign_id, at=AT)
        else:
            assert rounds.fail_model_round(session, grant.lease, code='model_transport_error' if outcome == 'uncertain' else 'model_response_invalid',
                remote_unknown=outcome == 'uncertain', at=AT)
        before = state(session, grant)
    with sessions.begin() as session:
        assert observe(session, grant)
        after = state(session, grant)
        assert after[1:4] == before[1:4]  # Known status/lease/remote outcome.
        assert after[-1] == before[-1]  # Never refund model/execution charges.


def test_failure_after_round_revocation_rolls_back_both_cleanup_and_round(owned, monkeypatch):
    sessions, grant = owned
    with sessions() as session:
        before = state(session, grant)
    def fail_after_round(*args, **kwargs):
        raise RuntimeError('synthetic failure at fence update')
    monkeypatch.setattr(resources, '_next', fail_after_round)
    with sessions.begin() as session:
        with pytest.raises(RuntimeError, match='synthetic failure'):
            observe(session, grant)
    with sessions() as session:
        assert state(session, grant) == before


def test_fence_exhaustion_does_not_partially_quarantine(owned, monkeypatch):
    sessions, grant = owned
    # Lower the helper limit to the legitimate current fence. The database's
    # transition trigger correctly prevents jumping a real fence to MAX_FENCE.
    monkeypatch.setattr(resources, 'MAX_FENCE', grant.fence)
    with sessions() as session:
        before = state(session, grant)
    with sessions.begin() as session:
        with pytest.raises(ValueError):
            observe(session, grant)
    with sessions() as session:
        assert state(session, grant) == before
