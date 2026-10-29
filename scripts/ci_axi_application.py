"""Manual hosted-only, ephemeral HTTP/PostgreSQL/RQ/RTL acceptance.

Seven attempts maximum: FIFO/arithmetic controls and five AXI cases. Importing
this module performs no I/O. No private operator scripts or user state are used.
"""
from __future__ import annotations

import argparse
import ast
import hashlib
from io import BytesIO
import json
import os
from pathlib import Path
import re
import shutil
import signal
import stat
import subprocess
import sys
import time
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen
from zipfile import ZipFile

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / 'ci-artifacts/axi-application'
BUILDKIT = 'moby/buildkit:buildx-stable-1@sha256:cec9f139f45e93c5c69c60f8b07cfad9f43f4ef6b6a6cd917527fea5ff2e3dea'
OWNER = 'io.veriforge.ci.owner'
CAP = 64 * 1024 * 1024
ATTEMPT_CAP = 4 * 1024 * 1024
MAX_ATTEMPTS = 7
CASES = ('fifo', 'arithmetic', 'control', 'order', 'strobe', 'response', 'replay')
STOP = False


def require(value, message):
    if not value:
        raise RuntimeError(message)


def jhash(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(',', ':'),
                                    ensure_ascii=False, allow_nan=False).encode()).hexdigest()


def write(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, indent=2, sort_keys=True) + '\n')


def project_name(environ):
    run = environ.get('GITHUB_RUN_ID', '')
    attempt = environ.get('GITHUB_RUN_ATTEMPT', '')
    require(bool(re.fullmatch(r'[0-9]{1,20}', run)) and bool(re.fullmatch(r'[0-9]{1,4}', attempt)), 'invalid hosted run identity')
    return 'vf-axi-ci-' + run + '-' + attempt


def migration_head(files):
    """Inspect committed literal metadata without importing migrations."""
    revisions = {}
    for name, data in files.items():
        if not name.startswith('migrations/versions/') or not name.endswith('.py'):
            continue
        values = {}
        for node in ast.parse(data).body:
            if isinstance(node, (ast.Assign, ast.AnnAssign)):
                targets = node.targets if isinstance(node, ast.Assign) else [node.target]
                for target in targets:
                    if isinstance(target, ast.Name) and target.id in ('revision', 'down_revision'):
                        values[target.id] = ast.literal_eval(node.value)
        require(set(values) == {'revision', 'down_revision'}, 'missing literal migration metadata')
        revision, parent = values['revision'], values['down_revision']
        require(isinstance(revision, str) and revision not in revisions, 'duplicate/non-string revision')
        require(parent is None or isinstance(parent, str), 'branched migration unsupported')
        revisions[revision] = parent
    require(revisions and all(p is None or p in revisions for p in revisions.values()), 'incomplete migration closure')
    heads = set(revisions) - {p for p in revisions.values() if p}
    require(len(heads) == 1, 'migration head must be unique')
    head = next(iter(heads))
    seen, cursor = set(), head
    while cursor is not None:
        require(cursor not in seen, 'migration cycle')
        seen.add(cursor)
        cursor = revisions[cursor]
    require(seen == set(revisions), 'disconnected migration history')
    return head


def compose_plan(project, image):
    require(bool(re.fullmatch(r'vf-axi-ci-[0-9]{1,20}-[0-9]{1,4}', project)), 'unsafe project')
    labels = {OWNER: project}
    environment = {'VERIFORGE_DATABASE_URL': 'postgresql+psycopg://veriforge@postgres:5432/veriforge',
                   'VERIFORGE_REDIS_URL': 'redis://valkey:6379/0', 'VERIFORGE_ARTIFACT_ROOT': '/artifacts',
                   'PYTHONDONTWRITEBYTECODE': '1', 'VERIFORGE_CI_EPHEMERAL_PROJECT': project}
    app = {'image': image, 'environment': environment, 'volumes': ['artifacts:/artifacts'],
           'labels': labels, 'restart': 'no', 'pids_limit': 128, 'cpus': 1,
           'mem_limit': '512m', 'cap_drop': ['ALL'], 'security_opt': ['no-new-privileges:true'],
           'logging': {'driver': 'local', 'options': {'max-size': '2m', 'max-file': '1', 'compress': 'false'}}}
    services = {
        'postgres': {'image': 'postgres:16-alpine', 'environment': {'POSTGRES_USER': 'veriforge',
                     'POSTGRES_DB': 'veriforge', 'POSTGRES_HOST_AUTH_METHOD': 'trust'},
                     'volumes': ['database:/var/lib/postgresql/data'], 'mem_limit': '512m',
                     'cpus': 1, 'pids_limit': 128, 'labels': labels, 'restart': 'no'},
        'valkey': {'image': 'valkey/valkey:8.1.4-alpine', 'command': ['valkey-server', '--appendonly', 'no'],
                   'volumes': ['broker:/data'], 'mem_limit': '256m', 'cpus': 0.5,
                   'pids_limit': 64, 'labels': labels, 'restart': 'no'},
        'api': {**app, 'ports': ['127.0.0.1::8000'],
                'command': ['uvicorn', 'veriforge.api.app:app', '--host', '0.0.0.0', '--port', '8000']},
        'worker': {**app, 'mem_limit': '2g', 'cpus': 2, 'pids_limit': 256,
                   'command': ['rq', 'worker', '--url', 'redis://valkey:6379/0', '--name', project + '-worker', 'veriforge']},
        'reconciler': {**app, 'mem_limit': '256m', 'cpus': 0.5,
                       'command': ['python', '-m', 'veriforge.core.worker', '--loop']},
    }
    return {'services': services, 'volumes': {v: {'labels': labels} for v in ('database', 'broker', 'artifacts')},
            'networks': {'default': {'labels': labels}}}


