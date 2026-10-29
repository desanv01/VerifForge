"""Firmware bus evidence and independent literal MMIO checker; no simulator imports."""
from typing import Annotated, Literal
from pydantic import Field, StrictBool, StrictInt
from veriforge.contracts import WireModel
from veriforge.riscv.contracts import RVFIEvent, U32
from veriforge.riscv.profile import PLATFORM
from .executable import FirmwarePreparation

MMIO_BASE, MMIO_END = 0x40000000, 0x40000010


def region(address):
    if MMIO_BASE <= address < MMIO_END:
        return "mmio"
    if PLATFORM.ram_base <= address < PLATFORM.ram_base + PLATFORM.ram_bytes:
        return "ram"
    raise ValueError("address outside reviewed RAM/MMIO")


class BusTransfer(WireModel):
    sequence: Annotated[StrictInt, Field(ge=0, le=65535)]
    cycle: Annotated[StrictInt, Field(ge=5, lt=1000000)]
    address: U32
    instruction: StrictBool
    write_strobes: Annotated[StrictInt, Field(ge=0, le=15)]
    write_data: U32
    read_data: U32


class HarnessOutput(WireModel):
    schema_version: Literal["firmware.native-observation.v1"] = "firmware.native-observation.v1"
    termination: Literal["completion_store_retired", "trap", "cycle_limit", "retirement_limit", "protocol_error", "transfer_limit"]
    cycles: Annotated[StrictInt, Field(ge=1, le=1000000)]
    events: Annotated[list[RVFIEvent], Field(strict=True, max_length=10000)]
    transfers: Annotated[list[BusTransfer], Field(strict=True, max_length=65536)]


class HarnessCheck(WireModel):
    execution_status: Literal["completed", "bounded_incomplete", "invalid_evidence"]
    verdict: Literal["pass_within_scope", "design_mismatch", "inconclusive"]
    reason: str
    first_mmio_sequence: int | None = None


# Independent reviewed requirements. Never derived from firmware headers or DUT decode.
EXPECTED = (
    (0x40000000, 15, 0x11223344), (0x40000000, 0, 0x11223344),
    (0x40000004, 15, 0x55667788), (0x40000004, 0, 0x55667788),
    (0x40000008, 15, 0xAABBCCDD), (0x40000008, 2, 0x00001100),
    (0x40000008, 0, 0xAABB11DD), (0x4000000C, 15, 0x13579BDF),
    (0x4000000C, 0, 0x13579BDF),
)


def lane_mask(strobes):
    return sum(255 << (8 * lane) for lane in range(4) if strobes & (1 << lane))


def check_observation(output: HarnessOutput, plan: FirmwarePreparation):
    """Reject malformed evidence first; an exhausted budget never proves a defect."""
    try:
        output = HarnessOutput.model_validate(output)
        plan = FirmwarePreparation.model_validate(plan)
        return _check(output, plan)
    except (ValueError, TypeError) as error:
        return HarnessCheck(execution_status="invalid_evidence", verdict="inconclusive", reason=str(error))


def _check(out, plan):
    if out.cycles > plan.max_cycles or len(out.events) > plan.max_retired:
        raise ValueError("observation exceeds job bounds")
    pending, mmio, registers = [], [], [0] * 4
    discrepancy = None
    previous = 4
    for index, bus in enumerate(out.transfers):
        if bus.sequence != index or not previous < bus.cycle < out.cycles or bus.address % 4:
            raise ValueError("bus sequence/cycle/alignment invalid")
        previous = bus.cycle
        kind = region(bus.address)
        if bus.instruction:
            if kind != "ram" or bus.write_strobes:
                raise ValueError("instruction bus access outside RAM or writes")
            continue
        pending.append(bus)
        if kind == "mmio":
            number = (bus.address - MMIO_BASE) // 4
            observed = (bus.address, bus.write_strobes,
                        bus.write_data & lane_mask(bus.write_strobes) if bus.write_strobes else bus.read_data)
            if discrepancy is None and (len(mmio) >= len(EXPECTED) or observed != EXPECTED[len(mmio)]):
                discrepancy = len(mmio)
            if not bus.write_strobes and bus.read_data != registers[number] and discrepancy is None:
                discrepancy = len(mmio)
            if bus.write_strobes:
                mask = lane_mask(bus.write_strobes)
                registers[number] = (registers[number] & ~mask) | (bus.write_data & mask)
            mmio.append(bus)
    previous_cycle, completion, consumed = 4, None, 0
    for order, row in enumerate(out.events):
        if order == 0 and row.rvfi_pc_rdata != PLATFORM.entry:
            raise ValueError("first retirement differs from reset entry")
        if row.rvfi_order != order or row.cycle is None or not previous_cycle < row.cycle < out.cycles:
            raise ValueError("retirement order/cycle invalid")
        previous_cycle = row.cycle
        if row.rvfi_pc_rdata % 4 or region(row.rvfi_pc_rdata) != "ram" or row.rvfi_intr:
            raise ValueError("PC/interrupt outside reviewed profile")
        if row.rvfi_mem_rmask and row.rvfi_mem_wmask:
            raise ValueError("simultaneous read/write retirement")
        if completion is not None:
            raise ValueError("retirement after completion")
        if row.rvfi_trap:
            if order != len(out.events) - 1 or out.termination != "trap":
                raise ValueError("trap termination inconsistent")
            continue
        mask = row.rvfi_mem_wmask or row.rvfi_mem_rmask
        if mask:
            if consumed >= len(pending):
                raise ValueError("retirement lacks accepted data transfer")
            bus = pending[consumed]
            consumed += 1
            if bus.cycle > row.cycle or row.rvfi_mem_addr != bus.address:
                raise ValueError("bus/retirement address or chronology differs")
            if row.rvfi_mem_wmask:
                if row.rvfi_mem_wmask != bus.write_strobes or (row.rvfi_mem_wdata ^ bus.write_data) & lane_mask(mask):
                    raise ValueError("bus/retirement write differs")
            elif bus.write_strobes or (row.rvfi_mem_rdata ^ bus.read_data) & lane_mask(mask):
                raise ValueError("bus/retirement read differs")
            if row.rvfi_mem_addr == PLATFORM.completion and row.rvfi_mem_wmask:
                if mask != 15 or row.rvfi_mem_wdata not in (1, 2):
                    raise ValueError("completion must be aligned full-word 1 or 2")
                completion = row.rvfi_mem_wdata
    if out.termination == "completion_store_retired":
        if completion is None or consumed != len(pending) or out.events[-1].cycle != out.cycles - 1:
            raise ValueError("completion or outstanding data transfers inconsistent")
        if completion == 1 and len(mmio) == len(EXPECTED) and discrepancy is None:
            return HarnessCheck(execution_status="completed", verdict="pass_within_scope", reason="Reviewed MMIO sequence, byte lanes and retired success mailbox agree")
        return HarnessCheck(execution_status="completed", verdict="design_mismatch", first_mmio_sequence=discrepancy,
                            reason="Completed firmware differs from reviewed MMIO requirements or returned failure mailbox")
    if completion is not None:
        raise ValueError("completion retirement has wrong termination")
    if out.termination == "retirement_limit" and len(out.events) != plan.max_retired:
        raise ValueError("retirement limit not reached")
    if out.termination == "cycle_limit" and out.cycles != plan.max_cycles:
        raise ValueError("cycle limit not reached")
    if out.termination == "transfer_limit" and len(out.transfers) != 65536:
        raise ValueError("transfer limit not reached")
    return HarnessCheck(execution_status="bounded_incomplete", verdict="inconclusive", reason="No completed firmware observation; termination=" + out.termination)
