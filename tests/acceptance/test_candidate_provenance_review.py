"""Independent candidate-source review: synthetic records; no tool execution."""
import hashlib
import json
from pathlib import Path

import pytest
from pydantic import ValidationError

from veriforge.adapters import get_adapter
from veriforge.contracts import ArtifactReference, AttemptResult, Experiment, FailureSignature, canonical_hash
from veriforge.evidence import CandidateProvenance, CandidateSourceReview, CandidateValidation, compare_candidate_sources, validate_candidate

SOURCE = b"module fifo; endmodule\n"
CHECKER = b"# independent synthetic checker; never executed\n"


def sha(data):
    return hashlib.sha256(data).hexdigest()


def plan(fixture="fifo_reset_bug"):
    return Experiment(experiment_id="independent-candidate", fixture_id=fixture, objective_ids=("FIFO_BEHAVIOR",),
                      operations=({"op": "reset"}, {"op": "write", "value": 7}, {"op": "reset"}, {"op": "read"}))


def write(directory, experiment, *, failed=False):
    directory = Path(directory)
    directory.mkdir(parents=True)
    descriptor = get_adapter("rtl.fifo.v1")
    contract = descriptor.contract_for(experiment)
    contract = contract.model_copy(update={"checker": contract.checker.model_copy(update={"sha256": sha(CHECKER)})})
    files = {"experiment.json": experiment.model_dump_json().encode(), "fifo.sv": SOURCE,
             "checker.py": CHECKER, "contract.json": contract.model_dump_json().encode()}
    refs = []
    for index, (name, data) in enumerate(files.items()):
        (directory / name).write_bytes(data)
        refs.append(ArtifactReference(artifact_id=f"independent-{index}", path=name, sha256=sha(data), size_bytes=len(data), media_type="text/plain"))
    source_hash = sha(json.dumps(dict(source=sha(SOURCE), variant=descriptor.fixture_source(experiment.fixture_id)["variant"],
                                     parameters=experiment.parameters.model_dump()), sort_keys=True, separators=(",", ":")).encode())
    result = AttemptResult(attempt_id=directory.name, experiment_id=experiment.experiment_id, execution_status="completed",
        verdict="design_mismatch" if failed else "pass_within_scope", artifacts=tuple(refs),
        identity=dict(source_hash=source_hash, checker_hash=sha(CHECKER), contract_hash=canonical_hash(contract),
                      experiment_hash=canonical_hash(experiment), environment_hash="e" * 64),
        failure_signature=FailureSignature(requirement_id="FIFO_BEHAVIOR", failure_class="fifo.empty", first_cycle=3,
            expected="empty", observed="not empty", signature_hash="e" * 64) if failed else None,
        uncertainties=("Synthetic review; no simulator/checker process executed",))
    (directory / "result.json").write_text(result.model_dump_json())
    return result


@pytest.fixture
def snapshots(tmp_path):
    original, candidate = tmp_path / "original", tmp_path / "candidate"
    write(original, plan(), failed=True)
    write(candidate, plan("fifo_correct"))
    return original, candidate


def validate(original, output, candidate_runner=None, provenance=None):
    return validate_candidate(original, output, candidate_fixture_id="fifo_correct",
        original_runner=lambda experiment, directory: write(directory, experiment, failed=True),
        candidate_runner=candidate_runner or (lambda experiment, directory: write(directory, experiment)),
        neighboring_plans=(plan(),), regression_plans=(plan(),), provenance=provenance)


def test_same_hdl_has_configuration_diff_and_preserves_files(snapshots):
    before = {(str(path), sha(path.read_bytes())) for directory in snapshots for path in directory.iterdir()}
    review = compare_candidate_sources(*snapshots)
    assert not review.source_bytes_changed and review.configuration_changed
    assert review.original_source.file_sha256 == review.candidate_source.file_sha256
    assert review.unified_source_diff == "" and review.unified_configuration_diff
    assert review.provenance.authorship_claim == "curated_source_not_generated_patch"
    assert before == {(str(path), sha(path.read_bytes())) for directory in snapshots for path in directory.iterdir()}


