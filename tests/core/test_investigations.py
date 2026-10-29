"""Initial additive schema gates; admission/API cases follow pure-record binding."""
import hashlib
import json
from datetime import datetime, timezone
from concurrent.futures import ThreadPoolExecutor
import importlib.util
from pathlib import Path
from types import SimpleNamespace

from alembic import command
from alembic.config import Config
import pytest
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql
from sqlalchemy.exc import IntegrityError
from sqlalchemy.schema import CreateTable

from veriforge.core.catalog import seed_catalog
from veriforge.core.database import (Base, Investigation, InvestigationArtifact, InvestigationVersion,
    InvestigationExecutionLink, Attempt, Finding, Project, make_engine, session_factory)
from veriforge.contracts import AttemptResult, Experiment
from veriforge.core.planner_experiments import resolve_attempt_experiment
from veriforge.core import investigations as service, service as campaigns
from veriforge.contracts.engineering import EvidenceReference
from veriforge.system_investigation import AddressBinding, RegisterIntent, SourceArtifact, bundle_files, compare_registers
from veriforge.system_investigation import records

AT = datetime(2026, 10, 30, tzinfo=timezone.utc)


def encode(model):
    return json.dumps(model.model_dump(mode='json'), sort_keys=True, separators=(',', ':'), ensure_ascii=False).encode()


def reference(name, data, revision='native-v1', attempt=None):
    return EvidenceReference(artifact_id=name, sha256=hashlib.sha256(data).hexdigest(), revision_id=revision, attempt_id=attempt)


def valid_bundle(*, project='fifo_correct', revision='engineering-1', attempts=(), extra=()):
    """Actual pure parsing on synthetic bytes; no tool execution/review claim."""
    header = b'// PRIVATE_SOURCE_ONLY\n#define FLAG_ADDR 0x40000000u\n'
    rtl = b"case (addr)\n8'h00: flag <= write_data;\ndefault: ;\nendcase\n"
    sources = [SourceArtifact(reference=reference(name, data), data=data, origin='synthetic_fixture') for name, data in [('header', header), ('rtl', rtl)]]
    intent = RegisterIntent(byte_base=0x40000000, bindings=(AddressBinding(binding_id='flag', header_macro='FLAG_ADDR', rtl_signal='flag', expected_address=0x40000000),))
    report = compare_registers(*sources, intent)
    files = bundle_files(report)
    blobs = {}
    def add(ref, data):
        blobs[(ref.artifact_id, ref.revision_id, ref.attempt_id)] = records.ArtifactBytes(reference=ref, data=data)
        return ref
    for source in sources: add(source.reference, source.data)
    proofs = {proof.artifact.artifact_id: proof.artifact for row in report.native_records for proof in row.fact.provenance.sources}
    add(proofs['reviewed-intent'], encode(intent))
    configuration = b'{"scope":"synthetic recorded engineering sources"}'
    configuration_ref = add(reference('configuration', configuration), configuration)
    project_revision = records.EngineeringProjectRevision(project_id=project, revision_id=revision,
        source_refs=tuple(proofs.values()) + (configuration_ref,), configuration_hash=configuration_ref.sha256)
    project_ref = add(reference('project', encode(project_revision)), encode(project_revision))
    manifest_ref = add(reference('manifest', encode(report.manifest)), encode(report.manifest))
    report_ref = add(reference('bridge', encode(report)), encode(report))
    native = []
    for row in report.native_records:
        add(row.fact.native_record, files['native/' + row.fact.native_record.artifact_id + '.json'])
        native.append(records.NativeAttachment(fact_id=row.fact.fact_id, domain=row.fact.domain, native_schema_id=row.fact.native_schema_id, reference=row.fact.native_record))
    for relation in report.manifest.relations:
        for ref in relation.evidence: add(ref, files['comparisons/' + ref.artifact_id + '.json'])
    for blob in extra: add(blob.reference, blob.data)
    record = records.EngineeringInvestigation(investigation_id='native-investigation', project_id=project, revision_id=revision,
        project_revision_ref=project_ref, manifest_ref=manifest_ref, bridge_report_ref=report_ref, native_records=tuple(native), attempts=attempts)
    authority = records.create_admission_authority(project_id=project, revision_id=revision, project_revision_ref=project_ref,
        manifest_ref=manifest_ref, bridge_report_ref=report_ref, attempts=attempts)
    return record, tuple(blobs.values()), authority