def validate_case(label, campaign, source=None):
    require(campaign['execution_status'] == 'completed' and len(campaign['attempts']) == 1, 'case did not complete exactly once')
    result = campaign['attempts'][0]['result']
    expected = {'fifo': ('pass_within_scope', 5, None), 'arithmetic': ('pass_within_scope', 15, None),
                'control': ('pass_within_scope', 149, None),
                'order': ('property_failure', 11, (7, 'AXIL_PROGRESS', 'axil.response_bound')),
                'strobe': ('design_mismatch', 9, (7, 'AXIL_READ', 'axil.read_value')),
                'response': ('property_failure', 7, (4, 'AXIL_HANDSHAKE', 'axil.stalled_response'))}
    if label == 'replay':
        require(source is not None, 'replay source missing')
        require(campaign['source_attempt_id'] == source['attempts'][0]['id'], 'replay lineage changed')
        require(campaign['plan'] == source['plan'] and campaign['identity'] == source['identity'], 'replay plan/identity changed')
        require(campaign['verdict'] == 'design_mismatch' and result['failure_signature'] == source['attempts'][0]['result']['failure_signature'], 'replay failure changed')
    else:
        verdict, cycles, failure = expected[label]
        require(campaign['verdict'] == verdict and result['usage']['cycles'] == cycles, 'unexpected checker result/cycles')
        signature = result['failure_signature']
        if failure:
            require(signature is not None and (signature['first_cycle'], signature['requirement_id'], signature['failure_class']) == failure, 'wrong semantic fault')
        else:
            require(signature is None, 'control has a failure')
    require(result['identity']['experiment_hash'] == jhash(campaign['plan']), 'experiment identity changed')
    require(all(result['identity'][k] == v for k, v in campaign['identity'].items()), 'frozen identity changed')
    require(result['usage']['wall_time_seconds'] <= 120, 'runner exceeded budget')
    versions = {t['name']: t['version'] for t in result['tool_versions']}
    require('5.042' in versions.get('verilator', '') and versions.get('cocotb') == '2.0.1', 'unexpected actual simulator versions')
    if label not in ('fifo',):
        require(result['trace'] == [], 'native family coerced to FIFO trace')
    return result


def unchanged(before, after):
    """Preserve complete finalized rows/files; new rows are permitted."""
    for table, rows in before['tables'].items():
        require(table in after['tables'], 'old table missing: ' + table)
        current = {jhash(row) for row in after['tables'][table]}
        require(all(jhash(row) in current for row in rows), 'old rows changed: ' + table)
    require(all(k in after['files'] and jhash(after['files'][k]) == jhash(v) for k, v in before['files'].items()), 'old artifact bytes changed')


def api(method, path, body=None, key=None):
    headers = {}
    if key:
        headers['Idempotency-Key'] = key
    data = None if body is None else json.dumps(body).encode()
    if data is not None:
        headers['Content-Type'] = 'application/json'
    request = Request('http://api:8000/api' + path, data=data, method=method, headers=headers)
    with urlopen(request, timeout=5) as response:
        content = response.read(ATTEMPT_CAP + 1)
        require(len(content) <= ATTEMPT_CAP, 'HTTP evidence cap')
        return content, {key.lower(): value for key, value in response.headers.items()}


def api_json(method, path, body=None, key=None):
    return json.loads(api(method, path, body, key)[0])


def fixture_plans():
    from veriforge.contracts import Experiment
    from veriforge.arithmetic.runner import directed_experiment as arithmetic
    from veriforge.axi_lite.runner import directed_experiment, witness_experiment
    fifo = Experiment(experiment_id='ci-fifo', fixture_id='fifo_correct', objective_ids=('FIFO_BEHAVIOR',), operations=(
        {'op': 'reset'}, {'op': 'write', 'value': 17}, {'op': 'reset'}, {'op': 'write', 'value': 42}, {'op': 'read'}))
    plans = {'fifo': fifo, 'arithmetic': arithmetic(), 'control': directed_experiment(),
             'order': witness_experiment('order', 'axil_aw_w_order_bug'),
             'strobe': witness_experiment('strobe', 'axil_strobe_bug'),
             'response': witness_experiment('response', 'axil_response_stability_bug')}
    from veriforge.api.app import CreateCampaign
    result = {}
    for label, plan in plans.items():
        data = plan.model_dump(mode='json')
        data['experiment_id'] = 'ci-application-' + label
        data['budget'].update(wall_time_seconds=120.0, max_artifact_bytes=ATTEMPT_CAP)
        adapter = 'rtl.fifo.v1' if label == 'fifo' else 'rtl.arithmetic.v1' if label == 'arithmetic' else 'rtl.axi_lite.v1'
        body = {'project_id': data['fixture_id'], 'adapter_id': adapter, 'plan': data}
        CreateCampaign.model_validate(body)
        result[label] = body
    return result


