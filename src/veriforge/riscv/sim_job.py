"""Internal fixed runner job; no command/build flags are accepted from experiments."""
import json
from pathlib import Path
import sys
from cocotb_tools.runner import get_runner
from .profile import ROOT


def main():
    job = json.loads(Path(sys.argv[1]).read_text())
    defines = {'RISCV_FORMAL': 1}
    if job['fixture_id'] == 'picorv32_reg_write_bug':
        defines['PICORV32_TESTBUG_002'] = 1
    elif job['fixture_id'] != 'picorv32_rv32i':
        raise ValueError('curated fixture required')
    runner = get_runner('verilator')
    runner.build(sources=[ROOT / 'targets/riscv/upstream/picorv32.v', ROOT / 'targets/riscv/picorv32_top.sv'],
        defines=defines, hdl_toplevel='picorv32_top', build_dir=job['build'], build_args=['-Wno-fatal'])
    (Path(job['output']) / 'built.marker').write_text('RTL build settled; no functional verdict yet')
    runner.test(hdl_toplevel='picorv32_top', test_module='veriforge.riscv.sim_test',
        build_dir=job['build'], test_dir=job['output'], extra_env={
            'VF_RISCV_BINARY': job['binary'], 'VF_RISCV_TRACE': job['trace'],
            'VF_RISCV_MAX_CYCLES': str(job['max_cycles']), 'VF_RISCV_MAX_RETIRED': str(job['max_retired'])})


if __name__ == '__main__':
    main()
