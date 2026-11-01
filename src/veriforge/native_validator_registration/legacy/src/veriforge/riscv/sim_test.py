"""Prepared observation-only RVFI driver; no expected architectural values here."""
import json
import os
from pathlib import Path
import cocotb
from cocotb.triggers import Timer
from .contracts import RVFIEvent
from .memory import Ram
from .profile import PLATFORM


@cocotb.test()
async def observe(dut):
    ram = Ram(Path(os.environ['VF_RISCV_BINARY']).read_bytes())
    events, reason = [], 'cycle_limit'
    try:
        for cycle in range(int(os.environ['VF_RISCV_MAX_CYCLES'])):
            dut.clk.value = 0
            dut.resetn.value = int(cycle >= 5)
            dut.mem_ready.value = 0
            dut.mem_rdata.value = 0
            await Timer(2, unit='ns')
            pending = None
            if cycle >= 5 and int(dut.mem_valid.value):
                address, strobes, data = int(dut.mem_addr.value), int(dut.mem_wstrb.value), int(dut.mem_wdata.value)
                dut.mem_rdata.value = ram.read_word(address)
                dut.mem_ready.value = 1
                pending = (address, data, strobes)
            await Timer(3, unit='ns')
            dut.clk.value = 1
            await Timer(5, unit='ns')
            if pending is not None:
                ram.write_word(*pending)
            if cycle >= 5 and int(dut.rvfi_valid.value):
                row = {name: int(getattr(dut, name).value) for name in RVFIEvent.model_fields if name.startswith('rvfi_')}
                row['cycle'] = cycle
                row['rvfi_trap'] = bool(row['rvfi_trap'])
                row['rvfi_intr'] = bool(row['rvfi_intr'])
                events.append(row)
                if row['rvfi_trap']:
                    reason = 'trap'
                    break
                if row['rvfi_mem_addr'] == PLATFORM.completion and row['rvfi_mem_wmask']:
                    reason = 'completion_store_retired'
                    break
                if len(events) >= int(os.environ['VF_RISCV_MAX_RETIRED']):
                    reason = 'retirement_limit'
                    break
    finally:
        Path(os.environ['VF_RISCV_TRACE']).write_text(json.dumps({'termination': reason, 'cycles': cycle+1, 'events': events}))
