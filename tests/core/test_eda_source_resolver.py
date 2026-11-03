"""Fake sessions, real immutable v3 admission and bounded synthetic/source files.

No engine/SQLite/API/native services, tools, credentials, model or network.
"""
from dataclasses import replace
from hashlib import sha256
from pathlib import Path
from types import SimpleNamespace,MappingProxyType
import copy
import json
from datetime import datetime,timezone
import pytest

from veriforge.contracts import canonical_hash
from veriforge.contracts.eda_tools import EdaInvestigation
from veriforge.system_investigation.records import ArtifactBytes
from veriforge.system_investigation import eda_records as eda
from veriforge.core import eda_source_resolver as R,eda_policy,investigations,engineering_catalog,service
from veriforge.core.eda_repair_runtime import VivadoDriver,KiCadDriver

ROOT=Path(__file__).resolve().parents[2]


class Session:
    def __init__(self):self.rows={};self.blobs={}
    def __enter__(self):return self
    def __exit__(self,*a):pass
    def get(self,model,key):return self.rows.get((model.__name__,key))
    def scalar(self,statement):return None # No local execution links on v3.
    def scalars(self,statement):
        key=next(v for k,v in statement.compile().params.items()if k.startswith('version_id'))
        return iter(self.blobs[key])


def encoded(value):return json.dumps(value,sort_keys=True,separators=(',',':')).encode()


def report(kind,*,fixed_rules=True):
    from veriforge.kicad.repair_execution import IGNORED
    body=dict(source='synthetic-source',date='2026-11-03T00:00:00Z',kicad_version='10.0.6',coordinate_units='mm',included_severities=['warning','error','exclusion'],ignored_checks=[])
    body['ignored_checks']=[dict(key=k,description='synthetic ignored rule')for k in sorted(IGNORED[kind])]if fixed_rules else[]
    body['$schema']=f'https://schemas.kicad.org/{kind}.v1.json'
    if kind=='erc':body['sheets']=[dict(path='/',uuid_path='/root/',violations=[])]
    else:body.update(violations=[],unconnected_items=[],schematic_parity=[])
    return encoded(body)


