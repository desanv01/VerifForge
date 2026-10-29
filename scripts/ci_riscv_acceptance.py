"""Fixed hosted-CI acceptance; no local build, registry push or arbitrary inputs."""
import hashlib
import json
import os
from pathlib import Path
import shutil
import signal
import subprocess
import sys
import tempfile
import time
from typing import Annotated, Literal

ROOT = Path(__file__).resolve().parents[1]
BUILDER = 'veriforge-riscv-ci'
BUILDKIT = 'moby/buildkit:buildx-stable-1@sha256:cec9f139f45e93c5c69c60f8b07cfad9f43f4ef6b6a6cd917527fea5ff2e3dea'
CAP = 32*1024*1024
REFERENCE = '4c6a9bc79dab80616932949707e7c0471d8e9e54'
CORE_HASH = '0836050971b3c6cdd28ac3b1e5719a67fb645161912bef1e472e63995ceb0622'
CORE_REVISION = 'ef203c2b0a3fb793280f5114941416c425c5b461'
LICENSE_HASH = 'c65e436d18972c9c4bd192494fe4870bd2e158179474975b80a3559f27631632'
IDENTITIES = ('source_hash','checker_hash','contract_hash','experiment_hash','environment_hash')
FIELDS = ('pc','instruction','rd','rd_value','trap','interrupt','memory')
SOURCE_FILES = (
    'infra/riscv/Dockerfile','infra/riscv/README.md',
    'scripts/ci_riscv_acceptance.py','.github/workflows/riscv-acceptance.yml',
    'src/veriforge/__init__.py','src/veriforge/contracts/__init__.py',
    'src/veriforge/rtl/__init__.py','src/veriforge/rtl/runner.py',
    'src/veriforge/rtl/container.py','src/veriforge/rtl/oracle.py',
    'src/veriforge/riscv/__init__.py','src/veriforge/riscv/__main__.py',
    'src/veriforge/riscv/contracts.py','src/veriforge/riscv/checker.py',
    'src/veriforge/riscv/trace.py','src/veriforge/riscv/profile.py',
    'src/veriforge/riscv/memory.py','src/veriforge/riscv/runner.py',
    'src/veriforge/riscv/sim_job.py','src/veriforge/riscv/sim_test.py',
    'targets/riscv/control.json','targets/riscv/mutant.json',
    'targets/riscv/link.ld','targets/riscv/smoke.S','targets/riscv/picorv32_top.sv',
    'targets/riscv/upstream.json','targets/riscv/upstream/picorv32.v',
    'targets/riscv/upstream/SPIKE-LICENSE','tests/riscv/test_baseline.py',
    'tests/riscv/test_ci_acceptance_plan.py',
)
ATTEMPT_FILES = (
    'experiment.json','profile.json','smoke.o','smoke.elf','smoke.bin','job.json','result.json',
    'comparison.json','retirement.json','source-manifest.json','checker-manifest.json',
    'environment.json','assembler/execution.log','compiler/execution.log','objcopy/execution.log',
    'reference/execution.log','reference/commit.log','dut/built.marker',
    'dut/rvfi.json','dut/execution.log','dut/results.xml','dut/test.result.xml',
)
META_FILES = ('summary.json','source-manifest.json','commands.json','host.json',
    'image-inspect.json','tool-metadata.json','build-metadata.json','image-id.txt',
    'build.log','pure.log','metadata.log','builder-before.log','builder-create.log',
    'builder-stop.log','builder-final.log','builder-caps.json','image-inspect.log',
    'control.log','mutant.log','control-replay.log','stop-pure.log','stop-metadata.log','stop-control.log',
    'stop-mutant.log','stop-control-replay.log','validate-control.log','validate-mutant.log',
    'validate-control-replay.log','stop-validate-control.log','stop-validate-mutant.log','stop-validate-control-replay.log')
