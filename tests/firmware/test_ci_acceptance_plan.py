"""Source/typed-artifact/fake-process checks only; no compiler, Docker or HDL."""
from copy import deepcopy
import importlib.util
import json
from pathlib import Path
import struct
from types import SimpleNamespace

import pytest

ROOT=Path(__file__).resolve().parents[2]
spec=importlib.util.spec_from_file_location('firmware_ci',ROOT/'scripts/ci_firmware_acceptance.py')
ci=importlib.util.module_from_spec(spec);spec.loader.exec_module(ci)


def synthetic_elf(words):
    """Handwritten ELF32 load/section bytes, never compiled executable evidence."""
    code=b''.join(word.to_bytes(4,'little') for word in words)
    data=bytearray(592)
    struct.pack_into('<16sHHIIIIIHHHHHH',data,0,b'\x7fELF\x01\x01\x01'+b'\0'*9,
        2,243,1,0x80000000,52,512,0,52,32,1,40,2,0)
    struct.pack_into('<IIIIIIII',data,52,1,256,0x80000000,0x80000000,len(code),len(code),5,4)
    data[256:256+len(code)]=code
    struct.pack_into('<IIIIIIIIII',data,552,0,1,6,0x80000000,256,len(code),0,0,4,0)
    return bytes(data),code


def test_seven_fixed_commands_and_simulator_share_one_deadline(tmp_path):
    calls=[]
    def invoke(command,out,err,deadline,**kw):
        calls.append((list(command),deadline));return dict(argv=list(command),returncode=0,wall_seconds=0.0)
    validation=[]
    result=ci.execute_steps(tmp_path,111.0,False,invoke=invoke,validate=lambda p:validation.append((p,len(calls))))
    assert tuple(row['phase'] for row in result)==ci.PHASES+('simulator',)
    assert all(deadline==111.0 for _,deadline in calls)
    assert validation==[(tmp_path,7)]
    assert calls[-1][0][1:3]==['-m','veriforge.firmware.sim_job']
    assert '-c' in calls[0][0] and '-c' in calls[1][0] and '-c' in calls[2][0]
    assert all(str(tmp_path/name) in calls[3][0] for name in ('start.o','independent_probe.o','main.o'))


def test_saved_replay_never_invokes_gcc_or_binutils(tmp_path):
    calls=[]
    def invoke(command,*a,**kw): calls.append(list(command));return dict(argv=list(command),returncode=0,wall_seconds=0.0)
    result=ci.execute_steps(tmp_path,111.0,True,invoke=invoke,validate=lambda p:None)
    assert tuple(r['phase'] for r in result)==('simulator',)
    assert len(calls)==1 and not any('gcc' in arg or 'objdump' in arg or 'readelf' in arg or 'objcopy' in arg for arg in calls[0])


def test_failed_compile_records_first_cause_and_never_simulates(tmp_path):
    calls=[];records=[]
    def invoke(command,*a,**kw):
        calls.append(command)
        if len(calls)==3: raise ci.GateError('synthetic exit 7')
        return dict(argv=list(command),returncode=0,wall_seconds=0.0)
    with pytest.raises(ci.GateError,match='compile_main: synthetic exit 7'):
        ci.execute_steps(tmp_path,111.0,False,invoke=invoke,validate=lambda p:pytest.fail('no validation after failure'),records=records)
    assert len(calls)==3 and records[-1]['returncode'] is None and records[-1]['error']=='synthetic exit 7'


def test_invalid_executable_never_reaches_simulator(tmp_path):
    def invalid(path): raise ValueError('invalid ELF/ISA')
    with pytest.raises(ValueError,match='ELF/ISA'):
        ci.execute_steps(tmp_path,111.0,True,invoke=lambda *a,**kw:pytest.fail('must validate first'),validate=invalid)