def snapshot():
    from sqlalchemy import inspect, text
    from redis import Redis
    from veriforge.core.database import make_engine
    tables = {}
    with make_engine().connect() as connection:
        connection.execute(text('SET TRANSACTION READ ONLY'))
        revision = connection.scalar(text('select version_num from alembic_version'))
        for name in inspect(connection).get_table_names():
            require(bool(re.fullmatch('[a-z0-9_]+', name)), 'unsafe table name')
            tables[name] = [row[0] for row in connection.execute(text('select to_jsonb(t) from ' + name + ' t'))]
    files = {}
    for path in Path('/artifacts').rglob('*'):
        require(not path.is_symlink(), 'artifact symlink')
        if path.is_file():
            data = path.read_bytes()
            files[path.relative_to('/artifacts').as_posix()] = {'size_bytes': len(data), 'sha256': hashlib.sha256(data).hexdigest()}
    require(sum(v['size_bytes'] for v in files.values()) <= 48 * 1024 * 1024, 'artifact volume cap')
    broker = Redis.from_url(os.environ['VERIFORGE_REDIS_URL'])
    queues = {'queue': broker.llen('rq:queue:veriforge')}
    queues.update({key: broker.zcard('rq:' + key + ':veriforge') for key in ('wip', 'deferred', 'scheduled', 'failed')})
    return {'revision': revision, 'tables': tables, 'files': files, 'queues': queues}


def idle_snapshot():
    deadline = time.monotonic() + 30
    while True:
        require(not STOP and time.monotonic() < deadline, 'queue settlement deadline')
        result = snapshot()
        if not any(result['queues'].values()):
            require(not any(a['execution_status'] in ('queued', 'running') for a in result['tables']['attempts']), 'active DB attempt')
            require(not any(a['status'] in ('queued', 'running') for a in result['tables']['evidence_exports']), 'active DB export')
            return result
        time.sleep(1)


def validate_package(campaign, destination):
    from veriforge.evidence import load_attempt
    directory = Path('/artifacts') / campaign['attempts'][0]['id']
    experiment, result = load_attempt(directory)  # Installed checker/schema only; never import copied checker.
    require(experiment.model_dump(mode='json') == campaign['plan'] and result.model_dump(mode='json') == campaign['attempts'][0]['result'], 'native package/DB mismatch')
    for ref in result.artifacts:
        path = directory / ref.path
        require(not path.is_symlink() and path.resolve().is_relative_to(directory.resolve()), 'unsafe artifact path')
        data = path.read_bytes()
        require(len(data) == ref.size_bytes and hashlib.sha256(data).hexdigest() == ref.sha256, 'manifest bytes changed')
    require(sum(p.stat().st_size for p in directory.rglob('*') if p.is_file()) <= ATTEMPT_CAP, 'attempt package cap')
    shutil.copytree(directory, destination)
    listed = api_json('GET', '/attempts/' + campaign['attempts'][0]['id'] + '/artifacts')
    write(destination.parent / (destination.name + '-artifact-list.json'), listed)
    for ref in listed['items']:
        if ref['downloadable']:
            raw, headers = api('GET', '/attempts/' + campaign['attempts'][0]['id'] + '/artifacts/' + ref['artifact_id'] + '/download')
            require(raw == (directory / ref['path']).read_bytes() and headers.get('x-content-type-options') == 'nosniff', 'download differs/header')


def export_readback(source, directory):
    attempt = source['attempts'][0]['id']
    job = api_json('POST', '/attempts/' + attempt + '/exports', key='ci-axi-export')
    repeated = api_json('POST', '/attempts/' + attempt + '/exports', key='ci-axi-export')
    require(job['id'] == repeated['id'], 'duplicate export job')
    deadline = time.monotonic() + 90
    while job['status'] in ('queued', 'running'):
        require(not STOP and time.monotonic() < deadline, 'export deadline')
        time.sleep(1)
        job = api_json('GET', '/exports/' + job['id'])
    require(job['status'] == 'completed', 'export execution failed')
    raw, headers = api('GET', '/exports/' + job['id'] + '/download')
    require(len(raw) == job['size_bytes'] and hashlib.sha256(raw).hexdigest() == job['sha256'], 'ZIP transport hash')
    require(headers.get('x-content-type-options') == 'nosniff' and headers.get('content-type', '').split(';')[0] == 'application/zip', 'ZIP header')
    original = Path('/artifacts') / attempt
    with ZipFile(BytesIO(raw)) as archive:
        names = archive.namelist()
        require(archive.testzip() is None and len(names) == len(set(names)), 'ZIP CRC/duplicate')
        manifest = json.loads(archive.read('manifest.json'))
        require(manifest['identity'] == source['attempts'][0]['result']['identity'] and manifest['experiment'] == source['plan'], 'ZIP identity/plan')
        require(manifest['adapter_id'] == 'rtl.axi_lite.v1' and manifest['trace'] == [], 'wrong family/FIFO coercion')
        require(manifest['family_trace'] == {'schema_version': 'axi_lite.trace.v1', 'artifact': 'artifacts/observed.json'}, 'native trace metadata')
        require(set(names) == {'manifest.json', 'experiment.json', 'REPORT.txt'} | {'artifacts/' + r['path'] for r in manifest['artifacts']}, 'ZIP entry closure')
        require({'artifacts/registers.sv', 'artifacts/checker-contracts.py', 'artifacts/check.json'} <= set(names), 'AXI evidence missing')
        require(not any('artifacts/' + p in names for p in ('job.json', 'environment.json', 'execution.log', 'fifo.sv', 'multiplier.sv')), 'private/wrong-family export')
        require(json.loads(archive.read('experiment.json')) == source['plan'], 'ZIP experiment differs')
        for ref in manifest['artifacts']:
            data = archive.read('artifacts/' + ref['path'])
            require(data == (original / ref['path']).read_bytes() and len(data) == ref['size_bytes'] and hashlib.sha256(data).hexdigest() == ref['sha256'], 'ZIP original bytes differ')
    (directory / 'axi-export.zip').write_bytes(raw)
    write(directory / 'export.json', job)
    return job


