"""Independent frozen gate correction review: synthetic campaigns and files only."""
import copy
import importlib.util
import json
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
EXPECTED = {'fifo': ('pass_within_scope', 5, None), 'arithmetic': ('pass_within_scope', 15, None),
    'control': ('pass_within_scope', 149, None),
    'order': ('property_failure', 11, (7, 'AXIL_PROGRESS', 'axil.response_bound')),
    'strobe': ('design_mismatch', 9, (7, 'AXIL_READ', 'axil.read_value')),
    'response': ('property_failure', 7, (4, 'AXIL_HANDSHAKE', 'axil.stalled_response'))}


@pytest.fixture
def ci(monkeypatch):
    import platform
    import subprocess
    monkeypatch.setattr(platform, 'machine', lambda: 'AMD64')
    def forbidden(*args, **kwargs):
        pytest.fail('Review cannot dispatch an external operation')
    for name in ('run', 'Popen', 'check_output'):
        monkeypatch.setattr(subprocess, name, forbidden)
    spec = importlib.util.spec_from_file_location('independent_verdict_class_review', ROOT / 'scripts/ci_axi_application.py')
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def campaign(ci, label):
    verdict, cycles, fault = EXPECTED[label]
    plan = {'fixture_id': 'SYNTHETIC-' + label, 'seed': 17}
    identity = {'source_hash': 'a'*64, 'checker_hash': 'b'*64, 'contract_hash': 'c'*64,
        'experiment_hash': ci.jhash(plan)}
    signature = None if fault is None else dict(first_cycle=fault[0], requirement_id=fault[1],
        failure_class=fault[2], expected='SYNTHETIC expected', observed='SYNTHETIC observed', signature_hash='d'*64)
    result = {'verdict': verdict, 'execution_status': 'completed', 'identity': copy.deepcopy(identity),
        'usage': {'cycles': cycles, 'wall_time_seconds': 0.1}, 'failure_signature': signature, 'trace': [],
        'tool_versions': [{'name': 'verilator', 'version': 'SYNTHETIC Verilator 5.042'}, {'name': 'cocotb', 'version': '2.0.1'}]}
    return dict(id='synthetic-' + label, execution_status='completed', verdict=verdict,
        plan=plan, identity=identity, attempts=[{'id': 'synthetic-attempt-' + label, 'result': result}])


@pytest.mark.parametrize('label', list(EXPECTED))
def test_exact_case_outcome_is_accepted_and_wrong_verdict_rejected(ci, label):
    value = campaign(ci, label)
    assert ci.validate_case(label, value) is value['attempts'][0]['result']
    wrong = 'design_mismatch' if value['verdict'] != 'design_mismatch' else 'property_failure'
    value['verdict'] = wrong
    with pytest.raises(RuntimeError, match='unexpected checker result/cycles'):
        ci.validate_case(label, value)


@pytest.mark.parametrize('label', ['order', 'strobe', 'response'])
@pytest.mark.parametrize('field', ['cycles', 'first_cycle', 'requirement_id', 'failure_class'])
def test_correct_kind_cannot_hide_changed_cycle_or_fault(ci, label, field):
    value = campaign(ci, label)
    result = value['attempts'][0]['result']
    if field == 'cycles':
        result['usage']['cycles'] += 1
    else:
        result['failure_signature'][field] = 99 if field == 'first_cycle' else 'WRONG'
    with pytest.raises(RuntimeError):
        ci.validate_case(label, value)


@pytest.mark.parametrize('damage', [None, 'verdict', 'source_attempt_id', 'plan', 'identity', 'signature'])
def test_strobe_replay_requires_exact_kind_identity_lineage_and_signature(ci, damage):
    source = campaign(ci, 'strobe')
    replay = copy.deepcopy(source)
    replay['source_attempt_id'] = source['attempts'][0]['id']
    if damage == 'verdict':
        replay['verdict'] = 'property_failure'
    elif damage == 'source_attempt_id':
        replay['source_attempt_id'] = 'foreign-attempt'
    elif damage == 'plan':
        replay['plan']['seed'] += 1
    elif damage == 'identity':
        replay['identity']['checker_hash'] = 'e'*64
    elif damage == 'signature':
        replay['attempts'][0]['result']['failure_signature']['observed'] = 'different'
    if damage is None:
        assert ci.validate_case('replay', replay, source)
    else:
        with pytest.raises(RuntimeError):
            ci.validate_case('replay', replay, source)


