"""Independent hosted firmware gate review. Synthetic files/fake processes only."""
import hashlib
import importlib.util
import json
from pathlib import Path
import struct
import sys
from types import SimpleNamespace

import pytest

from veriforge.firmware.executable import FirmwarePreparation,prepare_executable_case,commands

ROOT=Path(__file__).resolve().parents[2]
spec=importlib.util.spec_from_file_location('firmware_ci_independent',ROOT/'scripts/ci_firmware_acceptance.py')
ci=importlib.util.module_from_spec(spec);spec.loader.exec_module(ci)


def digest(data):return hashlib.sha256(data).hexdigest()


def put(path,data):
    path.parent.mkdir(parents=True,exist_ok=True);path.write_text(json.dumps(data))


def elf(words):
    """Handwritten ELF32 PT_LOAD and executable section, never compiled."""
    code=b''.join(w.to_bytes(4,'little') for w in words);data=bytearray(592)
    struct.pack_into('<16sHHIIIIIHHHHHH',data,0,b'\x7fELF\x01\x01\x01'+b'\0'*9,
        2,243,1,0x80000000,52,512,0,52,32,1,40,2,0)
    struct.pack_into('<IIIIIIII',data,52,1,256,0x80000000,0x80000000,len(code),len(code),5,4)
    data[256:256+len(code)]=code
    struct.pack_into('<IIIIIIIIII',data,552,0,1,6,0x80000000,256,len(code),0,0,4,0)
    return bytes(data),code


def observation(bad):
    ops=[(0x40000000,15,0x11223344),(0x40000000,0,0x11223344),
        (0x40000004,15,0x55667788),(0x40000004,0,0x55667788),
        (0x40000008,15,0xaabbccdd),(0x40000008,2,0x1100),(0x40000008,0,0xaabb11dd),
        (0x4000000c,15,0x13579bdf),(0x4000000c,0,0x13579bdf)]
    if bad:ops=ops[:2]+[(0x40000008,15,0x55667788),(0x40000004,0,0)]
    ops.append((0x8000ff00,15,2 if bad else 1));events=[];transfers=[]
    for i,(address,strobes,value) in enumerate(ops):
        cycle=6+3*i
        transfers.append(dict(sequence=i,cycle=cycle,address=address,instruction=False,
            write_strobes=strobes,write_data=value if strobes else 0,read_data=0 if strobes else value))
        events.append(dict(cycle=cycle+1,rvfi_order=i,rvfi_insn=0x002080a3 if strobes==2 else 0x0020a023 if strobes else 0x0000a103,
            rvfi_pc_rdata=0x80000000+4*i,rvfi_rd_addr=0 if strobes else 2,rvfi_rd_wdata=0 if strobes else value,
            rvfi_rs1_rdata=address,rvfi_mem_addr=address,rvfi_mem_rmask=0 if strobes else 15,rvfi_mem_wmask=strobes,
            rvfi_mem_rdata=0 if strobes else value,rvfi_mem_wdata=value if strobes else 0,rvfi_trap=False,rvfi_intr=False))
    return dict(schema_version='firmware.native-observation.v1',termination='completion_store_retired',
        cycles=events[-1]['cycle']+1,events=events,transfers=transfers)


