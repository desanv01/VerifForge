"""Independent admission boundary review. All bytes are synthetic; no tools/DB/UI."""
import hashlib
import json

import pytest

from veriforge.contracts import ArtifactReference, AttemptResult, canonical_hash
from veriforge.contracts.engineering import EvidenceReference
from veriforge.system_investigation import AddressBinding, RegisterIntent, SourceArtifact, bundle_files, compare_registers
from veriforge.system_investigation.models import RootArtifact
from veriforge.system_investigation import records as admission

PRIVATE = b'PRIVATE_ADMISSION_REVIEW_BUFFER_39f5'


def raw(model):
    return json.dumps(model.model_dump(mode='json'),sort_keys=True,separators=(',',':'),ensure_ascii=False).encode()


def reference(name,data,revision='external-source-r2',attempt=None):
    return EvidenceReference(artifact_id=name,sha256=hashlib.sha256(data).hexdigest(),revision_id=revision,attempt_id=attempt)


def authority(record):
    return admission.create_admission_authority(project_id=record.project_id,revision_id=record.revision_id,
        project_revision_ref=record.project_revision_ref,manifest_ref=record.manifest_ref,
        bridge_report_ref=record.bridge_report_ref,attempts=record.attempts)


def fixture(*,shared=False,attempt=True):
    """Valid native bridge records from bounded pure parsing, with explicit roots."""
    parent_data=b'// '+PRIVATE+b' shared design input'
    parent=RootArtifact(reference=reference('declared-root',parent_data),data=parent_data,origin='synthetic_fixture')
    header_data=b'#define READY_ADDR 0x40000004u\n'
    rtl_data=b"case (addr)\n8'h04: ready <= write_data;\ndefault: ;\nendcase\n"
    header=SourceArtifact(reference=reference('header',header_data),data=header_data,origin='synthetic_fixture',parents=(parent,))
    rtl=SourceArtifact(reference=reference('decoder',rtl_data),data=rtl_data,origin='synthetic_fixture',parents=(parent,) if shared else ())
    intent=RegisterIntent(byte_base=0x40000000,bindings=(AddressBinding(binding_id='ready',
        header_macro='READY_ADDR',rtl_signal='ready',expected_address=0x40000004),))
    report=compare_registers(header,rtl,intent)
    bundle=bundle_files(report)
    blobs={}
    def add(ref,data):
        blobs[(ref.artifact_id,ref.revision_id,ref.attempt_id)]=admission.ArtifactBytes(reference=ref,data=data)
        return ref
    for source in (header,rtl,parent): add(source.reference,source.data)
    project_sources={}
    attachments=[]
    for row in report.native_records:
        for proof in row.fact.provenance.sources:
            project_sources[(proof.artifact.artifact_id,proof.artifact.revision_id,proof.artifact.attempt_id)]=proof.artifact
            if proof.artifact.artifact_id=='reviewed-intent': add(proof.artifact,raw(intent))
        add(row.fact.native_record,bundle['native/'+row.fact.native_record.artifact_id+'.json'])
        attachments.append(admission.NativeAttachment(fact_id=row.fact.fact_id,domain=row.fact.domain,
            native_schema_id=row.fact.native_schema_id,reference=row.fact.native_record))
    for relation in report.manifest.relations:
        for ref in relation.evidence: add(ref,bundle['comparisons/'+ref.artifact_id+'.json'])
    config=b'{"association":"synthetic files only"}'
    config_ref=add(reference('configuration',config),config)
    revision=admission.EngineeringProjectRevision(project_id='host-project',revision_id='host-revision-7',
        source_refs=(*project_sources.values(),config_ref),configuration_hash=config_ref.sha256)
    def pretty(model): return model.model_dump_json(indent=2).encode()
    rev_bytes,manifest_bytes,report_bytes=map(pretty,(revision,report.manifest,report))
    rev_ref=add(reference('revision-record',rev_bytes),rev_bytes)
    manifest_ref=add(reference('manifest-record',manifest_bytes),manifest_bytes)
    report_ref=add(reference('bridge-record',report_bytes),report_bytes)
    links=()
    if attempt:
        log=b'synthetic imported diagnostic; no execution occurred'
        log_ref=add(reference('diagnostic',log,'native-result-version','external-run-8'),log)
        result=AttemptResult(attempt_id='external-run-8',experiment_id='native-wire-plan-2',
            execution_status='completed',verdict='timeout',artifacts=(ArtifactReference(artifact_id=log_ref.artifact_id,
                path='logs/run.txt',sha256=log_ref.sha256,size_bytes=len(log),media_type='text/plain'),))
        result_bytes=pretty(result)
        result_ref=add(reference('native-result',result_bytes,'native-result-version',result.attempt_id),result_bytes)
        links=(admission.CampaignAttemptEvidence(campaign_id='db-campaign-1',db_attempt_id='db-row-200',
            experiment_record_id='db-plan-row-700',native_attempt_id=result.attempt_id,
            wire_experiment_id=result.experiment_id,result_ref=result_ref),)
    record=admission.EngineeringInvestigation(investigation_id='association-only',project_id=revision.project_id,
        revision_id=revision.revision_id,project_revision_ref=rev_ref,manifest_ref=manifest_ref,
        bridge_report_ref=report_ref,native_records=tuple(attachments),attempts=links)
    return record,tuple(blobs.values()),authority(record)


