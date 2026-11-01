"""Trusted child entrypoint for curated arithmetic fixtures."""
import json
import sys
from pathlib import Path
from cocotb_tools.runner import get_runner


def main():
    job = json.loads(Path(sys.argv[1]).read_text())
    runner = get_runner("verilator")
    runner.build(sources=[job["source"]], hdl_toplevel="multiplier", build_dir=job["build"],
                 parameters=job["parameters"], build_args=["-Wno-fatal"], waves=True)
    (Path(job["output"]) / "built.marker").write_text("build completed; verdict pending")
    runner.test(hdl_toplevel="multiplier", test_module="veriforge.arithmetic.sim_test", build_dir=job["build"],
                test_dir=job["output"], waves=True,
                extra_env={"VF_STIMULUS": job["stimulus"], "VF_OBSERVED": job["observed"]})


if __name__ == "__main__":
    main()
