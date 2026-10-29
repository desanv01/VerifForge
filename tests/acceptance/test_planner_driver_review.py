"""Independent driver gates: SQLite and labelled fake broker/model/runner only."""
import asyncio
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
import json

from alembic import command
from alembic.config import Config
from pydantic import SecretStr
import pytest
import sqlalchemy as sa

from veriforge.contracts import AttemptResult, Experiment
from veriforge.core import dispatch, planner_driver as driver, planner_rounds as rounds
from veriforge.core import planner_resources as resources, planner_transport, service, worker
from veriforge.core.catalog import seed_catalog
from veriforge.core.database import (Attempt, Dispatch, Finding, PlannerCleanup, PlannerFeedback, PlannerIntent,
    PlannerModelCall, PlannerReservation, PlannerRound, PlannerState, make_engine, session_factory)
from veriforge.core.planner_experiments import resolve_attempt_experiment
from veriforge.planner.models import InvestigationLimits, ProviderCompletion
from veriforge.planner.provider import MetaConfig, MetaProvider, ProviderError

AT = datetime(2026, 10, 30, tzinfo=timezone.utc)
MODEL = 'independent-driver-fake'


@pytest.fixture
def db(tmp_path):
    engine = make_engine('sqlite:///' + (tmp_path / 'review.db').as_posix())
    config = Config('alembic.ini')
    with engine.begin() as connection:
        config.attributes['connection'] = connection
        command.upgrade(config, 'head')
    sessions = session_factory(engine)
    with sessions.begin() as session: seed_catalog(session)
    yield engine, sessions
    engine.dispose()


def result(record):
    return AttemptResult(attempt_id='independent-synthetic-checker', experiment_id=record.wire_id,
        execution_status='completed', verdict='pass_within_scope', usage={'cycles': 1},
        identity={**record.identity, 'environment_hash': 'c' * 64}, uncertainties=('Synthetic review result; no simulator executed',))


def seed(db, key='seed', planner=True):
    with db[1].begin() as session:
        exp = Experiment(experiment_id=key, fixture_id='fifo_correct', objective_ids=('FIFO_BEHAVIOR',),
            budget={'max_cycles': 8, 'wall_time_seconds': 2}, operations=({'op': 'reset'}, {'op': 'read'}))
        campaign = service.create_campaign(session, 'fifo_correct', exp.model_dump(mode='json'), key, completion_policy='sequential' if planner else 'single')
        service.start(session, campaign.id)
        attempt = session.scalar(sa.select(Attempt).where(Attempt.campaign_id == campaign.id))
        if planner:
            lease, _, _ = service.claim(session, attempt.id)
            service.finalize(session, attempt.id, lease, result(resolve_attempt_experiment(session, attempt)).model_dump(mode='json'))
            rounds.attach_planner(session, campaign.id, limits=InvestigationLimits(max_total_cycles=80,
                max_wall_seconds=60, max_model_seconds=2, max_execution_seconds=2, max_cycles_per_experiment=8),
                requested_model=MODEL, strategy='meta', at=AT)
        return campaign.id, attempt.id


def intent(db, campaign, kind):
    with db[1]() as session:
        state = session.get(PlannerState, campaign)
        return session.scalar(sa.select(PlannerIntent.id).where(PlannerIntent.campaign_id == campaign,
            PlannerIntent.generation == state.generation, PlannerIntent.kind == kind))


class Broker:
    def __init__(self, fail=False): self.ids, self.attempts, self.fail = [], [], fail
    def enqueue_planner(self, *args):
        assert len(args) == 1 and type(args[0]) is str
        self.ids.append(args[0])
        if self.fail: raise ConnectionError('fake credential text must not persist')
    def enqueue(self, value): self.attempts.append(value)


class FakeMeta(MetaProvider):
    def __init__(self, uncertain=False):
        super().__init__(MetaConfig(model=MODEL, api_key=SecretStr('synthetic-not-a-key')))
        self.calls, self.uncertain = 0, uncertain
    async def complete(self, context, **kwargs):
        self.calls += 1
        if self.uncertain: raise ProviderError('model_http_503', transient=True)
        return ProviderCompletion(model=MODEL, usage={'input_tokens': 1, 'output_tokens': 1}, finish_reason='stop',
            content=json.dumps({'proposal': {'kind': 'experiment', 'objective_ids': ['FIFO_BEHAVIOR'], 'seed': 11,
                'operations': [{'op': 'reset'}, {'op': 'write', 'value': 9}, {'op': 'read'}]}}))