def replace(record,blobs,field,data):
    old=getattr(record,field)
    new=reference(old.artifact_id,data,old.revision_id,old.attempt_id)
    updated=record.model_copy(update={field:new})
    return updated,tuple(admission.ArtifactBytes(reference=new,data=data) if b.reference==old else b for b in blobs)


def test_native_roundtrip_keeps_roots_private_bytes_and_namespace_meaning():
    record,blobs,host=fixture(shared=True)
    admitted=admission.admit_investigation(record,blobs,authority=host)
    wire=admission.serialize_investigation(admitted)
    assert PRIVATE not in wire and PRIVATE.decode() not in repr(admitted)
    assert {b.reference:b.data for b in admitted.artifacts}=={b.reference:b.data for b in blobs}
    assert admitted.envelope.operation=='admission_only' and admitted.envelope.new_execution=='not_run'
    assert admitted.envelope.claim_scope=='resolved_bytes_and_typed_consistency_only'
    assert admitted.envelope.attempt_context_scope=='host_pinned_association_not_execution_origin'
    assert admitted.attempt_results[0].attempt_id=='external-run-8'!=record.attempts[0].db_attempt_id
    assert admitted.attempt_results[0].experiment_id=='native-wire-plan-2'!=record.attempts[0].experiment_record_id
    assert admitted.bridge_report.comparisons[0].verdict=='inconclusive'
    assert admitted.bridge_report.manifest.relations[0].dependence=='same_source'
    assert admission.read_investigation(wire,tuple(reversed(blobs)),authority=host).envelope==admitted.envelope
    revision_meta=next(a for a in admitted.envelope.artifacts if 'project_revision' in a.roles)
    assert revision_meta.reference.sha256!=revision_meta.canonical_record_hash
    assert admitted.envelope.record_hash==canonical_hash(record)


@pytest.mark.parametrize('kind',['dict','unsealed-authority','unsealed-admission'])
def test_type_or_constructor_never_supplies_host_authority(kind):
    record,blobs,_=fixture()
    if kind=='unsealed-admission':
        forged=object.__new__(admission.AdmittedInvestigation)
        with pytest.raises(ValueError): admission.serialize_investigation(forged)
    else:
        host={} if kind=='dict' else object.__new__(admission.TrustedAdmissionAuthority)
        with pytest.raises(ValueError): admission.admit_investigation(record,blobs,authority=host)


def test_every_source_ancestor_native_relation_and_attempt_buffer_is_required():
    record,blobs,host=fixture()
    admitted=admission.admit_investigation(record,blobs,authority=host)
    roles={role for row in admitted.envelope.artifacts for role in row.roles}
    assert {'project_revision','manifest','bridge_report','project_source','native_fact',
        'provenance_source','relation_evidence','attempt_result','attempt_artifact'}<=roles
    for index in range(len(blobs)):
        with pytest.raises(ValueError): admission.admit_investigation(record,blobs[:index]+blobs[index+1:],authority=host)


@pytest.mark.parametrize('field',['campaign_id','db_attempt_id','experiment_record_id','native_attempt_id','wire_experiment_id'])
def test_client_cannot_select_another_host_context(field):
    record,blobs,host=fixture()
    changed=record.attempts[0].model_copy(update={field:'unrelated-client-selection'})
    with pytest.raises(ValueError):
        admission.admit_investigation(record.model_copy(update={'attempts':(changed,)}),blobs,authority=host)


@pytest.mark.parametrize('kind',['orphan','duplicate','cross-revision','foreign-attempt'])
def test_identity_closure_uses_full_reference_namespace(kind):
    record,blobs,host=fixture()
    if kind=='orphan':
        data=b'orphan';changed=(*blobs,admission.ArtifactBytes(reference=reference('orphan',data),data=data))
    elif kind=='duplicate': changed=(*blobs,blobs[0])
    else:
        blob=blobs[0]
        ref=blob.reference.model_copy(update={'revision_id':'wrong-revision'} if kind=='cross-revision' else {'attempt_id':'foreign-native-run'})
        changed=(admission.ArtifactBytes(reference=ref,data=blob.data),*blobs[1:])
    with pytest.raises(ValueError): admission.admit_investigation(record,changed,authority=host)


@pytest.mark.parametrize('kind',['duplicate-key','nan','infinity','utf8','deep'])
def test_host_pinned_malformed_json_is_rejected_without_private_payload(kind):
    record,blobs,_=fixture()
    original=next(b.data for b in blobs if b.reference==record.project_revision_ref)
    data={'duplicate-key':b'{"project_id":"'+PRIVATE+b'",'+original[1:],
        'nan':b'{"private":NaN}', 'infinity':b'{"private":Infinity}', 'utf8':b'\xff'+PRIVATE,
        'deep':b'['*1500+b'0'+b']'*1500}[kind]
    record,blobs=replace(record,blobs,'project_revision_ref',data)
    with pytest.raises(ValueError) as failed: admission.admit_investigation(record,blobs,authority=authority(record))
    assert PRIVATE.decode() not in str(failed.value)


