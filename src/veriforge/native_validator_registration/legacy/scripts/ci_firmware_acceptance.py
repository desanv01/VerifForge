"""Fixed firmware hosted gate. No uploaded commands, local builds or Spike MMIO."""
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import signal
import struct
import subprocess
import sys
import tempfile
import time

ROOT = Path(__file__).resolve().parents[1]
SNAPSHOT = Path('/tmp/firmware-snapshot')
BUILDER = 'veriforge-firmware-ci'
BUILDKIT = 'moby/buildkit:buildx-stable-1@sha256:cec9f139f45e93c5c69c60f8b07cfad9f43f4ef6b6a6cd917527fea5ff2e3dea'
CORE = '0836050971b3c6cdd28ac3b1e5719a67fb645161912bef1e472e63995ceb0622'
REFERENCE = '4c6a9bc79dab80616932949707e7c0471d8e9e54'
CAP = 32*1024**2
CASE_CAP = 4*1024**2
CASE_SECONDS = 120.0
PHASES = ('assemble_start','assemble_probe','compile_main','compiler','binary','disassembly','elf_headers')
LABELS = ('control','bad','candidate','bad-replay','candidate-replay')
FIXTURES = {'control':'fw-regmap-control-v1','bad':'fw-regmap-offset-bug-v1','candidate':'fw-regmap-offset-candidate-v1',
    'bad-replay':'fw-regmap-offset-bug-v1','candidate-replay':'fw-regmap-offset-candidate-v1'}
SOURCE_FILES = (
    'scripts/ci_firmware_acceptance.py','.github/workflows/firmware-acceptance.yml','tests/firmware/test_ci_acceptance_plan.py',
    'infra/riscv/Dockerfile','infra/riscv/README.md','pyproject.toml','uv.lock','docs/project/FIRMWARE.md',
    'src/veriforge/__init__.py','src/veriforge/contracts/__init__.py',
    'src/veriforge/firmware/__init__.py','src/veriforge/firmware/contracts.py','src/veriforge/firmware/executable.py',
    'src/veriforge/firmware/extract.py','src/veriforge/firmware/harness.py','src/veriforge/firmware/workflow.py',
    'src/veriforge/firmware/sim_job.py','src/veriforge/firmware/sim_test.py',
    'src/veriforge/riscv/__init__.py','src/veriforge/riscv/checker.py','src/veriforge/riscv/contracts.py',
    'src/veriforge/riscv/memory.py','src/veriforge/riscv/profile.py','src/veriforge/riscv/trace.py',
    'targets/riscv/upstream/picorv32.v','targets/riscv/upstream/SPIKE-LICENSE','targets/riscv/upstream.json','targets/riscv/picorv32_top.sv',
    'targets/firmware/executable/start.S','targets/firmware/executable/independent_probe.S','targets/firmware/executable/main.c',
    'targets/firmware/executable/link.ld','targets/firmware/executable/native_registers.sv','targets/firmware/executable/firmware_top.sv',
    'targets/firmware/executable/register-contract.json','targets/firmware/executable/control/registers.h',
    'targets/firmware/executable/offset_bug/registers.h','targets/firmware/executable/candidate/registers.h',
    'targets/firmware/executable/README.md','targets/firmware/executable/PROVENANCE.md',
    'targets/firmware/example.disasm','targets/firmware/mmio.json','targets/firmware/register-map.json',
    'targets/firmware/registers.h','targets/firmware/registers.sv','targets/firmware/README.md',
    'tests/firmware/test_executable.py','tests/firmware/test_facts.py','tests/firmware/test_harness.py',
    'tests/acceptance/test_firmware_harness_review.py',
)
PREPARED = ('start.S','independent_probe.S','main.c','link.ld','native_registers.sv','firmware_top.sv',
    'picorv32.v','picorv32_top.sv','registers.h','original-bad-registers.h','register-contract.json','address-check.json','preparation.json')
PROGRAM = ('start.o','independent_probe.o','main.o','firmware.elf','firmware.bin','firmware.map','disassembly.txt','elf_headers.txt')
CASE_FILES = PREPARED+PROGRAM+('candidate.diff','observation.json','simulation-job.json','execution-identities.json','execution.json','results.xml','test.result.xml')
LOG_FILES = tuple('logs/'+p+suffix for p in PHASES+('simulator',) for suffix in ('.stdout.log','.stderr.log'))
HOST_FILES = ('summary.json','commands.json','source-manifest.json','host.json','image-id.txt','image-inspect.json',
    'build-metadata.json','tool-metadata.json','build.log','build.stderr.log','metadata.log','metadata.stderr.log',
    'pure.log','pure.stderr.log','builder-before.log','builder-create.log','builder-stop.log','builder-state.log','builder-caps.json')
