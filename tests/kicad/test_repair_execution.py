"""Synthetic boundary operations only; never instantiate a native process."""
from pathlib import Path
from dataclasses import replace
import json
import pytest
from veriforge.kicad.reference import reference_requirements
from veriforge.kicad.repair_execution import Bundle, Review, execute_candidate, digest, encode, FILES, IGNORED
from tests.kicad.test_baseline import report

ROOT=Path(__file__).resolve().parents[2]

class Fake:
    provenance='fake'
    def __init__(self,fail=None): self.fail=fail; self.calls=[]; self.cleaned=0
    def run(self,c,*,cwd,environment,deadline,checkpoint):
        self.calls.append(c.name); checkpoint(); out=cwd.parent/'reports'
        if self.fail=='interrupt': raise KeyboardInterrupt()
        if self.fail=='drift': (cwd/'carrier.kicad_pro').write_bytes(b'bad'); checkpoint()
        if c.name in IGNORED:
            data=report(c.name); data['ignored_checks']=[{'key':k,'description':'synthetic'} for k in IGNORED[c.name]]
            if self.fail=='ignored': data['ignored_checks']=[]
            (out/(c.name+'.json')).write_bytes(encode(data))
        if c.name=='netlist': (out/'netlist.xml').write_bytes((ROOT/'tests/kicad/fixtures/carrier-control.netlist.xml').read_bytes())
        if c.name=='bom': (out/'properties.csv').write_text('Reference,Manufacturer,MPN\nJ1,Samtec,TSW-106-07-G-D\nJ2,Samtec,TSW-106-07-G-D\n')
        if c.name.endswith('svg'):
            p=out/('schematic' if c.name=='schematic-svg' else 'pcb'); p.mkdir()
            (p/'carrier-F_SilkS.svg').write_bytes(b'<svg/>')
        return {'returncode':2 if self.fail=='code' else 0,'stdout':b'10.0.6','stderr':b''}
    def cleanup(self,*,seconds): self.cleaned+=1; return {'settled':self.fail!='cleanup'}

def inputs():
    src=ROOT/'targets/kicad/carrier/control_corrected_v1'
    files={n:(src/n).read_bytes() for n in FILES}
    original=Bundle(tuple(files.items()),tuple((n,digest(b)) for n,b in files.items()))
    # Explicit synthetic metadata candidate; originals are never edited.
    files['carrier.kicad_pcb']=files['carrier.kicad_pcb'].replace(b'(property "Manufacturer" ""',b'(property "Manufacturer" "Samtec"').replace(b'(property "MPN" ""',b'(property "MPN" "TSW-106-07-G-D"')
    candidate=Bundle(tuple(files.items()),tuple((n,digest(b)) for n,b in files.items()))
    req=reference_requirements(datasheet_sha256='a'*64,intent_sha256='b'*64,reviewed_by='synthetic review')
    return dict(original=original,candidate=candidate,configuration=(('10.0/test.json',b'{}'),),
        configuration_sha256=digest(encode({'10.0/test.json':digest(b'{}')})),
        review=Review(req,digest(encode([__import__('dataclasses').asdict(r) for r in req]))))

def run(tmp_path,backend,**kw):
    args=dict(root=tmp_path/'attempt',operation_id='test-001',backend=backend,
        control=lambda:True,reservation=lambda op:op=='test-001',cancelled=lambda:False,**inputs())
    args.update(kw); return execute_candidate(**args)

def test_complete_fake_attempt_retains_projection_receipt_and_sources(tmp_path):
    f=Fake(); r=run(tmp_path,f)
    assert r['execution_status']=='fake_completed',r['errors']
    assert len(f.calls)==f.cleaned==7
    assert r['native_execution']=='not_run'
    assert r['investigation']['design_verdict']=='inconclusive'
    assert r['investigation']['mapping']['status']=='inconclusive' # original XML unresolved MPN
    assert (tmp_path/'attempt/receipt.json').exists()
    assert r['artifact_hashes']['reports/erc.json']
    assert len(r['investigation']['pcb_views']['views'])==1
    with pytest.raises(FileExistsError): run(tmp_path,Fake())

