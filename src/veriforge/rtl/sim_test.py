"""Trusted observation-only driver. Verdict is computed outside the simulator."""
import json
import os
from pathlib import Path
import cocotb
from cocotb.triggers import Timer


@cocotb.test()
async def observe(dut):
    stimulus = json.loads(Path(os.environ["VF_STIMULUS"]).read_text())
    observed = []
    for cycle, inputs in enumerate(stimulus):
        dut.clk.value = 0
        dut.reset.value = int(inputs["reset"])
        dut.wr_en.value = int(inputs["write"])
        dut.rd_en.value = int(inputs["read"])
        dut.wr_data.value = inputs["value"]
        await Timer(5, unit="ns")
        dut.clk.value = 1
        await Timer(5, unit="ns")
        observed.append({"cycle": cycle, "data": int(dut.rd_data.value),
                         "empty": bool(dut.empty.value), "full": bool(dut.full.value)})
    Path(os.environ["VF_OBSERVED"]).write_text(json.dumps(observed, indent=2))