CANCELLED = False


class GateError(RuntimeError):
    pass


def require(value,message):
    if not value: raise GateError(message)


def sha(data): return hashlib.sha256(data).hexdigest()


def canonical(value):
    return sha(json.dumps(value,sort_keys=True,separators=(',',':'),ensure_ascii=False,allow_nan=False).encode())


def get_json(path,cap=CASE_CAP):
    require(path.is_file() and not path.is_symlink() and path.stat().st_size<=cap,'missing/nonregular/oversized '+path.name)
    return json.loads(path.read_text())


def write_json(path,value):
    path.parent.mkdir(parents=True,exist_ok=True)
    path.write_text(json.dumps(value,sort_keys=True,indent=2))


def environment(worker=False):
    result={k:os.environ[k] for k in ('PATH','HOME','LANG','TMPDIR') if k in os.environ}
    if worker: result.update(HOME='/tmp',TMPDIR='/tmp',PYTHONPATH=str(ROOT/'src'),PYTHONDONTWRITEBYTECODE='1',CCACHE_DISABLE='1')
    return result


def bytes_under(root):
    return sum(p.stat().st_size for p in root.rglob('*') if p.is_file() and not p.is_symlink() and 'build' not in p.relative_to(root).parts)


def run_process(command,stdout,stderr,deadline,*,root,cap,host=False,settle=None):
    """One bounded process-tree supervisor, reused by all fixed host/worker steps."""
    require(not CANCELLED and time.monotonic()<deadline,'cancelled/exhausted aggregate deadline')
    stdout.parent.mkdir(parents=True,exist_ok=True)
    started=time.monotonic()
    primary_error=None
    with stdout.open('wb') as out,stderr.open('wb') as err:
        process=subprocess.Popen(list(command),env=environment(worker=not host),stdout=out,stderr=err,start_new_session=os.name!='nt')
        try:
            while process.poll() is None:
                reason=None
                if CANCELLED: reason='cancelled'
                elif time.monotonic()>=deadline: reason='timeout'
                elif stdout.stat().st_size>cap or stderr.stat().st_size>cap or bytes_under(root)>cap: reason='evidence_limit'
                elif host:
                    available=int(next(s.split()[1] for s in Path('/proc/meminfo').read_text().splitlines() if s.startswith('MemAvailable:')))*1024
                    if available<1024**3: reason='memory_limit'
                    elif shutil.disk_usage(root).free<2*1024**3: reason='disk_limit'
                if reason: raise GateError(reason)
                time.sleep(.2)
            require(process.returncode==0,'process exit '+str(process.returncode))
            require(bytes_under(root)<=cap,'evidence_limit')
            finished=time.monotonic()
            require(not CANCELLED,'cancelled')
            require(finished<deadline,'timeout')
        except Exception as exc:
            primary_error=exc
            raise
        finally:
            if process.poll() is None:
                try:
                    try:
                        if settle: settle()
                    except Exception as cleanup:
                        if primary_error is None: raise
                        primary_error.cleanup_error=str(cleanup)
                        primary_error.add_note('owned cleanup also failed: '+str(cleanup))
                finally:
                    try: os.killpg(process.pid,signal.SIGKILL)
                    except ProcessLookupError: pass
                    process.wait(timeout=5)
    return {'argv':list(command),'returncode':process.returncode,'wall_seconds':finished-started}


def runtime():
    sys.path.insert(0,str(ROOT/'src'))
    from veriforge.firmware.executable import FirmwarePreparation,commands,prepare_executable_case
    from veriforge.firmware.sim_job import validate_snapshot
    from veriforge.firmware.harness import HarnessOutput,check_observation
    return FirmwarePreparation,commands,prepare_executable_case,validate_snapshot,HarnessOutput,check_observation


def isa32(instruction):
    op,f3,f7=instruction&127,(instruction>>12)&7,instruction>>25
    require(instruction&3==3,'non-RV32I instruction width')
    valid=(op in (0x37,0x17,0x6f) or op==0x67 and f3==0 or op==0x63 and f3 in (0,1,4,5,6,7)
        or op==3 and f3 in (0,1,2,4,5) or op==0x23 and f3 in (0,1,2)
        or op==0x13 and (f3 not in (1,5) or f7==0 or f3==5 and f7==32)
        or op==0x33 and (f7==0 or f7==32 and f3 in (0,5)))
    require(valid,'instruction outside frozen integer ALU/branch/load/store RV32I subset')


