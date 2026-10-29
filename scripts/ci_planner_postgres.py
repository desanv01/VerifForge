"""Hosted-only PostgreSQL/RQ/process qualification with explicitly MOCKED HTTP.

No live Meta, native jobs, user data or private operator helpers. Manual dispatch
is gated on main's accepted/published role wiring and exclusive hosted grant.
"""
import argparse
import importlib.util
import hashlib
import json
import os
from pathlib import Path
import re
import signal
import subprocess
import sys
import threading
import time

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / 'ci-artifacts/planner-postgres'
OWNER = 'io.veriforge.ci.owner'
REVISION = '13engineeringprojects'
CAP = 32 * 1024 * 1024
CASES = ('duplicate', 'pg-fence', 'known-unknown', 'pause', 'late-cancel', 'kill-restart')
LIMITS = {'rounds': 8, 'deliveries': 12, 'mock_requests': 7, 'native_runs': 0, 'real_provider_calls': 0}
STOP = False
SHARED = None


def require(condition, message):
    if not condition:
        raise RuntimeError(message)


def write(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + '.writing')
    temporary.write_text(json.dumps(value, indent=2, sort_keys=True) + '\n')
    temporary.replace(path)


def project_name(environ):
    require(re.fullmatch(r'[0-9]{1,20}', environ.get('GITHUB_RUN_ID', '')) and
            re.fullmatch(r'[0-9]{1,4}', environ.get('GITHUB_RUN_ATTEMPT', '')), 'invalid run identity')
    return 'vf-planner-pg-ci-' + environ['GITHUB_RUN_ID'] + '-' + environ['GITHUB_RUN_ATTEMPT']


def host_module():
    spec = importlib.util.spec_from_file_location('vf_pg_shared_public_host', ROOT / 'scripts/ci_axi_application.py')
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def compose_plan(project, image, controls, evidence):
    require(re.fullmatch(r'vf-planner-pg-ci-[0-9]+-[0-9]+', project), 'unsafe project')
    labels = {OWNER: project}
    log = {'driver': 'local', 'options': {'max-size': '1m', 'max-file': '1', 'compress': 'false'}}
    env = {'VERIFORGE_DATABASE_URL': 'postgresql+psycopg://veriforge@postgres:5432/veriforge',
           'VERIFORGE_REDIS_URL': 'redis://valkey:6379/0', 'VF_PG_CI_PROJECT': project,
           'VERIFORGE_CONTROL_PATH': '/controls/state.json', 'VERIFORGE_PLANNER_ENABLED': '1',
           'PYTHONDONTWRITEBYTECODE': '1'}
    common = {'image': image, 'environment': env, 'labels': labels, 'restart': 'no',
              'mem_limit': '512m', 'cpus': 1, 'pids_limit': 128,
              'cap_drop': ['ALL'], 'security_opt': ['no-new-privileges:true'],
              'volumes': [str(controls) + ':/controls:ro', str(evidence) + ':/evidence'],
              'logging': log}
    def app(role):
        return {**common, 'command': ['python', 'tests/integration/planner_process_fixture.py', '--role', role]}
    services = {'postgres': {'image': 'postgres:16-alpine', 'labels': labels, 'restart': 'no',
                'environment': {'POSTGRES_USER': 'veriforge', 'POSTGRES_DB': 'veriforge', 'POSTGRES_HOST_AUTH_METHOD': 'trust'},
                'volumes': ['database:/var/lib/postgresql/data'], 'mem_limit': '768m', 'cpus': 2, 'pids_limit': 128, 'logging': log},
                'valkey': {'image': 'valkey/valkey:8.1.4-alpine', 'labels': labels, 'restart': 'no',
                           'command': ['valkey-server', '--appendonly', 'no'], 'volumes': ['broker:/data'],
                           'mem_limit': '256m', 'cpus': 0.5, 'pids_limit': 64, 'logging': log},
                'mock': {**app('mock'), 'mem_limit': '128m', 'cpus': 0.25}, 'api': app('api'),
                'runtime-a': app('runtime-a'), 'runtime-b': app('runtime-b'), 'runtime-c': app('runtime-c'),
                'controller': {**app('controller'), 'volumes': [str(controls) + ':/controls', str(evidence) + ':/evidence']}}
    return {'services': services, 'volumes': {name: {'labels': labels} for name in ('database', 'broker')},
            'networks': {'default': {'internal': True, 'labels': labels}}}