def fixture(tmp_path,monkeypatch,domain='kicad',*,clock_period_ps=10000,fixed_rules=True):
    s=Session();project=SimpleNamespace(id='project',owner='local',data={'domains':[domain]})
    s.rows['Project','project']=project
    monkeypatch.setattr(engineering_catalog,'get_engineering_project',lambda session,pid:project)
    monkeypatch.setattr(eda_policy,'_reviews',eda_policy._reviews)
    monkeypatch.setattr(eda_policy,'_implementation',eda_policy._implementation)
    shared=[]
    def artifact(name,raw,revision='requirements'):
        return ArtifactBytes(reference=dict(artifact_id=name,revision_id=revision,sha256=sha256(raw).hexdigest()),data=raw)
    if domain=='kicad':
        from veriforge.kicad.reference import reference_requirements
        from veriforge.kicad.repair_execution import FILES,IGNORED
        from veriforge.kicad.native_backend import Installation
        installation=Installation('a'*64)
        source_root=ROOT/'targets/kicad/carrier/control_corrected_v1'
        native={n:(source_root/n).read_bytes()for n in FILES}
        xml=(ROOT/'tests/kicad/fixtures/carrier-control.netlist.xml').read_bytes()
        ds=artifact('datasheet',b'synthetic datasheet, not engineering evidence');shared.append(ds)
        mappings=[dict(reference=m.reference,symbol_pin=m.symbol_pin,footprint_pad=m.footprint_pad,net=m.net,
            manufacturer=m.manufacturer,mpn=m.mpn,footprint=m.footprint,datasheet=ds.reference.model_dump(mode='json'),datasheet_location=m.datasheet_location)
            for m in reference_requirements(datasheet_sha256=ds.reference.sha256,intent_sha256='b'*64,reviewed_by='synthetic-host')]
        requirements=artifact('requirements',encoded(dict(domain=domain,mappings=mappings)))
        configuration=artifact('configuration',encoded(dict(domain=domain,profile='kicad10.configured.v1',ignored_checks={k:sorted(v)if fixed_rules else[]for k,v in IGNORED.items()},included_severities={'erc':['warning','error','exclusion'],'drc':['warning','error','exclusion']})))
        host=R.KiCadHost(installation,lambda operation:operation=='fresh-operation')
    else:
        from veriforge.vivado.repair_execution import ControllerInstallation,PROFILES
        from veriforge.vivado import repair_execution as execution
        from veriforge.vivado.readiness import render_batch_recipe,BatchRecipe
        installation=ControllerInstallation(tmp_path,tmp_path/'private/installed/controller.py')
        recipe=artifact('recipe',render_batch_recipe(BatchRecipe.EXERCISE3_POST_SYNTH).encode());shared.append(recipe)
        requirements=artifact('requirements',encoded(dict(domain=domain,part='xc7a200tsbg484-1',top='exercise3_nexys_video_top',clock_period_ps=clock_period_ps,recipe=recipe.reference.model_dump(mode='json'))))
        configuration=artifact('configuration',encoded(dict(domain=domain,profile='vivado.exercise3.v1')))
        monkeypatch.setattr(execution,'APPROVED_XDC_SHA256',sha256(b'synthetic constraint').hexdigest())
        # Fake installed-file boundary only. No controller module is imported.
        monkeypatch.setattr(ControllerInstallation,'controller',lambda *a:(tmp_path/'controller.py',b'synthetic-pinned-controller'))
        host=R.VivadoHost(installation,'U6-SYNTHETIC','synthesis',lambda job:pytest.fail('no native hooks in resolver'))
    shared+=[requirements,configuration];rows=[];entries=[];authorities=[]
    for ordinal in (1,2):
        revision='revision'+str(ordinal);version='version'+str(ordinal);parent='investigation'+str(ordinal)
        artifacts=list(shared)
        def add(name,raw):
            item=artifact(name,raw,revision);artifacts.append(item);return item.reference
        if domain=='kicad':
            sources=[dict(name='schematic.xml',reference=add('xml',xml)),dict(name='board.kicad_pcb',reference=add('board',native['carrier.kicad_pcb']))]
            reports=[dict(name=kind+'.json',reference=add(kind,report(kind,fixed_rules=fixed_rules)),source_refs=(sources[0 if kind=='erc'else 1]['reference'],))for kind in ('erc','drc')]
        else:
            from veriforge.vivado.investigation import SYNTH_REPORTS
            sources=[dict(name=n,reference=add('source'+str(i),b'synthetic constraint'if i==2 else('source'+str(ordinal)+n).encode()))for i,n in enumerate(('exercise3_core.v','exercise3_nexys_video_top.v','nexys_video_exercise3.xdc'))]
            body={'check_timing':'checking no_input_delay (12)\nchecking no_output_delay (6)\n','methodology':'| TIMING-18 | Warning | Missing IO | 12 |\n'}
            reports=[dict(name=n+'.rpt',reference=add(n,('| Tool Version : Vivado v.2026.1 Build 6511674\n| Design : exercise3_nexys_video_top\n| Device : xc7a200tsbg484-1\n| Design State : Synthesized\n| Command : '+('check_timing'if n=='check_timing'else'report_'+n)+'\n'+body.get(n,'synthetic')).encode()),source_refs=tuple(x['reference']for x in sources))for n in SYNTH_REPORTS]
        record=EdaInvestigation(investigation_id=parent,domain=domain,requirements=requirements.reference,candidate=dict(
            project=dict(project_id='project',revision_id=revision,source_refs=tuple(x['reference']for x in sources),configuration_hash=configuration.reference.sha256),
            sources=sources,configuration=configuration.reference,receipt=add('receipt',b'{"synthetic":true}'),reports=reports))
        authority=eda.authorize_eda(record,reviewed_by='explicit-synthetic-host-review');authorities.append(authority)
        envelope=eda.admit_eda(record,tuple(artifacts),authority=authority);raw=eda.encode(envelope)
        row=SimpleNamespace(id=version,investigation_id=parent,project_id='project',content=raw,content_hash=sha256(raw).hexdigest(),revision_id=revision,revision_hash=canonical_hash(record.candidate.project),manifest_hash=requirements.reference.sha256,schema_version='engineering.admission.v3',scope='recorded_import',execution_status='not_run')
        row.ordinal=ordinal;row.created_at=datetime(2026,11,3,tzinfo=timezone.utc)
        rows.append(copy.copy(row));s.rows['InvestigationVersion',version]=row;s.rows['Investigation',parent]=SimpleNamespace(id=parent,owner='local',project_id='project')
        s.blobs[version]=[SimpleNamespace(id=str(i),version_id=version,artifact_id=a.reference.artifact_id,native_revision_id=a.reference.revision_id,native_attempt_id=a.reference.attempt_id,sha256=a.reference.sha256,size_bytes=len(a.data),identity_key=investigations._identity(a.reference),data=a.data,role='eda_evidence',media_type='application/octet-stream')for i,a in enumerate(artifacts)]
        entries.append(R.NativeAssociation(version,row.content_hash,'project',revision,row.revision_hash,requirements.reference,configuration.reference,authority,installation,
            artifact('association-'+str(ordinal),b'synthetic reviewed association, not native evidence'),
            files=tuple(native.items())if domain=='kicad'else(),native_configuration=(('10.0/test.json',b'{}'),)if domain=='kicad'else(),exported_xml=sources[0]['reference']if domain=='kicad'else None))
    eda_policy.configure_eda_reviews(tuple(authorities),implementation_sha256=eda.implementation_hash())
    output=tmp_path/'output';output.mkdir()
    catalog=R.ReviewedNativeCatalog.from_trusted_host_review(tuple(entries))
    resolver=R.ImmutableEdaResolver(lambda:s,output_root=output,catalog=catalog,**{domain:host})
    return resolver,s,rows,entries