def native_case(root,snapshot,label):
    root.mkdir()
    fixture=ci.FIXTURES[label]
    prepare_executable_case(FirmwarePreparation(fixture_id=fixture),root)
    preparation=json.loads((root/'preparation.json').read_text());preparation['recipes']=commands(snapshot)
    put(root/'preparation.json',preparation)
    raw=observation(label in ('bad','bad-replay'));words=[r['rvfi_insn'] for r in raw['events']]
    image,code=elf(words);(root/'firmware.elf').write_bytes(image);(root/'firmware.bin').write_bytes(code)
    for name in ('start.o','independent_probe.o','main.o'):(root/name).write_bytes(b'SYNTHETIC OBJECT, NO COMPILER '+name.encode())
    (root/'firmware.map').write_text('SYNTHETIC MAP, NO LINKER')
    (root/'disassembly.txt').write_text(''.join(f'{0x80000000+4*i:08x}: {word:08x} '+
        ('sb x2,1(x1)' if word==0x002080a3 else 'sw x2,0(x1)' if word==0x0020a023 else 'lw x2,0(x1)')+'\n'
        for i,word in enumerate(words)))
    (root/'elf_headers.txt').write_text('Tag_RISCV_arch: "rv32i2p1"\n')
    put(root/'observation.json',raw)
    _,_,_,validate_snapshot,_,_=ci.runtime()
    plan,ids=validate_snapshot(root)
    put(root/'execution-identities.json',ids)
    put(root/'simulation-job.json',dict(preparation=plan.model_dump(mode='json'),
        binary=str(snapshot/'firmware.bin'),trace=str(snapshot/'observation.json')))
    recipe=commands(snapshot);recipe['simulator']=(sys.executable,'-m','veriforge.firmware.sim_job',str(snapshot))
    replay=label.endswith('-replay')
    execution=dict(schema_version='firmware.ci-execution.v1',fixture_id=fixture,completed=True,replay=replay,
        wall_seconds=0.0,commands=[dict(phase=p,argv=list(c),returncode=0) for p,c in recipe.items() if not replay or p=='simulator'],
        files={name:digest((root/name).read_bytes()) for name in ci.CASE_FILES if (root/name).is_file()})
    put(root/'execution.json',execution)
    return root


@pytest.fixture
def case(tmp_path,monkeypatch):
    monkeypatch.setattr(ci,'SNAPSHOT',tmp_path/'independent-snapshot')
    root=native_case(tmp_path/'case',ci.SNAPSHOT,'bad')
    hashes={name:digest((ROOT/name).read_bytes()) for name in ci.SOURCE_FILES}
    return root,hashes


def rebind(root):
    path=root/'execution.json';record=json.loads(path.read_text())
    record['files']={name:digest((root/name).read_bytes()) for name in ci.CASE_FILES if (root/name).is_file()}
    put(path,record)


def test_native_bad_case_derives_literal_first_fault_and_immutable_identity(case):
    root,hashes=case;result=ci.check_case(root,'bad',hashes,{'image_id':'synthetic-only'})
    assert result['check']['execution_status']=='completed' and result['check']['verdict']=='design_mismatch'
    assert result['first_conflict']['mmio_sequence']==2
    assert result['first_conflict']['expected_address']==0x40000004
    assert result['first_conflict']['observed_address']==0x40000008
    assert result['failure_signature']['failure_class']=='firmware.status_offset'
    assert result['failure_signature']['signature_hash']


@pytest.mark.parametrize('mutation',['missing-trace','trap','timeout','failed-build','stale-object','changed-contract',
    'changed-checker','bad-disassembly','uncovered-code','wrong-instruction','wrong-first-fault'])
def test_bad_native_evidence_never_passes_even_with_rebound_checksums(case,mutation):
    root,hashes=case
    if mutation=='missing-trace':(root/'observation.json').unlink()
    elif mutation in ('trap','timeout','wrong-instruction','wrong-first-fault'):
        path=root/'observation.json';raw=json.loads(path.read_text())
        if mutation=='trap':raw['termination']='trap';raw['events'][-1]['rvfi_trap']=True
        elif mutation=='timeout':raw['termination']='cycle_limit'
        elif mutation=='wrong-instruction':raw['events'][0]['rvfi_insn']=0x13
        else:raw['transfers'][2]['address']=raw['events'][2]['rvfi_mem_addr']=0x4000000c
        put(path,raw);rebind(root)
    elif mutation=='failed-build':
        path=root/'execution.json';data=json.loads(path.read_text());data['commands'][3]['returncode']=1;put(path,data)
    elif mutation=='stale-object':(root/'main.o').write_bytes(b'STALE OBJECT')
    elif mutation=='changed-contract':(root/'register-contract.json').write_bytes(b'{}');rebind(root)
    elif mutation=='changed-checker':hashes['src/veriforge/firmware/harness.py']='0'*64
    elif mutation=='bad-disassembly':(root/'disassembly.txt').write_text('80000000: 00000013 addi x0,x0,0\n');rebind(root)
    else:
        image,code=elf([0x13]*20);(root/'firmware.elf').write_bytes(image);(root/'firmware.bin').write_bytes(code);rebind(root)
    with pytest.raises((ci.GateError,ValueError,OSError)):ci.check_case(root,'bad',hashes,{'image_id':'synthetic-only'})