def executable_words(data):
    """Require complete executable-section byte coverage, not e_flags alone."""
    require(len(data)>=52 and data[:7]==b'\x7fELF\x01\x01\x01','ELF32 little-endian required')
    h=struct.unpack_from('<16sHHIIIIIHHHHHH',data)
    offset,size,count=h[6],h[11],h[12]
    require(0<count<=128 and size==40 and offset>=52 and offset+size*count<=len(data),'missing/invalid ELF section table')
    words={}
    for i in range(count):
        row=struct.unpack_from('<IIIIIIIIII',data,offset+i*size)
        _,kind,flags,address,fileoff,length,_,_,_,_=row
        if not flags&4: continue
        require(kind==1 and flags&2 and not flags&1 and address%4==0 and length%4==0 and fileoff+length<=len(data),'unsupported executable section')
        for j in range(0,length,4):
            pc=address+j
            require(pc not in words,'overlapping executable sections')
            words[pc]=int.from_bytes(data[fileoff+j:fileoff+j+4],'little')
            isa32(words[pc])
    require(words and len(words)<=4096,'empty/oversized executable ISA coverage')
    return words


def validate_program(root):
    _,_,_,validate_snapshot,_,_=runtime()
    from veriforge.firmware.extract import extract_elf,extract_disassembly,corroborate_instruction_bytes
    plan,identities=validate_snapshot(root)
    elf=(root/'firmware.elf').read_bytes()
    facts=extract_elf(elf,'firmware.elf')
    instructions=extract_disassembly((root/'disassembly.txt').read_bytes(),'disassembly.txt')
    corroborate_instruction_bytes(elf,facts,instructions)
    words=executable_words(elf)
    require({i.address:i.encoding for i in instructions}==words and all(i.width_bits==32 for i in instructions),'disassembly omits/changes executable instruction bytes')
    attributes=(root/'elf_headers.txt').read_text()
    require(re.findall(r'Tag_RISCV_arch:\s*"([^"]+)"',attributes)==['rv32i2p1'],'actual ELF architecture attributes differ from pinned RV32I toolchain')
    return plan,identities,words


def execute_steps(root,deadline,replay,invoke=run_process,validate=validate_program,records=None):
    _,commands,_,_,_,_=runtime()
    recipe=commands(root)
    require(tuple(recipe)==PHASES,'trusted seven-command preparation changed')
    records=[] if records is None else records
    def perform(phase,command,stdout):
        row={'phase':phase,'argv':list(command),'returncode':None}
        records.append(row)
        try: row.update(invoke(command,stdout,root/('logs/'+phase+'.stderr.log'),deadline,root=root,cap=CASE_CAP))
        except (GateError,OSError,subprocess.SubprocessError) as exc:
            row['error']=str(exc);raise GateError(phase+': '+str(exc)) from exc
    if not replay:
        for phase,command in recipe.items():
            stdout=root/('disassembly.txt' if phase=='disassembly' else 'elf_headers.txt' if phase=='elf_headers' else 'logs/'+phase+'.stdout.log')
            perform(phase,command,stdout)
    validate(root)  # Must happen before any actual simulator process.
    perform('simulator',(sys.executable,'-m','veriforge.firmware.sim_job',str(root)),root/'logs/simulator.stdout.log')
    return records


def copy_named(source,destination,names,required=False):
    for name in names:
        path=source/name
        if not path.exists():
            require(not required,'missing saved file '+name)
            continue
        require(path.is_file() and not path.is_symlink(),'nonregular saved file '+name)
        target=destination/name
        target.parent.mkdir(parents=True,exist_ok=True)
        shutil.copyfile(path,target)


def worker(label):
    require(label in LABELS,'unknown fixed firmware case')
    plan_type,_,prepare,_,_,_=runtime()
    plan=plan_type(fixture_id=FIXTURES[label])
    started=time.monotonic();deadline=started+plan.wall_time_seconds
    replay=label.endswith('-replay');record={'schema_version':'firmware.ci-execution.v1','completed':False,'fixture_id':plan.fixture_id,'replay':replay,'commands':[]}
    require(not SNAPSHOT.exists(),'fresh fixed snapshot required')
    try:
        if replay:
            original=get_json(Path('/saved/execution.json'))
            require(original['completed'] is True and original['replay'] is False and original['fixture_id']==plan.fixture_id,'saved original execution is not eligible')
            SNAPSHOT.mkdir()
            copy_named(Path('/saved'),SNAPSHOT,PREPARED+PROGRAM,required=True)
            copy_named(Path('/saved'),SNAPSHOT,('candidate.diff',))
            for name in PREPARED+PROGRAM:
                require(sha((SNAPSHOT/name).read_bytes())==original['files'][name],'saved executable/source changed')
            record['replay_original_hash']=sha(Path('/saved/execution.json').read_bytes())
        else: prepare(plan,SNAPSHOT)
        execute_steps(SNAPSHOT,deadline,replay,records=record['commands'])
        require(time.monotonic()<=deadline,'case total deadline exhausted')
        record['completed']=True
    except (OSError,ValueError,GateError,KeyError,subprocess.SubprocessError) as exc: record['error']=str(exc)[:2000]
    finally:
        record['wall_seconds']=time.monotonic()-started
        record['files']={name:sha((SNAPSHOT/name).read_bytes()) for name in CASE_FILES if (SNAPSHOT/name).is_file()}
        write_json(SNAPSHOT/'execution.json',record)
        copy_named(SNAPSHOT,Path('/attempt'),CASE_FILES+LOG_FILES)
    return 0 if record['completed'] else 1


