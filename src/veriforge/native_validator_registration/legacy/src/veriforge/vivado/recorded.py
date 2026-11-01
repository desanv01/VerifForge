"""Bounded, read-only import of recorded native receipts; never launches a tool.

Records contain no caller paths, raw diagnostics, argv or environment. Matching
hashes establish internal consistency, not authentication of an imported receipt.
"""
from dataclasses import dataclass
from hashlib import sha256
import json
import math
from pathlib import Path
import re

from .readiness import (
    BatchRecipe, CLOCK_PERIOD_NS, EXERCISE3_HASHES, PART, ProjectReadiness,
    SourceIdentity, TOP, render_batch_recipe,
)

MAX_RECORD_BYTES = 1_048_576
REPORTS = ("check_timing", "timing_summary", "clocks", "cdc", "methodology", "utilization")
PARTS = (PART, "xc7a100tcsg324-1", "xc7a35tcpg236-1")
LIMITS = {"file_count_limit", "initialization_cache_limit", "result_directory_limit",
          "total_directory_limit", "timeout", "stdout_limit_or_capture_error",
          "linked_output_rejected", "control_or_reservation_changed"}


@dataclass(frozen=True)
class PartObservation:
    part: str
    count: int


@dataclass(frozen=True)
class ReportAvailability:
    report: str
    availability: str
    sha256: str | None = None
    size_bytes: int | None = None
    verdict: str = "not_evaluated"


@dataclass(frozen=True)
class RecordedStorage:
    cache_bytes: int
    result_bytes: int
    files: int


@dataclass(frozen=True)
class RecordedBudget:
    cache_bytes: int
    result_bytes: int
    total_bytes: int
    files: int


@dataclass(frozen=True)
class RecordedAttempt:
    recipe: str
    receipt_sha256: str
    plan_sha256: str
    stdout_sha256: str
    script_sha256: str
    tool_version: str
    selected_part: str
    selected_part_readiness: str
    parts: tuple[PartObservation, ...]
    project: ProjectReadiness | None
    native_execution_status: str
    native_exit_code: int
    wall_seconds: float
    resource_limit: str | None
    storage_before_receipt: RecordedStorage
    storage_budget: RecordedBudget
    owned_processes_after_cleanup: int
    build_status: str
    reports: tuple[ReportAvailability, ...]
    provenance: str = "recorded_import_not_new_execution"
    import_status: str = "completed"
    new_execution: bool = False
    design_verdict: str = "not_evaluated"
    functional_status: str = "not_run"
    repair_status: str = "not_validated"
    identity_scope: str = "recorded_plan_receipt_agreement_not_current_source_bytes"


def _read(root: Path, name: str) -> bytes:
    path = root / name  # All names are implementation constants, never receipt paths.
    if path.is_symlink() or path.is_junction() or not path.resolve().is_relative_to(root):
        raise ValueError("linked recorded artifact")
    with path.open("rb") as stream:
        raw = stream.read(MAX_RECORD_BYTES + 1)
    if len(raw) > MAX_RECORD_BYTES:
        raise ValueError("recorded artifact exceeds byte limit")
    return raw


def _integer(value: object, minimum: int = 0) -> int:
    if type(value) is not int or value < minimum:
        raise ValueError("invalid recorded integer")
    return value


def _digest(raw: bytes) -> str:
    return sha256(raw).hexdigest()


def _artifact(entry: object, raw: bytes) -> bool:
    return isinstance(entry, dict) and type(entry.get("bytes")) is int and entry["bytes"] == len(raw) and entry.get("sha256") == _digest(raw)


def _markers(text: str, kind: str, names: tuple[str, ...], terminals: tuple[str, ...]) -> dict[str, str]:
    result: dict[str, str] = {}
    for line in text.splitlines():
        if not line.startswith(f"VF_{kind} "):
            continue
        tokens = line.split()
        if len(tokens) != 3 or tokens[1] not in names or tokens[2] not in ("started", *terminals):
            raise ValueError("invalid recorded marker")
        name, status = tokens[1:]
        if (status == "started" and name in result) or (status != "started" and result.get(name) != "started"):
            raise ValueError("conflicting recorded markers")
        result[name] = status
    return result


