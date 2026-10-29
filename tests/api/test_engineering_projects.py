"""HTTP project-kind contract on temporary SQLite; no execution or public writes."""
from fastapi.testclient import TestClient
import pytest
import sqlalchemy as sa

from veriforge.api.app import create_app
from veriforge.core import engineering_catalog as catalog
from veriforge.core.database import Campaign, Dispatch, Project


@pytest.fixture
def client(db):
    with TestClient(create_app(db[0])) as client:
        yield client


def add_engineering(db):
    with db[1].begin() as session:
        catalog.create_engineering_project(session, catalog.EngineeringProjectSpec(project_id='recorded-board', name='Recorded board', domains=('kicad', 'system')))


def test_project_list_and_detail_canonical_kind_without_fake_rtl_contract(client, db):
    add_engineering(db)
    items = {item['id']: item for item in client.get('/api/projects').json()['items']}
    board = items['recorded-board']
    assert board == {'id': 'recorded-board', 'name': 'Recorded board', 'description': '',
        'domains': ['kicad', 'system'], 'kind': 'engineering_recorded', 'contract_id': None}
    assert not {'adapter_id', 'family', 'source_file_hash'} & board.keys()
    assert client.get('/api/projects/recorded-board').json() == board
    assert items['fifo_correct']['kind'] == 'rtl_fixture'
    assert items['fifo_correct']['contract_id'] == 'fifo-v1'
    assert items['fifo_correct']['adapter_id'] == 'rtl.fifo.v1'
    assert items['fifo_correct']['family'] == 'fifo' and items['fifo_correct']['source_file_hash']
    assert client.get('/api/health').json()['investigation_api'] == '1'


def test_engineering_project_cannot_dispatch_and_no_public_create_route(client, db):
    add_engineering(db)
    response = client.post('/api/campaigns', json={'project_id': 'recorded-board',
        'plan': {'experiment_id': 'not-executable', 'fixture_id': 'recorded-board', 'contract_id': None, 'operations': [{'op': 'reset'}]}},
        headers={'Idempotency-Key': 'no-execution'})
    assert response.status_code in {409, 422}
    assert client.post('/api/projects', json={'kind': 'engineering_recorded'}).status_code == 405
    with db[1]() as session:
        assert session.scalar(sa.select(sa.func.count()).select_from(Campaign)) == 0
        assert session.scalar(sa.select(sa.func.count()).select_from(Dispatch)) == 0


def test_conflicting_metadata_fails_closed_and_foreign_project_hidden(client, db):
    with db[1].begin() as session:
        legacy = session.get(Project, 'fifo_correct')
        legacy.data = {**legacy.data, 'contract_id': 'different-contract'}
        session.add(Project(id='foreign-record', owner='foreign', kind='engineering_recorded', contract_id=None, data={}))
    assert client.get('/api/projects/fifo_correct').status_code == 409
    assert client.get('/api/projects').status_code == 409
    assert client.get('/api/projects/foreign-record').status_code == 404