@pytest.mark.parametrize('cleanup_failure',[False,True])
def test_timeout_kills_only_owned_process_even_if_container_stop_fails(tmp_path,monkeypatch,cleanup_failure):
    calls=[];child=SimpleNamespace(pid=4242,returncode=None)
    child.poll=lambda:child.returncode
    child.wait=lambda **kw:calls.append('wait')
    def kill(pid,sig): calls.append(('kill',pid));child.returncode=-9
    def settle():
        calls.append('settle')
        if cleanup_failure: raise ci.GateError('synthetic cleanup failure')
    ticks=iter([0.0,0.0,2.0])
    monkeypatch.setattr(ci,'time',SimpleNamespace(monotonic=lambda:next(ticks,2.0),sleep=lambda n:pytest.fail('no wait')))
    monkeypatch.setattr(ci.subprocess,'Popen',lambda *a,**kw:child)
    monkeypatch.setattr(ci.os,'killpg',kill,raising=False)
    monkeypatch.setattr(ci.signal,'SIGKILL',9,raising=False)
    with pytest.raises(ci.GateError,match='timeout') as error:
        ci.run_process(['SYNTHETIC_NO_EXECUTION'],tmp_path/'out',tmp_path/'err',1.0,root=tmp_path,cap=4096,settle=settle)
    assert calls==['settle',('kill',4242),'wait']
    if cleanup_failure: assert error.value.cleanup_error=='synthetic cleanup failure'


@pytest.mark.parametrize('finished',[.5,1.0,2.0])
def test_immediate_zero_exit_must_be_observed_before_deadline(tmp_path,monkeypatch,finished):
    """A successful exit cannot bypass the loop's deadline through a poll race."""
    ticks=iter([0.0,0.0,finished])
    monkeypatch.setattr(ci,'time',SimpleNamespace(monotonic=lambda:next(ticks),sleep=lambda n:pytest.fail('no wait')))
    child=SimpleNamespace(returncode=0,poll=lambda:0)
    monkeypatch.setattr(ci.subprocess,'Popen',lambda *a,**kw:child)
    invoke=lambda:ci.run_process(['SYNTHETIC_NO_EXECUTION'],tmp_path/'out',tmp_path/'err',1.0,root=tmp_path,cap=4096)
    if finished>=1.0:
        with pytest.raises(ci.GateError,match='timeout'): invoke()
    else: assert invoke()['wall_seconds']==.5


@pytest.mark.parametrize('word',[0x13,0x80010137,0xef010113,0x0020a023,0x0000a103,0x002080a3,0x0000006f])
def test_supported_integer_instruction_encodings(word): ci.isa32(word)


@pytest.mark.parametrize('word',[0,0x02000033,0x00000073,0xc0002073,0x0000100f])
def test_compressed_m_system_and_nonprofile_extensions_rejected(word):
    with pytest.raises(ci.GateError): ci.isa32(word)


@pytest.fixture
def prepared_synthetic_elf(tmp_path):
    from veriforge.firmware.executable import FirmwarePreparation,prepare_executable_case
    prepare_executable_case(FirmwarePreparation(fixture_id='fw-regmap-control-v1'),tmp_path)
    elf,code=synthetic_elf([0x13])
    (tmp_path/'firmware.elf').write_bytes(elf);(tmp_path/'firmware.bin').write_bytes(code)
    (tmp_path/'disassembly.txt').write_text('80000000: 00000013 addi x0,x0,0\n')
    (tmp_path/'elf_headers.txt').write_text('Tag_RISCV_arch: "rv32i2p1"\n')
    return tmp_path


def test_native_elf_and_disassembly_encodings_are_corroborated(prepared_synthetic_elf):
    _,ids,words=ci.validate_program(prepared_synthetic_elf)
    assert words=={0x80000000:0x13} and ids['binary_sha256']==ci.sha(b'\x13\0\0\0')


@pytest.mark.parametrize('corruption',['encoding','coverage','attributes','isa','binary'])
def test_native_executable_corruption_fails_closed(prepared_synthetic_elf,corruption):
    root=prepared_synthetic_elf
    if corruption=='encoding': (root/'disassembly.txt').write_text('80000000: 00000093 addi x1,x0,0\n')
    if corruption in ('coverage','isa'):
        elf,code=synthetic_elf([0x13,0x13] if corruption=='coverage' else [0x02000033])
        (root/'firmware.elf').write_bytes(elf);(root/'firmware.bin').write_bytes(code)
        if corruption=='isa': (root/'disassembly.txt').write_text('80000000: 02000033 mul x0,x0,x0\n')
    if corruption=='attributes': (root/'elf_headers.txt').write_text('Tag_RISCV_arch: "rv32im2p1"\n')
    if corruption=='binary': (root/'firmware.bin').write_bytes(b'tampered')
    with pytest.raises((ci.GateError,ValueError)): ci.validate_program(root)


