"""Opt-in VF024 acceptance using existing real VF021 evidence, no synthetic DUT."""
import hashlib
from io import BytesIO
import json
import os
from pathlib import Path
import time
from uuid import uuid4
from zipfile import ZipFile

import httpx
import pytest

SOURCE_ATTEMPT = os.getenv('VERIFORGE_ACCEPTANCE_ATTEMPT_ID', 'c874704d-369a-40c9-9444-ca1636383f0a')
pytestmark = pytest.mark.skipif(os.getenv('VERIFORGE_EVIDENCE_ACCEPTANCE') != '1', reason='requires explicit VF024 upgraded runtime gate')


@pytest.fixture
def client():
    with httpx.Client(base_url='http://127.0.0.1:8000/api', timeout=10) as client:
        assert client.get('/health').json().get('evidence_api') == '1'
        yield client


def poll(client, path, field, pending, timeout=180):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        response = client.get(path)
        assert response.status_code == 200, response.text
        data = response.json()
        if data[field] not in pending:
            return data
        time.sleep(1)
    pytest.fail(f'{path} did not settle')


def test_existing_real_artifact_export_and_download(client):
    response = client.get(f'/attempts/{SOURCE_ATTEMPT}/artifacts')
    assert response.status_code == 200, response.text
    items = response.json()['items']
    portable = [item for item in items if item['downloadable']]
    assert portable
    for item in portable:
        response = client.get(f"/attempts/{SOURCE_ATTEMPT}/artifacts/{item['artifact_id']}/download")
        assert response.status_code == 200, response.text
        assert hashlib.sha256(response.content).hexdigest() == item['sha256']
        assert len(response.content) == item['size_bytes']
        assert response.headers['x-content-type-options'] == 'nosniff'
    key = {'Idempotency-Key': 'vf024-export-' + str(uuid4())}
    first = client.post(f'/attempts/{SOURCE_ATTEMPT}/exports', headers=key)
    assert first.status_code == 202, first.text
    export_id = first.json()['id']
    assert client.post(f'/attempts/{SOURCE_ATTEMPT}/exports', headers=key).json()['id'] == export_id
    job = poll(client, f'/exports/{export_id}', 'status', {'queued', 'running'}, timeout=90)
    assert job['status'] == 'completed', job
    download = client.get(f'/exports/{export_id}/download')
    assert download.status_code == 200, download.text
    assert len(download.content) == job['size_bytes']
    assert hashlib.sha256(download.content).hexdigest() == job['sha256']
    assert download.headers['content-type'] == 'application/zip'
    assert download.headers['x-content-type-options'] == 'nosniff'
    with ZipFile(BytesIO(download.content)) as bundle:
        assert bundle.testzip() is None
        assert bundle.namelist()
        assert all(not name.startswith('/') and '..' not in name.split('/') for name in bundle.namelist())
    root = Path('/artifacts/vf024-acceptance')
    root.mkdir(exist_ok=True)
    (root / f'{export_id}.zip').write_bytes(download.content)
    (root / 'export-receipt.json').write_text(json.dumps(job, indent=2))
    print({'source_attempt_id': SOURCE_ATTEMPT, 'export': job})


@pytest.mark.skipif(os.getenv('VERIFORGE_REPLAY_ACCEPTANCE') != '1', reason='requires separate serialized simulator slot')
def test_exact_replay_of_accepted_mutant(client):
    campaigns = client.get('/campaigns?limit=200').json()['items']
    original = next(c for c in campaigns if any(a['id'] == SOURCE_ATTEMPT for a in c['attempts']))
    original_attempt = next(a for a in original['attempts'] if a['id'] == SOURCE_ATTEMPT)
    key = {'Idempotency-Key': 'vf024-replay-' + str(uuid4())}
    response = client.post(f'/attempts/{SOURCE_ATTEMPT}/replay', headers=key)
    assert response.status_code == 202, response.text
    replay = response.json()
    assert replay['source_attempt_id'] == SOURCE_ATTEMPT
    assert replay['plan'] == original['plan']
    assert replay['identity'] == original['identity']
    assert client.post(f'/attempts/{SOURCE_ATTEMPT}/replay', headers=key).json()['id'] == replay['id']
    settled = poll(client, f"/campaigns/{replay['id']}", 'execution_status', {'queued', 'running'})
    assert settled['execution_status'] == 'completed', settled
    assert settled['verdict'] == original['verdict'] == 'design_mismatch'
    assert len(settled['attempts']) == 1
    result = settled['attempts'][0]['result']
    assert result['failure_signature'] == original_attempt['result']['failure_signature']
    for name in ('source_hash', 'checker_hash', 'contract_hash', 'experiment_hash'):
        assert result['identity'][name] == original_attempt['result']['identity'][name]
    assert client.get(f"/campaigns/{original['id']}").json() == original
    root = Path('/artifacts/vf024-acceptance')
    root.mkdir(exist_ok=True)
    (root / 'replay-receipt.json').write_text(json.dumps(settled, indent=2))
    print({'replay_campaign_id': settled['id'], 'replay_attempt_id': settled['attempts'][0]['id']})