REQUIRED = ('result.json','comparison.json','retirement.json','dut/rvfi.json',
    'source-manifest.json','checker-manifest.json','environment.json','profile.json',
    'experiment.json','smoke.o','assembler/execution.log','smoke.elf','smoke.bin','reference/commit.log','dut/built.marker')
CANCELLED = False


class GateError(RuntimeError):
    pass


def digest(data):
    return hashlib.sha256(data).hexdigest()


def jhash(data):
    return digest(json.dumps(data,sort_keys=True,separators=(',',':'),ensure_ascii=False,allow_nan=False).encode())


def require(condition, message):
    if not condition:
        raise GateError(message)


def read_json(path):
    require(path.is_file() and path.stat().st_size <= 16*1024*1024, 'missing/oversized '+str(path.name))
    return json.loads(path.read_text())


def stage_sources(source, destination):
    hashes = {}
    for name in SOURCE_FILES:
        path = source/name
        require(path.is_file() and not path.is_symlink(), 'missing/nonregular curated source '+name)
        data = path.read_bytes()
        hashes[name] = digest(data)
        target = destination/name
        target.parent.mkdir(parents=True,exist_ok=True)
        target.write_bytes(data)
    require(hashes['targets/riscv/upstream/picorv32.v'] == CORE_HASH, 'core source pin changed')
    require(hashes['targets/riscv/upstream/SPIKE-LICENSE'] == LICENSE_HASH, 'reference license pin changed')
    require(sum((destination/p).stat().st_size for p in SOURCE_FILES) <= 512*1024, 'public source slice too large')
    return hashes


def completed(events):
    if not events:
        return False
    last = events[-1]
    return last['memory'] == dict(kind='write',address=0x8000ff00,width=4,byte_mask=15,value=1) and not last['trap'] and not last['interrupt']


