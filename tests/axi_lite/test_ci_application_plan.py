"""Source/fake-controller tests only. No Docker, database or RTL execution."""
import copy
import importlib.util
import json
from pathlib import Path
import re
from io import BytesIO
from types import SimpleNamespace
from zipfile import ZipFile

import pytest

ROOT = Path(__file__).resolve().parents[2]
spec = importlib.util.spec_from_file_location('vf_axi_application_ci_plan', ROOT / 'scripts/ci_axi_application.py')
ci = importlib.util.module_from_spec(spec)
spec.loader.exec_module(ci)


@pytest.mark.parametrize('values', [
    {}, {'GITHUB_RUN_ID': '../local', 'GITHUB_RUN_ATTEMPT': '1'},
    {'GITHUB_RUN_ID': '123', 'GITHUB_RUN_ATTEMPT': '1;rm'},
])
def test_project_identity_refuses_ambient_or_path_inputs(values):
    with pytest.raises(RuntimeError):
        ci.project_name(values)


def test_source_module_and_hosted_guard_start_no_local_operations(monkeypatch):
    monkeypatch.delenv('GITHUB_ACTIONS', raising=False)
    monkeypatch.setattr(ci.subprocess, 'Popen', lambda *a, **k: pytest.fail('local tool operation'))
    with pytest.raises(RuntimeError, match='hosted Linux only'):
        ci.hosted_main()


def test_ephemeral_plan_has_loopback_only_and_finite_separate_caps():
    plan = ci.compose_plan('vf-axi-ci-123-1', 'sha256:' + 'a' * 64)
    assert set(plan['services']) == {'postgres', 'valkey', 'api', 'worker', 'reconciler'}
    assert plan['services']['api']['ports'] == ['127.0.0.1::8000']
    assert all('ports' not in plan['services'][s] for s in ('postgres', 'valkey', 'worker', 'reconciler'))
    assert plan['services']['worker']['mem_limit'] == '2g'
    assert plan['services']['worker']['cpus'] == 2
    assert all('build' not in s and s['restart'] == 'no' for s in plan['services'].values())
    env = plan['services']['api']['environment']
    assert set(env) == {'VERIFORGE_DATABASE_URL', 'VERIFORGE_REDIS_URL', 'VERIFORGE_ARTIFACT_ROOT',
                        'PYTHONDONTWRITEBYTECODE', 'VERIFORGE_CI_EPHEMERAL_PROJECT'}
    assert '${' not in json.dumps(plan) and '.env' not in json.dumps(plan)
    assert all(v['labels'][ci.OWNER] == 'vf-axi-ci-123-1' for v in plan['volumes'].values())


def test_migration_target_is_explicit_complete_literal_chain():
    files = {'migrations/versions/a.py': b'revision="a"\ndown_revision=None',
             'migrations/versions/b.py': b'revision: str="b"\ndown_revision: str="a"'}
    assert ci.migration_head(files) == 'b'
    for broken in [
        {'migrations/versions/b.py': files['migrations/versions/b.py']},
        {**files, 'migrations/versions/c.py': b'revision="c"\ndown_revision="a"'},
        {'migrations/versions/a.py': b'revision="a"\ndown_revision="a"'},
    ]:
        with pytest.raises(RuntimeError):
            ci.migration_head(broken)


def test_typed_fixtures_have_exact_finite_budget_without_db(monkeypatch):
    from sqlalchemy.engine import Engine
    monkeypatch.setattr(Engine, 'connect', lambda *a, **k: pytest.fail('database access'))
    plans = ci.fixture_plans()
    assert set(plans) == set(ci.CASES) - {'replay'}
    assert ci.MAX_ATTEMPTS == 7
    assert {k: len(plans[k]['plan']['cycles']) for k in ('control', 'order', 'strobe', 'response')} == {
        'control': 149, 'order': 11, 'strobe': 9, 'response': 7}
    assert all(p['plan']['budget']['wall_time_seconds'] == 120 and
               p['plan']['budget']['max_artifact_bytes'] == ci.ATTEMPT_CAP for p in plans.values())


