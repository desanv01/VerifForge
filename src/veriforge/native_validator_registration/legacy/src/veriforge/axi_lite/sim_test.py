"""Exact input playback and settled pre-edge observations; no checking here."""
import json
import os
from pathlib import Path

import cocotb
from cocotb.triggers import ReadOnly, Timer

INPUTS = ("reset_n", "aw_valid", "aw_addr", "aw_prot", "w_valid", "w_data", "w_strb",
          "b_ready", "ar_valid", "ar_addr", "ar_prot", "r_ready")
BOOL_OUTPUTS = ("aw_ready", "w_ready", "b_valid", "ar_ready", "r_valid")
INT_OUTPUTS = ("b_resp", "r_data", "r_resp")


@cocotb.test()
async def observe(dut):
    rows = []
    for cycle, item in enumerate(json.loads(Path(os.environ["VF_STIMULUS"]).read_text())):
        dut.clk.value = 0
        for name in INPUTS:
            getattr(dut, name).value = int(item[name])
        await Timer(5, unit="ns")
        await ReadOnly()
        rows.append({"cycle": cycle,
                     **{name: bool(int(getattr(dut, name).value)) for name in BOOL_OUTPUTS},
                     **{name: int(getattr(dut, name).value) for name in INT_OUTPUTS}})
        # Leave ReadOnly before writing the edge. Never adapt submitted cycles.
        await Timer(1, unit="ns")
        dut.clk.value = 1
        await Timer(5, unit="ns")
    Path(os.environ["VF_OBSERVED"]).write_text(json.dumps({
        "schema_version": "axi_lite.trace.v1", "observations": rows}))
