"""Reviewed fixed bare-metal profile; no commands, paths or options come from plans."""
from dataclasses import dataclass
from pathlib import Path

CORE_REVISION = "ef203c2b0a3fb793280f5114941416c425c5b461"
REFERENCE_REVISION = "4c6a9bc79dab80616932949707e7c0471d8e9e54"
CORE_BLOB = "cc45fa998f1857b06a856ea59b931f6c37e06b34"
ROOT = Path(__file__).resolve().parents[3]


@dataclass(frozen=True)
class Platform:
    profile_id: str = "picorv32-rv32i-v1"
    isa: str = "RV32I"
    ram_base: int = 0x80000000
    ram_bytes: int = 0x10000
    entry: int = 0x80000000
    completion: int = 0x8000FF00
    completion_value: int = 1
    stack_top: int = 0x8000FEF0
    endian: str = "little"


PLATFORM = Platform()
COMPILER_FLAGS = ("-march=rv32i", "-mabi=ilp32", "-mno-relax", "-nostdlib", "-nostartfiles",
                  "-ffreestanding", "-Wl,--no-relax", "-Wl,--build-id=none")


def compile_command(output_dir: Path) -> tuple[str, ...]:
    """Recipe only. output_dir is controller-selected, never a planner source path."""
    return ("riscv64-unknown-elf-gcc", *COMPILER_FLAGS,
            "-T", str(ROOT / "targets/riscv/link.ld"), str(ROOT / "targets/riscv/smoke.S"),
            "-o", str(Path(output_dir) / "smoke.elf"))


def reference_command(elf: Path, log: Path, max_retired: int = 256) -> tuple[str, ...]:
    if type(max_retired) is not int or not 32 <= max_retired <= 10000:
        raise ValueError("reference instruction bound outside supported budget")
    return ("spike", "--isa=RV32I", "--priv=m", "--pcs=0:0x80000000", "-m0x80000000:0x10000",
            "--log-commits", "--log="+str(log), "--instructions="+str(max_retired), str(elf))