def fake_campaign(label):
    cycles = {'fifo': 5, 'arithmetic': 15, 'control': 149, 'order': 11, 'strobe': 9, 'response': 7}[label]
    failure = {'order': (7, 'AXIL_PROGRESS', 'axil.response_bound'),
               'strobe': (7, 'AXIL_READ', 'axil.read_value'),
               'response': (4, 'AXIL_HANDSHAKE', 'axil.stalled_response')}.get(label)
    signature = None if not failure else dict(first_cycle=failure[0], requirement_id=failure[1], failure_class=failure[2], signature_hash='fabricated', expected='test', observed='test')
    plan = {'fixture_id': label, 'budget': {'wall_time_seconds': 120}}
    identity = dict(source_hash='test-source', checker_hash='test-checker', contract_hash='test-contract', experiment_hash=ci.jhash(plan))
    result = dict(identity=identity, usage={'cycles': cycles, 'wall_time_seconds': 0.1}, failure_signature=signature,
                  trace=[{}] if label == 'fifo' else [], tool_versions=[{'name': 'verilator', 'version': 'fabricated Verilator 5.042'}, {'name': 'cocotb', 'version': '2.0.1'}])
    return dict(id='campaign-' + label, execution_status='completed', verdict='design_mismatch' if failure else 'pass_within_scope',
                plan=plan, identity=identity, attempts=[{'id': 'attempt-' + label, 'result': result}])


@pytest.mark.parametrize('label', ['fifo', 'arithmetic', 'control', 'order', 'strobe', 'response'])
def test_fabricated_controller_verdicts_are_checked_by_requirement(label):
    campaign = fake_campaign(label)
    ci.validate_case(label, campaign)
    broken = copy.deepcopy(campaign)
    broken['verdict'] = 'resource_limit'
    with pytest.raises(RuntimeError):
        ci.validate_case(label, broken)


def test_fabricated_replay_requires_exact_plan_identity_lineage_and_signature():
    source = fake_campaign('strobe')
    replay = copy.deepcopy(source)
    replay['source_attempt_id'] = source['attempts'][0]['id']
    ci.validate_case('replay', replay, source)
    for mutation in ['source_attempt_id', 'identity', 'plan']:
        broken = copy.deepcopy(replay)
        broken[mutation] = 'different'
        with pytest.raises(RuntimeError):
            ci.validate_case('replay', broken, source)


def test_immutability_keeps_bool_integer_and_bytes_distinct():
    before = {'tables': {'attempts': [{'id': 'old', 'valid': True}]}, 'files': {'a': {'sha256': 'a', 'size_bytes': 1}}}
    after = copy.deepcopy(before)
    after['tables']['attempts'].append({'id': 'new'})
    ci.unchanged(before, after)
    after['tables']['attempts'][0]['valid'] = 1
    with pytest.raises(RuntimeError, match='old rows changed'):
        ci.unchanged(before, after)
    after = copy.deepcopy(before)
    after['files']['a']['sha256'] = 'b'
    with pytest.raises(RuntimeError, match='artifact bytes'):
        ci.unchanged(before, after)


