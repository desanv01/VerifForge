"""Independent PG graph predicate checks; no database or service execution."""
import copy
import hashlib
import importlib.util
import json
import platform
from pathlib import Path
import subprocess

import pytest

ROOT = Path(__file__).resolve().parents[2]


@pytest.fixture
def fixture(monkeypatch, tmp_path):
    # Avoid Windows platform discovery spawning cmd during library import.
    monkeypatch.setattr(platform, 'machine', lambda: 'AMD64')
    p = ROOT / 'tests/integration/planner_process_fixture.py'
    assert hashlib.sha256(p.read_bytes().replace(b'\r\n', b'\n')).hexdigest() == '52d51d9b68a9a50f47cc8ab4a25734608caac68a07de5b97b7d40de5edd62dff'
    def forbidden(*args, **kwargs): pytest.fail('no operations in graph review')
    for name in ('run', 'Popen', 'check_output'): monkeypatch.setattr(subprocess, name, forbidden)
    spec = importlib.util.spec_from_file_location('independent_pg_graph', p)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    monkeypatch.setattr(module, 'urlopen', forbidden)
    monkeypatch.setattr(module, 'OUT', tmp_path)
    return module


def graph(chain):
    barrier = {'pid': 10, 'xid': '901', 'page': 3, 'tuple': 8}
    relation = {'locktype': 'relation', 'resource_relation': True, 'granted': True, 'mode': 'RowShareLock'}
    nodes = [{'pid': 10, 'application_name': 'pg-ci-controller', 'state': 'idle in transaction',
        'xid': '901', 'blockers': [], 'locks': [dict(relation), {'locktype': 'transactionid',
            'granted': True, 'mode': 'ExclusiveLock', 'xid': '901'}]}]
    for index, role in enumerate(('runtime-a', 'runtime-b')):
        wait = {'locktype': 'tuple', 'resource_relation': True, 'page': 3, 'tuple': 8, 'granted': False} if chain and index else {
            'locktype': 'transactionid', 'xid': '901', 'granted': False}
        nodes.append({'pid': 11 + index, 'application_name': 'pg-ci-' + role, 'state': 'active',
            'wait_event_type': 'Lock', 'blockers': [11 if chain and index else 10], 'locks': [dict(relation), wait]})
    expected = {'a': 'ra', 'b': 'rb'}
    return {'backends': nodes, 'pending': [{'intent': key, 'round': value, 'status': 'queued',
        'started_at': None, 'outcome': None} for key, value in expected.items()],
        'jobs': {role: {'job_id': key + '-job', 'intent': key, 'queue': 'veriforge-planner'}
            for role, key in (('runtime-a', 'a'), ('runtime-b', 'b'))},
        'deliveries': {'a-job': 'a', 'b-job': 'b'}, 'completed_intents': []}, expected, barrier


@pytest.mark.parametrize('chain', [False, True])
def test_direct_and_known_chained_waits_reach_held_exact_barrier(fixture, chain):
    observation, expected, barrier = graph(chain)
    before = copy.deepcopy(observation)
    assert fixture.validate_resource_graph(observation, expected, barrier) is True
    assert observation == before


@pytest.mark.parametrize('fault', ['cycle', 'foreign-edge', 'unblocked', 'self-edge', 'mixed-edge',
    'wrong-tuple', 'wrong-relation', 'wrong-xid', 'released-barrier', 'stale-barrier', 'blocked-barrier',
    'job-intent', 'job-id', 'job-queue', 'duplicate-job', 'completed', 'started', 'round', 'duplicate-pid',
    'idle-intermediate', 'locks-overcap', 'backends-overcap'])
