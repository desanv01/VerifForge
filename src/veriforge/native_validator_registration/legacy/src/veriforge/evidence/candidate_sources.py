"""Read-only bounded source review for two already recorded curated attempts."""
from difflib import unified_diff
import hashlib
import json
from pathlib import Path

from veriforge.contracts import canonical_hash
from .candidate_models import CandidateProvenance, CandidateReviewLimits, CandidateSourceReview, ReviewedSource
from .families import descriptor_for


def _sha(data):
    return hashlib.sha256(data).hexdigest()


def _encoded(data):
    return json.dumps(data, sort_keys=True, separators=(",", ":")).encode()


def _diff(before, after, name, limits):
    lines = unified_diff(before.splitlines(keepends=True), after.splitlines(keepends=True),
                         fromfile="original/" + name, tofile="candidate/" + name)
    chunks, size = [], 0
    for line in lines:
        if not line.endswith("\n"):
            line += "\n\\ No newline at end of file\n"
        size += len(line.encode("utf-8"))
        if size > limits.max_diff_bytes:
            raise ValueError("candidate diff exceeds byte limit")
        chunks.append(line)
    return "".join(chunks)


def _snapshot(root, experiment, result, export_limits, review_limits):
    from .workflow import portable_artifact_bytes
    descriptor = descriptor_for(experiment)
    by_name = {ref.path: ref for ref in result.artifacts}
    required = (descriptor.source_artifact, "checker.py", "contract.json")
    if any(name is None or name not in by_name for name in required):
        raise ValueError("candidate review requires source/checker/contract snapshots")
    source_ref = by_name[descriptor.source_artifact]
    if source_ref.size_bytes > review_limits.max_source_bytes:
        raise ValueError("candidate source exceeds byte limit")
    source = portable_artifact_bytes(root, source_ref.artifact_id, limits=export_limits)
    text = source.decode("utf-8")
    if len(text.splitlines()) > review_limits.max_source_lines:
        raise ValueError("candidate source exceeds line limit")
    checker = portable_artifact_bytes(root, by_name["checker.py"].artifact_id, limits=export_limits)
    contract_bytes = portable_artifact_bytes(root, by_name["contract.json"].artifact_id, limits=export_limits)
    contract = descriptor.contract_model.model_validate_json(contract_bytes)
    if (_sha(checker) != result.identity.checker_hash or contract.checker.sha256 != result.identity.checker_hash
            or canonical_hash(contract) != result.identity.contract_hash
            or contract.parameters != experiment.parameters or contract.contract_id != experiment.contract_id
            or contract.review_status != "reviewed"):
        raise ValueError("candidate review protected checker/contract identity mismatch")
    variant = descriptor.fixture_source(experiment.fixture_id)["variant"]
    source_identity = _sha(_encoded({"source": _sha(source), "variant": variant,
                                   "parameters": experiment.parameters.model_dump()}))
    if source_identity != result.identity.source_hash:
        raise ValueError("candidate review source snapshot identity mismatch")
    snapshot = ReviewedSource(path=descriptor.source_artifact, file_sha256=_sha(source),
        source_identity_sha256=source_identity, size_bytes=len(source),
        fixture_id=experiment.fixture_id, curated_variant=variant)
    configuration = {"fixture_id": experiment.fixture_id, "curated_variant": variant,
                     "parameters": experiment.parameters.model_dump(mode="json")}
    return snapshot, text, configuration


def compare_candidate_sources(original_attempt_dir: Path, candidate_attempt_dir: Path, *,
                              provenance: CandidateProvenance | None = None,
                              limits: CandidateReviewLimits = CandidateReviewLimits()) -> CandidateSourceReview:
    """Inspect recorded source/configuration; never apply patches or run candidates."""
    from .workflow import ExportLimits, _screen_content, load_attempt, same_failure
    provenance = CandidateProvenance.model_validate((provenance or CandidateProvenance()).model_dump(mode="json"))
    _screen_content(provenance.model_dump_json().encode())
    original_dir, candidate_dir = Path(original_attempt_dir).resolve(), Path(candidate_attempt_dir).resolve()
    original, original_result = load_attempt(original_dir)
    candidate, candidate_result = load_attempt(candidate_dir)
    descriptor = descriptor_for(original)
    if not same_failure(original_result, original_result):
        raise ValueError("source repair review requires a checked original design failure")
    if descriptor_for(candidate).adapter_id != descriptor.adapter_id or candidate.fixture_id == original.fixture_id:
        raise ValueError("candidate review requires a separate curated fixture in the same adapter")
    expected_plan = original.model_dump(mode="json")
    expected_plan["fixture_id"] = candidate.fixture_id
    if descriptor.validate_experiment(expected_plan) != candidate:
        raise ValueError("candidate review requires the exact original witness with only fixture changed")
    if (original_result.identity.checker_hash != candidate_result.identity.checker_hash
            or original_result.identity.contract_hash != candidate_result.identity.contract_hash):
        raise ValueError("candidate review cannot change checker or contract")
    export_limits = ExportLimits()
    before, before_text, before_config = _snapshot(original_dir, original, original_result, export_limits, limits)
    after, after_text, after_config = _snapshot(candidate_dir, candidate, candidate_result, export_limits, limits)
    review = CandidateSourceReview(adapter_id=descriptor.adapter_id,
        original_attempt_id=original_result.attempt_id, candidate_attempt_id=candidate_result.attempt_id,
        original_experiment_sha256=canonical_hash(original), candidate_experiment_sha256=canonical_hash(candidate),
        checker_sha256=original_result.identity.checker_hash, contract_sha256=original_result.identity.contract_hash,
        original_source=before, candidate_source=after,
        source_bytes_changed=before.file_sha256 != after.file_sha256,
        configuration_changed=before_config != after_config,
        unified_source_diff=_diff(before_text, after_text, before.path, limits),
        unified_configuration_diff=_diff(json.dumps(before_config, indent=2, sort_keys=True) + "\n",
            json.dumps(after_config, indent=2, sort_keys=True) + "\n", "curated-configuration.json", limits),
        provenance=provenance)
    if len((review.unified_source_diff + review.unified_configuration_diff).encode()) > limits.max_diff_bytes:
        raise ValueError("candidate combined diff exceeds byte limit")
    _screen_content(review.model_dump_json().encode())
    if load_attempt(original_dir) != (original, original_result) or load_attempt(candidate_dir) != (candidate, candidate_result):
        raise ValueError("candidate evidence changed during source review")
    return review
