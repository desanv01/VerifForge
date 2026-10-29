"""Independent runtime-boundary review; fake Meta and temporary SQLite only.

No broker, real provider credentials, HTTP server, RTL/native tool or PG access.
This file does not import/reuse the author's test suite.
"""
import asyncio
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
import stat
import threading
import time
from types import SimpleNamespace

import httpx
from alembic import command
from alembic.config import Config
from pydantic import SecretStr
import pytest
from sqlalchemy import select

from veriforge.contracts import Experiment
from veriforge.core import planner_cleanup as cleanup, planner_resources as resources, planner_rounds as rounds, service
from veriforge.core import planner_runtime as role, runtime_control
from veriforge.core.catalog import seed_catalog
from veriforge.core.database import Attempt, PlannerIntent, PlannerModelCall, PlannerRound, make_engine, session_factory
from veriforge.core.planner_experiments import resolve_attempt_experiment
from veriforge.planner.models import ProviderCompletion
from veriforge.planner.provider import MetaConfig, MetaProvider

AT = datetime(2026, 10, 30, tzinfo=timezone.utc)
MODEL = 'independent-review-fake-model'
CANARY = 'SYNTHETIC_REVIEW_ONLY_NOT_A_REAL_TOKEN'


@pytest.fixture(autouse=True)
def forbid_live_defaults(monkeypatch):
    def forbidden(*a, **k):
        pytest.fail('review attempted environment/default provider or real broker construction')
    monkeypatch.setattr(MetaConfig, 'from_env', forbidden)
    monkeypatch.setattr(role, '_environment_provider', forbidden)
    monkeypatch.setattr(role, '_rq_worker', forbidden)


@pytest.fixture
def sessions(tmp_path):
    engine = make_engine('sqlite:///' + (tmp_path / 'independent-runtime.db').as_posix())
    config = Config(str(Path(__file__).resolve().parents[2] / 'alembic.ini'))
    with engine.begin() as connection:
        config.attributes['connection'] = connection
        command.upgrade(config, 'head')  # Temporary SQLite test schema only.
    factory = session_factory(engine)
    with factory.begin() as session:
        seed_catalog(session)
    yield factory
    engine.dispose()


def pending_intent(factory):
    """One explicitly synthetic completed feedback attempt; never execution evidence."""
    with factory.begin() as session:
        plan = Experiment(experiment_id='independent-review-seed', fixture_id='fifo_correct',
                          objective_ids=('FIFO_BEHAVIOR',), operations=({'op': 'reset'},),
                          budget={'max_cycles': 8, 'wall_time_seconds': 2})
        campaign = service.create_campaign(session, 'fifo_correct', plan.model_dump(mode='json'),
                                           'independent-review', completion_policy='sequential')
        service.start(session, campaign.id)
        attempt = session.scalar(select(Attempt).where(Attempt.campaign_id == campaign.id))
        lease, _, _ = service.claim(session, attempt.id)
        record = resolve_attempt_experiment(session, attempt)
        service.finalize(session, attempt.id, lease, dict(attempt_id='fabricated-review-feedback',
            experiment_id=record.wire_id, execution_status='completed', verdict='pass_within_scope',
            identity={**record.identity, 'environment_hash': 'b' * 64}, usage={'cycles': 1}))
        rounds.attach_planner(session, campaign.id, strategy='meta', requested_model=MODEL, at=AT,
            limits={'max_total_cycles': 80, 'max_cycles_per_experiment': 8, 'max_wall_seconds': 60,
                    'max_execution_seconds': 2, 'max_model_seconds': 2})
        row = rounds.advance(session, campaign.id, at=AT)
        session.flush()
        intent = session.scalar(select(PlannerIntent.id).where(PlannerIntent.round_id == row.id))
        return row.id, intent


def runtime(factory, provider, **changes):
    options = dict(sessions=factory, provider=provider, control=lambda: 'RUNNING',
                   now=lambda: AT + timedelta(milliseconds=100), poll_seconds=0.005, settlement_seconds=0.02)
    options.update(changes)
    return role.PlannerRuntime(**options)


def completion(model=MODEL):
    return {'model': model, 'usage': {'prompt_tokens': 1, 'completion_tokens': 1},
            'choices': [{'finish_reason': 'stop', 'message': {'content': json.dumps({
                'proposal': {'kind': 'stop', 'reason': 'finite synthetic review'}})}}]}


