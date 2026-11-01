"""Read-only curated-project admission and fixed batch plans; never starts Vivado.

Hash-bound reviewed HDL/XDC only. Existing XPR/Tcl project scripts and model text
are never sourced. Execution/repair capability remains unvalidated.
"""
from dataclasses import dataclass
from enum import StrEnum
from hashlib import sha256
from pathlib import Path
from types import MappingProxyType

PART = "xc7a200tsbg484-1"
TOP = "exercise3_nexys_video_top"
CLOCK_PERIOD_NS = 10.0
MAX_SOURCE_BYTES = 1_048_576
# Reviewed local inputs, 2026-10-29. No private source is bundled here.
EXERCISE3_HASHES = MappingProxyType({
    "exercise3_core.v": "050a19942ce909f9b32a4e899e39f30408f9f7ac9df9cb0fe3c1f7748a98a76a",
    "exercise3_nexys_video_top.v": "b67dbc9ee84693b09e375c70dd014b50eacbf68a42a59717e1eb8d583730d81f",
    "nexys_video_exercise3.xdc": "f446fcd9f2056f2c47872ba74f94d79f02e30d3c1abdf27599f1c0e125a4763e",
})


class BatchRecipe(StrEnum):
    DEVICE_QUERY = "device_query_v1"
    EXERCISE3_POST_SYNTH = "exercise3_post_synth_v1"


@dataclass(frozen=True)
class SourceIdentity:
    name: str
    sha256: str
    size_bytes: int


@dataclass(frozen=True)
class ProjectReadiness:
    profile: str
    part: str
    top: str
    clock_period_ns: float
    sources: tuple[SourceIdentity, ...]
    execute_status: str = "not_validated"
    repair_status: str = "not_validated"
    timing_scope: str = "post_synthesis_estimate_no_io_delay_requirements"
    cdc_scope: str = "single_clock_external_async_inputs_not_proven_safe"


def inspect_exercise3(directory: Path) -> ProjectReadiness:
    """Read only three fixed reviewed files; reject containment/size/hash drift."""
    root = Path(directory).resolve(strict=True)
    identities = []
    for name, approved_hash in EXERCISE3_HASHES.items():
        path = (root / name).resolve(strict=True)
        if not path.is_relative_to(root) or not path.is_file():
            raise ValueError("project input escapes snapshot or is not a file")
        with path.open("rb") as stream:
            data = stream.read(MAX_SOURCE_BYTES + 1)
        if len(data) > MAX_SOURCE_BYTES:
            raise ValueError("project input exceeds byte limit")
        actual = sha256(data).hexdigest()
        if actual != approved_hash:
            raise ValueError("reviewed project identity changed")
        identities.append(SourceIdentity(name, actual, len(data)))
    return ProjectReadiness("exercise3_nexys_video_v1", PART, TOP, CLOCK_PERIOD_NS, tuple(identities))


def render_batch_recipe(recipe: BatchRecipe) -> str:
    """Only fixed implementation strings enter Tcl. Returns source, executes none."""
    if not isinstance(recipe, BatchRecipe):
        raise ValueError("select a fixed BatchRecipe enum")
    header = 'puts "VF_VERSION [version -short]"\n'
    if recipe == BatchRecipe.DEVICE_QUERY:
        return header + '''foreach part {xc7a200tsbg484-1 xc7a100tcsg324-1 xc7a35tcpg236-1} {
    set available [get_parts $part]
    puts "VF_PART $part [llength $available]"
}
exit 0
'''
    return header + '''# Run only in an admitted copied snapshot, never the user's project.
set_param general.maxThreads 2
if {[catch {
    puts "VF_STAGE read_sources started"
    read_verilog {exercise3_core.v exercise3_nexys_video_top.v}
    read_xdc nexys_video_exercise3.xdc
    puts "VF_STAGE read_sources passed"
    puts "VF_STAGE synthesis started"
    synth_design -top exercise3_nexys_video_top -part xc7a200tsbg484-1
    puts "VF_STAGE synthesis passed"
} detail]} {
    puts "VF_PREPARATION_FAILED $detail"
    exit 1
}
# Preserve separate report availability; a report command succeeding is not a verdict.
foreach {stage command} {
    check_timing {check_timing -verbose -file check_timing.rpt}
    timing_summary {report_timing_summary -report_unconstrained -file timing_summary.rpt}
    clocks {report_clocks -file clocks.rpt}
    cdc {report_cdc -details -file cdc.rpt}
    methodology {report_methodology -file methodology.rpt}
    utilization {report_utilization -file utilization.rpt}
} {
    puts "VF_REPORT $stage started"
    if {[catch {eval $command} detail]} {
        puts "VF_REPORT $stage unavailable"
        puts $detail
    } else {
        puts "VF_REPORT $stage captured"
    }
}
puts "VF_BUILD passed"
puts "VF_CHECKS functional_not_run timing_not_validated cdc_not_validated"
exit 0
'''


@dataclass(frozen=True)
class BatchPlan:
    argv: tuple[str, ...]
    cwd: str
    recipe: str
    script_sha256: str
    sources: tuple[SourceIdentity, ...]
    proposed_wall_seconds: int
    status: str = "source_only_not_executed"


def plan_batch(launcher: Path, snapshot: Path, recipe: BatchRecipe) -> BatchPlan:
    """Construct reviewable argv; does not create/copy files or execute commands.

    A future slot owner must write this exact recipe as recipe.tcl in the fresh
    attempt, rehash all admitted bytes immediately before dispatch, isolate the
    vendor environment/init hooks, and enforce the proposed runtime/output caps.
    """
    source = render_batch_recipe(recipe)
    launcher, snapshot = Path(launcher).resolve(strict=True), Path(snapshot).resolve(strict=True)
    if not launcher.is_file() or launcher.name.lower() not in {"vivado.bat", "vivado.exe", "vivado"}:
        raise ValueError("trusted Vivado launcher path required")
    if not snapshot.is_dir():
        raise ValueError("attempt snapshot must be a directory")
    expected_names = set(EXERCISE3_HASHES) if recipe == BatchRecipe.EXERCISE3_POST_SYNTH else set()
    if {path.name for path in snapshot.iterdir()} != expected_names:
        raise ValueError("plan requires a fresh isolated snapshot, not an existing project/work directory")
    inputs = inspect_exercise3(snapshot).sources if recipe == BatchRecipe.EXERCISE3_POST_SYNTH else ()
    return BatchPlan((str(launcher), "-mode", "batch", "-source", "recipe.tcl",
                      "-log", "vivado.log", "-journal", "vivado.jou"), str(snapshot), recipe.value,
                     sha256(source.encode()).hexdigest(), inputs,
                     60 if recipe == BatchRecipe.DEVICE_QUERY else 300)
