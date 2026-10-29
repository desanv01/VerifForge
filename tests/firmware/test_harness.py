"""Handwritten evidence and fake scheduling only; no HDL compiler/core is used."""
import json
from types import SimpleNamespace
import pytest
from veriforge.firmware.executable import FirmwarePreparation, prepare_executable_case
from veriforge.firmware.harness import EXPECTED, HarnessOutput, check_observation, lane_mask, region
from veriforge.firmware.sim_job import build_recipe, validate_snapshot

PLAN = FirmwarePreparation(fixture_id="fw-regmap-control-v1")


def observation(bad=False):
    operations = list(EXPECTED)
    if bad:
        operations = operations[:4]
        operations[2] = (0x40000008, 15, 0x55667788)
        operations[3] = (0x40000004, 0, 0)
    operations.append((0x8000FF00, 15, 2 if bad else 1))
    buses, events = [], []
    for index, (address, strobes, value) in enumerate(operations):
        cycle = 6 + index * 3
        buses.append(dict(sequence=index, cycle=cycle, address=address, instruction=False,
                          write_strobes=strobes, write_data=value if strobes else 0, read_data=value if not strobes else 0))
        events.append(dict(cycle=cycle+1, rvfi_order=index, rvfi_insn=0x23 if strobes else 0x03,
            rvfi_pc_rdata=0x80000000 + 4*index, rvfi_rd_addr=0, rvfi_rd_wdata=0, rvfi_rs1_rdata=0,
            rvfi_mem_addr=address, rvfi_mem_wmask=strobes, rvfi_mem_rmask=0 if strobes else 15,
            rvfi_mem_wdata=value if strobes else 0, rvfi_mem_rdata=value if not strobes else 0))
    return dict(termination="completion_store_retired", cycles=events[-1]["cycle"]+1, transfers=buses, events=events)


@pytest.mark.parametrize("bad,verdict,first", [(False, "pass_within_scope", None), (True, "design_mismatch", 2)])
def test_independent_requirements_distinguish_wrong_status_address(bad, verdict, first):
    result = check_observation(observation(bad), PLAN)
    assert (result.execution_status, result.verdict, result.first_mmio_sequence) == ("completed", verdict, first)


@pytest.mark.parametrize("mutation", ["sequence", "duplicate", "alignment", "unmapped", "fetch_mmio", "write_fetch",
    "order", "reset", "pc", "entry", "interrupt", "readwrite", "bus_address", "bus_data", "missing", "mailbox_mask", "mailbox_value", "late", "extra", "bound"])
def test_evidence_corruption_cannot_become_design_failure(mutation):
    out = observation()
    bus, event = out["transfers"][0], out["events"][0]
    if mutation == "sequence": bus["sequence"] = 1
    if mutation == "duplicate": out["transfers"][1]["cycle"] = bus["cycle"]
    if mutation == "alignment": bus["address"] += 1
    if mutation == "unmapped": bus["address"] = 0
    if mutation == "fetch_mmio": bus["instruction"] = True
    if mutation == "write_fetch": bus.update(instruction=True, address=0x80000000)
    if mutation == "order": event["rvfi_order"] = 1
    if mutation == "reset": event["cycle"] = 4
    if mutation == "pc": event["rvfi_pc_rdata"] = 0x40000000
    if mutation == "entry": event["rvfi_pc_rdata"] += 4
    if mutation == "interrupt": event["rvfi_intr"] = True
    if mutation == "readwrite": event["rvfi_mem_rmask"] = 15
    if mutation == "bus_address": event["rvfi_mem_addr"] += 4
    if mutation == "bus_data": event["rvfi_mem_wdata"] ^= 1
    if mutation == "missing": out["transfers"].pop()
    if mutation == "mailbox_mask": out["events"][-1]["rvfi_mem_wmask"] = out["transfers"][-1]["write_strobes"] = 1
    if mutation == "mailbox_value": out["events"][-1]["rvfi_mem_wdata"] = out["transfers"][-1]["write_data"] = 3
    if mutation == "late": event["cycle"] = bus["cycle"] - 1
    if mutation == "extra": out["surprise"] = "unknown"
    if mutation == "bound": out["cycles"] = PLAN.max_cycles + 1
    assert check_observation(out, PLAN).execution_status == "invalid_evidence"


