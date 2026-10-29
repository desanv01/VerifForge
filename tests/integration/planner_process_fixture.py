"""Fixed hosted fixture. ACTUAL PG/RQ/processes; MOCKED model HTTP; no RTL.

Inert on import. Synthetic feedback is never engineering execution evidence.
"""
import argparse
import asyncio
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
import hashlib
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import inspect
import json
import os
from pathlib import Path
import re
import threading
import time
from urllib.request import Request, urlopen
import httpx

MODEL = 'ci-explicit-mock-no-live-meta'
CANARY = 'SYNTHETIC_CI_ONLY_NOT_A_REAL_KEY'
DB = 'postgresql+psycopg://veriforge@postgres:5432/veriforge'
REDIS = 'redis://valkey:6379/0'
OUT = Path('/evidence')
CONTROL = Path('/controls/state.json')
CAPS = {'rounds': 8, 'deliveries': 12, 'mock_requests': 7}
BEHAVIORS = {'stop', 'held', '429', 'disconnect', 'late'}
CASE_DEADLINE = None


# Safe evidence schema: capability fields and future database columns stay private.
SAFE_FIELDS = {
    'campaigns': ('id', 'project_id', 'adapter_id', 'execution_status', 'verdict', 'version', 'event_sequence'),
    'attempts': ('id', 'campaign_id', 'execution_status', 'verdict', 'created_at', 'finished_at'),
    'planner_rounds': ('id', 'campaign_id', 'ordinal', 'generation', 'status', 'heartbeat_at', 'finished_at', 'fingerprint', 'experiment_id'),
    'planner_model_calls': ('round_id', 'requested_model', 'request_hash', 'schema_hash', 'input_allowance', 'started_at', 'deadline_at', 'outcome', 'usage', 'returned_model', 'actual_cost_usd'),
    'planner_cleanup': ('round_id', 'campaign_id', 'generation', 'request_hash', 'registered_at', 'local_state', 'owner_lost_at', 'settled_at', 'settlement_proof'),
    'planner_reservations': ('id', 'campaign_id', 'round_id', 'attempt_id', 'reserved_tokens', 'reserved_cycles', 'reserved_cost_usd', 'reserved_seconds'),
    'planner_intents': ('id', 'campaign_id', 'generation', 'kind', 'round_id', 'sent_at'),
}


def safe_row(table, row):
    return {key: row[key] for key in SAFE_FIELDS[table] if key in row}


def require(value, reason):
    if not value:
        raise RuntimeError(reason)


def sha(data):
    return hashlib.sha256(data).hexdigest()


