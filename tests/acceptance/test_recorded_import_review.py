"""Independent synthetic-only trusted import composition/CLI review."""
from contextlib import contextmanager
import hashlib
import json
from pathlib import Path
import runpy
from types import SimpleNamespace

from alembic import command
from alembic.config import Config
import pytest
import sqlalchemy as sa
from sqlalchemy.exc import OperationalError

from veriforge.core import recorded_import as importer, investigations
from veriforge.core.database import Project, InvestigationArtifact, InvestigationVersion, make_engine, session_factory
from veriforge.core.engineering_catalog import create_engineering_project
from veriforge.system_investigation import records

ROOT = Path(__file__).resolve().parents[2]
BUILDERS = runpy.run_path(str(ROOT / 'tests/core/test_recorded_import.py'))
AT, PATHS = BUILDERS['AT'], BUILDERS['PATHS']


@pytest.fixture
def admitted():
    return BUILDERS['admitted'].__wrapped__()  # Synthetic bytes only; no fixed real bundle.


@pytest.fixture
def db(tmp_path):
    engine = make_engine('sqlite:///' + (tmp_path / 'independent-import.db').as_posix())
    config = Config(str(ROOT / 'alembic.ini'))
    with engine.begin() as connection:
        config.attributes['connection'] = connection
        command.upgrade(config, importer.SUPPORTED_REVISION)
    yield engine, session_factory(engine)
    engine.dispose()


@pytest.fixture
def loader(monkeypatch, admitted):
    calls = []
    def prepare(**kwargs):
        calls.append(kwargs)
        assert kwargs == PATHS
        return admitted
    monkeypatch.setattr(importer.prepare_import, 'prepare_corrected_case', prepare)
    return calls


def counts(db):
    with db[0].connect() as conn:
        return {name: conn.execute(sa.text(f'SELECT COUNT(*) FROM "{name}"')).scalar_one()
                for name in sa.inspect(conn).get_table_names()}


def perform(db, key='independent-key'):
    with db[1].begin() as session:
        return importer.import_corrected_case(session, idempotency_key=key, at=AT, **PATHS)


def test_whole_unit_exact_identity_idempotency_and_no_execution_rows(db, admitted, loader):
    before = counts(db)
    first = perform(db)
    assert perform(db) == first
    assert first['database'] == 'pending_caller_commit' and first['new_execution'] == 'not_run'
    raw = records.serialize_investigation(admitted)
    assert first['content_sha256'] == hashlib.sha256(raw).hexdigest()
    after = counts(db)
    assert {key: after[key] - before[key] for key in after if after[key] != before[key]} == {
        'projects': 1, 'investigations': 1, 'investigation_versions': 1,
        'investigation_artifacts': len(admitted.artifacts)}
    with db[1]() as session:
        version = session.get(InvestigationVersion, first['db_version_id'])
        assert version.content == raw
        rows = session.scalars(sa.select(InvestigationArtifact)).all()
        stored = {(r.artifact_id, r.native_revision_id, r.native_attempt_id): r for r in rows}
        for blob in admitted.artifacts:
            r = blob.reference
            row = stored[r.artifact_id, r.revision_id, r.attempt_id]
            assert row.data == blob.data and row.sha256 == r.sha256
        view = investigations.version_view(session, first['db_investigation_id'], first['db_version_id'])
        assert view['manifest'] == admitted.manifest.model_dump(mode='json')
        assert view['execution_contexts'] == []


def test_caught_post_write_failure_rolls_back_import_but_preserves_unrelated_caller_work(db, loader, monkeypatch):
    before = counts(db)
    original = importer.investigations.persist_investigation
    def after_write(*args, **kwargs):
        original(*args, **kwargs)
        raise ValueError('PRIVATE_POST_WRITE_BYTES')
    monkeypatch.setattr(importer.investigations, 'persist_investigation', after_write)
    unrelated = importer.PROJECT_SPEC.model_copy(update={'project_id': 'unrelated-recorded-project'})
    with db[1].begin() as session:
        create_engineering_project(session, unrelated)
        session.flush()
        with pytest.raises(importer.RecordedImportError) as caught:
            importer.import_corrected_case(session, idempotency_key='caught-failure', at=AT, **PATHS)
        assert 'PRIVATE_POST_WRITE_BYTES' not in str(caught.value)
    after = counts(db)
    assert {key: after[key] - before[key] for key in after if after[key] != before[key]} == {'projects': 1}
    with db[1]() as session:
        assert session.get(Project, unrelated.project_id) is not None
        assert session.get(Project, importer.PROJECT_SPEC.project_id) is None


def test_outer_rollback_after_success_removes_all_new_rows(db, loader):
    before = counts(db)
    with pytest.raises(RuntimeError):
        with db[1].begin() as session:
            result = importer.import_corrected_case(session, idempotency_key='rollback', at=AT, **PATHS)
            assert result['database'] == 'pending_caller_commit'
            raise RuntimeError('simulated caller rollback')
    assert counts(db) == before


