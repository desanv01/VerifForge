"""Independent frozen-source review; temp files/fake calls, no services or sockets."""
import hashlib
import importlib.util
import json
from pathlib import Path
import subprocess
from types import SimpleNamespace

import pytest

ROOT = Path(__file__).resolve().parents[2]
# Reviewed source pins use LF text identity across Git checkouts; tool evidence remains raw-byte hashed.
FROZEN = {
    'scripts/ci_planner_postgres.py': '949960d9692b0f68574425dc7d6bc717e6fcac4723500e35c6fed35b44e8078a',
    'tests/integration/planner_process_fixture.py': 'c64e7b2ae0303d0fa35735ccf5db53d92d01e49016f6431323e535d8a4910ec3',
    'infra/planner/Dockerfile.ci': 'cdad35f0edf16d4d7ae50da2a01b00e3c65f404afd4133ee24d9a1b6c9285526',
    '.github/workflows/planner-postgres-acceptance.yml': '85e3250990212537bb4e5d0cf4c70106e9bdc11e41d51bf94febeb471ca83a39',
    'tests/acceptance/test_planner_postgres_plan.py': '69d607889ecb8199770fff411c7838f3bf09b36686259fbc35796be86adee214',
    'scripts/ci_axi_application.py': '1ef06a8e1fb855724d919046b8b1835edd01874ffc538280ab8d1f2dbdeb9624',
}
PROJECT = 'vf-planner-pg-ci-123456789-1'


@pytest.fixture
def modules(monkeypatch, tmp_path):
    for name, digest in FROZEN.items():
        assert hashlib.sha256((ROOT / name).read_bytes().replace(b'\r\n', b'\n')).hexdigest() == digest, name
    def forbidden(*args, **kwargs):
        pytest.fail('independent review cannot launch processes or HTTP')
    for name in ('run', 'Popen', 'check_output'):
        monkeypatch.setattr(subprocess, name, forbidden)
    loaded = []
    for index, name in enumerate(('scripts/ci_planner_postgres.py', 'tests/integration/planner_process_fixture.py')):
        spec = importlib.util.spec_from_file_location('independent_pg_' + str(index), ROOT / name)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        loaded.append(module)
    host, fixture = loaded
    monkeypatch.setattr(fixture, 'urlopen', forbidden)
    monkeypatch.setattr(host, 'OUT', tmp_path / 'evidence')
    monkeypatch.setattr(fixture, 'OUT', tmp_path / 'evidence')
    return host, fixture


def test_topology_has_only_bounded_private_fixture_roles(modules, tmp_path):
    host, fixture = modules
    controls, evidence = tmp_path / 'controls', tmp_path / 'evidence'
    plan = host.compose_plan(PROJECT, 'sha256:' + 'c' * 64, controls, evidence)
    assert set(plan['services']) == {'postgres', 'valkey', 'mock', 'api', 'runtime-a', 'runtime-b', 'runtime-c', 'controller'}
    assert plan['networks']['default']['internal'] is True
    assert plan['networks']['default']['labels'][host.OWNER] == PROJECT
    for role, service in plan['services'].items():
        assert not {'ports', 'env_file', 'privileged', 'build'} & service.keys()
        assert service['restart'] == 'no' and service['pids_limit'] <= 128
        assert service['cpus'] <= 2 and service['mem_limit'] in ('128m', '256m', '512m', '768m')
        assert service['logging'] == {'driver': 'local', 'options': {'max-size': '1m', 'max-file': '1', 'compress': 'false'}}
        assert service['labels'][host.OWNER] == PROJECT
        if role not in ('postgres', 'valkey'):
            assert service['cap_drop'] == ['ALL'] and service['security_opt'] == ['no-new-privileges:true']
            control = str(controls) + ':/controls' + ('' if role == 'controller' else ':ro')
            assert control in service['volumes']
            assert all('docker.sock' not in value for value in service['volumes'])
            assert not any('KEY' in key or 'TOKEN' in key for key in service['environment'])
    assert host.LIMITS == {'rounds': 8, 'deliveries': 12, 'mock_requests': 7, 'native_runs': 0, 'real_provider_calls': 0}
    assert fixture.MODEL == 'ci-explicit-mock-no-live-meta'


