"""Independent workflow checks. Scripted callbacks never constitute HDL evidence."""
from hashlib import sha256
import json
from pathlib import Path
from zipfile import ZipFile

import pytest

from veriforge.contracts import (
    ArtifactReference, AttemptResult, EvidenceIdentity, Experiment, FailureSignature,
    ToolVersion, canonical_hash,
)
from veriforge.evidence import (
    ExportLimits, export_bundle, load_attempt, reduce_failure, replay_attempt,
    validate_candidate,
)

ORIGINAL_SOURCE = "1" * 64
CANDIDATE_SOURCE = "2" * 64
CHECKER = "3" * 64
CONTRACT = "4" * 64


def experiment(name="independent-witness", value=13):
    return Experiment.model_validate({
        "experiment_id": name, "fixture_id": "fifo_reset_bug", "seed": 91,
        "objective_ids": ["FIFO_BEHAVIOR"], "operations": [
            {"op": "reset", "cycles": 2}, {"op": "write", "value": value},
            {"op": "idle", "cycles": 3}, {"op": "reset"}, {"op": "read"}],
    })


def scripted(plan, output, *, verdict="design_mismatch", identity_changes=None,
             signature_changes=None):
    """Persist a labeled stub result; never imports or starts an RTL runner."""
    output = Path(output)
    output.mkdir(parents=True, exist_ok=False)
    identity = {
        "source_hash": ORIGINAL_SOURCE if plan.fixture_id == "fifo_reset_bug" else CANDIDATE_SOURCE,
        "checker_hash": CHECKER, "contract_hash": CONTRACT,
        "experiment_hash": canonical_hash(plan), "environment_hash": "5" * 64,
    }
    identity.update(identity_changes or {})
    signature = {
        "requirement_id": "FIFO_BEHAVIOR", "failure_class": "fifo.empty",
        "first_cycle": 2, "expected": "true", "observed": "false", "signature_hash": "6" * 64,
    }
    signature.update(signature_changes or {})
    files = {
        "experiment.json": plan.model_dump_json().encode(),
        "stimulus.json": json.dumps({"scripted_only": True, "seed": plan.seed}).encode(),
        "fifo.sv": b"// synthetic HDL fixture; never compiled\n",
        "checker.py": b"# scripted fixture; never executed\n",
        "contract.json": b'{"scripted_only": true}',
    }
    refs = []
    for index, (name, data) in enumerate(files.items()):
        (output / name).write_bytes(data)
        refs.append(ArtifactReference(artifact_id=f"independent-{index}", path=name,
            sha256=sha256(data).hexdigest(), size_bytes=len(data), media_type="text/plain"))
    result = AttemptResult(attempt_id=output.name, experiment_id=plan.experiment_id,
        execution_status="completed", verdict=verdict, identity=EvidenceIdentity(**identity),
        failure_signature=FailureSignature(**signature) if verdict == "design_mismatch" else None,
        artifacts=tuple(refs), tool_versions=(ToolVersion(name="independent-scripted-stub", version="1"),))
    (output / "result.json").write_text(result.model_dump_json(), encoding="utf-8")
    return result


@pytest.fixture
def witness(tmp_path):
    path = tmp_path / "original"
    return path, scripted(experiment(), path)


def snapshot(path):
    return {p.relative_to(path).as_posix(): p.read_bytes() for p in path.rglob("*") if p.is_file()}


def test_export_recovery_verifies_bytes_and_submits_exact_declared_plan(witness, tmp_path):
    original, result = witness
    before = snapshot(original)
    archive_path = export_bundle(original, tmp_path / "report.zip")
    with ZipFile(archive_path) as archive:
        manifest = json.loads(archive.read("manifest.json"))
        recovered = Experiment.model_validate_json(archive.read("experiment.json"))
        assert canonical_hash(recovered) == result.identity.experiment_hash
        for ref in manifest["artifacts"]:
            payload = archive.read("artifacts/" + ref["path"])
            assert sha256(payload).hexdigest() == ref["sha256"]
            assert len(payload) == ref["size_bytes"]
            assert payload == (original / ref["path"]).read_bytes()
        assert "matching curated RTL runner" in archive.read("REPORT.txt").decode()
        assert "result.json" not in archive.namelist()  # Portable report, not an extracted attempt.
    # This is a test of manual typed-plan recovery, using a matching stub callback.
    # It does not execute checker.py/HDL from the archive or claim a simulator run.
    replayed = scripted(recovered, tmp_path / "recovered-plan")
    assert replayed.identity == result.identity
    assert recovered == experiment()
    assert before == snapshot(original)


@pytest.mark.parametrize("replacement", [b"x", b"x" * 2000])
def test_artifact_size_tampering_rejected_without_mutation(witness, replacement):
    original, _ = witness
    (original / "stimulus.json").write_bytes(replacement)
    before = snapshot(original)
    with pytest.raises(ValueError, match="hash or size|byte limit"):
        load_attempt(original)
    assert snapshot(original) == before


def test_traversal_artifact_rejected_before_external_read(witness, tmp_path):
    original, _ = witness
    sentinel = tmp_path / "outside.txt"
    sentinel.write_bytes(b"outside remains private")
    data = json.loads((original / "result.json").read_text())
    data["artifacts"][0]["path"] = "../outside.txt"
    (original / "result.json").write_text(json.dumps(data))
    with pytest.raises(ValueError, match="snapshot"):
        load_attempt(original)
    assert sentinel.read_bytes() == b"outside remains private"