def native_unit_observation(bad=False):
    """Independent hand-derived bus/RVFI units, never produced by a simulator."""
    operations=[(0x40000000,15,0x11223344),(0x40000000,0,0x11223344),
        (0x40000004,15,0x55667788),(0x40000004,0,0x55667788),
        (0x40000008,15,0xaabbccdd),(0x40000008,2,0x1100),(0x40000008,0,0xaabb11dd),
        (0x4000000c,15,0x13579bdf),(0x4000000c,0,0x13579bdf)]
    if bad: operations=operations[:2]+[(0x40000008,15,0x55667788),(0x40000004,0,0)]
    operations.append((0x8000ff00,15,2 if bad else 1))
    events=[];transfers=[]
    for i,(address,strobes,value) in enumerate(operations):
        cycle=6+3*i
        transfers.append(dict(sequence=i,cycle=cycle,address=address,instruction=False,write_strobes=strobes,
            write_data=value if strobes else 0,read_data=0 if strobes else value))
        events.append(dict(cycle=cycle+1,rvfi_order=i,rvfi_insn=0x002080a3 if strobes==2 else 0x0020a023 if strobes else 0x0000a103,
            rvfi_pc_rdata=0x80000000+4*i,rvfi_rd_addr=0 if strobes else 2,rvfi_rd_wdata=0 if strobes else value,rvfi_rs1_rdata=address,
            rvfi_mem_addr=address,rvfi_mem_rmask=0 if strobes else 15,rvfi_mem_wmask=strobes,rvfi_mem_wdata=value if strobes else 0,
            rvfi_mem_rdata=0 if strobes else value,rvfi_trap=False,rvfi_intr=False))
    return dict(schema_version='firmware.native-observation.v1',termination='completion_store_retired',
        cycles=events[-1]['cycle']+1,events=events,transfers=transfers)


def full_native_unit_case(tmp_path,label,monkeypatch):
    """Native-schema/byte-corroboration fixture; all command records are synthetic."""
    from veriforge.firmware.executable import FirmwarePreparation,prepare_executable_case,commands
    import sys
    case=tmp_path/'unit-case';snapshot=tmp_path/'independent-snapshot'
    monkeypatch.setattr(ci,'SNAPSHOT',snapshot)
    prepare_executable_case(FirmwarePreparation(fixture_id=ci.FIXTURES[label]),case)
    preparation=ci.get_json(case/'preparation.json')
    preparation['recipes']=commands(snapshot)
    ci.write_json(case/'preparation.json',preparation)
    observation=native_unit_observation(label=='bad')
    words=[row['rvfi_insn'] for row in observation['events']]
    elf,code=synthetic_elf(words)
    (case/'firmware.elf').write_bytes(elf);(case/'firmware.bin').write_bytes(code)
    for name in ('start.o','independent_probe.o','main.o'): (case/name).write_bytes(b'SYNTHETIC UNIT OBJECT; NOT ASSEMBLED')
    (case/'firmware.map').write_text('SYNTHETIC UNIT MAP; NO LINKER RAN')
    (case/'disassembly.txt').write_text(''.join(f'{0x80000000+4*i:08x}: {word:08x} addi x0,x0,0\n' for i,word in enumerate(words)))
    (case/'elf_headers.txt').write_text('Tag_RISCV_arch: "rv32i2p1"\n')
    ci.write_json(case/'observation.json',observation)
    plan,ids,_=ci.validate_program(case)
    ci.write_json(case/'execution-identities.json',ids)
    ci.write_json(case/'simulation-job.json',dict(preparation=plan.model_dump(mode='json'),binary=str(snapshot/'firmware.bin'),trace=str(snapshot/'observation.json')))
    argv=commands(snapshot);argv['simulator']=(sys.executable,'-m','veriforge.firmware.sim_job',str(snapshot))
    ci.write_json(case/'execution.json',dict(schema_version='firmware.ci-execution.v1',fixture_id=plan.fixture_id,
        completed=True,replay=False,wall_seconds=0.0,commands=[dict(phase=p,argv=list(c),returncode=0,wall_seconds=0.0) for p,c in argv.items()],
        files={name:ci.sha((case/name).read_bytes()) for name in ci.CASE_FILES if (case/name).is_file()}))
    hashes={name:ci.sha((ROOT/name).read_bytes()) for name in ci.SOURCE_FILES}
    return case,hashes


