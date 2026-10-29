"""Independent mount review with fake identities/API and temporary files only."""
from hashlib import sha256
import importlib.util
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

ROOT = Path(__file__).resolve().parents[2]
SPEC = importlib.util.spec_from_file_location('independent_axi_mount', ROOT / 'scripts/ci_axi_application.py')
ci = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(ci)


@pytest.fixture
def mount(monkeypatch, tmp_path):
    evidence, artifacts = tmp_path / 'evidence', tmp_path / 'artifacts'
    evidence.mkdir()
    artifacts.mkdir()
    monkeypatch.setattr(ci, 'Path', lambda value: artifacts if value == '/artifacts' else Path(value))
    monkeypatch.setattr(ci, 'effective_identity', lambda: (1001, 1002))
    monkeypatch.setattr(ci, 'directory_identity', lambda _: {'uid': 1001, 'gid': 1002, 'mode': '0700'})
    monkeypatch.setattr(ci.subprocess, 'Popen', lambda *a, **k: pytest.fail('unexpected process'))
    monkeypatch.setattr(ci.subprocess, 'run', lambda *a, **k: pytest.fail('unexpected subprocess'))
    monkeypatch.setattr(ci, 'api_json', lambda *a, **k: pytest.fail('unexpected HTTP'))
    return evidence, artifacts


def test_preflight_success_has_only_numeric_identity_and_exclusive_probe_receipt(mount, capsys):
    evidence, _ = mount
    ci.evidence_preflight(evidence)
    assert {p.name for p in evidence.iterdir()} == {'mount-preflight.json'}
    receipt = json.loads((evidence / 'mount-preflight.json').read_bytes())
    assert receipt['probe'] == 'write_read_remove_passed' and receipt['artifact_root'] == 'readable'
    assert receipt['effective_uid'] == 1001 and receipt['effective_gid'] == 1002
    log = json.loads(capsys.readouterr().out)['evidence_mount_preflight']
    assert set(log) == {'effective_uid', 'effective_gid', 'directory'}
    assert str(evidence) not in json.dumps(log)


def test_existing_probe_is_never_read_overwritten_or_deleted(mount):
    evidence, _ = mount
    probe = evidence / '.veriforge-mount-probe'
    probe.write_bytes(b'foreign existing evidence')
    with pytest.raises(FileExistsError):
        ci.evidence_preflight(evidence)
    assert probe.read_bytes() == b'foreign existing evidence'
    assert not (evidence / 'mount-preflight.json').exists()


def test_replaced_probe_is_detected_and_replacement_is_preserved(mount, monkeypatch):
    evidence, _ = mount
    probe, saved = evidence / '.veriforge-mount-probe', evidence / 'saved-original'
    original_open = Path.open
    def replace_before_read(path, mode='r', *args, **kwargs):
        if path == probe and mode == 'rb':
            path.rename(saved)
            path.write_bytes(b'replacement must survive')
        return original_open(path, mode, *args, **kwargs)
    monkeypatch.setattr(Path, 'open', replace_before_read)
    with pytest.raises(RuntimeError) as observed:
        ci.evidence_preflight(evidence)
    assert str(observed.value) == 'evidence probe replaced'
    assert any('refuse removal of replaced' in note for note in observed.value.__notes__)
    monkeypatch.setattr(Path, 'open', original_open)
    assert probe.read_bytes() == b'replacement must survive'
    assert saved.read_bytes() == b'veriforge-owned-evidence-preflight\n'
    assert not (evidence / 'mount-preflight.json').exists()


def test_probe_cleanup_preserves_primary_read_failure_and_secondary_evidence(mount, monkeypatch):
    evidence, _ = mount
    probe = evidence / '.veriforge-mount-probe'
    primary, secondary = OSError('primary probe read I/O failure'), PermissionError('secondary unlink denied')
    original_open, original_unlink = Path.open, Path.unlink
    def fail_read(path, mode='r', *args, **kwargs):
        if path == probe and mode == 'rb':
            raise primary
        return original_open(path, mode, *args, **kwargs)
    def fail_unlink(path, *args, **kwargs):
        if path == probe:
            raise secondary
        return original_unlink(path, *args, **kwargs)
    monkeypatch.setattr(Path, 'open', fail_read)
    monkeypatch.setattr(Path, 'unlink', fail_unlink)
    with pytest.raises(OSError) as observed:
        ci.evidence_preflight(evidence)
    assert observed.value is primary
    assert any('PermissionError' in note and 'secondary unlink denied' in note
        for note in observed.value.__notes__)
    assert not (evidence / 'mount-preflight.json').exists()


def test_probe_cleanup_failure_prevents_all_request_preparation(mount, monkeypatch):
    evidence, _ = mount
    monkeypatch.setattr(ci.sys, 'platform', 'linux')
    monkeypatch.setenv('VERIFORGE_CI_EPHEMERAL_PROJECT', 'vf-axi-ci-999-1')
    monkeypatch.setattr(ci, 'fixture_plans', lambda: pytest.fail('request before failed preflight'))
    original_unlink = Path.unlink
    def fail_unlink(path, *args, **kwargs):
        if path.name == '.veriforge-mount-probe':
            raise PermissionError('fake cleanup denied')
        return original_unlink(path, *args, **kwargs)
    monkeypatch.setattr(Path, 'unlink', fail_unlink)
    with pytest.raises(PermissionError):
        ci.exercise(evidence)
    assert {p.name for p in evidence.iterdir()} == {'.veriforge-mount-probe'}


