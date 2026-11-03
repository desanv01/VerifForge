"""Schema integration boundaries only; no database, queue or native operations."""
from types import SimpleNamespace

import pytest

from veriforge.core import eda_repair_runtime as runtime, eda_repair_jobs as jobs, service


@pytest.fixture(autouse=True)
def reset_installation():
    runtime.install_runtime(None)
    yield
    runtime.install_runtime(None)


class NoSessions:
    def __call__(self):
        pytest.fail('schema refusal reached a database session')
    def begin(self):
        pytest.fail('schema refusal reached a database transaction')


@pytest.mark.parametrize('entry', ['submit', 'status', 'cancel', 'dispatch_one', 'dispatch_ready', 'run', 'quarantine'])
def test_schema_refusal_precedes_every_application_job_access(monkeypatch, entry):
    sessions = NoSessions()
    seen = []
    def reject(actual):
        assert actual is sessions
        seen.append('schema')
        raise service.Conflict('schema unavailable')
    monkeypatch.setattr(runtime, 'require_eda_schema', reject)
    installed = runtime.EdaRepairRuntime(sessions, lambda *a: pytest.fail('resolver used'),
        lambda: jobs.EdaRepairPolicy(enabled=True, runtime_identity='a'*64), lambda:'RUNNING')
    runtime.install_runtime(installed)
    queue = SimpleNamespace(enqueue=lambda *a, **k: pytest.fail('queued'))
    request = jobs.EdaRepairRequest(project_id='p', original_version_id='o', candidate_version_id='c')
    actions = {'submit':lambda:installed.submit(request,'vivado','key'),
        'status':lambda:installed.status('id'), 'cancel':lambda:installed.cancel('id'),
        'dispatch_one':lambda:runtime.dispatch_one('id',queue),
        'dispatch_ready':lambda:runtime.dispatch_ready(queue),
        'run':lambda:runtime.run_eda_job('id'),
        'quarantine':lambda:runtime.quarantine_uncertain('id')}
    with pytest.raises(service.Conflict, match='schema unavailable'):
        actions[entry]()
    assert seen == ['schema']


@pytest.mark.parametrize('state', ['disabled', 'paused'])
def test_effectful_disabled_entry_does_not_inspect_schema(monkeypatch, state):
    monkeypatch.setattr(runtime, 'require_eda_schema', lambda *a: pytest.fail('disabled schema query'))
    installed = runtime.EdaRepairRuntime(NoSessions(), None,
        lambda:jobs.EdaRepairPolicy(enabled=state!='disabled',runtime_identity='a'*64),
        lambda:'STOPPED' if state=='paused' else 'RUNNING')
    runtime.install_runtime(installed)
    with pytest.raises(service.Conflict, match='disabled'):
        runtime.run_eda_job('id')


def test_paused_installed_runtime_still_allows_schema_checked_observation(monkeypatch):
    # A final fenced completion must remain inspectable after execution authority
    # is withdrawn; this check stops at the schema gate, before any DB access.
    checks=[]
    def reject(sessions):
        checks.append('schema')
        raise service.Conflict('schema unavailable')
    monkeypatch.setattr(runtime, 'require_eda_schema', reject)
    installed=runtime.EdaRepairRuntime(NoSessions(),None,lambda:jobs.EdaRepairPolicy(),lambda:'STOPPED')
    with pytest.raises(service.Conflict,match='schema unavailable'):
        installed.status('retained-job')
    assert checks==['schema']


def test_failed_trusted_replacement_clears_prior_runtime():
    from veriforge.core.eda_application import install_eda_application
    runtime.install_runtime(runtime.EdaRepairRuntime(NoSessions(),None,None,None))
    with pytest.raises(service.Conflict,match='catalog'):
        install_eda_application(NoSessions(),output_root='unused',catalog=object(),policy_reader=lambda:None,control=lambda:'RUNNING')
    with pytest.raises(service.Conflict,match='disabled'):
        runtime.configured()