@pytest.mark.parametrize('label',['control','bad','candidate'])
def test_full_typed_native_postprocessor_has_expected_bounded_outcomes(tmp_path,monkeypatch,label):
    case,hashes=full_native_unit_case(tmp_path,label,monkeypatch)
    report=ci.check_case(case,label,hashes,{'image_id':'synthetic-unit-image; never executed'})
    assert report['check']['verdict']==('design_mismatch' if label=='bad' else 'pass_within_scope')
    assert (report['first_conflict'] or {}).get('mmio_sequence')==(2 if label=='bad' else None)
    if label=='bad':
        assert report['failure_signature']['failure_class']=='firmware.status_offset'
        assert report['failure_signature']['first_cycle']==report['first_conflict']['cycle']
    else: assert report['failure_signature'] is None


@pytest.mark.parametrize('mutation',['native','isa','commands','source'])
def test_rebound_checksums_do_not_bless_wrong_native_postprocessor_evidence(tmp_path,monkeypatch,mutation):
    case,hashes=full_native_unit_case(tmp_path,'control',monkeypatch)
    if mutation in ('native','isa'):
        raw=ci.get_json(case/'observation.json')
        if mutation=='native': raw['transfers'][2]['address']=0x40000008
        else: raw['events'][0]['rvfi_insn']=0x02000033
        ci.write_json(case/'observation.json',raw)
    if mutation=='source': (case/'registers.h').write_text('CHANGED CURATED SOURCE')
    record=ci.get_json(case/'execution.json')
    if mutation=='commands': record['commands'][0]['argv']=['UNTRUSTED COMMAND']
    for name in record['files']: record['files'][name]=ci.sha((case/name).read_bytes())
    ci.write_json(case/'execution.json',record)
    with pytest.raises((ci.GateError,ValueError)):
        ci.check_case(case,'control',hashes,{'image_id':'synthetic-unit-image; never executed'})


def summaries():
    """Clearly synthetic acceptance summaries; no process or HDL observation."""
    cases={}
    for label in ci.LABELS:
        original=label.removesuffix('-replay');bad=original=='bad'
        cases[label]=dict(identity=dict(source_hash=original,experiment_hash=original,checker_hash='checker',contract_hash='contract',environment_hash='tools'),
            selected_header_hash='bad' if bad else 'correct',original_bad_hash='bad',candidate_diff_sha256='diff' if original=='candidate' else None,
            program_hashes={'firmware.bin':'bad-bin' if bad else 'good-bin'},observation_sha256='bad-trace' if bad else 'good-trace',
            semantic_hash='bad-trace' if bad else 'good-trace',check={'verdict':'design_mismatch' if bad else 'pass_within_scope'},
            first_conflict={'mmio_sequence':2} if bad else None,failure_signature={'class':'synthetic-offset'} if bad else None,
            cycles=50 if bad else 100,execution_sha256='execution-'+label,
            replay_original_hash='execution-'+original if label.endswith('-replay') else None)
    return cases


def test_all_five_cases_and_saved_binary_replay_are_required(): ci.compare_cases(summaries())


@pytest.mark.parametrize('mutation',['missing','checker','binary','trace','bad_base','replay_base','candidate','unchanged_defect_binary'])
def test_skips_or_relaxed_repair_replay_identity_cannot_pass(mutation):
    cases=summaries()
    if mutation=='missing': cases.pop('bad-replay')
    if mutation=='checker': cases['candidate']['identity']['checker_hash']='changed'
    if mutation=='binary': cases['bad-replay']['program_hashes']['firmware.bin']='new-build'
    if mutation=='trace': cases['candidate-replay']['semantic_hash']='changed'
    if mutation=='bad_base': cases['candidate']['original_bad_hash']='different-base'
    if mutation=='replay_base': cases['bad-replay']['replay_original_hash']='different-execution'
    if mutation=='candidate': cases['candidate']['program_hashes']['firmware.bin']='different-control-program'
    if mutation=='unchanged_defect_binary':
        cases['bad']['program_hashes']['firmware.bin']='good-bin'
        cases['bad-replay']['program_hashes']['firmware.bin']='good-bin'
    with pytest.raises(ci.GateError): ci.compare_cases(cases)