@pytest.mark.parametrize('fail_first,cancel_error', [(False, False), (True, False), (True, True)])
def test_fake_controller_finite_attempts_and_stop_on_unexpected_outcome(monkeypatch, tmp_path, fail_first, cancel_error):
    monkeypatch.setattr(ci.sys, 'platform', 'linux')
    monkeypatch.setenv('VERIFORGE_CI_EPHEMERAL_PROJECT', 'vf-axi-ci-123-1')
    monkeypatch.setattr(ci.time, 'sleep', lambda _: None)
    monkeypatch.setattr(ci, 'fixture_plans', lambda: {k: {'label': k} for k in ci.CASES if k != 'replay'})
    monkeypatch.setattr(ci, 'validate_package', lambda *_: None)
    monkeypatch.setattr(ci, 'export_readback', lambda *_: {'id': 'fake-export'})
    campaigns, submitted, cancelled = {}, [], []
    def fake_api(method, path, body=None, key=None):
        if path == '/health':
            return {'evidence_adapters': ['rtl.fifo.v1', 'rtl.arithmetic.v1', 'rtl.axi_lite.v1']}
        if method == 'POST' and path == '/campaigns':
            label = body['label']
            campaigns['campaign-' + label] = fake_campaign(label)
            if fail_first:
                campaigns['campaign-' + label]['verdict'] = 'harness_error'
            submitted.append(label)
            return copy.deepcopy(campaigns['campaign-' + label])
        if method == 'POST' and path.endswith('/replay'):
            value = copy.deepcopy(campaigns['campaign-strobe'])
            value.update(id='campaign-replay', source_attempt_id='attempt-strobe')
            value['attempts'][0]['id'] = 'attempt-replay'
            campaigns[value['id']] = value
            submitted.append('replay')
            return copy.deepcopy(value)
        identity = path.split('/')[2]
        if path.endswith('/cancel'):
            cancelled.append(identity)
            if cancel_error:
                raise RuntimeError('secondary fake cancel failure')
        return copy.deepcopy(campaigns[identity])
    monkeypatch.setattr(ci, 'api_json', fake_api)
    monkeypatch.setattr(ci, 'idle_snapshot', lambda: {'revision': 'fake-only', 'tables': {
        'campaigns': [{'id': c['id']} for c in campaigns.values()],
        'attempts': [{'id': c['attempts'][0]['id']} for c in campaigns.values()]}, 'files': {}, 'queues': {}})
    if fail_first:
        with pytest.raises(RuntimeError, match='unexpected checker result'):
            ci.exercise(tmp_path / 'fake')
        assert submitted == ['fifo'] and cancelled == ['campaign-fifo']
    else:
        ci.exercise(tmp_path / 'fake')
        assert submitted == list(ci.CASES) and not cancelled
        assert json.loads((tmp_path / 'fake/application-summary.json').read_text())['revision'] == 'fake-only'


def test_workflow_manual_pinned_readonly_and_cleanup_before_upload():
    workflow = (ROOT / '.github/workflows/axi-application-acceptance.yml').read_text()
    assert 'workflow_dispatch:' in workflow and 'inputs:' not in workflow
    assert 'contents: read' in workflow and 'persist-credentials: false' in workflow
    assert 'secrets.' not in workflow and 'timeout-minutes: 45' in workflow
    assert all(re.fullmatch(r'[0-9a-f]{40}', ref) for ref in re.findall(r'uses: [^@\s]+@([^\s]+)', workflow))
    assert workflow.index('ci_axi_application.py --cleanup') < workflow.index('uses: actions/upload-artifact')
    assert 'retention-days: 1' in workflow and 'if: always()' in workflow


def test_cleanup_without_marker_never_calls_docker(monkeypatch, tmp_path):
    monkeypatch.setattr(ci.subprocess, 'run', lambda *a, **k: pytest.fail('cleanup without ownership'))
    ci.cleanup('vf-axi-ci-123-1', tmp_path / 'missing')


@pytest.mark.parametrize('aggregate', [False, True])
def test_fast_fake_process_cannot_bypass_terminal_caps(monkeypatch, tmp_path, aggregate):
    monkeypatch.setattr(ci, 'OUT', tmp_path / 'evidence')
    ci.OUT.mkdir()
    if aggregate:
        with (ci.OUT / 'sparse-other-evidence').open('wb') as output:
            output.seek(ci.CAP)
            output.write(b'x')
    def fake_process(args, stdout, **kwargs):
        if not aggregate:
            stdout.seek(2 * 1024 * 1024)
        stdout.write(b'x')
        stdout.flush()
        return SimpleNamespace(poll=lambda: 0, returncode=0)
    monkeypatch.setattr(ci.subprocess, 'Popen', fake_process)
    with pytest.raises(RuntimeError, match='cap'):
        ci.Host('vf-axi-ci-123-1', tmp_path / 'work').run(['FAKE-ONLY'], 'fast')
    assert (ci.OUT / 'fast.log').stat().st_size <= 2 * 1024 * 1024


