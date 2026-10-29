"""Hand-authored trace vectors and pure RAM/profile checks; no ISA/HDL execution."""
from dataclasses import FrozenInstanceError
import hashlib
import json
from pathlib import Path

import pytest
from pydantic import ValidationError

from veriforge.riscv import (PLATFORM, RiscvExperiment, Retirement, RVFIEvent,
    parse_spike_commits, normalize_rvfi, compare_retirements, TraceFormatError, UnsupportedTrace)
from veriforge.riscv.memory import Ram
from veriforge.riscv.profile import ROOT, CORE_BLOB, assemble_command, compile_command, reference_command


SYNTHETIC_SPIKE = """core   0: 3 0x80000000 (0x00100093) x1  0x00000001
core   0: 3 0x80000004 (0x80010537) x10 0x80010000
core   0: 3 0x80000008 (0xf0050513) x10 0x8000ff00
core   0: 3 0x8000000c (0x00152023) mem 0x8000ff00 0x00000001
"""


def raw(order=0, instruction=0x00100093, **changes):
    data = dict(rvfi_order=order, rvfi_insn=instruction, rvfi_pc_rdata=PLATFORM.entry+4*order,
        rvfi_rd_addr=1, rvfi_rd_wdata=1, rvfi_rs1_rdata=0,
        rvfi_mem_addr=0, rvfi_mem_rmask=0, rvfi_mem_wmask=0,
        rvfi_mem_rdata=0, rvfi_mem_wdata=0)
    data.update(changes)
    return data


def synthetic_dut():
    return [raw(), raw(1, 0x80010537, rvfi_rd_addr=10, rvfi_rd_wdata=0x80010000),
        raw(2, 0xf0050513, rvfi_rd_addr=10, rvfi_rd_wdata=0x8000ff00),
        raw(3, 0x00152023, rvfi_rd_addr=0, rvfi_rd_wdata=0, rvfi_rs1_rdata=0x8000ff00,
            rvfi_mem_addr=0x8000ff00, rvfi_mem_wmask=15, rvfi_mem_wdata=1)]


def test_synthetic_event_agreement_is_not_a_live_execution_claim():
    reference = parse_spike_commits(SYNTHETIC_SPIKE)
    observed = normalize_rvfi(synthetic_dut())
    report = compare_retirements(reference, observed)
    assert report.verdict == "pass_within_scope" and report.compared == 4
    assert "cycle" not in Retirement.model_fields
    assert "cycle" in report.reason  # Explicitly records no cycle comparison.
    assert report.first_divergence is None


@pytest.mark.parametrize("field,value,expected", [
    ("rvfi_rd_wdata", 2, "rd_value"), ("rvfi_rd_addr", 2, "rd"),
    ("rvfi_pc_rdata", 0x80000001, "pc"), ("rvfi_insn", 0, "instruction"),
    ("rvfi_trap", True, "trap"), ("rvfi_intr", True, "interrupt"),
])
def test_first_meaningful_architectural_divergence(field, value, expected):
    rows = synthetic_dut()
    rows[1][field] = value
    report = compare_retirements(parse_spike_commits(SYNTHETIC_SPIKE), normalize_rvfi(rows))
    assert report.verdict == "design_mismatch"
    assert report.first_divergence.order == 1 and report.first_divergence.field == expected


def test_source_locations_do_not_change_alignment():
    reference = parse_spike_commits(SYNTHETIC_SPIKE)
    assert compare_retirements(reference, [r.model_copy(update={"source_line": 500+i, "source_cycle": 900+i*20}) for i, r in enumerate(reference)]).verdict == "pass_within_scope"


def test_missing_toolchain_records_unsupported_without_execution(tmp_path, monkeypatch):
    from veriforge.riscv import runner
    monkeypatch.setattr(runner.platform, "system", lambda: "Windows")
    def forbidden(*args, **kwargs):
        pytest.fail("unsupported host must not start tools")
    monkeypatch.setattr(runner.subprocess, "run", forbidden)
    monkeypatch.setattr(runner, "_bounded", forbidden)
    result = runner.run_experiment(RiscvExperiment(experiment_id="missing-toolchain"), tmp_path)
    assert result.execution_status == "completed" and result.verdict == "unsupported"
    assert result.identity is None and result.failure_signature is None
    assert result.usage.cycles == 0 and not result.tool_versions
    assert json.loads((tmp_path / "result.json").read_text())["verdict"] == "unsupported"
    with pytest.raises(ValueError, match="empty"):
        runner.run_experiment(RiscvExperiment(experiment_id="missing-toolchain"), tmp_path)


@pytest.mark.parametrize("change", [
    {"cycles": True}, {"cycles": "21"}, {"events": {}}, {"events": None},
    {"unexpected": 1}, {"termination": "retirement_limit"},
    {"termination": "cycle_limit", "cycles": 21},
])
def test_simulator_envelope_rejects_invalid_types_and_false_stop_reasons(tmp_path, change):
    from veriforge.riscv.runner import _simulation_output
    events = synthetic_dut()
    for i, event in enumerate(events):
        event['cycle'] = 5+i*5
    envelope = dict(termination='completion_store_retired', cycles=21, events=events)
    envelope.update(change)
    path = tmp_path/'rvfi.json'
    path.write_text(json.dumps(envelope))
    with pytest.raises((ValidationError, TraceFormatError)):
        _simulation_output(path, RiscvExperiment(experiment_id='envelope-review'))


