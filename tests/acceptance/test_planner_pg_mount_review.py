"""Independent PG mount source review: temp files and fake host observations only."""
import ast
import hashlib
import importlib.util
import json
from pathlib import Path
import subprocess

import pytest

ROOT = Path(__file__).resolve().parents[2]
USER = '4321:987'
IDENTITY = {'uid': 4321, 'gid': 987, 'mode': '0700'}
ROLES = ('mock', 'api', 'runtime-a', 'runtime-b', 'runtime-c', 'controller')


@pytest.fixture
def controller(monkeypatch, tmp_path):
    path = ROOT / 'scripts/ci_planner_postgres.py'
    # Source identity is portable across checkouts; tool evidence is never normalized.
    assert hashlib.sha256(path.read_bytes().replace(b'\r\n', b'\n')).hexdigest() == '3dd41e80bd492ad0ac53ad8e02d5c07c718fc181cfe8d8ed1bee76f9933b9694'
    assert hashlib.sha256((ROOT / 'tests/acceptance/test_planner_postgres_plan.py').read_bytes().replace(b'\r\n', b'\n')).hexdigest() == 'd914f057c142f511a791d156063abe1caa9465272ae2183edea92947b39a9e70'
    def forbidden(*args, **kwargs): pytest.fail('no external operation during mount review')
    for name in ('run', 'Popen', 'check_output'): monkeypatch.setattr(subprocess, name, forbidden)
    spec = importlib.util.spec_from_file_location('independent_pg_mount', path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    monkeypatch.setattr(module, 'ROOT', tmp_path)
    monkeypatch.setattr(module, 'OUT', tmp_path / 'evidence')
    module.OUT.mkdir()
    return module


@pytest.mark.parametrize('user', [None, 4321, 'root', '0:0', '-1:3', '1:-1', '1:2;cmd', '1:2/3',
    '4294967295:1', '1:4294967295', '1:2\n'])
def test_numeric_user_rejects_root_non_numeric_and_out_of_range(controller, user):
    with pytest.raises(RuntimeError): controller.numeric_user(user)


@pytest.mark.parametrize('identity', [(0, 987), (True, 987), (4321, False), (-1, 987), (4321, -1),
    (2**32 - 1, 987), (4321, 2**32 - 1)])
def test_observer_rejects_invalid_actual_identity(controller, monkeypatch, identity):
    monkeypatch.setattr(controller.os, 'geteuid', lambda: identity[0], raising=False)
    monkeypatch.setattr(controller.os, 'getegid', lambda: identity[1], raising=False)
    with pytest.raises(RuntimeError): controller.effective_identity()


def test_new_evidence_directory_is_exclusive_private_and_identity_bound(controller, monkeypatch):
    monkeypatch.setattr(controller, 'effective_identity', lambda: (4321, 987))
    monkeypatch.setattr(controller, 'directory_identity', lambda path: dict(IDENTITY))
    def forbidden(*args, **kwargs): pytest.fail('cannot alter existing permissions/ownership')
    monkeypatch.setattr(Path, 'chmod', forbidden)
    monkeypatch.setattr(controller.os, 'chmod', forbidden)
    monkeypatch.setattr(controller.os, 'chown', forbidden, raising=False)
    modes = []
    original = Path.mkdir
    def mkdir(path, *args, **kwargs):
        modes.append((path, kwargs.get('mode')))
        return original(path, *args, **kwargs)
    monkeypatch.setattr(Path, 'mkdir', mkdir)
    evidence, user = controller.prepare_evidence_mount()
    assert user == USER and evidence == (controller.OUT / 'data').resolve()
    assert (controller.OUT / 'data', 0o700) in modes
    receipt = json.loads((controller.OUT / 'evidence-mount.json').read_bytes())
    assert receipt == {'container_user': USER, 'directory': IDENTITY}
    (evidence / 'original').write_bytes(b'preserved')
    with pytest.raises(RuntimeError): controller.prepare_evidence_mount()
    assert (evidence / 'original').read_bytes() == b'preserved'


@pytest.mark.parametrize('field,value', [('uid', 0), ('gid', 988), ('mode', '0755')])
def test_new_directory_with_wrong_observed_ownership_or_mode_fails(controller, monkeypatch, field, value):
    monkeypatch.setattr(controller, 'effective_identity', lambda: (4321, 987))
    monkeypatch.setattr(controller, 'directory_identity', lambda path: {**IDENTITY, field: value})
    with pytest.raises(RuntimeError, match='identity/mode differs'): controller.prepare_evidence_mount()
    assert not (controller.OUT / 'evidence-mount.json').exists()


@pytest.mark.parametrize('role', ROLES)
def test_each_role_probe_performs_real_tempfile_roundtrip_without_overwriting(controller, monkeypatch, tmp_path, role):
    monkeypatch.setattr(controller, 'effective_identity', lambda: (4321, 987))
    monkeypatch.setattr(controller, 'directory_identity', lambda path: dict(IDENTITY))
    directory = tmp_path / role
    directory.mkdir()
    proof = controller.mount_preflight(directory, USER, role)
    assert proof['effective_uid'] == 4321 and proof['effective_gid'] == 987
    assert proof['observed'] == IDENTITY and proof['probe'] == 'exclusive_write_read_list_delete_passed'
    assert not list(directory.iterdir())
    collision = directory / ('.veriforge-' + role + '-mount-probe')
    collision.write_bytes(b'prior owned evidence')
    with pytest.raises(FileExistsError): controller.mount_preflight(directory, USER, role)
    assert collision.read_bytes() == b'prior owned evidence'


def test_replaced_probe_is_never_deleted(controller, monkeypatch, tmp_path):
    monkeypatch.setattr(controller, 'effective_identity', lambda: (4321, 987))
    monkeypatch.setattr(controller, 'directory_identity', lambda path: dict(IDENTITY))
    directory = tmp_path / 'mount'
    directory.mkdir()
    foreign = directory / 'foreign'
    foreign.write_bytes(b'preserve replacement')
    original = Path.iterdir
    def replace_then_list(path):
        if path == directory and foreign.exists():
            foreign.replace(directory / '.veriforge-api-mount-probe')
        return original(path)
    monkeypatch.setattr(Path, 'iterdir', replace_then_list)
    with pytest.raises(RuntimeError, match='probe replaced') as error:
        controller.mount_preflight(directory, USER, 'api')
    assert (directory / '.veriforge-api-mount-probe').read_bytes() == b'preserve replacement'
    assert error.value.__notes__ == ['Probe deletion also failed: RuntimeError']


def test_listing_and_deletion_failures_preserve_primary(controller, monkeypatch, tmp_path):
    monkeypatch.setattr(controller, 'effective_identity', lambda: (4321, 987))
    monkeypatch.setattr(controller, 'directory_identity', lambda path: dict(IDENTITY))
    first = OSError('first synthetic listing failure')
    def listing(*args): raise first
    def delete(*args): raise PermissionError('secondary deletion failure')
    monkeypatch.setattr(Path, 'iterdir', listing)
    monkeypatch.setattr(Path, 'unlink', delete)
    with pytest.raises(OSError) as error: controller.mount_preflight(tmp_path, USER, 'mock')
    assert error.value is first and first.__notes__ == ['Probe deletion also failed: PermissionError']


def test_plan_changes_only_app_identity_without_privilege_or_resource_expansion(controller, tmp_path):
    plan = controller.compose_plan('vf-planner-pg-ci-123-1', 'sha256:' + 'a' * 64, tmp_path / 'controls', tmp_path / 'data', USER)
    for role, value in plan['services'].items():
        if role in ROLES:
            assert value['user'] == USER
            assert value['cap_drop'] == ['ALL'] and value['security_opt'] == ['no-new-privileges:true']
            assert value['mem_limit'] == ('128m' if role == 'mock' else '512m')
            assert value['cpus'] == (0.25 if role == 'mock' else 1)
            assert value['pids_limit'] == 128
            control = str(tmp_path / 'controls') + ':/controls' + ('' if role == 'controller' else ':ro')
            assert control in value['volumes']
        else: assert 'user' not in value
        assert value['restart'] == 'no' and not {'ports', 'privileged', 'cap_add'} & value.keys()
        assert value['logging']['options'] == {'max-size': '1m', 'max-file': '1', 'compress': 'false'}


@pytest.mark.parametrize('fault', [None, 'effective-user', 'wrong-path', 'wrong-role', 'incomplete', 'process'])
def test_six_bounded_exact_role_preflights_and_early_failure(controller, monkeypatch, fault):
    calls = []
    first = PermissionError('synthetic mount denial')
    class FakeHost:
        prefix = ['FAKE', 'COMPOSE']
        def run(self, args, name, timeout):
            calls.append((args, name, timeout))
            i = args.index('-c')
            role, user, paths = args[i + 2], args[i + 3], args[i + 4:]
            assert user == USER and timeout == 15
            assert args[:i] == self.prefix + ['run', '--rm', '--no-deps', '--pull', 'never', '--entrypoint', 'python', role]
            code = args[i + 1]
            compile(code, '<inert-preflight>', 'exec')
            assert not any(word in code for word in ('urlopen', 'MetaProvider', 'subprocess', 'Qualification'))
            if fault == 'process' and role == 'runtime-a': raise first
            proof = {'role': role, 'mounts': [{'directory': path, 'effective_uid': 4321, 'effective_gid': 987,
                'observed': IDENTITY, 'probe': 'exclusive_write_read_list_delete_passed'} for path in paths]}
            if role == 'runtime-a':
                if fault == 'effective-user': proof['mounts'][0]['effective_gid'] = 988
                elif fault == 'wrong-path': proof['mounts'][0]['directory'] = '/unowned'
                elif fault == 'wrong-role': proof['role'] = 'controller'
                elif fault == 'incomplete': proof['mounts'][0]['probe'] = 'write-only'
            return controller.PROOF_MARKER.encode() + json.dumps(proof).encode() + b'\n'
    if fault is None:
        controller.preflight_writing_roles(FakeHost(), USER)
        assert len(calls) == 6
        for (args, name, timeout), role in zip(calls, ROLES):
            assert name == 'mount-preflight-' + role
            assert ('/controls' in args) == (role == 'controller')
            assert (controller.OUT / (name + '.json')).is_file()
    else:
        with pytest.raises((RuntimeError, PermissionError)) as error:
            controller.preflight_writing_roles(FakeHost(), USER)
        assert len(calls) == 3
        if fault == 'process': assert error.value is first
        assert not (controller.OUT / 'mount-preflight-runtime-a.json').exists()


def test_host_preflights_precede_any_storage_or_qualification_operation():
    source = (ROOT / 'scripts/ci_planner_postgres.py').read_text()
    tree = ast.parse(source)
    main = next(n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == 'main')
    text = ast.get_source_segment(source, main)
    assert text.index("'image-default-user'") < text.index('prepare_evidence_mount()')
    assert text.index('mkdir(mode=0o700)') < text.index('preflight_writing_roles(host, user)')
    assert text.index('preflight_writing_roles(host, user)') < text.index("'storage')") < text.index("'actual-process-qualification'")
