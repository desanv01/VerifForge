"""Independent CI review: synthetic bytes/fake processes, never tool execution."""
import ast
from copy import deepcopy
import hashlib
import importlib.util
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / 'scripts/ci_riscv_acceptance.py'
spec = importlib.util.spec_from_file_location('riscv_ci_independent_review', SCRIPT)
ci = importlib.util.module_from_spec(spec)
spec.loader.exec_module(ci)


def sha(data):
    return hashlib.sha256(data).hexdigest()


def canonical(value):
    return sha(json.dumps(value, sort_keys=True, separators=(',', ':')).encode())


def put(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value))


def rebind(directory):
    """Update transport checksums, so semantic tests cannot fail on stale bytes."""
    result = json.loads((directory/'result.json').read_text())
    for item in result['artifacts']:
        data = (directory/item['path']).read_bytes()
        item.update(sha256=sha(data), size_bytes=len(data))
    put(directory/'result.json', result)


def attempt(directory, mutant=False):
    """Hand-authored transport fixture; not compiled ELF or simulator evidence."""
    directory.mkdir(parents=True)
    hashes = {name:sha((ROOT/name).read_bytes()) for name in ci.SOURCE_FILES}
    fixture = 'picorv32_reg_write_bug' if mutant else 'picorv32_rv32i'
    source = {name:hashes['targets/riscv/'+name] for name in
              ('upstream/picorv32.v', 'picorv32_top.sv', 'smoke.S', 'link.ld')}
    source.update(core_revision=ci.CORE_REVISION, fixture_id=fixture,
                  profile_code_hash=hashes['src/veriforge/riscv/profile.py'])
    checker = {name:hashes['src/veriforge/riscv/'+name] for name in
               ('checker.py', 'trace.py', 'contracts.py')}
    profile = dict(profile_id='picorv32-rv32i-v1', isa='RV32I', ram_base=0x80000000,
                   ram_bytes=65536, entry=0x80000000, completion=0x8000ff00,
                   completion_value=1, stack_top=0x8000fef0, endian='little')
    plan = dict(schema_version='riscv.v1', experiment_id='rv32i-mutant-v1' if mutant else 'rv32i-control-v1',
                fixture_id=fixture, program_id='rv32i-smoke-v1', profile_id='picorv32-rv32i-v1',
                max_cycles=100000, max_retired=256, wall_time_seconds=120.0)
    environment = {field:hashes['src/veriforge/riscv/'+name] for field,name in
                   (('driver_hash','sim_test.py'), ('job_hash','sim_job.py'), ('memory_hash','memory.py'))}
    tools = [dict(name=name, version='synthetic-review-only') for name in
             ('verilator', 'spike', 'riscv64-unknown-elf-gcc')]
    environment.update(reference_revision=ci.REFERENCE, reference_submodules='', tools=tools,
        elf_hash=sha(b'NOT AN ELF: synthetic review'), binary_hash=sha(b'synthetic binary'),
        object_hash=sha(b'NOT AN OBJECT: synthetic review'),
        assembly_command=['riscv64-unknown-elf-gcc','-march=rv32i','-mabi=ilp32','-mno-relax',
            '-ffreestanding','-c',str(ROOT/'targets/riscv/smoke.S'),'-o',str(Path('/output')/'smoke.o')],
        compiler_command=['riscv64-unknown-elf-gcc','-march=rv32i','-mabi=ilp32','-mno-relax',
            '-nostdlib','-nostartfiles','-ffreestanding','-Wl,--no-relax','-Wl,--build-id=none',
            '-T',str(ROOT/'targets/riscv/link.ld'),str(Path('/output')/'smoke.o'),'-o',str(Path('/output')/'smoke.elf')],
        reference_command=['synthetic-review-only'],
        spike_binary_hash='a'*64, image_package_hash='b'*64)
    records = dict(zip(('source-manifest','checker-manifest','profile','experiment','environment'),
                       (source,checker,profile,plan,environment)))
    for name,value in records.items(): put(directory/(name+'.json'),value)
    identity = {field:canonical(value) for field,value in zip(ci.IDENTITIES,
                      (source,checker,profile,plan,environment))}
    instructions = (0x80010137, 0xef010113, 0xf011a023)
    values = (0x80010000, 0x8000fef0, 0)
    reference, raw = [], []
    for i,(insn,value) in enumerate(zip(instructions,values)):
        memory = dict(kind='write',address=0x8000ff00,width=4,byte_mask=15,value=1) if i==2 else None
        reference.append(dict(schema_version='riscv.retirement.v1',order=i,hart=0,
            pc=0x80000000+4*i,instruction=insn,rd=2 if i<2 else 0,rd_value=value,
            trap=False,interrupt=False,memory=memory,source_line=i+1,source_cycle=None))
        raw.append(dict(cycle=10*(i+1),rvfi_order=i,rvfi_insn=insn,
            rvfi_pc_rdata=0x80000000+4*i,rvfi_rd_addr=2 if i<2 else 0,
            rvfi_rd_wdata=value,rvfi_rs1_rdata=0x80010000,
            rvfi_mem_addr=0x8000ff00 if i==2 else 0,
            rvfi_mem_rmask=0,rvfi_mem_wmask=15 if i==2 else 0,
            rvfi_mem_rdata=0,rvfi_mem_wdata=1 if i==2 else 0,
            rvfi_trap=False,rvfi_intr=False))
    observed = deepcopy(reference)
    for i,event in enumerate(observed): event.update(source_line=None,source_cycle=10*(i+1))
    if mutant:
        raw[1]['rvfi_rd_wdata'] ^= 1
        observed[1]['rd_value'] ^= 1
        raw[-1]['rvfi_mem_wdata'] = observed[-1]['memory']['value'] = 3
    difference = dict(order=1,field='rd_value',expected=str(0x8000fef0),observed=str(0x8000fef1),
                      requirement_id='RISCV_ARCH_RETIREMENT') if mutant else None
    signature = dict(requirement_id='RISCV_ARCH_RETIREMENT',failure_class='riscv.rd_value',first_cycle=20,
        expected=difference['expected'],observed=difference['observed'],
        signature_hash=canonical(dict(checker=identity['checker_hash'],contract=identity['contract_hash'],field='rd_value'))) if mutant else None
    verdict = 'design_mismatch' if mutant else 'pass_within_scope'
    put(directory/'retirement.json',dict(schema_version='riscv.retirement-pair.v1',reference=reference,observed=observed))
    put(directory/'dut/rvfi.json',dict(termination='completion_store_retired',cycles=31,events=raw))
    put(directory/'comparison.json',dict(verdict=verdict,compared=2 if mutant else 3,
        first_divergence=difference,reason='first architectural divergence' if mutant else 'architectural events agree through completion; no cycle comparison'))
    (directory/'smoke.elf').write_bytes(b'NOT AN ELF: synthetic review')
    (directory/'smoke.o').write_bytes(b'NOT AN OBJECT: synthetic review')
    (directory/'assembler').mkdir()
    (directory/'assembler/execution.log').write_text('SYNTHETIC: no assembler process ran')
    (directory/'smoke.bin').write_bytes(b'synthetic binary')
    (directory/'reference').mkdir()
    (directory/'reference/commit.log').write_text(
        'core 0: 3 0x80000000 (0x80010137) x2 0x80010000\n'
        'core 0: 3 0x80000004 (0xef010113) x2 0x8000fef0\n'
        'core 0: 3 0x80000008 (0xf011a023) mem 0x8000ff00 0x00000001\n')
    (directory/'dut/built.marker').write_text('synthetic review; no RTL built')
    result = dict(schema_version='1',attempt_id='synthetic-review',experiment_id=plan['experiment_id'],
        execution_status='completed',verdict=verdict,identity=identity,tool_versions=tools,
        failure_signature=signature,usage=dict(cycles=31,wall_time_seconds=0.0,artifact_bytes=0),
        artifacts=[dict(artifact_id='artifact-'+str(i),path=name,sha256=sha((directory/name).read_bytes()),
            size_bytes=(directory/name).stat().st_size,media_type='application/octet-stream')
            for i,name in enumerate(ci.REQUIRED) if name!='result.json'])
    put(directory/'result.json',result)
    return hashes


