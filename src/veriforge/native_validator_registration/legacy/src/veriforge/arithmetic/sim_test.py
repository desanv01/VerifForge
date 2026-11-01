"""Observation only. No oracle, expected products, or verdict in simulator."""
import json
import os
from pathlib import Path
import cocotb
from cocotb.triggers import Timer, ReadOnly


@cocotb.test()
async def observe(dut):
    rows = []
    for cycle, item in enumerate(json.loads(Path(os.environ["VF_STIMULUS"]).read_text())):
        dut.clk.value = 0
        dut.reset.value = int(item["reset"])
        dut.in_valid.value = int(item["valid"])
        dut.a.value, dut.b.value = item["a"], item["b"]
        await Timer(5, unit="ns")
        dut.clk.value = 1
        await Timer(5, unit="ns")
        await ReadOnly()
        rows.append({"cycle": cycle, "valid": bool(dut.out_valid.value), "product": int(dut.product.value)})
        await Timer(1, unit="ns")
    Path(os.environ["VF_OBSERVED"]).write_text(json.dumps(rows))