def validate_attempt(directory, source_hashes, mutant=False):
    # Production calls this in the built image, whose Pydantic pin was checked.
    # Importing the controller on a bare Actions host does not install anything.
    sys.path.insert(0,str(ROOT/'src'))
    from pydantic import Field
    from veriforge.contracts import AttemptResult, WireModel
    from veriforge.riscv.contracts import Retirement, RiscvExperiment, Comparison
    from veriforge.riscv.trace import parse_spike_commits, normalize_rvfi
    from veriforge.riscv.checker import compare_retirements
    from veriforge.riscv.runner import _simulation_output
    from veriforge.riscv.profile import assemble_command, compile_command

    class RetirementPair(WireModel):
        schema_version: Literal['riscv.retirement-pair.v1']
        reference: Annotated[list[Retirement],Field(strict=True,min_length=1,max_length=256)]
        observed: Annotated[list[Retirement],Field(strict=True,min_length=1,max_length=256)]

    for name in REQUIRED:
        require((directory/name).is_file(), 'missing attempt evidence '+name)
    result = AttemptResult.model_validate(read_json(directory/'result.json')).model_dump(mode='json')
    expected = 'design_mismatch' if mutant else 'pass_within_scope'
    require(result['execution_status'] == 'completed' and result['verdict'] == expected, 'unexpected attempt status/verdict')
    identity = result['identity']
    require(isinstance(identity,dict) and set(identity) == set(IDENTITIES), 'missing evidence identity')
    for field in IDENTITIES:
        require(isinstance(identity[field],str) and len(identity[field]) == 64 and all(c in '0123456789abcdef' for c in identity[field]), 'invalid identity hash')
    records = {name:read_json(directory/(name+'.json')) for name in ('source-manifest','checker-manifest','environment','profile','experiment')}
    for record,field in zip(('source-manifest','checker-manifest','profile','experiment','environment'),IDENTITIES):
        require(jhash(records[record]) == identity[field], 'identity does not bind '+record)
    source, checker, env, plan = (records[k] for k in ('source-manifest','checker-manifest','environment','experiment'))
    typed_plan = RiscvExperiment.model_validate(plan)
    require(plan == typed_plan.model_dump(mode='json'),'experiment omits typed canonical fields')
    require(records['profile'] == dict(profile_id='picorv32-rv32i-v1',isa='RV32I',ram_base=0x80000000,
        ram_bytes=0x10000,entry=0x80000000,completion=0x8000ff00,completion_value=1,stack_top=0x8000fef0,endian='little'),'platform contract differs')
    for name in ('upstream/picorv32.v','picorv32_top.sv','smoke.S','link.ld'):
        require(source[name] == source_hashes['targets/riscv/'+name], 'DUT source differs from staged source')
    for name in ('checker.py','trace.py','contracts.py'):
        require(checker[name] == source_hashes['src/veriforge/riscv/'+name], 'checker source differs from staged source')
        require(digest((ROOT/'src/veriforge/riscv'/name).read_bytes()) == checker[name],'loaded native checker/parser source identity differs')
    for field,name in (('driver_hash','sim_test.py'),('job_hash','sim_job.py'),('memory_hash','memory.py')):
        require(env[field] == source_hashes['src/veriforge/riscv/'+name], 'environment omits current '+name)
    require(source['profile_code_hash'] == source_hashes['src/veriforge/riscv/profile.py'], 'profile code mismatch')
    fixture = 'picorv32_reg_write_bug' if mutant else 'picorv32_rv32i'
    require(source['core_revision'] == CORE_REVISION,'DUT revision differs')
    require(plan['fixture_id'] == source['fixture_id'] == fixture and plan['max_cycles'] == 100000 and plan['max_retired'] == 256 and plan['wall_time_seconds'] == 120.0 and plan['program_id'] == 'rv32i-smoke-v1' and plan['profile_id'] == 'picorv32-rv32i-v1', 'experiment differs from fixed budget/fixture')
    require(env['reference_revision'] == REFERENCE, 'reference revision differs')
    require(env['elf_hash'] == digest((directory/'smoke.elf').read_bytes()) and env['binary_hash'] == digest((directory/'smoke.bin').read_bytes()), 'program hash differs')
    require(env['object_hash'] == digest((directory/'smoke.o').read_bytes()),'assembled object hash differs')
    require(env['assembly_command'] == list(assemble_command(Path('/output'))) and env['compiler_command'] == list(compile_command(Path('/output'))),'assembly/link commands differ from fixed recipe')
    artifact_paths = [a['path'] for a in result['artifacts']]
    require(len(set(artifact_paths)) == len(artifact_paths) and set(REQUIRED)-{'result.json'} <= set(artifact_paths),'missing/duplicate required artifact identity')
    for artifact in result['artifacts']:
        name = artifact['path']
        require(name in ATTEMPT_FILES and name != 'result.json', 'unexpected artifact path')
        p = directory/name
        require(p.is_file() and not p.is_symlink() and p.stat().st_size == artifact['size_bytes'] and digest(p.read_bytes()) == artifact['sha256'], 'artifact identity mismatch')
    raw = _simulation_output(directory/'dut/rvfi.json',typed_plan).model_dump(mode='json')
    require(raw['termination'] == 'completion_store_retired' and type(raw['cycles']) is int and 0 < raw['cycles'] <= 100000, 'unexpected actual DUT termination')
    require(result['usage']['cycles'] == raw['cycles'],'reported DUT cycles differ')
    pair_document = read_json(directory/'retirement.json')
    pair = RetirementPair.model_validate(pair_document).model_dump(mode='json')
    require(pair == pair_document,'retirement pair omits native canonical fields')
    ref, got = pair['reference'], pair['observed']
    reference = parse_spike_commits((directory/'reference/commit.log').read_text(),max_retired=typed_plan.max_retired)
    observed = normalize_rvfi(raw['events'],max_retired=typed_plan.max_retired)
    require(ref == [e.model_dump(mode='json') for e in reference],'saved reference differs from native Spike commits')
    require(got == [e.model_dump(mode='json') for e in observed],'saved DUT differs from native RVFI events')
    require(ref and got and completed(ref), 'missing completed reference prefix')
    require(0 < len(ref) <= 256 and 0 < len(got) <= 256 and (mutant or len(ref) == len(got)), 'retirement prefix lengths differ')
    for events in (ref,got):
        require(all(type(e['order']) is int and e['order'] == i for i,e in enumerate(events)), 'retirement sequence is not contiguous')
    require(len(raw['events']) == len(got), 'raw and normalized retirement counts differ')
    comparison = Comparison.model_validate(read_json(directory/'comparison.json')).model_dump(mode='json')
    recomputed = compare_retirements(reference,observed)
    require(comparison == recomputed.model_dump(mode='json'),'native comparison/count differs from retained comparison')
    require(comparison['verdict'] == expected, 'comparison verdict differs')
    first = next(((i,f,a[f],b[f]) for i,(a,b) in enumerate(zip(ref,got)) for f in FIELDS if a[f] != b[f]),None)
    if mutant:
        # Hand-derived from unmodified TESTBUG_002: LUI physically writes sp^1;
        # the following ADDI's reported writeback is therefore one bit different.
        require(first == (1,'rd_value',0x8000fef0,0x8000fef1), 'unexpected functional mutant first divergence')
        difference = comparison['first_divergence']
        require(difference['order'] == first[0] and difference['field'] == first[1] and difference['expected'] == json.dumps(first[2]) and difference['observed'] == json.dumps(first[3]), 'first divergence record differs')
        signature = result['failure_signature']
        require(signature and signature['requirement_id'] == 'RISCV_ARCH_RETIREMENT' and signature['failure_class'] == 'riscv.rd_value' and signature['first_cycle'] == got[1]['source_cycle'], 'missing actual divergence signature')
        require(signature['expected'] == difference['expected'] and signature['observed'] == difference['observed'],'signature values differ from first divergence')
        require(signature['signature_hash'] == jhash(dict(checker=identity['checker_hash'],contract=identity['contract_hash'],field=first[1])),'signature hash differs from native first divergence')
    else:
        require(first is None and completed(got) and comparison['first_divergence'] is None and result['failure_signature'] is None, 'control architecture did not agree through completion')
    return {'identity':identity,'pair_hash':jhash(pair),'elf_hash':env['elf_hash'],
        'binary_hash':env['binary_hash'],'object_hash':env['object_hash'],'first_divergence':comparison['first_divergence'],
        'reference_hash':jhash(ref),'cycles':raw['cycles'],'compared':comparison['compared'],'verdict':expected}