@pytest.mark.parametrize('field,value', [('project', 'foreign'), ('sequence', True), ('sequence', 0),
    ('action', 'shell'), ('service', 'postgres'), ('service', '../runtime-a')])
def test_supervisor_rejects_unowned_or_untyped_actions(modules, field, value):
    host, _ = modules
    request = {'project': PROJECT, 'sequence': 1, 'action': 'kill', 'service': 'runtime-a'}
    request[field] = value
    with pytest.raises(RuntimeError):
        host.supervisor_action(PROJECT, request, set())


def test_supervisor_actions_are_ordered_and_finite(modules):
    host, _ = modules
    first = {'project': PROJECT, 'sequence': 1, 'action': 'kill', 'service': 'runtime-b'}
    second = {'project': PROJECT, 'sequence': 2, 'action': 'start-blocked', 'service': 'runtime-c'}
    assert host.supervisor_action(PROJECT, first, set()) == 'runtime-b'
    assert host.supervisor_action(PROJECT, second, {1}) == 'runtime-c'
    for request, completed in ((first, {1}), (second, set()), (second, {1, 2}), ({**first, 'args': []}, set())):
        with pytest.raises(RuntimeError):
            host.supervisor_action(PROJECT, request, completed)


@pytest.mark.parametrize('table', ['campaigns', 'attempts', 'planner_rounds', 'planner_model_calls',
    'planner_cleanup', 'planner_reservations', 'planner_intents'])
def test_snapshot_never_exports_capability_or_future_fields(modules, table):
    _, fixture = modules
    private = {name: 'UNEXPORTABLE-' + name for name in ('owner_token', 'execution_lease', 'lease',
        'incarnation', 'request_key', 'request_body', 'context', 'future_authority')}
    row = {**private, 'id': 'safe-id', 'round_id': 'safe-round', 'campaign_id': 'safe-campaign'}
    copy = dict(row)
    safe = fixture.safe_row(table, row)
    assert not private.keys() & safe.keys() and 'UNEXPORTABLE' not in json.dumps(safe)
    assert safe['campaign_id'] == 'safe-campaign' if 'campaign_id' in fixture.SAFE_FIELDS[table] else 'campaign_id' not in safe
    assert row == copy


@pytest.mark.parametrize('mutation', ['refund', 'delete', 'move-round', 'change-cost', 'old-outcome'])
def test_prior_reservations_and_outcomes_cannot_be_rewritten(modules, mutation):
    _, fixture = modules
    controller = fixture.Qualification.__new__(fixture.Qualification)
    reservation = {'id': 'r', 'round_id': 'round', 'campaign_id': 'campaign', 'reserved_tokens': 900,
        'reserved_seconds': '5', 'reserved_cost_usd': '0.25'}
    call = {'round_id': 'round', 'outcome': 'remote_outcome_unknown', 'request_hash': 'a' * 64}
    controller.baseline_reservations = {'r': reservation}
    controller.baseline_calls = {'round': call}
    result = {'planner_reservations': [dict(reservation)], 'planner_model_calls': [dict(call)]}
    controller.validate_history(result)
    if mutation == 'refund': result['planner_reservations'][0]['reserved_tokens'] = 0
    elif mutation == 'delete': result['planner_reservations'].clear()
    elif mutation == 'move-round': result['planner_reservations'][0]['round_id'] = 'other'
    elif mutation == 'change-cost': result['planner_reservations'][0]['reserved_cost_usd'] = '0'
    else: result['planner_model_calls'][0]['outcome'] = 'success'
    with pytest.raises(RuntimeError):
        controller.validate_history(result)