def write(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix('.writing')
    temporary.write_text(json.dumps(value, indent=2, sort_keys=True, default=str) + '\n')
    temporary.replace(path)


def read(path):
    require(path.stat().st_size <= 1024 * 1024, 'fixture JSON cap')
    return json.loads(path.read_text())


def ephemeral():
    project = os.environ.get('VF_PG_CI_PROJECT', '')
    require(os.name == 'posix' and re.fullmatch(r'vf-planner-pg-ci-[0-9]+-[0-9]+', project), 'hosted ephemeral fixture only')
    require(os.environ.get('VERIFORGE_DATABASE_URL') == DB and os.environ.get('VERIFORGE_REDIS_URL') == REDIS, 'non-fixture database/broker refused')
    return project


def now():
    return datetime.now(timezone.utc)


def verify_installed():
    source = read(Path('/workspace/ci-source-manifest.json'))
    observed = {}
    for name, expected in source['files'].items():
        if name.startswith(('src/', 'targets/', 'migrations/')) or name in (
                'pyproject.toml', 'uv.lock', 'alembic.ini', 'tests/integration/planner_process_fixture.py'):
            actual = sha((Path('/workspace') / name).read_bytes())
            require(actual == expected, 'installed committed source mismatch')
            observed[name] = actual
    write(OUT / 'installed-source-vector.json', {'commit': source['commit'], 'files': observed})


def engine(name):
    ephemeral()
    from sqlalchemy import create_engine
    require(re.fullmatch(r'[a-z-]+', name), 'fixed SQL application name')
    return create_engine(DB, pool_pre_ping=True, connect_args={'options': '-c application_name=pg-ci-' + name +
        ' -c lock_timeout=4000 -c statement_timeout=10000 -c idle_in_transaction_session_timeout=5000'})


def limits():
    return {'max_rounds': 2, 'max_total_cycles': 80, 'max_cycles_per_experiment': 8, 'max_wall_seconds': 120,
            'max_execution_seconds': 2, 'max_model_seconds': 5, 'max_output_tokens': 64, 'max_model_tokens': 20000}


def completion():
    return {'model': MODEL, 'usage': {'prompt_tokens': 1, 'completion_tokens': 1}, 'choices': [{
        'finish_reason': 'stop', 'message': {'content': json.dumps({'proposal': {'kind': 'stop', 'reason': 'explicit mock; no engineering execution'}})}}]}


def http(method, path, body=None, api=False):
    data = None if body is None else json.dumps(body).encode()
    request = Request(('http://api:8000/api' if api else 'http://mock:8080') + path, data=data,
                      method=method, headers={'Content-Type': 'application/json', 'Idempotency-Key': 'fixed-ci-' + sha(path.encode())[:16]})
    with urlopen(request, timeout=10) as response:
        raw = response.read(512001)
        require(len(raw) <= 512000, 'fixture response cap')
        return json.loads(raw)


def wait_for(function, seconds=15):
    end = time.monotonic() + seconds
    if CASE_DEADLINE is not None:
        end = min(end, CASE_DEADLINE)
    while time.monotonic() < end:
        value = function()
        if value:
            return value
        time.sleep(0.05)
    raise RuntimeError('bounded fixture observation expired')


def healthy(api=False):
    try:
        return http('GET', '/health', api=api)
    except OSError:
        return False


class MockState:
    def __init__(self):
        self.scripts, self.requests, self.released = {}, [], set()
        self.lock = threading.Lock()

    def register(self, payload):
        require(set(payload) == {'hash', 'behavior'} and re.fullmatch('[0-9a-f]{64}', payload['hash']) and payload['behavior'] in BEHAVIORS, 'fixed mock script only')
        with self.lock:
            require(payload['hash'] not in self.scripts and len(self.scripts) < CAPS['rounds'], 'script duplicate/cap')
            self.scripts[payload['hash']] = payload['behavior']
            write(OUT / 'mock-scripts.json', self.scripts)

    def observe(self, raw, role):
        digest = sha(raw)
        with self.lock:
            valid = digest in self.scripts and len(self.requests) < CAPS['mock_requests'] and role in ('runtime-a', 'runtime-b')
            if not valid:
                write(OUT / 'mock-protocol-violation.json', {'hash': digest, 'role': role, 'reason': 'association_or_request_cap'})
            require(valid, 'mock request association/cap')
            self.requests.append({'hash': digest, 'role': role, 'received_at': now().isoformat(), 'mocked': True})
            write(OUT / 'mock-requests.json', self.requests)
            return digest, self.scripts[digest]

    def release(self, digest):
        with self.lock:
            require(digest in self.scripts, 'unknown mock release')
            self.released.add(digest)
            write(OUT / 'mock-released.json', sorted(self.released))


def mock_server():
    ephemeral()
    state = MockState()
    write(OUT / 'mock-requests.json', [])
    write(OUT / 'mock-released.json', [])
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *_):
            pass
        def send(self, value, status=200):
            raw = json.dumps(value).encode()
            self.send_response(status)
            self.send_header('Content-Type', 'application/json')
            self.send_header('Content-Length', str(len(raw)))
            self.end_headers()
            self.wfile.write(raw)
        def do_GET(self):
            if self.path == '/health':
                self.send({'mocked_model': True})
            else:
                self.send({'error': 'fixed endpoint only'}, 404)
        def do_POST(self):
            try:
                length = int(self.headers.get('Content-Length', '0'))
                require(0 < length <= 512000, 'mock request byte cap')
                self.connection.settimeout(5)
                raw = self.rfile.read(length)
                require(len(raw) == length, 'short mock request')
                if self.path == '/script':
                    state.register(json.loads(raw)); self.send({'scripted': True}); return
                if self.path == '/release':
                    state.release(json.loads(raw)['hash']); self.send({'released': True}); return
                require(self.path == '/v1/chat/completions', 'fixed endpoint only')
                digest, behavior = state.observe(raw, self.headers.get('X-CI-Role'))
                if behavior in ('held', 'late'):
                    wait_for(lambda: digest in state.released, 10)
                if behavior == 'disconnect':
                    self.close_connection = True
                    return
                self.send({'error': 'synthetic rate rejection'} if behavior == '429' else completion(), 429 if behavior == '429' else 200)
            except (OSError, RuntimeError, ValueError, KeyError, TypeError):
                try:
                    self.send({'error': 'bounded mock fixture failure'}, 400)
                except OSError:
                    pass
    server = ThreadingHTTPServer(('0.0.0.0', 8080), Handler)
    server.daemon_threads = True
    server.serve_forever(poll_interval=0.1)


