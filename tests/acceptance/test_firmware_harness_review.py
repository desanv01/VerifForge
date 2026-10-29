"""Independent pure firmware-harness review; no compilation or HDL execution."""
import hashlib
import json
from pathlib import Path
import struct
from types import SimpleNamespace

import pytest

from veriforge.firmware.executable import FirmwarePreparation, prepare_executable_case
from veriforge.firmware.harness import check_observation
from veriforge.firmware.sim_job import validate_snapshot

PLAN = FirmwarePreparation(fixture_id="fw-regmap-control-v1")


def observation(bad=False):
    # Hand-derived literals, deliberately independent of the checker EXPECTED.
    ops = [(0x40000000, 15, 0x11223344), (0x40000000, 0, 0x11223344),
        (0x40000004, 15, 0x55667788), (0x40000004, 0, 0x55667788),
        (0x40000008, 15, 0xAABBCCDD), (0x40000008, 2, 0x00001100),
        (0x40000008, 0, 0xAABB11DD), (0x4000000c, 15, 0x13579BDF),
        (0x4000000c, 0, 0x13579BDF)]
    if bad:
        ops = ops[:4]
        ops[2], ops[3] = (0x40000008, 15, 0x55667788), (0x40000004, 0, 0)
    ops.append((0x8000ff00, 15, 2 if bad else 1))
    transfers, events = [], []
    for order, (address, strobes, value) in enumerate(ops):
        cycle = 6 + 3 * order
        transfers.append(dict(sequence=order, cycle=cycle, address=address, instruction=False,
            write_strobes=strobes, write_data=value if strobes else 0, read_data=0 if strobes else value))
        events.append(dict(cycle=cycle + 1, rvfi_order=order,
            rvfi_insn=0x002080a3 if strobes == 2 else 0x0020a023 if strobes else 0x0000a103,
            rvfi_pc_rdata=0x80000000 + 4 * order, rvfi_rd_addr=0 if strobes else 2,
            rvfi_rd_wdata=0 if strobes else value, rvfi_rs1_rdata=address,
            rvfi_mem_addr=address, rvfi_mem_wmask=strobes, rvfi_mem_rmask=0 if strobes else 15,
            rvfi_mem_wdata=value if strobes else 0, rvfi_mem_rdata=0 if strobes else value))
    return dict(termination="completion_store_retired", cycles=events[-1]["cycle"] + 1,
        transfers=transfers, events=events)


@pytest.mark.parametrize("fixture,bad,verdict", [("fw-regmap-control-v1", False, "pass_within_scope"),
    ("fw-regmap-offset-candidate-v1", False, "pass_within_scope"),
    ("fw-regmap-offset-bug-v1", True, "design_mismatch")])
def test_independent_literal_control_candidate_and_offset8_probe4(fixture, bad, verdict):
    report = check_observation(observation(bad), FirmwarePreparation(fixture_id=fixture))
    assert report.execution_status == "completed" and report.verdict == verdict
    assert report.first_mmio_sequence == (2 if bad else None)


def test_strobe_mask_and_retired_mailbox_are_independent_requirements():
    out = observation()
    out["transfers"][5]["write_data"] = out["events"][5]["rvfi_mem_wdata"] = 0x11111111
    assert check_observation(out, PLAN).verdict == "pass_within_scope"
    out["transfers"][-1]["write_data"] = out["events"][-1]["rvfi_mem_wdata"] = 2
    assert check_observation(out, PLAN).verdict == "design_mismatch"


@pytest.mark.parametrize("tail", [False, True])
def test_completed_observation_stops_at_retired_mailbox(tail):
    out = observation()
    if tail:
        out["transfers"].append(dict(sequence=len(out["transfers"]),
            cycle=out["cycles"], address=0x80000000, instruction=True,
            write_strobes=0, write_data=0, read_data=0x13))
    out["cycles"] += 5
    report = check_observation(out, PLAN)
    assert report.execution_status == "invalid_evidence", "Completed observer must stop immediately after its final retirement"