def _build_lifecycle(text: str) -> bool:
    """Reject impossible stage order; accept build success only for the full recipe.

    Partial report observations remain inspectable without upgrading build status.
    """
    lines = text.splitlines()
    stages = [line for line in lines if line.startswith("VF_STAGE ")]
    expected = ["VF_STAGE read_sources started", "VF_STAGE read_sources passed",
                "VF_STAGE synthesis started", "VF_STAGE synthesis passed"]
    for index, line in enumerate(stages):
        if index >= len(expected):
            raise ValueError("invalid recorded lifecycle")
        if line == expected[index]:
            continue
        if index in (1, 3) and line == expected[index].replace("passed", "failed") and index == len(stages) - 1:
            continue
        raise ValueError("invalid recorded lifecycle")
    builds = [line for line in lines if line.startswith("VF_BUILD ")]
    if not builds:
        return False
    if builds != ["VF_BUILD passed"]:
        raise ValueError("invalid recorded build marker")
    events = [line for line in lines if line.startswith(("VF_STAGE ", "VF_REPORT ", "VF_BUILD "))]
    if events[:4] != expected:
        raise ValueError("invalid recorded build lifecycle")
    cursor = 4
    for name in REPORTS:
        if events[cursor:cursor + 1] != [f"VF_REPORT {name} started"] or events[cursor + 1:cursor + 2] not in (
            [f"VF_REPORT {name} captured"], [f"VF_REPORT {name} unavailable"],
        ):
            raise ValueError("incomplete recorded build lifecycle")
        cursor += 2
    if events[cursor:] != ["VF_BUILD passed"]:
        raise ValueError("invalid recorded build lifecycle")
    return True


