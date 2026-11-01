"""Temporary SQLite + in-process HTTP; synthetic EDA bytes, never tool runs."""
from datetime import datetime, timezone
from hashlib import sha256
from pathlib import Path
import runpy

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import func, select

from veriforge.api.app import create_app
from veriforge.contracts.eda_tools import EdaInvestigation
from veriforge.core import eda_policy, investigations, service
from veriforge.core.database import Dispatch, InvestigationVersion, Project
from veriforge.core.engineering_catalog import EngineeringProjectSpec, create_engineering_project
from veriforge.system_investigation import eda_records as eda
from veriforge.system_investigation.records import ArtifactBytes


def synthetic_bundle():
    artifacts = []
    def add(name, raw):
        item = ArtifactBytes(reference=dict(artifact_id=name, revision_id='v1', sha256=sha256(raw).hexdigest()), data=raw)
        artifacts.append(item)
        return item.reference
    sources = [dict(name=name, reference=add('source-' + str(i), b'synthetic source ' + str(i).encode()))
        for i, name in enumerate(('exercise3_core.v', 'exercise3_nexys_video_top.v', 'nexys_video_exercise3.xdc'))]
    refs = tuple(s['reference'] for s in sources)
    config = add('config', eda.encode(dict(domain='vivado', profile='vivado.exercise3.v1')))
    recipe = add('recipe', b'# inert synthetic Tcl')
    requirements = add('requirements', eda.encode(dict(domain='vivado', part='xc7a200tsbg484-1',
        top='exercise3_nexys_video_top', clock_period_ps=10000, recipe=recipe.model_dump(mode='json'))))
    receipt = add('receipt', b'{"success":true}')
    report = add('report', b'Design Route Status\n   # of fully routed nets........ : 35\n   # of nets with routing errors.......... : 0\n')
    record = EdaInvestigation(investigation_id='eda-test', domain='vivado', requirements=requirements,
        candidate=dict(project=dict(project_id='eda-project', revision_id='v1', source_refs=refs, configuration_hash=config.sha256),
            sources=sources, configuration=config, receipt=receipt,
            reports=[dict(name='route_status.rpt', reference=report, source_refs=refs)]))
    return record, tuple(artifacts)


@pytest.fixture
def prepared(db, monkeypatch):
    monkeypatch.setattr(eda_policy, '_reviews', eda_policy._reviews)
    monkeypatch.setattr(eda_policy, '_implementation', eda_policy._implementation)
    record, artifacts = synthetic_bundle()
    authority = eda.authorize_eda(record, reviewed_by='synthetic-host-review')
    eda_policy.configure_eda_reviews((authority,), implementation_sha256=eda.implementation_hash())
    with db[1].begin() as session:
        create_engineering_project(session, EngineeringProjectSpec(project_id='eda-project', name='Synthetic EDA', domains=('vivado',)))
    return record, artifacts, authority


def persist(db, prepared, key='eda-key', parent=None):
    record, artifacts, authority = prepared
    with db[1].begin() as session:
        return investigations.persist_investigation(session, record, artifacts, authority=authority,
            idempotency_key=key, investigation_id=parent, at=datetime(2026, 11, 1, tzinfo=timezone.utc))


def test_persisted_eda_detail_projection_download_and_filter(db, prepared):
    row = persist(db, prepared)
    assert persist(db, prepared)['id'] == row['id']
    base = f"/api/investigations/{row['investigation_id']}/versions/{row['id']}"
    with TestClient(create_app(db[0], initialize_catalog=False)) as client:
        assert client.get('/api/projects/eda-project/investigations').json()['items'] == []
        for mode in ('v3', 'all'):
            items = client.get('/api/projects/eda-project/investigations?admission_schema=' + mode).json()['items']
            assert len(items) == 1 and items[0]['latest_version']['schema_version'] == 'engineering.admission.v3'
        detail = client.get(base)
        assert detail.status_code == 200 and detail.json()['execution_contexts'] == []
        response = client.get(base + '/projection')
        assert response.status_code == 200, response.text
        data = response.json()
        assert data['execution_status'] == 'not_run'
        projection = data['projections'][0]
        assert projection['schema_version'] == 'vivado.projection.v1'
        assert projection['data']['investigation']['execution']['status'] == 'not_run'
        assert client.get(base + '/projection', headers={'If-None-Match': response.headers['etag']}).status_code == 304
        artifact = client.get(base + '/artifacts').json()['items'][0]
        download = client.get(base + '/artifacts/' + artifact['id'] + '/download')
        assert download.status_code == 200 and sha256(download.content).hexdigest() == artifact['sha256']
        assert client.post(base, json={}).status_code == 405
    with db[1]() as session:
        assert session.scalar(select(func.count()).select_from(Dispatch)) == 0
        assert session.scalar(select(func.count()).select_from(InvestigationVersion)) == 1