def test_truncated_output_and_events_after_stop_fail_closed(tmp_path):
    from veriforge.riscv.runner import _simulation_output
    path = tmp_path/'rvfi.json'
    plan = RiscvExperiment(experiment_id='envelope-review')
    path.write_text('{"termination":')
    with pytest.raises(ValidationError):
        _simulation_output(path, plan)
    events = synthetic_dut()
    for i, event in enumerate(events):
        event['cycle'] = 5+i*5
    events.append(raw(4, cycle=25))
    path.write_text(json.dumps(dict(termination='cycle_limit', cycles=100000, events=events)))
    with pytest.raises(TraceFormatError, match='continues'):
        _simulation_output(path, plan)


def test_real_trap_stop_is_preserved_for_architectural_comparison(tmp_path):
    from veriforge.riscv.runner import _simulation_output
    path = tmp_path/'rvfi.json'
    path.write_text(json.dumps(dict(termination='trap', cycles=11,
        events=[raw(cycle=10, rvfi_trap=True)])))
    result = _simulation_output(path, RiscvExperiment(experiment_id='envelope-review'))
    checked = compare_retirements(parse_spike_commits(SYNTHETIC_SPIKE), normalize_rvfi(result.events))
    assert checked.verdict == 'design_mismatch' and checked.first_divergence.field == 'trap'


def test_partial_empty_and_uncompleted_reference_never_pass():
    reference = parse_spike_commits(SYNTHETIC_SPIKE)
    assert compare_retirements(reference, reference[:-1]).verdict == "inconclusive"
    assert compare_retirements((), ()).verdict == "inconclusive"
    assert compare_retirements(reference[:-1], reference[:-1]).verdict == "inconclusive"


def test_malformed_tail_is_rejected_before_design_classification():
    reference = parse_spike_commits(SYNTHETIC_SPIKE)
    rows = [r.model_dump() for r in reference]
    rows[0]["rd_value"] = 2
    rows[-1]["pc"] = True
    with pytest.raises(ValidationError):
        compare_retirements(reference, rows)


def test_retirement_sequence_never_sorts_or_hides_holes():
    rows = synthetic_dut()
    rows[1]["rvfi_order"] = 2
    with pytest.raises(TraceFormatError, match="contiguous"):
        normalize_rvfi(rows)
    reference = parse_spike_commits(SYNTHETIC_SPIKE)
    with pytest.raises(TraceFormatError):
        compare_retirements(reference, reference[::-1])


def test_signed_byte_load_compares_architectural_bytes_from_fullword_ram_read():
    reference = parse_spike_commits("core 0: 3 0x80000000 (0x00208103) x2 0xffffffff mem 0x80000102")
    observed = normalize_rvfi([raw(instruction=0x00208103, rvfi_rd_addr=2, rvfi_rd_wdata=0xffffffff,
        rvfi_rs1_rdata=0x80000100, rvfi_mem_addr=0x80000100, rvfi_mem_rmask=15, rvfi_mem_rdata=0x00ff0000)])
    assert reference[0].memory == observed[0].memory
    assert observed[0].memory.value == 255 and observed[0].memory.byte_mask == 4


def test_extra_store_lanes_are_not_silently_erased():
    reference = parse_spike_commits("core 0: 3 0x80000000 (0x002080a3) mem 0x80000101 0xab")
    observed = normalize_rvfi([raw(instruction=0x002080a3, rvfi_rd_addr=0,
        rvfi_rs1_rdata=0x80000100, rvfi_mem_addr=0x80000100, rvfi_mem_wmask=6, rvfi_mem_wdata=0xab00)])
    assert reference[0].memory.value == observed[0].memory.value == 0xab
    assert reference[0].memory.byte_mask == 2 and observed[0].memory.byte_mask == 6


@pytest.mark.parametrize("line", ["garbage", "core 0: 3 0x80000000 (0x00100093)",
    "core 0: 3 0x80000000 (0x00100093) x1 0x1 x2 0x2"])
def test_malformed_reference_records_raise_harness_errors(line):
    with pytest.raises((TraceFormatError, UnsupportedTrace)):
        parse_spike_commits(line)


@pytest.mark.parametrize("line", [
    "core 1: 3 0x80000000 (0x00100093) x1 0x1",
    "core 0: 0 0x80000000 (0x00100093) x1 0x1",
    "core 0: 3 0x80000000 (0x022081b3) x3 0x1",
    "core 0: 3 0x80000000 (0x00152023) mem 0x40000000 0x1",
    "core 0: 3 0x80000000 (0x0000a003) mem 0x80000100",
])
def test_unsupported_reference_scope_never_claims_design_failure(line):
    with pytest.raises(UnsupportedTrace):
        parse_spike_commits(line)