def test_independent_synthetic_control_mutant_replay(tmp_path):
    hashes = attempt(tmp_path/'control')
    control = ci.validate_attempt(tmp_path/'control',hashes)
    attempt(tmp_path/'mutant',True)
    assert ci.validate_attempt(tmp_path/'mutant',hashes,mutant=True)['first_divergence']['order']==1
    attempt(tmp_path/'replay')
    ci.validate_replay(control,ci.validate_attempt(tmp_path/'replay',hashes))


@pytest.mark.parametrize('corruption', ['raw-empty-rows','raw-disagrees','reference-garbage',
    'reference-disagrees','negative-register','invalid-comparison-count','bad-signature-hash'])
def test_rebound_transport_must_not_hide_invalid_native_evidence(tmp_path,corruption):
    directory = tmp_path/'attempt'
    mutant = corruption=='bad-signature-hash'
    hashes = attempt(directory,mutant)
    if corruption.startswith('raw-'):
        path=directory/'dut/rvfi.json'; data=json.loads(path.read_text())
        if corruption=='raw-empty-rows': data['events']=[{}]*3
        else: data['events'][0]['rvfi_rd_wdata'] ^= 1
        put(path,data)
    elif corruption.startswith('reference-'):
        path=directory/'reference/commit.log'
        path.write_text('garbage\n' if corruption=='reference-garbage' else path.read_text().replace('x2 0x80010000','x2 0x80010001'))
    elif corruption=='negative-register':
        path=directory/'retirement.json'; data=json.loads(path.read_text())
        data['reference'][0]['rd']=data['observed'][0]['rd']=-1
        put(path,data)
    elif corruption=='invalid-comparison-count':
        path=directory/'comparison.json'; data=json.loads(path.read_text());data['compared']=-100;put(path,data)
    else:
        path=directory/'result.json'; data=json.loads(path.read_text());data['failure_signature']['signature_hash']='0'*64;put(path,data)
    rebind(directory)
    with pytest.raises((ci.GateError,ValueError),match='.'):
        ci.validate_attempt(directory,hashes,mutant=mutant)


