"""Independent isolated SQLite and synthetic handoff checks; no provider/broker/PG."""
from concurrent.futures import ThreadPoolExecutor
from dataclasses import asdict, replace
from datetime import timedelta
import importlib.util
from pathlib import Path
import runpy
from threading import Barrier
from types import SimpleNamespace

from alembic import command
from alembic.config import Config
import pytest
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql
from sqlalchemy.exc import IntegrityError

from veriforge.core import planner_resources as resources, planner_cleanup as cleanup, planner_rounds as rounds, service
from veriforge.core.catalog import seed_catalog
from veriforge.core.database import (PlannerCleanup, PlannerModelCall, PlannerRound, PlannerState,
    PlannerRuntimeSlot, make_engine, session_factory)

ROOT = Path(__file__).resolve().parents[2]
BUILDERS = runpy.run_path(str(ROOT / 'tests/core/test_planner_resources.py'))
queued, AT = BUILDERS['queued'], BUILDERS['AT']
ROLE = resources.ROLE


@pytest.fixture
def db(tmp_path):
    engine = make_engine('sqlite:///' + (tmp_path / 'resource-review.db').as_posix())
    config = Config(str(ROOT / 'alembic.ini'))
    with engine.begin() as connection:
        config.attributes['connection'] = connection
        command.upgrade(config, 'head')  # Disposable test DB only, never configured/live.
    sessions = session_factory(engine)
    with sessions.begin() as session:
        seed_catalog(session)
    yield engine, sessions
    engine.dispose()


def claim(db, round_id, *, incarnation=None, at=AT):
    with db[1].begin() as session:
        return resources.claim_provider_slot(session, round_id, incarnation=incarnation or resources.new_incarnation(), role=ROLE, at=at, control='RUNNING')


def test_global_race_and_duplicate_delivery_grant_one_cleanup_owner(db):
    ids = [queued(db, key) for key in ('review-first', 'review-second')]
    barrier = Barrier(3)
    def race(round_id):
        barrier.wait()
        return claim(db, round_id)
    with ThreadPoolExecutor(max_workers=3) as pool:
        grants = list(pool.map(race, (ids[0], ids[0], ids[1])))
    assert sum(grant is not None for grant in grants) == 1
    winner = next(grant for grant in grants if grant)
    with db[1]() as session:
        assert session.scalar(sa.select(sa.func.count()).select_from(PlannerCleanup)) == 1
        assert resources.safe_snapshot(session).fence == 1
        loser = next(key for key in ids if key != winner.lease.round_id)
        assert session.get(PlannerRound, loser).status == 'queued'
    assert claim(db, winner.lease.round_id) is None


def test_claim_rollback_is_not_committed_authority_and_reclaim_changes_cleanup_owner(db):
    key, incarnation = queued(db, 'review-rollback'), resources.new_incarnation()
    with pytest.raises(RuntimeError):
        with db[1].begin() as session:
            old = resources.claim_provider_slot(session, key, incarnation=incarnation, role=ROLE, at=AT, control='RUNNING')
            assert old is not None
            with db[1]() as observer:
                assert resources.safe_snapshot(observer).state == 'idle'
                assert observer.get(PlannerCleanup, key) is None
            raise RuntimeError('synthetic crash before outer commit')
    fresh = claim(db, key, incarnation=incarnation)
    assert old.fence == fresh.fence and old.cleanup_ticket.owner_token != fresh.cleanup_ticket.owner_token
    with db[1].begin() as session:
        assert not resources.validate_provider_slot(session, old, role=ROLE, at=AT, control='RUNNING')
        assert resources.validate_provider_slot(session, fresh, role=ROLE, at=AT, control='RUNNING')


def test_settled_cleanup_allows_final_bookkeeping_but_never_new_io(db):
    grant = claim(db, queued(db, 'review-phases'))
    with db[1].begin() as session:
        cleanup.acknowledge_settled(session, grant.cleanup_ticket, proof='cooperative_tasks_settled', at=AT)
    with db[1].begin() as session:
        assert not resources.validate_provider_slot(session, grant, role=ROLE, at=AT, control='RUNNING', phase='pre_io')
        assert resources.validate_provider_slot(session, grant, role=ROLE, at=AT, control='RUNNING', phase='running')
        assert resources.validate_provider_slot(session, grant, role=ROLE, at=AT, control='RUNNING', phase='completion')
        assert rounds.fail_model_round(session, grant.lease, code='model_response_invalid', at=AT)
    with db[1].begin() as session:
        assert not resources.release_settled_slot(session, expected_fence=grant.fence + 1, expected_round_id=grant.lease.round_id, role=ROLE, at=AT)
        assert resources.release_settled_slot(session, expected_fence=grant.fence, expected_round_id=grant.lease.round_id, role=ROLE, at=AT)


def test_pre_io_rechecks_cleanup_after_campaign_lock_handoff(db, monkeypatch):
    grant = claim(db, queued(db, 'review-handoff'))
    original_lock, injected = service.lock, []
    def settle_at_lock(session, campaign_id):
        campaign = original_lock(session, campaign_id)
        if not injected:
            injected.append(True)
            # Deterministic authority-handoff simulation, not a claim that
            # PostgreSQL's concurrent callback/lock schedule was executed.
            cleanup.acknowledge_settled(session, grant.cleanup_ticket, proof='cooperative_tasks_settled', at=AT)
        return campaign
    monkeypatch.setattr(service, 'lock', settle_at_lock)
    with db[1].begin() as session:
        allowed = resources.validate_provider_slot(session, grant, role=ROLE, at=AT, control='RUNNING', phase='pre_io')
        assert session.get(PlannerCleanup, grant.lease.round_id).local_state == 'settled'
        assert not allowed, 'pre_io used cleanup state read before the campaign authority lock'


