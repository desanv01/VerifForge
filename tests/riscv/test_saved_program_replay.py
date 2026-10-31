"""Synthetic saved-control admission/process-boundary tests; no native execution."""
from dataclasses import asdict
import importlib.util
import json
from pathlib import Path
import struct
from types import SimpleNamespace

import pytest

from veriforge.riscv import runner
from veriforge.riscv.contracts import RiscvExperiment

ROOT = Path(__file__).resolve().parents[2]


def save(path, value):
    path.write_bytes(runner._json(value))


def elf_header(kind, entry=0, phoff=0, phnum=0):
    return struct.pack('<16sHHIIIIIHHHHHH', b'\x7fELF\x01\x01\x01'+b'\0'*9,
        kind, 243, 1, entry, phoff, 0, 0, 52, 32 if phnum else 0, phnum, 0, 0, 0)


@pytest.fixture
def original(tmp_path, monkeypatch):
    # Reuse only the existing clearly synthetic trace builder, never actual tools.
    import platform
    monkeypatch.setattr(platform, 'machine', lambda: 'AMD64')
    spec = importlib.util.spec_from_file_location('saved_test_builder', ROOT/'tests/riscv/test_ci_acceptance_plan.py')
    builder = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(builder)
    directory = tmp_path/'original'
    builder.synthetic_attempt(directory)
    binary = struct.pack('<3I', 0x80010137, 0xef010113, 0xf011a023)
    (directory/'smoke.o').write_bytes(elf_header(1))
    (directory/'smoke.elf').write_bytes(elf_header(2, runner.PLATFORM.entry, 52, 1)+
        struct.pack('<8I', 1, 84, runner.PLATFORM.entry, runner.PLATFORM.entry, len(binary), len(binary), 5, 4)+binary)
    (directory/'smoke.bin').write_bytes(binary)
    env = json.loads((directory/'environment.json').read_bytes())
    env.update(object_hash=runner._hash((directory/'smoke.o').read_bytes()), elf_hash=runner._hash((directory/'smoke.elf').read_bytes()),
        binary_hash=runner._hash(binary), spike_binary_hash=runner._hash(b'SYNTHETIC Spike'), image_package_hash=runner._hash(b'SYNTHETIC packages'))
    env['tools']=[{'name':'verilator','version':'SYNTHETIC Verilator'}, {'name':'riscv-gcc','version':'SYNTHETIC compiler'},
        {'name':'spike-revision','version':runner.REFERENCE_REVISION}, {'name':'cocotb','version':'2.0.1'}, {'name':'python','version':platform.python_version()}]
    save(directory/'environment.json', env)
    result = json.loads((directory/'result.json').read_bytes())
    result['identity']['environment_hash'] = runner._hash(runner._json(env))
    result['tool_versions']=env['tools']
    result['artifacts'] = [dict(artifact_id='artifact-'+str(i),path=p.relative_to(directory).as_posix(),
        sha256=runner._hash(p.read_bytes()), size_bytes=p.stat().st_size, media_type='application/octet-stream')
        for i,p in enumerate(sorted(p for p in directory.rglob('*') if p.is_file() and p.name!='result.json'))]
    result['usage']['artifact_bytes'] = sum(a['size_bytes'] for a in result['artifacts'])
    save(directory/'result.json',result)
    save(directory/'saved-origin.json',dict(schema_version='riscv.ci-origin.v1', source_commit='a'*40,
        run_id='123',run_attempt='1',case='control',attempt_id=result['attempt_id'],result_sha256=runner._hash((directory/'result.json').read_bytes())))
    return directory, RiscvExperiment.model_validate_json((directory/'experiment.json').read_bytes())


def test_trusted_original_program_is_captured_with_source_and_run_provenance(original):
    directory, experiment = original
    proof, program, env = runner.validate_saved_program(directory, experiment)
    assert set(program)=={'smoke.o','smoke.elf','smoke.bin'}
    assert all(data==(directory/name).read_bytes() for name,data in program.items())
    assert proof['original_run_identity']['source_commit']=='a'*40 and proof['original_run_identity']['run_id']=='123'
    assert proof['original_result_sha256']==runner._hash((directory/'result.json').read_bytes())
    assert proof['original_identity']['environment_hash']==runner._hash(runner._json(env))
    assert proof['original_attempt_id']==json.loads((directory/'result.json').read_bytes())['attempt_id']


@pytest.mark.parametrize('name',['smoke.o','smoke.elf','smoke.bin','experiment.json','profile.json','source-manifest.json',
    'checker-manifest.json','environment.json','retirement.json','comparison.json','dut/rvfi.json','reference/commit.log'])
