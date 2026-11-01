"""Read-only bounded source review for two already recorded curated attempts."""
from difflib import unified_diff
import hashlib
import json
from pathlib import Path
from typing import Literal

from pydantic import model_validator

from veriforge.contracts import Sha256, WireModel, canonical_hash
from .candidate_models import (CandidateProvenance, CandidateReviewLimits, CandidateSourceReview,
                               FamilyValidationSuite, ReviewedSource)
from .families import descriptor_for
from .workflow import CandidateValidation


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


def build_family_validation_suite(experiment):
    """Deterministic installed checks, not model-invented expected outputs.

    All cases retain the caller's contract, parameters, objectives and budgets.
    Too-small budgets reject construction rather than silently omitting a suite.
    """
    from .candidate_models import FamilyValidationCase, FamilyValidationSuite
    from .families import stimulus_identity
    descriptor = descriptor_for(experiment)
    experiment = descriptor.validate_experiment(experiment)
    base = experiment.model_dump(mode="json")
    cases = [FamilyValidationCase(role="witness", label="original-witness", experiment=experiment)]
    seen = set()
    def stimulus_hash(plan):
        return stimulus_identity(plan)
    seen.add(stimulus_hash(experiment))
    def add(role, label, changes):
        data = {**base, **changes, "experiment_id": "repair-" + label}
        plan = descriptor.validate_experiment(data)
        key = stimulus_hash(plan)
        if key not in seen:
            cases.append(FamilyValidationCase(role=role, label=label, experiment=plan))
            seen.add(key)
    if descriptor.adapter_id == "rtl.arithmetic.v1":
        rows = base["operations"]
        mask = (1 << experiment.parameters.width) - 1
        changed = []
        for row in rows:
            if row["op"] == "multiply" or row["op"] == "cycle" and row["valid"]:
                row = {**row, "a": row["a"] ^ 1}
            changed.append(row)
        add("neighbor", "operand-neighbor", {"operations": changed})
        add("neighbor", "drain-neighbor", {"operations": rows + [{"op": "idle", "cycles": 1}]})
        add("regression", "zero-and-maximum", {"operations": [
            {"op": "reset"}, {"op": "multiply", "a": 0, "b": mask},
            {"op": "multiply", "a": mask, "b": mask}, {"op": "idle", "cycles": 2}]})
        add("regression", "reset-cancels-pipeline", {"operations": [
            {"op": "reset"}, {"op": "multiply", "a": mask, "b": 1},
            {"op": "reset"}, {"op": "idle", "cycles": 3}]})
    elif descriptor.adapter_id == "rtl.axi_lite.v1":
        from veriforge.axi_lite.runner import directed_experiment, witness_experiment
        rows = base["cycles"]
        add("neighbor", "payload-neighbor", {"cycles": [
            {**row, "w_data": row["w_data"] ^ 1} for row in rows]})
        add("neighbor", "settlement-neighbor", {"cycles": rows + [{"reset_n": True}]})
        for name in ("order", "strobe", "response"):
            add("regression", "axil-" + name, {"cycles": witness_experiment(name).model_dump(mode="json")["cycles"]})
        add("regression", "axil-register-regression", {"cycles": directed_experiment().model_dump(mode="json")["cycles"]})
    else:
        raise ValueError("automatic repair suites support arithmetic and bounded AXI only")
    expected = descriptor.expected_identity(experiment)
    return FamilyValidationSuite(adapter_id=descriptor.adapter_id, checker_sha256=expected["checker_hash"],
        contract_sha256=expected["contract_hash"], cases=tuple(cases))