def validate_replay(control, replay):
    require(all(control[k] == replay[k] for k in ('identity','pair_hash','object_hash','elf_hash','binary_hash','cycles','compared')), 'exact control replay differs')


def validation_mode(mutant):
    require(os.name == 'posix' and os.getuid() != 0,'nonroot Linux evidence-validation container required')
    result = validate_attempt(Path('/attempt'),read_json(Path('/validation/source-manifest.json')),mutant=mutant)
    print(json.dumps(result,sort_keys=True))
    return 0


def run_command(image, source, output, label, experiment):
    return ['docker','run','--rm','--pull','never','--name','veriforge-riscv-ci-'+label,
        '--user',f'{os.getuid()}:{os.getgid()}','--network','none','--read-only',
        '--cap-drop','ALL','--security-opt','no-new-privileges','--cpus','2','--memory','2g',
        '--pids-limit','128','--tmpfs','/tmp:rw,exec,nosuid,size=128m',
        '--mount',f'type=bind,src={source}/src,dst=/workspace/src,readonly',
        '--mount',f'type=bind,src={source}/targets/riscv,dst=/workspace/targets/riscv,readonly',
        '--mount',f'type=bind,src={output},dst=/output',image,
        '/workspace/targets/riscv/'+experiment,'/output']


def export_evidence(work, source, destination):
    destination.mkdir(parents=True,exist_ok=False)
    selected = [(work/name,Path(name)) for name in META_FILES]
    selected += [(source/name,Path('source/workflow/riscv-acceptance.yml') if name.startswith('.github/') else Path('source')/name) for name in SOURCE_FILES]
    selected += [(work/label/name,Path(label)/name) for label in ('control','mutant','control-replay') for name in ATTEMPT_FILES]
    size, hashes = 0, {}
    for p,relative in selected:
        if not p.is_file() or p.is_symlink():
            continue
        size += p.stat().st_size
        require(size <= CAP-1024*1024, 'evidence exceeds aggregate cap')
        out = destination/relative
        out.parent.mkdir(parents=True,exist_ok=True)
        shutil.copyfile(p,out)
        hashes[relative.as_posix()] = digest(out.read_bytes())
    (destination/'receipt-sha256.json').write_text(json.dumps(hashes,indent=2,sort_keys=True))
    require(sum(p.stat().st_size for p in destination.rglob('*') if p.is_file()) <= CAP, 'receipt exceeds cap')


