"""Normalize architecture events from separate DUT/reference producers, not cycles."""
import re
from .contracts import MemoryEffect, Retirement, RVFIEvent
from .profile import PLATFORM


class TraceFormatError(ValueError):
    pass


class UnsupportedTrace(ValueError):
    pass


def signed(value, bits):
    return value - (1 << bits) if value & (1 << (bits-1)) else value


def _shape(instruction):
    opcode, funct3, funct7 = instruction & 127, instruction >> 12 & 7, instruction >> 25
    if instruction & 3 != 3:
        raise UnsupportedTrace("compressed/variable-length instructions are outside RV32I profile")
    if opcode in (0x03, 0x23):
        widths = {0: 1, 1: 2, 2: 4, 4: 1, 5: 2} if opcode == 0x03 else {0: 1, 1: 2, 2: 4}
        if funct3 not in widths:
            raise UnsupportedTrace("unsupported memory instruction")
        return opcode, widths[funct3]
    if opcode == 0x33 and not (funct7 == 0 or funct7 == 32 and funct3 in (0, 5)):
        raise UnsupportedTrace("M/custom register operation outside RV32I")
    if opcode == 0x13 and funct3 in (1, 5) and not (funct7 == 0 or funct7 == 32 and funct3 == 5):
        raise UnsupportedTrace("unsupported shift-immediate encoding")
    if opcode == 0x63 and funct3 not in (0, 1, 4, 5, 6, 7):
        raise UnsupportedTrace("unsupported branch encoding")
    if opcode == 0x67 and funct3 != 0:
        raise UnsupportedTrace("unsupported jalr encoding")
    if opcode not in (0x13, 0x33, 0x37, 0x17, 0x6F, 0x67, 0x63):
        raise UnsupportedTrace("instruction outside initial ALU/branch/load/store profile")
    return opcode, None


def _completion(event):
    return event.memory is not None and event.memory.kind == "write" and event.memory.address == PLATFORM.completion


def normalize_rvfi(rows, *, max_retired=10000):
    events = []
    for row in rows:
        if len(events) >= max_retired:
            raise TraceFormatError("retirement trace exceeds bound")
        raw = RVFIEvent.model_validate(row.model_dump() if isinstance(row, RVFIEvent) else row)
        if raw.rvfi_order != len(events):
            raise TraceFormatError("DUT retirement order must start at zero and be contiguous")
        try:
            opcode, width = _shape(raw.rvfi_insn)
        except UnsupportedTrace:
            # An unsupported observed encoding may itself be the DUT discrepancy.
            opcode, width = raw.rvfi_insn & 127, None
        read, write = raw.rvfi_mem_rmask, raw.rvfi_mem_wmask
        if read and write:
            raise TraceFormatError("simultaneous read/write is unsupported for this non-atomic profile")
        memory = None
        if raw.rvfi_trap:
            pass  # No committed memory effect on the unexpected trap event.
        elif width and (read or write):
            immediate = raw.rvfi_insn >> 20 if opcode == 3 else ((raw.rvfi_insn >> 25) << 5 | (raw.rvfi_insn >> 7 & 31))
            effective = (raw.rvfi_rs1_rdata + signed(immediate, 12)) & 0xFFFFFFFF
            offset = effective & 3
            mask = ((1 << width)-1) << offset
            is_write = bool(write)
            memory = MemoryEffect(kind="write" if is_write else "read", address=(raw.rvfi_mem_addr+offset) & 0xFFFFFFFF,
                width=width, byte_mask=write if is_write else read & mask,
                value=((raw.rvfi_mem_wdata if is_write else raw.rvfi_mem_rdata) >> (8*offset)) & ((1 << (8*width))-1))
        elif read or write:
            # Preserve unexpected observed side effects rather than silently dropping them.
            memory = MemoryEffect(kind="write" if write else "read", address=raw.rvfi_mem_addr,
                width=4, byte_mask=write or read, value=raw.rvfi_mem_wdata if write else raw.rvfi_mem_rdata)
        event = Retirement(order=raw.rvfi_order, pc=raw.rvfi_pc_rdata, instruction=raw.rvfi_insn,
            rd=raw.rvfi_rd_addr, rd_value=raw.rvfi_rd_wdata if raw.rvfi_rd_addr else 0,
            trap=raw.rvfi_trap, interrupt=raw.rvfi_intr, memory=memory, source_cycle=raw.cycle)
        events.append(event)
        if _completion(event):
            break
    return tuple(events)


