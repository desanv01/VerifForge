"""Repair worker integration: fake runners, local SQLite and synthetic evidence."""
import importlib.util
from pathlib import Path
from unittest.mock import Mock

import pytest
from sqlalchemy import select, text

from veriforge.core import dispatch, repair_runtime as runtime, worker
from veriforge.core.database import Attempt, PlannerRuntimeSlot, make_engine, now, session_factory
from veriforge.core.repair_models import RepairCase, RepairJob
from veriforge.core.runtime_control import ControlReader

spec = importlib.util.spec_from_file_location('repair_runtime_fixtures', Path(__file__).with_name('test_repair_jobs.py'))
fixtures = importlib.util.module_from_spec(spec)
spec.loader.exec_module(fixtures)


@pytest.fixture
def state(tmp_path, monkeypatch):
    monkeypatch.setattr(runtime, '_INSTALLED', runtime.RepairRuntime())
    data = fixtures.make_state(tmp_path)
    path = tmp_path / 'synthetic-control.json'
    path.write_text('{"control":"RUNNING"}')
    policy = [fixtures.policy()]
    binding = runtime.RepairRuntime(lambda: policy[0], ControlReader(path), data[2])
    yield data, binding, policy, path
    data[0].dispose()


def active(data, identity):
    with data[1]() as session:
        job = session.get(RepairJob, identity)
        return session.get(RepairCase, job.active_case_id).attempt_id


class Broker:
    def __init__(self, sessions): self.sessions, self.attempts = sessions, []
    def enqueue(self, identity):
        # A fresh transaction observes the durable child before delivery.
        with self.sessions() as session:
            assert session.get(Attempt, identity).execution_status == 'queued'
        self.attempts.append(identity)
    def enqueue_planner(self, _): raise AssertionError('No planner jobs here')
    def enqueue_export(self, _): raise AssertionError('No exports here')


def fake_runner(plan, output, *, cancel_event):
    return fixtures._fixture.recorded_arithmetic(plan, output)


def test_disabled_default_then_explicit_dispatch_claim_and_post_commit_advance(state):
    data, binding, _, _ = state
    identity = fixtures.admit(data)
    attempt = active(data, identity)
    broker = Broker(data[1])
    assert dispatch.dispatch_pending(data[1], broker)['sent'] == 0
    forbidden = Mock(side_effect=AssertionError('disabled runner'))
    assert worker.execute_attempt(attempt, sessions=data[1], runner=forbidden) == {'accepted': False}
    forbidden.assert_not_called()
    assert dispatch.dispatch_pending(data[1], broker, repair_runtime=binding)['sent'] == 1
    assert worker.execute_attempt(attempt, sessions=data[1], runner=fake_runner, repair_runtime=binding)['accepted']
    successor = active(data, identity)
    assert successor != attempt
    with data[1]() as session:
        assert session.get(Attempt, attempt).execution_status == 'completed'
        assert session.scalar(select(RepairCase).where(RepairCase.attempt_id == attempt)).result_hash
    assert dispatch.dispatch_pending(data[1], broker, repair_runtime=binding)['sent'] == 1
    assert broker.attempts == [attempt, successor]


@pytest.mark.parametrize('revoke', ['policy', 'canonical', 'job'])
def test_running_revocation_latches_and_prevents_late_result_and_successor(state, revoke):
    data, binding, policies, path = state
    identity = fixtures.admit(data)
    attempt = active(data, identity)
    def runner(plan, output, *, cancel_event):
        if revoke == 'policy': policies[0] = fixtures.jobs.RepairPolicy()
        elif revoke == 'canonical': path.write_text('{"control":"STOPPED"}')
        else:
            with data[1].begin() as session: fixtures.jobs.cancel(session, identity)
        assert cancel_event.is_set()
        policies[0] = fixtures.policy()
        path.write_text('{"control":"RUNNING"}')
        return fake_runner(plan, output, cancel_event=cancel_event)
    assert not worker.execute_attempt(attempt, sessions=data[1], runner=runner, repair_runtime=binding)['accepted']
    with data[1]() as session:
        assert session.get(Attempt, attempt).result is None
        assert session.get(RepairJob, identity).status in {'interrupted', 'cancelled'}
        assert len(list(session.scalars(select(RepairCase.attempt_id).where(RepairCase.attempt_id.is_not(None))))) == 1


