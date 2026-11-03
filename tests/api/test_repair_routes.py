"""Route-function/schema integration; no sockets, queue, worker or tool execution.

Windows sandbox's asyncio self-pipe cannot initialize TestClient. Actual ASGI
transport acceptance remains separate; these checks do not claim HTTP execution.
"""
import importlib.util
from pathlib import Path

from fastapi import HTTPException
from pydantic import ValidationError
import pytest
from sqlalchemy import select

from veriforge.api.repairs import repairs_router
from veriforge.core import repair_jobs as jobs
from veriforge.core.database import Attempt, Campaign
from veriforge.core.repair_models import RepairJob

_spec = importlib.util.spec_from_file_location('repair_route_support',
    Path(__file__).resolve().parents[1]/'core'/'test_repair_jobs.py')
support = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(support)


@pytest.fixture
def state(tmp_path):
    data = support.make_state(tmp_path)
    yield data
    data[0].dispose()


def endpoints(state, enabled=True):
    router = repairs_router(state[1], policy=support.policy() if enabled else None,
        control=lambda:'RUNNING', root=state[2])
    assert {route.path for route in router.routes} == {'/api/attempts/{attempt_id}/repairs',
        '/api/repairs/{job_id}', '/api/repairs/{job_id}/report', '/api/repairs/{job_id}/cancel'}
    assert next(route for route in router.routes if route.name == 'admit').status_code == 202
    return {route.name:route.endpoint for route in router.routes}


def test_disabled_by_default_before_snapshot_or_dispatch(state, monkeypatch):
    monkeypatch.setattr(jobs, '_original', lambda *a:pytest.fail('disabled feature must not read snapshots'))
    with pytest.raises(HTTPException) as caught:
        endpoints(state, False)['admit'](state[3], jobs.RepairRequest(kind='reduction'), 'disabled')
    assert caught.value.status_code == 503
    with state[1]() as session:
        assert list(session.scalars(select(RepairJob))) == []


def test_id_only_admission_is_durable_and_gets_never_run_a_tool(state, monkeypatch):
    from veriforge.core import dispatch, worker
    monkeypatch.setattr(dispatch, 'RQDispatcher', lambda *a,**k:pytest.fail('HTTP cannot contact broker'))
    monkeypatch.setattr(worker, 'execute_attempt', lambda *a,**k:pytest.fail('HTTP cannot execute tools'))
    api = endpoints(state)
    body = jobs.RepairRequest(kind='candidate',candidate_fixture_id='multiplier_correct')
    first = api['admit'](state[3], body, 'same')
    job_id = first['job_id']
    assert api['admit'](state[3],body,'same')['job_id'] == job_id
    with pytest.raises(HTTPException) as caught:
        api['admit'](state[3], jobs.RepairRequest(kind='reduction'), 'same')
    assert caught.value.status_code == 409
    with state[1]() as session: before = len(list(session.scalars(select(Attempt))))
    assert api['status'](job_id)['job_id'] == job_id
    with pytest.raises(HTTPException) as caught: api['report'](job_id)
    assert caught.value.status_code == 409
    with state[1]() as session: assert len(list(session.scalars(select(Attempt)))) == before
    assert api['cancel'](job_id)['execution_status'] == 'cancelled'
    assert api['cancel'](job_id)['execution_status'] == 'cancelled'
    assert api['report'](job_id)['report']['operational_acceptance'] is False


@pytest.mark.parametrize('extra', [{'path':'/untrusted'}, {'command':'execute'},
    {'provenance':{'recommendation_origin':'model_recommendation'}}, {'checker_hash':'0'*64}])
def test_arbitrary_inputs_and_authorship_are_not_an_http_capability(state, extra):
    with pytest.raises(ValidationError): jobs.RepairRequest.model_validate({'kind':'reduction',**extra})


def test_foreign_original_is_not_authorized_and_unknown_ids_are_safe(state):
    with state[1].begin() as session:
        attempt = session.get(Attempt, state[3])
        session.get(Campaign, attempt.campaign_id).owner = 'foreign'
    api = endpoints(state)
    with pytest.raises(HTTPException) as caught: api['admit'](state[3],jobs.RepairRequest(kind='reduction'),'foreign')
    assert caught.value.status_code == 404
    with pytest.raises(HTTPException) as caught: api['status']('not-a-job')
    assert caught.value.status_code == 404


@pytest.mark.parametrize('change', ['disabled', 'identity', 'limits', 'reader-error', 'wrong-type', 'unchanged'])
def test_live_policy_change_during_admission_fences_final_control(monkeypatch, change):
    """Fake service transaction only: no SQLite, socket or worker operation."""
    from contextlib import contextmanager
    from types import SimpleNamespace
    selected = [support.policy()]
    rollback = []
    def reader():
        if selected[0] is None:
            raise RuntimeError('synthetic-reader-unavailable')
        return selected[0]
    @contextmanager
    def transaction():
        try:
            yield object()
        except Exception:
            rollback.append(True)
            raise
    def preflight(session, attempt_id, key, body, *, policy, control, **kwargs):
        assert control() == 'RUNNING'
        if change == 'disabled': selected[0] = jobs.RepairPolicy()
        elif change == 'identity': selected[0] = selected[0].model_copy(update={'runtime_identity':'b'*64})
        elif change == 'limits': selected[0] = support.policy(max_cases=15)
        elif change == 'reader-error': selected[0] = None
        elif change == 'wrong-type': selected[0] = {'enabled':True}
        # The real admission service already checks this callback again after
        # its evidence/suite preflight. Assert the route supplies fresh policy.
        if control() != 'RUNNING': raise jobs.RepairDisabled('synthetic-revocation')
        if change == 'unchanged': return SimpleNamespace(id='synthetic-job')
        pytest.fail('captured policy permitted admission after its withdrawal')
    monkeypatch.setattr(jobs, 'admit', preflight)
    monkeypatch.setattr(jobs, 'status', lambda session,identity:{'job_id':identity})
    router = repairs_router(SimpleNamespace(begin=transaction), policy_reader=reader, control=lambda:'RUNNING')
    route = next(route.endpoint for route in router.routes if route.name == 'admit')
    if change == 'unchanged':
        assert route('synthetic-attempt', jobs.RepairRequest(kind='reduction'), 'synthetic-key') == {'job_id':'synthetic-job'}
        assert rollback == []
    else:
        with pytest.raises(HTTPException) as caught:
            route('synthetic-attempt', jobs.RepairRequest(kind='reduction'), 'synthetic-key')
        assert caught.value.status_code == 503 and rollback == [True]