def test_hosted_guard_prevents_any_local_tool_execution(monkeypatch):
    monkeypatch.delenv('GITHUB_ACTIONS',raising=False)
    def forbidden(*a,**kw): pytest.fail('no local subprocess')
    monkeypatch.setattr(ci.subprocess,'run',forbidden);monkeypatch.setattr(ci.subprocess,'Popen',forbidden)
    with pytest.raises(ci.GateError,match='hosted manual'): ci.main()


def test_public_closure_and_export_never_include_private_or_build_files(tmp_path):
    assert len(set(ci.SOURCE_FILES))==len(ci.SOURCE_FILES)
    assert 'src/veriforge/riscv/runner.py' not in ci.SOURCE_FILES and 'scripts/ci_riscv_acceptance.py' not in ci.SOURCE_FILES
    hashes=ci.stage_sources(tmp_path/'public');assert hashes['targets/riscv/upstream/picorv32.v']==ci.CORE
    assert not any('.coordination' in p or p.endswith('.env') for p in hashes)
    work=tmp_path/'work';work.mkdir();ci.write_json(work/'summary.json',dict(accepted=False))
    (work/'.env').write_text('PRIVATE');(work/'image.tar').write_text('NO EXPORT')
    (work/'control/build').mkdir(parents=True);(work/'control/build/generated.cpp').write_text('NO BUILD EXPORT')
    ci.export_evidence(work,tmp_path/'public',tmp_path/'evidence')
    exported=[p.relative_to(tmp_path/'evidence').as_posix() for p in (tmp_path/'evidence').rglob('*') if p.is_file()]
    assert not any('.env' in p or '/build/' in p or 'image.tar' in p for p in exported)
    assert (tmp_path/'evidence/source/workflow/firmware-acceptance.yml').is_file()


def test_commands_mount_only_curated_source_saved_inputs_and_owned_output(tmp_path,monkeypatch):
    monkeypatch.setattr(ci.os,'getuid',lambda:1001,raising=False);monkeypatch.setattr(ci.os,'getgid',lambda:1001,raising=False)
    cmd=ci.container_command('sha256:'+'a'*64,tmp_path/'public',tmp_path/'output','bad-replay','--worker-bad-replay',saved=tmp_path/'bad')
    assert cmd[-1]=='--worker-bad-replay' and '--network' in cmd and '--read-only' in cmd
    assert cmd[cmd.index('--memory')+1]=='2g' and cmd[cmd.index('--pids-limit')+1]=='128'
    mounts=[cmd[i+1] for i,v in enumerate(cmd) if v=='--mount']
    assert len(mounts)==5 and mounts[-1].endswith('dst=/saved,readonly')
    assert not any('docker.sock' in p or 'dst=/workspace,' in p for p in mounts)
    bundle=tmp_path/'finite-public-bundle'
    pure=ci.pure_command('sha256:'+'a'*64,bundle)
    pure_mounts=[pure[i+1] for i,v in enumerate(pure) if v=='--mount']
    assert pure_mounts==[f'type=bind,src={bundle},dst=/workspace,readonly']
    assert '/workspace/tests/firmware' in pure and 'pytest' in pure


def test_workflow_is_manual_fixed_bounded_and_never_publishes_image():
    text=(ROOT/'.github/workflows/firmware-acceptance.yml').read_text()
    assert 'workflow_dispatch:' in text and 'inputs:' not in text and 'schedule:' not in text and 'pull_request:' not in text
    assert 'timeout-minutes: 60' in text and 'contents: read' in text and 'persist-credentials: false' in text
    assert 'retention-days: 1' in text and 'include-hidden-files: false' in text
    assert 'cf430e030ddbb5b0abf93d22962f4752f3646cd9' in text