@pytest.mark.parametrize('phase',ci.PHASES)
def test_each_failed_compiler_or_binutils_phase_stops_before_simulation(tmp_path,phase):
    seen=[];records=[]
    def invoke(command,out,err,deadline,**kwargs):
        current=ci.PHASES[len(seen)];seen.append(current)
        assert deadline==123.0
        if current==phase:raise ci.GateError('synthetic phase failure')
        return dict(argv=list(command),returncode=0)
    with pytest.raises(ci.GateError,match=phase):
        ci.execute_steps(tmp_path,123.0,False,invoke=invoke,
            validate=lambda _:pytest.fail('failure must prevent simulator validation'),records=records)
    assert seen==list(ci.PHASES[:ci.PHASES.index(phase)+1])


def test_saved_replay_rebuilds_hdl_only_and_keeps_same_aggregate_deadline(tmp_path):
    seen=[];validated=[]
    def invoke(command,out,err,deadline,**kwargs):
        seen.append((list(command),deadline));return dict(argv=list(command),returncode=0)
    records=ci.execute_steps(tmp_path,456.0,True,invoke=invoke,validate=lambda root:validated.append(root))
    assert validated==[tmp_path] and len(seen)==1 and seen[0][1]==456.0
    assert seen[0][0][1:3]==['-m','veriforge.firmware.sim_job']
    assert [r['phase'] for r in records]==['simulator']
    assert not any(tool in arg for arg in seen[0][0] for tool in ('gcc','objcopy','objdump','readelf'))


@pytest.mark.parametrize('word',[0x00000001,0x02000033,0x300110f3])
def test_unexecuted_executable_bytes_must_still_belong_to_supported_isa(word):
    image,_=elf([0x13,word])
    with pytest.raises(ci.GateError,match='RV32I|subset'):ci.executable_words(image)


def test_worker_aggregate_clock_includes_preparation_and_all_steps(tmp_path,monkeypatch):
    monkeypatch.setattr(ci,'SNAPSHOT',tmp_path/'snapshot');output=tmp_path/'output'
    path_type=Path
    monkeypatch.setattr(ci,'Path',lambda value:output if value=='/attempt' else path_type(value))
    ticks=iter([0.0,121.0,122.0]);monkeypatch.setattr(ci.time,'monotonic',lambda:next(ticks,122.0))
    def fake_steps(root,deadline,replay,records):
        assert deadline==120.0 and not replay
    monkeypatch.setattr(ci,'execute_steps',fake_steps)
    assert ci.worker('control')==1
    receipt=json.loads((output/'execution.json').read_text())
    assert receipt['completed'] is False and receipt['error']=='case total deadline exhausted'


@pytest.mark.parametrize('changed',['none','start.o','firmware.elf','firmware.bin'])
def test_worker_replay_preserves_saved_bytes_and_rejects_drift_before_simulator(tmp_path,monkeypatch,changed):
    snapshot=tmp_path/'snapshot';saved=tmp_path/'saved';output=tmp_path/'attempt'
    monkeypatch.setattr(ci,'SNAPSHOT',snapshot)
    native_case(snapshot,snapshot,'bad')
    original=json.loads((snapshot/'execution.json').read_text())
    original_hash=digest((snapshot/'execution.json').read_bytes())
    assert snapshot.resolve().is_relative_to(tmp_path.resolve()) and saved.resolve().is_relative_to(tmp_path.resolve())
    snapshot.rename(saved)  # Only this test's verified contained synthetic directory.
    if changed!='none':(saved/changed).write_bytes(b'changed saved artifact')
    path_type=Path
    def mapped_path(value):
        name=str(value).replace('\\','/')
        for guest,host in (('/saved',saved),('/attempt',output)):
            if name==guest:return host
            if name.startswith(guest+'/'):return host/name[len(guest)+1:]
        return path_type(value)
    monkeypatch.setattr(ci,'Path',mapped_path)
    seen=[]
    def fake_steps(root,deadline,replay,records):
        assert replay and root==snapshot
        seen.append('simulator')
        assert all(digest((root/name).read_bytes())==original['files'][name] for name in ci.PREPARED+ci.PROGRAM)
        put(root/'observation.json',observation(True))
        _,_,_,validate,_,_=ci.runtime();plan,ids=validate(root)
        put(root/'execution-identities.json',ids)
        put(root/'simulation-job.json',dict(preparation=plan.model_dump(mode='json'),binary=str(root/'firmware.bin'),trace=str(root/'observation.json')))
        records.append(dict(phase='simulator',argv=[sys.executable,'-m','veriforge.firmware.sim_job',str(root)],returncode=0))
    monkeypatch.setattr(ci,'execute_steps',fake_steps)
    code=ci.worker('bad-replay');record=json.loads((output/'execution.json').read_text())
    if changed=='none':
        assert code==0 and seen==['simulator'] and record['completed'] is True
        assert record['replay_original_hash']==original_hash
        assert all((output/name).read_bytes()==(saved/name).read_bytes() for name in ci.PREPARED+ci.PROGRAM)
    else:
        assert code==1 and not seen and record['completed'] is False
        assert 'saved executable/source changed' in record['error']


