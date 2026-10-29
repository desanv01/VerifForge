"""Temporary SQLite and synthetic native closure; no native/provider execution."""
from datetime import datetime, timezone
from hashlib import sha256
from pathlib import Path
import runpy

import pytest
import sqlalchemy as sa

from veriforge.core import investigations as store, service
from veriforge.core.database import (Base, Project, Investigation, InvestigationVersion,
    InvestigationArtifact, InvestigationExecutionLink, Campaign, Attempt, ExperimentRecord,
    Dispatch, Finding, make_engine, session_factory)
from veriforge.core.engineering_catalog import EngineeringProjectSpec, create_engineering_project
from veriforge.system_investigation import native_tool_records as native

ROOT = Path(__file__).resolve().parents[2]
PURE = runpy.run_path(str(ROOT / 'tests/system_investigation/test_native_tool_records.py'))
LEGACY = runpy.run_path(str(ROOT / 'tests/core/test_investigations.py'))
AT = datetime(2026, 10, 30, tzinfo=timezone.utc)


@pytest.fixture
def db(tmp_path):
    engine = make_engine('sqlite:///' + (tmp_path / 'native.db').as_posix())
    Base.metadata.create_all(engine)
    sessions = session_factory(engine)
    with sessions.begin() as session:
        create_engineering_project(session, EngineeringProjectSpec(project_id='recorded-project',
            name='Synthetic native evidence', domains=('riscv', 'firmware')))
    yield engine, sessions
    engine.dispose()


def native_bundle(kind='riscv', tmp_path=None):
    raw = PURE['synthetic_riscv']() if kind == 'riscv' else PURE['synthetic_firmware'](tmp_path)
    return PURE['bundle'](raw, kind)


def persist(db, bundle=None, key='native', **kwargs):
    record, artifacts, authority = bundle or native_bundle()
    with db[1].begin() as session:
        return store.persist_investigation(session, record, artifacts, authority=authority,
            idempotency_key=key, at=AT, **kwargs)


def counts(session):
    return [session.scalar(sa.select(sa.func.count()).select_from(model)) for model in
            (Campaign, Attempt, ExperimentRecord, Dispatch, Finding, InvestigationExecutionLink)]


@pytest.mark.parametrize('kind', ['riscv', 'firmware'])
def test_both_families_persist_restart_exact_bytes_and_no_execution(db, tmp_path, monkeypatch, kind):
    bundle = native_bundle(kind, tmp_path)
    monkeypatch.setattr(store, '_db_context', lambda *a: pytest.fail('local attempt association'))
    monkeypatch.setattr(store, '_origin', lambda *a, **k: pytest.fail('local execution origin'))
    with db[1]() as session:
        before = counts(session)
    row = persist(db, bundle)
    assert row['report_kind'] == kind + '.recorded-report.v1'
    assert row['execution_status'] == 'not_run' and row['schema_version'] == 'engineering.admission.v2'
    db[0].dispose()  # read through a new connection; no process-global admission cache
    with db[1]() as session:
        assert counts(session) == before
        detail = store.version_view(session, row['investigation_id'], row['id'])
        assert 'native_report' in detail and 'bridge_report' not in detail and 'execution_contexts' not in detail
        assert detail['project_revision']['revision_id'] == bundle[0].revision_id
        assert detail['envelope']['validation']['implementation_hash'] == sha256(Path(native.__file__).read_bytes()).hexdigest()
        listed = store.artifact_list(session, row['investigation_id'], row['id'])['items']
        originals = {store._key(item.reference): item.data for item in bundle[1]}
        stored = session.scalars(sa.select(InvestigationArtifact).where(InvestigationArtifact.version_id == row['id'])).all()
        assert {(item.artifact_id, item.native_revision_id, item.native_attempt_id): item.data for item in stored} == originals
        for item in (listed[0], listed[-1]):
            data, digest = store.artifact_download(session, row['investigation_id'], row['id'], item['id'])
            assert data == originals[(item['artifact_id'], item['revision_id'], item['attempt_id'])]
            assert digest == sha256(data).hexdigest()
        if kind == 'firmware':
            assert all(item['attempt_id'] is None for item in listed)
        else:
            assert any(item['attempt_id'] for item in listed)


def test_append_repeat_idempotency_and_outer_rollback(db):
    bundle = native_bundle()
    first = persist(db, bundle)
    assert persist(db, bundle) == first
    with db[1]() as session:
        original = session.get(InvestigationVersion, first['id']).content
    second = persist(db, bundle, key='second', investigation_id=first['investigation_id'])
    assert second['ordinal'] == 2
    assert persist(db, bundle, key='second', investigation_id=first['investigation_id']) == second
    with db[1]() as session:
        assert session.get(InvestigationVersion, first['id']).content == original
    with pytest.raises(RuntimeError), db[1].begin() as session:
        record, artifacts, authority = bundle
        store.persist_investigation(session, record, artifacts, authority=authority, idempotency_key='rollback', at=AT)
        raise RuntimeError('outer rollback')
    with db[1]() as session:
        assert session.scalar(sa.select(sa.func.count()).select_from(Investigation)) == 1


