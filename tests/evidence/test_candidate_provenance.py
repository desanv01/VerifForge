"""Pure source/provenance checks. Scripted attempts are never simulator evidence."""
import hashlib
import json
from pathlib import Path

import pytest

from veriforge.adapters import get_adapter
from veriforge.contracts import (
    ArtifactReference, AttemptResult, EvidenceIdentity, Experiment, FailureSignature,
    ToolVersion, canonical_hash,
)
from veriforge.evidence import (
    CandidateProvenance, CandidateReviewLimits, CandidateSourceReview, CandidateValidation,
    ModelRecommendation, compare_candidate_sources, validate_candidate,
)

H = "a" * 64
ORIGINAL_SOURCE = b"module fifo; logic broken; endmodule\n"
CANDIDATE_SOURCE = b"module fifo; logic fixed; endmodule\n"
CHECKER = b"# scripted test checker identity, not simulator evidence\n"


def sha(data):
    return hashlib.sha256(data).hexdigest()


def plan(fixture="fifo_reset_bug"):
    return Experiment(experiment_id="scripted-source-review", fixture_id=fixture,
        objective_ids=("FIFO_BEHAVIOR",), operations=({"op": "reset"}, {"op": "write", "value": 17},
            {"op": "reset"}, {"op": "read"}))


def write_attempt(directory, experiment, source, *, failed=False):
    directory = Path(directory)
    directory.mkdir(parents=True)
    descriptor = get_adapter("rtl.fifo.v1")
    contract = descriptor.reviewed_contract(experiment.parameters)
    contract = contract.model_copy(update={"checker": contract.checker.model_copy(update={"sha256": sha(CHECKER)})})
    files = {"experiment.json": experiment.model_dump_json().encode(), "fifo.sv": source,
             "checker.py": CHECKER, "contract.json": contract.model_dump_json().encode()}
    refs = []
    for name, data in files.items():
        (directory / name).write_bytes(data)
        refs.append(ArtifactReference(artifact_id="artifact-" + str(len(refs)), path=name,
            sha256=sha(data), size_bytes=len(data), media_type="text/plain"))
    source_hash = sha(json.dumps({"source": sha(source),
        "variant": descriptor.fixture_source(experiment.fixture_id)["variant"],
        "parameters": experiment.parameters.model_dump()}, sort_keys=True, separators=(",", ":")).encode())
    result = AttemptResult(attempt_id=directory.name, experiment_id=experiment.experiment_id,
        execution_status="completed", verdict="design_mismatch" if failed else "pass_within_scope",
        identity=EvidenceIdentity(source_hash=source_hash, checker_hash=sha(CHECKER),
            contract_hash=canonical_hash(contract), experiment_hash=canonical_hash(experiment), environment_hash=H),
        failure_signature=FailureSignature(requirement_id="FIFO_BEHAVIOR", failure_class="fifo.empty",
            first_cycle=2, expected="True", observed="False", signature_hash=H) if failed else None,
        tool_versions=(ToolVersion(name="scripted-source-review-only", version="1"),), artifacts=tuple(refs))
    (directory / "result.json").write_text(result.model_dump_json())
    return result


@pytest.fixture
def snapshots(tmp_path):
    original, candidate = tmp_path / "original", tmp_path / "candidate"
    write_attempt(original, plan(), ORIGINAL_SOURCE, failed=True)
    write_attempt(candidate, plan("fifo_correct"), CANDIDATE_SOURCE)
    return original, candidate


def update_artifact(directory, name, data):
    result = AttemptResult.model_validate_json((directory / "result.json").read_bytes())
    (directory / name).write_bytes(data)
    refs = tuple(ref.model_copy(update={"sha256": sha(data), "size_bytes": len(data)})
                 if ref.path == name else ref for ref in result.artifacts)
    result = result.model_copy(update={"artifacts": refs})
    (directory / "result.json").write_text(result.model_dump_json())
    return result


def model_provenance():
    return CandidateProvenance(recommendation_origin="model_recommendation", proposal_id="proposal-1",
        proposal_sha256="c" * 64, model_call=ModelRecommendation(provider="meta", model="Llama",
            model_call_id="call-1", prompt_sha256=H, response_sha256="b" * 64))


def test_review_exact_source_and_variant_diffs_preserves_originals(snapshots):
    original, candidate = snapshots
    before = {(path.parent.name, path.name): sha(path.read_bytes())
              for directory in snapshots for path in directory.iterdir()}
    review = compare_candidate_sources(original, candidate)
    assert review.source_bytes_changed and review.configuration_changed
    assert "-module fifo; logic broken;" in review.unified_source_diff
    assert "+module fifo; logic fixed;" in review.unified_source_diff
    assert '"curated_variant": 1' in review.unified_configuration_diff
    assert '"curated_variant": 0' in review.unified_configuration_diff
    assert str(original) not in review.model_dump_json()
    assert review.provenance.materialization == "curated_fixture_selection"
    assert review.provenance.authorship_claim == "curated_source_not_generated_patch"
    assert CandidateSourceReview.model_validate_json(review.model_dump_json()) == review
    assert before == {(path.parent.name, path.name): sha(path.read_bytes())
                      for directory in snapshots for path in directory.iterdir()}


