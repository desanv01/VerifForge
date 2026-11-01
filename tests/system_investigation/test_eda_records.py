"""Pure admission over retained public KiCad bytes; no native execution."""
import json
from hashlib import sha256
from pathlib import Path
import pytest
from veriforge.contracts.eda_tools import EdaInvestigation
from veriforge.system_investigation.records import ArtifactBytes
from veriforge.system_investigation.eda_records import authorize_eda,admit_eda,readback_eda,project_eda,encode,EdaAuthority

ROOT=Path(__file__).resolve().parents[2]

def bundle():
    artifacts=[]
    def add(name,raw):
        a=ArtifactBytes(reference=dict(artifact_id=name,revision_id="v1",sha256=sha256(raw).hexdigest()),data=raw)
        artifacts.append(a);return a.reference
    base=ROOT/'targets/kicad'
    xml=add("xml",(ROOT/'tests/kicad/fixtures/carrier-control.netlist.xml').read_bytes())
    pcb=add("pcb",(base/'carrier/control_corrected_v1/carrier.kicad_pcb').read_bytes())
    raw=(base/'evidence/control-corrected-v1-20261029T145057431340Z/reports/erc.json').read_bytes()
    from veriforge.kicad.reports import parse_report
    parsed=parse_report(raw,"erc")
    config=add("config",encode(dict(schema_version="engineering.eda-configuration.v1",domain="kicad",profile="kicad10.configured.v1",ignored_checks={"erc":parsed.ignored_checks},included_severities={"erc":parsed.included_severities})))
    req=add("req",encode(dict(domain="kicad")))
    receipt=add("receipt",b'{"recorded":true}')
    report=add("erc",raw)
    record=EdaInvestigation(investigation_id="retained-kicad",domain="kicad",requirements=req,
        candidate=dict(project=dict(project_id="carrier",revision_id="v1",source_refs=(xml,pcb),configuration_hash=config.sha256),
        sources=[dict(name="schematic.xml",reference=xml),dict(name="board.kicad_pcb",reference=pcb)],configuration=config,receipt=receipt,
        reports=[dict(name="erc.json",reference=report,source_refs=(xml,))]))
    return record,tuple(artifacts)

def test_retained_byte_admission_readback_projection_is_not_execution():
    record,artifacts=bundle();authority=authorize_eda(record,reviewed_by="host-reviewer")
    envelope=admit_eda(record,artifacts,authority=authority)
    assert envelope==readback_eda(encode(envelope),artifacts,authority=authority)
    projected=project_eda(envelope,artifacts,authority=authority)
    assert projected['execution_status']=='not_observed' and projected['design_verdict']=='inconclusive'
    assert projected['projection']['mapping']['gap']=='reviewed_mapping_assertions_missing'
    assert projected['projection']['configured_reports'][0]['kind']=='erc'

@pytest.mark.parametrize('change',['missing','orphan','duplicate','swapped','authority','envelope','checker'])
def test_complete_closure_and_host_only_authority(change):
    record,artifacts=bundle();authority=authorize_eda(record,reviewed_by="host-reviewer")
    envelope=admit_eda(record,artifacts,authority=authority)
    if change=='missing':artifacts=artifacts[:-1]
    elif change=='orphan':artifacts+= (ArtifactBytes(reference=dict(artifact_id='orphan',revision_id='v1',sha256=sha256(b'x').hexdigest()),data=b'x'),)
    elif change=='duplicate':artifacts+=artifacts[:1]
    elif change=='swapped':
        a=artifacts[0];artifacts=(a.model_copy(update={'data':b'bad'}),*artifacts[1:])
    elif change=='authority':authority={"record_hash":envelope.record_hash,"reviewed_by":"client"}
    elif change=='checker':envelope=envelope.model_copy(update={'checker_sha256':'0'*64})
    else:envelope=envelope.model_copy(update={'total_artifact_bytes':0})
    with pytest.raises(ValueError):readback_eda(encode(envelope),artifacts,authority=authority)

