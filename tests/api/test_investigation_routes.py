"""Real temporary-SQLite HTTP reads; synthetic native inputs, no tools/jobs."""
from pathlib import Path
import runpy

from fastapi.testclient import TestClient
import pytest
import sqlalchemy as sa

from veriforge.api.app import create_app
from veriforge.core import investigations
from veriforge.core.database import InvestigationArtifact, Project, Dispatch

# Reuse this assignment's synthetic bundle builder without changing test package
# layout or importing a conflicting test module basename.
helpers = runpy.run_path(str(Path(__file__).resolve().parents[1] / 'core/test_investigations.py'))
valid_bundle, AT = helpers['valid_bundle'], helpers['AT']


def store(db, *, key='api', revision='engineering-1', investigation_id=None):
    record, blobs, authority = valid_bundle(revision=revision)
    with db[1].begin() as session:
        return investigations.persist_investigation(session, record, blobs, authority=authority,
            idempotency_key=key, at=AT, investigation_id=investigation_id)


@pytest.fixture
def client(db):
    with TestClient(create_app(db[0])) as client:
        yield client


def url(row):
    return f"/api/investigations/{row['investigation_id']}/versions/{row['id']}"


def test_import_visible_after_refresh_with_safe_downloads_and_etag(client, db):
    row = store(db)
    root = f"/api/investigations/{row['investigation_id']}"
    listed = client.get('/api/projects/fifo_correct/investigations')
    assert listed.status_code == 200
    assert listed.json()['items'][0]['id'] == row['investigation_id']
    assert client.get(root).json()['latest_version']['id'] == row['id']
    assert client.get(root + '/versions').json()['items'][0]['id'] == row['id']
    response = client.get(url(row))
    assert response.status_code == 200
    data = response.json()
    assert data['scope'] == 'recorded_import' and data['execution_status'] == 'not_run'
    assert data['envelope']['operation'] == 'admission_only'
    assert data['envelope']['claim_scope'] == 'resolved_bytes_and_typed_consistency_only'
    assert data['project_revision']['project_id'] == 'fifo_correct'
    assert 'PRIVATE_SOURCE_ONLY' not in response.text
    assert response.headers['etag'] == '"' + row['content_hash'] + '"'
    assert client.get(url(row), headers={'If-None-Match': response.headers['etag']}).status_code == 304
    artifacts = client.get(url(row) + '/artifacts').json()['items']
    source = next(item for item in artifacts if item['artifact_id'] == 'header')
    assert source['id'] != source['artifact_id'] and 'path' not in source
    downloaded = client.get(url(row) + f"/artifacts/{source['id']}/download")
    assert downloaded.status_code == 200 and b'PRIVATE_SOURCE_ONLY' in downloaded.content
    assert downloaded.headers['content-type'] == 'application/octet-stream'
    assert downloaded.headers['x-content-type-options'] == 'nosniff'
    assert downloaded.headers['cache-control'] == 'no-store'
    assert downloaded.headers['content-disposition'] == f"attachment; filename=\"{source['sha256']}.bin\""


def test_bounded_pagination_and_no_public_admission_or_execution(client, db):
    first = store(db)
    second = store(db, key='second', revision='engineering-2', investigation_id=first['investigation_id'])
    path = f"/api/investigations/{first['investigation_id']}/versions"
    page = client.get(path, params={'limit': 1}).json()
    assert page['next_cursor'] == 1
    assert client.get(path, params={'after': 1}).json()['items'][0]['id'] == second['id']
    assert client.get(path, params={'limit': 0}).status_code == 422
    assert client.get(path, params={'limit': 51}).status_code == 422
    assert client.get(path, params={'after': -1}).status_code == 422
    assert client.post('/api/investigations', json={'verified': True, 'maturity': 'demonstrated'}).status_code == 404
    assert client.post(url(first), json={'execution_status': 'completed'}).status_code == 405
    with db[1]() as session:
        assert session.scalar(sa.select(sa.func.count()).select_from(Dispatch)) == 0


def test_foreign_or_misbound_artifact_and_project_not_found(client, db):
    first = store(db)
    second = store(db, key='other')
    foreign_blob = client.get(url(second) + '/artifacts').json()['items'][0]['id']
    assert client.get(url(first) + f'/artifacts/{foreign_blob}/download').status_code == 404
    assert client.get(f"/api/investigations/{first['investigation_id']}/versions/{second['id']}").status_code == 404
    assert client.get(url(first) + '/artifacts/header/download').status_code == 404
    assert client.get('/api/projects/missing/investigations').status_code == 404
    with db[1].begin() as session: session.get(Project, 'fifo_correct').owner = 'foreign'
    assert client.get(url(first)).status_code == 404


def test_corrupt_blob_fails_closed_even_conditional_detail(client, db):
    row = store(db)
    etag = client.get(url(row)).headers['etag']
    # Simulate storage corruption in this isolated DB, never production mutation.
    with db[0].begin() as connection:
        connection.execute(sa.text('DROP TRIGGER immutable_investigation_artifacts_update'))
        connection.execute(sa.update(InvestigationArtifact).where(InvestigationArtifact.version_id == row['id'],
            InvestigationArtifact.artifact_id == 'header').values(data=b'x' * len(valid_bundle()[1][0].data)))
    response = client.get(url(row), headers={'If-None-Match': etag})
    assert response.status_code == 409
    assert response.json()['detail'] == 'Investigation evidence is unavailable or changed'
    assert client.get(url(row) + '/artifacts').status_code == 409