class MockHTTPTransport(httpx.AsyncBaseTransport):
    def __init__(self, role_name, backend=None):
        require(role_name in ('runtime-a', 'runtime-b', 'runtime-c'), 'fixed runtime role')
        self.role_name = role_name
        self.inner = httpx.AsyncHTTPTransport(retries=0, trust_env=False) if backend is None else backend
    async def handle_async_request(self, request):
        from veriforge.planner.provider import ENDPOINT
        require(str(request.url) == ENDPOINT and request.method == 'POST', 'logical provider endpoint changed')
        raw = await request.aread()
        require(len(raw) <= 512000, 'admitted request cap')
        digest = sha(raw)
        behavior = read(OUT / 'mock-scripts.json').get(digest)
        require(behavior in BEHAVIORS, 'unscripted mock HTTP')
        forwarded = httpx.Request('POST', 'http://mock:8080/v1/chat/completions', content=raw,
                                 headers={'Content-Type': 'application/json', 'X-CI-Role': self.role_name})
        try:
            return await self.inner.handle_async_request(forwarded)
        except asyncio.CancelledError:
            if behavior != 'late':
                raise
            # Explicit noncooperative fixture injection after actual HTTP
            # request observation. Its late synthetic completion is discarded.
            deadline = time.monotonic() + 10
            while digest not in read(OUT / 'mock-released.json'):
                require(time.monotonic() < deadline, 'late fixture deadline')
                try:
                    await asyncio.sleep(0.01)
                except asyncio.CancelledError:
                    continue
            return httpx.Response(200, json=completion(), request=forwarded)
    async def aclose(self):
        await self.inner.aclose()


def runtime_process(role_name):
    project = ephemeral()
    from pydantic import SecretStr
    from veriforge.core import planner_runtime as runtime, planner_resources as resources
    from veriforge.core.database import session_factory
    from veriforge.core.runtime_control import ControlReader
    from veriforge.planner.provider import MetaConfig, MetaProvider
    database = engine(role_name)
    sessions = session_factory(database)
    def no_environment(*a, **k):
        raise RuntimeError('fixture forbids provider environment construction')
    MetaConfig.from_env = no_environment
    stop = threading.Event()
    def worker_factory(owner):
        worker = runtime._rq_worker(owner)  # Actual dedicated RQ worker and allowlist.
        execute_job = worker.execute_job
        def observed_job(job, queue):
            write(OUT / (role_name + '-active-job.json'), {'job_id': job.id, 'intent': job.args[0], 'queue': queue.name})
            return execute_job(job, queue)
        worker.execute_job = observed_job
        original = owner.execute
        events = []
        def observed(intent):
            result = original(intent)
            events.append({'intent': intent, 'delivery_result': result})
            write(OUT / (role_name + '-jobs.json'), events)
            return result
        owner.execute = observed  # Observer forwards unchanged production result.
        write(OUT / (role_name + '-ready.json'), {'worker': worker.name, 'pid': os.getpid(),
              'incarnation_hash': sha(owner.incarnation.encode()), 'role': role_name, 'mocked_provider': True})
        def probe_requests():
            seen = set()
            while not stop.wait(0.05):
                path = OUT / 'stale-grant-probe.json'
                if not path.exists():
                    continue
                request = read(path)
                if request['role'] != role_name or request['round'] in seen:
                    continue
                seen.add(request['round'])
                def observe_owned():
                    grant = owner._authority.grants.get(request['round'])
                    require(grant is not None, 'original private grant missing')
                    with sessions.begin() as session:
                        valid = resources.validate_provider_slot(session, grant, role=resources.ROLE, at=now(), control='RUNNING')
                    write(OUT / 'stale-grant-result.json', {'round': request['round'], 'valid': valid, 'original_owner_only': True})
                owner._loop.call_soon_threadsafe(observe_owned)
        thread = threading.Thread(target=probe_requests, daemon=True)
        thread.start()
        return worker
    try:
        status = runtime.serve(enabled=True, sessions=sessions, control=ControlReader(CONTROL),
            provider_factory=lambda: MetaProvider(MetaConfig(model=MODEL, api_key=SecretStr(CANARY)), transport=MockHTTPTransport(role_name)),
            worker_factory=worker_factory)
        write(OUT / (role_name + '-exit.json'), status.__dict__)
    finally:
        stop.set()
        database.dispose()