@pytest.mark.parametrize('fault', ['success', 'primary', 'cleanup', 'both', 'measure', 'overcap', 'cancel', 'shared-cancel', 'deadline', 'incomplete'])
def test_final_receipt_counts_every_final_byte_and_preserves_first_failure(modules, monkeypatch, fault):
    host, _ = modules
    host.OUT.mkdir()
    native = b'SYNTHETIC PRESERVED BYTES\x00\xff'
    (host.OUT / 'data.bin').write_bytes(native)
    (host.OUT / 'summary.json').write_bytes(b'previous receipt must not be counted twice')
    primary = ValueError('first failure \u00e9 \U0001f41f') if fault in ('primary', 'both', 'overcap', 'measure') else None
    secondary = OSError('secondary cleanup')
    def cleanup(*args):
        if fault in ('cleanup', 'both'): raise secondary
    monkeypatch.setattr(host, 'cleanup', cleanup)
    measurement = OSError('measurement unavailable')
    def measure(out):
        if fault == 'measure': raise measurement
        return sum(p.stat().st_size for p in out.rglob('*') if p.is_file())
    monkeypatch.setattr(host, 'SHARED', SimpleNamespace(STOP=fault == 'shared-cancel', evidence_size=measure))
    monkeypatch.setattr(host, 'STOP', fault == 'cancel')
    if fault == 'overcap': monkeypatch.setattr(host, 'CAP', 1)
    summary = {'status': 'started' if fault == 'incomplete' else 'checks_passed_pending_cleanup'}
    returned = host.finish_owned(PROJECT, host.OUT, summary, primary, 0 if fault == 'deadline' else float('inf'))
    raw = (host.OUT / 'summary.json').read_bytes()
    assert json.loads(raw) == summary and raw.endswith(b'\n') and b'\r\n' not in raw
    assert (host.OUT / 'data.bin').read_bytes() == native
    if fault == 'measure':
        assert summary['evidence_bytes'] is None and summary['evidence_budget_status'] == 'unknown'
    else:
        assert summary['evidence_bytes'] == sum(p.stat().st_size for p in host.OUT.rglob('*') if p.is_file())
    assert summary['status'] == ('passed' if fault == 'success' else 'failed')
    if primary is not None: assert returned is primary
    if fault == 'cleanup': assert returned is secondary
    if fault == 'both': assert any('cleanup' in note for note in primary.__notes__)
    if fault == 'overcap': assert summary['evidence_bytes'] > host.CAP and summary['evidence_budget_status'] == 'exceeded'
    if fault == 'success': assert returned is None


@pytest.mark.parametrize('has_primary', [False, True])
def test_summary_write_failure_cannot_return_success_or_replace_primary(modules, monkeypatch, has_primary):
    host, _ = modules
    monkeypatch.setattr(host, 'cleanup', lambda *args: None)
    monkeypatch.setattr(host, 'SHARED', SimpleNamespace(STOP=False, evidence_size=lambda out: 0))
    primary = RuntimeError('first') if has_primary else None
    persistence = OSError('disk unavailable')
    def broken(*args): raise persistence
    monkeypatch.setattr(Path, 'write_bytes', broken)
    result = host.finish_owned(PROJECT, host.OUT, {'status': 'checks_passed_pending_cleanup'}, primary, float('inf'))
    assert result is (primary if has_primary else persistence)
    if has_primary: assert any('persistence' in note for note in primary.__notes__)