def effective_identity():
    uid, gid = os.geteuid(), os.getegid()
    require(type(uid) is int and type(gid) is int and 0 <= uid < 2**32 - 1 and 0 <= gid < 2**32 - 1,
            'invalid effective numeric identity')
    return uid, gid


def directory_identity(path):
    info = path.stat()
    return {'uid': info.st_uid, 'gid': info.st_gid, 'mode': format(stat.S_IMODE(info.st_mode), '04o')}


def prepare_evidence_mount():
    """Only a newly created fixed child of this checkout's owned output tree."""
    uid, gid = effective_identity()
    require(uid != 0, 'hosted exercise requires non-root runner identity')
    output = OUT.resolve()
    require(not OUT.is_symlink() and output.is_relative_to(ROOT.resolve()), 'unsafe evidence output root')
    evidence = OUT / 'application'
    require(not evidence.exists() and not evidence.is_symlink(), 'evidence directory already exists')
    require(evidence.resolve() == output / 'application', 'unsafe evidence mount')
    evidence.mkdir(mode=0o700)  # No chmod/chown, existing directory, or shared volume changes.
    observed = directory_identity(evidence)
    require(observed == {'uid': uid, 'gid': gid, 'mode': '0700'}, 'evidence directory identity/mode differs')
    write(OUT / 'evidence-mount.json', {'container_user': f'{uid}:{gid}', 'directory': observed})
    return evidence.resolve(), f'{uid}:{gid}'


def exercise_command(prefix, evidence, user):
    require(bool(re.fullmatch(r'[0-9]{1,10}:[0-9]{1,10}', user)), 'unsafe exercise numeric identity')
    uid, gid = (int(part) for part in user.split(':'))
    require(0 < uid < 2**32 - 1 and 0 <= gid < 2**32 - 1, 'invalid exercise numeric identity')
    require(evidence == OUT.resolve() / 'application' and not evidence.is_symlink() and evidence.is_dir(), 'unsafe evidence mount')
    return prefix + ['run', '--rm', '--no-deps', '--user', user, '-v', str(evidence) + ':/evidence',
                     'api', 'python', '/workspace/scripts/ci_axi_application.py', '--exercise']


def evidence_preflight(destination):
    """Verify this controller's bind mount before source writes or any POST."""
    require(destination.is_dir() and not destination.is_symlink(), 'unsafe evidence directory')
    uid, gid = effective_identity()
    observed = directory_identity(destination)
    # Numeric-only diagnostics survive in the bounded command log even when
    # the bind mount cannot retain its own failure receipt.
    print(json.dumps({'evidence_mount_preflight': {'effective_uid': uid, 'effective_gid': gid,
          'directory': observed}}, sort_keys=True), flush=True)
    require((observed['uid'], observed['gid']) == (uid, gid), 'evidence directory owner differs from exercise identity')
    probe = destination / '.veriforge-mount-probe'
    payload = b'veriforge-owned-evidence-preflight\n'
    created, first_failure = None, None
    try:
        with probe.open('xb') as stream:
            created = os.fstat(stream.fileno())
            stream.write(payload)
        with probe.open('rb') as stream:
            current = os.fstat(stream.fileno())
            require((current.st_dev, current.st_ino) == (created.st_dev, created.st_ino), 'evidence probe replaced')
            require(stream.read(len(payload) + 1) == payload, 'evidence probe readback differs')
    except BaseException as error:
        first_failure = error
        raise
    finally:
        if created is not None:
            try:
                current = probe.lstat()
                require(not probe.is_symlink() and (current.st_dev, current.st_ino) == (created.st_dev, created.st_ino), 'refuse removal of replaced evidence probe')
                probe.unlink()
                require(not probe.exists(), 'evidence probe removal failed')
            except BaseException as cleanup_error:
                if first_failure is None:
                    raise
                first_failure.add_note('Evidence probe cleanup also failed: ' +
                                       type(cleanup_error).__name__ + ': ' + str(cleanup_error)[:500])
    # A non-root exercise needs read/traverse access to the service-owned volume.
    # Completed package bytes are independently read/validated later as before.
    artifacts = Path('/artifacts')
    require(artifacts.is_dir() and not artifacts.is_symlink(), 'artifact volume unavailable')
    next(artifacts.iterdir(), None)
    write(destination / 'mount-preflight.json', {'effective_uid': uid, 'effective_gid': gid,
          'directory': observed, 'probe': 'write_read_remove_passed', 'artifact_root': 'readable'})


