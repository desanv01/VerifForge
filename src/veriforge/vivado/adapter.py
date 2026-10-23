"""Trusted, finite recipes. Imported evidence never establishes functional correctness.

No command execution, caller-provided Tcl, project paths, or model-provided source
is accepted. A future authenticated runner must add bounded live execution and
independent checks before advertising more than import-only support.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from enum import StrEnum
from hashlib import sha256
import json
from pathlib import Path
import re
import shutil
from uuid import uuid4

ADAPTER_VERSION = "vivado.fixture.v1"
PARSER_VERSION = "vivado.log.v1"
MAX_LOG_BYTES = 1_048_576
MAX_DIAGNOSTICS = 200
MAX_LINE_CHARS = 2000
PART = "xc7a35tcpg236-1"  # Availability and licensing remain unverified.
SOURCE = """// Curated deliberately misconfigured-project fixture, not a vendor defect.
module vf_counter(input wire clk, input wire reset, output reg [7:0] count);
  always @(posedge clk)
    if (reset) count <= 8'd0;
    else count <= count + 8'd1;
endmodule
"""


class Recipe(StrEnum):
    WRONG_TOP = "wrong_top"
    MISSING_SOURCE = "missing_source"
    REPAIRED = "repaired"


@dataclass(frozen=True)
class Capability:
    support_level: str
    executable_candidates: tuple[str, ...]
    searched_locations: tuple[str, ...]
    tool_version: str | None = None
    device_support: str = "unverified"
    live_validation: str = "blocked_no_live_executor"
    max_concurrency: int = 1


def inventory() -> Capability:
    """Read PATH and fixed install layouts only; never starts vendor tools."""
    roots = (
        "C:/AMD", "C:/Xilinx", "C:/Program Files/AMD", "C:/Program Files/Xilinx",
        "D:/AMD", "D:/Xilinx", "C:/tools/Xilinx",
    )
    found = set()
    for name in ("vivado", "vivado.bat", "vivado.exe"):
        located = shutil.which(name)
        if located:
            found.add(str(Path(located).resolve()))
    # Fixed depths, no recursion through drives or vendor trees.
    for root in roots:
        for pattern in ("Vivado/*/bin/vivado.*", "*/Vivado/bin/vivado.*"):
            for path in Path(root).glob(pattern):
                if path.is_file() and path.suffix.lower() in (".bat", ".exe"):
                    found.add(str(path.resolve()))
    return Capability("import-only", tuple(sorted(found)), ("PATH", *roots))


@dataclass(frozen=True)
class PreparedAttempt:
    attempt_id: str
    directory: str
    recipe: str
    source_sha256: str
    recipe_sha256: str
    manifest_sha256: str
    parent_attempt_id: str | None
    execution_status: str = "prepared"
    build_status: str = "not_run"
    functional_status: str = "not_run"


def _script(recipe: Recipe) -> str:
    source = "missing.v" if recipe == Recipe.MISSING_SOURCE else "counter.v"
    top = "deliberately_wrong_top" if recipe == Recipe.WRONG_TOP else "vf_counter"
    # All interpolated tokens above are implementation constants.
    return f"""# {ADAPTER_VERSION}; curated recipe, no user Tcl.