@pytest.mark.parametrize("field,value", [
    ("source_bytes_changed", True), ("configuration_changed", False), ("adapter_id", "rtl.arithmetic.v1"),
])
def test_rehashed_review_cannot_launder_contradictory_source_or_family_claims(snapshots, tmp_path, field, value):
    record = validate(snapshots[0], tmp_path / "validation")
    payload = record.model_dump(mode="json")
    payload["source_review"][field] = value
    with pytest.raises(ValidationError):
        payload["source_review_sha256"] = canonical_hash(CandidateSourceReview.model_validate(payload["source_review"]))
        CandidateValidation.model_validate(payload)


def test_constructed_invalid_provenance_is_revalidated_before_read_or_run(tmp_path):
    # model_construct is a deliberate untrusted-input stand-in, not authority.
    forged = CandidateProvenance.model_construct(authorship_claim="model_authored_patch")
    with pytest.raises(ValueError):
        validate(tmp_path / "absent", tmp_path / "must-not-create", provenance=forged)
    assert not (tmp_path / "must-not-create").exists()


@pytest.mark.parametrize("which", ["checker.py", "contract.json"])
def test_complete_but_changed_protected_snapshot_cannot_pass(snapshots, tmp_path, which):
    def changed(experiment, directory):
        result = write(directory, experiment)
        data = b"# changed protected checker\n" if which == "checker.py" else b"{}"
        (directory / which).write_bytes(data)
        refs = tuple(ref.model_copy(update={"sha256": sha(data), "size_bytes": len(data)}) if ref.path == which else ref for ref in result.artifacts)
        result = result.model_copy(update={"artifacts": refs})
        (directory / "result.json").write_text(result.model_dump_json())
        return result
    result = validate(snapshots[0], tmp_path / "invalid", candidate_runner=changed)
    assert result.status == "failed_under_checks" and result.source_review_status == "invalid_source_snapshots"
    assert result.witness.verdict == "pass_within_scope" and result.source_review is None


def test_incomplete_legacy_evidence_stays_explicitly_unavailable(snapshots, tmp_path):
    original = snapshots[0]
    result = AttemptResult.model_validate_json((original / "result.json").read_text())
    result = result.model_copy(update={"artifacts": tuple(ref for ref in result.artifacts if ref.path != "checker.py")})
    (original / "result.json").write_text(result.model_dump_json())
    record = validate(original, tmp_path / "legacy")
    assert record.source_review_status == "missing_source_snapshots" and record.source_review is None
    assert record.source_review_sha256 is None


def test_link_resolving_source_outside_attempt_is_rejected(snapshots, tmp_path, monkeypatch):
    outside = tmp_path / "outside.sv"
    outside.write_bytes(SOURCE)
    original_resolve = Path.resolve
    candidate_source = snapshots[1] / "fifo.sv"
    def redirected(path, *args, **kwargs):
        return outside if path == candidate_source else original_resolve(path, *args, **kwargs)
    monkeypatch.setattr(Path, "resolve", redirected)
    with pytest.raises(ValueError, match="escapes"):
        compare_candidate_sources(*snapshots)


@pytest.mark.parametrize("data", [b"// MODEL_API_KEY=deliberately-fake\n", b"module fifo;\xff\n"])
def test_source_byte_screening_rejects_rehashed_secret_markers_and_invalid_utf8(snapshots, data):
    candidate = snapshots[1]
    result = AttemptResult.model_validate_json((candidate / "result.json").read_text())
    (candidate / "fifo.sv").write_bytes(data)
    refs = tuple(ref.model_copy(update={"sha256": sha(data), "size_bytes": len(data)}) if ref.path == "fifo.sv" else ref for ref in result.artifacts)
    (candidate / "result.json").write_text(result.model_copy(update={"artifacts": refs}).model_dump_json())
    with pytest.raises(ValueError, match="UTF-8|secret"):
        compare_candidate_sources(*snapshots)
