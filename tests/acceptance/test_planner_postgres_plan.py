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
    plan = host.compose_plan(project, 'sha256:' + 'a' * 64, tmp_path / 'controls', tmp_path / 'evidence', '1001:123')
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
    plan = host.compose_plan('vf-planner-pg-ci-123-1', 'image', tmp_path, tmp_path, '1001:123')
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


@pytest.mark.parametrize('user', ['0:0', '1;exec:2', 'user:group', '1001:-1', '4294967295:1', None])
def test_bind_identity_is_numeric_nonroot_and_bounded(user, tmp_path):
    with pytest.raises(RuntimeError, match='identity|user'):
        host.compose_plan('vf-planner-pg-ci-123-1', 'image', tmp_path, tmp_path, user)


def test_all_application_roles_use_observed_identity_without_changing_storage_identity(tmp_path):
    plan = host.compose_plan('vf-planner-pg-ci-123-1', 'image', tmp_path, tmp_path, '4321:987')
    assert all(plan['services'][role]['user'] == '4321:987' for role in host.WRITING_ROLES)
    assert all('user' not in plan['services'][role] for role in ('postgres', 'valkey'))
    assert all(plan['services'][role]['cap_drop'] == ['ALL'] and
               plan['services'][role]['security_opt'] == ['no-new-privileges:true'] for role in host.WRITING_ROLES)


def test_prepare_creates_only_new_owned_evidence_directory(monkeypatch, tmp_path):
    monkeypatch.setattr(host, 'ROOT', tmp_path)
    monkeypatch.setattr(host, 'OUT', tmp_path / 'output')
    host.OUT.mkdir()
    monkeypatch.setattr(host, 'effective_identity', lambda: (4321, 987))
    monkeypatch.setattr(host, 'directory_identity', lambda path: {'uid': 4321, 'gid': 987, 'mode': '0700'})
    evidence, user = host.prepare_evidence_mount()
    assert evidence == (host.OUT / 'data').resolve() and evidence.is_dir() and user == '4321:987'
    assert json.loads((host.OUT / 'evidence-mount.json').read_text())['directory']['mode'] == '0700'
    (evidence / 'keep').write_text('existing data')
    with pytest.raises(RuntimeError, match='already exists'):
        host.prepare_evidence_mount()
    assert (evidence / 'keep').read_text() == 'existing data'


def test_prepare_rejects_foreign_or_broad_directory_mode(monkeypatch, tmp_path):
    monkeypatch.setattr(host, 'ROOT', tmp_path)
    monkeypatch.setattr(host, 'OUT', tmp_path / 'output')
    host.OUT.mkdir()
    monkeypatch.setattr(host, 'effective_identity', lambda: (4321, 987))
    monkeypatch.setattr(host, 'directory_identity', lambda path: {'uid': 0, 'gid': 987, 'mode': '0777'})
    with pytest.raises(RuntimeError, match='identity/mode differs'):
        host.prepare_evidence_mount()
    assert not (host.OUT / 'evidence-mount.json').exists()


def test_preflight_exclusive_write_read_list_delete_and_preserve_existing_file(monkeypatch, tmp_path):
    monkeypatch.setattr(host, 'effective_identity', lambda: (4321, 987))
    monkeypatch.setattr(host, 'directory_identity', lambda path: {'uid': 4321, 'gid': 987, 'mode': '0700'})
    proof = host.mount_preflight(tmp_path, '4321:987', 'controller')
    assert proof['probe'] == 'exclusive_write_read_list_delete_passed' and not list(tmp_path.iterdir())
    existing = tmp_path / '.veriforge-controller-mount-probe'
    existing.write_bytes(b'preserve prior owner file')
    with pytest.raises(FileExistsError):
        host.mount_preflight(tmp_path, '4321:987', 'controller')
    assert existing.read_bytes() == b'preserve prior owner file'


@pytest.mark.parametrize('mismatch', ['uid', 'owner', 'mode'])
def test_preflight_denies_identity_and_permission_mismatch_before_writes(monkeypatch, tmp_path, mismatch):
    monkeypatch.setattr(host, 'effective_identity', lambda: (4322 if mismatch == 'uid' else 4321, 987))
    monkeypatch.setattr(host, 'directory_identity', lambda path: {'uid': 0 if mismatch == 'owner' else 4321,
                                                                'gid': 987, 'mode': '0755' if mismatch == 'mode' else '0700'})
    with pytest.raises(RuntimeError, match='differs'):
        host.mount_preflight(tmp_path, '4321:987', 'runtime-c')
    assert not list(tmp_path.iterdir())