def api_process():
    ephemeral()
    import uvicorn
    from veriforge.api.app import create_app
    from veriforge.api.planner import PlannerPolicy
    from veriforge.core.runtime_control import ControlReader
    app = create_app(engine=engine('api'), planner_policy=PlannerPolicy(enabled=True, requested_model=MODEL, limits=limits()),
                     planner_control=ControlReader(CONTROL))
    uvicorn.run(app, host='0.0.0.0', port=8000, access_log=False, log_level='warning')


class Qualification:
    def __init__(self):
        self.project = ephemeral()
        verify_installed()
        from veriforge.core.database import session_factory
        self.engine = engine('controller')
        self.sessions = session_factory(self.engine)
        self.prepared, self.deliveries, self.completed_cases = [], [], []
        self.baseline_calls = {}
        self.baseline_reservations = {}

    def start_case(self, label):
        global CASE_DEADLINE
        CASE_DEADLINE = time.monotonic() + 45
        write(OUT / 'current-case.json', {'case': label, 'started_at': now().isoformat(), 'outer_seconds': 45})

    def snapshot(self, label):
        from sqlalchemy import text
        from veriforge.core.database import PlannerRuntimeSlot
        with self.sessions() as session:
            require(session.scalar(text('select version_num from alembic_version')) == '13engineeringprojects', 'wrong PG revision')
            names = ('campaigns', 'attempts', 'planner_rounds', 'planner_model_calls', 'planner_cleanup', 'planner_reservations', 'planner_intents')
            result = {}
            for name in names:
                rows = [dict(row[0]) for row in session.execute(text('select to_jsonb(t) from ' + name + ' t'))]
                result[name] = [safe_row(name, row) for row in rows]
            slot = session.get(PlannerRuntimeSlot, 'local_planner_provider')
            result['resource'] = {'state': slot.state, 'fence': slot.fence, 'round': slot.active_round_id,
                                  'incarnation_hash': sha(slot.incarnation.encode()) if slot.incarnation else None}
        self.validate_history(result)
        write(OUT / ('snapshot-' + label + '.json'), result)
        return result

    def validate_history(self, result):
        for round_id, row in self.baseline_calls.items():
            require(next(v for v in result['planner_model_calls'] if v['round_id'] == round_id) == row, 'earlier call outcome/reservation identity changed')
        for identity, row in self.baseline_reservations.items():
            require(next((v for v in result['planner_reservations'] if v['id'] == identity), None) == row, 'earlier reservation changed/refunded')

    def prepare(self, label, behavior, project_probe=False):
        require(len(self.prepared) < CAPS['rounds'], 'seed/round cap')
        from sqlalchemy import select
        from veriforge.contracts import Experiment
        from veriforge.core import service, planner_rounds as rounds
        from veriforge.core.database import Attempt, PlannerIntent, PlannerModelCall
        from veriforge.core.planner_experiments import resolve_attempt_experiment
        with self.sessions.begin() as session:
            plan = Experiment(experiment_id='ci-synthetic-' + label, fixture_id='fifo_correct', objective_ids=('FIFO_BEHAVIOR',),
                              operations=({'op': 'reset'},), budget={'max_cycles': 8, 'wall_time_seconds': 2})
            campaign = service.create_campaign(session, 'fifo_correct', plan.model_dump(mode='json'), 'ci-' + label, completion_policy='sequential')
            service.start(session, campaign.id)
            attempt = session.scalar(select(Attempt).where(Attempt.campaign_id == campaign.id))
            lease, _, _ = service.claim(session, attempt.id)
            record = resolve_attempt_experiment(session, attempt)
            service.finalize(session, attempt.id, lease, dict(attempt_id='SYNTHETIC-NOT-RTL', experiment_id=record.wire_id,
                execution_status='completed', verdict='pass_within_scope', identity={**record.identity, 'environment_hash': 'b' * 64},
                usage={'cycles': 1}, uncertainties=['Synthetic CI feedback; no engineering tool executed']))
            identity = campaign.id
        if project_probe:
            self.project_before_campaign(identity)
        else:
            http('POST', '/campaigns/' + identity + '/planner', {'schema_version': 'planner.attach.v1'}, api=True)
        with self.sessions.begin() as session:
            model_round = rounds.advance(session, identity, at=now(), control='RUNNING')
            session.flush()
            intent = session.scalar(select(PlannerIntent.id).where(PlannerIntent.round_id == model_round.id))
            call = session.get(PlannerModelCall, model_round.id)
            require(sha(call.request_body) == call.request_hash, 'admitted bytes/hash differ')
            value = {'label': label, 'campaign': identity, 'round': model_round.id, 'intent': intent, 'hash': call.request_hash, 'synthetic_seed': True}
        admitted = self.snapshot('admitted-' + label)
        reservations = [row for row in admitted['planner_reservations'] if row['round_id'] == value['round']]
        require(reservations and sum(row['reserved_tokens'] for row in reservations) > 0, 'missing conservative model reservation')
        for row in admitted['planner_reservations']:
            self.baseline_reservations[row['id']] = row
        self.prepared.append(value)
        http('POST', '/script', {'hash': value['hash'], 'behavior': behavior})
        write(OUT / 'prepared-rounds.json', self.prepared)
        return value

    def blockers(self, application, expected):
        from sqlalchemy import text
        with self.engine.connect() as connection:
            rows = [dict(r._mapping) for r in connection.execute(text("select pid,application_name,wait_event_type,pg_blocking_pids(pid) blockers from pg_stat_activity where application_name=:app"), {'app': application})]
        matching = [r for r in rows if expected in r['blockers']]
        return matching

    def project_before_campaign(self, campaign):
        from sqlalchemy import text
        project = self.engine.connect(); parent = self.engine.connect()
        pt, ct = project.begin(), parent.begin()
        proof = {}
        try:
            project.execute(text("update projects set id=id where id='fifo_correct'"))
            parent.execute(text('update campaigns set id=id where id=:id'), {'id': campaign})
            pp = project.scalar(text('select pg_backend_pid()')); cp = parent.scalar(text('select pg_backend_pid()'))
            with ThreadPoolExecutor(max_workers=1) as pool:
                future = pool.submit(http, 'POST', '/campaigns/' + campaign + '/planner', {'schema_version': 'planner.attach.v1'}, True)
                try:
                    proof['waiting_project'] = wait_for(lambda: self.blockers('pg-ci-api', pp), 2)
                    pt.commit()
                    proof['waiting_campaign_after_project'] = wait_for(lambda: self.blockers('pg-ci-api', cp), 2)
                finally:
                    if pt.is_active: pt.rollback()
                    if ct.is_active: ct.commit()
                future.result(10)
            write(OUT / 'project-before-campaign-lock-proof.json', proof)
        finally:
            if pt.is_active: pt.rollback()
            if ct.is_active: ct.rollback()
            project.close(); parent.close()

    def enqueue(self, value, copies=1):
        from veriforge.core.dispatch import RQDispatcher
        from veriforge.core.planner_runtime import allowed_job, TARGET
        dispatcher = RQDispatcher(REDIS)
        require(TARGET in inspect.getsource(RQDispatcher.enqueue_planner), 'unaccepted producer/consumer target wiring')
        actual = dispatcher.planner_queue.enqueue
        def observed(*args, **kwargs):
            require(len(self.deliveries) < CAPS['deliveries'], 'RQ delivery cap')
            job = actual(*args, **kwargs)
            require(allowed_job(job, dispatcher.planner_queue), 'actual RQ job violates fixed allowlist')
            self.deliveries.append({'job_id': job.id, 'function': job.func_name, 'args': job.args, 'kwargs': job.kwargs, 'origin': job.origin})
            write(OUT / 'actual-rq-deliveries.json', self.deliveries)
            return job
        dispatcher.planner_queue.enqueue = observed
        for _ in range(copies):
            dispatcher.enqueue_planner(value['intent'])

    def requests(self, value):
        return [r for r in read(OUT / 'mock-requests.json') if r['hash'] == value['hash']]

    def jobs(self, intent):
        events = []
        for role in ('runtime-a', 'runtime-b'):
            path = OUT / (role + '-jobs.json')
            if path.exists(): events += [r for r in read(path) if r['intent'] == intent]
        return events

    def settled(self, value):
        from veriforge.core import planner_cleanup as cleanup
        with self.sessions() as session:
            view = cleanup.read_cleanup(session, value['round'])
            return view is not None and view.local_state == 'settled'

    def release(self, value):
        http('POST', '/release', {'hash': value['hash']})

    def finish(self, label, values):
        from redis import Redis
        from veriforge.core.planner_driver import release_settled_resource
        broker = Redis.from_url(REDIS)
        wait_for(lambda: broker.llen('rq:queue:veriforge-planner') == broker.zcard('rq:wip:veriforge-planner') == 0)
        require(broker.llen('rq:queue:veriforge') == 0, 'native execution unexpectedly queued')
        for value in values:
            wait_for(lambda value=value: self.settled(value))
        release_settled_resource(self.sessions)
        snapshot = self.snapshot(label)
        require(snapshot['resource']['state'] == 'idle', 'capacity released without exact cleanup proof or still blocked')
        for value in values:
            row = next(r for r in snapshot['planner_model_calls'] if r['round_id'] == value['round'])
            require(row['outcome'] is not None, 'unfinished model outcome')
            self.baseline_calls[value['round']] = row
        for reservation in snapshot['planner_reservations']:
            self.baseline_reservations[reservation['id']] = reservation
        self.completed_cases.append(label)

    def prove_idle_transactions(self):
        from sqlalchemy import text
        with self.engine.connect() as connection:
            rows = [dict(r._mapping) for r in connection.execute(text("select pid,application_name,state from pg_stat_activity where application_name in ('pg-ci-runtime-a','pg-ci-runtime-b') and state='idle in transaction'"))]
        require(not rows, 'runtime Session/transaction remained open during provider await')
        write(OUT / 'no-idle-transactions-during-http.json', {'observed_idle_transactions': rows})

    def run(self):
        wait_for(lambda: (OUT / 'runtime-a-ready.json').exists() and (OUT / 'runtime-b-ready.json').exists())
        wait_for(healthy)
        wait_for(lambda: healthy(api=True))
        self.start_case('duplicate')
        first = self.prepare('duplicate', 'held', project_probe=True)
        self.enqueue(first, 2)
        request = wait_for(lambda: self.requests(first))
        self.prove_idle_transactions()
        self.release(first)
        wait_for(lambda: len(self.jobs(first['intent'])) == 2)
        require(len(self.requests(first)) == 1, 'duplicate caused resend')
        self.finish('duplicate', [first])
        # Two separate runtime processes block on actual PG resource row.
        self.start_case('pg-fence')
        a = self.prepare('fence-a', 'held'); b = self.prepare('fence-b', 'held')
        from sqlalchemy import text
        with self.engine.connect() as barrier:
            transaction = barrier.begin()
            try:
                barrier.execute(text("select resource from planner_runtime_slots where resource='local_planner_provider' for update"))
                pid = barrier.scalar(text('select pg_backend_pid()'))
                self.enqueue(a); self.enqueue(b)
                waiters = wait_for(lambda: (self.blockers('pg-ci-runtime-a', pid) + self.blockers('pg-ci-runtime-b', pid))
                    if self.blockers('pg-ci-runtime-a', pid) and self.blockers('pg-ci-runtime-b', pid) else None, 2)
                write(OUT / 'actual-pg-resource-waiters.json', waiters)
            finally:
                transaction.commit()
        winner = wait_for(lambda: a if self.requests(a) else b if self.requests(b) else None)
        wait_for(lambda: len(self.jobs(a['intent'])) + len(self.jobs(b['intent'])) >= 1)
        original_role = request[0]['role']
        write(OUT / 'stale-grant-probe.json', {'role': original_role, 'round': first['round']})
        require(wait_for(lambda: read(OUT / 'stale-grant-result.json') if (OUT / 'stale-grant-result.json').exists() else None)['valid'] is False, 'stale own grant authorized new fence')
        self.release(winner)
        wait_for(lambda: len(self.jobs(a['intent'])) + len(self.jobs(b['intent'])) == 2)
        require(len(self.requests(a)) + len(self.requests(b)) == 1, 'global slot admitted two calls')
        self.finish('pg-fence', [winner])
        self.start_case('known-unknown')
        for label, behavior, expected in [('known', '429', 'model_http_429'), ('unknown', 'disconnect', 'remote_outcome_unknown')]:
            value = self.prepare(label, behavior); self.enqueue(value)
            wait_for(lambda: self.jobs(value['intent']))
            self.enqueue(value); wait_for(lambda: len(self.jobs(value['intent'])) == 2)
            require(len(self.requests(value)) == 1, 'same intent resent')
            self.finish(label, [value])
            require(self.baseline_calls[value['round']]['outcome'] == expected, 'known/unknown classification changed')
        self.completed_cases[-2:] = ['known-unknown']
        self.start_case('pause')
        value = self.prepare('pause', 'held'); self.enqueue(value); wait_for(lambda: self.requests(value))
        write(CONTROL, {'control': 'PAUSED'})
        wait_for(lambda: self.jobs(value['intent']))
        wait_for(lambda: self.settled(value))
        self.release(value)
        self.finish('pause', [value])
        require(self.baseline_calls[value['round']]['outcome'] == 'remote_outcome_unknown', 'pause invented known remote outcome')
        write(CONTROL, {'control': 'RUNNING'})
        self.start_case('late-cancel')
        value = self.prepare('late', 'late'); self.enqueue(value); wait_for(lambda: self.requests(value))
        http('POST', '/campaigns/' + value['campaign'] + '/cancel', api=True)
        wait_for(lambda: self.jobs(value['intent']), 5)
        require(not self.settled(value), 'noncooperative task falsely settled')
        self.release(value)
        self.finish('late-cancel', [value])
        require(self.baseline_calls[value['round']]['outcome'] == 'remote_outcome_unknown', 'late completion replaced uncertainty')
        self.start_case('kill-restart')
        value = self.prepare('hard-death', 'held'); self.enqueue(value)
        request = wait_for(lambda: self.requests(value))[0]
        current = self.snapshot('before-death')
        ready = read(OUT / (request['role'] + '-ready.json'))
        active_job = read(OUT / (request['role'] + '-active-job.json'))
        require(active_job['intent'] == value['intent'] and active_job['queue'] == 'veriforge-planner', 'kill owner job association changed')
        require(ready['incarnation_hash'] == current['resource']['incarnation_hash'], 'kill target not exact current owner')
        write(OUT / 'supervisor-request.json', {'project': self.project, 'sequence': 1, 'action': 'kill', 'service': request['role']})
        proof = wait_for(lambda: read(OUT / 'supervisor-ack-1.json') if (OUT / 'supervisor-ack-1.json').exists() else None, 20)
        require(proof['positive_owner_death'] and proof['exit_code'] == 137, 'positive process death missing')
        from veriforge.core import planner_resources as resources
        from veriforge.core.database import PlannerRuntimeSlot
        with self.sessions.begin() as session:
            slot = session.get(PlannerRuntimeSlot, resources.RESOURCE)
            require(resources.quarantine_lost_owner(session, expected_fence=slot.fence, expected_round_id=slot.active_round_id,
                expected_incarnation=slot.incarnation, owner_loss_observed=True, role=resources.SUPERVISOR_ROLE, at=now(), control='RUNNING'), 'explicit supervisor CAS failed')
        self.enqueue(value); wait_for(lambda: self.jobs(value['intent']))
        write(OUT / 'supervisor-request.json', {'project': self.project, 'sequence': 2, 'action': 'start-blocked', 'service': 'runtime-c'})
        wait_for(lambda: (OUT / 'supervisor-ack-2.json').exists(), 20)
        status = read(OUT / 'runtime-c-exit.json')
        require(not status['accepting'] and status['cleanup_pending'] and status['reason'] == 'resource_blocked', 'replacement took over')
        final = self.snapshot('final-unknown')
        require(final['resource']['state'] == 'unknown' and not self.settled(value), 'dead owner got false cleanup proof')
        with self.sessions.begin() as session:
            require(not resources.release_settled_slot(session, expected_fence=final['resource']['fence'], expected_round_id=value['round'], role=resources.ROLE, at=now()), 'unknown owner slot force-cleared')
        from redis import Redis
        broker = Redis.from_url(REDIS)
        wait_for(lambda: broker.llen('rq:queue:veriforge-planner') == 0)
        # A hard-killed job may remain in the actual started/failed registry.
        # Account for precisely that job; never clear broker state to fake idle.
        registries = {name: [member.decode() for member in broker.zrange('rq:' + name + ':veriforge-planner', 0, -1)]
                      for name in ('wip', 'failed', 'deferred', 'scheduled')}
        require(all(member.split(':', 1)[0] == active_job['job_id'] for members in registries.values() for member in members), 'unaccounted broker registry member')
        require(broker.llen('rq:queue:veriforge') == broker.zcard('rq:wip:veriforge') == 0, 'unexpected native job')
        write(OUT / 'final-broker-registries.json', {'registries': registries, 'allowed_dead_job_id': active_job['job_id']})
        self.completed_cases.append('kill-restart')
        require(time.monotonic() < CASE_DEADLINE, 'terminal case deadline')
        require(len(self.prepared) == 8 and len(self.deliveries) == 12 and len(read(OUT / 'mock-requests.json')) == 7, 'finite count mismatch')
        require(not (OUT / 'mock-protocol-violation.json').exists(), 'unexpected mock HTTP attempt was rejected')
        write(OUT / 'summary.json', {'status': 'actual_pg_broker_process_with_mock_http_passed', 'cases': self.completed_cases,
            'rounds': 8, 'deliveries': 12, 'mock_http_requests': 7, 'real_provider_calls': 0, 'native_executions': 0,
            'model_usage_kind': 'synthetic declared units, not actual provider tokens/billing',
            'expected_final_resource': 'unknown', 'synthetic_seed_feedback': True,
            'claims': 'Actual PostgreSQL/Valkey/RQ/process boundaries with explicit mock HTTP; not live Meta or native execution'})

    def failure(self, first):
        global CASE_DEADLINE
        CASE_DEADLINE = time.monotonic() + 20
        errors = []
        try:
            write(CONTROL, {'control': 'PAUSED'})
            state = self.snapshot('failure-before-settlement')
            active = next((p for p in self.prepared if p['round'] == state['resource']['round']), None)
            if active is not None:
                try:
                    http('POST', '/campaigns/' + active['campaign'] + '/cancel', api=True)
                except Exception as error:
                    errors.append('owned cancel: ' + type(error).__name__)
            self.snapshot('failure-retained')
        except Exception as error:
            errors.append('failure snapshot: ' + type(error).__name__)
        write(OUT / 'summary.json', {'status': 'failed', 'first_failure': type(first).__name__ + ': ' + str(first)[:300],
              'secondary_settlement': errors, 'real_provider_calls': 0, 'native_executions': 0,
              'local_cleanup_not_assumed': True, 'claims': 'Failed ephemeral PG/RQ fixture; no live Meta claim'})


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--role', required=True, choices=('mock', 'api', 'runtime-a', 'runtime-b', 'runtime-c', 'controller'))
    role = parser.parse_args().role
    ephemeral()
    if role == 'mock': mock_server()
    elif role == 'api': api_process()
    elif role.startswith('runtime-'): runtime_process(role)
    else:
        controller = Qualification()
        try:
            controller.run()
        except BaseException as error:
            try:
                controller.failure(error)
            except Exception:
                error.add_note('Could not persist secondary fixture failure receipt')
            raise
        finally:
            controller.engine.dispose()


if __name__ == '__main__':
    main()