def exercise(destination=None):
    require(sys.platform == 'linux' and bool(re.fullmatch(r'vf-axi-ci-[0-9]+-[0-9]+', os.environ.get('VERIFORGE_CI_EPHEMERAL_PROJECT', ''))), 'ephemeral controller only')
    destination = Path('/evidence') if destination is None else Path(destination)
    destination.mkdir(exist_ok=True)
    evidence_preflight(destination)
    installed = Path('/workspace/ci-source-manifest.json')
    if installed.exists():
        metadata = json.loads(installed.read_text())
        observed = {}
        for name, expected in metadata['files'].items():
            if name.startswith(('src/', 'targets/', 'migrations/', 'tests/integration/')) or name in ('pyproject.toml', 'uv.lock', 'alembic.ini', 'scripts/ci_axi_application.py'):
                data = (Path('/workspace') / name).read_bytes()
                actual = hashlib.sha256(data).hexdigest()
                require(actual == expected, 'installed source differs: ' + name)
                observed[name] = actual
        write(destination / 'installed-source-vector.json', {'commit': metadata['commit'], 'files': observed})
    else:
        require(destination != Path('/evidence'), 'installed source identity manifest missing')
    plans = fixture_plans()
    write(destination / 'frozen-requests.json', {'requests': plans, 'hashes': {k: jhash(v) for k, v in plans.items()}})
    before = idle_snapshot()
    require(not before['tables']['campaigns'] and not before['files'], 'gate requires empty new database/artifacts')
    require(set(api_json('GET', '/health')['evidence_adapters']) == {'rtl.fifo.v1', 'rtl.arithmetic.v1', 'rtl.axi_lite.v1'}, 'API admission missing')
    results, baseline, active = {}, before, None
    first_failure = None
    try:
        for label in CASES:
            require(not STOP and len(results) < MAX_ATTEMPTS, 'finite attempt bound')
            if label == 'replay':
                source = results['strobe']
                write(destination / 'frozen-replay-request.json', {'source_attempt_id': source['attempts'][0]['id'], 'body': None, 'plan_hash': jhash(source['plan']), 'identity': source['identity']})
                campaign = api_json('POST', '/attempts/' + source['attempts'][0]['id'] + '/replay', key='ci-replay')
            else:
                campaign = api_json('POST', '/campaigns', plans[label], 'ci-create-' + label)
                active = campaign['id']
                campaign = api_json('POST', '/campaigns/' + active + '/start', key='ci-start-' + label)
            active = campaign['id']
            deadline = time.monotonic() + 180
            while campaign['execution_status'] in ('queued', 'running'):
                require(not STOP and time.monotonic() < deadline, 'case cancellation/180s deadline')
                time.sleep(1)
                campaign = api_json('GET', '/campaigns/' + active)
            write(destination / (label + '-campaign.json'), campaign)
            validation = {'package_validation': 'pending', 'semantic_expectation': 'not_checked',
                          'preservation': 'not_checked', 'acceptance': 'unaccepted'}
            write(destination / (label + '-validation-status.json'), validation)
            # Retain an intact typed/hash-checked package before the gate's
            # expected outcome is tested. A copied package is not acceptance.
            validate_package(campaign, destination / (label + '-attempt'))
            validation.update(package_validation='passed', semantic_expectation='pending')
            write(destination / (label + '-validation-status.json'), validation)
            validate_case(label, campaign, results.get('strobe') if label == 'replay' else None)
            current = idle_snapshot()
            unchanged(baseline, current)
            validation.update(semantic_expectation='passed', preservation='passed', acceptance='accepted')
            write(destination / (label + '-validation-status.json'), validation)
            baseline = current
            results[label] = campaign
            active = None
        require(len(results) == MAX_ATTEMPTS and len(baseline['tables']['attempts']) == MAX_ATTEMPTS, 'unexpected attempt count')
        require(api_json('GET', '/campaigns/' + results['strobe']['id']) == results['strobe'], 'replay source changed')
        export = export_readback(results['strobe'], destination)
        final = idle_snapshot()
        unchanged(baseline, final)
        write(destination / 'final-state.json', final)
        summary = {'status': 'actual_seven_attempts_and_export_passed', 'revision': final['revision'],
                   'cases': {k: {'campaign_id': v['id'], 'attempt_id': v['attempts'][0]['id'], 'verdict': v['verdict'],
                                 'identity': v['attempts'][0]['result']['identity'], 'usage': v['attempts'][0]['result']['usage'],
                                 'tools': v['attempts'][0]['result']['tool_versions']} for k, v in results.items()},
                   'export': export, 'queues': final['queues'], 'claims': 'Ephemeral committed-source API gate only; no local adoption/browser/model claim'}
        write(destination / 'application-summary.json', summary)
    except BaseException as error:
        first_failure = error
        raise
    finally:
        if active:
            try:
                api_json('POST', '/campaigns/' + active + '/cancel', key='ci-owned-cancel')
            except Exception as cancel_error:
                # Cleanup failure is secondary to the checker/package failure.
                # Outer owned-project cleanup remains mandatory regardless.
                if first_failure is None:
                    raise
                first_failure.add_note('Owned campaign cancellation failed: ' + type(cancel_error).__name__)
                try:
                    write(destination / 'cancel-failure.json', {'campaign_id': active,
                          'first_failure': type(first_failure).__name__, 'cancel_error': type(cancel_error).__name__})
                except OSError:
                    first_failure.add_note('Could not persist cancellation failure receipt')


class Host:
    def __init__(self, project, work):
        self.project, self.work = project, work
        self.builder = project + '-builder'
        self.prefix = ['docker', 'compose', '-f', str(work / 'compose.json'), '-p', project]
        self.deadline = time.monotonic() + 2400
        self.commands = []

    def run(self, args, name, timeout=90):
        require(not STOP and time.monotonic() < self.deadline, 'hosted controller deadline/cancel')
        OUT.mkdir(parents=True, exist_ok=True)
        path = OUT / (name + '.log')
        started = time.monotonic()
        process, first_failure = None, None
        try:
            with path.open('wb') as output:
                process = subprocess.Popen(args, stdout=output, stderr=subprocess.STDOUT)
                while process.poll() is None:
                    require(not STOP and time.monotonic() < min(self.deadline, started + timeout), 'owned command deadline/cancel')
                    require(path.stat().st_size <= 2 * 1024 * 1024 and evidence_size(OUT) <= CAP, 'evidence/log cap')
                    time.sleep(1)
        except BaseException as error:
            first_failure = error
            if process is not None and process.poll() is None:
                process.terminate()
                try:
                    process.wait(timeout=30)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait(timeout=5)
        observed_bytes = path.stat().st_size
        # A command may finish before the first poll. Apply both caps again
        # after exit; retain a bounded prefix and mark truncation explicitly.
        if observed_bytes > 2 * 1024 * 1024:
            with path.open('r+b') as output:
                output.truncate(2 * 1024 * 1024)
        self.commands.append({'name': name, 'exit_code': None if process is None else process.returncode,
                              'seconds': round(time.monotonic() - started, 3), 'observed_log_bytes': observed_bytes,
                              'retained_log_bytes': path.stat().st_size, 'log_truncated': observed_bytes > path.stat().st_size})
        write(OUT / 'commands.json', self.commands)
        if first_failure is not None:
            raise first_failure
        require(not STOP and time.monotonic() < min(self.deadline, started + timeout), 'terminal command deadline/cancel')
        require(observed_bytes <= 2 * 1024 * 1024 and evidence_size(OUT) <= CAP, 'terminal evidence/log cap')
        require(process.returncode == 0, 'failed stage: ' + name)
        return path.read_bytes()