@pytest.mark.parametrize('user', ['0:1', '1:4294967295', '4294967295:1', '1:-1', '1:2 --privileged', '1:2:3'])
def test_command_rejects_root_unbounded_and_option_injection(monkeypatch, tmp_path, user):
    monkeypatch.setattr(ci, 'OUT', tmp_path)
    evidence = tmp_path / 'application'
    evidence.mkdir()
    with pytest.raises(RuntimeError):
        ci.exercise_command(['FAKE-COMPOSE'], evidence, user)


def test_only_one_off_exercise_gets_host_user_and_existing_caps_survive(monkeypatch, tmp_path):
    monkeypatch.setattr(ci, 'OUT', tmp_path)
    evidence = tmp_path / 'application'
    evidence.mkdir()
    prefix = ['FAKE-COMPOSE']
    args = ci.exercise_command(prefix, evidence, '1001:1002')
    assert prefix == ['FAKE-COMPOSE']
    assert args == prefix + ['run', '--rm', '--no-deps', '--user', '1001:1002', '-v',
        str(evidence) + ':/evidence', 'api', 'python', '/workspace/scripts/ci_axi_application.py', '--exercise']
    services = ci.compose_plan('vf-axi-ci-999-1', 'sha256:' + 'a'*64)['services']
    for role in ('api', 'worker', 'reconciler'):
        assert not {'user', 'privileged', 'cap_add'} & set(services[role])
        assert services[role]['cap_drop'] == ['ALL']
        assert services[role]['security_opt'] == ['no-new-privileges:true']
        assert services[role]['logging']['options'] == {'max-size': '2m', 'max-file': '1', 'compress': 'false'}
    assert [services[r]['mem_limit'] for r in ('api', 'worker', 'reconciler')] == ['512m', '2g', '256m']


def test_prepare_requests_0700_and_refuses_existing_tree(monkeypatch, tmp_path):
    monkeypatch.setattr(ci, 'ROOT', tmp_path)
    monkeypatch.setattr(ci, 'OUT', tmp_path / 'out')
    ci.OUT.mkdir()
    monkeypatch.setattr(ci, 'effective_identity', lambda: (1001, 1002))
    monkeypatch.setattr(ci, 'directory_identity', lambda _: {'uid': 1001, 'gid': 1002, 'mode': '0700'})
    modes = []
    original = Path.mkdir
    def mkdir(path, mode=0o777, **kwargs):
        if path.name == 'application':
            modes.append(mode)
        return original(path, mode=mode, **kwargs)
    monkeypatch.setattr(Path, 'mkdir', mkdir)
    path, user = ci.prepare_evidence_mount()
    assert modes == [0o700] and user == '1001:1002'
    (path / 'preserve').write_bytes(b'original')
    with pytest.raises(RuntimeError, match='already exists'):
        ci.prepare_evidence_mount()
    assert (path / 'preserve').read_bytes() == b'original'


@pytest.mark.parametrize('deny_read', [False, True])
def test_package_readback_remains_independent_and_permission_failure_fails_closed(mount, monkeypatch, tmp_path, deny_read):
    import veriforge.evidence
    _, artifacts = mount
    directory = artifacts / 'fake-attempt'
    directory.mkdir()
    source = directory / 'observed.json'
    content = b'{"synthetic":true}'
    source.write_bytes(content)
    ref = SimpleNamespace(path='observed.json', size_bytes=len(content), sha256=sha256(content).hexdigest())
    experiment = SimpleNamespace(model_dump=lambda **kw: {'synthetic': 'plan'})
    result = SimpleNamespace(model_dump=lambda **kw: {'synthetic': 'result'}, artifacts=[ref])
    monkeypatch.setattr(veriforge.evidence, 'load_attempt', lambda _: (experiment, result))
    campaign = {'plan': {'synthetic': 'plan'}, 'attempts': [{'id': 'fake-attempt', 'result': {'synthetic': 'result'}}]}
    api_calls = []
    monkeypatch.setattr(ci, 'api_json', lambda *a, **k: api_calls.append(a) or {'items': []})
    if deny_read:
        original_read = Path.read_bytes
        def fail_read(path):
            if path == source:
                raise PermissionError('fake retained package denied')
            return original_read(path)
        monkeypatch.setattr(Path, 'read_bytes', fail_read)
        with pytest.raises(PermissionError):
            ci.validate_package(campaign, tmp_path / 'copied')
        assert not api_calls and not (tmp_path / 'copied').exists()
    else:
        ci.validate_package(campaign, tmp_path / 'copied')
        assert (tmp_path / 'copied/observed.json').read_bytes() == content
        assert api_calls == [('GET', '/attempts/fake-attempt/artifacts')]
