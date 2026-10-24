import pytest
from alembic import command
from alembic.config import Config

from veriforge.core.catalog import seed_catalog
from veriforge.core.database import make_engine, session_factory


@pytest.fixture
def db(tmp_path):
    engine = make_engine("sqlite:///" + (tmp_path / "state.db").as_posix())
    config = Config("alembic.ini")
    with engine.begin() as connection:
        config.attributes["connection"] = connection
        command.upgrade(config, "head")
    sessions = session_factory(engine)
    with sessions.begin() as session:
        seed_catalog(session)
    yield engine, sessions
    engine.dispose()