@pytest.mark.parametrize('domain',['kicad','vivado'])
def test_real_v3_store_readback_constructs_exact_concrete_driver(tmp_path,monkeypatch,domain):
    resolver,s,rows,entries=fixture(tmp_path,monkeypatch,domain)
    driver=resolver(domain,*rows)
    assert type(driver)is(KiCadDriver if domain=='kicad'else VivadoDriver)
    if domain=='kicad':
        assert driver.original.checked()['carrier.kicad_pcb']==driver.candidate.checked()['carrier.kicad_pcb']
        assert len(driver.review.checked())==24 and driver.reservation('fresh-operation') and not driver.reservation('stale')
        assert list(resolver.root.iterdir())==[] # No KiCad driver/materialization effects.
    else:
        assert driver.baseline.project.sources==driver.pins.original
        assert driver.pins.io_requirements_sha256 is None # No invented I/O document.
        assert driver.runtime.root==resolver.root/'vivado-native'
        other=resolver(domain,*rows)
        assert other.runtime.root==driver.runtime.root and other.original!=driver.original
        assert (driver.original/'exercise3_core.v').read_bytes()!=(driver.candidate/'exercise3_core.v').read_bytes()
        assert driver.baseline.execution.status=='not_run'


@pytest.mark.parametrize('change',['same','foreign_project','unowned','stale_content','missing_blob','extra_blob','swapped_bytes','unreviewed'])
def test_invalid_store_or_ownership_fails_before_materialization(tmp_path,monkeypatch,change):
    resolver,s,rows,entries=fixture(tmp_path,monkeypatch,'vivado')
    if change=='same':rows[1]=rows[0]
    if change=='foreign_project':rows[1]=copy.copy(rows[1]);rows[1].project_id='foreign'
    if change=='unowned':s.rows['Project','project'].owner='foreign'
    if change=='stale_content':s.rows['InvestigationVersion','version1'].content_hash='0'*64
    if change=='missing_blob':s.blobs['version1'].pop()
    if change=='extra_blob':s.blobs['version1'].append(copy.copy(s.blobs['version1'][0]))
    if change=='swapped_bytes':s.blobs['version1'][0].data=b'changed'
    if change=='unreviewed':monkeypatch.setattr(eda_policy,'_reviews',MappingProxyType({}))
    with pytest.raises((ValueError,service.Conflict,service.NotFound)):resolver('vivado',*rows)
    assert not list(resolver.root.iterdir())


