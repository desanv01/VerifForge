"""Source/fake-controller checks only; never starts PG, Redis, Docker or HTTP."""
import importlib.util
import asyncio
import json
from pathlib import Path
import re
from types import SimpleNamespace

import pytest
import httpx

ROOT = Path(__file__).resolve().parents[2]


def load(name, path):
    spec = importlib.util.spec_from_file_location(name, ROOT / path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


host = load('review_planner_pg_host', 'scripts/ci_planner_postgres.py')
fixture = load('review_planner_pg_fixture', 'tests/integration/planner_process_fixture.py')


@pytest.mark.parametrize('environment', [{}, {'GITHUB_RUN_ID': '../project', 'GITHUB_RUN_ATTEMPT': '1'},
                                        {'GITHUB_RUN_ID': '12', 'GITHUB_RUN_ATTEMPT': '1;command'}])
def test_no_ambient_or_arbitrary_project_identity(environment):
    with pytest.raises(RuntimeError):
        host.project_name(environment)


def test_import_and_disabled_local_entrypoint_have_no_side_effects(monkeypatch):
    monkeypatch.delenv('GITHUB_ACTIONS', raising=False)
    monkeypatch.setattr(host.subprocess, 'Popen', lambda *a, **k: pytest.fail('process dispatch'))
    monkeypatch.setattr(host.subprocess, 'run', lambda *a, **k: pytest.fail('Docker or service operation'))
    with pytest.raises(RuntimeError, match='hosted Linux only'):
        host.main()
    monkeypatch.delenv('VF_PG_CI_PROJECT', raising=False)
    with pytest.raises(RuntimeError, match='ephemeral fixture only'):
        fixture.ephemeral()


def test_plan_has_private_owned_resources_and_no_native_worker_or_credentials(tmp_path):
    project = 'vf-planner-pg-ci-123-1'
    plan = host.compose_plan(project, 'sha256:' + 'a' * 64, tmp_path / 'controls', tmp_path / 'evidence')
    assert plan['networks']['default']['internal'] is True
    assert set(plan['volumes']) == {'database', 'broker'}
    assert not any('ports' in value or 'env_file' in value or 'build' in value for value in plan['services'].values())
    assert not {'rtl-worker', 'worker', 'reconciler'} & set(plan['services'])
    assert all(v['labels'][host.OWNER] == project for v in plan['services'].values())
    assert all(v['mem_limit'] and v['pids_limit'] and v['restart'] == 'no' for v in plan['services'].values())
    for name in ('runtime-a', 'runtime-b', 'runtime-c', 'api'):
        assert str(tmp_path / 'controls') + ':/controls:ro' in plan['services'][name]['volumes']
        assert 'MODEL_API_KEY' not in plan['services'][name]['environment']
    assert str(tmp_path / 'controls') + ':/controls' in plan['services']['controller']['volumes']
    assert host.LIMITS == {'rounds': 8, 'deliveries': 12, 'mock_requests': 7, 'native_runs': 0, 'real_provider_calls': 0}
    assert fixture.CAPS == {k: host.LIMITS[k] for k in ('rounds', 'deliveries', 'mock_requests')}
    assert len(host.CASES) == 6 and host.REVISION == '13engineeringprojects'


def test_supervisor_permits_exactly_one_kill_then_one_blocked_start():
    project = 'vf-planner-pg-ci-123-1'
    kill = {'project': project, 'sequence': 1, 'action': 'kill', 'service': 'runtime-a'}
    restart = {'project': project, 'sequence': 2, 'action': 'start-blocked', 'service': 'runtime-c'}
    assert host.supervisor_action(project, kill, set()) == 'runtime-a'
    assert host.supervisor_action(project, restart, {1}) == 'runtime-c'
    for request, done in [(restart, set()), (kill, {1}), ({**kill, 'service': 'postgres'}, set()),
                          ({**kill, 'project': 'foreign'}, set()), ({**kill, 'command': 'arbitrary'}, set()),
                          ({**restart, 'service': 'runtime-b'}, {1}), ({**kill, 'sequence': True}, set())]:
        with pytest.raises(RuntimeError):
            host.supervisor_action(project, request, done)


def test_mock_scripts_and_request_caps_are_fixed_and_hash_bound(monkeypatch, tmp_path):
    monkeypatch.setattr(fixture, 'OUT', tmp_path)
    state = fixture.MockState()
    raw = b'{"synthetic":true}'
    value = {'hash': fixture.sha(raw), 'behavior': 'held'}
    state.register(value)
    with pytest.raises(RuntimeError, match='duplicate'):
        state.register(value)
    with pytest.raises(RuntimeError):
        state.register({'hash': 'f' * 64, 'behavior': 'run-shell'})
    with pytest.raises(RuntimeError):
        state.observe(b'changed admitted bytes', 'runtime-a')
    for _ in range(7):
        assert state.observe(raw, 'runtime-a') == (value['hash'], 'held')
    with pytest.raises(RuntimeError, match='cap'):
        state.observe(raw, 'runtime-a')
    assert len(state.requests) == 7
    assert fixture.CANARY not in json.dumps(state.requests)
    state.release(value['hash'])
    assert json.loads((tmp_path / 'mock-released.json').read_text()) == [value['hash']]


def test_injected_transport_preserves_body_and_only_forwards_to_fixed_mock(monkeypatch, tmp_path):
    from veriforge.planner.provider import ENDPOINT
    monkeypatch.setattr(fixture, 'OUT', tmp_path)
    raw = b'{"explicit":"synthetic admitted body"}'
    fixture.write(tmp_path / 'mock-scripts.json', {fixture.sha(raw): 'stop'})
    captured = []
    async def backend(request):
        captured.append(request)
        assert str(request.url) == 'http://mock:8080/v1/chat/completions'
        assert await request.aread() == raw
        assert 'authorization' not in request.headers
        return httpx.Response(200, json=fixture.completion())
    async def scenario():
        transport = fixture.MockHTTPTransport('runtime-a', backend=httpx.MockTransport(backend))
        try:
            response = await transport.handle_async_request(httpx.Request('POST', ENDPOINT, content=raw,
                headers={'Authorization': 'Bearer ' + fixture.CANARY}))
            assert response.status_code == 200 and len(captured) == 1
            with pytest.raises(RuntimeError, match='logical provider'):
                await transport.handle_async_request(httpx.Request('POST', 'https://example.invalid/', content=raw))
            assert len(captured) == 1
        finally:
            await transport.aclose()
    asyncio.run(scenario())


@pytest.mark.parametrize('behavior', ['held', 'late'])
def test_cancellation_fault_is_explicit_bounded_and_never_resends(monkeypatch, tmp_path, behavior):
    from veriforge.planner.provider import ENDPOINT
    monkeypatch.setattr(fixture, 'OUT', tmp_path)
    raw = b'{"synthetic":"cancellation boundary"}'
    digest = fixture.sha(raw)
    fixture.write(tmp_path / 'mock-scripts.json', {digest: behavior})
    fixture.write(tmp_path / 'mock-released.json', [])
    async def scenario():
        entered = asyncio.Event()
        count = []
        async def pending(request):
            count.append(request)
            entered.set()
            await asyncio.Event().wait()
        transport = fixture.MockHTTPTransport('runtime-a', backend=httpx.MockTransport(pending))
        task = asyncio.create_task(transport.handle_async_request(httpx.Request('POST', ENDPOINT, content=raw)))
        try:
            await asyncio.wait_for(entered.wait(), 1)
            task.cancel()
            if behavior == 'held':
                with pytest.raises(asyncio.CancelledError):
                    await asyncio.wait_for(task, 1)
            else:
                await asyncio.sleep(0.03)
                assert not task.done()
                fixture.write(tmp_path / 'mock-released.json', [digest])
                assert (await asyncio.wait_for(task, 1)).status_code == 200
            assert len(count) == 1
        finally:
            fixture.write(tmp_path / 'mock-released.json', [digest])
            if not task.done():
                task.cancel()
                await asyncio.gather(task, return_exceptions=True)
            await transport.aclose()
    asyncio.run(scenario())


def test_queue_delivery_cap_precedes_real_enqueue_and_never_changes_target(monkeypatch, tmp_path):
    from veriforge.core import dispatch
    monkeypatch.setattr(fixture, 'OUT', tmp_path)
    calls = []
    class Queue:
        name = 'veriforge-planner'
        def enqueue(self, target, intent, **kwargs):
            calls.append((target, intent, kwargs))
            return SimpleNamespace(id='fake-job-' + str(len(calls)), origin=self.name, func_name=target, args=(intent,), kwargs={})
    class Dispatcher:
        def __init__(self, *_):
            self.planner_queue = Queue()
        def enqueue_planner(self, intent):
            self.planner_queue.enqueue('veriforge.core.planner_runtime.execute_intent', intent, job_timeout=180, result_ttl=0)
    monkeypatch.setattr(dispatch, 'RQDispatcher', Dispatcher)
    controller = fixture.Qualification.__new__(fixture.Qualification)
    controller.deliveries = []
    value = {'intent': 'aaaaaaaa-bbbb-4ccc-8ddd-eeeeeeeeeeee'}
    controller.enqueue(value, 12)
    with pytest.raises(RuntimeError, match='cap'):
        controller.enqueue(value)
    assert len(calls) == len(controller.deliveries) == 12
    assert all(args[0] == 'veriforge.core.planner_runtime.execute_intent' for args in calls)


def test_case_wait_is_terminally_bounded_by_case_deadline(monkeypatch):
    monkeypatch.setattr(fixture, 'CASE_DEADLINE', 1)
    monkeypatch.setattr(fixture.time, 'monotonic', lambda: 2)
    with pytest.raises(RuntimeError, match='bounded'):
        fixture.wait_for(lambda: pytest.fail('expired case performed work'))


def test_cleanup_without_marker_performs_no_docker_operation(monkeypatch, tmp_path):
    monkeypatch.setattr(host.subprocess, 'run', lambda *a, **k: pytest.fail('unowned cleanup'))
    host.cleanup('vf-planner-pg-ci-123-1', tmp_path / 'missing')


def test_cleanup_rejects_foreign_owned_project_container_before_mutation(monkeypatch, tmp_path):
    project = 'vf-planner-pg-ci-123-1'
    monkeypatch.setattr(host, 'ROOT', tmp_path)
    work = tmp_path / '.planner-pg-ci' / project
    host.write(work / 'owner.json', {'project': project})
    commands = []
    def fake(args, **kwargs):
        commands.append(args)
        if args[1:3] == ['ps', '-aq']:
            return SimpleNamespace(returncode=0, stdout=b'foreign-id\n')
        return SimpleNamespace(returncode=0, stdout=json.dumps({host.OWNER: 'foreign'}).encode())
    monkeypatch.setattr(host.subprocess, 'run', fake)
    with pytest.raises(RuntimeError, match='foreign container'):
        host.cleanup(project, work)
    assert not any('down' in command or 'kill' in command or 'rm' in command for command in commands)


def test_wrong_committed_migration_head_fails_before_build_preserves_first_error(monkeypatch, tmp_path):
    monkeypatch.setattr(host, 'ROOT', tmp_path)
    monkeypatch.setattr(host, 'OUT', tmp_path / 'evidence')
    monkeypatch.setattr(host.sys, 'platform', 'linux')
    for key, value in {'GITHUB_ACTIONS': 'true', 'GITHUB_EVENT_NAME': 'workflow_dispatch',
                       'GITHUB_REF': 'refs/heads/main', 'GITHUB_RUN_ID': '123', 'GITHUB_RUN_ATTEMPT': '1'}.items():
        monkeypatch.setenv(key, value)
    class FakeHost:
        def __init__(self, *_):
            pass
        def run(self, *a, **k):
            pytest.fail('wrong schema reached Docker')
    shared = SimpleNamespace(Host=FakeHost, stage_sources=lambda work: ('a' * 40, '14-unreviewed'), evidence_size=lambda out: 0, STOP=False)
    monkeypatch.setattr(host, 'host_module', lambda: shared)
    def cleanup_fails(*_):
        raise RuntimeError('secondary cleanup error')
    monkeypatch.setattr(host, 'cleanup', cleanup_fails)
    with pytest.raises(RuntimeError, match='schema is not13'):
        host.main()
    saved = json.loads((host.OUT / 'summary.json').read_text())
    assert saved['status'] == 'failed' and 'schema is not13' in saved['first_failure']
    assert 'failed' in saved['owned_cleanup']


def test_dockerfile_pin_attribution_and_locked_minimal_image():
    source = (ROOT / 'infra/planner/Dockerfile.ci').read_text()
    assert 'python:3.12.13-slim@sha256:d657ab0ade19f404a6ccc883ab399540de667aff751748ce23c07330c5a89e64' in source
    assert 'https://hub.docker.com/_/python' in source and '3362634339580d3232e65a66dd5a36c47ae7ff14' in source
    assert 'uv==0.11.25' in source and 'uv sync --locked --no-dev' in source
    assert 'apt-get' not in source and 'verilator' not in source.lower()


def test_workflow_is_manual_main_only_readonly_and_always_scoped_cleanup():
    source = (ROOT / '.github/workflows/planner-postgres-acceptance.yml').read_text()
    assert 'workflow_dispatch:' in source and 'inputs:' not in source and 'push:' not in source and 'pull_request:' not in source
    assert "github.ref == 'refs/heads/main'" in source and 'contents: read' in source
    assert 'persist-credentials: false' in source and 'secrets.' not in source
    assert 'timeout-minutes: 30' in source and 'retention-days: 1' in source
    assert source.index('ci_planner_postgres.py --cleanup') < source.index('uses: actions/upload-artifact')
    assert all(re.fullmatch('[a-f0-9]{40}', digest) for digest in re.findall(r'uses: [^@\s]+@([^\s]+)', source))


@pytest.mark.parametrize('table', fixture.SAFE_FIELDS)
def test_snapshot_allowlist_excludes_authority_and_future_columns(table):
    row = {key: 'safe' for key in fixture.SAFE_FIELDS[table]}
    row.update(owner_token='raw-authority', lease='raw-lease', execution_lease='raw-execution',
               incarnation='raw-owner', request_key='raw-key', request_body='private', future_secret='private')
    assert fixture.safe_row(table, row) == {key: 'safe' for key in fixture.SAFE_FIELDS[table]}


def test_all_service_log_caps_are_explicit_and_uncompressed(tmp_path):
    plan = host.compose_plan('vf-planner-pg-ci-123-1', 'image', tmp_path, tmp_path)
    assert all(value['logging'] == {'driver': 'local', 'options': {'max-size': '1m', 'max-file': '1', 'compress': 'false'}}
               for value in plan['services'].values())


@pytest.mark.parametrize('broken', ['volume', 'network', 'buildx'])
def test_cleanup_requires_positive_inventories_and_builder_absence(monkeypatch, tmp_path, broken):
    project = 'vf-planner-pg-ci-123-1'
    monkeypatch.setattr(host, 'ROOT', tmp_path)
    monkeypatch.setattr(host, 'OUT', tmp_path / 'evidence')
    work = tmp_path / '.planner-pg-ci' / project
    host.write(work / 'owner.json', {'project': project})
    host.write(work / 'builder-owned.json', {'builder': project + '-builder'})
    def fake(args, **kwargs):
        return SimpleNamespace(returncode=1 if args[1] == broken else 0, stdout=b'')
    monkeypatch.setattr(host.subprocess, 'run', fake)
    with pytest.raises(RuntimeError, match='absence unknown'):
        host.cleanup(project, work)
    assert not (host.OUT / 'cleanup.json').exists()


def test_final_receipt_exact_bytes_and_failure_metadata(monkeypatch, tmp_path):
    monkeypatch.setattr(host, 'OUT', tmp_path)
    (tmp_path / 'other.bin').write_bytes(b'1234567')
    monkeypatch.setattr(host, 'cleanup', lambda *_: None)
    monkeypatch.setattr(host, 'STOP', False)
    monkeypatch.setattr(host, 'SHARED', SimpleNamespace(STOP=False, evidence_size=lambda out: sum(p.stat().st_size for p in out.rglob('*') if p.is_file())))
    summary = {'status': 'checks_passed_pending_cleanup'}
    assert host.finish_owned('project', tmp_path, summary, None, float('inf')) is None
    saved = json.loads((tmp_path / 'summary.json').read_text())
    assert saved['status'] == 'passed'
    assert saved['evidence_bytes'] == sum(p.stat().st_size for p in tmp_path.rglob('*') if p.is_file())


@pytest.mark.parametrize('fault', ['measure', 'write', 'oversize'])
def test_finalizer_preserves_first_failure_on_evidence_fault(monkeypatch, tmp_path, fault):
    monkeypatch.setattr(host, 'OUT', tmp_path)
    monkeypatch.setattr(host, 'STOP', False)
    monkeypatch.setattr(host, 'cleanup', lambda *_: None)
    def measure(out):
        if fault == 'measure':
            raise OSError('unavailable measurement')
        return host.CAP + 1 if fault == 'oversize' else 0
    monkeypatch.setattr(host, 'SHARED', SimpleNamespace(STOP=False, evidence_size=measure))
    if fault == 'write':
        monkeypatch.setattr(Path, 'write_bytes', lambda *_: (_ for _ in ()).throw(OSError('unavailable persistence')))
    first = RuntimeError('original cause')
    assert host.finish_owned('project', tmp_path, {'status': 'started'}, first, float('inf')) is first
    assert first.__notes__
    if fault != 'write':
        saved = json.loads((tmp_path / 'summary.json').read_text())
        assert saved['status'] == 'failed' and saved['first_failure'] == 'RuntimeError: original cause'
        assert saved['evidence_budget_status'] == ('unknown' if fault == 'measure' else 'exceeded')
        if fault == 'oversize':
            assert saved['evidence_bytes'] > host.CAP


@pytest.mark.parametrize('mutation', ['remove', 'refund', 'change_identity'])
def test_prior_reservation_survives_known_and_unknown_outcomes(mutation):
    controller = fixture.Qualification.__new__(fixture.Qualification)
    original = {'id': 'reservation', 'round_id': 'round', 'reserved_tokens': 100}
    controller.baseline_reservations = {'reservation': original}
    controller.baseline_calls = {'round': {'round_id': 'round', 'outcome': 'remote_outcome_unknown'}}
    result = {'planner_model_calls': list(controller.baseline_calls.values()), 'planner_reservations': [dict(original)]}
    controller.validate_history(result)
    if mutation == 'remove':
        result['planner_reservations'] = []
    elif mutation == 'refund':
        result['planner_reservations'][0]['reserved_tokens'] = 0
    else:
        result['planner_reservations'][0]['round_id'] = 'another-round'
    with pytest.raises(RuntimeError, match='reservation changed/refunded'):
        controller.validate_history(result)


def test_cleanup_requires_post_remove_builder_absence(monkeypatch, tmp_path):
    project = 'vf-planner-pg-ci-123-1'
    monkeypatch.setattr(host, 'ROOT', tmp_path)
    monkeypatch.setattr(host, 'OUT', tmp_path / 'evidence')
    work = tmp_path / '.planner-pg-ci' / project
    host.write(work / 'owner.json', {'project': project})
    host.write(work / 'builder-owned.json', {'builder': project + '-builder'})
    def fake(args, **kwargs):
        return SimpleNamespace(returncode=0, stdout=(project + '-builder').encode() if args[1:3] == ['buildx', 'ls'] else b'')
    monkeypatch.setattr(host.subprocess, 'run', fake)
    with pytest.raises(RuntimeError, match='builder remains'):
        host.cleanup(project, work)
