"""Independent bounded marked-proof parser review; no command or service execution."""
import importlib.util
import hashlib
import json
from pathlib import Path
import subprocess

import pytest

ROOT = Path(__file__).resolve().parents[2]
MARKER = b'VERIFORGE_PG_MOUNT_PROOF_V1='
PREFIX = (b' Network vf-planner-pg-ci-40952295201-1_default  Creating\n'
          b' Network vf-planner-pg-ci-40952295201-1_default  Created\n')


@pytest.fixture
def module(monkeypatch, tmp_path):
    for name, digest in {
        'scripts/ci_planner_postgres.py': '3dd41e80bd492ad0ac53ad8e02d5c07c718fc181cfe8d8ed1bee76f9933b9694',
        'tests/acceptance/test_planner_postgres_plan.py': 'ffbbd5fb68e2e22df37aac2db071732743e222d39e87373c863d1305ac54af90',
    }.items():
        # Portable source identity only; retained tool bytes are not normalized.
        assert hashlib.sha256((ROOT / name).read_bytes().replace(b'\r\n', b'\n')).hexdigest() == digest
    def forbidden(*args, **kwargs): pytest.fail('proof review cannot launch operations')
    for name in ('run', 'Popen', 'check_output'): monkeypatch.setattr(subprocess, name, forbidden)
    spec = importlib.util.spec_from_file_location('independent_mount_proof', ROOT / 'scripts/ci_planner_postgres.py')
    value = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(value)
    monkeypatch.setattr(value, 'OUT', tmp_path)
    return value


def payload(role='mock'):
    return {'role': role, 'mounts': [
        {'directory': path, 'effective_uid': 1001, 'effective_gid': 1001,
         'observed': {'uid': 1001, 'gid': 1001, 'mode': '0700'},
         'probe': 'exclusive_write_read_list_delete_passed'}
        for path in (['/evidence', '/controls'] if role == 'controller' else ['/evidence'])]}


def marked(value): return MARKER + json.dumps(value, sort_keys=True).encode() + b'\n'


def test_exact_actual_prefix_and_unmarked_json_have_no_proof_authority(module):
    # Exact original retained protocol, not reclassified as a new actual success.
    original = PREFIX + json.dumps(payload(), sort_keys=True).encode() + b'\n'
    with pytest.raises(RuntimeError): module.parse_mount_proof(original, 'mock', '1001:1001', ['/evidence'])
    corrected = PREFIX + b'{"unrelated":"unmarked JSON"}\n' + marked(payload()) + b'other progress\n'
    before = bytes(corrected)
    assert module.parse_mount_proof(corrected, 'mock', '1001:1001', ['/evidence']) == payload()
    assert corrected == before


@pytest.mark.parametrize('role', ['mock', 'api', 'runtime-a', 'runtime-b', 'runtime-c', 'controller'])
def test_one_exact_proof_for_each_role(module, role):
    paths = ['/evidence', '/controls'] if role == 'controller' else ['/evidence']
    assert module.parse_mount_proof(PREFIX + marked(payload(role)), role, '1001:1001', paths) == payload(role)


@pytest.mark.parametrize('kind', ['missing', 'duplicate', 'other-version', 'unknown-plus-valid', 'leading-space',
    'garbage-json', 'trailing-json', 'invalid-utf8', 'wrong-type', 'null', 'array', 'duplicate-top',
    'duplicate-mount', 'duplicate-observed', 'nan', 'infinity'])
def test_protocol_ambiguity_and_malformed_proofs_fail_closed(module, kind):
    valid = marked(payload())
    variants = {
        'missing': PREFIX,
        'duplicate': valid + valid,
        'other-version': valid.replace(b'_V1=', b'_V99='),
        'unknown-plus-valid': valid.replace(b'_V1=', b'_V99=') + valid,
        'leading-space': b' ' + valid,
        'garbage-json': MARKER + b'{broken}\n',
        'trailing-json': valid.rstrip() + b' {}\n',
        'invalid-utf8': MARKER + b'\xff\n',
        'wrong-type': valid.decode(),
        'null': MARKER + b'null\n',
        'array': MARKER + b'[]\n',
        'duplicate-top': valid.replace(b'"role": "mock"', b'"role": "mock", "role": "mock"'),
        'duplicate-mount': valid.replace(b'"directory": "/evidence"', b'"directory": "/evidence", "directory": "/evidence"'),
        'duplicate-observed': valid.replace(b'"uid": 1001', b'"uid": 1001, "uid": 1001'),
        'nan': valid.replace(b'"effective_uid": 1001', b'"effective_uid": NaN'),
        'infinity': valid.replace(b'"effective_uid": 1001', b'"effective_uid": Infinity'),
    }
    with pytest.raises((RuntimeError, ValueError, UnicodeError)):
        module.parse_mount_proof(variants[kind], 'mock', '1001:1001', ['/evidence'])