@pytest.mark.parametrize("reason", ["cycle_limit", "retirement_limit", "trap", "protocol_error"])
def test_incomplete_execution_never_proves_a_design_defect(reason):
    out = observation(True)
    out["events"].pop()
    out["transfers"].pop()
    out["termination"] = reason
    if reason == "cycle_limit": out["cycles"] = PLAN.max_cycles
    if reason == "retirement_limit":
        # Not reaching the declared limit is malformed metadata, not a design defect.
        assert check_observation(out, PLAN).execution_status == "invalid_evidence"
        return
    assert check_observation(out, PLAN).verdict == "inconclusive"


def test_byte_write_only_changes_selected_lane_and_reads_are_independent():
    assert lane_mask(2) == 0xFF00
    out = observation()
    # Whole-word replication by a byte-store producer is legal: only WSTRB lanes matter.
    out["transfers"][5]["write_data"] = out["events"][5]["rvfi_mem_wdata"] = 0x11111111
    assert check_observation(out, PLAN).verdict == "pass_within_scope"
    out["transfers"][6]["read_data"] ^= 0x100
    out["events"][6]["rvfi_mem_rdata"] ^= 0x100
    assert check_observation(out, PLAN).first_mmio_sequence == 6


def test_snapshot_hashes_wrapper_core_checker_and_fixed_verilator_recipe(tmp_path):
    manifest = prepare_executable_case(PLAN, tmp_path)
    assert {"firmware_top.sv", "picorv32.v", "picorv32_top.sv"} <= manifest["source_files"].keys()
    assert set(manifest["runtime_files"]) == {"harness.py", "sim_job.py", "sim_test.py"}
    recipe = build_recipe(tmp_path)
    assert recipe["defines"] == {"RISCV_FORMAL": 1} and recipe["hdl_toplevel"] == "firmware_top"
    assert [path.name for path in recipe["sources"]] == ["picorv32.v", "picorv32_top.sv", "native_registers.sv", "firmware_top.sv"]
    (tmp_path / "firmware_top.sv").write_text("changed")
    with pytest.raises(ValueError, match="identity changed"): validate_snapshot(tmp_path)


def minimal_elf(program):
    import struct
    header = b"\x7fELF\x01\x01\x01" + b"\0"*9
    header += struct.pack("<HHIIIIIHHHHHH", 2, 243, 1, 0x80000000, 52, 0, 0, 52, 32, 1, 0, 0, 0)
    return header + struct.pack("<IIIIIIII", 1, 84, 0x80000000, 0x80000000, len(program), len(program), 5, 4) + program


@pytest.mark.parametrize("mutation", [None, "binary", "elf_flags", "elf_reserved", "runtime_hash"])
def test_elf_binary_and_runtime_identity_gate_with_synthetic_elf(tmp_path, mutation):
    import struct
    manifest = prepare_executable_case(PLAN, tmp_path)
    program = b"\x13\x00\x00\x00"
    elf = bytearray(minimal_elf(program))
    if mutation == "elf_flags": struct.pack_into("<I", elf, 36, 1)
    if mutation == "elf_reserved": struct.pack_into("<I", elf, 72, 0xF000)
    if mutation == "runtime_hash":
        manifest["runtime_files"]["harness.py"] = "0"*64
        (tmp_path / "preparation.json").write_text(json.dumps(manifest))
    (tmp_path / "firmware.elf").write_bytes(elf)
    (tmp_path / "firmware.bin").write_bytes(b"\0"*4 if mutation == "binary" else program)
    if mutation:
        with pytest.raises(ValueError): validate_snapshot(tmp_path)
    else:
        plan, identities = validate_snapshot(tmp_path)
        assert plan == PLAN and len(identities["elf_sha256"]) == 64


