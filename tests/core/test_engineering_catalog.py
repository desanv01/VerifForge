"""Local catalog semantics and isolated migrations, never project execution."""
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
import importlib.util
from pathlib import Path
from types import SimpleNamespace

from alembic import command
from alembic.config import Config
import pytest
import sqlalchemy as sa
from sqlalchemy.exc import IntegrityError

from veriforge.core import engineering_catalog as catalog, families, service
from veriforge.core.catalog import seed_catalog
from veriforge.core.database import Campaign, Project, make_engine, session_factory


@pytest.fixture
def db(tmp_path):
    engine = make_engine('sqlite:///' + (tmp_path / 'catalog.db').as_posix())
    config = Config('alembic.ini')
    with engine.begin() as connection:
        config.attributes['connection'] = connection
        command.upgrade(config, 'head')
    sessions = session_factory(engine)
    with sessions.begin() as session: seed_catalog(session)
    yield engine, sessions
    engine.dispose()


def spec(**changes):
    return catalog.EngineeringProjectSpec(project_id='recorded-board', name='Recorded board evidence', description='Import only', domains=('kicad', 'system'), **changes)


def create(db, value=None):
    with db[1].begin() as session:
        return catalog.project_view(catalog.create_engineering_project(session, value or spec()))


def test_create_get_idempotent_and_canonical_wire_metadata(db):
    first = create(db)
    assert create(db) == first
    assert first == {'id': 'recorded-board', 'name': 'Recorded board evidence', 'description': 'Import only',
        'domains': ['kicad', 'system'], 'kind': 'engineering_recorded', 'contract_id': None}
    with db[1]() as session:
        assert catalog.project_view(catalog.get_engineering_project(session, 'recorded-board', expected=spec())) == first
    with pytest.raises(service.Conflict): create(db, spec().model_copy(update={'name': 'Changed'}))
    with pytest.raises(service.Conflict): create(db, spec().model_copy(update={'project_id': 'fifo_correct'}))


def test_create_outer_rollback_is_atomic_and_concurrent_same_metadata_is_unique(db):
    with pytest.raises(RuntimeError):
        with db[1].begin() as session:
            catalog.create_engineering_project(session, spec())
            raise RuntimeError('Caller rollback before admission')
    with db[1]() as session: assert session.get(Project, 'recorded-board') is None
    with ThreadPoolExecutor(max_workers=3) as pool:
        rows = list(pool.map(lambda _: create(db), range(3)))
    assert rows[0] == rows[1] == rows[2]


def test_engineering_execution_rejected_before_adapter_or_reused_key(db, monkeypatch):
    create(db)
    def forbidden(*args): pytest.fail('Engineering projects must not resolve a default FIFO adapter')
    monkeypatch.setattr(service, 'project_adapter', forbidden)
    with db[1].begin() as session:
        for plan in ({}, {'contract_id': None}, {'contract_id': 'fifo-v1'}):
            with pytest.raises(service.Conflict, match='do not support RTL'):
                service.create_campaign(session, 'recorded-board', plan, 'shared-key')
        with pytest.raises(service.Conflict, match='do not support RTL'):
            families.project_adapter(session.get(Project, 'recorded-board'))
        assert session.scalar(sa.select(sa.func.count()).select_from(Campaign)) == 0


def test_legacy_fixture_default_and_fields_remain_executable(db):
    with db[1].begin() as session:
        row = session.get(Project, 'fifo_correct')
        wire = catalog.project_view(row)
        assert wire['kind'] == 'rtl_fixture' and wire['contract_id'] == 'fifo-v1'
        assert wire['adapter_id'] == families.FIFO_ADAPTER and wire['source_file_hash'] == row.data['source_file_hash']
        plan = {'experiment_id': 'legacy', 'fixture_id': 'fifo_correct', 'contract_id': 'fifo-v1', 'objective_ids': ['FIFO_BEHAVIOR'], 'operations': [{'op': 'reset'}]}
        campaign = service.create_campaign(session, row.id, plan, 'legacy')
        assert service.create_campaign(session, row.id, plan, 'legacy').id == campaign.id
    create(db)
    with db[1].begin() as session:
        with pytest.raises(service.Conflict, match='do not support RTL'):
            service.create_campaign(session, 'recorded-board', plan, 'legacy')


@pytest.mark.parametrize('kind,contract', [('rtl_fixture', None), ('engineering_recorded', 'fifo-v1'), ('other', None)])
def test_project_kind_contract_constraint_and_campaign_contract_stays_required(db, kind, contract):
    with pytest.raises(IntegrityError):
        with db[1].begin() as session: session.add(Project(id='bad', kind=kind, contract_id=contract, data={}))
    assert not next(column for column in sa.inspect(db[0]).get_columns('campaigns') if column['name'] == 'contract_id')['nullable']


@pytest.mark.parametrize('field,value', [('kind', 'rtl_fixture'), ('owner', 'other'), ('data', {'adapter_id': 'rtl.fifo.v1'}), ('id', 'renamed')])
def test_engineering_metadata_immutable_orm_and_sql(db, field, value):
    create(db)
    with pytest.raises((ValueError, IntegrityError)):
        with db[1].begin() as session: setattr(session.get(Project, 'recorded-board'), field, value)
    with pytest.raises(IntegrityError):
        with db[0].begin() as connection: connection.execute(sa.text("UPDATE projects SET owner='other' WHERE id='recorded-board'"))