def exercise_context(ci, monkeypatch, destination, *, fail=None):
    monkeypatch.setattr(ci.sys, 'platform', 'linux')
    monkeypatch.setenv('VERIFORGE_CI_EPHEMERAL_PROJECT', 'vf-axi-ci-711-1')
    monkeypatch.setattr(ci, 'STOP', False)
    monkeypatch.setattr(ci, 'evidence_preflight', lambda _: None)
    monkeypatch.setattr(ci, 'fixture_plans', lambda: {label: {'label': label} for label in EXPECTED})
    if fail:
        monkeypatch.setattr(ci, 'CASES', ('order',))
    events, rows, values, errors = [], [], {}, []
    def api(method, path, body=None, key=None):
        if path == '/health':
            return {'evidence_adapters': ['rtl.fifo.v1', 'rtl.arithmetic.v1', 'rtl.axi_lite.v1']}
        if path.endswith('/cancel'):
            events.append('cancel')
            raise OSError('synthetic secondary cancel failure')
        if path == '/campaigns':
            value = campaign(ci, body['label'])
            if fail == 'semantic':
                value['verdict'] = 'design_mismatch'
            values[value['id']] = value
            rows.append({'id': value['id']})
            return copy.deepcopy(value)
        if path.endswith('/replay'):
            value = copy.deepcopy(values['synthetic-strobe'])
            value.update(id='synthetic-replay', source_attempt_id=value['attempts'][0]['id'])
            value['attempts'][0]['id'] = 'synthetic-attempt-replay'
            values[value['id']] = value
            rows.append({'id': value['id']})
            return value
        return copy.deepcopy(values[path.split('/')[2]])
    monkeypatch.setattr(ci, 'api_json', api)
    def snapshot():
        return {'tables': {'campaigns': copy.deepcopy(rows), 'attempts': copy.deepcopy(rows)}, 'files': {},
            'revision': 'synthetic', 'queues': {'synthetic': 'idle'}}
    monkeypatch.setattr(ci, 'idle_snapshot', snapshot)
    package_error, preserve_error = ValueError('synthetic package cap/identity failure'), ValueError('synthetic old row changed')
    def package(value, path):
        events.append('package')
        path.mkdir()
        (path / 'SYNTHETIC.txt').write_bytes(b'SYNTHETIC package; no native execution')
        if fail == 'package':
            raise package_error
    monkeypatch.setattr(ci, 'validate_package', package)
    original_validate, original_unchanged = ci.validate_case, ci.unchanged
    def validate(*args):
        events.append('semantic')
        try:
            return original_validate(*args)
        except BaseException as error:
            errors.append(error)
            raise
    monkeypatch.setattr(ci, 'validate_case', validate)
    def preserve(*args):
        events.append('preserve')
        if fail == 'preservation':
            raise preserve_error
        return original_unchanged(*args)
    monkeypatch.setattr(ci, 'unchanged', preserve)
    def export(*args):
        events.append('export')
        assert all(json.loads((destination / (label + '-validation-status.json')).read_bytes())['acceptance'] == 'accepted'
            for label in ci.CASES)
        return {'synthetic': True}
    monkeypatch.setattr(ci, 'export_readback', export)
    return events, errors, package_error, preserve_error


@pytest.mark.parametrize('failure', ['package', 'semantic', 'preservation'])
def test_failure_retains_package_before_semantics_without_acceptance_or_export(ci, monkeypatch, tmp_path, failure):
    destination = tmp_path / 'synthetic-output'
    events, errors, package_error, preserve_error = exercise_context(ci, monkeypatch, destination, fail=failure)
    with pytest.raises((RuntimeError, ValueError)) as observed:
        ci.exercise(destination)
    expected = package_error if failure == 'package' else preserve_error if failure == 'preservation' else errors[0]
    assert observed.value is expected
    assert any('Owned campaign cancellation failed' in note for note in observed.value.__notes__)
    assert events == ({'package': ['package', 'cancel'], 'semantic': ['package', 'semantic', 'cancel'],
        'preservation': ['package', 'semantic', 'preserve', 'cancel']}[failure])
    assert (destination / 'order-attempt/SYNTHETIC.txt').read_bytes() == b'SYNTHETIC package; no native execution'
    status = json.loads((destination / 'order-validation-status.json').read_bytes())
    assert status['acceptance'] == 'unaccepted' and status['preservation'] == 'not_checked'
    assert status['package_validation'] == ('pending' if failure == 'package' else 'passed')
    assert status['semantic_expectation'] == ('not_checked' if failure == 'package' else 'pending')
    assert not (destination / 'application-summary.json').exists()
    assert not (destination / 'final-state.json').exists() and 'export' not in events


def test_all_seven_fake_acceptances_follow_package_semantics_and_preservation(ci, monkeypatch, tmp_path):
    destination = tmp_path / 'synthetic-output'
    events, _, _, _ = exercise_context(ci, monkeypatch, destination)
    ci.exercise(destination)
    assert events == ['package', 'semantic', 'preserve']*7 + ['export', 'preserve']
    summary = json.loads((destination / 'application-summary.json').read_bytes())
    assert set(summary['cases']) == set(ci.CASES) and len(summary['cases']) == 7
    for label in ci.CASES:
        assert json.loads((destination / (label + '-validation-status.json')).read_bytes()) == {
            'package_validation': 'passed', 'semantic_expectation': 'passed', 'preservation': 'passed', 'acceptance': 'accepted'}


def test_unchanged_runtime_and_evidence_caps_remain_fail_closed(ci):
    assert ci.MAX_ATTEMPTS == 7 and ci.ATTEMPT_CAP == 4*1024**2 and ci.CAP == 64*1024**2
    value = campaign(ci, 'order')
    value['attempts'][0]['result']['usage']['wall_time_seconds'] = 120.001
    with pytest.raises(RuntimeError, match='runner exceeded budget'):
        ci.validate_case('order', value)
