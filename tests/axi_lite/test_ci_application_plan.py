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
    # Actual hosted run40934406068 failed before migration: Docker's local
    # driver rejects default compression when only one log file is retained.
    for role in ('api', 'worker', 'reconciler'):
        logging = plan['services'][role]['logging']
        assert logging['driver'] == 'local'
        assert logging['options'] == {'max-size': '2m', 'max-file': '1', 'compress': 'false'}


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
    verdict = {'order': 'property_failure', 'strobe': 'design_mismatch', 'response': 'property_failure'}.get(label, 'pass_within_scope')
    return dict(id='campaign-' + label, execution_status='completed', verdict=verdict,
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


@pytest.mark.parametrize('label,expected', [('order', 'property_failure'), ('strobe', 'design_mismatch'), ('response', 'property_failure')])
def test_each_negative_requires_its_exact_historical_verdict_kind(label, expected):
    campaign = fake_campaign(label)
    assert campaign['verdict'] == expected
    ci.validate_case(label, campaign)
    campaign['verdict'] = 'design_mismatch' if expected == 'property_failure' else 'property_failure'
    with pytest.raises(RuntimeError, match='unexpected checker result/cycles'):
        ci.validate_case(label, campaign)


@pytest.mark.parametrize('label', ['order', 'strobe', 'response'])
@pytest.mark.parametrize('damage', ['cycles', 'first_cycle', 'requirement_id', 'failure_class'])
def test_negative_class_correction_preserves_exact_cycle_and_semantic_fault(label, damage):
    campaign = fake_campaign(label)
    if damage == 'cycles':
        campaign['attempts'][0]['result']['usage']['cycles'] += 1
    else:
        signature = campaign['attempts'][0]['result']['failure_signature']
        signature[damage] = signature[damage] + 1 if damage == 'first_cycle' else 'wrong-kind'
    with pytest.raises(RuntimeError):
        ci.validate_case(label, campaign)


def test_strobe_replay_rejects_property_failure_even_with_matching_signature():
    source = fake_campaign('strobe')
    replay = copy.deepcopy(source)
    replay.update(source_attempt_id=source['attempts'][0]['id'], verdict='property_failure')
    with pytest.raises(RuntimeError, match='replay failure changed'):
        ci.validate_case('replay', replay, source)


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
    artifacts = tmp_path / 'artifacts'
    artifacts.mkdir()
    real_path = Path
    monkeypatch.setattr(ci, 'Path', lambda value: artifacts if value == '/artifacts' else real_path(value))
    monkeypatch.setattr(ci, 'effective_identity', lambda: (tmp_path.stat().st_uid, tmp_path.stat().st_gid))
    monkeypatch.setattr(ci.time, 'sleep', lambda _: None)
    monkeypatch.setattr(ci, 'fixture_plans', lambda: {k: {'label': k} for k in ci.CASES if k != 'replay'})
    campaigns, submitted, cancelled, checks, exported = {}, [], [], [], []
    original_validate_case = ci.validate_case
    def fake_package(campaign, destination):
        destination.mkdir()
        (destination / 'fake-evidence.txt').write_bytes(b'synthetic retained package, not simulator evidence')
        checks.append(('package', campaign['id']))
    def track_semantics(label, campaign, source=None):
        assert checks[-1] == ('package', campaign['id'])
        checks.append(('semantic', campaign['id']))
        return original_validate_case(label, campaign, source)
    def fake_export(*_):
        exported.append('fake-export')
        return {'id': 'fake-export'}
    monkeypatch.setattr(ci, 'validate_package', fake_package)
    monkeypatch.setattr(ci, 'validate_case', track_semantics)
    monkeypatch.setattr(ci, 'export_readback', fake_export)
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
        assert (tmp_path / 'fake/fifo-attempt/fake-evidence.txt').exists()
        assert json.loads((tmp_path / 'fake/fifo-validation-status.json').read_text()) == {
            'package_validation': 'passed', 'semantic_expectation': 'pending',
            'preservation': 'not_checked', 'acceptance': 'unaccepted'}
        assert not exported and not (tmp_path / 'fake/application-summary.json').exists()
    else:
        ci.exercise(tmp_path / 'fake')
        assert submitted == list(ci.CASES) and not cancelled
        assert json.loads((tmp_path / 'fake/application-summary.json').read_text())['revision'] == 'fake-only'
        assert exported == ['fake-export']
        for label in ci.CASES:
            assert json.loads((tmp_path / 'fake' / (label + '-validation-status.json')).read_text()) == {
                'package_validation': 'passed', 'semantic_expectation': 'passed',
                'preservation': 'passed', 'acceptance': 'accepted'}


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


@pytest.mark.parametrize('primary_message,cleanup_fails,cancelled', [
    (None, False, False), ('checker failure ' + '9' * 120 + '\u2713', False, False),
    (None, True, False), ('original failure', True, False), (None, False, True),
])
def test_final_receipt_counts_complete_failure_cleanup_and_utf8_bytes(monkeypatch, tmp_path,
        primary_message, cleanup_fails, cancelled):
    output = tmp_path / 'evidence'
    output.mkdir()
    evidence = b'preserved native evidence\x00\xff'
    (output / 'native.bin').write_bytes(evidence)
    (output / 'summary.json').write_bytes(b'obsolete receipt must be excluded')
    monkeypatch.setattr(ci, 'OUT', output)
    monkeypatch.setattr(ci, 'STOP', cancelled)
    cleanup_error = RuntimeError('cleanup failed with digits 1234567890')
    def cleanup(*_):
        if cleanup_fails:
            raise cleanup_error
    monkeypatch.setattr(ci, 'cleanup', cleanup)
    primary = ValueError(primary_message) if primary_message else None
    summary = {'status': 'application_checks_passed_pending_cleanup', 'diagnostic': '\u03bb'}
    result = ci.finish_owned('vf-axi-ci-123-1', tmp_path / 'work', summary, primary)
    if primary:
        assert result is primary
    elif cleanup_fails:
        assert result is cleanup_error
    elif cancelled:
        assert result is not None and 'cancelled' in summary['error']
    else:
        assert result is None
    actual = sum(path.stat().st_size for path in output.rglob('*') if path.is_file())
    saved = json.loads((output / 'summary.json').read_bytes())
    assert saved == summary and saved['evidence_bytes'] == actual
    assert saved['evidence_budget_status'] == 'within_budget'
    assert saved['status'] == ('actual_hosted_gate_passed' if result is None else 'failed_or_cancelled')
    assert (output / 'native.bin').read_bytes() == evidence
    if result is not None:
        assert saved['error'] == type(result).__name__ + ': ' + str(result)[:500]


def test_final_receipt_exact_cap_and_one_byte_over_preserve_original_evidence(monkeypatch, tmp_path):
    monkeypatch.setattr(ci, 'cleanup', lambda *_: None)
    monkeypatch.setattr(ci, 'STOP', False)
    def finish(directory, cap):
        directory.mkdir()
        (directory / 'native.bin').write_bytes(b'x' * 950)
        monkeypatch.setattr(ci, 'OUT', directory)
        monkeypatch.setattr(ci, 'CAP', cap)
        summary = {'status': 'application_checks_passed_pending_cleanup'}
        result = ci.finish_owned('vf-axi-ci-123-1', tmp_path / 'work', summary)
        actual = sum(p.stat().st_size for p in directory.iterdir())
        assert summary['evidence_bytes'] == actual
        assert json.loads((directory / 'summary.json').read_bytes()) == summary
        assert (directory / 'native.bin').read_bytes() == b'x' * 950
        return result, summary
    # Same-width cap digits keep the exact-boundary serialization stable.
    _, probe = finish(tmp_path / 'probe', 9999)
    boundary = probe['evidence_bytes']
    assert 1000 <= boundary < 9999
    result, exact = finish(tmp_path / 'exact', boundary)
    assert result is None and exact['evidence_bytes'] == boundary
    result, over = finish(tmp_path / 'over', boundary - 1)
    assert isinstance(result, RuntimeError) and 'final evidence cap' in str(result)
    assert over['status'] == 'failed_or_cancelled' and over['evidence_budget_status'] == 'exceeded'
    assert over['evidence_bytes'] > over['evidence_budget_bytes']
    assert 'evidence_budget_error' in over and 'error' in over


def test_over_budget_receipt_keeps_primary_failure_and_counts_added_metadata(monkeypatch, tmp_path):
    monkeypatch.setattr(ci, 'OUT', tmp_path)
    monkeypatch.setattr(ci, 'CAP', 1)
    monkeypatch.setattr(ci, 'STOP', False)
    monkeypatch.setattr(ci, 'cleanup', lambda *_: None)
    (tmp_path / 'native.log').write_bytes(b'original retained evidence')
    primary = ValueError('primary checker failure 987654321')
    summary = {'status': 'started'}
    assert ci.finish_owned('vf-axi-ci-123-1', tmp_path / 'work', summary, primary) is primary
    assert 'primary checker failure' in summary['error']
    assert summary['evidence_budget_status'] == 'exceeded'
    assert summary['evidence_bytes'] == sum(p.stat().st_size for p in tmp_path.iterdir())
    assert any('Final evidence budget' in note for note in primary.__notes__)
    assert (tmp_path / 'native.log').read_bytes() == b'original retained evidence'


@pytest.mark.parametrize('primary_exists', [False, True])
def test_unavailable_evidence_measurement_reports_null_and_preserves_primary(monkeypatch, tmp_path, primary_exists):
    monkeypatch.setattr(ci, 'OUT', tmp_path)
    monkeypatch.setattr(ci, 'STOP', False)
    monkeypatch.setattr(ci, 'cleanup', lambda *_: None)
    def fail_measurement(_):
        raise OSError('metadata unavailable')
    monkeypatch.setattr(ci, 'evidence_size', fail_measurement)
    primary = ValueError('original failure') if primary_exists else None
    summary = {'status': 'application_checks_passed_pending_cleanup'}
    returned = ci.finish_owned('vf-axi-ci-123-1', tmp_path / 'work', summary, primary)
    assert returned is primary if primary_exists else isinstance(returned, OSError)
    saved = json.loads((tmp_path / 'summary.json').read_bytes())
    assert saved['evidence_bytes'] is None and saved['evidence_budget_status'] == 'unavailable'
    assert saved['status'] == 'failed_or_cancelled'
    assert ('original failure' if primary_exists else 'metadata unavailable') in saved['error']
    assert 'metadata unavailable' in saved['evidence_budget_error']


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


def mount_context(monkeypatch, tmp_path):
    """Fake Linux identity/mode; temporary files do not prove Docker/Linux DAC."""
    monkeypatch.setattr(ci, 'ROOT', tmp_path)
    monkeypatch.setattr(ci, 'OUT', tmp_path / 'ci-artifacts/axi-application')
    ci.OUT.mkdir(parents=True)
    monkeypatch.setattr(ci, 'effective_identity', lambda: (1001, 1002))
    monkeypatch.setattr(ci, 'directory_identity', lambda _: {'uid': 1001, 'gid': 1002, 'mode': '0700'})


def test_owned_new_evidence_mount_uses_numeric_host_identity_for_exercise_only(monkeypatch, tmp_path):
    mount_context(monkeypatch, tmp_path)
    evidence, user = ci.prepare_evidence_mount()
    assert user == '1001:1002' and evidence == ci.OUT.resolve() / 'application'
    assert json.loads((ci.OUT / 'evidence-mount.json').read_text()) == {
        'container_user': user, 'directory': {'uid': 1001, 'gid': 1002, 'mode': '0700'}}
    prefix = ['docker', 'compose', '-f', '/owned/compose.json', '-p', 'vf-axi-ci-123-1']
    args = ci.exercise_command(prefix, evidence, user)
    assert args == prefix + ['run', '--rm', '--no-deps', '--user', user, '-v', str(evidence) + ':/evidence',
                             'api', 'python', '/workspace/scripts/ci_axi_application.py', '--exercise']
    plan = ci.compose_plan('vf-axi-ci-123-1', 'sha256:' + 'a' * 64)
    for role in ('api', 'worker', 'reconciler'):
        service = plan['services'][role]
        assert 'user' not in service and 'privileged' not in service and 'cap_add' not in service
        assert service['cap_drop'] == ['ALL'] and service['security_opt'] == ['no-new-privileges:true']
    assert plan['services']['api']['mem_limit'] == '512m'
    assert plan['services']['worker']['mem_limit'] == '2g'
    assert plan['services']['reconciler']['mem_limit'] == '256m'
    source = (ROOT / 'scripts/ci_axi_application.py').read_text()
    assert "'actual-application', 1350" in source
    assert 'evidence.chmod' not in source and 'os.chown' not in source
    with pytest.raises(RuntimeError, match='already exists'):
        ci.prepare_evidence_mount()


@pytest.mark.parametrize('user', ['root', '0:0', '1001:1002;id', '1:4294967295', '-1:1', '1:2:3', 'true:1'])
def test_exercise_user_is_bounded_numeric_nonroot(monkeypatch, tmp_path, user):
    mount_context(monkeypatch, tmp_path)
    evidence, _ = ci.prepare_evidence_mount()
    with pytest.raises(RuntimeError, match='identity'):
        ci.exercise_command(['FAKE-NO-EXECUTION'], evidence, user)


@pytest.mark.parametrize('damage', ['outside_root', 'foreign_owner', 'wrong_mode', 'root_runner', 'sibling_prefix'])
def test_mount_refuses_unowned_permissions_and_noncontained_path(monkeypatch, tmp_path, damage):
    mount_context(monkeypatch, tmp_path)
    if damage == 'outside_root':
        monkeypatch.setattr(ci, 'ROOT', tmp_path / 'different-checkout')
    elif damage == 'foreign_owner':
        monkeypatch.setattr(ci, 'directory_identity', lambda _: {'uid': 1003, 'gid': 1002, 'mode': '0700'})
    elif damage == 'wrong_mode':
        monkeypatch.setattr(ci, 'directory_identity', lambda _: {'uid': 1001, 'gid': 1002, 'mode': '0755'})
    elif damage == 'root_runner':
        monkeypatch.setattr(ci, 'effective_identity', lambda: (0, 0))
    else:
        evidence = ci.OUT.parent / (ci.OUT.name + '-foreign') / 'application'
        evidence.mkdir(parents=True)
        with pytest.raises(RuntimeError, match='unsafe evidence mount'):
            ci.exercise_command(['FAKE-NO-EXECUTION'], evidence, '1001:1002')
        return
    with pytest.raises(RuntimeError):
        ci.prepare_evidence_mount()
    assert not (ci.OUT / 'evidence-mount.json').exists()


def preflight_context(monkeypatch, tmp_path):
    artifacts = tmp_path / 'artifacts'
    artifacts.mkdir()
    evidence = tmp_path / 'evidence'
    evidence.mkdir()
    real_path = Path
    monkeypatch.setattr(ci, 'Path', lambda value: artifacts if value == '/artifacts' else real_path(value))
    monkeypatch.setattr(ci, 'effective_identity', lambda: (evidence.stat().st_uid, evidence.stat().st_gid))
    return evidence


def test_preflight_records_only_effective_identity_mode_and_owned_probe_outcome(monkeypatch, tmp_path, capsys):
    evidence = preflight_context(monkeypatch, tmp_path)
    ci.evidence_preflight(evidence)
    assert not (evidence / '.veriforge-mount-probe').exists()
    receipt = json.loads((evidence / 'mount-preflight.json').read_text())
    assert receipt == {'effective_uid': evidence.stat().st_uid, 'effective_gid': evidence.stat().st_gid,
                       'directory': ci.directory_identity(evidence), 'probe': 'write_read_remove_passed', 'artifact_root': 'readable'}
    assert set(p.name for p in evidence.iterdir()) == {'mount-preflight.json'}
    assert json.loads(capsys.readouterr().out) == {'evidence_mount_preflight': {
        'effective_uid': receipt['effective_uid'], 'effective_gid': receipt['effective_gid'],
        'directory': receipt['directory']}}


@pytest.mark.parametrize('failure', ['existing_probe', 'read_failure', 'wrong_owner', 'artifact_unavailable'])
def test_preflight_failure_precedes_source_requests_and_all_posts(monkeypatch, tmp_path, failure):
    evidence = preflight_context(monkeypatch, tmp_path)
    monkeypatch.setattr(ci.sys, 'platform', 'linux')
    monkeypatch.setenv('VERIFORGE_CI_EPHEMERAL_PROJECT', 'vf-axi-ci-123-1')
    monkeypatch.setattr(ci, 'fixture_plans', lambda: pytest.fail('requests prepared after failed preflight'))
    monkeypatch.setattr(ci, 'api_json', lambda *a, **k: pytest.fail('HTTP after failed preflight'))
    probe = evidence / '.veriforge-mount-probe'
    if failure == 'existing_probe':
        probe.write_bytes(b'preexisting must remain untouched')
    elif failure == 'wrong_owner':
        monkeypatch.setattr(ci, 'effective_identity', lambda: (12345, 12345))
    elif failure == 'artifact_unavailable':
        (tmp_path / 'artifacts').rmdir()
    else:
        original = Path.open
        def fail_read(path, mode='r', *args, **kwargs):
            if path == probe and mode == 'rb':
                raise PermissionError('fake probe read denied')
            return original(path, mode, *args, **kwargs)
        monkeypatch.setattr(Path, 'open', fail_read)
    with pytest.raises((RuntimeError, OSError)):
        ci.exercise(evidence)
    assert not (evidence / 'installed-source-vector.json').exists()
    assert not (evidence / 'frozen-requests.json').exists()
    assert not (evidence / 'mount-preflight.json').exists()
    if failure == 'existing_probe':
        assert probe.read_bytes() == b'preexisting must remain untouched'
    else:
        assert not probe.exists()


@pytest.mark.parametrize('phase', ['read', 'write'])
@pytest.mark.parametrize('cleanup', ['lstat', 'unlink'])
def test_probe_cleanup_retains_exact_primary_exception_and_bounded_secondary_note(monkeypatch, tmp_path, phase, cleanup):
    from contextlib import contextmanager
    evidence = preflight_context(monkeypatch, tmp_path)
    probe = evidence / '.veriforge-mount-probe'
    primary = OSError('first probe ' + phase + ' failure')
    secondary = PermissionError('secondary cleanup denied ' + 'x' * 900)
    original_open = Path.open
    original_cleanup = getattr(Path, cleanup)

    def fail_write(_):
        raise primary

    @contextmanager
    def writing(path, mode, *args, **kwargs):
        with original_open(path, mode, *args, **kwargs) as stream:
            yield SimpleNamespace(fileno=stream.fileno, write=fail_write)

    def open_probe(path, mode='r', *args, **kwargs):
        if path == probe:
            if phase == 'read' and mode == 'rb':
                raise primary
            if phase == 'write' and mode == 'xb':
                return writing(path, mode, *args, **kwargs)
        return original_open(path, mode, *args, **kwargs)

    def fail_cleanup(path, *args, **kwargs):
        if path == probe:
            raise secondary
        return original_cleanup(path, *args, **kwargs)

    monkeypatch.setattr(Path, 'open', open_probe)
    monkeypatch.setattr(Path, cleanup, fail_cleanup)
    with pytest.raises(OSError) as observed:
        ci.evidence_preflight(evidence)
    assert observed.value is primary
    assert len(primary.__notes__) == 1
    assert 'PermissionError: secondary cleanup denied' in primary.__notes__[0]
    assert len(primary.__notes__[0]) < 600
    assert not (evidence / 'mount-preflight.json').exists()


def test_package_validation_failure_stays_unaccepted_without_semantic_check_or_export(monkeypatch, tmp_path):
    evidence = preflight_context(monkeypatch, tmp_path)
    monkeypatch.setattr(ci.sys, 'platform', 'linux')
    monkeypatch.setenv('VERIFORGE_CI_EPHEMERAL_PROJECT', 'vf-axi-ci-123-1')
    monkeypatch.setattr(ci, 'fixture_plans', lambda: {'fifo': {'fake': 'request'}})
    monkeypatch.setattr(ci, 'idle_snapshot', lambda: {'tables': {'campaigns': [], 'attempts': []}, 'files': {}, 'queues': {}})
    campaign = fake_campaign('fifo')
    calls = []
    def fake_api(method, path, *args, **kwargs):
        calls.append((method, path))
        if path == '/health':
            return {'evidence_adapters': ['rtl.fifo.v1', 'rtl.arithmetic.v1', 'rtl.axi_lite.v1']}
        return copy.deepcopy(campaign)
    primary = ValueError('fake package hash mismatch')
    def fail_package(_, destination):
        destination.mkdir()
        (destination / 'partial-unaccepted.txt').write_bytes(b'fake incomplete bytes')
        raise primary
    monkeypatch.setattr(ci, 'api_json', fake_api)
    monkeypatch.setattr(ci, 'validate_package', fail_package)
    monkeypatch.setattr(ci, 'validate_case', lambda *_: pytest.fail('semantics after failed package validation'))
    monkeypatch.setattr(ci, 'export_readback', lambda *_: pytest.fail('export after failed package validation'))
    with pytest.raises(ValueError) as observed:
        ci.exercise(evidence)
    assert observed.value is primary
    assert calls == [('GET', '/health'), ('POST', '/campaigns'),
                     ('POST', '/campaigns/campaign-fifo/start'), ('POST', '/campaigns/campaign-fifo/cancel')]
    assert json.loads((evidence / 'fifo-validation-status.json').read_text()) == {
        'package_validation': 'pending', 'semantic_expectation': 'not_checked',
        'preservation': 'not_checked', 'acceptance': 'unaccepted'}
    assert not (evidence / 'application-summary.json').exists()