def test_conflicting_legacy_metadata_fails_closed_and_foreign_engineering_hidden(db):
    with db[1].begin() as session:
        row = session.get(Project, 'fifo_correct')
        row.data = {**row.data, 'kind': 'engineering_recorded'}
    with db[1]() as session:
        with pytest.raises(service.Conflict): catalog.project_view(session.get(Project, 'fifo_correct'))
    with db[1].begin() as session:
        session.add(Project(id='foreign', owner='other', kind='engineering_recorded', contract_id=None, data={}))
    with db[1]() as session:
        with pytest.raises(service.NotFound): catalog.get_engineering_project(session, 'foreign')


def test_downgrade_refuses_engineering_history(db):
    create(db)
    config = Config('alembic.ini')
    with pytest.raises(ValueError, match='retained engineering'):
        with db[0].begin() as connection:
            config.attributes['connection'] = connection
            command.downgrade(config, '12u2resources')


def test_sqlite_upgrade_downgrade_preserves_existing_project_campaign_and_foreign_keys(tmp_path):
    engine = make_engine('sqlite:///' + (tmp_path / 'legacy.db').as_posix())
    config = Config('alembic.ini')
    at = datetime(2026, 10, 30, tzinfo=timezone.utc)
    try:
        with engine.begin() as connection:
            config.attributes['connection'] = connection
            command.upgrade(config, '12u2resources')
            metadata = sa.MetaData()
            metadata.reflect(connection)
            connection.execute(metadata.tables['contracts'].insert(), dict(id='legacy-contract', data={'original': True}, identity={}))
            connection.execute(metadata.tables['projects'].insert(), dict(id='legacy-project', owner='local', contract_id='legacy-contract', data={'id': 'legacy-project', 'custom': 'retain'}))
            connection.execute(metadata.tables['campaigns'].insert(), dict(id='legacy-campaign', owner='local', project_id='legacy-project', contract_id='legacy-contract', create_key='legacy', request_hash='a'*64,
                plan={'exact': 'original'}, identity={}, execution_status='draft', version=0, event_sequence=0, created_at=at, updated_at=at))
        with engine.begin() as connection:
            config.attributes['connection'] = connection
            connection.exec_driver_sql('BEGIN')
            connection.exec_driver_sql('PRAGMA defer_foreign_keys=ON')
            command.upgrade(config, '13engineeringprojects')
            assert connection.exec_driver_sql('PRAGMA defer_foreign_keys').scalar() == 1
        with engine.begin() as connection:
            assert connection.execute(sa.text('PRAGMA foreign_keys')).scalar() == 1
            assert connection.execute(sa.text('PRAGMA foreign_key_check')).all() == []
            assert connection.execute(sa.text("SELECT kind FROM projects WHERE id='legacy-project'")).scalar() == 'rtl_fixture'
            assert connection.execute(sa.text("SELECT plan FROM campaigns WHERE id='legacy-campaign'")).scalar() == '{"exact": "original"}'
            config.attributes['connection'] = connection
            command.downgrade(config, '12u2resources')
        with engine.connect() as connection:
            assert connection.execute(sa.text('PRAGMA foreign_key_check')).all() == []
            assert connection.execute(sa.text("SELECT contract_id FROM projects WHERE id='legacy-project'")).scalar() == 'legacy-contract'
    finally: engine.dispose()


def test_postgres_ddl_is_nullable_only_for_project_and_has_canonical_checks(monkeypatch):
    spec_ = importlib.util.spec_from_file_location('engineering_migration', Path('migrations/versions/13engineeringprojects_project_kind.py'))
    module = importlib.util.module_from_spec(spec_)
    spec_.loader.exec_module(module)
    calls, checks, statements = [], [], []
    monkeypatch.setattr(module, 'op', SimpleNamespace(get_bind=lambda: SimpleNamespace(dialect=SimpleNamespace(name='postgresql')),
        add_column=lambda table, column: calls.append(('add', table, column.name)),
        alter_column=lambda table, column, **kw: calls.append(('nullable', table, column, kw['nullable'])),
        create_check_constraint=lambda name, table, sql: checks.append(sql), execute=lambda sql: statements.append(str(sql))))
    module.upgrade()
    assert calls == [('add', 'projects', 'kind'), ('nullable', 'projects', 'contract_id', True)]
    assert checks == [str(next(c for c in Project.__table__.constraints if isinstance(c, sa.CheckConstraint)).sqltext)]
    assert any('NEW.data::text' in sql and 'Immutable' in sql for sql in statements)


def test_sqlite_project_rebuild_rolls_back_as_one_transaction(tmp_path):
    engine = make_engine('sqlite:///' + (tmp_path / 'rollback.db').as_posix())
    config = Config('alembic.ini')
    try:
        with engine.begin() as connection:
            config.attributes['connection'] = connection
            command.upgrade(config, '12u2resources')
        with pytest.raises(RuntimeError):
            with engine.begin() as connection:
                config.attributes['connection'] = connection
                command.upgrade(config, '13engineeringprojects')
                raise RuntimeError('No adoption commit')
        with engine.connect() as connection:
            assert 'kind' not in {column['name'] for column in sa.inspect(connection).get_columns('projects')}
            assert connection.execute(sa.text('SELECT version_num FROM alembic_version')).scalar() == '12u2resources'
            assert connection.execute(sa.text('PRAGMA foreign_keys')).scalar() == 1
            assert connection.execute(sa.text('PRAGMA foreign_key_check')).all() == []
    finally: engine.dispose()