_COMMIT = re.compile(r"^core\s+(\d+):\s+([0-3])\s+(0x[0-9a-fA-F]+)\s+\((0x[0-9a-fA-F]+)\)(.*)$")


def parse_spike_commits(text: str, *, max_retired=10000):
    """Pinned Spike commit format; rejects non-integer/CSR/vector/ambiguous records."""
    events = []
    if len(text.encode()) > 16*1024*1024:
        raise TraceFormatError("reference log exceeds byte bound")
    for line_number, line in enumerate(text.splitlines(), 1):
        if not line.strip():
            continue
        if len(line) > 4096 or len(events) >= max_retired:
            raise TraceFormatError("reference trace exceeds bound")
        match = _COMMIT.fullmatch(line)
        if match is None:
            raise TraceFormatError("unrecognized reference commit record")
        hart, mode, pc, instruction = (int(value, 0) if value.startswith('0x') else int(value) for value in match.groups()[:4])
        if hart != 0 or mode != 3:
            raise UnsupportedTrace("only single-hart machine-mode execution is configured")
        if pc % 4:
            raise UnsupportedTrace("reference PC is not word-aligned")
        opcode, width = _shape(instruction)
        tokens, registers, accesses = match.group(5).split(), [], []
        while tokens:
            token = tokens.pop(0)
            if re.fullmatch(r"x\d{1,2}", token) and tokens:
                registers.append((int(token[1:]), int(tokens.pop(0), 16)))
            elif token == "mem" and tokens:
                address = int(tokens.pop(0), 16)
                value = int(tokens.pop(0), 16) if tokens and tokens[0].startswith("0x") else None
                accesses.append((address, value))
            else:
                raise UnsupportedTrace("unsupported reference side effect")
        registers = [(rd, value) for rd, value in registers if rd != 0]
        if len(registers) > 1 or len(accesses) > 1:
            raise TraceFormatError("unexpected multiple architectural effects")
        rd, value = registers[0] if registers else (0, 0)
        expected_rd = instruction >> 7 & 31 if opcode in (3, 0x13, 0x33, 0x37, 0x17, 0x6F, 0x67) else 0
        if rd != expected_rd:
            raise TraceFormatError("reference integer register write is absent or inconsistent")
        memory = None
        if width:
            if len(accesses) != 1:
                raise TraceFormatError("reference memory effect missing")
            address, store_value = accesses[0]
            if address % width:
                raise UnsupportedTrace("misaligned reference access outside profile")
            if not PLATFORM.ram_base <= address < address+width <= PLATFORM.ram_base+PLATFORM.ram_bytes:
                raise UnsupportedTrace("reference memory access outside the configured RAM; MMIO is not modeled")
            if opcode == 3 and not rd:
                raise UnsupportedTrace("load to x0 lacks reference read-data observation")
            if (opcode == 3) != (store_value is None):
                raise TraceFormatError("reference read/write effect disagrees with instruction")
            memory = MemoryEffect(kind="read" if opcode == 3 else "write", address=address,
                width=width, byte_mask=((1 << width)-1) << (address & 3),
                value=(value if opcode == 3 else store_value) & ((1 << (8*width))-1))
        elif accesses:
            raise TraceFormatError("unexpected reference memory effect")
        event = Retirement(order=len(events), pc=pc, instruction=instruction, rd=rd, rd_value=value,
                           memory=memory, source_line=line_number)
        events.append(event)
        if _completion(event):
            break
    return tuple(events)
