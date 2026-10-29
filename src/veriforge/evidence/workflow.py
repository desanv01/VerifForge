"""No commands or patches are accepted here; runners are trusted controller callbacks."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
import re
from typing import Annotated, Callable, Literal
from zipfile import ZIP_DEFLATED, ZipFile

from pydantic import Field, ValidationError

from veriforge.adapters import ValidatedExperiment

from veriforge.contracts import (
    ArtifactReference, AttemptResult, WireModel, canonical_hash,
)
from .families import descriptor_for, validate_arithmetic_trace, validate_experiment

EvidenceExperiment = Annotated[ValidatedExperiment, Field(discriminator="target")]
EvidenceRunner = Callable[[ValidatedExperiment, Path], AttemptResult]


class ExportLimits(WireModel):
    max_bytes: int = Field(default=20_971_520, ge=1024, le=1_073_741_824)
    max_files: int = Field(default=100, ge=1, le=1000)


class ReplayRecord(WireModel):
    original: AttemptResult
    replayed: AttemptResult
    same_semantic_failure: bool


class ReductionRecord(WireModel):
    original_experiment: EvidenceExperiment
    original_result: AttemptResult
    reduced_experiment: EvidenceExperiment
    reduced_result: AttemptResult
    attempts: tuple[AttemptResult, ...]
    submitted_experiments: tuple[EvidenceExperiment, ...]
    max_attempts: int
    improved: bool
    claim: Literal["bounded_search_no_minimality_guarantee"] = "bounded_search_no_minimality_guarantee"


class CandidateValidation(WireModel):
    original_experiment: EvidenceExperiment
    original_result: AttemptResult
    original_replay: AttemptResult
    candidate_fixture_id: str
    candidate_experiments: tuple[EvidenceExperiment, ...]
    neighboring_plan_count: int
    regression_plan_count: int
    witness: AttemptResult | None = None
    neighbors: tuple[AttemptResult, ...] = ()
    regressions: tuple[AttemptResult, ...] = ()
    status: Literal["original_not_reproduced", "failed_under_checks", "validated_under_checks"]
    execution_basis: Literal["supplied_runner_evidence"] = "supplied_runner_evidence"


def _digest(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _read(path: Path, limit: int) -> bytes:
    with path.open("rb") as stream:
        data = stream.read(limit + 1)
    if len(data) > limit:
        raise ValueError("evidence exceeds byte limit")
    return data


def _artifact(root: Path, relative: str) -> Path:
    # Models validate relative POSIX names; resolution also rejects symlink escapes.
    path = (root / relative).resolve()
    if not path.is_relative_to(root) or not path.is_file():
        raise ValueError("artifact escapes attempt or is missing")
    return path


def load_attempt(attempt_dir: Path, *, limits: ExportLimits = ExportLimits()) -> tuple[ValidatedExperiment, AttemptResult]:
    """Read exact stored input and verify every referenced artifact, size and hash."""
    root = Path(attempt_dir).resolve()
    result = AttemptResult.model_validate_json(_read(_artifact(root, "result.json"), limits.max_bytes))
    experiment = validate_experiment(json.loads(_read(_artifact(root, "experiment.json"), limits.max_bytes)))
    if result.experiment_id != experiment.experiment_id:
        raise ValueError("result refers to a different experiment")
    if result.identity is None or result.identity.experiment_hash != canonical_hash(experiment):
        raise ValueError("missing or mismatched experiment identity")
    if len(result.artifacts) > limits.max_files:
        raise ValueError("too many evidence files")
    paths, ids = set(), set()
    total = 0
    for ref in result.artifacts:
        if ref.path in paths or ref.artifact_id in ids:
            raise ValueError("duplicate artifact path or ID")
        paths.add(ref.path)
        ids.add(ref.artifact_id)
        total += ref.size_bytes
        if total > limits.max_bytes:
            raise ValueError("evidence exceeds byte limit")
        data = _read(_artifact(root, ref.path), min(ref.size_bytes, limits.max_bytes))
        if len(data) != ref.size_bytes or _digest(data) != ref.sha256:
            raise ValueError("artifact hash or size mismatch")
    descriptor = descriptor_for(experiment)
    if descriptor.trace_artifact is not None:
        if result.trace:
            raise ValueError("family-specific trace cannot use FIFO trace fields")
        design = result.verdict in {"pass_within_scope", "design_mismatch", "property_failure"}
        if descriptor.trace_artifact in paths:
            raw = _read(_artifact(root, descriptor.trace_artifact), limits.max_bytes)
            ref = next(ref for ref in result.artifacts if ref.path == descriptor.trace_artifact)
            if len(raw) != ref.size_bytes or _digest(raw) != ref.sha256:
                raise ValueError("arithmetic trace changed during validation")
            validate_arithmetic_trace(raw, experiment, result)
        elif design:
            raise ValueError("checked arithmetic result requires its family trace artifact")
    return experiment, result


def _identity_matches(result: AttemptResult, experiment: ValidatedExperiment, original: AttemptResult,
                      *, candidate: bool = False) -> bool:
    a, b = original.identity, result.identity
    return bool(a and b and result.experiment_id == experiment.experiment_id
                and b.experiment_hash == canonical_hash(experiment)
                and a.checker_hash == b.checker_hash and a.contract_hash == b.contract_hash
                and (candidate or a.source_hash == b.source_hash))


def same_failure(original: AttemptResult, result: AttemptResult) -> bool:
    """Semantic identity intentionally excludes cycle/value changes during reduction."""
    a, b = original.failure_signature, result.failure_signature
    return bool(original.execution_status == result.execution_status == "completed"
                and original.verdict == result.verdict
                and original.verdict in {"design_mismatch", "property_failure"}
                and a and b and a.requirement_id == b.requirement_id
                and a.failure_class == b.failure_class
                and original.identity and result.identity
                and original.identity.source_hash == result.identity.source_hash
                and original.identity.checker_hash == result.identity.checker_hash
                and original.identity.contract_hash == result.identity.contract_hash)


def _new_output(path: Path) -> Path:
    path = Path(path).resolve()
    path.mkdir(parents=True, exist_ok=False)
    return path


def _save(path: Path, value: WireModel):
    with path.open("x", encoding="utf-8") as stream:
        stream.write(value.model_dump_json(indent=2))


def replay_attempt(attempt_dir: Path, output_dir: Path, *, runner: EvidenceRunner | None = None) -> ReplayRecord:
    experiment, original = load_attempt(attempt_dir)
    if runner is None:
        descriptor = descriptor_for(experiment)
        expected = descriptor.expected_identity(experiment)
        if any(getattr(original.identity, key) != value for key, value in expected.items()):
            raise ValueError("replay source/checker/contract/experiment identity changed")
        ref = next((ref for ref in original.artifacts if ref.path == "stimulus.json"), None)
        if ref is None:
            raise ValueError("native replay requires verified stored stimulus")
        raw = _read(_artifact(Path(attempt_dir).resolve(), ref.path), ref.size_bytes)
        if _digest(raw) != ref.sha256 or json.loads(raw) != descriptor.expand_stimulus(experiment):
            raise ValueError("stored stimulus differs from exact typed experiment")
        result = descriptor.resolve_runner("native")(experiment, output_dir)
    else:
        result = runner(experiment, output_dir)
    if not _identity_matches(result, experiment, original):
        raise ValueError("replay source/checker/contract/experiment identity changed")
    return ReplayRecord(original=original, replayed=result, same_semantic_failure=same_failure(original, result))


def _candidates(experiment: ValidatedExperiment):
    operations = experiment.model_dump(mode="json")["operations"]
    # Chunk deletion first; then single deletions and bounded scalar simplification.
    size = max(1, len(operations) // 2)
    while size:
        for start in range(0, len(operations), size):
            yield operations[:start] + operations[start + size:]
        size //= 2
    for index, op in enumerate(operations):
        for field, value in (("cycles", 1), ("value", 0), ("a", 0), ("b", 0)):
            if field in op and op[field] != value:
                yield operations[:index] + [{**op, field: value}] + operations[index + 1:]


def reduce_failure(attempt_dir: Path, output_dir: Path, *, runner: EvidenceRunner,
                   max_attempts: int = 30) -> ReductionRecord:
    if type(max_attempts) is not int or not 1 <= max_attempts <= 1000:
        raise ValueError("max_attempts must be between 1 and 1000")
    experiment, original = load_attempt(attempt_dir)
    if not same_failure(original, original):
        raise ValueError("reduction requires an independently checked design failure")
    root = _new_output(output_dir)
    best, best_result, attempts, submitted = experiment, original, [], []
    seen = {canonical_hash(experiment)}
    while len(attempts) < max_attempts:
        accepted = False
        for operations in _candidates(best):
            data = best.model_dump(mode="json")
            data["operations"] = operations
            try:
                proposed = validate_experiment(data)
            except ValidationError:
                continue
            key = canonical_hash(proposed)
            if key in seen:
                continue
            seen.add(key)
            result = runner(proposed, root / f"attempt-{len(attempts):04d}")
            attempts.append(result)
            submitted.append(proposed)
            if _identity_matches(result, proposed, original) and same_failure(original, result):
                best, best_result, accepted = proposed, result, True
                break
            if len(attempts) >= max_attempts:
                break
        if not accepted:
            break
    record = ReductionRecord(original_experiment=experiment, original_result=original,
        reduced_experiment=best, reduced_result=best_result, attempts=tuple(attempts),
        submitted_experiments=tuple(submitted), max_attempts=max_attempts,
        improved=canonical_hash(best) != canonical_hash(experiment))
    _save(root / "reduction.json", record)
    return record


def validate_candidate(attempt_dir: Path, output_dir: Path, *, candidate_fixture_id: str,
                       candidate_runner: EvidenceRunner, original_runner: EvidenceRunner,
                       neighboring_plans: tuple[ValidatedExperiment, ...], regression_plans: tuple[ValidatedExperiment, ...]) -> CandidateValidation:
    experiment, original = load_attempt(attempt_dir)
    if not same_failure(original, original):
        raise ValueError("candidate validation requires a checked original failure")
    if not neighboring_plans or not regression_plans or len(neighboring_plans) + len(regression_plans) > 100:
        raise ValueError("supply bounded nonempty neighboring and regression plans")
    if candidate_fixture_id == experiment.fixture_id:
        raise ValueError("candidate must select a separate curated fixture revision")
    plans = (experiment, *neighboring_plans, *regression_plans)
    candidates = []
    for plan in plans:
        plan = validate_experiment(plan)
        if descriptor_for(plan).adapter_id != descriptor_for(experiment).adapter_id:
            raise ValueError("candidate validation cannot change adapter family")
        if (plan.contract_id, plan.target, plan.parameters, plan.fixture_id) != (
                experiment.contract_id, experiment.target, experiment.parameters, experiment.fixture_id):
            raise ValueError("validation plans must keep original contract/target/parameters/revision")
        data = plan.model_dump(mode="json")
        data["fixture_id"] = candidate_fixture_id
        candidates.append(validate_experiment(data))
    root = _new_output(output_dir)
    reproduced = original_runner(experiment, root / "original-replay")
    common = dict(original_experiment=experiment, original_result=original, original_replay=reproduced,
                  candidate_fixture_id=candidate_fixture_id, candidate_experiments=tuple(candidates),
                  neighboring_plan_count=len(neighboring_plans), regression_plan_count=len(regression_plans))
    if not _identity_matches(reproduced, experiment, original) or not same_failure(original, reproduced):
        record = CandidateValidation(**common, status="original_not_reproduced")
    else:
        results = tuple(candidate_runner(plan, root / f"candidate-{index:03d}") for index, plan in enumerate(candidates))
        candidate_source = results[0].identity.source_hash if results[0].identity else None
        passed = all(_identity_matches(result, plan, original, candidate=True)
                     and result.execution_status == "completed" and result.verdict == "pass_within_scope"
                     and result.identity.source_hash == candidate_source
                     and candidate_source != original.identity.source_hash
                     for plan, result in zip(candidates, results))
        n = len(neighboring_plans)
        record = CandidateValidation(**common, witness=results[0], neighbors=results[1:n+1],
            regressions=results[n+1:], status="validated_under_checks" if passed else "failed_under_checks")
    _save(root / "validation.json", record)
    return record


# Raw logs, job descriptions, environment diagnostics and arbitrary uploaded files
# are deliberately absent. Exact originals remain in the local attempt directory.
_SENSITIVE = re.compile(r"(?i)([a-z]:[/\\]|/(?:users|home|workspace|attempt|tmp|var|private)/|"
                        r"(?:api[_-]?key|access[_-]?token|password|authorization)\s*[\"']?\s*[:=]|"
                        r"\b(?:sk-|ghp_|github_pat_)[a-z0-9_-]{8,}|-----BEGIN .*PRIVATE KEY)")


def _screen_content(data: bytes) -> None:
    try:
        content = data.decode("utf-8", errors="strict")
    except UnicodeDecodeError as exc:
        raise ValueError("portable evidence must be UTF-8 text") from exc
    if _SENSITIVE.search(content):
        raise ValueError("portable evidence contains possible secret or local machine path")


def _portable_bytes(root: Path, ref: ArtifactReference, limits: ExportLimits, experiment: ValidatedExperiment) -> bytes:
    if ref.path not in descriptor_for(experiment).portable_artifacts:
        raise ValueError("artifact is not permitted for portable download")
    data = _read(_artifact(root, ref.path), min(ref.size_bytes, limits.max_bytes))
    if len(data) != ref.size_bytes or _digest(data) != ref.sha256:
        raise ValueError("artifact changed during portable read")
    _screen_content(data)
    return data


def portable_artifact_bytes(attempt_dir: Path, artifact_id: str, *,
                            limits: ExportLimits = ExportLimits()) -> bytes:
    """Return a verified bounded snapshot; authorization remains the caller's duty."""
    experiment, result = load_attempt(attempt_dir, limits=limits)
    ref = next((ref for ref in result.artifacts if ref.artifact_id == artifact_id), None)
    if ref is None:
        raise ValueError("artifact ID is not present in the verified attempt")
    return _portable_bytes(Path(attempt_dir).resolve(), ref, limits, experiment)