def main():
    global CANCELLED
    require(os.name == 'posix' and os.environ.get('GITHUB_ACTIONS') == 'true' and os.environ.get('GITHUB_EVENT_NAME') == 'workflow_dispatch' and os.environ.get('GITHUB_REPOSITORY') == 'desanv01/VerifForge', 'hosted manual CI only; no local execution')
    require(os.getuid() != 0, 'nonroot host runner required')
    for sig in (signal.SIGINT,signal.SIGTERM):
        signal.signal(sig,lambda *_:globals().__setitem__('CANCELLED',True))
    started = time.monotonic()
    work_deadline = started+43*60
    work = Path(tempfile.mkdtemp(prefix='riscv-ci-',dir=os.environ['RUNNER_TEMP']))
    source = work/'public'
    output = ROOT/'ci-artifacts/riscv'
    require(not output.exists(), 'evidence directory already exists')
    env = {k:os.environ[k] for k in ('PATH','HOME','LANG','TMPDIR') if k in os.environ}
    commands = []
    owned_containers = set()
    builder_owned = False
    settlement_deadline = None
    summary = {'schema_version':'riscv.ci.v1','accepted':False,'attempts':{},
        'commit':os.environ.get('GITHUB_SHA'),'run_id':os.environ.get('GITHUB_RUN_ID'),
        'run_attempt':os.environ.get('GITHUB_RUN_ATTEMPT'),'builder_settled':False}

    def small(args, log, timeout=15):
        remaining = (settlement_deadline or work_deadline)-time.monotonic()
        require(remaining > 0,'control/settlement deadline exhausted')
        command = ['docker',*args]
        commands.append(command)
        p = subprocess.run(command,env=env,capture_output=True,timeout=min(timeout,remaining))
        (work/log).write_bytes(p.stdout[:1024*1024]+p.stderr[:1024*1024])
        return p

    def stop_run(label):
        if label not in owned_containers:
            return
        name = 'veriforge-riscv-ci-'+label
        p = small(['ps','-aq','--filter','name=^/'+name+'$'],'stop-'+label+'.log')
        require(p.returncode == 0,'cannot inspect owned container '+label)
        if p.stdout.strip():
            removed = small(['rm','-f',name],'stop-'+label+'.log')
            require(removed.returncode == 0,'cannot settle owned container '+label)
        final = small(['ps','-aq','--filter','name=^/'+name+'$'],'stop-'+label+'.log')
        require(final.returncode == 0 and not final.stdout.strip(),'owned container remains '+label)

    def supervised(command, log, seconds, limit, container=None):
        require(not CANCELLED and min(limit,work_deadline)-time.monotonic() > 0, 'cancelled/exhausted aggregate deadline')
        if container:
            existing = small(['ps','-aq','--filter','name=^/veriforge-riscv-ci-'+container+'$'],'stop-'+container+'.log')
            require(existing.returncode == 0 and not existing.stdout.strip(),'run container name already exists')
            owned_containers.add(container)
        commands.append(command)
        end = min(time.monotonic()+seconds,limit,work_deadline)
        reason = None
        with (work/log).open('wb') as stream:
            p = subprocess.Popen(command,env=env,stdout=stream,stderr=subprocess.STDOUT,start_new_session=True)
            try:
                while p.poll() is None:
                    available = int(next(s.split()[1] for s in Path('/proc/meminfo').read_text().splitlines() if s.startswith('MemAvailable:')))*1024
                    total = sum(f.stat().st_size for f in work.rglob('*') if f.is_file())
                    if CANCELLED: reason = 'cancelled'
                    elif time.monotonic() >= end: reason = 'timeout'
                    elif available < 1024**3: reason = 'memory_limit'
                    elif shutil.disk_usage(work).free < 2*1024**3: reason = 'disk_limit'
                    elif total > 28*1024**2 or (work/log).stat().st_size > 8*1024**2: reason = 'evidence_limit'
                    if reason:
                        raise GateError(log+': '+reason)
                    time.sleep(1)
            finally:
                if p.poll() is None:
                    try:
                        if container: stop_run(container)
                    finally:
                        try: os.killpg(p.pid,signal.SIGKILL)
                        except ProcessLookupError: pass
                        p.wait(timeout=5)
            require(p.returncode == 0, log+': command failed '+str(p.returncode))

    try:
        hashes = stage_sources(ROOT,source)
        (work/'source-manifest.json').write_text(json.dumps(hashes,indent=2,sort_keys=True))
        available = int(next(s.split()[1] for s in Path('/proc/meminfo').read_text().splitlines() if s.startswith('MemAvailable:')))*1024
        host = {'cpus':os.cpu_count(),'available_memory_bytes':available,'free_disk_bytes':shutil.disk_usage(work).free}
        (work/'host.json').write_text(json.dumps(host,indent=2))
        require(host['cpus'] >= 4 and available >= 10*1024**3 and host['free_disk_bytes'] >= 10*1024**3, 'insufficient measured public-runner capacity')
        print('Building pinned full Spike toolchain, four jobs; no image export.',flush=True)
        before = small(['buildx','ls','--format','{{.Name}}'],'builder-before.log')
        require(before.returncode == 0 and BUILDER not in before.stdout.decode().splitlines(),'builder name already exists')
        builder_owned = True
        created = small(['buildx','create','--name',BUILDER,'--driver','docker-container','--platform','linux/amd64','--driver-opt',
            f'image={BUILDKIT},memory=8g,memory-swap=8g,cpu-period=100000,cpu-quota=400000,restart-policy=no'],'builder-create.log',30)
        require(created.returncode == 0,'owned builder creation failed')
        context = work/'build-context'
        context.mkdir()
        for name in ('Dockerfile','README.md'):
            shutil.copyfile(source/'infra/riscv'/name,context/name)
        supervised(['docker','buildx','build','--builder',BUILDER,'--load','--platform','linux/amd64','--progress=plain',
            '--build-arg','SPIKE_BUILD_JOBS=4','--metadata-file',str(work/'build-metadata.json'),'--iidfile',str(work/'image-id.txt'),
            '-t','veriforge-riscv:ci','-f',str(context/'Dockerfile'),str(context)],'build.log',35*60,started+35*60)
        caps = small(['inspect','buildx_buildkit_'+BUILDER+'0','--format','{{json .HostConfig}}'],'builder-caps.json')
        require(caps.returncode == 0,'missing actual builder cap receipt')
        limits = read_json(work/'builder-caps.json')
        require(all(limits[k] == v for k,v in {'Memory':8*1024**3,'MemorySwap':8*1024**3,'CpuQuota':400000,'CpuPeriod':100000}.items()),'actual builder resource caps differ')
        stopped = small(['buildx','stop',BUILDER],'builder-stop.log',30)
        status = small(['inspect','buildx_buildkit_'+BUILDER+'0','--format','{{.State.Running}}'],'builder-final.log')
        require(stopped.returncode == 0 and status.returncode == 0 and status.stdout.strip() == b'false','builder did not settle')
        summary['builder_settled'] = True
        image = (work/'image-id.txt').read_text().strip()
        require(image.startswith('sha256:') and len(image) == 71,'missing immutable image ID')
        summary['image_id'] = image
        inspected = small(['image','inspect',image],'image-inspect.log')
        require(inspected.returncode == 0,'built image is unavailable')
        (work/'image-inspect.json').write_bytes(inspected.stdout)
        execution_end = min(time.monotonic()+8*60,work_deadline)
        isolated = ['docker','run','--rm','--pull','never','--user',f'{os.getuid()}:{os.getgid()}',
            '--network','none','--read-only','--cap-drop','ALL','--security-opt','no-new-privileges',
            '--cpus','2','--memory','2g','--pids-limit','128','--tmpfs','/tmp:rw,exec,nosuid,size=128m']
        metadata_code = "import hashlib,importlib.metadata,json,pathlib,platform; p=pathlib.Path; print(json.dumps({'python':platform.python_version(),'packages':{d.metadata['Name']:d.version for d in importlib.metadata.distributions()},'revision':p('/opt/spike/UPSTREAM_REVISION').read_text().strip(),'submodules':p('/opt/spike/UPSTREAM_SUBMODULES').read_text(),'packages_dpkg':p('/opt/veriforge/PACKAGE_VERSIONS').read_text(),'spike_binary_hash':hashlib.sha256(p('/opt/spike/bin/spike').read_bytes()).hexdigest()},sort_keys=True))"
        supervised(isolated+['--name','veriforge-riscv-ci-metadata','--entrypoint','python',image,'-c',metadata_code],'metadata.log',20,execution_end,container='metadata')
        metadata = read_json(work/'metadata.log')
        require(metadata['revision'] == REFERENCE,'actual reference revision mismatch')
        require(all(metadata['packages'].get(k) == v for k,v in {'cocotb':'2.0.1','pydantic':'2.12.3','pytest':'8.4.2'}.items()),'actual image Python pins mismatch')
        (work/'tool-metadata.json').write_text(json.dumps(metadata,indent=2,sort_keys=True))
        pure = isolated+['--name','veriforge-riscv-ci-pure','--mount',f'type=bind,src={source}/src,dst=/workspace/src,readonly',
            '--mount',f'type=bind,src={source}/targets/riscv,dst=/workspace/targets/riscv,readonly',
            '--mount',f'type=bind,src={source}/tests/riscv,dst=/workspace/tests/riscv,readonly',
            '--entrypoint','python',image,'-m','pytest','/workspace/tests/riscv/test_baseline.py','-q','-p','no:cacheprovider','--basetemp=/tmp/pytest-riscv']
        supervised(pure,'pure.log',30,execution_end,container='pure')
        for label,plan in (('control','control.json'),('mutant','mutant.json'),('control-replay','control.json')):
            require(execution_end-time.monotonic() >= 150,'insufficient remaining execution budget; no skipped green')
            directory = work/label
            directory.mkdir()
            print('Actual bounded '+label+' core/reference attempt.',flush=True)
            supervised(run_command(image,source,directory,label,plan),label+'.log',180,execution_end,container=label)
            validation = isolated+['--name','veriforge-riscv-ci-validate-'+label,
                '--mount',f'type=bind,src={source}/src,dst=/workspace/src,readonly',
                '--mount',f'type=bind,src={source}/scripts/ci_riscv_acceptance.py,dst=/workspace/scripts/ci_riscv_acceptance.py,readonly',
                '--mount',f'type=bind,src={work}/source-manifest.json,dst=/validation/source-manifest.json,readonly',
                '--mount',f'type=bind,src={directory},dst=/attempt,readonly',
                '--entrypoint','python',image,'/workspace/scripts/ci_riscv_acceptance.py',
                '--validate-mutant' if label == 'mutant' else '--validate-control']
            supervised(validation,'validate-'+label+'.log',10,execution_end,container='validate-'+label)
            summary['attempts'][label] = read_json(work/('validate-'+label+'.log'))
            require(read_json(directory/'environment.json')['spike_binary_hash'] == metadata['spike_binary_hash'],'attempt reference binary differs from image metadata')
        validate_replay(summary['attempts']['control'],summary['attempts']['control-replay'])
        control,mutant = (summary['attempts'][k] for k in ('control','mutant'))
        require(all(control[k] == mutant[k] for k in ('object_hash','elf_hash','binary_hash','reference_hash')),'mutant stimulus/reference differs from control')
        require(all(control['identity'][k] == mutant['identity'][k] for k in ('checker_hash','contract_hash','environment_hash')),'mutant changes checker/contract/environment')
        summary['accepted'] = True
    except (GateError,OSError,ValueError,KeyError,TypeError,AttributeError,IndexError,subprocess.SubprocessError) as exc:
        summary['error'] = str(exc)[:2000]
    finally:
        settlement_deadline = min(time.monotonic()+60,work_deadline+60)
        # Only these fixed names were ever created by this isolated ephemeral job.
        for label in tuple(owned_containers):
            try: stop_run(label)
            except (GateError,OSError,subprocess.SubprocessError) as exc: summary['cleanup_error'] = str(exc)
        if builder_owned and not summary['builder_settled']:
            try:
                stopped = small(['buildx','stop',BUILDER],'builder-stop.log',30)
                state = small(['inspect','buildx_buildkit_'+BUILDER+'0','--format','{{.State.Running}}'],'builder-final.log')
                summary['builder_settled'] = stopped.returncode == 0 and state.returncode == 0 and state.stdout.strip() == b'false'
            except (GateError,OSError,subprocess.SubprocessError) as exc: summary['cleanup_error'] = str(exc)
        summary['accepted'] = summary['accepted'] and summary['builder_settled'] and not CANCELLED and 'cleanup_error' not in summary
        summary['wall_seconds'] = time.monotonic()-started
        (work/'commands.json').write_text(json.dumps(commands,indent=2))
        (work/'summary.json').write_text(json.dumps(summary,indent=2,sort_keys=True))
        try:
            export_evidence(work,source,output)
        except (GateError,OSError) as exc:
            summary['accepted'] = False
            summary['evidence_error'] = str(exc)
            output.mkdir(parents=True,exist_ok=True)
            for p in output.rglob('*'):
                if p.is_file(): p.unlink()
            (output/'summary.json').write_text(json.dumps(summary,indent=2))
        # Bounded logs-only fallback when artifact storage/upload is unavailable.
        fallback = json.dumps(summary,sort_keys=True,indent=2)
        for label in ('control','mutant','control-replay'):
            for name in ('comparison.json','reference/commit.log','dut/rvfi.json'):
                p = work/label/name
                if p.is_file() and p.stat().st_size <= 16384:
                    fallback += '\n'+label+'/'+name+'\n'+p.read_text()
        print('RISCV_CI_RECEIPT\n'+fallback[:256*1024],flush=True)
    return 0 if summary['accepted'] else 1


if __name__ == '__main__':
    if sys.argv[1:] in (['--validate-control'],['--validate-mutant']):
        raise SystemExit(validation_mode(sys.argv[1] == '--validate-mutant'))
    require(len(sys.argv) == 1,'no arbitrary CI controller inputs')
    raise SystemExit(main())