def test_preflight_keeps_first_error_when_probe_delete_also_fails(monkeypatch, tmp_path):
    monkeypatch.setattr(host, 'effective_identity', lambda: (4321, 987))
    monkeypatch.setattr(host, 'directory_identity', lambda path: {'uid': 4321, 'gid': 987, 'mode': '0700'})
    original_unlink = Path.unlink
    with monkeypatch.context() as patch:
        patch.setattr(Path, 'iterdir', lambda *_: (_ for _ in ()).throw(OSError('first listing failure')))
        patch.setattr(Path, 'unlink', lambda *_: (_ for _ in ()).throw(PermissionError('secondary delete failure')))
        with pytest.raises(OSError, match='first listing failure') as captured:
            host.mount_preflight(tmp_path, '4321:987', 'mock')
        assert captured.value.__notes__ == ['Probe deletion also failed: PermissionError']
    original_unlink(tmp_path / '.veriforge-mock-mount-probe')


def test_each_role_gets_bounded_preflight_before_service_start(monkeypatch, tmp_path):
    monkeypatch.setattr(host, 'OUT', tmp_path)
    calls = []
    class FakeHost:
        prefix = ['docker', 'compose', '-p', 'owned-project']
        def run(self, command, name, timeout):
            calls.append((command, name, timeout))
            role = command[command.index('-c') + 2]
            code = command[command.index('-c') + 1]
            compile(code, '<published-preflight>', 'exec')
            assert 'print(' + repr(host.PROOF_MARKER) + ' + json.dumps(' in code
            paths = command[command.index('-c') + 4:]
            return (host.PROOF_MARKER + json.dumps({'role': role, 'mounts': [{'directory': path, 'effective_uid': 4321,
                'effective_gid': 987, 'observed': {'uid': 4321, 'gid': 987, 'mode': '0700'},
                'probe': 'exclusive_write_read_list_delete_passed'} for path in paths]})).encode()
    host.preflight_writing_roles(FakeHost(), '4321:987')
    assert len(calls) == len(host.WRITING_ROLES) == 6
    for (command, name, timeout), role in zip(calls, host.WRITING_ROLES):
        assert command[:4] == FakeHost.prefix and '--no-deps' in command
        assert '--no-build' not in command and '--build' not in command
        assert command[command.index('--pull') + 1] == 'never' and '--entrypoint' in command
        assert name == 'mount-preflight-' + role and timeout == 15
        assert ('/controls' in command) == (role == 'controller')
        assert (tmp_path / (name + '.json')).is_file()
    source = (ROOT / 'scripts/ci_planner_postgres.py').read_text()
    assert source.index('        preflight_writing_roles(host, user)') < source.index("        host.run(host.prefix + ['up', '-d', '--no-deps', '--no-build', 'postgres'")


def test_failed_role_preflight_stops_before_remaining_roles(monkeypatch, tmp_path):
    monkeypatch.setattr(host, 'OUT', tmp_path)
    calls = []
    class FakeHost:
        prefix = ['docker', 'compose']
        def run(self, command, name, timeout):
            calls.append(name)
            raise PermissionError('mount denied')
    with pytest.raises(PermissionError, match='mount denied'):
        host.preflight_writing_roles(FakeHost(), '4321:987')
    assert calls == ['mount-preflight-mock'] and not list(tmp_path.iterdir())


# Exact Docker prefix retained from run40952295201; following marked proof is
# synthetic new-protocol output, not a claim the old run emitted this marker.
RETAINED_DOCKER_PREFIX = (b' Network vf-planner-pg-ci-40952295201-1_default  Creating\n'
                          b' Network vf-planner-pg-ci-40952295201-1_default  Created\n')


def proof_payload(role='mock'):
    paths = ['/evidence', '/controls'] if role == 'controller' else ['/evidence']
    return {'role': role, 'mounts': [{'directory': path, 'effective_uid': 1001, 'effective_gid': 1001,
            'observed': {'uid': 1001, 'gid': 1001, 'mode': '0700'},
            'probe': 'exclusive_write_read_list_delete_passed'} for path in paths]}


def marked_proof(payload):
    return host.PROOF_MARKER.encode() + json.dumps(payload).encode() + b'\n'


def test_marked_proof_accepts_exact_retained_docker_progress_without_changing_raw_log():
    payload = proof_payload()
    raw = RETAINED_DOCKER_PREFIX + marked_proof(payload) + b'other Docker progress {not proof}\n'
    original = bytes(raw)
    assert host.parse_mount_proof(raw, 'mock', '1001:1001', ['/evidence']) == payload
    assert raw == original


@pytest.mark.parametrize('bad', ['missing', 'unmarked_json', 'duplicate', 'malformed', 'unknown_version',
                               'leading_space', 'trailing_json', 'duplicate_key', 'nonfinite', 'oversize_line', 'oversize_output'])
