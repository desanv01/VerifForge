"""Temporary SQLite projection reads; synthetic admitted inputs, no tool jobs."""
from pathlib import Path
import runpy

import pytest
import sqlalchemy as sa
from fastapi.testclient import TestClient

from veriforge.api.app import create_app
from veriforge.core.database import Dispatch, InvestigationArtifact, Project

helpers = runpy.run_path(str(Path(__file__).with_name('test_investigation_routes.py')))
store = helpers['store']


@pytest.fixture
def client(db):
    with TestClient(create_app(db[0], initialize_catalog=False)) as client:
        yield client


def url(row):
    return f"/api/investigations/{row['investigation_id']}/versions/{row['id']}/projection"


def test_bound_read_only_system_projection(client, db):
    row = store(db)
    response = client.get(url(row))
    assert response.status_code == 200
    body = response.json()
    assert body['schema_version'] == 'engineering.projection.v1'
    assert (body['project_id'], body['investigation_id'], body['version_id'], body['content_hash']) == (
        row['project_id'], row['investigation_id'], row['id'], row['content_hash'])
    assert body['scope'] == 'recorded_import' and body['execution_status'] == 'not_run'
    system = body['projections'][0]
    assert system['domain'] == 'system' and system['schema_version'] == 'system.investigation.v1'
    assert system['data']['correction_status'] == 'no_candidate_rerun_evidence'
    assert system['data']['manifest_checks'] is not None
    assert body['gaps']
    assert client.get(url(row), headers={'If-None-Match': response.headers['etag']}).status_code == 304
    assert client.post(url(row), json={}).status_code == 405
    with db[1]() as session:
        assert session.scalar(sa.select(sa.func.count()).select_from(Dispatch)) == 0


def test_projection_revalidates_even_with_matching_etag(client, db):
    row = store(db)
    response = client.get(url(row))
    assert response.status_code == 200
    with db[0].begin() as connection:
        connection.execute(sa.text('DROP TRIGGER immutable_investigation_artifacts_update'))
        original = connection.execute(sa.select(InvestigationArtifact.data).where(
            InvestigationArtifact.version_id == row['id'], InvestigationArtifact.artifact_id == 'header')).scalar_one()
        corrupt = bytes([original[0] ^ 1]) + original[1:]
        connection.execute(sa.update(InvestigationArtifact).where(
            InvestigationArtifact.version_id == row['id'], InvestigationArtifact.artifact_id == 'header').values(data=corrupt))
    assert client.get(url(row), headers={'If-None-Match': response.headers['etag']}).status_code == 409


def test_projection_preserves_ownership_and_version_binding(client, db):
    first, second = store(db), store(db, key='second')
    assert client.get(url(first).replace(first['id'], second['id'])).status_code == 404
    with db[1].begin() as session:
        session.get(Project, first['project_id']).owner = 'foreign'
    assert client.get(url(first)).status_code == 404


@pytest.mark.parametrize('family,relative,domain,count', [
    ('riscv-41202693632', 'u4-riscv/ci-41202693632/riscv-evidence-c14525e3791569215ba385a1c5582d52d0b8858f-41202693632-1', 'riscv', 4),
    ('firmware-40929665294', 'u5-firmware/ci-40929665294/firmware-evidence-5565985bbe2bb2a0be869d366baa48c715057657-40929665294-1', 'firmware', 5),
])
def test_retained_native_bundle_to_persisted_http_projection(client, db, family, relative, domain, count):
    """Recorded evidence through SQLite/HTTP, never new native/model execution."""
    from datetime import datetime, timezone
    from veriforge.core.engineering_catalog import EngineeringProjectSpec, create_engineering_project
    from veriforge.system_investigation.native_tool_import import prepare_native_bundle, apply_prepared

    root = Path(__file__).resolve().parents[2] / '.artifacts' / relative
    if not root.is_dir():
        pytest.skip('retained private native evidence is not distributed in this checkout')
    project_id = 'projection-' + domain
    prepared = prepare_native_bundle(family=family, bundle_root=root,
        project_id=project_id, revision_id=project_id + '-v1')
    with db[1].begin() as session:
        create_engineering_project(session, EngineeringProjectSpec(project_id=project_id,
            name='Recorded projection test', domains=(domain,)))
    with db[1].begin() as session:
        result = apply_prepared(session, prepared, idempotency_key='projection-' + domain,
            at=datetime(2026, 11, 1, tzinfo=timezone.utc))
        from veriforge.core import investigations
        row = investigations.version_view(session, result['db_investigation_id'], result['db_version_id'])
    response = client.get(url(row))
    assert response.status_code == 200, response.text
    body = response.json()
    assert body['content_hash'] == row['content_hash'] and body['execution_status'] == 'not_run'
    projection = body['projections'][0]
    assert projection['schema_version'] == domain + '.investigation-set.v1'
    cases = {case['case_label']: case for case in projection['data']['cases']}
    assert len(cases) == count
    if domain == 'riscv':
        assert cases['control-saved-replay']['lineage']['status'] == 'saved_program_bound'
        assert cases['control-replay']['lineage']['status'] == 'exact_recompiled_replay'
        assert cases['mutant']['first_divergence']['cycle'] == 14
    else:
        assert cases['bad']['first_conflict']['cycle'] == 95
        assert cases['candidate']['candidate']['validation_status'] == 'recorded_scoped_validation'
    with db[1]() as session:
        assert session.scalar(sa.select(sa.func.count()).select_from(Dispatch)) == 0