@pytest.mark.parametrize('field',ci.IDENTITIES)
def test_each_native_identity_is_required_exactly(tmp_path,field):
    hashes=attempt(tmp_path/'attempt')
    path=tmp_path/'attempt/result.json'; data=json.loads(path.read_text());data['identity'][field]='0'*64;put(path,data)
    with pytest.raises(ci.GateError): ci.validate_attempt(path.parent,hashes)


@pytest.mark.parametrize('kind',['missing-object','changed-object','missing-assembler-log','wrong-assembly','wrong-link'])
def test_stable_object_and_exact_recipe_are_required(tmp_path,kind):
    directory=tmp_path/'attempt';hashes=attempt(directory)
    if kind=='missing-object': (directory/'smoke.o').unlink()
    elif kind=='missing-assembler-log': (directory/'assembler/execution.log').unlink()
    elif kind=='changed-object':
        (directory/'smoke.o').write_bytes(b'object tamper with otherwise unchanged binary and ELF')
        rebind(directory)
    else:
        path=directory/'environment.json';data=json.loads(path.read_text())
        data['assembly_command' if kind=='wrong-assembly' else 'compiler_command']=['wrong-recipe']
        put(path,data)
        result=json.loads((directory/'result.json').read_text())
        result['identity']['environment_hash']=canonical(data);put(directory/'result.json',result);rebind(directory)
    with pytest.raises(ci.GateError): ci.validate_attempt(directory,hashes)


@pytest.mark.parametrize('field',['object_hash','elf_hash','binary_hash'])
def test_exact_replay_never_relaxes_full_object_elf_or_binary_identity(tmp_path,field):
    hashes=attempt(tmp_path/'control')
    control=ci.validate_attempt(tmp_path/'control',hashes)
    changed=deepcopy(control);changed[field]='0'*64
    with pytest.raises(ci.GateError,match='replay'): ci.validate_replay(control,changed)


def test_control_mutant_comparison_requires_exact_object_and_full_elf():
    tree=ast.parse(SCRIPT.read_text())
    checks=[n for n in ast.walk(tree) if isinstance(n,ast.Call) and isinstance(n.func,ast.Name)
            and n.func.id=='require' and any(isinstance(a,ast.Constant) and a.value=='mutant stimulus/reference differs from control' for a in n.args)]
    assert len(checks)==1
    keys=next(n for n in ast.walk(checks[0]) if isinstance(n,ast.Tuple))
    assert set(ast.literal_eval(keys))=={'object_hash','elf_hash','binary_hash','reference_hash'}


def test_export_uses_finite_public_allowlists_and_aggregate_cap(tmp_path):
    work,source=tmp_path/'work',tmp_path/'source';work.mkdir();source.mkdir()
    put(work/'summary.json',dict(accepted=False))
    for private in ('.env','.coordination/current-state.json','image.tar','secret.txt'):
        path=work/private;path.parent.mkdir(parents=True,exist_ok=True);path.write_text('PRIVATE_SENTINEL')
    ci.export_evidence(work,source,tmp_path/'export')
    assert sorted(p.relative_to(tmp_path/'export').as_posix() for p in (tmp_path/'export').rglob('*') if p.is_file())==['receipt-sha256.json','summary.json']
    with (work/'build.log').open('wb') as stream: stream.truncate(ci.CAP)
    with pytest.raises(ci.GateError,match='cap'): ci.export_evidence(work,source,tmp_path/'over-cap')