def advance(db, campaign):
    return driver.execute_intent(intent(db, campaign, 'advance'), sessions=db[1], now=lambda: AT)


def handler(db, provider):
    authority = planner_transport.ResourceAuthority(resources.new_incarnation())
    def invoke(intent_id):
        with db[1]() as session: round_id = session.get(PlannerIntent, intent_id).round_id
        return asyncio.run(planner_transport.run_model_intent(round_id, sessions=db[1], authority=authority,
            provider=provider, now=lambda: AT, control=lambda: 'RUNNING', settlement_seconds=0.02))
    return invoke


def proposed(db, key='seed'):
    campaign, _ = seed(db, key)
    assert advance(db, campaign)['accepted']
    provider = FakeMeta()
    model_id = intent(db, campaign, 'model')
    assert driver.execute_intent(model_id, sessions=db[1], now=lambda: AT, model_handler=handler(db, provider))['accepted']
    with db[1]() as session:
        child = session.scalar(sa.select(Attempt.id).where(Attempt.campaign_id == campaign, Attempt.execution_status == 'queued'))
    assert child and provider.calls == 1
    return campaign, child


def test_publisher_uses_sql_limit_id_only_and_redelivery_without_admission(db):
    for key in ('one', 'two', 'three'): seed(db, key)
    statements = []
    def trace(conn, cursor, sql, params, context, many): statements.append(sql.lower())
    sa.event.listen(db[0], 'before_cursor_execute', trace)
    broker = Broker()
    try: assert driver.publish_pending(db[1], broker, now=lambda: AT, limit=1) == {'sent': 1, 'failed': 0}
    finally: sa.event.remove(db[0], 'before_cursor_execute', trace)
    assert len(broker.ids) == 1
    assert any('select planner_intents.id' in sql and 'limit' in sql for sql in statements)
    with db[1]() as session:
        assert session.scalar(sa.select(sa.func.count()).select_from(PlannerModelCall)) == 0
        assert session.scalar(sa.select(sa.func.count()).select_from(PlannerReservation)) == 3
    assert driver.publish_pending(db[1], broker, now=lambda: AT, limit=1)['sent'] == 1
    assert driver.publish_pending(db[1], broker, now=lambda: AT + timedelta(seconds=31), limit=1)['sent'] == 1


def test_failed_broker_and_ack_commit_crash_redeliver_same_durable_id(db):
    campaign, _ = seed(db)
    target = intent(db, campaign, 'advance')
    failed = Broker(fail=True)
    assert driver.publish_pending(db[1], failed, now=lambda: AT) == {'sent': 0, 'failed': 1}
    with db[1]() as session: assert session.get(PlannerIntent, target).sent_at is None
    class AckCrash:
        def __call__(self): return db[1]()
        @contextmanager
        def begin(self):
            with db[1].begin() as session:
                yield session
                if session.get(PlannerIntent, target).sent_at is not None:
                    raise RuntimeError('Synthetic crash after broker accept, before ack commit')
    broker = Broker()
    with pytest.raises(RuntimeError): driver.publish_pending(AckCrash(), broker, now=lambda: AT)
    assert driver.publish_pending(db[1], broker, now=lambda: AT)['sent'] == 1
    assert broker.ids == [target, target]
    assert driver.execute_intent(target, sessions=db[1], now=lambda: AT)['accepted']
    assert not driver.execute_intent(target, sessions=db[1], now=lambda: AT)['accepted']
    with db[1]() as session: assert session.scalar(sa.select(sa.func.count()).select_from(PlannerModelCall)) == 1


def test_model_id_has_no_runtime_fallback_and_uncertain_work_never_resends(db):
    campaign, _ = seed(db)
    assert advance(db, campaign)['accepted']
    target = intent(db, campaign, 'model')
    assert driver.execute_intent(target, sessions=db[1], now=lambda: AT)['reason'] == 'runtime_unavailable'
    provider = FakeMeta(uncertain=True)
    invoke = handler(db, provider)
    assert driver.execute_intent(target, sessions=db[1], now=lambda: AT, model_handler=invoke)['accepted']
    with db[1]() as session:
        before = rounds.totals(session, campaign)
        assert session.get(PlannerState, campaign).phase in rounds.TERMINAL
    driver.release_settled_resource(db[1], now=lambda: AT)
    assert not driver.execute_intent(target, sessions=db[1], now=lambda: AT, model_handler=invoke)['accepted']
    assert driver.publish_pending(db[1], Broker(), now=lambda: AT)['sent'] == 0
    with db[1]() as session: assert rounds.totals(session, campaign) == before
    assert provider.calls == 1