@pytest.mark.parametrize('mutation', ['top-extra', 'mount-extra', 'observed-extra', 'missing-observed',
    'wrong-role', 'wrong-path', 'uid', 'gid', 'owner-uid', 'owner-gid', 'bool-uid', 'bool-owner',
    'float-gid', 'mode', 'status', 'no-mount', 'extra-mount'])
def test_schema_and_identity_are_exact(module, mutation):
    value = payload()
    mount = value['mounts'][0]
    if mutation == 'top-extra': value['future'] = 'untrusted'
    elif mutation == 'mount-extra': mount['future'] = 'untrusted'
    elif mutation == 'observed-extra': mount['observed']['future'] = 'untrusted'
    elif mutation == 'missing-observed': del mount['observed']
    elif mutation == 'wrong-role': value['role'] = 'api'
    elif mutation == 'wrong-path': mount['directory'] = '/controls'
    elif mutation == 'uid': mount['effective_uid'] = 1002
    elif mutation == 'gid': mount['effective_gid'] = 1002
    elif mutation == 'owner-uid': mount['observed']['uid'] = 0
    elif mutation == 'owner-gid': mount['observed']['gid'] = 0
    elif mutation == 'bool-uid': mount['effective_uid'] = True
    elif mutation == 'bool-owner': mount['observed']['uid'] = True
    elif mutation == 'float-gid': mount['effective_gid'] = 1001.0
    elif mutation == 'mode': mount['observed']['mode'] = '0777'
    elif mutation == 'status': mount['probe'] = 'write-only'
    elif mutation == 'no-mount': value['mounts'] = []
    else: value['mounts'].append(dict(mount))
    with pytest.raises(RuntimeError): module.parse_mount_proof(marked(value), 'mock', '1001:1001', ['/evidence'])


def test_controller_requires_ordered_exact_mounts_and_expected_association(module):
    value = payload('controller')
    raw = marked(value)
    for role, user, paths in [('controller', '1001:1001', ['/controls', '/evidence']),
        ('controller', '1002:1001', ['/evidence', '/controls']), ('controller', '1001:1001', ['/evidence']),
        ('unknown', '1001:1001', ['/evidence'])]:
        with pytest.raises(RuntimeError): module.parse_mount_proof(raw, role, user, paths)
    value['mounts'].reverse()
    with pytest.raises(RuntimeError): module.parse_mount_proof(marked(value), 'controller', '1001:1001', ['/evidence', '/controls'])


def test_exact_line_and_total_output_byte_boundaries(module):
    line = marked(payload()).rstrip(b'\n')
    exact = line + b' ' * (4096 - len(line))
    assert module.parse_mount_proof(exact, 'mock', '1001:1001', ['/evidence']) == payload()
    with pytest.raises(RuntimeError): module.parse_mount_proof(exact + b' ', 'mock', '1001:1001', ['/evidence'])
    total = b'x' * (1024 * 1024 - len(line) - 1) + b'\n' + line
    assert len(total) == 1024 * 1024
    assert module.parse_mount_proof(total, 'mock', '1001:1001', ['/evidence']) == payload()
    with pytest.raises(RuntimeError): module.parse_mount_proof(b'x' + total, 'mock', '1001:1001', ['/evidence'])


def test_six_emitters_keep_execution_bounds_and_preserve_raw_noisy_output(module):
    logs = []
    class FakeHost:
        prefix = ['INERT', 'COMPOSE']
        def run(self, args, name, timeout):
            i = args.index('-c')
            role, user, paths = args[i + 2], args[i + 3], args[i + 4:]
            assert args[:i] == self.prefix + ['run', '--rm', '--no-deps', '--pull', 'never', '--entrypoint', 'python', role]
            assert timeout == 15 and user == '1001:1001'
            assert MARKER.decode() in args[i + 1]
            compile(args[i + 1], '<inert-fixed-emitter>', 'exec')
            assert paths == (['/evidence', '/controls'] if role == 'controller' else ['/evidence'])
            output = PREFIX + marked(payload(role))
            logs.append(output)
            return output
    module.preflight_writing_roles(FakeHost(), '1001:1001')
    assert len(logs) == 6 and all(raw.startswith(PREFIX) for raw in logs)
    for role in module.WRITING_ROLES:
        assert json.loads((module.OUT / ('mount-preflight-' + role + '.json')).read_bytes()) == payload(role)