def test_marked_proof_rejects_missing_duplicate_or_malformed_protocol(bad):
    valid = marked_proof(proof_payload())
    cases = {
        'missing': RETAINED_DOCKER_PREFIX,
        'unmarked_json': RETAINED_DOCKER_PREFIX + json.dumps(proof_payload()).encode(),
        'duplicate': valid + valid,
        'malformed': host.PROOF_MARKER.encode() + b'{broken}\n',
        'unknown_version': valid.replace(b'_V1=', b'_V2='),
        'leading_space': b' ' + valid,
        'trailing_json': valid.rstrip() + b' {}\n',
        'duplicate_key': valid.replace(b'"role": "mock"', b'"role": "mock", "role": "mock"'),
        'nonfinite': valid.replace(b'1001', b'NaN', 1),
        'oversize_line': host.PROOF_MARKER.encode() + b' ' * 4096 + b'{}',
        'oversize_output': b'x' * (1024 * 1024) + valid,
    }
    with pytest.raises((RuntimeError, ValueError)):
        host.parse_mount_proof(cases[bad], 'mock', '1001:1001', ['/evidence'])


@pytest.mark.parametrize('bad', ['role', 'path', 'uid', 'gid', 'mode', 'observed_owner', 'missing_observed',
                               'unknown_key', 'unknown_mount_key', 'bool_uid', 'probe', 'mount_count'])
def test_marked_proof_rejects_unknown_schema_or_wrong_association(bad):
    payload = proof_payload()
    mount = payload['mounts'][0]
    if bad == 'role': payload['role'] = 'runtime-a'
    elif bad == 'path': mount['directory'] = '/controls'
    elif bad == 'uid': mount['effective_uid'] = 1002
    elif bad == 'gid': mount['effective_gid'] = 1002
    elif bad == 'mode': mount['observed']['mode'] = '0777'
    elif bad == 'observed_owner': mount['observed']['uid'] = 0
    elif bad == 'missing_observed': del mount['observed']
    elif bad == 'unknown_key': payload['untrusted'] = True
    elif bad == 'unknown_mount_key': mount['untrusted'] = True
    elif bad == 'bool_uid': mount['effective_uid'] = True
    elif bad == 'probe': mount['probe'] = 'not_performed'
    else: payload['mounts'].append(dict(mount))
    with pytest.raises(RuntimeError):
        host.parse_mount_proof(marked_proof(payload), 'mock', '1001:1001', ['/evidence'])


def test_marked_controller_proof_requires_both_ordered_mounts():
    payload = proof_payload('controller')
    assert host.parse_mount_proof(marked_proof(payload), 'controller', '1001:1001', ['/evidence', '/controls']) == payload
    payload['mounts'].reverse()
    with pytest.raises(RuntimeError, match='association'):
        host.parse_mount_proof(marked_proof(payload), 'controller', '1001:1001', ['/evidence', '/controls'])



def resource_graph(chain=False):
    barrier = {'pid': 900, 'xid': '77', 'page': 0, 'tuple': 1}
    expected = {'intent-a': 'round-a', 'intent-b': 'round-b'}
    relation = {'locktype': 'relation', 'mode': 'RowShareLock', 'granted': True,
                'xid': None, 'resource_relation': True, 'page': None, 'tuple': None}
    xid = {'locktype': 'transactionid', 'mode': 'ShareLock', 'granted': False,
           'xid': '77', 'resource_relation': None, 'page': None, 'tuple': None}
    holder = {'pid': 900, 'application_name': 'pg-ci-controller', 'state': 'idle in transaction',
              'xid': '77', 'blockers': [], 'locks': [dict(relation), {**xid, 'mode': 'ExclusiveLock', 'granted': True}]}
    nodes = [holder]
    for index, role in enumerate(('runtime-a', 'runtime-b')):
        waiting = {**xid, 'locktype': 'tuple', 'resource_relation': True, 'page': 0, 'tuple': 1} if chain and index else dict(xid)
        nodes.append({'pid': 901 + index, 'application_name': 'pg-ci-' + role, 'state': 'active', 'xid': None,
                      'wait_event_type': 'Lock', 'wait_event': 'tuple' if chain and index else 'transactionid',
                      'blockers': [901 if chain and index else 900], 'locks': [dict(relation), waiting]})
    observation = {'backends': nodes,
        'pending': [{'intent': key, 'round': value, 'status': 'queued', 'started_at': None, 'outcome': None} for key, value in expected.items()],
        'jobs': {role: {'job_id': 'job-' + key, 'intent': 'intent-' + key, 'queue': 'veriforge-planner'}
                 for role, key in [('runtime-a', 'a'), ('runtime-b', 'b')]},
        'deliveries': {'job-a': 'intent-a', 'job-b': 'intent-b'}, 'completed_intents': []}
    return observation, expected, barrier