@pytest.mark.parametrize('failure',['interrupt','drift','code','cleanup'])
def test_failures_stop_and_retain_attempt(tmp_path,failure):
    f=Fake(failure); r=run(tmp_path,f)
    assert r['execution_status'] in ('failed','interrupted')
    assert f.calls==['version']; assert f.cleaned==1
    assert r['records'][0]
    assert json.loads((tmp_path/'attempt/receipt.json').read_bytes())['errors']

def test_unadmitted_native_backend_is_held_without_call(tmp_path):
    f=Fake(); f.provenance='native'; r=run(tmp_path,f)
    assert r['execution_status']=='held'; assert not f.calls
    assert r['native_execution']=='not_run'

def test_cancel_before_staging_and_bad_review_fail_closed(tmp_path):
    with pytest.raises(InterruptedError): run(tmp_path,Fake(),cancelled=lambda:True)
    assert not (tmp_path/'attempt').exists()
    review=replace(inputs()['review'],expected_sha256='f'*64)
    with pytest.raises(ValueError,match='review identity'): run(tmp_path,Fake(),review=review)

def test_reservation_rechecked_during_attempt(tmp_path):
    f=Fake()
    r=run(tmp_path,f,reservation=lambda op:not f.calls)
    assert r['execution_status']=='interrupted'; assert f.calls==['version']

def test_changed_ignored_rules_stop_before_exports(tmp_path):
    f=Fake('ignored'); r=run(tmp_path,f)
    assert r['execution_status']=='failed'; assert f.calls==['version','erc']
    assert (tmp_path/'attempt/reports/erc.json').exists()

def test_protected_candidate_mismatch_never_calls_backend(tmp_path):
    args=inputs(); args['candidate']=args['original']; f=Fake()
    r=run(tmp_path,f,**args)
    assert r['execution_status']=='failed'; assert f.calls==[]

def test_stage_deadline_retains_cleanup_receipt(tmp_path):
    t=[0]
    def clock(): t[0]+=2; return t[0]
    f=Fake(); r=run(tmp_path,f,clock=clock)
    assert r['execution_status']=='failed'; assert f.cleaned==1
    assert r['records'][0]['cleanup']['settled'] is True

def test_aggregate_source_bytes_refused_before_any_write(tmp_path,monkeypatch):
    class SizedBytes(bytes):
        def __len__(self): return 8*1024**2
    args=inputs()
    for key in ('original','candidate'):
        bundle=args[key]
        args[key]=replace(bundle,files=tuple((n,SizedBytes(raw)) for n,raw in bundle.files))
    # Hashes still bind exact real small bytes; only reported lengths are large.
    writes=[]
    monkeypatch.setattr(Path,'write_bytes',lambda *a,**k:writes.append(a))
    with pytest.raises(ValueError,match='aggregate staging'):
        run(tmp_path,Fake(),**args)
    assert not writes; assert not (tmp_path/'attempt').exists()

def test_aggregate_config_files_refused_before_any_write(tmp_path,monkeypatch):
    configs=tuple((f'10.0/config-{n}.json',b'{}') for n in range(51))
    writes=[]
    monkeypatch.setattr(Path,'write_bytes',lambda *a,**k:writes.append(a))
    with pytest.raises(ValueError,match='aggregate staging'):
        run(tmp_path,Fake(),configuration=configs,
            configuration_sha256=digest(encode({n:digest(b) for n,b in configs})))
    assert not writes; assert not (tmp_path/'attempt').exists()

def test_aggregate_config_bytes_refused_before_any_write(tmp_path,monkeypatch):
    class SizedConfig(bytes):
        def __len__(self): return 1024**2
    configs=tuple((f'10.0/config-{n}.json',SizedConfig(b'{}')) for n in range(31))
    writes=[]
    monkeypatch.setattr(Path,'write_bytes',lambda *a,**k:writes.append(a))
    with pytest.raises(ValueError,match='aggregate staging'):
        run(tmp_path,Fake(),configuration=configs,
            configuration_sha256=digest(encode({n:digest(b) for n,b in configs})))
    assert not writes; assert not (tmp_path/'attempt').exists()
