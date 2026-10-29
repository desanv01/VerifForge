"""Stable firmware recipe review: pure preparation/fake HDL calls, no compiler."""
import hashlib
import json
from pathlib import Path
import struct
import sys
from types import SimpleNamespace

import pytest

from veriforge.firmware import executable, sim_job
from veriforge.firmware.executable import FirmwarePreparation

PHASES=('assemble_start','assemble_probe','compile_main','compiler','binary','disassembly','elf_headers')
COMPILE_FLAGS=('-march=rv32i','-mabi=ilp32','-mno-relax','-ffreestanding',
    '-fno-builtin','-fno-pic','-fno-pie','-fno-stack-protector','-msmall-data-limit=0',
    '-Os','-Wall','-Wextra','-Werror')


def test_fixed_named_object_stages_and_unchanged_link_policy(tmp_path):
    root=tmp_path.resolve();recipe=executable.commands(root)
    assert tuple(recipe)==PHASES
    for phase,source,obj in (('assemble_start','start.S','start.o'),
        ('assemble_probe','independent_probe.S','independent_probe.o'),('compile_main','main.c','main.o')):
        include=('-I',str(root)) if phase=='compile_main' else ()
        assert recipe[phase]==('riscv64-unknown-elf-gcc',*COMPILE_FLAGS,*include,
            '-c',str(root/source),'-o',str(root/obj))
    link=recipe['compiler']
    expected_flags=('-march=rv32i','-mabi=ilp32','-mno-relax','-nostdlib','-nostartfiles','-ffreestanding',
        '-fno-builtin','-fno-pic','-fno-pie','-fno-stack-protector','-msmall-data-limit=0',
        '-Os','-Wall','-Wextra','-Werror','-Wl,--no-relax','-Wl,--build-id=none')
    assert link==('riscv64-unknown-elf-gcc',*expected_flags,'-I',str(root),'-T',str(root/'link.ld'),
        str(root/'start.o'),str(root/'independent_probe.o'),str(root/'main.o'),
        '-Wl,-Map='+str(root/'firmware.map'),'-o',str(root/'firmware.elf'))
    assert '-c' not in link and not any(arg.endswith(('.c','.S')) for arg in link)
    assert not any('strip' in arg or arg in ('-s','-Wl,-s') for args in recipe.values() for arg in args)
    assert recipe['binary']==('riscv64-unknown-elf-objcopy','-O','binary',str(root/'firmware.elf'),str(root/'firmware.bin'))


@pytest.mark.parametrize('fixture',tuple(executable.FIXTURES))
def test_preparation_binds_recipe_sources_and_code_without_running_tools(tmp_path,monkeypatch,fixture):
    import subprocess
    def forbidden(*a,**kw): pytest.fail('source preparation must never start a tool')
    monkeypatch.setattr(subprocess,'run',forbidden);monkeypatch.setattr(subprocess,'Popen',forbidden)
    plan=FirmwarePreparation(fixture_id=fixture)
    metadata=executable.prepare_executable_case(plan,tmp_path)
    stored=json.loads((tmp_path/'preparation.json').read_text())
    assert metadata['execution_status']=='not_run' and metadata['recipe_status']=='prepared_not_executed'
    assert tuple(stored['recipes'])==PHASES
    assert stored['recipes']=={key:list(value) for key,value in executable.commands(tmp_path).items()}
    assert metadata['preparer_hash']==hashlib.sha256(Path(executable.__file__).read_bytes()).hexdigest()
    assert metadata['preparation']['wall_time_seconds']==120.0
    for name in ('start.S','independent_probe.S','main.c','registers.h','link.ld'):
        data=(tmp_path/name).read_bytes()
        assert metadata['source_files'][name]==dict(sha256=hashlib.sha256(data).hexdigest(),size_bytes=len(data))
    assert all(not (tmp_path/name).exists() for name in ('start.o','independent_probe.o','main.o','firmware.elf','firmware.bin'))


@pytest.fixture
def prepared(tmp_path):
    metadata=executable.prepare_executable_case(FirmwarePreparation(fixture_id='fw-regmap-control-v1'),tmp_path)
    program=b'\x13\x00\x00\x00'
    elf=(b'\x7fELF\x01\x01\x01'+b'\0'*9+
        struct.pack('<HHIIIIIHHHHHH',2,243,1,0x80000000,52,0,0,52,32,1,0,0,0)+
        struct.pack('<IIIIIIII',1,84,0x80000000,0x80000000,len(program),len(program),5,4)+program)
    (tmp_path/'firmware.elf').write_bytes(elf);(tmp_path/'firmware.bin').write_bytes(program)
    return tmp_path,metadata


@pytest.mark.parametrize('source',['start.S','independent_probe.S','main.c','link.ld','registers.h'])
def test_tampered_stage_input_is_rejected_before_hdl_build(prepared,source):
    root,_=prepared
    (root/source).write_bytes((root/source).read_bytes()+b'\n/* changed */\n')
    with pytest.raises(ValueError,match='source identity'): sim_job.validate_snapshot(root)


def test_changed_preparer_code_identity_cannot_bless_new_recipe(prepared):
    root,metadata=prepared;metadata['preparer_hash']='0'*64
    (root/'preparation.json').write_text(json.dumps(metadata))
    with pytest.raises(ValueError,match='validator identity'): sim_job.validate_snapshot(root)


@pytest.mark.parametrize('failure',['invalid-snapshot','hdl-build','none'])
def test_internal_job_fails_before_next_hdl_operation(prepared,monkeypatch,failure):
    root,_=prepared;calls=[]
    def build(**kwargs):
        calls.append('build')
        if failure=='hdl-build': raise RuntimeError('synthetic HDL build failure')
    def test(**kwargs): calls.append('test')
    fake=SimpleNamespace(get_runner=lambda name:SimpleNamespace(build=build,test=test))
    monkeypatch.setitem(sys.modules,'cocotb_tools.runner',fake)
    monkeypatch.setattr(sys,'argv',['sim_job',str(root)])
    if failure=='invalid-snapshot':
        (root/'start.S').write_bytes(b'changed')
        with pytest.raises(ValueError): sim_job.main()
        assert not calls
    elif failure=='hdl-build':
        with pytest.raises(RuntimeError): sim_job.main()
        assert calls==['build']
    else:
        sim_job.main();assert calls==['build','test']
        identity=json.loads((root/'execution-identities.json').read_text())
        assert identity['elf_sha256']==hashlib.sha256((root/'firmware.elf').read_bytes()).hexdigest()
        assert identity['binary_sha256']==hashlib.sha256((root/'firmware.bin').read_bytes()).hexdigest()