def test_any_saved_program_or_proof_tampering_is_rejected_before_dispatch(original,monkeypatch,name):
    directory, experiment = original
    (directory/name).write_bytes((directory/name).read_bytes()+b' ')
    monkeypatch.setattr(runner.subprocess,'run',lambda *a,**k:pytest.fail('process during admission'))
    with pytest.raises((ValueError,OSError)):
        runner.validate_saved_program(directory,experiment)


@pytest.mark.parametrize('damage',['root_source','failed_result','wrong_fixture','new_budget','origin_path','missing_artifact','duplicate_artifact'])
def test_noneligible_originals_never_grant_replay(original,damage):
    directory, experiment = original
    if damage=='wrong_fixture':experiment=experiment.model_copy(update={'fixture_id':'picorv32_reg_write_bug'})
    elif damage=='new_budget':experiment=experiment.model_copy(update={'max_cycles':100001})
    elif damage=='origin_path':
        value=json.loads((directory/'saved-origin.json').read_bytes());value['source_commit']='C:/private';save(directory/'saved-origin.json',value)
    else:
        result=json.loads((directory/'result.json').read_bytes())
        if damage=='failed_result':result['verdict']='inconclusive'
        elif damage=='root_source':result['identity']['source_hash']='f'*64
        elif damage=='missing_artifact':result['artifacts']=[a for a in result['artifacts'] if a['path']!='smoke.o']
        else:result['artifacts'].append(result['artifacts'][0])
        save(directory/'result.json',result)
    with pytest.raises(ValueError):runner.validate_saved_program(directory,experiment)


@pytest.mark.parametrize('damage',['elf_header','object_header','binary','entry','segment_size'])
def test_elf_binary_layout_validation_is_pure_and_strict(original,damage):
    directory,_=original
    obj,elf,binary=[(directory/name).read_bytes() for name in ('smoke.o','smoke.elf','smoke.bin')]
    if damage=='elf_header':elf=b'BAD!'+elf[4:]
    elif damage=='object_header':obj=b'BAD!'+obj[4:]
    elif damage=='binary':binary=b'\0'*len(binary)
    elif damage=='entry':elf=elf[:24]+struct.pack('<I',runner.PLATFORM.entry+4)+elf[28:]
    else:elf=elf[:72]+struct.pack('<I',0xFFFFFFFF)+elf[76:]
    with pytest.raises(ValueError):runner._program_layout(obj,elf,binary)