def persist(db, bundle=None, key='first', **kwargs):
    record, blobs, authority = bundle or valid_bundle()
    with db[1].begin() as session:
        return service.persist_investigation(session, record, blobs, authority=authority, idempotency_key=key, at=AT, **kwargs)


def execution_bundle(db):
    with db[1].begin() as session:
        experiment = Experiment(experiment_id='wire-seed', fixture_id='fifo_correct', objective_ids=('FIFO_BEHAVIOR',), operations=({'op': 'reset'},))
        campaign = campaigns.create_campaign(session, 'fifo_correct', experiment.model_dump(mode='json'), 'context-seed')
        campaigns.start(session, campaign.id)
        attempt = session.scalar(sa.select(Attempt).where(Attempt.campaign_id == campaign.id))
        lease, _, _ = campaigns.claim(session, attempt.id)
        bound = resolve_attempt_experiment(session, attempt)
        result = AttemptResult(attempt_id='native-run-not-db-id', experiment_id=bound.wire_id, execution_status='completed', verdict='pass_within_scope',
            identity={**bound.identity, 'environment_hash': 'b' * 64}, usage={'cycles': 1}, uncertainties=('Synthetic result; no tool executed',))
        campaigns.finalize(session, attempt.id, lease, result.model_dump(mode='json'))
        raw = encode(result)
        ref = reference('recorded-result', raw, revision='attempt-revision', attempt=result.attempt_id)
        binding = records.CampaignAttemptEvidence(campaign_id=campaign.id, db_attempt_id=attempt.id,
            experiment_record_id=bound.id, native_attempt_id=result.attempt_id, wire_experiment_id=bound.wire_id, result_ref=ref)
    return valid_bundle(attempts=(binding,), extra=(records.ArtifactBytes(reference=ref, data=raw),)), result


@pytest.fixture
def db(tmp_path):
    engine = make_engine('sqlite:///' + (tmp_path / 'investigations.db').as_posix())
    config = Config('alembic.ini')
    with engine.begin() as connection:
        config.attributes['connection'] = connection
        command.upgrade(config, 'head')
    sessions = session_factory(engine)
    with sessions.begin() as session: seed_catalog(session)
    yield engine, sessions
    engine.dispose()


def schema_version(db):
    """Schema-only synthetic bytes, never claimed as admitted native evidence."""
    with db[1].begin() as session:
        investigation = Investigation(project_id='fifo_correct', create_key='schema-test', request_hash='a' * 64)
        session.add(investigation)
        session.flush()
        version = InvestigationVersion(investigation_id=investigation.id, project_id='fifo_correct', ordinal=1,
            admission_key='schema-test', schema_version='schema-test', revision_id='schema-test',
            revision_hash='b' * 64, manifest_hash='c' * 64, content_hash=hashlib.sha256(b'{}').hexdigest(), content=b'{}')
        session.add(version)
        session.flush()
        return investigation.id, version.id


def artifact(version_id, index, data=b'x'):
    return InvestigationArtifact(version_id=version_id, artifact_id='source-' + str(index), native_revision_id='external-revision',
        native_attempt_id='opaque-external-run', identity_key=hashlib.sha256(str(index).encode()).hexdigest(),
        sha256=hashlib.sha256(data).hexdigest(), size_bytes=len(data), media_type='application/octet-stream', role='source', data=data)


