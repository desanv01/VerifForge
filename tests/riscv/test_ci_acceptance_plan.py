"""Pure CI gate regressions with clearly synthetic files; no Docker/tool execution."""
from copy import deepcopy
import importlib.util
import json
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
spec = importlib.util.spec_from_file_location('ci_riscv',ROOT/'scripts/ci_riscv_acceptance.py')
ci = importlib.util.module_from_spec(spec)
spec.loader.exec_module(ci)


def write(path,value):
    path.parent.mkdir(parents=True,exist_ok=True)
    path.write_text(json.dumps(value))


def synthetic_attempt(path,mutant=False):
    """Fakes retained files only to exercise transport gates, never real evidence."""
    source_hashes = {name:ci.digest((ROOT/name).read_bytes()) for name in ci.SOURCE_FILES}
    source = {name:source_hashes['targets/riscv/'+name] for name in ('upstream/picorv32.v','picorv32_top.sv','smoke.S','link.ld')}
    fixture = 'picorv32_reg_write_bug' if mutant else 'picorv32_rv32i'
    source.update(core_revision=ci.CORE_REVISION,fixture_id=fixture,profile_code_hash=source_hashes['src/veriforge/riscv/profile.py'])
    checker = {name:source_hashes['src/veriforge/riscv/'+name] for name in ('checker.py','trace.py','contracts.py')}
    environment = {key:source_hashes['src/veriforge/riscv/'+name] for key,name in (
        ('driver_hash','sim_test.py'),('job_hash','sim_job.py'),('memory_hash','memory.py'))}
    environment.update(reference_revision=ci.REFERENCE,elf_hash=ci.digest(b'synthetic ELF'),binary_hash=ci.digest(b'synthetic binary'))
    profile = dict(profile_id='picorv32-rv32i-v1',isa='RV32I',ram_base=0x80000000,ram_bytes=0x10000,
        entry=0x80000000,completion=0x8000ff00,completion_value=1,stack_top=0x8000fef0,endian='little')
    plan = dict(schema_version='riscv.v1',experiment_id='synthetic-native-mutant' if mutant else 'synthetic-native-control',
        fixture_id=fixture,max_cycles=100000,max_retired=256,wall_time_seconds=120.0,
        program_id='rv32i-smoke-v1',profile_id='picorv32-rv32i-v1')
    records = {'source-manifest':source,'checker-manifest':checker,'profile':profile,'experiment':plan,'environment':environment}
    for name,data in records.items(): write(path/(name+'.json'),data)
    identity = {field:ci.jhash(records[name]) for field,name in zip(ci.IDENTITIES,('source-manifest','checker-manifest','profile','experiment','environment'))}
    def event(order,value=0):
        return dict(schema_version='riscv.retirement.v1',hart=0,order=order,
            pc=0x80000000+4*order,instruction=(0x80010137,0xef010113,0xf011a023)[order],
            rd=2 if order<2 else 0,rd_value=value,trap=False,interrupt=False,memory=None,
            source_line=order+1,source_cycle=None)
    reference = [event(0,0x80010000),event(1,0x8000fef0),event(2)]
    reference[-1]['memory'] = dict(kind='write',address=0x8000ff00,width=4,byte_mask=15,value=1)
    observed = deepcopy(reference)
    for i,e in enumerate(observed):
        e.update(source_line=None,source_cycle=10*(i+1))
    if mutant:
        observed[1]['rd_value'] = 0x8000fef1
        observed[-1]['memory']['value'] = 3
    raw=[]
    for i,e in enumerate(observed):
        raw.append(dict(cycle=10*(i+1),rvfi_order=i,rvfi_insn=e['instruction'],rvfi_pc_rdata=e['pc'],
            rvfi_rd_addr=e['rd'],rvfi_rd_wdata=e['rd_value'],rvfi_rs1_rdata=0x80010000,
            rvfi_mem_addr=0x8000ff00 if i==2 else 0,rvfi_mem_rmask=0,rvfi_mem_wmask=15 if i==2 else 0,
            rvfi_mem_rdata=0,rvfi_mem_wdata=e['memory']['value'] if i==2 else 0,rvfi_trap=False,rvfi_intr=False))
    difference = dict(order=1,field='rd_value',expected=json.dumps(0x8000fef0),observed=json.dumps(0x8000fef1),requirement_id='RISCV_ARCH_RETIREMENT') if mutant else None
    signature = dict(requirement_id='RISCV_ARCH_RETIREMENT',failure_class='riscv.rd_value',
        first_cycle=20,expected=difference['expected'],observed=difference['observed'],
        signature_hash=ci.jhash(dict(checker=identity['checker_hash'],contract=identity['contract_hash'],field='rd_value'))) if mutant else None
    verdict = 'design_mismatch' if mutant else 'pass_within_scope'
    write(path/'retirement.json',{'schema_version':'riscv.retirement-pair.v1','reference':reference,'observed':observed})
    write(path/'comparison.json',dict(verdict=verdict,first_divergence=difference,compared=2 if mutant else 3,
        reason='first architectural divergence' if mutant else 'architectural events agree through completion; no cycle comparison'))
    write(path/'dut/rvfi.json',dict(termination='completion_store_retired',cycles=31,events=raw))
    (path/'smoke.elf').write_bytes(b'synthetic ELF')
    (path/'smoke.bin').write_bytes(b'synthetic binary')
    (path/'reference').mkdir()
    (path/'reference/commit.log').write_text(
        'core 0: 3 0x80000000 (0x80010137) x2 0x80010000\n'
        'core 0: 3 0x80000004 (0xef010113) x2 0x8000fef0\n'
        'core 0: 3 0x80000008 (0xf011a023) mem 0x8000ff00 0x00000001\n')
    (path/'dut/built.marker').write_text('SYNTHETIC MARKER; no RTL build')
    artifacts = [dict(artifact_id='artifact-'+str(i),path=name,sha256=ci.digest((path/name).read_bytes()),size_bytes=(path/name).stat().st_size,media_type='application/octet-stream') for i,name in enumerate(ci.REQUIRED) if name!='result.json']
    result = dict(schema_version='1',attempt_id='synthetic-native-unit',experiment_id=plan['experiment_id'],
        execution_status='completed',verdict=verdict,identity=identity,usage={'cycles':31,'wall_time_seconds':0.0,'artifact_bytes':0},
        failure_signature=signature,artifacts=artifacts)
    write(path/'result.json',result)
    return source_hashes