def test_execution_waits_for_separate_committed_cas_release_and_lock_order(db):
    campaign, attempt_id = proposed(db)
    broker = Broker()
    assert dispatch.dispatch_pending(db[1], broker, clock=lambda: AT)['sent'] == 0
    assert driver.release_settled_resource(db[1], now=lambda: AT)
    with db[1]() as session:
        assert resources.safe_snapshot(session).state == 'idle'
        assert session.scalar(sa.select(sa.func.count()).select_from(PlannerCleanup)) == 1
    statements = []
    def trace(conn, cursor, sql, params, context, many): statements.append(sql.lower())
    sa.event.listen(db[0], 'before_cursor_execute', trace)
    try:
        with db[1].begin() as session:
            claimed, owned = driver.claim_attempt(session, attempt_id, at=AT, control='RUNNING')
            assert claimed and owned
    finally: sa.event.remove(db[0], 'before_cursor_execute', trace)
    global_lock = next(i for i, sql in enumerate(statements) if sql.startswith('update planner_runtime_slots'))
    campaign_lock = next(i for i, sql in enumerate(statements) if sql.startswith('update campaigns'))
    assert global_lock < campaign_lock
    with db[1].begin() as session: assert driver.claim_attempt(session, attempt_id, at=AT, control='RUNNING') == (None, True)


@pytest.mark.parametrize('mutation', ['generation', 'round_status', 'missing_round_binding', 'paused', 'deadline'])
def test_execution_claim_rejects_stale_or_unassociated_children(db, mutation):
    campaign, attempt_id = proposed(db)
    assert driver.release_settled_resource(db[1], now=lambda: AT)
    with db[1].begin() as session:
        if mutation == 'generation': session.get(PlannerState, campaign).generation += 1
        if mutation in {'round_status', 'missing_round_binding'}:
            row = session.scalar(sa.select(PlannerRound).where(PlannerRound.campaign_id == campaign, PlannerRound.status == 'accepted'))
            if mutation == 'round_status': row.status = 'failed'
            else: row.experiment_id = None
    with db[1].begin() as session:
        claimed, owned = driver.claim_attempt(session, attempt_id, at=AT + timedelta(seconds=61) if mutation == 'deadline' else AT,
            control='PAUSED' if mutation == 'paused' else 'RUNNING')
        assert claimed is None and owned


@pytest.mark.parametrize('infrastructure', [False, True])
def test_planner_worker_finalization_is_authoritative_no_generic_fallback(db, tmp_path, monkeypatch, infrastructure):
    campaign, attempt_id = proposed(db)
    driver.release_settled_resource(db[1], now=lambda: AT)
    calls = []
    original = rounds.finish_execution_step
    def finalize(*args, **kwargs):
        calls.append(kwargs.get('infrastructure_error', False))
        return original(*args, **kwargs)
    monkeypatch.setattr(rounds, 'finish_execution_step', finalize)
    def fake_runner(experiment, output, *, cancel_event):
        if infrastructure: raise RuntimeError('Synthetic runner failure')
        with db[1]() as session: return result(resolve_attempt_experiment(session, session.get(Attempt, attempt_id)))
    assert worker.execute_attempt(attempt_id, sessions=db[1], runner=fake_runner, artifact_root=tmp_path, now=lambda: AT)['accepted']
    assert calls == [infrastructure]
    with db[1]() as session:
        attempt = session.get(Attempt, attempt_id)
        assert attempt.execution_status == ('infrastructure_error' if infrastructure else 'completed')
        assert session.scalar(sa.select(sa.func.count()).select_from(Finding)) == 0
        assert session.scalar(sa.select(sa.func.count()).select_from(PlannerFeedback)) == 2


def test_planner_finalizer_refusal_cannot_publish_generic_result(db, tmp_path, monkeypatch):
    _, attempt_id = proposed(db)
    driver.release_settled_resource(db[1], now=lambda: AT)
    monkeypatch.setattr(rounds, 'finish_execution_step', lambda *a, **kw: False)
    def forbidden(*args, **kwargs): pytest.fail('Refused planner result must not call generic finalizer')
    monkeypatch.setattr(service, 'finalize', forbidden)
    def fake_runner(experiment, output, *, cancel_event):
        with db[1]() as session: return result(resolve_attempt_experiment(session, session.get(Attempt, attempt_id)))
    assert not worker.execute_attempt(attempt_id, sessions=db[1], runner=fake_runner, artifact_root=tmp_path, now=lambda: AT)['accepted']
    with db[1]() as session: assert session.get(Attempt, attempt_id).result is None