def test_rehashed_bridge_source_size_must_match_retained_actual_bytes():
    record,blobs,_=fixture()
    report=json.loads(next(b.data for b in blobs if b.reference==record.bridge_report_ref))
    report['sources'][0]['size_bytes']+=1
    record,blobs=replace(record,blobs,'bridge_report_ref',json.dumps(report).encode())
    with pytest.raises(ValueError): admission.admit_investigation(record,blobs,authority=authority(record))


def test_revalidating_bad_private_buffer_does_not_echo_bytes_in_exception():
    record,blobs,host=fixture()
    tampered=blobs[0].model_copy(update={'data':PRIVATE+b' changed after model validation'})
    with pytest.raises(ValueError) as failed:
        admission.admit_investigation(record,(tampered,*blobs[1:]),authority=host)
    assert PRIVATE.decode() not in str(failed.value)


@pytest.mark.parametrize('provide_evidence',[False,True])
def test_capability_references_are_resolved_without_granting_new_execution(provide_evidence):
    record,blobs,_=fixture()
    evidence=b'synthetic capability note; never execution evidence'
    ref=reference('capability-note',evidence)
    manifest=json.loads(next(b.data for b in blobs if b.reference==record.manifest_ref))
    manifest['adapters']=[dict(identity=dict(schema_version='engineering.adapter.v1',adapter_id='pure-import',
        adapter_version='1',native_schema_id='AddressFact.v1',implementation_hash='1'*64,configuration_hash='2'*64),
        support=[dict(mode=mode,maturity='experimental' if mode=='import' else 'unsupported',
            scope='synthetic declared scope',evidence=[ref.model_dump(mode='json')] if mode=='import' else [])
            for mode in ('import','execute','repair')])]
    report=json.loads(next(b.data for b in blobs if b.reference==record.bridge_report_ref))
    report['manifest']=manifest
    record,blobs=replace(record,blobs,'manifest_ref',json.dumps(manifest).encode())
    record,blobs=replace(record,blobs,'bridge_report_ref',json.dumps(report).encode())
    if provide_evidence:
        blobs=(*blobs,admission.ArtifactBytes(reference=ref,data=evidence))
        admitted=admission.admit_investigation(record,blobs,authority=authority(record))
        assert admitted.envelope.new_execution=='not_run'
        assert next(a for a in admitted.envelope.artifacts if a.reference==ref).roles==('capability_evidence',)
    else:
        with pytest.raises(ValueError): admission.admit_investigation(record,blobs,authority=authority(record))


@pytest.mark.parametrize('claim,value',[('new_execution','completed'),('claim_scope','design_correct'),
    ('attempt_context_scope','local_execution_verified'),('overall_design_verdict','pass')])
def test_persisted_client_claims_never_grant_execution_or_verdict(claim,value):
    record,blobs,host=fixture()
    wire=json.loads(admission.serialize_investigation(admission.admit_investigation(record,blobs,authority=host)))
    wire[claim]=value
    with pytest.raises(ValueError): admission.read_investigation(json.dumps(wire).encode(),blobs,authority=host)


def test_read_always_readmits_bytes_and_metadata():
    record,blobs,host=fixture()
    wire=admission.serialize_investigation(admission.admit_investigation(record,blobs,authority=host))
    with pytest.raises(ValueError): admission.read_investigation(wire,blobs[1:],authority=host)
    body=json.loads(wire);body['artifacts'][0]['roles']=['project_source']
    with pytest.raises(ValueError): admission.read_investigation(json.dumps(body).encode(),blobs,authority=host)


@pytest.mark.parametrize('kind',['count','artifact-bytes','total-bytes','envelope-bytes'])
def test_declared_bounds_reject_before_unbounded_decode(kind):
    record,blobs,host=fixture()
    if kind=='count':
        with pytest.raises(ValueError): admission.admit_investigation(record,(blobs[0],)*257,authority=host)
    elif kind=='artifact-bytes':
        data=b'x'*(admission.MAX_ARTIFACT_BYTES+1)
        with pytest.raises(ValueError): admission.ArtifactBytes(reference=reference('large',data),data=data)
    elif kind=='total-bytes':
        data=b'x'*admission.MAX_ARTIFACT_BYTES
        too_big=tuple(admission.ArtifactBytes(reference=reference('large-'+str(i),data),data=data) for i in range(17))
        with pytest.raises(ValueError): admission.admit_investigation(record,too_big,authority=host)
    else:
        with pytest.raises(ValueError): admission.read_investigation(b' '*(admission.MAX_ENVELOPE_BYTES+1),blobs,authority=host)