def test_owner_loss_no_ttl_late_ack_and_current_cas_release_do_not_restore_authority(db):
    first, second = queued(db, 'review-loss'), queued(db, 'review-next')
    grant = claim(db, first)
    with db[1].begin() as session:
        assert resources.mark_owner_lost(session, grant, role=ROLE, at=AT)
        current = resources.safe_snapshot(session)
        remote = session.get(PlannerModelCall, first).outcome
    assert current.state == 'unknown' and current.fence > grant.fence
    assert remote == 'remote_outcome_unknown'
    assert claim(db, second, at=AT + timedelta(days=10)) is None
    with db[1].begin() as session:
        assert not resources.release_settled_slot(session, expected_fence=current.fence, expected_round_id=first, role=ROLE, at=AT + timedelta(days=10))
        assert cleanup.acknowledge_settled(session, grant.cleanup_ticket, proof='cooperative_tasks_settled', at=AT + timedelta(days=10))
    with db[1].begin() as session:
        assert not resources.validate_provider_slot(session, grant, role=ROLE, at=AT + timedelta(days=10), control='RUNNING', phase='completion')
        assert not resources.release_settled_slot(session, expected_fence=grant.fence, expected_round_id=first, role=ROLE, at=AT + timedelta(days=10))
        assert resources.release_settled_slot(session, expected_fence=current.fence, expected_round_id=first, role=ROLE, at=AT + timedelta(days=10))
        assert session.get(PlannerModelCall, first).outcome == remote


def test_wrong_pause_authority_cannot_mutate_but_exact_pause_revokes(db):
    grant = claim(db, queued(db, 'review-pause'))
    with db[1].begin() as session:
        assert not resources.validate_provider_slot(session, replace(grant, fence=999), role=ROLE, at=AT, control='PAUSED')
        assert session.get(PlannerRound, grant.lease.round_id).status == 'running'
    with db[1].begin() as session:
        assert not resources.validate_provider_slot(session, grant, role=ROLE, at=AT, control='PAUSED')
    with db[1]() as session:
        assert session.get(PlannerState, grant.lease.campaign_id).stop_reason == 'paused'
        assert session.get(PlannerModelCall, grant.lease.round_id).outcome == 'remote_outcome_unknown'
        assert session.get(PlannerCleanup, grant.lease.round_id).local_state == 'pending'


def test_repr_snapshot_and_sql_monotonic_guard(db):
    grant = claim(db, queued(db, 'review-secrets'))
    assert all(secret not in repr(grant) for secret in (grant.incarnation, grant.lease.lease, grant.cleanup_ticket.owner_token))
    with db[1]() as session:
        public = asdict(resources.safe_snapshot(session))
        assert not {'incarnation', 'lease', 'owner_token', 'cleanup_ticket'} & public.keys()
    with pytest.raises(IntegrityError):
        with db[0].begin() as conn:
            conn.execute(sa.text('UPDATE planner_runtime_slots SET fence=0'))


def test_raw_sql_cannot_manufacture_proofless_settlement_or_release(db):
    grant = claim(db, queued(db, 'review-raw-proof'))
    with pytest.raises(IntegrityError):
        with db[0].begin() as conn:
            conn.execute(sa.text("UPDATE planner_cleanup SET local_state='settled',settled_at=registered_at WHERE round_id=:id"), {'id': grant.lease.round_id})
    with pytest.raises(IntegrityError):
        with db[0].begin() as conn:
            conn.execute(sa.text("UPDATE planner_runtime_slots SET fence=fence+1,state='idle',incarnation=NULL,active_round_id=NULL,campaign_id=NULL,acquired_at=NULL,owner_lost_at=NULL"))
    with db[1].begin() as session:
        assert not resources.release_settled_slot(session, expected_fence=grant.fence, expected_round_id=grant.lease.round_id, role=ROLE, at=AT)
        assert resources.safe_snapshot(session).state == 'active'
        assert session.get(PlannerCleanup, grant.lease.round_id).local_state == 'pending'


@pytest.mark.parametrize('field', ['incarnation', 'owner', 'campaign'])
def test_wrong_owner_association_cannot_validate_or_mark_loss(db, field):
    grant = claim(db, queued(db, 'review-forgery-' + field))
    if field == 'incarnation':
        forged = replace(grant, incarnation=resources.new_incarnation())
    elif field == 'owner':
        forged = replace(grant, cleanup_ticket=replace(grant.cleanup_ticket, owner_token='wrong-owner'))
    else:
        forged = replace(grant, lease=replace(grant.lease, campaign_id='wrong-campaign'))
    with db[1].begin() as session:
        assert not resources.validate_provider_slot(session, forged, role=ROLE, at=AT, control='RUNNING')
        assert not resources.mark_owner_lost(session, forged, role=ROLE, at=AT)
        assert resources.safe_snapshot(session).fence == grant.fence
        assert session.get(PlannerRound, grant.lease.round_id).status == 'running'


def test_postgres_lock_sql_is_resource_first_and_reloads_slot_only_offline():
    queries = []
    fake = SimpleNamespace(get_bind=lambda: SimpleNamespace(dialect=SimpleNamespace(name='postgresql')),
        scalar=lambda query: queries.append(query) or object())
    resources._lock(fake)
    assert len(queries) == 1 and 'FOR UPDATE' in str(queries[0].compile(dialect=postgresql.dialect()))
    assert queries[0].get_execution_options()['populate_existing'] is True