def supervisor_action(project, request, completed):
    require(type(request) is dict and set(request) == {'project', 'sequence', 'action', 'service'}, 'supervisor request shape')
    require(request['project'] == project and type(request['sequence']) is int, 'supervisor association')
    if request['sequence'] == 1:
        require(not completed and request['action'] == 'kill' and request['service'] in ('runtime-a', 'runtime-b'), 'only one owned active-runtime kill')
    else:
        require(request['sequence'] == 2 and completed == {1} and request['action'] == 'start-blocked' and request['service'] == 'runtime-c', 'only one blocked replacement start')
    return request['service']


class Supervisor:
    """Two fixed owned Docker actions, never a general command RPC."""
    def __init__(self, host, project, image):
        self.host, self.project, self.image = host, project, image
        self.completed, self.actions, self.error = set(), [], None
        self.stop = threading.Event()
        self.thread = threading.Thread(target=self.loop, daemon=True)

    def command(self, args, timeout=20):
        result = subprocess.run(args, capture_output=True, timeout=timeout)
        require(result.returncode == 0, 'owned supervisor command failed')
        return result.stdout

    def loop(self):
        try:
            while not self.stop.wait(0.1):
                path = OUT / 'data/supervisor-request.json'
                if not path.exists():
                    continue
                require(path.stat().st_size <= 4096, 'supervisor request cap')
                request = json.loads(path.read_text())
                if request.get('sequence') in self.completed:
                    continue
                require(not STOP and time.monotonic() < self.host.deadline, 'supervisor cancellation/deadline')
                service = supervisor_action(self.project, request, self.completed)
                if request['action'] == 'kill':
                    identity = self.command(self.host.prefix + ['ps', '-q', service]).decode().strip()
                    require(re.fullmatch('[0-9a-f]{64}', identity), 'missing owned runtime identity')
                    info = json.loads(self.command(['docker', 'inspect', identity, '--format', '{{json .}}']))
                    require(info['Image'] == self.image and info['Config']['Labels'].get(OWNER) == self.project and info['State']['Running'], 'foreign/not-running runtime')
                    self.command(['docker', 'kill', '--signal', 'KILL', identity])
                    code = self.command(['docker', 'wait', identity]).decode().strip()
                    require(code == '137', 'SIGKILL exit not positively observed')
                    ack = {'sequence': 1, 'service': service, 'container_id': identity, 'exit_code': 137, 'positive_owner_death': True}
                else:
                    self.command(self.host.prefix + ['up', '-d', '--no-deps', '--no-build', '--pull', 'never', service])
                    identity = self.command(self.host.prefix + ['ps', '-aq', service]).decode().strip()
                    code = self.command(['docker', 'wait', identity]).decode().strip()
                    require(code == '0', 'replacement did not fail closed')
                    ack = {'sequence': 2, 'service': service, 'container_id': identity, 'exit_code': 0}
                self.completed.add(request['sequence'])
                self.actions.append(ack)
                write(OUT / ('data/supervisor-ack-' + str(request['sequence']) + '.json'), ack)
        except BaseException as error:
            self.error = type(error).__name__ + ': ' + str(error)
            if SHARED is not None:
                SHARED.STOP = True
        finally:
            write(OUT / 'supervisor-actions.json', {'actions': self.actions, 'error': self.error})