def five_summaries(tmp_path,monkeypatch):
    snapshot=tmp_path/'stable-check-snapshot';monkeypatch.setattr(ci,'SNAPSHOT',snapshot)
    hashes={name:digest((ROOT/name).read_bytes()) for name in ci.SOURCE_FILES};results={}
    for label in ci.LABELS:
        root=native_case(tmp_path/label,snapshot,label)
        if label.endswith('-replay'):
            path=root/'execution.json';record=json.loads(path.read_text())
            record['replay_original_hash']=results[label.removesuffix('-replay')]['execution_sha256'];put(path,record)
        results[label]=ci.check_case(root,label,hashes,{'image_id':'synthetic-only'})
        target=tmp_path/('checked-'+label)
        assert snapshot.resolve().is_relative_to(tmp_path.resolve()) and target.resolve().is_relative_to(tmp_path.resolve())
        snapshot.rename(target)
    return results


@pytest.mark.parametrize('changed',['none','missing-case','checker','contract','saved-elf','saved-object','saved-trace','lineage'])
def test_five_native_cases_require_exact_checker_candidate_and_saved_replay(tmp_path,monkeypatch,changed):
    summaries=five_summaries(tmp_path,monkeypatch)
    if changed=='none':
        ci.compare_cases(summaries)
        assert summaries['candidate']['check']['verdict']=='pass_within_scope'
        assert summaries['bad-replay']['failure_signature']==summaries['bad']['failure_signature']
        return
    if changed=='missing-case':summaries.pop('candidate-replay')
    elif changed in ('checker','contract'):summaries['candidate']['identity'][changed+'_hash']='0'*64
    elif changed=='lineage':summaries['bad-replay']['replay_original_hash']='0'*64
    elif changed=='saved-trace':summaries['bad-replay']['observation_sha256']='0'*64
    else:summaries['bad-replay']['program_hashes']['firmware.elf' if changed=='saved-elf' else 'main.o']='0'*64
    with pytest.raises(ci.GateError):ci.compare_cases(summaries)


def test_successful_process_observed_after_deadline_fails_closed(tmp_path,monkeypatch):
    ticks=iter([0.0,0.0,2.0]);child=SimpleNamespace(returncode=0,poll=lambda:0)
    monkeypatch.setattr(ci.time,'monotonic',lambda:next(ticks,2.0))
    monkeypatch.setattr(ci.subprocess,'Popen',lambda *a,**kw:child)
    with pytest.raises(ci.GateError,match='timeout|deadline'):
        ci.run_process(['SYNTHETIC_NO_EXECUTION'],tmp_path/'out',tmp_path/'err',1.0,root=tmp_path,cap=4096)


