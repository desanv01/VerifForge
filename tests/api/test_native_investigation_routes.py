"""Synthetic recorded native reads through TestClient, never tools/services."""
from hashlib import sha256
from pathlib import Path
import runpy

from fastapi.testclient import TestClient
import pytest
import sqlalchemy as sa

from veriforge.api.app import create_app
from veriforge.core import investigations as store
from veriforge.core.database import Project, InvestigationArtifact, InvestigationVersion

HELPERS = runpy.run_path(str(Path(__file__).resolve().parents[1] / 'core/test_native_investigation_store.py'))
db, persist, native_bundle = HELPERS['db'], HELPERS['persist'], HELPERS['native_bundle']


@pytest.fixture
def client(db):
    with TestClient(create_app(db[0], initialize_catalog=False)) as client:
        yield client


def version_url(row):
    return f"/api/investigations/{row['investigation_id']}/versions/{row['id']}"


def test_health_advertises_closed_recorded_capabilities_without_revalidation(client, monkeypatch):
    monkeypatch.setattr(store, '_installed_native_validator_hash', lambda: pytest.fail('health source scan'))
    monkeypatch.setattr(store, '_load', lambda *a: pytest.fail('health evidence admission'))
    response = client.get('/api/health')
    assert response.status_code == 200
    body = response.json()
    assert body['investigation_api'] == '1'
    assert body['investigation_admission_schemas'] == list(store.ADMISSION_SCHEMAS) == [
        'engineering.admission.v1', 'engineering.admission.v2']
    assert body['investigation_report_kinds'] == list(store.NATIVE_REPORT_KINDS) == [
        'riscv.recorded-report.v1', 'firmware.recorded-report.v1']
    assert body['model'] == 'not_integrated' and body['execution'] == 'separate_worker'
    assert body['planner_admission_enabled'] is False


@pytest.mark.parametrize('kind', ['riscv', 'firmware'])
def test_exact_native_detail_download_and_conditional_validation(db, client, tmp_path, kind):
    row = persist(db, native_bundle(kind, tmp_path))
    path = version_url(row)
    response = client.get(path)
    assert response.status_code == 200
    detail = response.json()
    assert detail['report_kind'] == kind + '.recorded-report.v1'
    assert detail['native_report']['schema_version'] == detail['report_kind']
    assert detail['execution_status'] == 'not_run'
    assert 'bridge_report' not in detail and 'execution_contexts' not in detail
    assert detail['envelope']['external_context_scope'] == 'host_pinned_external_locator_not_local_execution_origin'
    assert client.get(path, headers={'If-None-Match': response.headers['etag']}).status_code == 304
    artifacts = client.get(path + '/artifacts').json()['items']
    blob = artifacts[0]
    download = client.get(path + f"/artifacts/{blob['id']}/download")
    assert download.status_code == 200
    assert sha256(download.content).hexdigest() == blob['sha256']
    assert len(download.content) == blob['size_bytes']
    assert download.headers['cache-control'] == 'no-store'
    assert download.headers['x-content-type-options'] == 'nosniff'
    assert download.headers['content-disposition'] == f'attachment; filename="{blob["sha256"]}.bin"'
    assert client.post(path, json={}).status_code == 405
    assert all(route.methods <= {'GET', 'HEAD'} for route in client.app.routes
               if getattr(route, 'path', '').startswith('/api/investigations'))


def test_default_v1_discovery_negotiation_and_pages(client, db):
    legacy = persist(db, HELPERS['LEGACY']['valid_bundle'](project='recorded-project'), key='v1')
    native_rows = [persist(db, key=f'native-{i}') for i in range(3)]
    path = '/api/projects/recorded-project/investigations'
    assert [item['id'] for item in client.get(path).json()['items']] == [legacy['investigation_id']]
    assert 'report_kind' not in client.get(version_url(legacy)).json()
    assert 'bridge_report' in client.get(version_url(legacy)).json()
    collected, cursor = [], ''
    while True:
        response = client.get(path, params={'admission_schema':'v2', 'limit':1, 'after':cursor})
        assert response.status_code == 200
        page = response.json()
        collected.extend(item['id'] for item in page['items'])
        cursor = page['next_cursor']
        if cursor is None:
            break
    assert collected == sorted(row['investigation_id'] for row in native_rows)
    assert len(client.get(path, params={'admission_schema':'all'}).json()['items']) == 4
    row = native_rows[0]
    root = f"/api/investigations/{row['investigation_id']}"
    assert client.get(root).status_code == 404
    assert client.get(root, params={'admission_schema':'v2'}).json()['latest_version']['id'] == row['id']
    assert client.get(root + '/versions').json()['items'] == []
    second = persist(db, key='appended', investigation_id=row['investigation_id'])
    first_page = client.get(root + '/versions', params={'admission_schema':'all', 'limit':1}).json()
    assert first_page['items'][0]['id'] == row['id'] and first_page['next_cursor'] == 1
    assert client.get(root + '/versions', params={'admission_schema':'v2','after':1}).json()['items'][0]['id'] == second['id']
    for route in (path, root, root + '/versions'):
        assert client.get(route, params={'admission_schema':'engineering.admission.v2'}).status_code == 422


@pytest.mark.parametrize('corruption', ['validator', 'blob', 'role', 'schema'])
def test_corruption_not_hidden_by_etag_or_summary(client, db, monkeypatch, corruption):
    row = persist(db)
    path = version_url(row)
    etag = client.get(path).headers['etag']
    if corruption == 'validator':
        monkeypatch.setattr(store, '_installed_native_validator_hash', lambda: '0' * 64)
    else:
        with db[1].begin() as session:
            if corruption == 'schema':
                session.execute(sa.update(InvestigationVersion).where(InvestigationVersion.id == row['id']).values(schema_version='unknown'))
            else:
                blob = session.scalar(sa.select(InvestigationArtifact).where(InvestigationArtifact.version_id == row['id']))
                changes = {'data':b'x', 'size_bytes':1} if corruption == 'blob' else {'role':'native_report'}
                session.execute(sa.update(InvestigationArtifact).where(InvestigationArtifact.id == blob.id).values(**changes))
    assert client.get(path, headers={'If-None-Match':etag}).status_code == 409
    assert client.get(path + '/artifacts').status_code == 409
    if corruption != 'schema':
        assert client.get('/api/projects/recorded-project/investigations', params={'admission_schema':'v2'}).status_code == 409
        assert client.get(f"/api/investigations/{row['investigation_id']}/versions", params={'admission_schema':'all'}).status_code == 409


def test_owned_version_artifact_and_project_fences(client, db):
    first, second = persist(db), persist(db, key='second')
    assert client.get(f"/api/investigations/{first['investigation_id']}/versions/{second['id']}").status_code == 404
    foreign_artifact = client.get(version_url(second) + '/artifacts').json()['items'][0]['id']
    assert client.get(version_url(first) + f'/artifacts/{foreign_artifact}/download').status_code == 404
    with db[1].begin() as session:
        session.execute(sa.update(Project).where(Project.id == 'recorded-project').values(owner='foreign'))
    assert client.get(version_url(first)).status_code == 404
    assert client.get('/api/projects/recorded-project/investigations', params={'admission_schema':'all'}).status_code == 404