def inspect_recorded_attempt(directory: Path, recipe: BatchRecipe) -> RecordedAttempt:
    """Inspect fixed receipt/plan/stdout names and six bounded report files only.

    Input claims remain recorded imports, even when internally consistent. The
    caller chooses the local evidence directory; the returned record is safe to
    serialize without exposing it. No report contents become correctness verdicts.
    """
    if not isinstance(recipe, BatchRecipe):
        raise ValueError("fixed BatchRecipe required")
    root = Path(directory).resolve(strict=True)
    receipt_raw = _read(root, "query-receipt.json" if recipe == BatchRecipe.DEVICE_QUERY else "synthesis-receipt.json")
    plan_raw, stdout = _read(root, "plan.json"), _read(root, "stdout.log")
    receipt, plan = json.loads(receipt_raw), json.loads(plan_raw)
    if not isinstance(receipt, dict) or not isinstance(plan, dict):
        raise ValueError("recorded objects required")
    expected_script = _digest(render_batch_recipe(recipe).encode())
    if plan.get("recipe") != recipe.value or plan.get("script_sha256") != expected_script or receipt.get("script_sha256") != expected_script:
        raise ValueError("recorded recipe identity mismatch")
    artifacts = receipt.get("artifacts")
    if not isinstance(artifacts, dict) or len(artifacts) > 4096 or not _artifact(artifacts.get("stdout.log"), stdout):
        raise ValueError("recorded stdout identity mismatch")
    # Match vendor CRLF and imported LF equally; identity remains over raw bytes.
    text = "\n".join(stdout.decode("utf-8", errors="strict").splitlines())
    versions = re.findall(r"^VF_VERSION (20\d{2}\.\d+(?:\.\d+)?)$", text, re.M)
    if len(versions) != 1 or receipt.get("version") != versions:
        raise ValueError("recorded version mismatch")
    exit_code = _integer(receipt.get("exit_code"), -2147483648)
    active = _integer(receipt.get("active_owned_processes_after_cleanup"))
    wall = receipt.get("wall_seconds")
    if type(wall) not in (int, float) or not math.isfinite(wall) or wall < 0:
        raise ValueError("invalid recorded wall time")
    limit = receipt.get("limit_reason")
    if limit is not None and (not isinstance(limit, str) or limit not in LIMITS):
        raise ValueError("unknown recorded limit")
    storage, budget = receipt.get("storage_before_receipt"), receipt.get("caps")
    if not isinstance(storage, dict) or not isinstance(budget, dict):
        raise ValueError("recorded storage counters required")
    observed_storage = RecordedStorage(*(_integer(storage.get(k)) for k in ("cache_bytes", "result_bytes", "files")))
    storage_budget = RecordedBudget(*(_integer(budget.get(k), 1) for k in ("cache_bytes", "result_bytes", "total_bytes", "files")))
    exceeded = (observed_storage.cache_bytes > storage_budget.cache_bytes or
                observed_storage.result_bytes > storage_budget.result_bytes or
                observed_storage.files > storage_budget.files or
                observed_storage.cache_bytes + observed_storage.result_bytes > storage_budget.total_bytes)
    if exceeded and limit is None:
        raise ValueError("recorded storage excess lacks limit status")
    execution = ("cleanup_incomplete" if active else "timed_out" if limit == "timeout" else
                 "resource_limited" if limit in LIMITS - {"timeout", "control_or_reservation_changed", "linked_output_rejected"} else
                 "interrupted" if limit else "completed" if exit_code == 0 else "failed")
    stages = _markers(text, "STAGE", ("read_sources", "synthesis", "reports"), ("passed", "failed"))
    report_markers = _markers(text, "REPORT", REPORTS, ("captured", "unavailable"))
    parts = tuple(PartObservation(p, int(c)) for p, c in re.findall(r"^VF_PART (\S+) (\d+)$", text, re.M))
    project = None
    if recipe == BatchRecipe.DEVICE_QUERY:
        if tuple(p.part for p in parts) != PARTS or receipt.get("parts") != [{"part": p.part, "count": p.count} for p in parts]:
            raise ValueError("recorded part query mismatch")
        if stages or report_markers or "VF_BUILD passed" in text.splitlines():
            raise ValueError("project markers in device-only query")
        ready = "recorded_available" if execution == "completed" and parts[0].count == 1 else "not_established"
        build = "not_run"
    else:
        # The fixed synthesis recipe never emits a part query. Do not project any
        # untrusted part token (including malformed marker payloads) from its log.
        if any(line.startswith("VF_PART") for line in text.splitlines()):
            raise ValueError("part query marker in synthesis recording")
        passed = _build_lifecycle(text)
        identities = []
        sources = plan.get("sources")
        manifest = plan.get("source_manifest", {})
        if not isinstance(sources, list) or len(sources) != 3 or not isinstance(manifest, dict):
            raise ValueError("recorded source identities required")
        if (manifest.get("part"), manifest.get("top"), manifest.get("clock_period_ns")) != (PART, TOP, CLOCK_PERIOD_NS) or manifest.get("sources") != sources:
            raise ValueError("recorded project identity mismatch")
        for source, (name, digest) in zip(sources, EXERCISE3_HASHES.items(), strict=True):
            if not isinstance(source, dict) or source.get("name") != name or source.get("sha256") != digest:
                raise ValueError("recorded source identity mismatch")
            size = _integer(source.get("size_bytes"), 1)
            if size > MAX_RECORD_BYTES or artifacts.get(name) != {"bytes": size, "sha256": digest}:
                raise ValueError("recorded source receipt mismatch")
            identities.append(SourceIdentity(name, digest, size))
        project = ProjectReadiness("exercise3_nexys_video_v1", PART, TOP, CLOCK_PERIOD_NS, tuple(identities))
        ready = "not_queried_in_this_attempt"
        build = ("reported_passed" if execution == "completed" and passed and stages.get("synthesis") == "passed" else
                 "reported_failed" if "failed" in stages.values() else
                 "incomplete" if stages else "not_observed")
    reports = []
    for name in REPORTS:
        filename = name + ".rpt"
        marker = report_markers.get(name)
        path = root / filename
        digest = size = None
        if path.is_symlink() or path.is_junction():
            raise ValueError("linked recorded artifact")
        if not path.exists():
            availability = "missing" if marker == "captured" else "unavailable" if marker == "unavailable" else "incomplete" if marker == "started" else "absent"
        else:
            raw = _read(root, filename)
            verified = _artifact(artifacts.get(filename), raw)
            availability = "captured" if marker == "captured" and verified else "incomplete" if marker in ("started", "unavailable") else "unverified"
            if verified:
                digest, size = _digest(raw), len(raw)
        reports.append(ReportAvailability(name, availability, digest, size))
    return RecordedAttempt(recipe.value, _digest(receipt_raw), _digest(plan_raw), _digest(stdout), expected_script,
                           versions[0], PART, ready, parts, project, execution, exit_code, float(wall), limit,
                           observed_storage, storage_budget, active, build, tuple(reports))