@pytest.mark.parametrize('change',['xml','configuration','files','board_bytes','requirements','installation'])
def test_native_catalog_association_cannot_be_swapped(tmp_path,monkeypatch,change):
    resolver,s,rows,entries=fixture(tmp_path,monkeypatch)
    entry=entries[1]
    if change=='xml':entry=replace(entry,exported_xml=entries[0].exported_xml)
    if change=='configuration':entry=replace(entry,native_configuration=(('different.json',b'{}'),))
    if change=='files':entry=replace(entry,files=entry.files[:-1])
    if change=='board_bytes':entry=replace(entry,files=tuple((n,b'changed'if n=='carrier.kicad_pcb'else b)for n,b in entry.files))
    if change=='requirements':entry=replace(entry,requirements=entry.requirements.model_copy(update={'sha256':'0'*64}))
    if change=='installation':
        from veriforge.kicad.native_backend import Installation
        entry=replace(entry,installation=Installation('c'*64))
    resolver.catalog=R.ReviewedNativeCatalog.from_trusted_host_review((entries[0],entry))
    with pytest.raises((ValueError,service.Conflict)):resolver('kicad',*rows)
    assert not list(resolver.root.iterdir())


@pytest.mark.parametrize('name',['../escape.json','/absolute.json','x\\y.json','x:y.json'])
def test_catalog_paths_reject_before_writes(tmp_path,monkeypatch,name):
    resolver,s,rows,entries=fixture(tmp_path,monkeypatch)
    with pytest.raises(service.Conflict):R.ReviewedNativeCatalog.from_trusted_host_review((replace(entries[0],native_configuration=((name,b'{}'),)),))
    assert not list(resolver.root.iterdir())


def test_catalog_capability_is_explicit_immutable_and_bounded(tmp_path,monkeypatch):
    resolver,s,rows,entries=fixture(tmp_path,monkeypatch)
    with pytest.raises(TypeError):R.ReviewedNativeCatalog()
    with pytest.raises(AttributeError):resolver.catalog._entries={}
    resolver.catalog=None
    with pytest.raises(service.Conflict):resolver('kicad',*rows)
    with pytest.raises(service.Conflict):R.ReviewedNativeCatalog.from_trusted_host_review((replace(entries[0],files=(('large',b'x'*(R.MAX_FILE+1)),)),))
    monkeypatch.setattr(R,'MAX_CATALOG_BYTES',1)
    with pytest.raises(service.Conflict):R.ReviewedNativeCatalog.from_trusted_host_review(tuple(entries))
    assert not list(resolver.root.iterdir())


@pytest.mark.parametrize('profile',['clock','rules','controller'])
def test_recorded_import_does_not_widen_fixed_native_profile(tmp_path,monkeypatch,profile):
    domain='kicad'if profile=='rules'else'vivado'
    resolver,s,rows,entries=fixture(tmp_path,monkeypatch,domain,
        clock_period_ps=20000 if profile=='clock'else 10000,fixed_rules=profile!='rules')
    if profile=='controller':
        from veriforge.vivado.repair_execution import ControllerInstallation
        def unavailable(*a):raise ValueError('synthetic installation drift')
        monkeypatch.setattr(ControllerInstallation,'controller',unavailable)
    with pytest.raises((ValueError,service.Conflict)):resolver(domain,*rows)
    assert not list(resolver.root.iterdir())


@pytest.mark.parametrize('field',['files','native_configuration'])
def test_mutable_inner_pairs_cannot_enter_reviewed_catalog(tmp_path,monkeypatch,field):
    resolver,s,rows,entries=fixture(tmp_path,monkeypatch)
    entry=entries[0];values=getattr(entry,field)
    selected=next(i for i,pair in enumerate(values)if field!='files'or pair[0]=='carrier.kicad_sch')
    mutable=list(values[selected]);mixed=tuple(mutable if i==selected else pair for i,pair in enumerate(values))
    invalid=replace(entry,**{field:mixed})
    with pytest.raises(service.Conflict):
        R.ReviewedNativeCatalog.from_trusted_host_review((invalid,entries[1]))
    # A caller mutating its rejected pair cannot change the installed capability.
    mutable[1]=b'changed after attempted installation'
    driver=resolver('kicad',*rows)
    retained=dict(driver.original.files if field=='files'else driver.configuration)
    assert retained[values[selected][0]]==values[selected][1]
    assert not list(resolver.root.iterdir())