def check_case(root,label,source_hashes,tools):
    """Independent postprocessor; never called by the observation producer."""
    plan_type,commands,prepare,_,output_type,checker=runtime()
    from veriforge.contracts import canonical_hash,FailureSignature
    from veriforge.firmware.contracts import RegisterContract
    require(label in LABELS,'unknown checker case')
    expected_plan=plan_type(fixture_id=FIXTURES[label])
    require(not SNAPSHOT.exists(),'fresh independent preparation required')
    prepare(expected_plan,SNAPSHOT)
    expected=get_json(SNAPSHOT/'preparation.json')
    preparation=get_json(root/'preparation.json')
    require(sha(Path(__file__).read_bytes())==source_hashes['scripts/ci_firmware_acceptance.py'],'loaded controller identity changed')
    require(preparation==expected,'saved preparation differs from exact curated sources/runtime/commands')
    for name,identity in expected['source_files'].items():
        require(sha((root/name).read_bytes())==identity['sha256'],'source snapshot differs')
    for name in ('contracts.py','executable.py','extract.py','workflow.py','harness.py','sim_job.py','sim_test.py'):
        require(sha((ROOT/'src/veriforge/firmware'/name).read_bytes())==source_hashes['src/veriforge/firmware/'+name],'loaded immutable firmware checker/runtime differs')
    for name in ('memory.py','profile.py','contracts.py','trace.py','checker.py'):
        require(sha((ROOT/'src/veriforge/riscv'/name).read_bytes())==source_hashes['src/veriforge/riscv/'+name],'loaded shared runtime differs')
    plan,identities,words=validate_program(root)
    require(plan==expected_plan,'plan/bounds changed')
    execution=get_json(root/'execution.json')
    replay=label.endswith('-replay')
    require(execution['schema_version']=='firmware.ci-execution.v1' and execution['completed'] is True and execution['fixture_id']==plan.fixture_id and execution['replay'] is replay,'missing completed actual case')
    require(type(execution['wall_seconds']) in (int,float) and 0<=execution['wall_seconds']<=CASE_SECONDS,'case wall budget exceeded')
    phases=('simulator',) if replay else PHASES+('simulator',)
    require(tuple(row['phase'] for row in execution['commands'])==phases,'missing/extra/recompiled execution phases')
    expected_commands=commands(SNAPSHOT)
    expected_commands['simulator']=(sys.executable,'-m','veriforge.firmware.sim_job',str(SNAPSHOT))
    for row in execution['commands']:
        require(type(row['returncode']) is int and row['returncode']==0 and row['argv']==list(expected_commands[row['phase']]),'actual command/returncode differs from fixed recipe')
    for name in PREPARED+PROGRAM+('observation.json','execution-identities.json','simulation-job.json'):
        require(sha((root/name).read_bytes())==execution['files'][name],'retained execution file hash differs')
    require(get_json(root/'execution-identities.json')==identities,'native ELF/binary/preparation identities differ')
    job=get_json(root/'simulation-job.json')
    require(job==dict(preparation=plan.model_dump(mode='json'),binary=str(SNAPSHOT/'firmware.bin'),trace=str(SNAPSHOT/'observation.json')),'simulator internal job changed')
    observation=output_type.model_validate(get_json(root/'observation.json'))
    for event in observation.events:
        require(words.get(event.rvfi_pc_rdata)==event.rvfi_insn,'retired instruction differs from actual ELF code')
    checked=checker(observation,plan)
    bad=label in ('bad','bad-replay')
    require(checked.execution_status=='completed' and checked.verdict==('design_mismatch' if bad else 'pass_within_scope'),'independent MMIO checker did not establish expected outcome')
    require(observation.termination=='completion_store_retired','missing retired completion')
    mailbox=observation.events[-1]
    require(mailbox.rvfi_mem_addr==0x8000ff00 and mailbox.rvfi_mem_wmask==15 and mailbox.rvfi_mem_wdata==(2 if bad else 1),'unexpected completion value')
    mmio=[b for b in observation.transfers if not b.instruction and 0x40000000<=b.address<0x40000010]
    first=None
    if bad:
        require(checked.first_mmio_sequence==2 and len(mmio)==4,'unexpected defect access sequence')
        third,probe=mmio[2:]
        require((third.address,third.write_strobes,third.write_data)==(0x40000008,15,0x55667788),'wrong actual first STATUS write')
        require((probe.address,probe.write_strobes,probe.read_data)==(0x40000004,0,0),'independent STATUS probe did not observe reset zero')
        first=dict(requirement_id='FIRMWARE_STATUS_ADDRESS',mmio_sequence=2,bus_sequence=third.sequence,cycle=third.cycle,
            expected_address=0x40000004,observed_address=third.address,expected_probe=0x55667788,observed_probe=probe.read_data)
    else: require(checked.first_mmio_sequence is None and len(mmio)==9,'missing control/neighbor/byte-lane accesses')
    contract=RegisterContract.model_validate(get_json(root/'register-contract.json'))
    require(canonical_hash(contract)==expected['contract_hash'],'independent contract identity differs')
    identity=dict(source_hash=canonical(preparation),checker_hash=canonical({k:source_hashes[k] for k in source_hashes if k.startswith('src/veriforge/firmware/') or k=='scripts/ci_firmware_acceptance.py'}),
        contract_hash=canonical([expected['contract_hash'],expected['profile_hash']]),experiment_hash=canonical_hash(plan),environment_hash=canonical(tools))
    signature=None
    if first:
        signature=FailureSignature(requirement_id=first['requirement_id'],failure_class='firmware.status_offset',first_cycle=first['cycle'],
            expected=json.dumps({'address':first['expected_address'],'probe':first['expected_probe']},sort_keys=True),
            observed=json.dumps({'address':first['observed_address'],'probe':first['observed_probe']},sort_keys=True),
            signature_hash=canonical({'checker':identity['checker_hash'],'contract':identity['contract_hash'],'requirement':first['requirement_id']})).model_dump(mode='json')
    return dict(identity=identity,program_hashes={name:sha((root/name).read_bytes()) for name in PROGRAM},
        observation_sha256=sha((root/'observation.json').read_bytes()),semantic_hash=canonical(observation.model_dump(mode='json')),
        check=checked.model_dump(mode='json'),first_conflict=first,failure_signature=signature,cycles=observation.cycles,
        selected_header_hash=expected['selected_header_hash'],original_bad_hash=expected['original_bad_hash'],
        candidate_diff_sha256=sha((root/'candidate.diff').read_bytes()) if (root/'candidate.diff').is_file() else None,
        replay_original_hash=execution.get('replay_original_hash'),execution_sha256=sha((root/'execution.json').read_bytes()),
        reference_support='Spike MMIO unsupported; independent literal MMIO checker only')