def test_same_key_changed_content_and_original_revision_authority_reject(db):
    record, artifacts, authority = native_bundle()
    persist(db, (record, artifacts, authority))
    changed = record.model_copy(update={'investigation_id': 'another-native-record'})
    with pytest.raises(service.Conflict):
        persist(db, (changed, artifacts, authority))
    changed = record.model_copy(update={'revision_id': 'substituted-latest-revision'})
    with pytest.raises(ValueError, match='host-pinned'):
        persist(db, (changed, artifacts, authority), key='bad-revision')


def test_append_new_revision_preserves_original_anchor(db):
    from veriforge.contracts.engineering import SystemManifest
    from veriforge.system_investigation import records
    record, artifacts, authority = original = native_bundle()
    first = persist(db, original)
    by_key = {store._key(item.reference): item for item in artifacts}
    def replace(reference, value):
        raw = store._encode(value.model_dump(mode='json'))
        updated = reference.model_copy(update={'sha256': sha256(raw).hexdigest()})
        by_key[store._key(reference)] = records.ArtifactBytes(reference=updated, data=raw)
        return updated
    revision = records.EngineeringProjectRevision.model_validate_json(by_key[store._key(record.project_revision_ref)].data)
    manifest = SystemManifest.model_validate_json(by_key[store._key(record.manifest_ref)].data)
    report = store._REPORT.validate_json(by_key[store._key(record.report_ref)].data)
    revision_id = 'new-associated-revision'
    project_ref = replace(record.project_revision_ref, revision.model_copy(update={'revision_id':revision_id}))
    manifest_ref = replace(record.manifest_ref, manifest.model_copy(update={'revision_id':revision_id}))
    selection = report.selection.model_copy(update={'selected':tuple(
        binding.model_copy(update={'ref':manifest_ref, 'size_bytes':len(by_key[store._key(manifest_ref)].data)})
        if binding.ref == record.manifest_ref else binding for binding in report.selection.selected)})
    report_ref = replace(record.report_ref, report.model_copy(update={'manifest_ref':manifest_ref, 'selection':selection}))
    updated = record.model_copy(update={'revision_id':revision_id, 'project_revision_ref':project_ref,
                                        'manifest_ref':manifest_ref, 'report_ref':report_ref})
    pins = authority.pins
    authority = native.create_external_admission_authority(project_id=record.project_id, revision_id=revision_id,
        project_revision_ref=project_ref, manifest_ref=manifest_ref, report_kind=record.report_kind,
        report_ref=report_ref, locators=pins.locators, validator_implementation_hash=pins.validator_implementation_hash)
    second = persist(db, (updated, tuple(by_key.values()), authority), key='new-revision', investigation_id=first['investigation_id'])
    with db[1]() as session:
        old = store.version_view(session, first['investigation_id'], first['id'])
        new = store.version_view(session, second['investigation_id'], second['id'])
        assert old['project_revision']['revision_id'] == record.revision_id
        assert old['revision_hash'] == record.project_revision_ref.sha256
        assert new['project_revision']['revision_id'] == revision_id
        assert new['revision_hash'] == project_ref.sha256 != old['revision_hash']


@pytest.mark.parametrize('mix', ['family', 'schema'])
def test_homogeneous_history(db, tmp_path, mix):
    row = persist(db)
    bundle = native_bundle('firmware', tmp_path) if mix == 'family' else LEGACY['valid_bundle'](project='recorded-project')
    with pytest.raises(service.Conflict, match='family'):
        persist(db, bundle, key='mixed', investigation_id=row['investigation_id'])


@pytest.mark.parametrize('failure', ['changed', 'unreadable'])
def test_fresh_installed_identity_on_admission_and_every_read(db, monkeypatch, failure):
    bundle = native_bundle()
    row = persist(db, bundle)
    with db[1]() as session:
        original = session.get(InvestigationVersion, row['id']).content
    if failure == 'changed':
        monkeypatch.setattr(store, '_installed_native_validator_hash', lambda: '0' * 64)
    else:
        monkeypatch.setattr(native, '__file__', str(ROOT / 'missing-fixed-validator.py'))
    for reader in (
        lambda s: store.version_view(s, row['investigation_id'], row['id']),
        lambda s: store.artifact_list(s, row['investigation_id'], row['id']),
        lambda s: store.list_investigations(s, 'recorded-project', admission_schema='v2'),
        lambda s: store.investigation_view(s, row['investigation_id'], admission_schema='all'),
        lambda s: store.list_versions(s, row['investigation_id'], admission_schema='v2'),
    ):
        with db[1]() as session, pytest.raises(service.Conflict):
            reader(session)
    with pytest.raises((service.Conflict, ValueError)):
        persist(db, bundle, key='unavailable-validator')
    with db[1]() as session:
        assert session.get(InvestigationVersion, row['id']).content == original


