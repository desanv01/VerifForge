"""Production compatibility composition; fake legacy storage is not native proof.

The synthetic old-proof routing fixture deliberately uses a trusted backend
double. Exact legacy checker execution remains separate accepted/held evidence.
"""
from contextlib import contextmanager
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import runpy
import shutil
import socket
import subprocess
from types import ModuleType, SimpleNamespace

import pytest
import sqlalchemy as sa

from veriforge.core import investigations as store, native_validator_policy as policy, service
from veriforge.core.database import Base, Investigation, InvestigationVersion, InvestigationArtifact, make_engine, session_factory
from veriforge.core.engineering_catalog import EngineeringProjectSpec, create_engineering_project
from veriforge.system_investigation import native_tool_import as importer, records

ROOT=Path(__file__).resolve().parents[2]
PURE=runpy.run_path(str(Path(__file__).with_name('test_native_tool_records.py')))
AT=datetime(2026,11,1,tzinfo=timezone.utc)
sha=lambda raw:hashlib.sha256(raw).hexdigest()

@pytest.fixture(autouse=True)
def isolated(monkeypatch):
    policy.configure_registered_validator(None)
    monkeypatch.setattr(subprocess,'run',lambda *a,**k:pytest.fail('No child/tool validation authorized'))
    monkeypatch.setattr(subprocess,'Popen',lambda *a,**k:pytest.fail('No process authorized'))
    monkeypatch.setattr(socket,'create_connection',lambda *a,**k:pytest.fail('No network authorized'))
    yield
    policy.configure_registered_validator(None)

@pytest.fixture
def old_rows(tmp_path):
    record,artifacts,authority=PURE['bundle'](PURE['synthetic_riscv']())
    admitted=records.admit_investigation(record,artifacts,authority=authority)
    envelope=admitted.envelope.model_copy(update={'validation':admitted.envelope.validation.model_copy(update={'implementation_hash':policy.LEGACY_HASH})})
    raw=store._encode(envelope.model_dump(mode='json'))
    engine=make_engine('sqlite:///'+(tmp_path/'routing-fixture.db').as_posix())
    Base.metadata.create_all(engine)
    sessions=session_factory(engine)
    with sessions.begin() as session:
        create_engineering_project(session,EngineeringProjectSpec(project_id=record.project_id,name='Synthetic routing fixture',domains=('riscv',)))
        parent=Investigation(project_id=record.project_id,create_key='old-key',request_hash=sha(raw),created_at=AT)
        session.add(parent);session.flush()
        row=InvestigationVersion(investigation_id=parent.id,project_id=record.project_id,ordinal=1,admission_key='old-key',
            schema_version=envelope.schema_version,revision_id=record.revision_id,revision_hash=record.project_revision_ref.sha256,
            manifest_hash=record.manifest_ref.sha256,content_hash=sha(raw),content=raw,created_at=AT)
        session.add(row);session.flush()
        metadata={store._key(item.reference):item for item in envelope.artifacts}
        for item in artifacts:
            ref=item.reference
            session.add(InvestigationArtifact(version_id=row.id,artifact_id=ref.artifact_id,native_revision_id=ref.revision_id,
                native_attempt_id=ref.attempt_id,identity_key=store._identity(ref),sha256=ref.sha256,size_bytes=len(item.data),
                role=','.join(metadata[store._key(ref)].roles),media_type='application/octet-stream',data=item.data))
        ids=parent.id,row.id
    yield SimpleNamespace(sessions=sessions,ids=ids,raw=raw,envelope=envelope,admitted=admitted,artifacts=artifacts)
    engine.dispose()

def state(session):
    return {table.name:[tuple(row) for row in session.execute(sa.select(table).order_by(*table.primary_key.columns))]
            for table in Base.metadata.sorted_tables}

def backend_double(tmp_path,old_rows):
    module=ModuleType('synthetic_registered_backend')
    source=tmp_path/'backend-source-identity.py'
    source.write_bytes(b'# synthetic host registration identity; no wheel/native code\n')
    module.__file__=str(source)
    for name in ('LEGACY_HASH','CURRENT_HASH','REPORT_KINDS','MAX_NATIVE_PAGE','MAX_OPERATION_BYTES','OPERATION_SECONDS'):
        setattr(module,name,getattr(policy,name))
    module.PolicyUnavailable=type('FakeUnavailable',(ValueError,),{})
    module.LegacyReadView=SimpleNamespace
    module._verify_sources=lambda:None
    module.calls=0
    module.operations=0
    module.keys=set()
    @contextmanager
    def operation():
        module.operations+=1;module.keys=set()
        try: yield
        finally: module.keys=set()
    def read(raw,artifacts):
        if raw!=old_rows.raw or len(artifacts)!=len(old_rows.artifacts): raise ValueError('Exact fixture differs')
        for item in artifacts:
            if sha(item.data)!=item.reference.sha256: raise ValueError('Fresh artifact bytes differ')
        if sha(raw) not in module.keys: module.calls+=1;module.keys.add(sha(raw))
        return module.LegacyReadView(envelope=old_rows.envelope,project_revision=old_rows.admitted.project_revision,
            manifest=old_rows.admitted.manifest,native_report=old_rows.admitted.native_report)
    module.validation_operation=operation
    module.select_route=lambda envelope:'legacy' if envelope.validation.implementation_hash==policy.LEGACY_HASH else 'current'
    module.read_legacy=read
    module.queue_legacy=lambda raw,artifacts,**kw:read(raw,artifacts)
    module.flush_legacy=lambda:None
    return module,source

def test_old_persisted_bytes_are_unavailable_without_registration_and_unchanged(old_rows):
    with old_rows.sessions() as session:
        before=state(session)
        with pytest.raises(service.Conflict): store.version_view(session,*old_rows.ids)
        assert state(session)==before
    assert policy._REGISTERED is None