@pytest.mark.parametrize('adjustment', [0, -1])
def test_receipt_exact_cap_and_one_byte_excess(modules, monkeypatch, adjustment):
    host, _ = modules
    host.OUT.mkdir()
    native = b'x' * 9800
    (host.OUT / 'retained.bin').write_bytes(native)
    monkeypatch.setattr(host, 'cleanup', lambda *args: None)
    monkeypatch.setattr(host, 'SHARED', SimpleNamespace(STOP=False,
        evidence_size=lambda out: sum(p.stat().st_size for p in out.rglob('*') if p.is_file())))
    cap = len(native)
    for _ in range(12):
        expected = {'status': 'passed', 'owned_cleanup': 'passed', 'evidence_bytes': cap,
            'evidence_budget_bytes': cap, 'evidence_budget_status': 'within_budget'}
        actual = len(native) + len((json.dumps(expected, indent=2, sort_keys=True) + '\n').encode())
        if actual == cap: break
        cap = actual
    else: pytest.fail('independent byte boundary did not converge')
    monkeypatch.setattr(host, 'CAP', cap + adjustment)
    summary = {'status': 'checks_passed_pending_cleanup'}
    returned = host.finish_owned(PROJECT, host.OUT, summary, None, float('inf'))
    actual = sum(p.stat().st_size for p in host.OUT.rglob('*') if p.is_file())
    assert actual == summary['evidence_bytes']
    if adjustment == 0:
        assert returned is None and actual == cap and summary['status'] == 'passed'
    else:
        assert returned is not None and actual > host.CAP and summary['status'] == 'failed'
        assert summary['evidence_budget_status'] == 'exceeded'
    assert (host.OUT / 'retained.bin').read_bytes() == native


@pytest.mark.parametrize('stage', ['build', 'migrate13', 'model-schema'])
def test_failed_host_phases_keep_every_identity_known_before_failure(modules, monkeypatch, tmp_path, stage):
    host, _ = modules
    monkeypatch.setattr(host, 'ROOT', tmp_path)
    monkeypatch.setattr(host.sys, 'platform', 'linux')
    for key, value in {'GITHUB_ACTIONS': 'true', 'GITHUB_EVENT_NAME': 'workflow_dispatch',
        'GITHUB_REF': 'refs/heads/main', 'GITHUB_RUN_ID': '123456789', 'GITHUB_RUN_ATTEMPT': '1'}.items():
        monkeypatch.setenv(key, value)
    primary = RuntimeError('first synthetic stage ' + stage)
    commit, image = 'a' * 40, 'sha256:' + 'b' * 64
    reached = []
    class FakeHost:
        def __init__(self, project, work):
            self.builder = project + '-builder'
            self.prefix, self.commands = ['FAKE-COMPOSE'], []
        def run(self, args, name, timeout=90):
            reached.append(name)
            if name == stage: raise primary
            if name == 'builder-caps':
                (host.OUT / 'builder-caps.log').write_bytes(b'synthetic')
                return json.dumps({'Memory': 4 * 1024**3, 'MemorySwap': 4 * 1024**3,
                    'CpuQuota': 200000, 'CpuPeriod': 100000}).encode()
            return {'builder-stopped': b'false', 'image': image.encode(),
                'empty-schema': b'0', 'actual-revision': b'13engineeringprojects'}.get(name, b'')
    def sources(work):
        host.write(host.OUT / 'source-manifest.json', {'files': {}})
        return commit, '13engineeringprojects'
    shared = SimpleNamespace(Host=FakeHost, STOP=False, BUILDKIT='SYNTHETIC-NO-IMAGE',
        stage_sources=sources, evidence_size=lambda out: sum(p.stat().st_size for p in out.rglob('*') if p.is_file()))
    monkeypatch.setattr(host, 'host_module', lambda: shared)
    monkeypatch.setattr(host, 'cleanup', lambda *args: None)
    monkeypatch.setattr(host.subprocess, 'check_output', lambda *args, **kwargs: b'inert synthetic staged bytes')
    monkeypatch.setattr(host.subprocess, 'run', lambda *args, **kwargs: SimpleNamespace(returncode=0))
    with pytest.raises(RuntimeError) as error: host.main()
    assert error.value is primary and reached[-1] == stage
    saved = json.loads((host.OUT / 'summary.json').read_bytes())
    assert saved['commit'] == commit and saved['status'] == 'failed'
    assert saved['image_id'] == (None if stage == 'build' else image)
    assert saved['migration_revision'] == ('13engineeringprojects' if stage == 'model-schema' else None)
    assert saved['evidence_bytes'] == sum(p.stat().st_size for p in host.OUT.rglob('*') if p.is_file())