def test_unrelated_stale_or_unowned_paths_reject(fixture, fault):
    o, expected, barrier = graph(True)
    holder, a, b = o['backends']
    if fault == 'cycle': a['blockers'] = [12]
    elif fault == 'foreign-edge': b['blockers'] = [99]
    elif fault == 'unblocked': b['blockers'] = []
    elif fault == 'self-edge': b['blockers'] = [12]
    elif fault == 'mixed-edge': b['blockers'] = [10, 99]
    elif fault == 'wrong-tuple': b['locks'][1]['tuple'] = 9
    elif fault == 'wrong-relation': b['locks'][1]['resource_relation'] = False
    elif fault == 'wrong-xid': a['locks'][1]['xid'] = '902'
    elif fault == 'released-barrier': holder['state'] = 'idle'
    elif fault == 'stale-barrier': holder['xid'] = '900'
    elif fault == 'blocked-barrier': holder['blockers'] = [12]
    elif fault == 'job-intent': o['jobs']['runtime-b']['intent'] = 'other'
    elif fault == 'job-id': o['jobs']['runtime-b']['job_id'] = 'unrecorded'
    elif fault == 'job-queue': o['jobs']['runtime-b']['queue'] = 'veriforge'
    elif fault == 'duplicate-job': o['jobs']['runtime-b'] = dict(o['jobs']['runtime-a'])
    elif fault == 'completed': o['completed_intents'] = ['a']
    elif fault == 'started': o['pending'][0]['started_at'] = 'already started'
    elif fault == 'round': o['pending'][0]['round'] = 'other'
    elif fault == 'duplicate-pid': b['pid'] = a['pid']
    elif fault == 'idle-intermediate':
        o['backends'].append({'pid': 13, 'application_name': 'pg-ci-runtime-a', 'state': 'idle',
            'wait_event_type': None, 'locks': [], 'blockers': [10]}); b['blockers'] = [13]
    elif fault == 'locks-overcap': a['locks'] += [dict(a['locks'][0]) for _ in range(63)]
    else: o['backends'] += [dict(holder, pid=20 + n) for n in range(6)]
    with pytest.raises(RuntimeError): fixture.validate_resource_graph(o, expected, barrier)


def test_parameterized_pg16_statement_binds_only_owned_ids(fixture):
    from sqlalchemy import text
    from sqlalchemy.dialects.postgresql import dialect
    compiled = text(fixture.RESOURCE_GRAPH_SQL).compile(dialect=dialect())
    assert set(compiled.params) == {'barrier', 'intent_a', 'intent_b'}
    sql = fixture.RESOURCE_GRAPH_SQL.lower()
    assert 'pg_blocking_pids(a.pid)' in sql and 'backend_xid::text' in sql
    assert 'limit 9' in sql and 'limit 65' in sql and "'planner_runtime_slots'::regclass" in sql
    assert not any(word in sql for word in ('update ', 'delete ', 'insert ', 'a.query', 'owner_token', 'execution_lease'))


def test_failed_graph_retains_last_observation_and_exact_query_associations(fixture, tmp_path):
    o, expected, barrier = graph(True)
    o['backends'][2]['blockers'] = [999]
    class Connection:
        def __enter__(self): return self
        def __exit__(self, *args): pass
        def scalar(self, statement, params):
            assert params == {'barrier': 10, 'intent_a': 'a', 'intent_b': 'b'}
            return {'backends': o['backends'], 'pending': o['pending']}
    class Engine:
        def connect(self): return Connection()
    controller = fixture.Qualification.__new__(fixture.Qualification)
    controller.engine = Engine()
    controller.deliveries = [{'job_id': key + '-job', 'args': [key]} for key in ('a', 'b')]
    for role, job in o['jobs'].items(): (tmp_path / (role + '-active-job.json')).write_text(json.dumps(job))
    assert controller.resource_waiters([{'intent': key, 'round': value} for key, value in expected.items()], barrier) is None
    saved = json.loads((tmp_path / 'actual-pg-resource-last-observation.json').read_bytes())
    assert saved['backends'] == o['backends'] and saved['barrier'] == barrier
    assert 'cyclic blocker path' in saved['validation']