def wait_for(predicate, seconds=2):
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        if predicate():
            return
        time.sleep(0.005)
    assert predicate(), 'synthetic owner did not settle within review bound'


@pytest.mark.parametrize('enabled,control', [(False, 'RUNNING'), (1, 'RUNNING'), (True, 'STOPPED')])
def test_inactive_entrypoint_does_not_construct_database_or_factories(monkeypatch, enabled, control):
    import veriforge.core.database as database
    def forbidden(*a, **k):
        pytest.fail('inactive path performed construction')
    monkeypatch.setattr(database, 'make_engine', forbidden)
    result = role.serve(enabled=enabled, control=lambda: control,
                        provider_factory=forbidden, worker_factory=forbidden)
    assert not result.running and not result.accepting


def test_control_reader_rejects_nonregular_opened_descriptor(tmp_path, monkeypatch):
    """Fake fstat avoids creating/blocking on an actual FIFO or device."""
    path = tmp_path / 'control.json'
    path.write_text('{"control":"RUNNING"}')
    actual = runtime_control.os.fstat
    def nonregular(fd):
        value = actual(fd)
        return SimpleNamespace(st_dev=value.st_dev, st_ino=value.st_ino, st_size=value.st_size,
            st_mtime_ns=value.st_mtime_ns, st_ctime_ns=value.st_ctime_ns, st_mode=stat.S_IFIFO)
    monkeypatch.setattr(runtime_control.os, 'fstat', nonregular)
    assert runtime_control.ControlReader(path)() == 'STOPPED'


def test_control_reader_requests_posix_race_guard_flags_when_exposed(tmp_path, monkeypatch):
    """Emulated flag availability checks construction, not actual POSIX I/O."""
    path = tmp_path / 'control.json'
    path.write_text('{"control":"RUNNING"}')
    actual, observed = runtime_control.os.open, []
    synthetic = {'O_NONBLOCK': 1 << 26, 'O_NOFOLLOW': 1 << 27, 'O_CLOEXEC': 1 << 28}
    for name, bit in synthetic.items():
        monkeypatch.setattr(runtime_control.os, name, bit, raising=False)
    mask = sum(synthetic.values())
    def capture(target, flags):
        observed.append(flags)
        return actual(target, flags & ~mask)
    monkeypatch.setattr(runtime_control.os, 'open', capture)
    assert runtime_control.ControlReader(path)() == 'RUNNING'
    assert len(observed) == 1 and all(observed[0] & bit for bit in synthetic.values())


def test_control_reader_regular_file_fallback_without_posix_flags_is_still_fail_closed(tmp_path, monkeypatch):
    """Windows-compatible path: regular file/byte/identity checks remain active."""
    path = tmp_path / 'control.json'
    path.write_text('{"control":"RUNNING"}')
    for name in ('O_NONBLOCK', 'O_NOFOLLOW', 'O_CLOEXEC'):
        monkeypatch.delattr(runtime_control.os, name, raising=False)
    assert runtime_control.ControlReader(path)() == 'RUNNING'
    path.write_text('{"control":"PAUSED"}')
    assert runtime_control.ControlReader(path)() == 'STOPPED'
    path.write_text('{"control":"RUNNING"}')
    actual = runtime_control.os.read
    def short(descriptor, limit):
        assert limit == 65_537
        return actual(descriptor, 1)
    monkeypatch.setattr(runtime_control.os, 'read', short)
    assert runtime_control.ControlReader(path)() == 'STOPPED'


def test_malformed_queue_ids_fail_before_any_database_or_provider_operation():
    def forbidden_session(*a, **k):
        pytest.fail('malformed bridge ID reached database')
    provider = MetaProvider(MetaConfig(model=MODEL, api_key=SecretStr(CANARY)),
        transport=httpx.MockTransport(lambda request: pytest.fail('malformed ID sent provider request')))
    owner = runtime(forbidden_session, provider)
    canonical = 'aaaaaaaa-bbbb-4ccc-8ddd-eeeeeeeeeeee'
    assert role.allowed_job(SimpleNamespace(func_name=role.TARGET, args=(canonical,), kwargs={}), SimpleNamespace(name=role.QUEUE))
    for value in [canonical.upper(), canonical.replace('-', ''), {'id': canonical}, (canonical,), 1]:
        assert not role.allowed_job(SimpleNamespace(func_name=role.TARGET, args=(value,), kwargs={}), SimpleNamespace(name=role.QUEUE))
        assert not owner.execute(value)['accepted']
        assert owner.handle_model_intent(value)['reason'] == 'runtime_unavailable'


