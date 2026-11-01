"""Actual in-process HTTP transport with temporary SQLite and synthetic evidence."""
from pathlib import Path
import runpy

from fastapi.testclient import TestClient
import pytest

from veriforge.api.app import create_app
from veriforge.core.repair_jobs import RepairPolicy
from veriforge.core.repair_runtime import RepairRuntime

support = runpy.run_path(str(Path(__file__).parents[1] / 'core' / 'test_repair_jobs.py'))


def test_default_http_admission_disabled(tmp_path):
    engine, sessions, root, original = support['make_state'](tmp_path)
    try:
        with TestClient(create_app(engine, initialize_catalog=False, artifact_root=root)) as client:
            health = client.get('/api/health').json()
            assert health['repair_api'] == '1' and health['repair_admission_enabled'] is False
            assert health['repair_families'] == ['arithmetic', 'axi_lite']
            assert client.post(f'/api/attempts/{original}/repairs',
                headers={'Idempotency-Key': 'disabled'}, json={'kind': 'reduction'}).status_code == 503
    finally:
        engine.dispose()


def test_http_binding_fresh_policy_idempotency_and_cancel(tmp_path, monkeypatch):
    engine, sessions, root, original = support['make_state'](tmp_path)
    from veriforge.core import worker
    monkeypatch.setattr(worker, 'execute_attempt', lambda *a, **k: pytest.fail('HTTP launched a worker'))
    current = [support['policy']()]
    runtime = RepairRuntime(lambda: current[0], lambda: 'RUNNING', root.resolve())
    try:
        with pytest.raises(ValueError, match='roots differ'):
            create_app(engine, initialize_catalog=False, artifact_root=root/'other', repair_runtime=runtime)
        with TestClient(create_app(engine, initialize_catalog=False, artifact_root=root, repair_runtime=runtime)) as client:
            request = {'kind': 'candidate', 'candidate_fixture_id': 'multiplier_correct'}
            url = f'/api/attempts/{original}/repairs'
            first = client.post(url, headers={'Idempotency-Key': 'candidate'}, json=request)
            assert first.status_code == 202
            job_id = first.json()['job_id']
            repeated = client.post(url, headers={'Idempotency-Key': 'candidate'}, json=request)
            assert repeated.status_code == 202 and repeated.json()['job_id'] == job_id
            assert client.get(f'/api/repairs/{job_id}').json()['execution_status'] == 'active'
            current[0] = RepairPolicy()
            assert client.get('/api/health').json()['repair_admission_enabled'] is False
            assert client.post(url, headers={'Idempotency-Key': 'revoked'}, json=request).status_code == 503
            assert client.post(f'/api/repairs/{job_id}/cancel').status_code == 200
            assert client.get(f'/api/repairs/{job_id}').json()['execution_status'] == 'cancelled'
    finally:
        engine.dispose()