def synthetic_elf(program):
    return (b"\x7fELF\x01\x01\x01" + b"\0" * 9
        + struct.pack("<HHIIIIIHHHHHH", 2, 243, 1, 0x80000000, 52, 0, 0, 52, 32, 1, 0, 0, 0)
        + struct.pack("<IIIIIIII", 1, 84, 0x80000000, 0x80000000, len(program), len(program), 5, 4)
        + program)


@pytest.fixture
def snapshot(tmp_path):
    manifest = prepare_executable_case(PLAN, tmp_path)
    program = b"\x13\x00\x00\x00"
    (tmp_path / "firmware.elf").write_bytes(synthetic_elf(program))
    (tmp_path / "firmware.bin").write_bytes(program)
    return tmp_path, manifest


def test_elf_binary_identity_gate_preserves_exact_loaded_bytes(snapshot):
    root, manifest = snapshot
    plan, hashes = validate_snapshot(root)
    assert plan == PLAN
    for name, digest in (("firmware.elf", hashes["elf_sha256"]), ("firmware.bin", hashes["binary_sha256"])):
        assert hashlib.sha256((root / name).read_bytes()).hexdigest() == digest
    (root / "firmware.bin").write_bytes(b"\0" * 4)
    with pytest.raises(ValueError):
        validate_snapshot(root)


def test_snapshot_cannot_omit_a_built_source_identity(snapshot):
    root, manifest = snapshot
    del manifest["source_files"]["native_registers.sv"]
    (root / "preparation.json").write_text(json.dumps(manifest))
    with pytest.raises(ValueError, match="source|manifest|identity|snapshot"):
        validate_snapshot(root)


@pytest.mark.parametrize("field", ["preparer_hash", "fact_extractor_hash", "address_checker_hash"])
def test_recorded_validator_identity_is_checked_before_build(snapshot, field):
    root, manifest = snapshot
    manifest[field] = "0" * 64
    (root / "preparation.json").write_text(json.dumps(manifest))
    with pytest.raises(ValueError, match="identity|hash|prepar|checker|extract"):
        validate_snapshot(root)


def fake_collection(monkeypatch, request):
    import veriforge.firmware.sim_test as driver
    class Signal:
        value = 0
    names = ("clk", "resetn", "ram_ready", "ram_rdata", "mem_valid", "mem_ready",
        "mem_addr", "mem_instr", "mem_wstrb", "mem_wdata", "mem_rdata", "rvfi_valid")
    dut = SimpleNamespace(**{name: Signal() for name in names})
    class Suspend:
        def __await__(self):
            yield
    cycle = -1
    def timer(amount, **kwargs):
        nonlocal cycle
        if amount == 2:
            cycle += 1
            values = request(cycle)
            for name, value in values.items():
                getattr(dut, name).value = value
            dut.mem_ready.value = 0
        if amount == 3:
            dut.mem_ready.value = int(cycle in (6, 7))
        return Suspend()
    monkeypatch.setattr(driver, "Timer", timer)
    coroutine = driver.collect(dut, b"\x13\0\0\0", PLAN)
    try:
        for _ in range(50):
            try:
                coroutine.send(None)
            except StopIteration as done:
                return done.value
        pytest.fail("fake request sequence failed to settle")
    finally:
        coroutine.close()


def test_held_valid_records_each_accepted_edge_once(monkeypatch):
    def request(cycle):
        return dict(mem_valid=int(cycle >= 5), mem_addr=0 if cycle >= 8 else 0x40000000,
            mem_instr=0, mem_wstrb=15, mem_wdata=0x11223344, mem_rdata=0)
    output = fake_collection(monkeypatch, request)
    assert output.termination == "protocol_error"
    assert [(r.sequence, r.cycle) for r in output.transfers] == [(0, 6), (1, 7)]


def test_instruction_side_write_is_rejected_before_ram_side_effect(monkeypatch):
    def request(cycle):
        return dict(mem_valid=int(cycle >= 5), mem_addr=0 if cycle >= 8 else 0x80000000,
            mem_instr=1, mem_wstrb=15, mem_wdata=0xffffffff, mem_rdata=0)
    output = fake_collection(monkeypatch, request)
    assert output.termination == "protocol_error"
    assert not output.transfers, "Instruction-side writes must be refused, not accepted into RAM"