def supervised_function(namespace):
    """Compile only the nested controller for deterministic process-boundary tests."""
    tree=ast.parse(SCRIPT.read_text())
    main=next(n for n in tree.body if isinstance(n,ast.FunctionDef) and n.name=='main')
    function=next(n for n in main.body if isinstance(n,ast.FunctionDef) and n.name=='supervised')
    exec(compile(ast.fix_missing_locations(ast.Module(body=[function],type_ignores=[])),str(SCRIPT),'exec'),namespace)
    return namespace['supervised']


@pytest.mark.parametrize('cleanup_raises',[False,True])
def test_timeout_settles_owned_container_and_kills_client_even_if_cleanup_fails(tmp_path,cleanup_raises):
    calls=[]
    process=SimpleNamespace(pid=4242,returncode=None)
    process.poll=lambda:process.returncode
    process.wait=lambda **kw:calls.append(('wait',kw))
    def kill(pid,sig): calls.append(('kill',pid));process.returncode=-9
    def stop(label):
        calls.append(('stop',label))
        if cleanup_raises: raise ci.GateError('synthetic cleanup failure')
    class Memory:
        def read_text(self): return 'MemAvailable: 16000000 kB\n'
    namespace=dict(CANCELLED=False,work_deadline=1000,work=tmp_path,env={},commands=[],
        owned_containers=set(),GateError=ci.GateError,require=ci.require,
        time=SimpleNamespace(monotonic=lambda:100,sleep=lambda _:pytest.fail('no real wait')),
        Path=lambda path:Memory() if path=='/proc/meminfo' else Path(path),
        shutil=SimpleNamespace(disk_usage=lambda _:SimpleNamespace(free=20*1024**3)),
        subprocess=SimpleNamespace(Popen=lambda *a,**kw:process,STDOUT=-2),
        os=SimpleNamespace(killpg=kill),signal=SimpleNamespace(SIGKILL=9),stop_run=stop,
        small=lambda *a,**kw:SimpleNamespace(returncode=0,stdout=b''))
    with pytest.raises(ci.GateError,match='cleanup|timeout'):
        supervised_function(namespace)(['FAKE_NO_EXECUTION'],'fake.log',0,900,container='control')
    assert calls[0]==('stop','control') and calls[1]==('kill',4242) and calls[2][0]=='wait'


def test_hosted_guard_prevents_local_process_execution(monkeypatch):
    monkeypatch.delenv('GITHUB_ACTIONS',raising=False)
    def forbidden(*a,**kw): pytest.fail('no local subprocess authorized')
    monkeypatch.setattr(ci.subprocess,'run',forbidden)
    monkeypatch.setattr(ci.subprocess,'Popen',forbidden)
    with pytest.raises(ci.GateError,match='hosted manual'): ci.main()


def test_fixed_public_workflow_and_child_isolation(monkeypatch,tmp_path):
    workflow=(ROOT/'.github/workflows/riscv-acceptance.yml').read_text()
    assert 'workflow_dispatch:' in workflow and 'inputs:' not in workflow
    assert all(event not in workflow for event in ('pull_request:','push:','schedule:'))
    for text in ('timeout-minutes: 45','contents: read','persist-credentials: false',
                 'retention-days: 1','include-hidden-files: false','path: ci-artifacts/riscv/'):
        assert text in workflow
    script=SCRIPT.read_text()
    assert not any(token in script for token in ('docker.sock','--push','image save','docker login'))
    assert ci.CAP==32*1024**2
    assert not any('.coordination' in p or p.endswith('.env') or 'build_image' in p for p in ci.SOURCE_FILES)
    monkeypatch.setattr(ci.os,'getuid',lambda:1001,raising=False)
    monkeypatch.setattr(ci.os,'getgid',lambda:1001,raising=False)
    args=ci.run_command('sha256:'+'a'*64,tmp_path/'source',tmp_path/'output','control','control.json')
    for flag,value in (('--pull','never'),('--network','none'),('--user','1001:1001'),
        ('--memory','2g'),('--cpus','2'),('--pids-limit','128'),('--cap-drop','ALL'),
        ('--security-opt','no-new-privileges'),('--tmpfs','/tmp:rw,exec,nosuid,size=128m')):
        assert args[args.index(flag)+1]==value
    assert '--read-only' in args
    mounts=[args[i+1] for i,arg in enumerate(args) if arg=='--mount']
    assert len(mounts)==3 and sum(m.endswith(',readonly') for m in mounts)==2
    assert all('dst=/workspace,' not in m for m in mounts)