def cleanup(project, work):
    if not (work / 'owner.json').exists():
        return
    require(json.loads((work / 'owner.json').read_text())['project'] == project and
            work.resolve() == (ROOT / '.planner-pg-ci' / project).resolve(), 'cleanup ownership mismatch')
    deadline = time.monotonic() + 120
    def call(args, timeout=20):
        remaining = deadline - time.monotonic()
        require(remaining > 0, 'cleanup deadline; absence unknown')
        result = subprocess.run(args, capture_output=True, timeout=min(timeout, remaining))
        require(result.returncode == 0, 'cleanup inventory/action unavailable; absence unknown')
        require(len(result.stdout) <= 1024 * 1024, 'cleanup inventory cap')
        return result
    def inventory(kind):
        args = (['docker', 'ps', '-aq', '--filter', 'label=com.docker.compose.project=' + project]
                if kind == 'container' else ['docker', kind, 'ls', '--format', '{{.Name}}'])
        return set(call(args).stdout.decode().splitlines())
    containers = inventory('container')
    for identity in containers:
        info = call(['docker', 'inspect', identity, '--format', '{{json .Config.Labels}}'])
        require(json.loads(info.stdout).get(OWNER) == project, 'refuse foreign container cleanup')
    resources = {'volume': {project + '_database', project + '_broker'}, 'network': {project + '_default'}}
    for kind, names in resources.items():
        present = inventory(kind)
        for name in names & present:
            info = call(['docker', kind, 'inspect', name, '--format', '{{json .Labels}}'])
            require(json.loads(info.stdout).get(OWNER) == project, 'refuse foreign data/network cleanup')
    if (work / 'compose.json').exists():
        call(['docker', 'compose', '-f', str(work / 'compose.json'), '-p', project, 'down', '--volumes', '--remove-orphans', '--timeout', '20'], 90)
    builder = project + '-builder'
    if (work / 'builder-owned.json').exists():
        require(json.loads((work / 'builder-owned.json').read_text()) == {'builder': builder}, 'builder ownership mismatch')
        listed = call(['docker', 'buildx', 'ls', '--format', '{{.Name}}'])
        if builder in listed.stdout.decode().splitlines():
            call(['docker', 'buildx', 'rm', '--force', builder], 30)
        require(builder not in call(['docker', 'buildx', 'ls', '--format', '{{.Name}}']).stdout.decode().splitlines(), 'owned builder remains')
    require(not inventory('container'), 'owned containers remain')
    for kind, names in resources.items():
        require(not names & inventory(kind), 'owned data/network remains')
    write(OUT / 'cleanup.json', {'project': project, 'status': 'owned_ephemeral_resources_removed'})


def final_receipt_bytes(summary, retained):
    """Include the receipt's own final UTF-8 bytes in the evidence count."""
    summary['evidence_bytes'] = retained
    for _ in range(16):
        payload = (json.dumps(summary, indent=2, sort_keys=True) + '\n').encode('utf-8')
        total = retained + len(payload)
        if total == summary['evidence_bytes']:
            return payload
        summary['evidence_bytes'] = total
    raise RuntimeError('final receipt size did not converge')


def finish_owned(project, work, summary, first, deadline):
    def failure(error, context):
        nonlocal first
        if first is None:
            first = error
        else:
            first.add_note(context + ': ' + type(error).__name__)
    try:
        cleanup(project, work)
        summary['owned_cleanup'] = 'passed'
    except BaseException as error:
        summary['owned_cleanup'] = 'failed: ' + type(error).__name__ + ': ' + str(error)[:300]
        failure(error, 'Owned cleanup also failed')
    if first is None and (STOP or SHARED.STOP or time.monotonic() >= deadline):
        first = RuntimeError('final cancellation/deadline')
    if first is None and summary['status'] != 'checks_passed_pending_cleanup':
        first = RuntimeError('qualification incomplete')
    summary.update(status='passed' if first is None else 'failed', evidence_bytes=None,
                   evidence_budget_bytes=CAP, evidence_budget_status='within_budget')
    if first is not None:
        summary['first_failure'] = type(first).__name__ + ': ' + str(first)[:500]
    retained = None
    try:
        existing = OUT / 'summary.json'
        measured = SHARED.evidence_size(OUT) - (existing.stat().st_size if existing.exists() else 0)
        require(measured >= 0, 'invalid final evidence measurement')
        retained = measured
        final_receipt_bytes(summary, retained)
        require(summary['evidence_bytes'] <= CAP, 'final evidence cap')
    except BaseException as error:
        summary.update(evidence_budget_status='unknown' if retained is None else 'exceeded',
                       evidence_budget_error=type(error).__name__ + ': ' + str(error)[:300])
        failure(error, 'Final evidence accounting also failed')
    if first is not None:
        summary.update(status='failed', first_failure=type(first).__name__ + ': ' + str(first)[:500],
                       secondary_failures=getattr(first, '__notes__', []))
    try:
        payload = (final_receipt_bytes(summary, retained) if retained is not None else
                   (json.dumps(summary, indent=2, sort_keys=True) + '\n').encode('utf-8'))
        OUT.mkdir(parents=True, exist_ok=True)
        (OUT / 'summary.json').write_bytes(payload)
        print('PLANNER_PG_RECEIPT=' + json.dumps(summary, sort_keys=True), flush=True)
    except BaseException as error:
        failure(error, 'Summary persistence also failed')
    return first