def compare_cases(cases):
    require(set(cases)==set(LABELS),'all five actual cases are required')
    control,bad,candidate=(cases[k] for k in LABELS[:3])
    for value in cases.values():
        require(all(value['identity'][f]==control['identity'][f] for f in ('checker_hash','contract_hash','environment_hash')),'checker/contract/environment changed across cases')
    require(control['selected_header_hash']==candidate['selected_header_hash'] and bad['selected_header_hash']==candidate['original_bad_hash'],'candidate is not exact bad-base restoration')
    require(candidate['candidate_diff_sha256'] is not None and control['program_hashes']==candidate['program_hashes'] and control['semantic_hash']==candidate['semantic_hash'],'candidate control executable/behavior differs')
    require(bad['program_hashes']['firmware.bin']!=control['program_hashes']['firmware.bin'],'seeded offset defect did not change executable bytes')
    for original,replay in (('bad','bad-replay'),('candidate','candidate-replay')):
        a,b=cases[original],cases[replay]
        require(b['replay_original_hash']==a['execution_sha256'],'replay is not bound to saved original execution')
        require(all(a[k]==b[k] for k in ('identity','program_hashes','observation_sha256','semantic_hash','check','first_conflict','failure_signature','cycles')),'saved executable replay differs')


def stage_sources(destination):
    hashes={}
    for name in SOURCE_FILES:
        p=ROOT/name
        require(p.is_file() and not p.is_symlink(),'missing/nonregular public source '+name)
        out=destination/name;out.parent.mkdir(parents=True,exist_ok=True);out.write_bytes(p.read_bytes())
        hashes[name]=sha(out.read_bytes())
    require(hashes['targets/riscv/upstream/picorv32.v']==CORE,'pinned public core differs')
    require(bytes_under(destination)<=1024**2,'public source closure exceeds cap')
    return hashes