def test_registered_backend_reads_and_exact_retry_preserve_proofs_blobs_and_rows(tmp_path,old_rows):
    module,source=backend_double(tmp_path,old_rows)
    policy.configure_registered_validator(module,source_sha256=sha(source.read_bytes()))
    with old_rows.sessions() as session:
        before=state(session)
        detail=store.version_view(session,*old_rows.ids)
        assert detail['envelope']['validation']['implementation_hash']==policy.LEGACY_HASH
        assert store._encode(detail['envelope'])==old_rows.raw
        retried=store.retry_legacy_admission(session,envelope_bytes=old_rows.raw,artifacts=old_rows.artifacts,idempotency_key='old-key')
        assert retried['id']==old_rows.ids[1] and state(session)==before
        with pytest.raises(service.Conflict):
            store.retry_legacy_admission(session,envelope_bytes=old_rows.raw,artifacts=old_rows.artifacts,idempotency_key='new-key')
        assert state(session)==before
    assert module.operations>=2

@pytest.mark.parametrize('damage',['owner','blob'])
def test_registered_read_keeps_fresh_owner_and_blob_rejection(tmp_path,old_rows,damage):
    module,source=backend_double(tmp_path,old_rows)
    policy.configure_registered_validator(module,source_sha256=sha(source.read_bytes()))
    with old_rows.sessions.begin() as session:
        if damage=='owner': session.execute(sa.update(Investigation).where(Investigation.id==old_rows.ids[0]).values(owner='foreign'))
        else:
            blob=session.scalar(sa.select(InvestigationArtifact).where(InvestigationArtifact.version_id==old_rows.ids[1]))
            session.execute(sa.update(InvestigationArtifact).where(InvestigationArtifact.id==blob.id).values(data=bytes([blob.data[0]^1])+blob.data[1:]))
    with old_rows.sessions() as session:
        with pytest.raises(service.NotFound if damage=='owner' else service.Conflict):
            store.projection_inputs(session,*old_rows.ids)

def test_registration_capture_and_source_end_guard_remain_operation_local(tmp_path,old_rows):
    module,source=backend_double(tmp_path,old_rows)
    policy.configure_registered_validator(module,source_sha256=sha(source.read_bytes()))
    with policy.validation_operation():
        policy.read_legacy(old_rows.raw,old_rows.artifacts)
        policy.configure_registered_validator(None)
        policy.read_legacy(old_rows.raw,old_rows.artifacts)
    assert module.calls==1
    with pytest.raises(policy.PolicyUnavailable): policy.read_legacy(old_rows.raw,old_rows.artifacts)
    policy.configure_registered_validator(module,source_sha256=sha(source.read_bytes()))
    with pytest.raises(policy.PolicyUnavailable):
        with policy.validation_operation(): source.write_bytes(b'# changed test source\n')

def test_registered_policy_requires_exact_source_and_supported_caps(tmp_path,old_rows):
    module,source=backend_double(tmp_path,old_rows)
    with pytest.raises(policy.PolicyUnavailable): policy.configure_registered_validator(module,source_sha256='0'*64)
    module.MAX_NATIVE_PAGE=50
    with pytest.raises(ValueError): policy.configure_registered_validator(module,source_sha256=sha(source.read_bytes()))
    assert policy._REGISTERED is None

def test_four_case_retained_import_current_read_and_separate_old_history(tmp_path,old_rows):
    source=ROOT/'.artifacts/u4-riscv/ci-41202693632/riscv-evidence-c14525e3791569215ba385a1c5582d52d0b8858f-41202693632-1'
    if not source.is_dir(): pytest.skip('Retained four-case bundle unavailable; no native execution fabricated')
    before={p.relative_to(source).as_posix():sha(p.read_bytes()) for p in source.rglob('*') if p.is_file()}
    copied=tmp_path/'recorded-four-case'
    shutil.copytree(source,copied)
    assert before=={p.relative_to(copied).as_posix():sha(p.read_bytes()) for p in copied.rglob('*') if p.is_file()}
    assert before=={p.relative_to(source).as_posix():sha(p.read_bytes()) for p in source.rglob('*') if p.is_file()}
    prepared=importer.prepare_native_bundle(family='riscv-41202693632',bundle_root=copied,
        project_id=old_rows.envelope.record.project_id,revision_id='fourcase-working-source')
    report=prepared.admitted.native_report
    saved=next(case for case in report.cases if case.locator.case_label=='control-saved-replay')
    assert report.schema_version=='riscv.recorded-report.v2' and len(report.cases)==4
    assert saved.inputs.saved_program is not None and saved.producer_phases==('reference','dut')
    with old_rows.sessions.begin() as session:
        old=state(session)
        result=store.persist_investigation(session,prepared.admitted.envelope.record,prepared.admitted.artifacts,
            authority=prepared.authority,idempotency_key='fourcase-key',at=AT)
        summary,admitted,blobs=store.projection_inputs(session,result['investigation_id'],result['id'])
        assert summary['report_kind']=='riscv.recorded-report.v2' and admitted.envelope.validation.version=='2'
        assert len(blobs)==len(prepared.admitted.artifacts)
        assert session.get(InvestigationVersion,old_rows.ids[1]).content==old_rows.raw
        with pytest.raises(service.Conflict,match='report family changed'):
            store.persist_investigation(session,prepared.admitted.envelope.record,prepared.admitted.artifacts,
                authority=prepared.authority,idempotency_key='cannot-relabel-history',at=AT,investigation_id=old_rows.ids[0])
        assert [row for row in old['investigation_versions'] if row[0]==old_rows.ids[1]]