@pytest.mark.parametrize('label',['control','mutant','control-replay'])
def test_validator_uses_same_image_readonly_inputs_and_shared_budget(tmp_path,label):
    tree=ast.parse(SCRIPT.read_text())
    main=next(n for n in tree.body if isinstance(n,ast.FunctionDef) and n.name=='main')
    assignment=next(n for n in ast.walk(main) if isinstance(n,ast.Assign)
                    and any(isinstance(t,ast.Name) and t.id=='validation' for t in n.targets))
    image='sha256:'+'a'*64
    isolated=['docker','run','--rm','--pull','never','--user','1001:1001','--network','none',
        '--read-only','--cap-drop','ALL','--security-opt','no-new-privileges',
        '--cpus','2','--memory','2g','--pids-limit','128','--tmpfs','/tmp:rw,exec,nosuid,size=128m']
    namespace=dict(isolated=isolated,image=image,label=label,source=tmp_path/'public',
                   work=tmp_path/'work',directory=tmp_path/'work'/label)
    exec(compile(ast.fix_missing_locations(ast.Module(body=[assignment],type_ignores=[])),str(SCRIPT),'exec'),namespace)
    command=namespace['validation']
    assert command[:len(isolated)]==isolated
    assert command[-3:]==[image,'/workspace/scripts/ci_riscv_acceptance.py',
                         '--validate-mutant' if label=='mutant' else '--validate-control']
    assert command[command.index('--entrypoint')+1]=='python'
    mounts=[command[i+1] for i,arg in enumerate(command) if arg=='--mount']
    assert len(mounts)==4 and all(m.endswith(',readonly') for m in mounts)
    assert {m.split('dst=')[1].split(',')[0] for m in mounts}=={
        '/workspace/src','/workspace/scripts/ci_riscv_acceptance.py',
        '/validation/source-manifest.json','/attempt'}
    calls=[n for n in ast.walk(main) if isinstance(n,ast.Call) and isinstance(n.func,ast.Name)
           and n.func.id=='supervised' and isinstance(n.args[0],ast.Name) and n.args[0].id=='validation']
    assert len(calls)==1 and ast.literal_eval(calls[0].args[2])==10
    assert isinstance(calls[0].args[3],ast.Name) and calls[0].args[3].id=='execution_end'
    assert 'validate-'+label+'.log' in ci.META_FILES
    assert 'stop-validate-'+label+'.log' in ci.META_FILES


@pytest.mark.parametrize('args,expected',[
    (['--validate-control'],'control'),(['--validate-mutant'],'mutant'),
    (['--validate-control','extra'],'reject'),(['--arbitrary'],'reject'),([], 'main')])
def test_controller_entrypoint_has_only_two_fixed_validation_modes(args,expected):
    tree=ast.parse(SCRIPT.read_text())
    entry=tree.body[-1]
    calls=[]
    namespace=dict(__name__='__main__',sys=SimpleNamespace(argv=['controller',*args]),
        require=ci.require,validation_mode=lambda mutant:calls.append('mutant' if mutant else 'control') or 0,
        main=lambda:calls.append('main') or 0)
    with pytest.raises(ci.GateError if expected=='reject' else SystemExit):
        exec(compile(ast.fix_missing_locations(ast.Module(body=[entry],type_ignores=[])),str(SCRIPT),'exec'),namespace)
    assert calls==([] if expected=='reject' else [expected])


@pytest.mark.parametrize('platform,uid',[('nt',1001),('posix',0)])
def test_validation_mode_refuses_wrong_platform_or_root_without_reading_files(platform,uid):
    tree=ast.parse(SCRIPT.read_text())
    function=next(n for n in tree.body if isinstance(n,ast.FunctionDef) and n.name=='validation_mode')
    def forbidden(*a,**kw): pytest.fail('guard must fail before reading or validating')
    namespace=dict(require=ci.require,os=SimpleNamespace(name=platform,getuid=lambda:uid),
                   Path=forbidden,validate_attempt=forbidden,read_json=forbidden)
    exec(compile(ast.fix_missing_locations(ast.Module(body=[function],type_ignores=[])),str(SCRIPT),'exec'),namespace)
    with pytest.raises(ci.GateError,match='nonroot Linux'): namespace['validation_mode'](False)