@pytest.mark.parametrize("kind", ["source", "checker", "contract"])
def test_review_rejects_rehashed_snapshot_not_bound_to_identity(snapshots, kind):
    original, candidate = snapshots
    if kind == "source":
        update_artifact(candidate, "fifo.sv", b"module forged; endmodule\n")
    elif kind == "checker":
        update_artifact(candidate, "checker.py", b"# changed checker\n")
    else:
        contract = json.loads((candidate / "contract.json").read_bytes())
        contract["version"] = "2"
        update_artifact(candidate, "contract.json", json.dumps(contract).encode())
    with pytest.raises(ValueError, match="identity mismatch"):
        compare_candidate_sources(original, candidate)


@pytest.mark.parametrize("limits", [CandidateReviewLimits(max_source_bytes=1),
    CandidateReviewLimits(max_source_lines=1, max_diff_bytes=1), CandidateReviewLimits(max_diff_bytes=1)])
def test_review_bounds_are_enforced_without_truncating(snapshots, limits):
    with pytest.raises(ValueError, match="limit"):
        compare_candidate_sources(*snapshots, limits=limits)


def test_precise_model_and_external_recommendations(snapshots):
    model = compare_candidate_sources(*snapshots, provenance=model_provenance())
    assert model.provenance.recommendation_origin == "model_recommendation"
    assert model.provenance.assertion_basis == "controller_supplied_record"
    assert model.provenance.materialization == "curated_fixture_selection"
    external = CandidateProvenance(recommendation_origin="external_recommendation",
        proposal_id="external-1", proposal_sha256=H, external_reference_id="review-1")
    assert compare_candidate_sources(*snapshots, provenance=external).provenance == external


@pytest.mark.parametrize("data", [
    {"recommendation_origin": "model_recommendation"},
    {"recommendation_origin": "external_recommendation", "proposal_id": "p", "proposal_sha256": H},
    {"recommendation_origin": "curated_catalog", "proposal_id": "not-curated"},
    {"recommendation_origin": "model_generated_patch"},
    {"materialization": "uploaded_patch"}, {"shell": "run anything"},
    {"authorship_claim": "model_authored_source"},
])
def test_cannot_invent_patch_authorship_or_omit_origin_identity(data):
    with pytest.raises(ValueError):
        CandidateProvenance.model_validate(data)


def validate(original, output, *, candidate_runner=None, provenance=None):
    return validate_candidate(original, output, candidate_fixture_id="fifo_correct",
        original_runner=lambda exp, out: write_attempt(out, exp, ORIGINAL_SOURCE, failed=True),
        candidate_runner=candidate_runner or (lambda exp, out: write_attempt(out, exp, CANDIDATE_SOURCE)),
        neighboring_plans=(plan(),), regression_plans=(plan(),), provenance=provenance)


def test_validation_embeds_bound_source_review_and_recommendation(snapshots, tmp_path):
    record = validate(snapshots[0], tmp_path / "validated", provenance=model_provenance())
    assert record.status == "validated_under_checks"
    assert record.source_review_status == "available"
    assert record.source_review_sha256 == canonical_hash(record.source_review)
    assert record.provenance == record.source_review.provenance == model_provenance()
    assert CandidateValidation.model_validate_json(record.model_dump_json()) == record
    data = record.model_dump(mode="json")
    data["source_review"]["candidate_attempt_id"] = "unrelated-attempt"
    data["source_review_sha256"] = canonical_hash(CandidateSourceReview.model_validate(data["source_review"]))
    with pytest.raises(ValueError, match="bound to the validation"):
        CandidateValidation.model_validate(data)


@pytest.mark.parametrize("field,value", [
    ("adapter_id", "rtl.arithmetic.v1"),
    ("original_source.fixture_id", "fifo_correct"),
    ("original_source.curated_variant", 7),
    ("candidate_source.path", "other.sv"),
    ("original_source.size_bytes", 999),
])
def test_rehashed_review_cannot_change_trusted_family_or_snapshot_metadata(snapshots, tmp_path, field, value):
    record = validate(snapshots[0], tmp_path / "metadata-binding")
    payload = record.model_dump(mode="json")
    if "." in field:
        parent, key = field.split(".")
        payload["source_review"][parent][key] = value
    else:
        payload["source_review"][field] = value
    with pytest.raises(ValueError):
        review = CandidateSourceReview.model_validate(payload["source_review"])
        payload["source_review_sha256"] = canonical_hash(review)
        CandidateValidation.model_validate(payload)


def test_source_review_change_claims_are_derived_not_freeform(snapshots):
    review = compare_candidate_sources(*snapshots)
    for update in ({"source_bytes_changed": False}, {"configuration_changed": False},
                   {"unified_source_diff": ""}, {"unified_configuration_diff": ""}):
        with pytest.raises(ValueError):
            CandidateSourceReview.model_validate({**review.model_dump(mode="json"), **update})