def test_artifact_count_and_per_blob_sql_bounds(db):
    _, version_id = schema_version(db)
    with db[1].begin() as session:
        session.add_all(artifact(version_id, index) for index in range(256))
    with pytest.raises(IntegrityError, match='quota'):
        with db[1].begin() as session: session.add(artifact(version_id, 257))
    with pytest.raises(IntegrityError):
        with db[1].begin() as session: session.add(artifact(version_id, 258, b'x' * (1_048_576 + 1)))


def test_total_artifact_bytes_sql_bound(db):
    _, version_id = schema_version(db)
    with db[1].begin() as session:
        session.add_all(artifact(version_id, index, b'x' * 1_048_576) for index in range(16))
    with pytest.raises(IntegrityError, match='quota'):
        with db[1].begin() as session: session.add(artifact(version_id, 17))


@pytest.mark.parametrize('table', ['investigations', 'investigation_versions', 'investigation_artifacts'])
@pytest.mark.parametrize('operation', ['UPDATE', 'DELETE'])
def test_immutable_sql_history(db, table, operation):
    _, version_id = schema_version(db)
    with db[1].begin() as session: session.add(artifact(version_id, 0))
    sql = f'DELETE FROM {table}' if operation == 'DELETE' else f'UPDATE {table} SET id=id'
    with pytest.raises(IntegrityError, match='Immutable'):
        with db[0].begin() as connection: connection.execute(sa.text(sql))


def test_downgrade_retains_history(db):
    schema_version(db)
    config = Config('alembic.ini')
    with pytest.raises(ValueError, match='retained investigation'):
        with db[0].begin() as connection:
            config.attributes['connection'] = connection
            command.downgrade(config, '10u2cleanup')


def test_version_cannot_change_project_anchor(db):
    investigation_id, _ = schema_version(db)
    with pytest.raises(IntegrityError):
        with db[1].begin() as session:
            session.add(InvestigationVersion(investigation_id=investigation_id, project_id='fifo_overflow', ordinal=2,
                admission_key='wrong-project', schema_version='schema-test', revision_id='schema-test',
                revision_hash='b' * 64, manifest_hash='c' * 64, content_hash='d' * 64, content=b'{}'))


def test_postgres_scaffold_emits_matching_tables_and_ordered_index(monkeypatch):
    spec = importlib.util.spec_from_file_location('u9_migration', Path('migrations/versions/11u9investigations_durable_investigations.py'))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    metadata = sa.MetaData()
    for name in ('projects', 'campaigns', 'attempts', 'experiments'):
        Base.metadata.tables[name].to_metadata(metadata)
    events, tables, statements = [], [], []
    def create_table(name, *args):
        events.append(name)
        tables.append(sa.Table(name, metadata, *args))
    monkeypatch.setattr(module, 'op', SimpleNamespace(create_index=lambda *args, **kwargs: events.append('index'),
        create_table=create_table, get_bind=lambda: SimpleNamespace(dialect=SimpleNamespace(name='postgresql')),
        execute=lambda sql: statements.append(str(sql))))
    module.upgrade()
    assert events[0] == 'index'
    normalize = lambda ddl: {line.strip().rstrip(',') for line in ddl.splitlines()}
    for table in tables:
        assert normalize(str(CreateTable(table).compile(dialect=postgresql.dialect()))) == normalize(str(CreateTable(Base.metadata.tables[table.name]).compile(dialect=postgresql.dialect())))
    assert len(tables) == 4
    assert sum('CREATE TRIGGER' in sql for sql in statements) == 5
    assert any('FOR UPDATE' in sql and '16777216' in sql and '256' in sql for sql in statements)