@pytest.mark.parametrize("mutation", ["missing_peripheral", "extra_source", "missing_candidate", "preparer_hash", "fact_extractor_hash", "address_checker_hash"])
def test_snapshot_requires_complete_sources_and_validator_identities(tmp_path, mutation):
    plan = FirmwarePreparation(fixture_id="fw-regmap-offset-candidate-v1")
    manifest = prepare_executable_case(plan, tmp_path)
    if mutation == "missing_peripheral": del manifest["source_files"]["native_registers.sv"]
    elif mutation == "missing_candidate": del manifest["source_files"]["candidate.diff"]
    elif mutation == "extra_source": manifest["source_files"]["extra.c"] = {"sha256": "0"*64, "size_bytes": 0}
    else: manifest[mutation] = "0"*64
    (tmp_path / "preparation.json").write_text(json.dumps(manifest))
    with pytest.raises(ValueError, match="identity|source set"): validate_snapshot(tmp_path)


@pytest.mark.parametrize("tail", [False, True])
def test_completed_envelope_ends_on_the_mailbox_retirement_cycle(tail):
    out = observation()
    if tail:
        out["transfers"].append(dict(sequence=len(out["transfers"]), cycle=out["cycles"], address=0x80000000,
                                    instruction=True, write_strobes=0, write_data=0, read_data=0x13))
    out["cycles"] += 3
    assert check_observation(out, PLAN).execution_status == "invalid_evidence"


@pytest.mark.parametrize("address,kind", [(0x40000000,"mmio"), (0x4000000f,"mmio"), (0x80000000,"ram"), (0x8000ffff,"ram")])
def test_full_windows(address, kind):
    assert region(address) == kind


@pytest.mark.parametrize("instruction_write", [False, True])
def test_fake_driver_accepts_each_settled_edge_once_and_observes_peripheral(monkeypatch, instruction_write):
    import veriforge.firmware.sim_test as driver
    class Signal:
        value = 0
    signals = ["clk", "resetn", "ram_ready", "ram_rdata", "mem_valid", "mem_ready", "mem_addr", "mem_instr", "mem_wstrb", "mem_wdata", "mem_rdata", "rvfi_valid"]
    dut = SimpleNamespace(**{key: Signal() for key in signals})
    class YieldOnce:
        def __await__(self):
            yield
    cycle = -1
    def timer(amount, **kwargs):
        nonlocal cycle
        if amount == 2:
            cycle += 1
            # Ready low until settled; tests sampling at accepted pre-edge phase.
            dut.mem_valid.value = int(cycle in (5, 6, 7))
            dut.mem_ready.value = 0
            dut.mem_addr.value = 0 if cycle == 7 else 0x80000000 if instruction_write else 0x40000000
            dut.mem_instr.value = int(instruction_write)
            dut.mem_wstrb.value = 15 if cycle == 5 else 0
            dut.mem_wdata.value = 0x12345678
            dut.mem_rdata.value = 0x12345678
        if amount == 3:
            dut.mem_ready.value = dut.mem_valid.value
        if amount == 5:
            assert dut.ram_ready.value == 0  # No Python peripheral emulation.
        return YieldOnce()
    monkeypatch.setattr(driver, "Timer", timer)
    # Break via a deliberate invalid request, without creating an OS event loop.
    coroutine = driver.collect(dut, b"\x13\x00\x00\x00", PLAN)
    for _ in range(50):
        try: coroutine.send(None)
        except StopIteration as stop:
            result = stop.value
            break
    else: pytest.fail("fake driver did not terminate")
    assert result.termination == "protocol_error"
    assert [(bus.sequence, bus.cycle, bus.write_strobes) for bus in result.transfers] == ([] if instruction_write else [(0,5,15),(1,6,0)])