def evidence_size(path):
    return sum(p.stat().st_size for p in path.rglob('*') if p.is_file())


def stage_sources(work):
    started = time.monotonic()
    commit = subprocess.check_output(['git', 'rev-parse', 'HEAD'], cwd=ROOT, text=True, timeout=15).strip()
    files, hashes = {}, {}
    names = subprocess.check_output(['git', 'ls-tree', '-r', '--name-only', commit], cwd=ROOT, text=True, timeout=15).splitlines()
    required = {'pyproject.toml', 'uv.lock', 'alembic.ini', 'infra/rtl/Dockerfile', 'infra/app/Dockerfile',
                'scripts/ci_axi_application.py'}
    provenance = {'.github/workflows/axi-application-acceptance.yml', 'tests/axi_lite/test_ci_application_plan.py'}
    for name in names:
        require(not STOP and time.monotonic() - started <= 120, 'public source staging deadline/cancel')
        if name in required | provenance or name.startswith(('src/', 'targets/', 'migrations/', 'tests/integration/')):
            target = (work / name).resolve()
            require(target.is_relative_to(work.resolve()) and '\\' not in name, 'source escaped context')
            mode = subprocess.check_output(['git', 'ls-tree', commit, '--', name], cwd=ROOT, text=True, timeout=15).split()[0]
            require(mode == '100644', 'nonregular committed source')
            data = subprocess.check_output(['git', 'show', commit + ':' + name], cwd=ROOT, timeout=15)
            files[name] = data
            require(sum(map(len, files.values())) <= 16 * 1024 * 1024, 'public source cap')
            hashes[name] = hashlib.sha256(data).hexdigest()
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(data)
    require(required <= set(files) and sum(map(len, files.values())) <= 16 * 1024 * 1024, 'public source closure missing/too large')
    head = migration_head(files)
    write(OUT / 'source-manifest.json', {'commit': commit, 'files': hashes, 'migration_target': head})
    return commit, head


def cleanup(project, work):
    """Only this run's marked Compose project and unique builder; no global prune."""
    marker = work / 'owner.json'
    if not marker.exists():
        return
    require(json.loads(marker.read_text())['owner'] == project and work.resolve() == (ROOT / '.axi-ci-ephemeral' / project).resolve(), 'cleanup ownership mismatch')
    errors = []
    # All project services, named volumes and network are created with this exact owner label.
    project_containers = subprocess.run(['docker', 'ps', '-aq', '--filter', 'label=com.docker.compose.project=' + project], capture_output=True, text=True, timeout=15)
    require(project_containers.returncode == 0, 'cannot inventory owned cleanup resources')
    for kind, names in [('container', project_containers.stdout.splitlines()),
                        ('volume', [project + '_' + s for s in ('database', 'broker', 'artifacts')]),
                        ('network', [project + '_default'])]:
        for name in names:
            probe = subprocess.run(['docker', kind, 'inspect', name, '--format', '{{json .}}'], capture_output=True, timeout=10)
            if probe.returncode == 0:
                info = json.loads(probe.stdout)
                labels = info.get('Config', {}).get('Labels', {}) if kind == 'container' else info.get('Labels', {})
                require(labels.get(OWNER) == project, 'refuse cleanup foreign resource')
    if (work / 'compose.json').exists():
        result = subprocess.run(['docker', 'compose', '-f', str(work / 'compose.json'), '-p', project, 'down', '--volumes', '--remove-orphans', '--timeout', '20'], capture_output=True, timeout=90)
        if result.returncode:
            errors.append('owned Compose cleanup failed')
    builder = project + '-builder'
    builders = subprocess.run(['docker', 'buildx', 'ls', '--format', '{{.Name}}'], capture_output=True, text=True, timeout=15)
    if (work / 'builder-owned.json').exists() and builder in builders.stdout.splitlines():
        result = subprocess.run(['docker', 'buildx', 'rm', '--force', builder], capture_output=True, timeout=30)
        if result.returncode:
            errors.append('owned builder cleanup failed')
        registered = subprocess.run(['docker', 'buildx', 'ls', '--format', '{{.Name}}'], capture_output=True, text=True, timeout=15)
        if registered.returncode or builder in registered.stdout.splitlines():
            errors.append('owned builder still registered')
    remaining = subprocess.run(['docker', 'ps', '-aq', '--filter', 'label=com.docker.compose.project=' + project], capture_output=True, text=True, timeout=15)
    if remaining.returncode or remaining.stdout.strip():
        errors.append('owned project containers still present')
    for kind, names in [('volume', [project + '_' + v for v in ('database', 'broker', 'artifacts')]), ('network', [project + '_default'])]:
        for name in names:
            remaining = subprocess.run(['docker', kind, 'inspect', name], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=10)
            if remaining.returncode == 0:
                errors.append('owned ' + kind + ' still present')
    write(OUT / 'cleanup.json', {'project': project, 'status': 'failed' if errors else 'owned_ephemeral_resources_removed', 'errors': errors})
    require(not errors, '; '.join(errors))