def test_eda_host_review_revocation_blocks_cached_reads_without_rewriting(db, prepared):
    row = persist(db, prepared)
    base = f"/api/investigations/{row['investigation_id']}/versions/{row['id']}"
    with TestClient(create_app(db[0], initialize_catalog=False)) as client:
        prior = client.get(base + '/projection')
        eda_policy.configure_eda_reviews((), implementation_sha256=eda.implementation_hash())
        assert client.get(base + '/projection', headers={'If-None-Match': prior.headers['etag']}).status_code == 409
        assert client.get('/api/projects/eda-project/investigations?admission_schema=v3').status_code == 409
    with db[1]() as session:
        assert session.get(InvestigationVersion, row['id']).content_hash == row['content_hash']


def test_eda_wire_review_and_foreign_ownership_cannot_admit(db, prepared):
    record, artifacts, authority = prepared
    impostor = eda.authorize_eda(record, reviewed_by='wire-claim')
    with pytest.raises(ValueError, match='installed'):
        persist(db, (record, artifacts, impostor))
    persist(db, prepared)
    with db[1].begin() as session:
        session.add(Project(id='foreign-eda', owner='foreign', kind='engineering_recorded',
            contract_id=None, data=dict(id='foreign-eda', name='Foreign', description='', domains=['vivado'])))
    with db[1]() as session, pytest.raises(service.NotFound):
        investigations.list_investigations(session, 'foreign-eda', admission_schema='v3')


def test_eda_append_preserves_versions_and_default_disabled_policy(db, prepared):
    first = persist(db, prepared)
    second = persist(db, prepared, key='second', parent=first['investigation_id'])
    assert second['ordinal'] == 2 and second['id'] != first['id']
    with db[1]() as session:
        assert investigations.list_versions(session, first['investigation_id'], admission_schema='v3')['items'][0]['id'] == first['id']
    eda_policy.configure_eda_reviews((), implementation_sha256=eda.implementation_hash())
    with pytest.raises(ValueError, match='unavailable'):
        persist(db, prepared, key='third')


def test_kicad_portable_recorded_bytes_keep_missing_mapping_explicit(db, prepared):
    bundle = runpy.run_path(str(Path(__file__).parents[1] / 'system_investigation/test_eda_records.py'))['bundle']
    record, artifacts = bundle()
    authority = eda.authorize_eda(record, reviewed_by='synthetic-host-review')
    eda_policy.configure_eda_reviews((authority,), implementation_sha256=eda.implementation_hash())
    with db[1].begin() as session:
        create_engineering_project(session, EngineeringProjectSpec(project_id='carrier', name='Public reference fixture', domains=('kicad',)))
    row = persist(db, (record, artifacts, authority))
    with TestClient(create_app(db[0], initialize_catalog=False)) as client:
        base = f"/api/investigations/{row['investigation_id']}/versions/{row['id']}"
        response = client.get(base + '/projection')
        assert response.status_code == 200, response.text
        projection = response.json()['projections'][0]
        assert projection['schema_version'] == 'kicad.investigation.v1'
        assert projection['data']['mapping']['gap'] == 'reviewed_mapping_assertions_missing'
        assert projection['data']['physical_electrical_verdict'] == 'inconclusive'
        assert projection['data']['configured_reports'][0]['kind'] == 'erc'
        assert client.get(base).json()['envelope']['provenance'] == 'recorded_bytes_not_new_execution'