@pytest.mark.parametrize('primary_exists', [False, True])
def test_cleanup_failure_never_leaves_success_or_masks_first_cause(monkeypatch, tmp_path, primary_exists):
    monkeypatch.setattr(ci, 'OUT', tmp_path / 'evidence')
    def fail_cleanup(*_):
        raise RuntimeError('secondary cleanup')
    monkeypatch.setattr(ci, 'cleanup', fail_cleanup)
    summary = {'status': 'application_checks_passed_pending_cleanup'}
    primary = ValueError('first checker failure') if primary_exists else None
    returned = ci.finish_owned('vf-axi-ci-123-1', tmp_path / 'work', summary, primary)
    assert summary['status'] == 'failed_or_cancelled' and summary['owned_cleanup']['status'] == 'failed'
    if primary_exists:
        assert returned is primary and 'first checker failure' in summary['error']
    else:
        assert isinstance(returned, RuntimeError) and 'secondary cleanup' in summary['error']


def test_cleanup_refuses_foreign_container_before_down_or_builder_removal(monkeypatch, tmp_path):
    monkeypatch.setattr(ci, 'ROOT', tmp_path)
    project = 'vf-axi-ci-123-1'
    work = tmp_path / '.axi-ci-ephemeral' / project
    ci.write(work / 'owner.json', {'owner': project})
    calls = []
    def fake_run(args, **kwargs):
        calls.append(args)
        if args[1:3] == ['ps', '-aq']:
            return SimpleNamespace(returncode=0, stdout='foreign\n')
        return SimpleNamespace(returncode=0, stdout=json.dumps({'Config': {'Labels': {ci.OWNER: 'someone-else'}}}).encode())
    monkeypatch.setattr(ci.subprocess, 'run', fake_run)
    with pytest.raises(RuntimeError, match='foreign resource'):
        ci.cleanup(project, work)
    assert not any('down' in args or 'rm' in args for args in calls)


@pytest.mark.parametrize('tamper', [False, True])
def test_fake_export_matches_original_native_bytes_and_refuses_family_tamper(monkeypatch, tmp_path, tamper):
    source = fake_campaign('strobe')
    original = tmp_path / 'artifacts/attempt-strobe'
    original.mkdir(parents=True)
    references = []
    for name in ['registers.sv', 'checker-contracts.py', 'check.json', 'observed.json']:
        raw = ('fabricated-' + name).encode()
        (original / name).write_bytes(raw)
        references.append({'path': name, 'size_bytes': len(raw), 'sha256': ci.hashlib.sha256(raw).hexdigest()})
    manifest = {'identity': source['attempts'][0]['result']['identity'], 'experiment': source['plan'],
                'adapter_id': 'rtl.axi_lite.v1', 'trace': [], 'artifacts': references,
                'family_trace': {'schema_version': 'axi_lite.trace.v1', 'artifact': 'artifacts/observed.json'}}
    if tamper:
        manifest['family_trace']['schema_version'] = 'fifo.trace.v1'
    buffer = BytesIO()
    with ZipFile(buffer, 'w') as archive:
        archive.writestr('manifest.json', json.dumps(manifest))
        archive.writestr('experiment.json', json.dumps(source['plan']))
        archive.writestr('REPORT.txt', 'fabricated controller evidence only')
        for ref in references:
            archive.writestr('artifacts/' + ref['path'], (original / ref['path']).read_bytes())
    raw = buffer.getvalue()
    job = {'id': 'fake-export', 'status': 'completed', 'size_bytes': len(raw), 'sha256': ci.hashlib.sha256(raw).hexdigest()}
    monkeypatch.setattr(ci, 'api_json', lambda *a, **k: copy.deepcopy(job))
    monkeypatch.setattr(ci, 'api', lambda *a, **k: (raw, {'x-content-type-options': 'nosniff', 'content-type': 'application/zip'}))
    real_path = Path
    monkeypatch.setattr(ci, 'Path', lambda value: tmp_path / 'artifacts' if value == '/artifacts' else real_path(value))
    if tamper:
        with pytest.raises(RuntimeError, match='native trace metadata'):
            ci.export_readback(source, tmp_path)
    else:
        assert ci.export_readback(source, tmp_path) == job
        assert (tmp_path / 'axi-export.zip').read_bytes() == raw