def hosted_main():
    require(os.environ.get('GITHUB_ACTIONS') == 'true' and sys.platform == 'linux', 'hosted Linux only; no local build/server')
    project = project_name(os.environ)
    work = ROOT / '.axi-ci-ephemeral' / project
    require(not work.exists(), 'ephemeral workspace already exists')
    work.mkdir(parents=True)
    write(work / 'owner.json', {'owner': project})
    host = Host(project, work)
    summary = {'status': 'started', 'project': project, 'maximum_attempts': MAX_ATTEMPTS, 'local_adoption': False}
    first_failure = None
    try:
        commit, head = stage_sources(work)
        summary.update(commit=commit, migration_target=head)
        (work / 'ci-source-manifest.json').write_bytes((OUT / 'source-manifest.json').read_bytes())
        existing = host.run(['docker', 'buildx', 'ls', '--format', '{{.Name}}'], 'builders-before').decode().splitlines()
        require(host.builder not in existing, 'builder unexpectedly exists')
        write(work / 'builder-owned.json', {'owner': project, 'builder': host.builder})
        host.run(['docker', 'buildx', 'create', '--name', host.builder, '--driver', 'docker-container', '--platform', 'linux/amd64', '--driver-opt',
                  f'image={BUILDKIT},memory=4g,memory-swap=4g,cpu-period=100000,cpu-quota=200000,restart-policy=no'], 'create-builder')
        app = project + '-app'
        # One multistage build: an isolated BuildKit daemon cannot resolve a
        # host-only --load tag as its next build's FROM image.
        rtl_lines = (work / 'infra/rtl/Dockerfile').read_bytes().splitlines(keepends=True)
        require(rtl_lines[0].startswith(b'FROM ') and b' AS ' not in rtl_lines[0], 'unexpected RTL recipe')
        rtl_lines[0] = rtl_lines[0].rstrip(b'\r\n') + b' AS ci_rtl\n'
        app_recipe = (work / 'infra/app/Dockerfile').read_bytes()
        require(app_recipe.count(b'FROM ${RTL_IMAGE}') == 1, 'unexpected application recipe')
        recipe = b''.join(rtl_lines) + b'\n' + app_recipe.replace(b'FROM ${RTL_IMAGE}', b'FROM ci_rtl') + b'\nCOPY scripts/ci_axi_application.py /workspace/scripts/ci_axi_application.py\nCOPY ci-source-manifest.json /workspace/ci-source-manifest.json\n'
        (work / 'Dockerfile.application').write_bytes(recipe)
        summary['application_recipe_sha256'] = hashlib.sha256(recipe).hexdigest()
        host.run(['docker', 'buildx', 'build', '--builder', host.builder, '--load', '--platform', 'linux/amd64', '-t', app, '-f', str(work / 'Dockerfile.application'), str(work)], 'build-app', 900)
        caps = json.loads(host.run(['docker', 'inspect', 'buildx_buildkit_' + host.builder + '0', '--format', '{{json .HostConfig}}'], 'builder-caps'))
        require(caps['Memory'] == caps['MemorySwap'] == 4 * 1024**3 and caps['CpuQuota'] == 200000 and caps['CpuPeriod'] == 100000, 'builder caps differ')
        # Keep no environment/config inspection data in receipts.
        (OUT / 'builder-caps.log').unlink()
        summary['builder_caps'] = {'memory_bytes': caps['Memory'], 'cpu_quota': caps['CpuQuota']}
        host.run(['docker', 'buildx', 'stop', host.builder], 'stop-builder')
        require(host.run(['docker', 'inspect', 'buildx_buildkit_' + host.builder + '0', '--format', '{{.State.Running}}'], 'builder-stopped').strip() == b'false', 'builder did not settle')
        image = host.run(['docker', 'image', 'inspect', app, '--format', '{{.Id}}'], 'app-image').decode().strip()
        summary['image_id'] = image
        write(work / 'compose.json', compose_plan(project, image))
        host.run(host.prefix + ['up', '-d', '--no-build', 'postgres', 'valkey'], 'start-storage')
        summary['storage_image_ids'] = {role: host.run(['docker', 'image', 'inspect', tag, '--format', '{{.Id}}'], role + '-image').decode().strip()
                                        for role, tag in [('postgres', 'postgres:16-alpine'), ('valkey', 'valkey/valkey:8.1.4-alpine')]}
        deadline = time.monotonic() + 60
        while True:
            ready = subprocess.run(host.prefix + ['exec', '-T', 'postgres', 'pg_isready', '-U', 'veriforge'], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=5)
            if ready.returncode == 0:
                break
            require(time.monotonic() < deadline and not STOP, 'PostgreSQL readiness')
            time.sleep(1)
        empty = host.run(host.prefix + ['exec', '-T', 'postgres', 'psql', '-U', 'veriforge', '-At', '-c', "select count(*) from information_schema.tables where table_schema='public'"], 'empty-db').decode().strip()
        require(empty == '0', 'database not empty')
        host.run(host.prefix + ['run', '--rm', '--no-deps', 'api', 'python', '-m', 'alembic', 'upgrade', head], 'qualify-migrations', 90)
        host.run(host.prefix + ['run', '--rm', '--no-deps', 'api', 'python', '-m', 'alembic', 'check'], 'qualify-model-schema', 60)
        actual = host.run(host.prefix + ['exec', '-T', 'postgres', 'psql', '-U', 'veriforge', '-At', '-c', 'select version_num from alembic_version'], 'actual-revision').decode().strip()
        require(actual == head, 'actual revision differs')
        summary['qualified_empty_postgres_revision'] = actual
        host.run(host.prefix + ['up', '-d', '--no-deps', '--no-build', 'api', 'worker', 'reconciler'], 'start-application')
        address = host.run(host.prefix + ['port', 'api', '8000'], 'api-loopback').decode().strip()
        require(bool(re.fullmatch(r'127\.0\.0\.1:[0-9]+', address)), 'API not loopback-only')
        deadline = time.monotonic() + 60
        while True:
            try:
                with urlopen('http://' + address + '/api/health', timeout=3) as response:
                    require(response.status == 200, 'API health')
                    break
            except (OSError, URLError):
                require(time.monotonic() < deadline and not STOP, 'API startup deadline')
                time.sleep(1)
        evidence, user = prepare_evidence_mount()
        host.run(exercise_command(host.prefix, evidence, user), 'actual-application', 1350)
        application = json.loads((evidence / 'application-summary.json').read_text())
        require(application['status'] == 'actual_seven_attempts_and_export_passed' and len(application['cases']) == MAX_ATTEMPTS, 'incomplete application result')
        summary.update(status='application_checks_passed_pending_cleanup', application=application)
    except BaseException as error:
        first_failure = error
    finally:
        first_failure = finish_owned(project, work, summary, first_failure)
    if first_failure is not None:
        raise first_failure