def test_invalid_source_snapshots_cannot_hide_behind_passing_runner(snapshots, tmp_path):
    def tampered(exp, out):
        write_attempt(out, exp, CANDIDATE_SOURCE)
        return update_artifact(out, "checker.py", b"# unbound changed checker\n")
    record = validate(snapshots[0], tmp_path / "invalid", candidate_runner=tampered)
    assert record.status == "failed_under_checks"
    assert record.source_review_status == "invalid_source_snapshots" and record.source_review is None
    assert record.witness.verdict == "pass_within_scope"


def test_legacy_records_do_not_invent_review_or_model_authorship(snapshots, tmp_path):
    record = validate(snapshots[0], tmp_path / "new")
    data = record.model_dump(mode="json")
    for key in ("provenance", "source_review", "source_review_status", "source_review_sha256"):
        data.pop(key)
    legacy = CandidateValidation.model_validate(data)
    assert legacy.provenance is None and legacy.source_review is None
    assert legacy.source_review_status == "legacy_unrecorded"


def test_missing_legacy_snapshot_is_explicitly_unavailable(snapshots, tmp_path):
    original = snapshots[0]
    result = AttemptResult.model_validate_json((original / "result.json").read_bytes())
    result = result.model_copy(update={"artifacts": tuple(ref for ref in result.artifacts if ref.path != "checker.py")})
    (original / "result.json").write_text(result.model_dump_json())
    record = validate(original, tmp_path / "missing-review")
    assert record.status == "validated_under_checks"
    assert record.source_review_status == "missing_source_snapshots"
    assert record.source_review is None and record.source_review_sha256 is None
    assert record.provenance.recommendation_origin == "curated_catalog"


def test_sensitive_provenance_rejected_before_runner_or_attempt_read(tmp_path):
    data = model_provenance().model_dump(mode="json")
    data["model_call"]["provider"] = "C:/Users/private"
    provenance = CandidateProvenance.model_validate(data)
    with pytest.raises(ValueError, match="possible secret or local"):
        validate(tmp_path / "does-not-exist", tmp_path / "never-created", provenance=provenance)
    assert not (tmp_path / "never-created").exists()


def test_source_review_cannot_change_witness_input(snapshots, tmp_path):
    candidate = tmp_path / "different-witness"
    write_attempt(candidate, plan("fifo_correct").model_copy(update={"seed": 7}), CANDIDATE_SOURCE)
    with pytest.raises(ValueError, match="exact original witness"):
        compare_candidate_sources(snapshots[0], candidate)


ROOT = Path(__file__).resolve().parents[2]
ACCEPTED_ORIGINAL = ROOT / "infra/rtl/evidence/linux-tests-4/test_real_correct_mutant_and_r0/replay"
ACCEPTED_CANDIDATE = ROOT / "tests/evidence/.tmp-live-final/test_real_replay_reduce_export0/actual-validation/candidate-000"


@pytest.mark.skipif(not ACCEPTED_ORIGINAL.exists() or not ACCEPTED_CANDIDATE.exists(),
                    reason="saved accepted real FIFO snapshots unavailable")
def test_saved_real_curated_fix_is_configuration_change_not_generated_source_patch():
    before = {(str(directory), path.name): sha(path.read_bytes())
              for directory in (ACCEPTED_ORIGINAL, ACCEPTED_CANDIDATE) for path in directory.iterdir() if path.is_file()}
    review = compare_candidate_sources(ACCEPTED_ORIGINAL, ACCEPTED_CANDIDATE)
    assert not review.source_bytes_changed and review.unified_source_diff == ""
    assert review.configuration_changed
    assert review.original_source.curated_variant == 1 and review.candidate_source.curated_variant == 0
    assert review.provenance.authorship_claim == "curated_source_not_generated_patch"
    assert before == {(str(directory), path.name): sha(path.read_bytes())
                     for directory in (ACCEPTED_ORIGINAL, ACCEPTED_CANDIDATE) for path in directory.iterdir() if path.is_file()}


ARITHMETIC = ROOT / "tests/arithmetic/.tmp-live-vf050b-retry-20261024"


@pytest.mark.skipif(not ARITHMETIC.exists(), reason="saved accepted arithmetic snapshots unavailable")
@pytest.mark.parametrize("mutant", [1, 2])
def test_saved_arithmetic_review_preserves_family_checker_and_curated_provenance(mutant):
    original = ARITHMETIC / f"test_live_curated_multiplier_m{mutant}"
    candidate = ARITHMETIC / "test_live_curated_multiplier_m0"
    before = {(str(directory), path.name): sha(path.read_bytes())
              for directory in (original, candidate) for path in directory.iterdir() if path.is_file()}
    review = compare_candidate_sources(original, candidate)
    assert review.adapter_id == "rtl.arithmetic.v1"
    assert not review.source_bytes_changed and review.configuration_changed
    assert review.original_source.path == review.candidate_source.path == "multiplier.sv"
    assert review.original_source.curated_variant == mutant and review.candidate_source.curated_variant == 0
    assert before == {(str(directory), path.name): sha(path.read_bytes())
                     for directory in (original, candidate) for path in directory.iterdir() if path.is_file()}
