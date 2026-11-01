"""Read-only inventory and fixed command preparation. This module never executes."""

from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path
import shutil


@dataclass(frozen=True)
class Capability:
    executable_candidates: tuple[str, ...]
    support_level: str = "import-only"
    live_execution: str = "not_implemented"
    electrical_acceptance: str = "not_established"


def inventory() -> Capability:
    paths = set()
    for name in ("kicad-cli", "kicad-cli.exe"):
        found = shutil.which(name)
        if found:
            paths.add(str(Path(found).resolve()))
    for path in Path("C:/Program Files/KiCad").glob("*/bin/kicad-cli.exe"):
        if path.is_file():
            paths.add(str(path.resolve()))
    return Capability(tuple(sorted(paths)))


class Check(StrEnum):
    ERC = "erc"
    DRC = "drc"
    NETLIST = "netlist"


def command_argv(check: Check, executable: Path, source: Path, output: Path) -> tuple[str, ...]:
    """For a future trusted runner: absolute host-selected paths, no user flags.

    Execution authorization, fresh attempt directories, source snapshots, process
    environment isolation and resource budgets remain runner responsibilities.
    """
    if not isinstance(check, Check):
        raise ValueError("check must be a Check enum")
    executable, source, output = map(lambda p: Path(p).resolve(), (executable, source, output))
    if not executable.is_file() or not source.is_file():
        raise ValueError("executable and source must exist")
    expected = ".kicad_pcb" if check == Check.DRC else ".kicad_sch"
    if source.suffix != expected or output == source or output.exists():
        raise ValueError("wrong source type or output would overwrite a file")
    if check == Check.NETLIST:
        args = ("sch", "export", "netlist", "--format", "kicadxml")
    else:
        args = ("pcb" if check == Check.DRC else "sch", check.value,
                "--format", "json", "--units", "mm", "--severity-all",
                "--exit-code-violations")
        if check == Check.DRC:
            args += ("--schematic-parity", "--refill-zones")
    return (str(executable), *args, "--output", str(output), str(source))