def test_only_prefix_through_first_completion_is_in_scope():
    events = parse_spike_commits(SYNTHETIC_SPIKE+"core 0: 3 0x80000010 (0x0000006f)\n")
    assert len(events) == 4
    assert len(normalize_rvfi(synthetic_dut()+[raw(4)])) == 4


def test_native_ram_endian_strobes_and_scope():
    ram = Ram(b'\x13\x00\x00\x00')
    assert ram.read_word(PLATFORM.entry) == 0x13
    address = PLATFORM.entry+0x100
    ram.write_word(address, 0x12345678, 15)
    ram.write_word(address, 0xaabbccdd, 6)
    assert ram.read_word(address) == 0x12bbcc78
    assert ram.read_word(PLATFORM.entry+PLATFORM.ram_bytes-4) == 0
    for bad in (0x40000000, PLATFORM.entry+1, PLATFORM.entry+PLATFORM.ram_bytes, True):
        with pytest.raises(ValueError):
            ram.read_word(bad)
    with pytest.raises(ValueError):
        ram.write_word(address, 0, True)


def test_typed_plans_and_static_recipes_cannot_accept_commands():
    experiment = RiscvExperiment(experiment_id="source-only")
    assert experiment.program_id == "rv32i-smoke-v1"
    for changes in ({"command": "exec"}, {"isa": "RV64GC"}, {"max_retired": True}, {"program_id": "../../private.elf"}):
        with pytest.raises(ValidationError):
            RiscvExperiment.model_validate({**experiment.model_dump(), **changes})
    command = compile_command(Path('controller-output'))
    assert {'-march=rv32i', '-mabi=ilp32', '-mno-relax', '-nostdlib', '-nostartfiles', '-Wl,--no-relax'} <= set(command)
    ref = reference_command(Path('control.elf'), Path('commit.log'))
    assert '--pcs=0:0x80000000' in ref and '--instructions=256' in ref
    with pytest.raises(ValueError):
        reference_command(Path('control.elf'), Path('commit.log'), True)
    with pytest.raises(FrozenInstanceError):
        PLATFORM.entry = 0


def test_public_core_is_byte_identical_to_attributed_upstream_blob():
    data = (ROOT / 'targets/riscv/upstream/picorv32.v').read_bytes()
    assert hashlib.sha1(b'blob '+str(len(data)).encode()+b'\0'+data).hexdigest() == CORE_BLOB
    assert b'Permission to use, copy, modify' in data


def test_fixed_object_stage_preserves_full_elf_and_exact_commands():
    output = Path('/output')
    assembly = assemble_command(output)
    link = compile_command(output)
    assert assembly[-2:] == ('-o', str(output/'smoke.o'))
    assert '-c' in assembly and str(ROOT/'targets/riscv/smoke.S') in assembly
    assert str(output/'smoke.o') in link and str(ROOT/'targets/riscv/smoke.S') not in link
    assert link[-2:] == ('-o', str(output/'smoke.elf'))
    assert '-Wl,--build-id=none' in link and '-Wl,--no-relax' in link
    assert not any(flag in assembly+link for flag in ('-s','-Wl,-s','-Wl,--strip-all','-Wl,--discard-all'))


@pytest.mark.parametrize('assembly_exit,expected',[(9,'build_error'),(0,'timeout')])
def test_assembly_failure_or_expired_total_budget_never_links(tmp_path,monkeypatch,assembly_exit,expected):
    """Synthetic process boundary only; no assembler/compiler/reference executes."""
    from types import SimpleNamespace
    from veriforge.riscv import runner
    monkeypatch.setattr(runner.platform,'system',lambda:'Linux')
    monkeypatch.setattr(runner.shutil,'which',lambda name:'synthetic-never-executed')
    monkeypatch.setattr(runner.subprocess,'run',lambda *a,**kw:SimpleNamespace(stdout='synthetic version\n'))
    temporary = runner.tempfile.TemporaryDirectory
    monkeypatch.setattr(runner.tempfile,'TemporaryDirectory',lambda **kw:temporary(dir=tmp_path,**kw))
    original=Path.read_text
    monkeypatch.setattr(Path,'read_text',lambda path,*a,**kw:runner.REFERENCE_REVISION
        if path.as_posix()=='/opt/spike/UPSTREAM_REVISION' else original(path,*a,**kw))
    ticks=iter([0.0,0.0,121.0])
    monkeypatch.setattr(runner.time,'monotonic',lambda:next(ticks,121.0))
    calls=[]
    def assemble(command,directory,env,seconds,cancel,limit):
        calls.append((command,directory.name,seconds))
        (directory/'execution.log').write_text('SYNTHETIC: no assembly ran')
        if assembly_exit==0: (tmp_path/'attempt/smoke.o').write_bytes(b'synthetic object')
        return assembly_exit,None
    monkeypatch.setattr(runner,'_bounded',assemble)
    output=tmp_path/'attempt'
    result=runner.run_experiment(RiscvExperiment(experiment_id='synthetic-assembly-boundary'),output)
    assert result.verdict==expected and result.failure_signature is None
    assert calls==[(list(assemble_command(output)),'assembler',120.0)]