def test_synthetic_control_mutant_and_replay_gate(tmp_path):
    hashes = synthetic_attempt(tmp_path/'control')
    control = ci.validate_attempt(tmp_path/'control',hashes)
    synthetic_attempt(tmp_path/'mutant',True)
    mutant = ci.validate_attempt(tmp_path/'mutant',hashes,mutant=True)
    assert mutant['first_divergence']['order'] == 1
    synthetic_attempt(tmp_path/'replay')
    ci.validate_replay(control,ci.validate_attempt(tmp_path/'replay',hashes))


@pytest.mark.parametrize('status,verdict',[
    ('completed','unsupported'),('completed','inconclusive'),('completed','timeout'),
    ('completed','resource_limit'),('infrastructure_error','harness_error'),
    ('cancelled','inconclusive')])
def test_unavailable_or_incomplete_attempt_never_goes_green(tmp_path,status,verdict):
    hashes = synthetic_attempt(tmp_path)
    p=tmp_path/'result.json'
    result=json.loads(p.read_text());result.update(execution_status=status,verdict=verdict);write(p,result)
    with pytest.raises(ci.GateError,match='status/verdict'): ci.validate_attempt(tmp_path,hashes)


def test_tampered_artifact_and_source_fail_before_acceptance(tmp_path):
    hashes=synthetic_attempt(tmp_path)
    (tmp_path/'smoke.bin').write_bytes(b'tampered')
    with pytest.raises(ci.GateError,match='program hash'): ci.validate_attempt(tmp_path,hashes)


