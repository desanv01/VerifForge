"""Firmware-only observation driver. Peripheral behavior lives in RTL, not here."""
import json
import os
from pathlib import Path
import cocotb
from cocotb.triggers import Timer
from veriforge.riscv.contracts import RVFIEvent
from veriforge.riscv.memory import Ram
from veriforge.riscv.profile import PLATFORM
from .executable import FirmwarePreparation
from .harness import BusTransfer, HarnessOutput, region


async def collect(dut, program, plan):
    plan = FirmwarePreparation.model_validate(plan)
    # Linker reserves upper RAM for stack/mailbox. No preloaded completion value.
    if not program or len(program) > 0xE000:
        raise ValueError("firmware image outside reviewed linker bounds")
    ram = Ram(program)
    events, transfers, reason = [], [], "cycle_limit"
    for cycle in range(plan.max_cycles):
        dut.clk.value = 0
        dut.resetn.value = int(cycle >= 5)
        dut.ram_ready.value = 0
        dut.ram_rdata.value = 0
        await Timer(2, unit="ns")
        pending = None
        protocol_error = False
        if cycle >= 5 and int(dut.mem_valid.value):
            address = int(dut.mem_addr.value)
            try:
                kind = region(address)
                if int(dut.mem_instr.value) and int(dut.mem_wstrb.value):
                    raise ValueError("instruction request cannot write memory")
                if address % 4 or (kind == "mmio" and int(dut.mem_instr.value)):
                    raise ValueError("unaligned or MMIO instruction request")
                if kind == "ram":
                    dut.ram_rdata.value = ram.read_word(address)
                    dut.ram_ready.value = 1
            except ValueError:
                protocol_error = True
        await Timer(3, unit="ns")
        # Settled pre-edge valid && ready is exactly the synchronous acceptance.
        if not protocol_error and cycle >= 5 and int(dut.mem_valid.value) and int(dut.mem_ready.value):
            pending = BusTransfer(sequence=len(transfers), cycle=cycle,
                address=int(dut.mem_addr.value), instruction=bool(int(dut.mem_instr.value)),
                write_strobes=int(dut.mem_wstrb.value), write_data=int(dut.mem_wdata.value),
                read_data=int(dut.mem_rdata.value))
        dut.clk.value = 1
        await Timer(5, unit="ns")
        if pending is not None:
            transfers.append(pending)
            if not pending.instruction and region(pending.address) == "ram":
                ram.write_word(pending.address, pending.write_data, pending.write_strobes)
        if cycle < 5 and int(dut.rvfi_valid.value):
            protocol_error = True
        if protocol_error:
            reason = "protocol_error"
            break
        if cycle >= 5 and int(dut.rvfi_valid.value):
            values = {name: int(getattr(dut, name).value) for name in RVFIEvent.model_fields if name.startswith("rvfi_")}
            values.update(cycle=cycle, rvfi_trap=bool(values["rvfi_trap"]), rvfi_intr=bool(values["rvfi_intr"]))
            row = RVFIEvent(**values)
            events.append(row)
            if row.rvfi_trap:
                reason = "trap"
                break
            if row.rvfi_mem_addr == PLATFORM.completion and row.rvfi_mem_wmask:
                reason = "completion_store_retired"
                break
            if len(events) >= plan.max_retired:
                reason = "retirement_limit"
                break
        if len(transfers) >= 65536:
            reason = "transfer_limit"
            break
    return HarnessOutput(termination=reason, cycles=cycle+1, events=events, transfers=transfers)


@cocotb.test()
async def observe(dut):
    job = json.loads(Path(os.environ["VF_FIRMWARE_JOB"]).read_text())
    result = await collect(dut, Path(job["binary"]).read_bytes(), FirmwarePreparation.model_validate(job["preparation"]))
    Path(job["trace"]).write_text(result.model_dump_json(indent=2))