# This is the existing execution report plus an exact reproducible check-suite
# identity; orchestration and tool ownership stay in the existing workflow.
class FamilyCandidateReport(WireModel):
    schema_version: Literal["family.candidate-report.v1"] = "family.candidate-report.v1"
    suite: FamilyValidationSuite
    suite_sha256: Sha256
    validation: CandidateValidation

    @model_validator(mode="after")
    def bind_suite(self):
        from .workflow import _identity_matches, same_failure
        if self.schema_version != "family.candidate-report.v1" or self.suite_sha256 != canonical_hash(self.suite):
            raise ValueError("candidate suite identity changed")
        record = self.validation
        if record.original_experiment != self.suite.cases[0].experiment:
            raise ValueError("candidate suite is not bound to the original witness")
        expected = []
        for case in self.suite.cases:
            data = case.experiment.model_dump(mode="json")
            data["fixture_id"] = record.candidate_fixture_id
            expected.append(descriptor_for(case.experiment).validate_experiment(data))
        if (tuple(expected) != record.candidate_experiments
                or record.neighboring_plan_count != sum(c.role == "neighbor" for c in self.suite.cases)
                or record.regression_plan_count != sum(c.role == "regression" for c in self.suite.cases)):
            raise ValueError("candidate execution does not match exact check suite")
        identity = record.original_result.identity
        if (identity is None or identity.checker_hash != self.suite.checker_sha256
                or identity.contract_hash != self.suite.contract_sha256
                or record.original_result.experiment_id != record.original_experiment.experiment_id
                or any(getattr(identity, key) != value for key, value in
                       descriptor_for(record.original_experiment).expected_identity(record.original_experiment).items())
                or not same_failure(record.original_result, record.original_result)
                or record.provenance is None):
            raise ValueError("candidate report requires checked original failure and explicit origin")
        reproduced = (_identity_matches(record.original_replay, record.original_experiment, record.original_result)
                      and same_failure(record.original_result, record.original_replay))
        results = (() if record.witness is None else (record.witness,)) + record.neighbors + record.regressions
        if record.status == "original_not_reproduced":
            if reproduced or results:
                raise ValueError("unreproduced candidate cannot contain executed candidate checks")
        else:
            if (not reproduced or len(results) != len(expected)
                    or len(record.neighbors) != record.neighboring_plan_count
                    or len(record.regressions) != record.regression_plan_count):
                raise ValueError("candidate report omits checks or failing-witness replay")
            passed = all(result.execution_status == "completed" and result.verdict == "pass_within_scope"
                and result.failure_signature is None and result.identity is not None
                and all(getattr(result.identity, key) == value for key, value in
                        descriptor_for(plan).expected_identity(plan).items())
                for plan, result in zip(expected, results))
            if record.status == "validated_under_checks" and (not passed or record.source_review_status != "available"):
                raise ValueError("validated repair requires every exact check and source provenance")
        return self


def validate_family_candidate(attempt_dir, output_dir, *, candidate_fixture_id, original_runner,
                              candidate_runner, provenance=None, suite=None):
    """Execute the witness, neighboring and regression checks via trusted runners.

    Callers own resource/control admission; no model calls, patches or shell input.
    Scripted evidence remains scripted: this API cannot certify a native launch.
    """
    from .families import checked_family_attempt, _checked_runner
    from .workflow import validate_candidate, _save
    original, _ = checked_family_attempt(attempt_dir)
    suite = FamilyValidationSuite.model_validate((suite or build_family_validation_suite(original)).model_dump(mode="json"))
    if suite.cases[0].experiment != original:
        raise ValueError("candidate suite witness differs from original")
    # Group roles deterministically even for explicitly supplied suites.
    cases = (suite.cases[0], *(c for c in suite.cases if c.role == "neighbor"),
             *(c for c in suite.cases if c.role == "regression"))
    suite = FamilyValidationSuite.model_validate({**suite.model_dump(mode="json"),
        "cases": [c.model_dump(mode="json") for c in cases]})
    source, output = Path(attempt_dir).resolve(), Path(output_dir).resolve()
    if output.is_relative_to(source) or source.is_relative_to(output):
        raise ValueError("candidate output must be separate from original evidence")
    record = validate_candidate(source, output, candidate_fixture_id=candidate_fixture_id,
        original_runner=_checked_runner(original_runner), candidate_runner=_checked_runner(candidate_runner),
        neighboring_plans=tuple(c.experiment for c in cases if c.role == "neighbor"),
        regression_plans=tuple(c.experiment for c in cases if c.role == "regression"), provenance=provenance)
    report = FamilyCandidateReport(suite=suite, suite_sha256=canonical_hash(suite), validation=record)
    _save(output / "family-validation.json", report)
    return report