@pytest.mark.parametrize('fault', ['none', 'container-inventory', 'volume-inventory', 'network-inventory',
    'builder-inventory', 'post-container-inventory', 'builder-remains', 'volume-remains', 'foreign-container'])
def test_cleanup_requires_owned_successful_positive_postinventories(modules, monkeypatch, tmp_path, fault):
    host, _ = modules
    monkeypatch.setattr(host, 'ROOT', tmp_path)
    work = tmp_path / '.planner-pg-ci' / PROJECT
    host.write(work / 'owner.json', {'project': PROJECT})
    host.write(work / 'builder-owned.json', {'builder': PROJECT + '-builder'})
    host.write(work / 'compose.json', {})
    commands, counts = [], {}
    def fake(args, **kwargs):
        commands.append(args)
        assert 0 < kwargs['timeout'] <= 120
        category = ('container' if args[1:3] == ['ps', '-aq'] else
            args[1] if args[1:3] in (['volume', 'ls'], ['network', 'ls'], ['buildx', 'ls']) else 'action')
        counts[category] = counts.get(category, 0) + 1
        fail = fault == category.replace('buildx', 'builder') + '-inventory'
        fail |= fault == 'post-container-inventory' and category == 'container' and counts[category] == 2
        output = b''
        if fault == 'builder-remains' and category == 'buildx': output = (PROJECT + '-builder\n').encode()
        if fault == 'volume-remains' and category == 'volume': output = (PROJECT + '_database\n').encode()
        if fault == 'foreign-container' and category == 'container': output = b'foreign-container-id\n'
        if 'inspect' in args:
            output = json.dumps({host.OWNER: 'foreign' if fault == 'foreign-container' else PROJECT}).encode()
        return SimpleNamespace(returncode=1 if fail else 0, stdout=output)
    monkeypatch.setattr(host.subprocess, 'run', fake)
    if fault == 'none':
        host.cleanup(PROJECT, work)
        assert json.loads((host.OUT / 'cleanup.json').read_bytes())['status'] == 'owned_ephemeral_resources_removed'
        assert counts['container'] == counts['volume'] == counts['network'] == 2
    else:
        with pytest.raises(RuntimeError): host.cleanup(PROJECT, work)
        assert not (host.OUT / 'cleanup.json').exists()
        if fault == 'foreign-container': assert not any('down' in args or 'rm' in args for args in commands)


def test_mock_body_identity_role_and_request_limit_fail_closed(modules):
    _, fixture = modules
    state = fixture.MockState()
    raw = b'{"explicit":"mock only"}'
    digest = fixture.sha(raw)
    state.register({'hash': digest, 'behavior': 'held'})
    for body, role in ((b'changed', 'runtime-a'), (raw, 'controller'), (raw, 'runtime-c')):
        with pytest.raises(RuntimeError): state.observe(body, role)
    assert not state.requests
    for index in range(7): state.observe(raw, 'runtime-a' if index % 2 else 'runtime-b')
    with pytest.raises(RuntimeError): state.observe(raw, 'runtime-a')
    assert len(state.requests) == 7
    assert fixture.CANARY not in json.dumps(state.requests)
    assert all(row['hash'] == digest and row['mocked'] is True for row in state.requests)


def test_disabled_entrypoints_and_expired_case_do_no_operation(modules, monkeypatch):
    host, fixture = modules
    monkeypatch.delenv('GITHUB_ACTIONS', raising=False)
    monkeypatch.delenv('VF_PG_CI_PROJECT', raising=False)
    with pytest.raises(RuntimeError): host.main()
    with pytest.raises(RuntimeError): fixture.ephemeral()
    monkeypatch.setattr(fixture, 'CASE_DEADLINE', 1)
    monkeypatch.setattr(fixture.time, 'monotonic', lambda: 2)
    with pytest.raises(RuntimeError): fixture.wait_for(lambda: pytest.fail('expired case admitted work'))
