"""Independent pure/synthetic source review. No compiler, simulator or model runs."""
import hashlib
import json
from pathlib import Path
from types import SimpleNamespace

import pytest
from pydantic import ValidationError

from veriforge.riscv import RiscvExperiment, compare_retirements, normalize_rvfi, parse_spike_commits
from veriforge.riscv.profile import PLATFORM
from veriforge.riscv import runner
from veriforge.riscv.trace import TraceFormatError


REFERENCE = (
    "core   0: 3 0x80000000 (0x00100093) x1  0x00000001\n"
    "core   0: 3 0x80000004 (0x00152023) mem 0x8000ff00 0x00000001\n"
)


def raw(order=0, **changes):
    row = dict(cycle=10 + 10 * order, rvfi_order=order, rvfi_insn=0x00100093,
        rvfi_pc_rdata=PLATFORM.entry + 4 * order, rvfi_rd_addr=1, rvfi_rd_wdata=1,
        rvfi_rs1_rdata=0, rvfi_mem_addr=0, rvfi_mem_rmask=0, rvfi_mem_wmask=0,
        rvfi_mem_rdata=0, rvfi_mem_wdata=0, rvfi_trap=False, rvfi_intr=False)
    row.update(changes)
    return row


def rows():
    return [raw(), raw(1, rvfi_insn=0x00152023, rvfi_rd_addr=0, rvfi_rd_wdata=0,
        rvfi_rs1_rdata=PLATFORM.completion, rvfi_mem_addr=PLATFORM.completion,
        rvfi_mem_wmask=15, rvfi_mem_wdata=1)]


@pytest.mark.parametrize("length", [0, 1, 3])
def test_length_mismatch_never_exposes_an_out_of_range_divergence(length):
    reference = parse_spike_commits(REFERENCE)
    observed = normalize_rvfi(rows())
    if length <= 1:
        observed = observed[:length]
    else:
        observed += (observed[-1].model_copy(update={"order": 2}),)
    checked = compare_retirements(reference, observed)
    assert checked.verdict == "inconclusive" and checked.first_divergence is None


def test_architecture_ignores_different_cycle_and_line_metadata():
    expected = parse_spike_commits(REFERENCE)
    got = normalize_rvfi(rows())
    assert expected[0].source_line != got[0].source_cycle
    assert compare_retirements(expected, got).verdict == "pass_within_scope"
    bad = list(got)
    bad[0] = bad[0].model_copy(update={"rd_value": 2})
    mismatch = compare_retirements(expected, bad)
    assert mismatch.first_divergence.order == 0 and mismatch.first_divergence.field == "rd_value"


@pytest.mark.parametrize("funct3,offset,width,rd_value", [
    (0, 1, 1, 0xffffff80), (4, 3, 1, 0x80),
    (1, 2, 2, 0xffff8000), (5, 0, 2, 0x8000), (2, 0, 4, 0x80000000),
])
def test_independent_load_byte_lane_and_sign_normalization(funct3, offset, width, rd_value):
    instruction = (offset << 20) | (1 << 15) | (funct3 << 12) | (2 << 7) | 3
    address = PLATFORM.entry + 0x100 + offset
    reference = parse_spike_commits(
        f"core 0: 3 0x80000000 (0x{instruction:08x}) x2 0x{rd_value:08x} mem 0x{address:08x}\n")
    value = rd_value & ((1 << (width * 8)) - 1)
    observed = normalize_rvfi([raw(rvfi_insn=instruction, rvfi_rd_addr=2,
        rvfi_rd_wdata=rd_value, rvfi_rs1_rdata=PLATFORM.entry + 0x100,
        rvfi_mem_addr=PLATFORM.entry + 0x100, rvfi_mem_rmask=15,
        rvfi_mem_rdata=value << (offset * 8))])
    assert reference[0].memory == observed[0].memory
    assert observed[0].memory.value == value
    assert observed[0].memory.byte_mask == ((1 << width) - 1) << offset


@pytest.mark.parametrize("change", [{"rvfi_order": 1}, {"rvfi_rd_wdata": True}, {"rvfi_pc_rdata": -1}])
def test_malformed_event_never_becomes_a_design_verdict(change):
    with pytest.raises((ValidationError, TraceFormatError)):
        normalize_rvfi([raw(**change)])