@pytest.mark.parametrize('field,value', [('data', b'x'), ('role', 'native_report'),
    ('identity_key', '0'*64), ('sha256', '0'*64), ('media_type', 'text/plain')])
def test_stored_artifact_corruption_rejected_in_summary_and_detail(db, field, value):
    row = persist(db)
    with db[1].begin() as session:
        blob = session.scalar(sa.select(InvestigationArtifact).where(InvestigationArtifact.version_id == row['id']))
        updates = {field: value}
        if field == 'data':
            updates['size_bytes'] = len(value)
        session.execute(sa.update(InvestigationArtifact).where(InvestigationArtifact.id == blob.id).values(**updates))
    with db[1]() as session, pytest.raises(service.Conflict):
        store.version_view(session, row['investigation_id'], row['id'])
    with db[1]() as session, pytest.raises(service.Conflict):
        store.list_versions(session, row['investigation_id'], admission_schema='all')


def test_native_id_collision_does_not_query_local_attempt(db, monkeypatch):
    from sqlalchemy.orm import Session
    bundle = native_bundle()
    # Plant a real local row with the producer string using existing v1 seed helper.
    from veriforge.core.catalog import seed_catalog
    from veriforge.contracts import Experiment
    from veriforge.core import service as campaigns
    with db[1].begin() as session:
        seed_catalog(session)
        campaign = campaigns.create_campaign(session, 'fifo_correct', Experiment(experiment_id='collision',
            fixture_id='fifo_correct', objective_ids=('FIFO_BEHAVIOR',), operations=({'op':'reset'},)).model_dump(mode='json'), 'collision')
        campaigns.start(session, campaign.id)
        session.add(Attempt(id=PURE['RV_IDS']['control'][0], campaign_id=campaign.id))
    real_get = Session.get
    def guarded_get(self, entity, *args, **kwargs):
        assert entity is not Attempt, 'native lookup reached local Attempt'
        return real_get(self, entity, *args, **kwargs)
    monkeypatch.setattr(Session, 'get', guarded_get)
    row = persist(db, bundle)
    with db[1]() as session:
        store.version_view(session, row['investigation_id'], row['id'])
        assert session.scalar(sa.select(sa.func.count()).select_from(InvestigationExecutionLink)) == 0


def test_planted_execution_link_rejected(db):
    # A coherent local v1 context is still forbidden on a native v2 version.
    from veriforge.core.catalog import seed_catalog
    with db[1].begin() as session:
        seed_catalog(session)
    legacy_bundle, _ = LEGACY['execution_bundle'](db)
    legacy = persist(db, legacy_bundle, key='legacy')
    row = persist(db)
    with db[1].begin() as session:
        link = session.scalar(sa.select(InvestigationExecutionLink).where(InvestigationExecutionLink.version_id == legacy['id']))
        # Deliberate corruption injection; FK enforcement is outside this negative fixture.
        session.connection().exec_driver_sql('PRAGMA foreign_keys=OFF')
        session.execute(sa.insert(InvestigationExecutionLink).values(version_id=row['id'], project_id='recorded-project',
            campaign_id=link.campaign_id, attempt_id=link.attempt_id, experiment_id=link.experiment_id,
            result_hash=link.result_hash, association_hash=link.association_hash, content=link.content))
    with db[1]() as session, pytest.raises(service.Conflict):
        store.version_view(session, row['investigation_id'], row['id'])


@pytest.mark.parametrize('change', ['foreign', 'rtl', 'metadata'])
def test_canonical_project_fences(db, change):
    with db[1].begin() as session:
        values = {'owner':'foreign'} if change == 'foreign' else ({'kind':'rtl_fixture', 'contract_id':'contract'} if change == 'rtl' else {'data':{'name':'broken'}})
        if change == 'rtl':
            from veriforge.core.database import Contract
            session.add(Contract(id='contract', data={}, identity={}))
            session.flush()
        session.execute(sa.update(Project).where(Project.id == 'recorded-project').values(**values))
    with pytest.raises((service.NotFound, service.Conflict)):
        persist(db)


def test_filter_before_limit_and_latest_eligible(db):
    v1 = persist(db, LEGACY['valid_bundle'](project='recorded-project'), key='v1')
    rows = [persist(db, key=f'v2-{i}') for i in range(3)]
    with db[1]() as session:
        assert [x['id'] for x in store.list_investigations(session, 'recorded-project')['items']] == [v1['investigation_id']]
        result, cursor = [], ''
        while True:
            page = store.list_investigations(session, 'recorded-project', limit=1, after=cursor, admission_schema='v2')
            result.extend(x['id'] for x in page['items'])
            cursor = page['next_cursor']
            if cursor is None:
                break
        assert result == sorted(x['investigation_id'] for x in rows)
        assert len(store.list_investigations(session, 'recorded-project', admission_schema='all')['items']) == 4
        with pytest.raises(service.NotFound):
            store.investigation_view(session, rows[0]['investigation_id'])
        with pytest.raises(ValueError):
            store.list_investigations(session, 'recorded-project', admission_schema='unknown')