def test_export_budget_failure_and_existing_destination_preserve_all_bytes(witness, tmp_path):
    original, _ = witness
    before = snapshot(original)
    blocked = tmp_path / "limited.zip"
    with pytest.raises(ValueError, match="file or byte"):
        export_bundle(original, blocked, limits=ExportLimits(max_files=5))
    assert not blocked.exists()
    existing = tmp_path / "existing.zip"
    existing.write_bytes(b"preexisting export")
    with pytest.raises(FileExistsError):
        export_bundle(original, existing)
    assert existing.read_bytes() == b"preexisting export"
    assert snapshot(original) == before


@pytest.mark.parametrize("identity_field", ["checker_hash", "contract_hash", "source_hash"])
def test_reducer_never_accepts_failure_under_changed_identity(witness, tmp_path, identity_field):
    original, original_result = witness
    before = snapshot(original)
    def changed(plan, output):
        return scripted(plan, output, identity_changes={identity_field: "9" * 64})
    record = reduce_failure(original, tmp_path / "reduction", runner=changed, max_attempts=3)
    assert len(record.attempts) == 3
    assert record.reduced_experiment == experiment()
    assert record.reduced_result == original_result
    assert not record.improved
    assert before == snapshot(original)


def test_reduction_preserves_requirement_class_but_allows_failure_cycle_to_move(witness, tmp_path):
    original, _ = witness
    def moved_cycle(plan, output):
        return scripted(plan, output, signature_changes={"first_cycle": 0})
    record = reduce_failure(original, tmp_path / "reduction", runner=moved_cycle, max_attempts=3)
    assert record.improved
    assert record.reduced_result.failure_signature.first_cycle == 0
    assert record.reduced_result.failure_signature.failure_class == "fifo.empty"
    assert record.reduced_result.failure_signature.requirement_id == "FIFO_BEHAVIOR"
    assert all(p.contract_id == experiment().contract_id and p.budget == experiment().budget
               and p.seed == 91 and p.fixture_id == "fifo_reset_bug" for p in record.submitted_experiments)


def candidate_options():
    return dict(candidate_fixture_id="fifo_correct", original_runner=scripted,
        neighboring_plans=(experiment("independent-neighbor", 14),),
        regression_plans=(experiment("independent-regression", 15),))


def test_candidate_runs_original_witness_then_candidate_witness_and_distinct_suites(witness, tmp_path):
    original, _ = witness
    before = snapshot(original)
    calls = []
    def reproduce(plan, output):
        calls.append(("original", plan))
        return scripted(plan, output)
    def candidate(plan, output):
        calls.append(("candidate", plan))
        return scripted(plan, output, verdict="pass_within_scope")
    options = candidate_options()
    options["original_runner"] = reproduce
    record = validate_candidate(original, tmp_path / "validation", candidate_runner=candidate, **options)
    assert record.status == "validated_under_checks"
    assert [kind for kind, _ in calls] == ["original", "candidate", "candidate", "candidate"]
    assert calls[0][1] == experiment()
    assert [p.experiment_id for _, p in calls[1:]] == [
        "independent-witness", "independent-neighbor", "independent-regression"]
    expected = experiment().model_dump(mode="json")
    expected["fixture_id"] = "fifo_correct"
    assert calls[1][1].model_dump(mode="json") == expected
    assert all(r.identity.checker_hash == CHECKER and r.identity.contract_hash == CONTRACT
               for r in (record.witness, *record.neighbors, *record.regressions))
    assert record.execution_basis == "supplied_runner_evidence"
    assert snapshot(original) == before


@pytest.mark.parametrize("wrong", ["contract_hash", "checker_hash", "source_hash"])
def test_last_candidate_regression_identity_mismatch_invalidates_all_checks(witness, tmp_path, wrong):
    calls = 0
    def candidate(plan, output):
        nonlocal calls
        calls += 1
        changes = {wrong: "9" * 64} if calls == 3 else None
        return scripted(plan, output, verdict="pass_within_scope", identity_changes=changes)
    record = validate_candidate(witness[0], tmp_path / "validation", candidate_runner=candidate, **candidate_options())
    assert calls == 3
    assert record.status == "failed_under_checks"
    assert record.witness.verdict == "pass_within_scope"


def test_failed_original_reproduction_prevents_candidate_execution(witness, tmp_path):
    def no_candidate(*args):
        pytest.fail("candidate ran before original reproduction")
    def changed_original(plan, output):
        return scripted(plan, output, signature_changes={"requirement_id": "OTHER_REQUIREMENT"})
    options = candidate_options()
    options["original_runner"] = changed_original
    record = validate_candidate(witness[0], tmp_path / "validation", candidate_runner=no_candidate, **options)
    assert record.status == "original_not_reproduced"
    assert record.witness is None


def test_replay_uses_stored_seed_operations_and_budget_without_mutating_original(witness, tmp_path):
    original, _ = witness
    before = snapshot(original)
    seen = []
    def runner(plan, output):
        seen.append(plan)
        return scripted(plan, output)
    record = replay_attempt(original, tmp_path / "replay", runner=runner)
    assert seen == [experiment()]
    assert record.same_semantic_failure
    assert before == snapshot(original)