def export_bundle(attempt_dir: Path, destination: Path, *, limits: ExportLimits = ExportLimits()) -> Path:
    """Create an exclusive bounded ZIP; reject suspicious content instead of mutating evidence."""
    experiment, result = load_attempt(attempt_dir, limits=limits)
    root, destination = Path(attempt_dir).resolve(), Path(destination).resolve()
    if destination.is_relative_to(root):
        raise ValueError("export must not modify original evidence directory")
    descriptor = descriptor_for(experiment)
    selected = [ref for ref in result.artifacts if ref.path in descriptor.portable_artifacts]
    metadata = {
        "schema_version": "1", "experiment": experiment.model_dump(mode="json"),
        "execution_status": result.execution_status, "verdict": result.verdict,
        "identity": result.identity.model_dump(mode="json"),
        "tool_versions": [v.model_dump(mode="json") for v in result.tool_versions],
        "trace": [row.model_dump(mode="json") for row in result.trace],
        "adapter_id": descriptor.adapter_id,
        "family_trace": ({"schema_version": descriptor.trace_schema,
            "artifact": "artifacts/" + descriptor.trace_artifact}
            if descriptor.trace_artifact and any(ref.path == descriptor.trace_artifact for ref in selected) else None),
        "failure": ({k: v for k, v in result.failure_signature.model_dump(mode="json").items()
                     if k not in {"expected", "observed"}} if result.failure_signature else None),
        "artifacts": [ref.model_dump(mode="json") for ref in selected],
        "limitations": ["Local logs and environment diagnostics excluded; replay requires matching installed curated runner.",
                        "Hashes establish consistency, not authenticity against a malicious evidence producer."],
    }
    files = {"manifest.json": json.dumps(metadata, sort_keys=True, indent=2).encode(),
             "experiment.json": experiment.model_dump_json(indent=2).encode(),
             "REPORT.txt": b"VerifForge evidence export\nReplay the exact experiment using the matching curated RTL runner.\nVerify manifest artifact SHA-256 values before use. No model call is needed.\nA passing build is not functional correctness. No global minimality is claimed.\n"}
    for ref in selected:
        files["artifacts/" + ref.path] = _portable_bytes(root, ref, limits, experiment)
    if len(files) > limits.max_files or sum(map(len, files.values())) > limits.max_bytes:
        raise ValueError("export exceeds file or byte limit")
    for data in files.values():
        _screen_content(data)
    # No file exists until every byte has passed verification and screening.
    with destination.open("xb") as stream:
        with ZipFile(stream, "w", compression=ZIP_DEFLATED) as archive:
            for name, data in files.items():
                archive.writestr(name, data)
    return destination