@pytest.fixture
def fake_tools(monkeypatch, tmp_path):
    """Only fake _bounded/subprocess outputs; the production parser/checker runs."""
    binary = tmp_path / "fake-spike"
    binary.write_bytes(b"synthetic binary marker; never executed")
    monkeypatch.setattr(runner.platform, "system", lambda: "Linux")
    monkeypatch.setattr(runner.shutil, "which", lambda name: str(binary))
    monkeypatch.setattr(runner.importlib.metadata, "version", lambda name: "2.0.1")
    monkeypatch.setattr(runner.subprocess, "run", lambda *a, **kw: SimpleNamespace(stdout="synthetic tool version\n"))
    temporary_directory = runner.tempfile.TemporaryDirectory
    monkeypatch.setattr(runner.tempfile, "TemporaryDirectory", lambda **kwargs: temporary_directory(dir=tmp_path, **kwargs))
    original_read = Path.read_text

    def read(path, *args, **kwargs):
        if path.as_posix() == "/opt/spike/UPSTREAM_REVISION":
            return runner.REFERENCE_REVISION
        if path.as_posix() == "/opt/spike/UPSTREAM_SUBMODULES":
            return "synthetic submodule identity"
        return original_read(path, *args, **kwargs)

    original_bytes = Path.read_bytes
    monkeypatch.setattr(Path, "read_text", read)
    monkeypatch.setattr(Path, "read_bytes", lambda path: b"synthetic packages" if path.as_posix() == "/opt/veriforge/PACKAGE_VERSIONS" else original_bytes(path))

    def run(envelope):
        output = tmp_path / "attempt"
        calls = []

        def execute(command, directory, env, seconds, cancel, byte_limit):
            phase = directory.name
            calls.append((phase, seconds, byte_limit))
            (directory / "execution.log").write_text("SYNTHETIC: no tool process ran")
            if phase == "compiler":
                (output / "smoke.elf").write_bytes(b"synthetic ELF")
            elif phase == "objcopy":
                (output / "smoke.bin").write_bytes(b"synthetic instructions")
            elif phase == "reference":
                (directory / "commit.log").write_text(REFERENCE)
            elif phase == "dut":
                (directory / "built.marker").write_text("synthetic build marker")
                (directory / "rvfi.json").write_text(json.dumps(envelope))
            return 0, None

        monkeypatch.setattr(runner, "_bounded", execute)
        result = runner.run_experiment(RiscvExperiment(experiment_id="synthetic-review"), output)
        return result, output, calls

    return run


def test_runner_synthetic_evidence_identity_and_partial_trace(fake_tools):
    result, output, calls = fake_tools({"termination": "cycle_limit", "cycles": 100000, "events": rows()[:1]})
    assert result.verdict == "resource_limit" and result.failure_signature is None
    assert result.identity is not None
    assert [c[0] for c in calls] == ["compiler", "objcopy", "reference", "dut"]
    assert all(0 < c[1] <= 120 and c[2] == 32 * 1024 * 1024 for c in calls)
    for artifact in result.artifacts:
        data = (output / artifact.path).read_bytes()
        assert len(data) == artifact.size_bytes
        assert hashlib.sha256(data).hexdigest() == artifact.sha256


@pytest.mark.parametrize("change", [
    {"events": None}, {"termination": "unknown"}, {"termination": "trap"}, {"cycles": 100001},
])
def test_runner_rejects_malformed_or_inconsistent_trace_envelope(fake_tools, change):
    envelope = {"termination": "completion_store_retired", "cycles": 21, "events": rows()}
    envelope.update(change)
    result, output, _ = fake_tools(envelope)
    assert result.execution_status == "infrastructure_error"
    assert result.verdict == "harness_error" and result.failure_signature is None
    assert (output / "result.json").exists()


def test_source_manifest_covers_ram_semantics(fake_tools):
    result, output, _ = fake_tools({"termination": "completion_store_retired", "cycles": 21, "events": rows()})
    assert result.verdict == "pass_within_scope"
    environment = json.loads((output / "environment.json").read_text())
    assert environment["memory_hash"] == hashlib.sha256(Path(runner.__file__).with_name("memory.py").read_bytes()).hexdigest()


def test_expired_total_deadline_never_dispatches_another_phase(fake_tools, monkeypatch):
    ticks = iter([0.0, 121.0])
    monkeypatch.setattr(runner.time, "monotonic", lambda: next(ticks, 121.0))
    result, _, calls = fake_tools({"termination": "completion_store_retired", "cycles": 21, "events": rows()})
    assert not calls
    assert result.verdict == "timeout" and result.failure_signature is None