@pytest.mark.parametrize('chain', [False, True])
def test_resource_graph_proves_two_exact_owned_waiters_direct_or_chained(chain):
    observation, expected, barrier = resource_graph(chain)
    assert fixture.validate_resource_graph(observation, expected, barrier)


@pytest.mark.parametrize('bad', ['missing_role', 'wrong_role', 'unblocked', 'cycle', 'foreign', 'disconnected',
    'wrong_resource', 'wrong_tuple', 'wrong_xid', 'barrier_released', 'barrier_stale', 'barrier_wrong_resource',
    'wrong_job', 'wrong_intent', 'wrong_round', 'stale_job', 'completed_job', 'call_started', 'duplicate_intent',
    'backend_cap', 'lock_cap'])
def test_resource_graph_rejects_unrelated_stale_or_adversarial_waits(bad):
    observation, expected, barrier = resource_graph(True)
    node = observation['backends'][2]
    job = observation['jobs']['runtime-b']
    if bad == 'missing_role': observation['backends'].pop()
    elif bad == 'wrong_role': node['application_name'] = 'foreign'
    elif bad == 'unblocked': node['wait_event_type'] = 'Client'
    elif bad == 'cycle': observation['backends'][1]['blockers'] = [902]
    elif bad == 'foreign': node['blockers'] = [999]
    elif bad == 'disconnected': node['blockers'] = []
    elif bad == 'wrong_resource': node['locks'][-1]['resource_relation'] = False
    elif bad == 'wrong_tuple': node['locks'][-1]['tuple'] = 2
    elif bad == 'wrong_xid': observation['backends'][1]['locks'][-1]['xid'] = '88'
    elif bad == 'barrier_released': observation['backends'][0]['state'] = 'idle'
    elif bad == 'barrier_stale': observation['backends'][0]['xid'] = '88'
    elif bad == 'barrier_wrong_resource': observation['backends'][0]['locks'][0]['resource_relation'] = False
    elif bad == 'wrong_job': job['job_id'] = 'foreign-job'
    elif bad == 'wrong_intent': job['intent'] = 'foreign-intent'
    elif bad == 'wrong_round': observation['pending'][1]['round'] = 'foreign-round'
    elif bad == 'stale_job': job['queue'] = 'veriforge'
    elif bad == 'completed_job': observation['completed_intents'] = ['intent-b']
    elif bad == 'call_started': observation['pending'][1]['started_at'] = 'already-started'
    elif bad == 'duplicate_intent': job.update(observation['jobs']['runtime-a'])
    elif bad == 'backend_cap': observation['backends'] *= 4
    else: node['locks'] *= 33
    with pytest.raises(RuntimeError):
        fixture.validate_resource_graph(observation, expected, barrier)


def test_resource_graph_last_observation_retained_on_timeout_without_relaxing_deadline(monkeypatch, tmp_path):
    observation, expected, barrier = resource_graph(True)
    observation['backends'][2]['blockers'] = [999]
    controller = fixture.Qualification.__new__(fixture.Qualification)
    controller.deliveries = [{'job_id': key, 'args': (value,)} for key, value in observation['deliveries'].items()]
    class Connection:
        def __enter__(self): return self
        def __exit__(self, *_): pass
        def scalar(self, query, params):
            assert 'pg_locks' in str(query) and 'query' not in str(query).split('FROM pg_stat_activity')[0]
            return {key: observation[key] for key in ('backends', 'pending')}
    controller.engine = SimpleNamespace(connect=lambda: Connection())
    monkeypatch.setattr(fixture, 'OUT', tmp_path)
    for role in ('runtime-a', 'runtime-b'):
        fixture.write(tmp_path / (role + '-active-job.json'), observation['jobs'][role])
    values = tuple({'intent': key, 'round': value} for key, value in expected.items())
    assert controller.resource_waiters(values, barrier) is None
    saved = json.loads((tmp_path / 'actual-pg-resource-last-observation.json').read_text())
    assert 'foreign/disconnected/cyclic' in saved['validation'] and saved['backends'][2]['blockers'] == [999]
    source = (ROOT / 'tests/integration/planner_process_fixture.py').read_text()
    assert 'wait_for(lambda: self.resource_waiters((a, b), identity), 2)' in source
    assert 'CASE_DEADLINE = time.monotonic() + 45' in source
    assert "len(self.requests(a)) + len(self.requests(b)) == 1" in source