def test_finalization_rechecks_policy_even_if_runner_never_polls(state):
    data, binding, policies, _ = state
    identity = fixtures.admit(data)
    attempt = active(data, identity)
    def runner(plan, output, *, cancel_event):
        result = fake_runner(plan, output, cancel_event=cancel_event)
        policies[0] = fixtures.jobs.RepairPolicy()
        return result
    assert not worker.execute_attempt(attempt, sessions=data[1], runner=runner, repair_runtime=binding)['accepted']
    with data[1]() as session: assert session.get(Attempt, attempt).result is None


def test_unknown_runner_effect_stops_no_retry(state):
    data, binding, _, _ = state
    identity = fixtures.admit(data); attempt = active(data, identity)
    bad = Mock(side_effect=RuntimeError('synthetic interrupted effect'))
    result = worker.execute_attempt(attempt, sessions=data[1], runner=bad, repair_runtime=binding)
    assert result['infrastructure_error']
    assert not worker.execute_attempt(attempt, sessions=data[1], runner=bad, repair_runtime=binding)['accepted']
    assert bad.call_count == 1
    with data[1]() as session:
        assert session.get(RepairJob, identity).reason == 'child_effect_unknown_no_retry'


def test_terminal_commit_reopens_then_periodic_reconcile_queues_once(state):
    data, binding, _, _ = state
    identity = fixtures.admit(data)
    first = active(data, identity)
    fixtures.complete_one(data, identity)  # Terminal result committed without worker continuation.
    url = str(data[0].url)
    data[0].dispose()
    reopened = make_engine(url); sessions = session_factory(reopened)
    try:
        with sessions.begin() as session:
            session.add(PlannerRuntimeSlot(resource='local_planner_provider', state='idle', changed_at=now()))
        broker = Broker(sessions)
        result = worker.reconcile_once(sessions=sessions, dispatcher=broker, repair_runtime=binding)
        assert result['repairs'] == {'examined': 1, 'failed': 0}
        successor = active((reopened, sessions, data[2], data[3]), identity)
        assert successor != first and broker.attempts == [successor]
        worker.reconcile_once(sessions=sessions, dispatcher=broker, repair_runtime=binding)
        assert broker.attempts == [successor]
    finally: reopened.dispose()


def test_policy_change_before_claim_and_manually_started_planned_child_are_fenced(state):
    data, binding, policies, _ = state
    identity = fixtures.admit(data)
    first = active(data, identity)
    policies[0] = fixtures.policy(max_wall_seconds=599)
    assert not worker.execute_attempt(first, sessions=data[1], runner=Mock(), repair_runtime=binding)['accepted']
    policies[0] = fixtures.policy()
    with data[1].begin() as session:
        planned = session.scalar(select(RepairCase).where(RepairCase.job_id == identity, RepairCase.status == 'planned'))
        fixtures.service.start(session, planned.campaign_id)
        session.flush()
        unauthorized = session.scalar(select(Attempt.id).where(Attempt.campaign_id == planned.campaign_id))
    assert not worker.execute_attempt(unauthorized, sessions=data[1], runner=Mock(), repair_runtime=binding)['accepted']


def test_runtime_install_is_explicit_and_wrong_root_does_not_claim(state, tmp_path):
    data, binding, _, _ = state
    identity = fixtures.admit(data); attempt = active(data, identity)
    runtime.install(binding)
    assert not worker.execute_attempt(attempt, sessions=data[1], runner=Mock(), artifact_root=tmp_path/'other')['accepted']
    assert worker.execute_attempt(attempt, sessions=data[1], runner=fake_runner)['accepted']
    with pytest.raises(ValueError): runtime.advance_batch(data[1], limit=101)


def test_partial_schema_and_policy_reader_failure_are_fail_closed(state, tmp_path):
    data, binding, _, _ = state
    identity = fixtures.admit(data); attempt = active(data, identity)
    broken = runtime.RepairRuntime(Mock(side_effect=OSError('synthetic policy failure')), binding.control, binding.root)
    assert not worker.execute_attempt(attempt, sessions=data[1], runner=Mock(), repair_runtime=broken)['accepted']
    with pytest.raises(ValueError): runtime.RepairRuntime(root=Path('relative'))
    engine = make_engine('sqlite:///' + (tmp_path/'partial.db').as_posix())
    try:
        with engine.begin() as connection: connection.execute(text('CREATE TABLE repair_jobs (id TEXT)'))
        with session_factory(engine)() as session:
            assert runtime.allowed(session, 'synthetic-id') is False
    finally: engine.dispose()