@pytest.mark.parametrize('boundary', ['pause', 'outer-timeout'])
def test_mock_http_pending_call_is_bounded_cancelled_without_known_outcome_or_resend(sessions, boundary):
    round_id, intent = pending_intent(sessions)
    entered, finished = threading.Event(), threading.Event()
    control, count = ['RUNNING'], []
    async def pending(request):
        count.append(request)
        entered.set()
        try:
            await asyncio.Event().wait()
        finally:
            finished.set()
    provider = MetaProvider(MetaConfig(model=MODEL, api_key=SecretStr(CANARY)), transport=httpx.MockTransport(pending))
    owner = runtime(sessions, provider, control=lambda: control[0], operation_seconds=0.1 if boundary == 'outer-timeout' else 1)
    try:
        assert owner.start().accepting
        with ThreadPoolExecutor(max_workers=1) as pool:
            running = pool.submit(owner.handle_model_intent, intent)
            assert entered.wait(1)
            before = time.monotonic()
            if boundary == 'pause':
                control[0] = 'PAUSED'
            result = running.result(1.5)
            assert time.monotonic() - before < 1.5
        if boundary == 'outer-timeout':
            assert result['reason'] == 'runtime_timeout' and result['cleanup_pending']
        wait_for(finished.is_set)
        wait_for(lambda: cleanup_state(sessions, round_id) == 'settled')
        with sessions() as session:
            assert session.get(PlannerModelCall, round_id).outcome == 'remote_outcome_unknown'
        owner.handle_model_intent(intent)
        assert len(count) == 1
    finally:
        assert not owner.shutdown(grace_seconds=1).running


@pytest.mark.parametrize('outcome', ['429', 'wrong-model', 'transport-unknown'])
def test_mock_http_outcomes_survive_runtime_bridge_without_resend(sessions, outcome):
    round_id, intent = pending_intent(sessions)
    requests = []
    def handler(request):
        requests.append(request)
        assert request.headers['authorization'] == 'Bearer ' + CANARY
        if outcome == '429':
            return httpx.Response(429, json={'error': 'synthetic bounded response'})
        if outcome == 'transport-unknown':
            raise httpx.ReadTimeout('synthetic diagnostic must not persist', request=request)
        return httpx.Response(200, json=completion('another-model'))
    provider = MetaProvider(MetaConfig(model=MODEL, api_key=SecretStr(CANARY)), transport=httpx.MockTransport(handler))
    owner = runtime(sessions, provider)
    try:
        assert owner.start().accepting
        result = owner.handle_model_intent(intent)
        assert len(requests) == 1 and CANARY not in repr(result)
        wait_for(lambda: cleanup_state(sessions, round_id) == 'settled')
        with sessions() as session:
            call = session.get(PlannerModelCall, round_id)
            expected = {'429': 'model_http_429', 'wrong-model': 'model_response_invalid',
                        'transport-unknown': 'remote_outcome_unknown'}[outcome]
            assert call.outcome == expected
            if outcome == 'wrong-model':
                assert result['reason'] == 'model_identity_mismatch'  # Durable DB code is deliberately normalized.
            assert CANARY.encode() not in call.request_body and CANARY not in json.dumps(call.context)
        owner.handle_model_intent(intent)
        assert len(requests) == 1
    finally:
        assert not owner.shutdown(grace_seconds=1).running


def cleanup_state(factory, round_id):
    with factory() as session:
        value = cleanup.read_cleanup(session, round_id)
        return None if value is None else value.local_state


def test_shutdown_during_event_loop_initialization_returns_bounded_pending_not_exception(sessions, monkeypatch):
    """Slow loop initialization is synthetic; release/join guarantees no leaked thread."""
    entered, release = threading.Event(), threading.Event()
    actual_new_loop = role.asyncio.new_event_loop
    def initializing():
        entered.set()
        assert release.wait(2), 'review failed to release synthetic initialization'
        return actual_new_loop()
    monkeypatch.setattr(role.asyncio, 'new_event_loop', initializing)
    provider = MetaProvider(MetaConfig(model=MODEL, api_key=SecretStr(CANARY)),
        transport=httpx.MockTransport(lambda request: pytest.fail('startup failure sent provider request')))
    owner = runtime(sessions, provider)
    monkeypatch.setattr(owner._started, 'wait', lambda timeout: entered.wait(1) and False)
    try:
        assert owner.start().reason == 'loop_unavailable'
        assert owner._loop is None and owner._thread.is_alive()
        before = time.monotonic()
        status = owner.shutdown(grace_seconds=0.05)
        assert status.cleanup_pending and time.monotonic() - before < 0.3
    finally:
        release.set()
        if owner._thread is not None:
            owner._thread.join(2)
            assert not owner._thread.is_alive()