def test_admission_restart_exact_download_and_version_append(db):
    bundle = valid_bundle()
    first = persist(db, bundle)
    assert persist(db, bundle) == first
    with db[1]() as session:
        detail = service.version_view(session, first['investigation_id'], first['id'])
        assert detail['execution_status'] == 'not_run' and detail['scope'] == 'recorded_import'
        assert detail['project_revision']['configuration_hash'] == hashlib.sha256(b'{"scope":"synthetic recorded engineering sources"}').hexdigest()
        assert 'PRIVATE_SOURCE_ONLY' not in json.dumps(detail, default=str)
        listed = service.artifact_list(session, first['investigation_id'], first['id'])['items']
        expected = {(blob.reference.artifact_id, blob.reference.revision_id, blob.reference.attempt_id): blob.data for blob in bundle[1]}
        for item in listed:
            data, digest = service.artifact_download(session, first['investigation_id'], first['id'], item['id'])
            assert data == expected[(item['artifact_id'], item['revision_id'], item['attempt_id'])]
            assert hashlib.sha256(data).hexdigest() == digest
    second = persist(db, valid_bundle(revision='engineering-2'), key='second', investigation_id=first['investigation_id'])
    assert second['ordinal'] == 2 and second['content_hash'] != first['content_hash']
    with db[1]() as session:
        assert service.investigation_view(session, first['investigation_id'])['latest_version']['id'] == second['id']
        page = service.list_versions(session, first['investigation_id'], limit=1)
        assert page['next_cursor'] == 1
        assert service.list_versions(session, first['investigation_id'], after=1)['items'][0]['id'] == second['id']


def test_idempotency_changed_content_conflicts(db):
    first = persist(db)
    with pytest.raises(campaigns.Conflict): persist(db, valid_bundle(revision='changed'))
    with pytest.raises(campaigns.Conflict): persist(db, valid_bundle(revision='changed'), investigation_id=first['investigation_id'])


def test_readmission_rejects_hacked_bytes_and_untrusted_authority_before_publication(db):
    record, blobs, authority = valid_bundle()
    with db[1].begin() as session:
        with pytest.raises(ValueError):
            service.persist_investigation(session, record, blobs, authority={}, idempotency_key='bad', at=AT)
        forged = blobs[0].model_copy(update={'data': b'changed'})
        with pytest.raises(ValueError):
            service.persist_investigation(session, record, (forged,) + blobs[1:], authority=authority, idempotency_key='bad', at=AT)
        assert session.scalar(sa.select(sa.func.count()).select_from(Investigation)) == 0


def test_concurrent_same_key_and_atomic_rollback(db):
    with ThreadPoolExecutor(max_workers=3) as pool:
        rows = list(pool.map(lambda _: persist(db), range(3)))
    assert len({row['id'] for row in rows}) == 1
    with pytest.raises(RuntimeError):
        with db[1].begin() as session:
            record, blobs, authority = valid_bundle(revision='rollback')
            service.persist_investigation(session, record, blobs, authority=authority, idempotency_key='rollback', at=AT, investigation_id=rows[0]['investigation_id'])
            raise RuntimeError('Crash before commit')
    with db[1]() as session:
        assert session.scalar(sa.select(sa.func.count()).select_from(InvestigationVersion)) == 1


def test_db_context_separates_native_ids_without_claiming_execution_origin(db):
    bundle, native = execution_bundle(db)
    stored = persist(db, bundle)
    binding = bundle[0].attempts[0]
    with db[1]() as session:
        detail = service.version_view(session, stored['investigation_id'], stored['id'])
        assert detail['execution_contexts'] == [{'binding': binding.model_dump(mode='json'), 'origin_at_admission': 'context_only'}]
        assert session.get(Attempt, binding.db_attempt_id).result['attempt_id'] == native.attempt_id != binding.db_attempt_id
        link = session.scalar(sa.select(InvestigationExecutionLink))
        assert link.experiment_id == binding.experiment_record_id != binding.wire_experiment_id
        assert link.attempt_id == binding.db_attempt_id
        assert session.scalar(sa.select(sa.func.count()).select_from(Finding)) == 0


