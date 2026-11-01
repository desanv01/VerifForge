"""Additive schema checks use temporary SQLite, never the operator database."""
import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import inspect, text

from veriforge.core.database import Base, make_engine, session_factory
from veriforge.core.catalog import seed_catalog
from veriforge.core.recorded_import import require_supported_schema


def test_additive_repair_upgrade_preserves_catalog_and_import_schema(tmp_path):
    engine = make_engine('sqlite:///' + (tmp_path / 'migration.db').as_posix())
    config = Config('alembic.ini')
    try:
        with engine.begin() as connection:
            config.attributes['connection'] = connection
            command.upgrade(config, '13engineeringprojects')
        sessions = session_factory(engine)
        with sessions.begin() as session:
            seed_catalog(session)
            require_supported_schema(session)
        with engine.begin() as connection:
            before_tables = set(inspect(connection).get_table_names())
            before = list(connection.execute(text('SELECT * FROM projects ORDER BY id')))
            config.attributes['connection'] = connection
            command.upgrade(config, '14u3repairjobs')
            assert set(inspect(connection).get_table_names()) == before_tables | {'repair_jobs', 'repair_cases'}
            assert list(connection.execute(text('SELECT * FROM projects ORDER BY id'))) == before
            assert list(connection.execute(text('PRAGMA foreign_key_check'))) == []
            columns = {c['name']: c for c in inspect(connection).get_columns('repair_jobs')}
            assert columns['cleanup_status']['type'].length == 64
            assert set(columns) == set(Base.metadata.tables['repair_jobs'].columns.keys())
            indexes = inspect(connection).get_indexes('repair_cases')
            assert any(i['name'] == 'uq_repair_active_child' and i['unique'] for i in indexes)
        with sessions.begin() as session:
            require_supported_schema(session)
        with engine.begin() as connection:
            config.attributes['connection'] = connection
            with pytest.raises(RuntimeError, match='preservation review'):
                command.downgrade(config, '13engineeringprojects')
    finally:
        engine.dispose()