def final_receipt_bytes(summary, retained):
    """Count the final UTF-8 receipt itself, including the count's digit width."""
    summary['evidence_bytes'] = retained
    for _ in range(16):
        payload = (json.dumps(summary, indent=2, sort_keys=True) + '\n').encode('utf-8')
        total = retained + len(payload)
        if summary['evidence_bytes'] == total:
            return payload
        summary['evidence_bytes'] = total
    raise RuntimeError('final receipt byte count did not converge')


def finish_owned(project, work, summary, first_failure=None):
    """No success until owned cleanup passes; retain the first failure object."""
    try:
        cleanup(project, work)
        summary['owned_cleanup'] = {'status': 'passed'}
    except BaseException as cleanup_error:
        summary['owned_cleanup'] = {'status': 'failed', 'error': type(cleanup_error).__name__ + ': ' + str(cleanup_error)[:500]}
        if first_failure is None:
            first_failure = cleanup_error
        else:
            first_failure.add_note('Owned cleanup also failed: ' + type(cleanup_error).__name__)
    if first_failure is None and STOP:
        first_failure = RuntimeError('cancelled before final acceptance')
    if first_failure is None and summary['status'] != 'application_checks_passed_pending_cleanup':
        first_failure = RuntimeError('application checks incomplete')
    summary.update(status='actual_hosted_gate_passed' if first_failure is None else 'failed_or_cancelled',
                   evidence_bytes=None, evidence_budget_bytes=CAP, evidence_budget_status='within_budget')
    if first_failure is not None:
        summary['error'] = type(first_failure).__name__ + ': ' + str(first_failure)[:500]
    retained = None
    try:
        existing = OUT / 'summary.json'
        measured = evidence_size(OUT) - (existing.stat().st_size if existing.exists() else 0)
        require(measured >= 0, 'invalid final evidence measurement')
        retained = measured
        final_receipt_bytes(summary, retained)
        require(summary['evidence_bytes'] <= CAP, 'final evidence cap')
    except Exception as budget_error:
        summary['evidence_budget_status'] = 'unavailable' if retained is None else 'exceeded'
        summary['evidence_bytes'] = None
        summary['evidence_budget_error'] = type(budget_error).__name__ + ': ' + str(budget_error)[:500]
        if first_failure is None:
            first_failure = budget_error
        else:
            first_failure.add_note('Final evidence budget also failed: ' + type(budget_error).__name__)
    if first_failure is not None:
        summary.update(status='failed_or_cancelled', error=type(first_failure).__name__ + ': ' + str(first_failure)[:500])
    try:
        # Failure, cleanup and budget metadata must all precede the final count.
        # Preserve evidence even over budget; the bounded diagnostic receipt
        # reports the actual excess rather than deleting files to claim success.
        payload = (final_receipt_bytes(summary, retained) if retained is not None else
                   (json.dumps(summary, indent=2, sort_keys=True) + '\n').encode('utf-8'))
        OUT.mkdir(parents=True, exist_ok=True)
        (OUT / 'summary.json').write_bytes(payload)
        print('AXI_APPLICATION_RECEIPT=' + json.dumps(summary, sort_keys=True), flush=True)
    except BaseException as transport_error:
        if first_failure is None:
            first_failure = transport_error
        else:
            first_failure.add_note('Summary persistence also failed: ' + type(transport_error).__name__)
    return first_failure


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument('--exercise', action='store_true')
    mode.add_argument('--cleanup', action='store_true')
    args = parser.parse_args()
    def stopped(*_):
        global STOP
        STOP = True
    signal.signal(signal.SIGTERM, stopped)
    signal.signal(signal.SIGINT, stopped)
    if args.exercise:
        exercise()
    elif args.cleanup:
        require(os.environ.get('GITHUB_ACTIONS') == 'true' and sys.platform == 'linux', 'hosted cleanup only')
        project = project_name(os.environ)
        cleanup(project, ROOT / '.axi-ci-ephemeral' / project)
    else:
        hosted_main()


if __name__ == '__main__':
    main()