def isolation(label):
    return ['docker','run','--rm','--pull','never','--name','veriforge-firmware-'+label,'--user',f'{os.getuid()}:{os.getgid()}',
        '--network','none','--read-only','--cap-drop','ALL','--security-opt','no-new-privileges','--cpus','2','--memory','2g',
        '--pids-limit','128','--tmpfs','/tmp:rw,exec,nosuid,size=128m']


def container_command(image,source,output,label,mode,saved=None,inputs=None):
    command=isolation(label)+['--mount',f'type=bind,src={source}/src,dst=/workspace/src,readonly',
        '--mount',f'type=bind,src={source}/targets,dst=/workspace/targets,readonly',
        '--mount',f'type=bind,src={source}/scripts/ci_firmware_acceptance.py,dst=/workspace/scripts/ci_firmware_acceptance.py,readonly']
    if output: command+=['--mount',f'type=bind,src={output},dst=/attempt']
    if saved: command+=['--mount',f'type=bind,src={saved},dst=/saved,readonly']
    if inputs:
        for host,guest in inputs: command+=['--mount',f'type=bind,src={host},dst={guest},readonly']
    return command+['--entrypoint','python',image,'/workspace/scripts/ci_firmware_acceptance.py',mode]


def pure_command(image,source):
    # This is the finite staged public bundle, never the repository checkout.
    return isolation('pure')+['--mount',f'type=bind,src={source},dst=/workspace,readonly',
        '--entrypoint','python',image,'-m','pytest','/workspace/tests/firmware',
        '/workspace/tests/acceptance/test_firmware_harness_review.py','-q','-p','no:cacheprovider','--basetemp=/tmp/pytest-firmware']


def metadata():
    import importlib.metadata
    import platform
    root=Path('/tmp/metadata');root.mkdir()
    tools={}
    deadline=time.monotonic()+20
    for name in ('verilator','riscv64-unknown-elf-gcc','riscv64-unknown-elf-objcopy','riscv64-unknown-elf-objdump','riscv64-unknown-elf-readelf'):
        run_process((name,'--version'),root/(name+'.out'),root/(name+'.err'),deadline,root=root,cap=1024**2)
        path=Path(shutil.which(name))
        tools[name]=dict(version=(root/(name+'.out')).read_text().splitlines()[0],sha256=sha(path.read_bytes()))
    print(json.dumps(dict(python=platform.python_version(),packages={d.metadata['Name'].lower():d.version for d in importlib.metadata.distributions()},
        tools=tools,dpkg=Path('/opt/veriforge/PACKAGE_VERSIONS').read_text(),reference_revision=Path('/opt/spike/UPSTREAM_REVISION').read_text().strip(),reference_support='Spike MMIO unsupported'),sort_keys=True))
    return 0


def export_evidence(work,source,destination):
    destination.mkdir(parents=True,exist_ok=False)
    selected=[(work/name,Path(name)) for name in HOST_FILES]
    selected += [(source/name,Path('source/workflow/firmware-acceptance.yml') if name.startswith('.github/') else Path('source')/name) for name in SOURCE_FILES]
    for label in LABELS:
        selected += [(work/label/name,Path(label)/name) for name in CASE_FILES+LOG_FILES]
        selected += [(work/(label+ext),Path(label+ext)) for ext in ('.log','.stderr.log','.check.log','.check.json','.check.stderr.log')]
    selected += [(work/('cleanup-'+label+'.log'),Path('cleanup-'+label+'.log')) for label in ('metadata','pure')+LABELS+tuple('check-'+s for s in LABELS)]
    size=0;hashes={}
    for path,relative in selected:
        if not path.is_file() or path.is_symlink(): continue
        size+=path.stat().st_size;require(size<CAP-1024**2,'evidence cap exceeded')
        out=destination/relative;out.parent.mkdir(parents=True,exist_ok=True);shutil.copyfile(path,out)
        hashes[relative.as_posix()]=sha(out.read_bytes())
    write_json(destination/'receipt-sha256.json',hashes)
    require(bytes_under(destination)<=CAP,'receipt cap exceeded')