@pytest.mark.parametrize('cleanup_fails',[False,True])
def test_timeout_always_settles_owned_container_and_client(tmp_path,monkeypatch,cleanup_fails):
    seen=[];child=SimpleNamespace(pid=12345,returncode=None)
    child.poll=lambda:child.returncode;child.wait=lambda **kw:seen.append('wait')
    ticks=iter([0.0,0.0,2.0])
    monkeypatch.setattr(ci.time,'monotonic',lambda:next(ticks,2.0))
    monkeypatch.setattr(ci.subprocess,'Popen',lambda *a,**kw:child)
    def settle():
        seen.append('settle')
        if cleanup_fails:raise RuntimeError('synthetic settlement failure')
    def kill(pid,sig):assert pid==12345;seen.append('kill');child.returncode=-9
    monkeypatch.setattr(ci.os,'killpg',kill,raising=False);monkeypatch.setattr(ci.signal,'SIGKILL',9,raising=False)
    with pytest.raises(ci.GateError,match='timeout'):
        ci.run_process(['SYNTHETIC_NO_EXECUTION'],tmp_path/'out',tmp_path/'err',1.0,root=tmp_path,cap=4096,settle=settle)
    assert seen==['settle','kill','wait']


def test_private_environment_values_and_local_controller_never_reach_execution(monkeypatch):
    monkeypatch.setenv('META_API_KEY','PRIVATE_CANARY');monkeypatch.setenv('GITHUB_TOKEN','PRIVATE_CANARY')
    assert 'PRIVATE_CANARY' not in ci.environment().values() and 'PRIVATE_CANARY' not in ci.environment(worker=True).values()
    monkeypatch.delenv('GITHUB_ACTIONS',raising=False)
    monkeypatch.setattr(ci.subprocess,'Popen',lambda *a,**kw:pytest.fail('no local process authorized'))
    with pytest.raises(ci.GateError,match='hosted manual'):ci.main()


def test_fixed_isolation_readonly_saved_bytes_and_bounded_export(tmp_path,monkeypatch):
    monkeypatch.setattr(ci.os,'getuid',lambda:1001,raising=False);monkeypatch.setattr(ci.os,'getgid',lambda:1001,raising=False)
    command=ci.container_command('sha256:'+'a'*64,tmp_path/'public',tmp_path/'out','bad-replay','--worker-bad-replay',saved=tmp_path/'saved')
    for flag,value in (('--user','1001:1001'),('--network','none'),('--memory','2g'),('--cpus','2'),('--pids-limit','128'),('--pull','never')):
        assert command[command.index(flag)+1]==value
    assert '--read-only' in command and '--cap-drop' in command
    mounts=[command[i+1] for i,arg in enumerate(command) if arg=='--mount']
    assert len(mounts)==5 and sum(m.endswith(',readonly') for m in mounts)==4
    assert any('dst=/saved,readonly' in m for m in mounts) and not any('docker.sock' in m for m in mounts)
    work=tmp_path/'work';work.mkdir();source=tmp_path/'source';source.mkdir()
    put(work/'summary.json',{'accepted':False});(work/'.env').write_text('PRIVATE_CANARY')
    ci.export_evidence(work,source,tmp_path/'export')
    assert not (tmp_path/'export/.env').exists()
    with (work/'build.log').open('wb') as stream:stream.truncate(ci.CAP)
    with pytest.raises(ci.GateError,match='cap'):ci.export_evidence(work,source,tmp_path/'oversized')


def test_manual_workflow_always_preserves_bounded_artifacts_and_no_image_publish():
    workflow=(ROOT/'.github/workflows/firmware-acceptance.yml').read_text()
    assert 'workflow_dispatch:' in workflow and 'inputs:' not in workflow
    assert all(event not in workflow for event in ('pull_request:','push:','schedule:'))
    for required in ('contents: read','persist-credentials: false','timeout-minutes: 60',
        'if: always()','retention-days: 1','include-hidden-files: false','path: ci-artifacts/firmware/',
        'cf430e030ddbb5b0abf93d22962f4752f3646cd9'):
        assert required in workflow
    source=(ROOT/'scripts/ci_firmware_acceptance.py').read_text()
    assert not any(token in source for token in ('--push','docker.sock','docker login','image save'))
    assert len(ci.SOURCE_FILES)==50 and ci.CAP==32*1024**2 and ci.CASE_CAP==4*1024**2
    assert not any('.coordination' in path or '.artifacts' in path for path in ci.SOURCE_FILES)