def main(cleanup_only=False):
    global SHARED
    require(os.environ.get('GITHUB_ACTIONS') == 'true' and os.environ.get('GITHUB_EVENT_NAME') == 'workflow_dispatch'
            and os.environ.get('GITHUB_REF') == 'refs/heads/main' and sys.platform == 'linux', 'manual published-main hosted Linux only')
    project = project_name(os.environ)
    work = ROOT / '.planner-pg-ci' / project
    if cleanup_only:
        cleanup(project, work)
        return
    require(not work.exists(), 'run already attempted; no retry')
    work.mkdir(parents=True)
    write(work / 'owner.json', {'project': project})
    SHARED = host_module()
    SHARED.OUT, SHARED.CAP = OUT, CAP
    class BoundedHost(SHARED.Host):
        def run(self, args, name, timeout=90):
            output, first = None, None
            try:
                output = super().run(args, name, timeout)
            except BaseException as error:
                first = error
            try:
                path = OUT / (name + '.log')
                if path.exists() and path.stat().st_size > 1024 * 1024:
                    with path.open('r+b') as stream:
                        stream.truncate(1024 * 1024)
                    if self.commands and self.commands[-1]['name'] == name:
                        self.commands[-1].update(retained_log_bytes=1024 * 1024, log_truncated=True)
                        write(OUT / 'commands.json', self.commands)
                    if first is None:
                        first = RuntimeError('planner fixture terminal1MiB log cap')
            except BaseException as error:
                if first is None:
                    first = error
                else:
                    first.add_note('Log retention also failed: ' + type(error).__name__)
            if first is not None:
                raise first
            return output
    host = BoundedHost(project, work)
    host.deadline = time.monotonic() + 1500
    summary = {'status': 'started', 'claim': 'PostgreSQL/RQ/process qualification with MOCKED Meta HTTP only',
               'limits': LIMITS, 'local_adoption': False, 'project': project,
               'commit': None, 'image_id': None, 'migration_revision': None}
    supervisor, first = None, None
    try:
        commit, head = SHARED.stage_sources(work)
        summary['commit'] = commit
        require(head == REVISION, 'review required: committed schema is not13')
        for name in ('scripts/ci_planner_postgres.py', 'tests/integration/planner_process_fixture.py',
                     'infra/planner/Dockerfile.ci', '.github/workflows/planner-postgres-acceptance.yml',
                     'tests/acceptance/test_planner_postgres_plan.py'):
            data = subprocess.check_output(['git', 'show', commit + ':' + name], cwd=ROOT, timeout=15)
            destination = work / name
            destination.parent.mkdir(parents=True, exist_ok=True)
            destination.write_bytes(data)
            manifest = json.loads((OUT / 'source-manifest.json').read_text())
            manifest['files'][name] = hashlib.sha256(data).hexdigest()
            write(OUT / 'source-manifest.json', manifest)
        (work / 'ci-source-manifest.json').write_bytes((OUT / 'source-manifest.json').read_bytes())
        existing = host.run(['docker', 'buildx', 'ls', '--format', '{{.Name}}'], 'builders-before').decode().splitlines()
        require(host.builder not in existing, 'builder already exists')
        write(work / 'builder-owned.json', {'builder': host.builder})
        host.run(['docker', 'buildx', 'create', '--name', host.builder, '--driver', 'docker-container', '--platform', 'linux/amd64',
                  '--driver-opt', f'image={SHARED.BUILDKIT},memory=4g,memory-swap=4g,cpu-period=100000,cpu-quota=200000,restart-policy=no'], 'create-builder')
        tag = project + '-application'
        host.run(['docker', 'buildx', 'build', '--builder', host.builder, '--load', '--platform', 'linux/amd64', '--label', OWNER + '=' + project, '-t', tag,
                  '-f', str(work / 'infra/planner/Dockerfile.ci'), str(work)], 'build', 900)
        caps = json.loads(host.run(['docker', 'inspect', 'buildx_buildkit_' + host.builder + '0', '--format', '{{json .HostConfig}}'], 'builder-caps'))
        (OUT / 'builder-caps.log').unlink()
        require(caps['Memory'] == caps['MemorySwap'] == 4 * 1024**3 and caps['CpuQuota'] == 200000 and caps['CpuPeriod'] == 100000, 'actual builder caps differ')
        summary['builder_caps'] = {k: caps[k] for k in ('Memory', 'MemorySwap', 'CpuQuota', 'CpuPeriod')}
        host.run(['docker', 'buildx', 'stop', host.builder], 'stop-builder')
        require(host.run(['docker', 'inspect', 'buildx_buildkit_' + host.builder + '0', '--format', '{{.State.Running}}'], 'builder-stopped').strip() == b'false', 'builder not settled')
        image = host.run(['docker', 'image', 'inspect', tag, '--format', '{{.Id}}'], 'image').decode().strip()
        summary['image_id'] = image
        (work / 'controls').mkdir()
        write(work / 'controls/state.json', {'control': 'RUNNING'})
        (OUT / 'data').mkdir()
        write(work / 'compose.json', compose_plan(project, image, (work / 'controls').resolve(), (OUT / 'data').resolve()))
        host.run(host.prefix + ['up', '-d', '--no-deps', '--no-build', 'postgres', 'valkey'], 'storage')
        deadline = time.monotonic() + 60
        while True:
            ready = subprocess.run(host.prefix + ['exec', '-T', 'postgres', 'pg_isready', '-U', 'veriforge'], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=5)
            if ready.returncode == 0:
                break
            require(not STOP and time.monotonic() < deadline, 'PG readiness deadline')
            time.sleep(1)
        require(host.run(host.prefix + ['exec', '-T', 'postgres', 'psql', '-U', 'veriforge', '-At', '-c', "select count(*) from information_schema.tables where table_schema='public'"], 'empty-schema').strip() == b'0', 'database not empty')
        host.run(host.prefix + ['run', '--rm', '--no-deps', 'controller', 'python', '-m', 'alembic', 'upgrade', REVISION], 'migrate13')
        actual_revision = host.run(host.prefix + ['exec', '-T', 'postgres', 'psql', '-U', 'veriforge', '-At', '-c', 'select version_num from alembic_version'], 'actual-revision').decode().strip()
        summary['migration_revision'] = actual_revision
        require(actual_revision == REVISION, 'actual PG revision differs')
        host.run(host.prefix + ['run', '--rm', '--no-deps', 'controller', 'python', '-m', 'alembic', 'check'], 'model-schema')
        host.run(host.prefix + ['up', '-d', '--no-deps', '--no-build', 'mock', 'api', 'runtime-a', 'runtime-b'], 'roles')
        supervisor = Supervisor(host, project, image)
        supervisor.thread.start()
        host.run(host.prefix + ['run', '--rm', '--no-deps', 'controller'], 'actual-process-qualification', 400)
        require(supervisor.error is None and supervisor.completed == {1, 2}, 'owned process actions incomplete')
        outcome = json.loads((OUT / 'data/summary.json').read_text())
        require(outcome['status'] == 'actual_pg_broker_process_with_mock_http_passed', 'fixture qualification did not pass')
        summary.update(commit=commit, image_id=image, migration_revision=REVISION, qualification=outcome,
                       status='checks_passed_pending_cleanup')
    except BaseException as error:
        first = error
    finally:
        if supervisor is not None:
            supervisor.stop.set()
            supervisor.thread.join(25)
            if supervisor.thread.is_alive() and first is None:
                first = RuntimeError('supervisor did not settle')
        first = finish_owned(project, work, summary, first, host.deadline)
    if first is not None:
        raise first


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--cleanup', action='store_true')
    options = parser.parse_args()
    def stop(*_):
        global STOP
        STOP = True
        if SHARED is not None:
            SHARED.STOP = True
    signal.signal(signal.SIGTERM, stop)
    signal.signal(signal.SIGINT, stop)
    main(options.cleanup)