def test_changed_record_requires_new_host_review_and_configuration_is_fixed():
    record,artifacts=bundle();authority=authorize_eda(record,reviewed_by="host-reviewer")
    with pytest.raises(ValueError):admit_eda(record.model_copy(update={'investigation_id':'other'}),artifacts,authority=authority)
    with pytest.raises(TypeError):EdaAuthority()
    with pytest.raises(ValueError):EdaInvestigation.model_validate(dict(record.model_dump(mode='json'),new_execution=True))

def test_baseline_association_cannot_change_configuration_or_project():
    record,_=bundle()
    baseline=record.candidate.model_copy(update={'project':record.candidate.project.model_copy(update={'revision_id':'old'})})
    accepted=EdaInvestigation.model_validate(dict(record.model_dump(),baseline=baseline))
    assert accepted.baseline.project.revision_id=='old'
    bad=baseline.model_copy(update={'project':baseline.project.model_copy(update={'project_id':'other'})})
    with pytest.raises(ValueError):EdaInvestigation.model_validate(dict(record.model_dump(),baseline=bad))
    with pytest.raises(ValueError):EdaInvestigation.model_validate(dict(record.model_dump(),baseline=record.candidate))

def test_even_host_review_cannot_hide_report_ignored_scope_mismatch():
    record,artifacts=bundle()
    old=next(a for a in artifacts if a.reference==record.candidate.configuration)
    data=json.loads(old.data);data['ignored_checks']={'erc':[]}
    raw=encode(data)
    changed=ArtifactBytes(reference=old.reference.model_copy(update={'sha256':sha256(raw).hexdigest(),'revision_id':'v2'}),data=raw)
    candidate=record.candidate.model_copy(update={'configuration':changed.reference,'project':record.candidate.project.model_copy(update={'configuration_hash':changed.reference.sha256})})
    record=EdaInvestigation.model_validate(dict(record.model_dump(),candidate=candidate))
    authority=authorize_eda(record,reviewed_by='host-reviewer')
    with pytest.raises(ValueError,match='rule scope'):
        admit_eda(record,tuple(changed if a is old else a for a in artifacts),authority=authority)

def test_vivado_synthetic_reports_use_existing_builder_and_never_attest_execution():
    artifacts=[]
    def add(name,raw):
        a=ArtifactBytes(reference=dict(artifact_id=name,revision_id='v1',sha256=sha256(raw).hexdigest()),data=raw)
        artifacts.append(a);return a.reference
    sources=[dict(name=n,reference=add('source-'+str(i),b'synthetic source '+str(i).encode())) for i,n in enumerate(('exercise3_core.v','exercise3_nexys_video_top.v','nexys_video_exercise3.xdc'))]
    refs=tuple(s['reference'] for s in sources)
    config=add('config',encode(dict(domain='vivado',profile='vivado.exercise3.v1')))
    recipe=add('recipe',b'# inert synthetic Tcl bytes')
    req=add('req',encode(dict(domain='vivado',part='xc7a200tsbg484-1',top='exercise3_nexys_video_top',clock_period_ps=10000,recipe=recipe.model_dump(mode='json'))))
    receipt=add('receipt',b'{"success":true,"exit_code":0}')
    report=add('route',b'Design Route Status\n   # of fully routed nets........ : 35\n   # of nets with routing errors.......... : 0\n')
    record=EdaInvestigation(investigation_id='synthetic-vivado',domain='vivado',requirements=req,candidate=dict(
        project=dict(project_id='exercise',revision_id='v1',source_refs=refs,configuration_hash=config.sha256),sources=sources,
        configuration=config,receipt=receipt,reports=[dict(name='route_status.rpt',reference=report,source_refs=refs)]))
    authority=authorize_eda(record,reviewed_by='synthetic-reviewer')
    envelope=admit_eda(record,tuple(artifacts),authority=authority)
    result=project_eda(envelope,tuple(artifacts),authority=authority)
    assert result['execution_status']=='not_observed'
    assert result['projection']['investigation']['execution']['status']=='not_run'
    assert result['projection']['investigation']['complete_report_set'] is False