def main():
    global CANCELLED
    require(os.name=='posix' and os.environ.get('GITHUB_ACTIONS')=='true' and os.environ.get('GITHUB_EVENT_NAME')=='workflow_dispatch'
        and os.environ.get('GITHUB_REPOSITORY')=='desanv01/VerifForge','hosted manual CI only; no local build')
    require(os.getuid()!=0,'nonroot host required')
    for sig in (signal.SIGINT,signal.SIGTERM): signal.signal(sig,lambda *_:globals().__setitem__('CANCELLED',True))
    start=time.monotonic();end=start+54*60
    work=Path(tempfile.mkdtemp(prefix='firmware-ci-',dir=os.environ['RUNNER_TEMP']));source=work/'public'
    destination=ROOT/'ci-artifacts/firmware';require(not destination.exists(),'fresh evidence output required')
    owned=set();builder_owned=False;records=[]
    summary=dict(schema_version='firmware.ci.v1',accepted=False,cases={},builder_settled=False,
        commit=os.environ.get('GITHUB_SHA'),run_id=os.environ.get('GITHUB_RUN_ID'),run_attempt=os.environ.get('GITHUB_RUN_ATTEMPT'),reference_support='Spike MMIO unsupported')
    cleanup_end=None
    def small(args,name,seconds=15):
        remaining=(cleanup_end or end)-time.monotonic();require(remaining>0,'control deadline exhausted')
        command=['docker',*args];records.append(command)
        p=subprocess.run(command,env=environment(),capture_output=True,timeout=min(seconds,remaining))
        (work/name).write_bytes(p.stdout[:1024**2]+p.stderr[:1024**2]);return p
    def stop(label):
        if label not in owned: return
        name='veriforge-firmware-'+label
        p=small(['ps','-aq','--filter','name=^/'+name+'$'],'cleanup-'+label+'.log')
        require(p.returncode==0,'owned container inspection failed')
        if p.stdout.strip(): require(small(['rm','-f',name],'cleanup-'+label+'.log').returncode==0,'owned container stop failed')
        p=small(['ps','-aq','--filter','name=^/'+name+'$'],'cleanup-'+label+'.log')
        require(p.returncode==0 and not p.stdout.strip(),'owned container remains')
    def run(command,name,seconds,limit,label=None):
        if label:
            p=small(['ps','-aq','--filter','name=^/veriforge-firmware-'+label+'$'],'cleanup-'+label+'.log')
            require(p.returncode==0 and not p.stdout.strip(),'container name collision');owned.add(label)
        records.append(command)
        return run_process(command,work/(name+'.log'),work/(name+'.stderr.log'),min(end,limit,time.monotonic()+seconds),
            root=work,cap=28*1024**2,host=True,settle=(lambda:stop(label)) if label else None)
    try:
        hashes=stage_sources(source);write_json(work/'source-manifest.json',hashes)
        memory=int(next(s.split()[1] for s in Path('/proc/meminfo').read_text().splitlines() if s.startswith('MemAvailable:')))*1024
        host=dict(cpus=os.cpu_count(),available_memory=memory,free_disk=shutil.disk_usage(work).free);write_json(work/'host.json',host)
        require(host['cpus']>=4 and memory>=10*1024**3 and host['free_disk']>=10*1024**3,'insufficient public runner capacity')
        p=small(['buildx','ls','--format','{{.Name}}'],'builder-before.log')
        require(p.returncode==0 and BUILDER not in p.stdout.decode().splitlines(),'builder name collision')
        builder_owned=True
        p=small(['buildx','create','--name',BUILDER,'--driver','docker-container','--platform','linux/amd64','--driver-opt',
            f'image={BUILDKIT},memory=8g,memory-swap=8g,cpu-period=100000,cpu-quota=400000,restart-policy=no'],'builder-create.log',30)
        require(p.returncode==0,'builder creation failed')
        context=work/'context';context.mkdir()
        for name in ('Dockerfile','README.md'): shutil.copyfile(source/'infra/riscv'/name,context/name)
        print('Building pinned full toolchain for firmware; no image export.',flush=True)
        run(['docker','buildx','build','--builder',BUILDER,'--load','--platform','linux/amd64','--progress=plain','--build-arg','SPIKE_BUILD_JOBS=4',
            '--iidfile',str(work/'image-id.txt'),'--metadata-file',str(work/'build-metadata.json'),'-t','veriforge-firmware:ci',str(context)],'build',35*60,start+35*60)
        p=small(['inspect','buildx_buildkit_'+BUILDER+'0','--format','{{json .HostConfig}}'],'builder-caps.json')
        require(p.returncode==0,'missing builder cap receipt')
        caps=get_json(work/'builder-caps.json')
        require(all(caps[k]==v for k,v in dict(Memory=8*1024**3,MemorySwap=8*1024**3,CpuQuota=400000,CpuPeriod=100000).items()),'builder caps differ')
        require(small(['buildx','stop',BUILDER],'builder-stop.log',30).returncode==0,'builder stop failed')
        state=small(['inspect','buildx_buildkit_'+BUILDER+'0','--format','{{.State.Running}}'],'builder-state.log')
        require(state.returncode==0 and state.stdout.strip()==b'false','builder remains running');summary['builder_settled']=True
        image=(work/'image-id.txt').read_text().strip();require(re.fullmatch(r'sha256:[a-f0-9]{64}',image),'image ID missing');summary['image_id']=image
        require(small(['image','inspect',image],'image-inspect.json').returncode==0,'built image missing')
        execution_end=min(end,time.monotonic()+18*60)
        run(container_command(image,source,None,'metadata','--metadata'),'metadata',30,execution_end,'metadata')
        tools=get_json(work/'metadata.log');tools['image_id']=image
        require(tools['reference_revision']==REFERENCE,'pinned toolchain reference source revision differs')
        require(all(tools['packages'].get(k)==v for k,v in {'cocotb':'2.0.1','pydantic':'2.12.3','pytest':'8.4.2'}.items()),'tool image Python pins differ')
        write_json(work/'tool-metadata.json',tools)
        run(pure_command(image,source),'pure',30,execution_end,'pure')
        for label in LABELS:
            require(execution_end-time.monotonic()>=150,'insufficient remaining case budget; never skip green')
            output=work/label;output.mkdir()
            saved=work/label.removesuffix('-replay') if label.endswith('-replay') else None
            print('Actual firmware '+label+'; saved firmware is not recompiled on replay.',flush=True)
            run(container_command(image,source,output,label,'--worker-'+label,saved=saved),label,180,execution_end,label)
            inputs=((output,'/case'),(work/'source-manifest.json','/source-manifest.json'),(work/'tool-metadata.json','/tools.json'))
            run(container_command(image,source,None,'check-'+label,'--check-'+label,inputs=inputs),label+'.check',15,execution_end,'check-'+label)
            summary['cases'][label]=get_json(work/(label+'.check.log'))
            shutil.copyfile(work/(label+'.check.log'),work/(label+'.check.json'))
        compare_cases(summary['cases']);summary['accepted']=True
    except (GateError,OSError,ValueError,TypeError,KeyError,AttributeError,IndexError,subprocess.SubprocessError) as exc:
        summary['error']=str(exc)[:2000]
        if hasattr(exc,'cleanup_error'): summary['cleanup_error']=exc.cleanup_error
    finally:
        cleanup_end=min(end+60,time.monotonic()+60)
        for label in sorted(owned):
            try: stop(label)
            except (GateError,OSError,subprocess.SubprocessError) as exc: summary['cleanup_error']=str(exc)
        if builder_owned and not summary['builder_settled']:
            try:
                p=small(['buildx','stop',BUILDER],'builder-stop.log',30)
                state=small(['inspect','buildx_buildkit_'+BUILDER+'0','--format','{{.State.Running}}'],'builder-state.log')
                summary['builder_settled']=p.returncode==0 and state.returncode==0 and state.stdout.strip()==b'false'
            except (GateError,OSError,subprocess.SubprocessError) as exc: summary['cleanup_error']=str(exc)
        summary['accepted']=summary['accepted'] and summary['builder_settled'] and not CANCELLED and 'cleanup_error' not in summary
        summary['wall_seconds']=time.monotonic()-start;write_json(work/'commands.json',records);write_json(work/'summary.json',summary)
        try: export_evidence(work,source,destination)
        except (GateError,OSError) as exc:
            summary['accepted']=False;summary['evidence_error']=str(exc)
            destination.mkdir(parents=True,exist_ok=True)
            for p in destination.rglob('*'):
                if p.is_file(): p.unlink()
            write_json(destination/'summary.json',summary)
        print('FIRMWARE_CI_RECEIPT\n'+json.dumps(summary,sort_keys=True,indent=2)[:256*1024],flush=True)
    return 0 if summary['accepted'] else 1


if __name__=='__main__':
    mode=sys.argv[1:] or ['--host']
    require(len(mode)==1,'no arbitrary controller inputs')
    for sig in (signal.SIGINT,signal.SIGTERM): signal.signal(sig,lambda *_:globals().__setitem__('CANCELLED',True))
    if mode[0]=='--host': raise SystemExit(main())
    require(os.name=='posix' and os.getuid()!=0,'nonroot Linux container mode required')
    if mode[0]=='--metadata': raise SystemExit(metadata())
    if mode[0].startswith('--worker-') and mode[0][9:] in LABELS: raise SystemExit(worker(mode[0][9:]))
    if mode[0].startswith('--check-') and mode[0][8:] in LABELS:
        print(json.dumps(check_case(Path('/case'),mode[0][8:],get_json(Path('/source-manifest.json')),get_json(Path('/tools.json'))),sort_keys=True));raise SystemExit(0)
    raise GateError('unsupported fixed controller mode')