@pytest.mark.parametrize('field,value', [('db_attempt_id', 'unrelated'), ('experiment_record_id', 'unrelated'), ('campaign_id', 'unrelated')])
def test_invalid_db_context_cannot_publish_import(db, field, value):
    bundle, _ = execution_bundle(db)
    binding = bundle[0].attempts[0].model_copy(update={field: value})
    record = bundle[0].model_copy(update={'attempts': (binding,)})
    authority = records.create_admission_authority(project_id=record.project_id, revision_id=record.revision_id,
        project_revision_ref=record.project_revision_ref, manifest_ref=record.manifest_ref, bridge_report_ref=record.bridge_report_ref, attempts=record.attempts)
    with pytest.raises((ValueError, campaigns.Conflict, campaigns.NotFound)):
        persist(db, (record, bundle[1], authority))
    with db[1]() as session:
        assert session.scalar(sa.select(sa.func.count()).select_from(Investigation)) == 0


def test_cross_project_db_context_and_stale_finalized_result_rejected(db):
    bundle, _ = execution_bundle(db)
    binding = bundle[0].attempts[0]
    # Valid pure graph associated with another owned curated project is still
    # unable to claim a DB campaign from fifo_correct.
    wrong = valid_bundle(project='fifo_reset_bug', attempts=(binding,), extra=tuple(blob for blob in bundle[1] if blob.reference == binding.result_ref))
    with pytest.raises(campaigns.Conflict): persist(db, wrong)
    stored = persist(db, bundle)
    with db[1].begin() as session:
        attempt = session.get(Attempt, binding.db_attempt_id)
        attempt.result = {**attempt.result, 'uncertainties': ['Changed externally']}
    with db[1]() as session:
        with pytest.raises(campaigns.Conflict): service.version_view(session, stored['investigation_id'], stored['id'])


def test_orm_immutable_import_and_owned_reads(db):
    stored = persist(db)
    with pytest.raises(ValueError):
        with db[1].begin() as session: session.get(InvestigationVersion, stored['id']).content = b'changed'
    with db[1].begin() as session: session.get(Project, 'fifo_correct').owner = 'foreign'
    with db[1]() as session:
        with pytest.raises(campaigns.NotFound): service.version_view(session, stored['investigation_id'], stored['id'])


@pytest.mark.parametrize('exact_bytes', [True, False])
def test_origin_requires_exact_retained_result_bytes_not_just_equivalent_json(db, tmp_path, exact_bytes):
    bundle, native = execution_bundle(db)
    binding = bundle[0].attempts[0]
    directory = tmp_path / binding.db_attempt_id
    directory.mkdir()
    with db[1]() as session:
        experiment = resolve_attempt_experiment(session, session.get(Attempt, binding.db_attempt_id))
        (directory / 'experiment.json').write_bytes(json.dumps(experiment.plan).encode())
    # Both files contain the same typed synthetic result, but only one matches
    # the exact admitted native byte identity. Neither demonstrates tool use.
    (directory / 'result.json').write_bytes(encode(native) if exact_bytes else native.model_dump_json().encode())
    stored = persist(db, bundle, artifact_root=tmp_path)
    with db[1]() as session:
        detail = service.version_view(session, stored['investigation_id'], stored['id'])
        expected = 'retained_execution_bytes' if exact_bytes else 'context_only'
        assert detail['execution_contexts'][0]['origin_at_admission'] == expected
        assert detail['execution_status'] == 'not_run'


def test_execution_link_composite_fk_rejects_cross_project_even_raw_sql(db):
    bundle, _ = execution_bundle(db)
    binding = bundle[0].attempts[0]
    foreign = persist(db, valid_bundle(project='fifo_reset_bug'))
    with pytest.raises(IntegrityError):
        with db[1].begin() as session:
            session.add(InvestigationExecutionLink(version_id=foreign['id'], project_id='fifo_reset_bug', campaign_id=binding.campaign_id,
                attempt_id=binding.db_attempt_id, experiment_id=binding.experiment_record_id, result_hash='a' * 64,
                association_hash='b' * 64, content=b'{}'))


def test_no_history_empty_migration_roundtrip(db):
    config = Config('alembic.ini')
    with db[0].begin() as connection:
        config.attributes['connection'] = connection
        command.downgrade(config, '10u2cleanup')
        command.upgrade(config, 'head')
    assert persist(db)['ordinal'] == 1