@pytest.mark.parametrize('field',['identity','pair_hash','elf_hash','binary_hash','cycles','compared'])
def test_exact_replay_rejects_each_identity_or_trace_difference(field):
    first=dict(identity={'source_hash':'a'*64},pair_hash='b'*64,elf_hash='c'*64,binary_hash='d'*64,cycles=50,compared=4)
    second=deepcopy(first);second[field]='different'
    with pytest.raises(ci.GateError,match='replay'): ci.validate_replay(first,second)


def test_manual_hosted_only_refuses_local_tool_dispatch(monkeypatch):
    monkeypatch.delenv('GITHUB_ACTIONS',raising=False)
    def forbidden(*a,**kw): pytest.fail('no subprocess allowed outside hosted manual job')
    monkeypatch.setattr(ci.subprocess,'run',forbidden)
    monkeypatch.setattr(ci.subprocess,'Popen',forbidden)
    with pytest.raises(ci.GateError,match='hosted manual'): ci.main()


def test_three_commands_keep_identity_paths_and_exact_isolation(tmp_path,monkeypatch):
    monkeypatch.setattr(ci.os,'getuid',lambda:1001,raising=False)
    monkeypatch.setattr(ci.os,'getgid',lambda:1001,raising=False)
    for label,plan in (('control','control.json'),('mutant','mutant.json'),('control-replay','control.json')):
        command=ci.run_command('sha256:'+'a'*64,tmp_path/'public',tmp_path/label,label,plan)
        assert command[-2:]==['/workspace/targets/riscv/'+plan,'/output']
        for flag,value in (('--network','none'),('--cpus','2'),('--memory','2g'),('--pids-limit','128'),('--user','1001:1001')):
            assert command[command.index(flag)+1]==value
        assert '--read-only' in command and '--cap-drop' in command
        mounts=[command[i+1] for i,arg in enumerate(command) if arg=='--mount']
        assert len(mounts)==3 and mounts[0].endswith('/workspace/src,readonly') and mounts[1].endswith('/workspace/targets/riscv,readonly')
        assert not any('docker.sock' in arg or 'dst=/workspace,' in arg for arg in command)


def test_public_source_stage_and_export_exclude_operational_state(tmp_path):
    hashes=ci.stage_sources(ROOT,tmp_path/'public')
    assert 'infra/riscv/build_image.py' not in hashes
    assert 'tests/riscv/test_build_supervision.py' not in hashes
    assert not any('.coordination' in name or name.endswith('.env') for name in hashes)
    readme=(tmp_path/'public/infra/riscv/README.md').read_text()
    assert '01a16' not in readme and '.coordination' not in readme
    work=tmp_path/'work';work.mkdir();write(work/'summary.json',{'accepted':False})
    (work/'.env').write_text('NEVER EXPORT')
    ci.export_evidence(work,tmp_path/'public',tmp_path/'evidence')
    assert not (tmp_path/'evidence/.env').exists()
    assert (tmp_path/'evidence/source/workflow/riscv-acceptance.yml').is_file()
    assert not any(part.startswith('.') for p in (tmp_path/'evidence').rglob('*') for part in p.relative_to(tmp_path/'evidence').parts)


def test_workflow_is_fixed_manual_bounded_and_has_no_registry_publish():
    text=(ROOT/'.github/workflows/riscv-acceptance.yml').read_text()
    assert 'workflow_dispatch:' in text and 'inputs:' not in text
    assert 'pull_request:' not in text and 'schedule:' not in text and 'push:' not in text
    assert 'timeout-minutes: 45' in text and 'contents: read' in text
    assert 'persist-credentials: false' in text and 'retention-days: 1' in text
    assert 'cf430e030ddbb5b0abf93d22962f4752f3646cd9' in text
    assert 'include-hidden-files: false' in text and 'ci-artifacts/riscv/' in text