puts "VF_VERSION [version -short]"
puts "VF_STAGE read_sources started"
if {{[catch {{read_verilog {source}}} detail]}} {{
    puts "VF_STAGE read_sources failed"
    puts $detail
    exit 1
}}
puts "VF_STAGE read_sources passed"
puts "VF_STAGE synthesis started"
if {{[catch {{synth_design -top {top} -part {PART}}} detail]}} {{
    puts "VF_STAGE synthesis failed"
    puts $detail
    exit 1
}}
puts "VF_STAGE synthesis passed"
puts "VF_STAGE reports started"
if {{[catch {{report_utilization -file utilization.rpt}} detail]}} {{
    puts "VF_STAGE reports failed"
    puts $detail
    exit 1
}}
puts "VF_STAGE reports passed"
puts "VF_BUILD passed"
exit 0
"""


def _digest(data: bytes) -> str:
    return sha256(data).hexdigest()


def _json_bytes(value: object) -> bytes:
    return (json.dumps(value, sort_keys=True, indent=2) + "\n").encode()


def prepare_attempt(output_root: Path, recipe: Recipe) -> PreparedAttempt:
    """Create a unique self-contained fixture snapshot, without executing it."""
    if not isinstance(recipe, Recipe):
        raise ValueError("recipe must be a trusted Recipe enum")
    return _prepare(output_root, recipe, None)


def _prepare(output_root: Path, recipe: Recipe, parent: str | None) -> PreparedAttempt:
    root = Path(output_root).resolve()
    root.mkdir(parents=True, exist_ok=True)
    attempt_id = "vivado-" + uuid4().hex
    directory = root / attempt_id
    directory.mkdir(exist_ok=False)
    source = SOURCE.encode()
    script = _script(recipe).encode()
    manifest = _json_bytes({
        "adapter": ADAPTER_VERSION, "attempt_id": attempt_id, "recipe": recipe.value,
        "parent_attempt_id": parent, "fixture": "curated_counter_v1",
        "deliberately_broken": recipe != Recipe.REPAIRED,
        "sources": {"counter.v": _digest(source)}, "recipe_sha256": _digest(script),
        "part": PART, "functional_contract": "reset clears count; otherwise increment modulo 256",
        "functional_check_status": "not_run", "tool_version": None,
        "environment_identity": "unverified_no_execution",
        "required_checks": ["reproduce_selected_build_failure", "repaired_synthesis", "independent_counter_reset_wrap_check"],
        "reviewed_command_argv": ["vivado", "-mode", "batch", "-source", "recipe.tcl", "-log", "vivado.log", "-journal", "vivado.jou"],
        "execution": "not_run", "stage_timeout_seconds": 120,
        "note": "Timeout is a runner limit, not a design defect. No live executor supplied.",
    })
    (directory / "counter.v").write_bytes(source)
    (directory / "recipe.tcl").write_bytes(script)
    (directory / "manifest.json").write_bytes(manifest)
    result = PreparedAttempt(attempt_id, str(directory), recipe.value, _digest(source),
                             _digest(script), _digest(manifest), parent)
    (directory / "prepared.json").write_bytes(_json_bytes(asdict(result)))
    return result


def prepare_repair_pair(output_root: Path, broken_recipe: Recipe) -> tuple[PreparedAttempt, PreparedAttempt]:
    """Recreate identical trusted sources in separate baseline/candidate directories."""
    if not isinstance(broken_recipe, Recipe) or broken_recipe == Recipe.REPAIRED:
        raise ValueError("choose wrong_top or missing_source")
    original = _prepare(output_root, broken_recipe, None)
    candidate = _prepare(output_root, Recipe.REPAIRED, original.attempt_id)
    return original, candidate


@dataclass(frozen=True)
class Diagnostic:
    severity: str
    message_id: str | None
    stage: str
    line: int
    text: str


@dataclass(frozen=True)
class StageObservation:
    stage: str
    status: str
    line: int


@dataclass(frozen=True)
class ImportedReport:
    directory: str
    parser_version: str
    provenance: str
    original_sha256: str
    sanitized_sha256: str
    original_bytes: int
    tool_version: str | None
    build_status: str
    functional_status: str
    diagnostics: tuple[Diagnostic, ...]
    stages: tuple[StageObservation, ...]
    failure_signature: str | None
    diagnostics_truncated: bool
    uncertainties: tuple[str, ...]
    execution_status: str = "completed"  # The import completed, not a vendor process.
    tool_execution_status: str = "unverified_import"


_SECRET = re.compile(
    r"(?i)\b(?:[a-z0-9_]*(?:api[_-]?key|token|password|secret)|(?:xilinxd?|lm)_license_file)"
    # Accept the closing quote of JSON/quoted keys and escaped quoted values.
    r'''["']?[ \t]*[:=][ \t]*(?:"(?:\\.|[^"\\\r\n])*"|'(?:\\.|[^'\\\r\n])*'|[^\s,;}]+)'''
)
_MESSAGE = re.compile(r"^(INFO|WARNING|CRITICAL WARNING|ERROR|FATAL):\s*(?:\[([^\]\r\n]{1,100})\])?\s*(.*)")
_STAGE = re.compile(r"^VF_STAGE (read_sources|synthesis|reports) (started|passed|failed)$")


def _sanitize(text: str) -> str:
    text = re.sub(r"(?i)\bBearer\s+[^\s]+", "Bearer [REDACTED]", text)
    text = re.sub(r"(?i)(https?://)[^\s/@]+:[^\s/@]+@", r"\1[REDACTED]@", text)
    text = _SECRET.sub("[REDACTED]", text)
    # A Windows user-directory segment can contain spaces. Stop at the next
    # separator, quote or line boundary while retaining project-relative clues.
    text = re.sub(r'''(?i)([A-Z]:[/\\]Users[/\\])[^/\\\r\n"']+''', r"\1[USER]", text)
    return text


def import_log(log_path: Path, output_root: Path) -> ImportedReport:
    """Import at most 1 MiB. Store only sanitized logs; never execute log text.

    An original-content hash preserves identity without copying potential secrets.
    Keep the original locally if byte-for-byte vendor evidence is required.
    """
    with Path(log_path).open("rb") as stream:
        raw = stream.read(MAX_LOG_BYTES + 1)
    if len(raw) > MAX_LOG_BYTES:
        raise ValueError("Vivado import exceeds 1 MiB limit")
    sanitized = _sanitize(raw.decode("utf-8", errors="replace"))
    diagnostics: list[Diagnostic] = []
    stages: list[StageObservation] = []
    active = "unknown"
    version = None
    passed = False
    error_count = 0
    truncated = False
    failed_stage_names: set[str] = set()
    for number, line in enumerate(sanitized.splitlines(), 1):
        if line.startswith("VF_VERSION ") or line.startswith("Vivado v"):
            match = re.search(r"(?<!\d)(20\d{2}\.\d+(?:\.\d+)?)\b", line)
            if match:
                version = match.group(1)
        stage = _STAGE.fullmatch(line)
        if stage:
            active = stage[1]
            if stage[2] == "failed":
                failed_stage_names.add(active)
            if len(stages) < MAX_DIAGNOSTICS:
                stages.append(StageObservation(active, stage[2], number))
            else:
                truncated = True
        passed |= line == "VF_BUILD passed"
        message = _MESSAGE.match(line)
        if message:
            error_count += message[1] in ("ERROR", "FATAL")
            if len(diagnostics) < MAX_DIAGNOSTICS:
                diagnostics.append(Diagnostic(message[1], message[2], active, number, message[3][:MAX_LINE_CHARS]))
            else:
                truncated = True
    failed = error_count > 0 or bool(failed_stage_names)
    build = "reported_failed" if failed else "reported_passed" if passed else "unknown"
    # Include all error IDs, even beyond the display cap; independent of timestamps/paths.
    ids = sorted({(m[1], m[2] or "unidentified") for line in sanitized.splitlines()
                  if (m := _MESSAGE.match(line)) and m[1] in ("ERROR", "FATAL")})
    failed_stages = sorted(failed_stage_names)
    signature = _digest(_json_bytes([PARSER_VERSION, version, failed_stages, ids])) if failed else None
    root = Path(output_root).resolve()
    root.mkdir(parents=True, exist_ok=True)
    destination = root / ("import-" + uuid4().hex)
    destination.mkdir(exist_ok=False)
    result = ImportedReport(
        str(destination), PARSER_VERSION, "imported_unverified", _digest(raw), _digest(sanitized.encode()), len(raw),
        version, build, "not_run", tuple(diagnostics), tuple(stages), signature, truncated,
        ("Imported log claims are unverified; no Vivado process was executed by this adapter.",
         "Build recovery does not establish functional correctness, equivalence, timing, or device support.",
         "Sanitization is best-effort; review artifacts before external disclosure."),
    )
    (destination / "vivado.sanitized.log").write_bytes(sanitized.encode())
    (destination / "observations.json").write_bytes(_json_bytes(asdict(result)))
    return result