@pytest.mark.parametrize('stop',[None,'cancelled','timeout','resource_limit','late','pre_cancel'])
def test_saved_execution_calls_only_reference_and_dut_never_compiler_tools(original,tmp_path,monkeypatch,stop):
    directory,experiment=original
    spike=tmp_path/'spike';spike.write_bytes(b'SYNTHETIC Spike')
    monkeypatch.setattr(runner.platform,'system',lambda:'Linux')
    lookups=[]
    def which(name):
        lookups.append(name)
        assert name in ('verilator','spike')
        return str(spike) if name=='spike' else 'SYNTHETIC verilator'
    monkeypatch.setattr(runner.shutil,'which',which)
    queried=[]
    def version(argv,**kwargs):
        queried.append(argv)
        assert argv==['verilator','--version']
        return SimpleNamespace(stdout='SYNTHETIC Verilator\n')
    monkeypatch.setattr(runner.subprocess,'run',version)
    monkeypatch.setattr(runner.importlib.metadata,'version',lambda _: '2.0.1')
    original_text,original_bytes=Path.read_text,Path.read_bytes
    def text(path,*a,**kw):
        if path.as_posix()=='/opt/spike/UPSTREAM_REVISION':return runner.REFERENCE_REVISION
        if path.as_posix()=='/opt/spike/UPSTREAM_SUBMODULES':return 'SYNTHETIC submodules'
        return original_text(path,*a,**kw)
    def data(path):
        return b'SYNTHETIC packages' if path.as_posix()=='/opt/veriforge/PACKAGE_VERSIONS' else original_bytes(path)
    monkeypatch.setattr(Path,'read_text',text);monkeypatch.setattr(Path,'read_bytes',data)
    temporary=runner.tempfile.TemporaryDirectory
    monkeypatch.setattr(runner.tempfile,'TemporaryDirectory',lambda **kw:temporary(dir=tmp_path,**kw))
    monkeypatch.setattr(runner,'assemble_command',lambda *_:pytest.fail('assembler recipe in replay'))
    monkeypatch.setattr(runner,'compile_command',lambda *_:pytest.fail('link recipe in replay'))
    calls=[]
    clock=[0.0];cancel=[stop=='pre_cancel']
    monkeypatch.setattr(runner.time,'monotonic',lambda:clock[0])
    def execute(argv,phase,env,seconds,cancel_event,limit):
        calls.append((argv,phase.name,seconds,limit))
        (phase/'execution.log').write_text('SYNTHETIC: no native process')
        if stop in ('cancelled','timeout','resource_limit','late'):
            if stop=='cancelled':cancel[0]=True
            if stop=='late':clock[0]=121.0
            return 0, stop if stop in ('timeout','resource_limit') else None
        if phase.name=='reference':(phase/'commit.log').write_bytes((directory/'reference/commit.log').read_bytes())
        else:
            assert phase.name=='dut'
            (phase/'rvfi.json').write_bytes((directory/'dut/rvfi.json').read_bytes())
            (phase/'built.marker').write_text('SYNTHETIC')
        return 0,None
    monkeypatch.setattr(runner,'_bounded',execute)
    output=tmp_path/'replay'
    before={p.relative_to(directory).as_posix():p.read_bytes() for p in directory.rglob('*') if p.is_file()}
    result=runner.run_experiment(experiment,output,saved_program=directory,cancel_event=SimpleNamespace(is_set=lambda:cancel[0]))
    if stop:
        assert result.verdict==('inconclusive' if stop in ('cancelled','pre_cancel') else 'timeout' if stop=='late' else stop)
        assert result.execution_status==('cancelled' if stop in ('cancelled','pre_cancel') else 'completed')
        assert result.failure_signature is None and len(calls)==(0 if stop=='pre_cancel' else 1)
        assert {p.relative_to(directory).as_posix():p.read_bytes() for p in directory.rglob('*') if p.is_file()}==before
        return
    assert result.execution_status=='completed' and result.verdict=='pass_within_scope'
    assert [p for _,p,_,_ in calls]==['reference','dut'] and queried==[['verilator','--version']]
    assert all(0<seconds<=experiment.wall_time_seconds and limit==32*1024**2 for _,_,seconds,limit in calls)
    assert not any((output/name).exists() for name in ('assembler','compiler','objcopy'))
    assert all((output/name).read_bytes()==before[name] for name in ('smoke.o','smoke.elf','smoke.bin'))
    env=json.loads((output/'environment.json').read_bytes())
    assert env['assembly_command']==env['compiler_command']==[] and env['program_mode']=='saved_program_no_compilation'
    assert result.identity.environment_hash!=json.loads(before['result.json'])['identity']['environment_hash']
    assert {p.relative_to(directory).as_posix():p.read_bytes() for p in directory.rglob('*') if p.is_file()}==before
    assert set(lookups)=={'verilator','spike'}
    spec=importlib.util.spec_from_file_location('saved_ci_consumer',ROOT/'scripts/ci_riscv_acceptance.py')
    ci=importlib.util.module_from_spec(spec);spec.loader.exec_module(ci)
    hashes={name:ci.digest((ROOT/name).read_bytes()) for name in ci.SOURCE_FILES}
    first=ci.validate_attempt(directory,hashes)
    second=ci.validate_attempt(output,hashes,saved=True)
    ci.validate_saved_replay(first,second)


def test_output_cannot_modify_original_before_admission(original):
    directory,experiment=original
    with pytest.raises(ValueError,match='disjoint'):
        runner.run_experiment(experiment,directory/'nested',saved_program=directory)
    assert not (directory/'nested').exists()


def test_oversized_dut_output_is_rejected_before_open():
    path=SimpleNamespace(stat=lambda:SimpleNamespace(st_size=16*1024**2+1,st_mode=0o100600),
        is_symlink=lambda:False,is_junction=lambda:False,
        open=lambda *a:pytest.fail('oversized DUT file was opened'))
    with pytest.raises(runner.TraceFormatError):
        runner._simulation_output(path,RiscvExperiment(experiment_id='synthetic-size-bound'))


def test_growing_file_uses_limited_read_and_rejects_changed_metadata(tmp_path,monkeypatch):
    path=tmp_path/'growing';path.write_bytes(b'1234')
    original=Path.open;requested=[]
    class Growing:
        def __init__(self,stream):self.stream=stream
        def __enter__(self):return self
        def __exit__(self,*args):self.stream.close()
        def fileno(self):return self.stream.fileno()
        def read(self,limit):
            requested.append(limit)
            data=self.stream.read(limit)
            with original(path,'ab') as append:append.write(b'5')
            return data
    monkeypatch.setattr(Path,'open',lambda target,mode='r',*a,**kw:
        Growing(original(target,mode,*a,**kw)) if target==path and mode=='rb' else original(target,mode,*a,**kw))
    with pytest.raises(ValueError,match='changed or exceeded'):
        runner._read_bounded(path,4)
    assert requested==[5]