@pytest.mark.parametrize('damage', ['missing-blob', 'duplicate-blob', 'wrong-revision'])
def test_even_sealed_wrapper_mutation_is_revalidated_without_publication(db, admitted, damage):
    forged = object.__new__(type(admitted))
    for name, value in vars(admitted).items():
        object.__setattr__(forged, name, value)
    if damage == 'missing-blob':
        object.__setattr__(forged, 'artifacts', admitted.artifacts[1:])
    elif damage == 'duplicate-blob':
        object.__setattr__(forged, 'artifacts', (*admitted.artifacts, admitted.artifacts[0]))
    else:
        altered = admitted.envelope.record.model_copy(update={'revision_id': 'unapproved-revision'})
        object.__setattr__(forged, 'envelope', admitted.envelope.model_copy(update={'record': altered}))
    before = counts(db)
    with db[1].begin() as session:
        with pytest.raises(importer.RecordedImportError):
            importer._persist_prepared(session, forged, idempotency_key='forged', at=AT)
    assert counts(db) == before


def test_conflicting_frozen_project_spec_is_not_updated(db, loader):
    changed = importer.PROJECT_SPEC.model_copy(update={'domains': ('system',)})
    with db[1].begin() as session:
        create_engineering_project(session, changed)
    before = counts(db)
    with pytest.raises(importer.RecordedImportError, match='immutable_content_conflict'):
        perform(db)
    assert counts(db) == before
    with db[1]() as session:
        assert session.get(Project, changed.project_id).data['domains'] == ['system']


@pytest.mark.parametrize('kind', ['old', 'future', 'missing', 'multiple'])
def test_exact_schema_marker_refusal_precedes_input_read_and_never_auto_migrates(db, monkeypatch, kind):
    with db[0].begin() as conn:
        if kind == 'missing':
            conn.execute(sa.text('DELETE FROM alembic_version'))
        elif kind == 'multiple':
            conn.execute(sa.text("INSERT INTO alembic_version(version_num) VALUES ('future-branch')"))
        else:
            conn.execute(sa.text('UPDATE alembic_version SET version_num=:value'), {'value': '12u2resources' if kind == 'old' else 'future-unknown'})
    def forbidden(**kwargs):
        pytest.fail('Unsupported schema reached input preparation')
    monkeypatch.setattr(importer.prepare_import, 'prepare_corrected_case', forbidden)
    before = counts(db)
    with db[1].begin() as session:
        with pytest.raises(importer.RecordedImportError, match='unsupported_schema_revision'):
            importer.import_corrected_case(session, idempotency_key='schema', at=AT, **PATHS)
    assert counts(db) == before


def arguments(commit=False):
    args = ['--case', 'u7-corrected-v1']
    for key, path in PATHS.items():
        args += ['--' + key.replace('_', '-'), str(path)]
    return args + (['--commit', '--idempotency-key', 'review-cli'] if commit else [])


def test_default_cli_never_opens_database_or_echoes_configuration(loader, monkeypatch, capsys):
    monkeypatch.setenv('VERIFORGE_DATABASE_URL', 'PRIVATE_CONFIGURATION_SENTINEL')
    def forbidden():
        pytest.fail('Default preparation opened a database')
    monkeypatch.setattr(importer, '_configured_engine', forbidden)
    assert importer.main(arguments()) == 0
    output = capsys.readouterr()
    assert json.loads(output.out)['database'] == 'not_opened'
    assert 'PRIVATE_CONFIGURATION_SENTINEL' not in output.out + output.err


def test_commit_failure_is_rolled_back_and_never_prints_success_or_private_driver_text(db, loader, monkeypatch, capsys):
    real_sessions = db[1]
    @contextmanager
    def transaction():
        with real_sessions.begin() as session:
            yield session
            raise OperationalError('PRIVATE_SQL_DRIVER_SENTINEL', {}, RuntimeError('PRIVATE_URL_SENTINEL'))
    monkeypatch.setattr(importer, '_configured_engine', lambda: db[0])
    monkeypatch.setattr(importer, 'session_factory', lambda engine: SimpleNamespace(begin=transaction))
    before = counts(db)
    assert importer.main(arguments(True)) == 2
    output = capsys.readouterr()
    assert json.loads(output.out) == {'status': 'rejected', 'stage': 'storage', 'code': 'commit_or_connection_failed'}
    assert 'PRIVATE_' not in output.out + output.err and 'committed' not in output.out
    assert counts(db) == before


def test_cli_refuses_secret_url_argument_without_echo_or_input_read(monkeypatch, capsys):
    def forbidden(**kwargs):
        pytest.fail('Invalid CLI arguments reached input preparation')
    monkeypatch.setattr(importer.prepare_import, 'prepare_corrected_case', forbidden)
    assert importer.main(arguments() + ['--database-url', 'PRIVATE_URL_SENTINEL']) == 2
    output = capsys.readouterr()
    assert json.loads(output.out)['code'] == 'invalid_arguments'
    assert 'PRIVATE_URL_SENTINEL' not in output.out + output.err