def test_duplicate_cross_thread_id_bridge_has_one_mock_request_and_no_session_across_await(sessions):
    _, intent = pending_intent(sessions)
    entered, release = threading.Event(), threading.Event()
    active, lock, observed = set(), threading.Lock(), []
    class TrackedSessions:
        @contextmanager
        def _open(self, transaction):
            thread = threading.get_ident()
            with (sessions.begin() if transaction else sessions()) as session:
                with lock:
                    active.add(id(session))
                try:
                    yield session
                    assert threading.get_ident() == thread
                finally:
                    with lock:
                        active.remove(id(session))
        def begin(self):
            return self._open(True)
        def __call__(self):
            return self._open(False)
    async def handler(request):
        with lock:
            assert not active
        observed.append((threading.get_ident(), asyncio.get_running_loop()))
        entered.set()
        while not release.is_set():
            await asyncio.sleep(0.005)
        with lock:
            assert not active
        return httpx.Response(200, json=completion())
    provider = MetaProvider(MetaConfig(model=MODEL, api_key=SecretStr(CANARY)), transport=httpx.MockTransport(handler))
    owner = runtime(TrackedSessions(), provider)
    try:
        assert owner.start().accepting
        with ThreadPoolExecutor(max_workers=2) as pool:
            first = pool.submit(owner.handle_model_intent, intent)
            try:
                assert entered.wait(1)
                second = pool.submit(owner.handle_model_intent, intent).result(1)
                assert second['reason'] == 'not_eligible'
            finally:
                release.set()
            first.result(2)
        assert len(observed) == 1 and observed[0][0] != threading.get_ident()
    finally:
        release.set()
        assert not owner.shutdown(grace_seconds=1).cleanup_pending


def test_noncooperative_old_owner_cannot_gain_supervisor_takeover_or_false_settlement(sessions, monkeypatch):
    round_id, intent = pending_intent(sessions)
    entered, release = threading.Event(), threading.Event()
    async def stubborn(context, **kwargs):
        entered.set()
        while not release.is_set():
            try:
                await asyncio.sleep(0.005)
            except asyncio.CancelledError:
                continue
        return ProviderCompletion(model=MODEL, usage={'input_tokens': 1, 'output_tokens': 1},
            finish_reason='stop', content=json.dumps({'proposal': {'kind': 'stop', 'reason': 'late synthetic result'}}))
    provider = MetaProvider(MetaConfig(model=MODEL, api_key=SecretStr(CANARY)))
    monkeypatch.setattr(provider, 'complete', stubborn)
    def no_supervisor(*a, **k):
        pytest.fail('runtime automatically invoked trusted supervisor takeover')
    monkeypatch.setattr(resources, 'quarantine_lost_owner', no_supervisor)
    owner = runtime(sessions, provider)
    replacement = runtime(sessions, MetaProvider(MetaConfig(model=MODEL, api_key=SecretStr(CANARY)),
        transport=httpx.MockTransport(lambda request: pytest.fail('replacement sent a request'))))
    try:
        assert owner.start().accepting
        with ThreadPoolExecutor(max_workers=1) as pool:
            running = pool.submit(owner.handle_model_intent, intent)
            try:
                assert entered.wait(1)
                before = time.monotonic()
                status = owner.shutdown(grace_seconds=0.1)
                assert time.monotonic() - before < 0.5 and status.cleanup_pending
                assert cleanup_state(sessions, round_id) != 'settled'
                assert replacement.start().reason == 'resource_blocked'
            finally:
                release.set()
            running.result(2)
        wait_for(lambda: cleanup_state(sessions, round_id) == 'settled')
        with sessions() as session:
            assert session.get(PlannerModelCall, round_id).outcome == 'remote_outcome_unknown'
            assert resources.safe_snapshot(session).state == 'unknown'
    finally:
        release.set()
        owner.shutdown(grace_seconds=1)
        replacement.shutdown(grace_seconds=1)