def test_manual_path_remains_claimable_while_provider_resource_is_busy(db):
    campaign, _ = seed(db, 'busy')
    advance(db, campaign)
    with db[1]() as session: round_id = session.get(PlannerIntent, intent(db, campaign, 'model')).round_id
    with db[1].begin() as session:
        assert resources.claim_provider_slot(session, round_id, incarnation=resources.new_incarnation(), role=resources.ROLE, at=AT, control='RUNNING')
    _, manual_id = seed(db, 'manual', planner=False)
    with db[1].begin() as session:
        claimed, owned = driver.claim_attempt(session, manual_id, at=AT, control='RUNNING')
        assert claimed and not owned


def test_trusted_control_and_clock_fail_closed_and_consumer_ids_only(db):
    campaign, _ = seed(db)
    broker = Broker()
    def broken_control(): raise RuntimeError('Synthetic control unavailable')
    assert driver.publish_pending(db[1], broker, now=lambda: AT, control=broken_control)['sent'] == 0
    with pytest.raises(ValueError): driver.publish_pending(db[1], broker, now=lambda: AT.replace(tzinfo=None))
    for invalid in ({'intent_id': intent(db, campaign, 'advance')}, True, 'arbitrary-handler'):
        assert driver.execute_intent(invalid, sessions=db[1], now=lambda: AT)['reason'] == 'invalid_intent'
    assert not broker.ids


def test_recovery_batch_is_sql_bounded_and_pause_never_invokes_inline_handler(db, monkeypatch):
    for key in ('pause-one', 'pause-two', 'pause-three'): seed(db, key)
    def forbidden(*args, **kwargs): pytest.fail('Reconciliation must not run intent/model inline')
    monkeypatch.setattr(driver, 'execute_intent', forbidden)
    statements = []
    def trace(conn, cursor, sql, params, context, many): statements.append(sql.lower())
    sa.event.listen(db[0], 'before_cursor_execute', trace)
    broker = Broker()
    try: outcome = driver.reconcile_once(db[1], broker, now=lambda: AT, control=lambda: 'STOPPED', limit=1)
    finally: sa.event.remove(db[0], 'before_cursor_execute', trace)
    assert outcome == {'recovered': 1, 'released': False, 'sent': 0, 'failed': 0}
    assert not broker.ids
    assert any('select planner_states.campaign_id' in sql and 'limit' in sql for sql in statements)
    with db[1]() as session:
        assert session.scalar(sa.select(sa.func.count()).select_from(PlannerState).where(PlannerState.phase == 'interrupted')) == 1
        assert session.scalar(sa.select(sa.func.count()).select_from(PlannerModelCall)) == 0


def test_committed_result_recovery_consumes_once_without_runner_or_model_replay(db, monkeypatch):
    campaign, attempt_id = proposed(db)
    driver.release_settled_resource(db[1], now=lambda: AT)
    with db[1].begin() as session:
        claimed, owned = driver.claim_attempt(session, attempt_id, at=AT, control='RUNNING')
        assert claimed and owned
        # Simulate a pre-integration worker commit that lost its continuation.
        service.finalize(session, attempt_id, claimed[0], result(resolve_attempt_experiment(session, session.get(Attempt, attempt_id))).model_dump(mode='json'))
    def forbidden(*args, **kwargs): pytest.fail('Committed execution must not be replayed')
    monkeypatch.setattr(worker, 'execute_attempt', forbidden)
    broker = Broker()
    first = driver.reconcile_once(db[1], broker, now=lambda: AT)
    second = driver.reconcile_once(db[1], broker, now=lambda: AT)
    assert first['recovered'] == 1 and second['recovered'] == 0
    assert len(broker.ids) == 1 and not broker.attempts
    assert dispatch.dispatch_pending(db[1], broker, clock=lambda: AT)['sent'] == 0
    with db[1]() as session:
        assert session.scalar(sa.select(sa.func.count()).select_from(PlannerFeedback)) == 2
        assert session.scalar(sa.select(sa.func.count()).select_from(PlannerModelCall)) == 1
        assert session.get(Attempt, attempt_id).execution_status == 'completed'
