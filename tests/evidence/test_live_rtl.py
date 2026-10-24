"""Opt-in real HDL evidence workflow using the prebuilt, network-isolated image."""
import json
import os
from pathlib import Path

import pytest

from veriforge.contracts import Experiment
from veriforge.evidence import export_bundle, load_attempt, reduce_failure, replay_attempt, validate_candidate
from veriforge.rtl import run_container_experiment

pytestmark = pytest.mark.skipif(os.environ.get("VF_REAL_RTL") != "1", reason="opt-in real Docker simulator check")


def test_real_replay_reduce_export_and_curated_candidate(tmp_path):
    root = Path(__file__).resolve().parents[2]
    accepted = os.environ.get("VF_EVIDENCE_ORIGINAL")
    if accepted:
        # Accepted originals are read/verified, never copied over or mutated.
        attempt = Path(accepted)
        experiment, original = load_attempt(attempt)
    else:
        data = json.loads((root / "targets/fifo/directed.json").read_text())
        data["fixture_id"] = "fifo_reset_bug"
        data["budget"] = {"wall_time_seconds": 120.0}
        experiment = Experiment.model_validate(data)
        attempt = tmp_path / "actual-original"
        original = run_container_experiment(experiment, attempt)
    assert original.verdict == "design_mismatch", original.model_dump_json()
    replay = replay_attempt(attempt, tmp_path / "actual-replay", runner=run_container_experiment)
    assert replay.same_semantic_failure
    assert load_attempt(tmp_path / "actual-replay") == (experiment, replay.replayed)
    reduced = reduce_failure(attempt, tmp_path / "actual-reduction", runner=run_container_experiment, max_attempts=2)
    assert len(reduced.attempts) <= 2
    assert reduced.reduced_result.failure_signature.failure_class == original.failure_signature.failure_class
    assert reduced.reduced_experiment.cycle_count <= experiment.cycle_count
    for index, (submitted, result) in enumerate(zip(reduced.submitted_experiments, reduced.attempts)):
        assert load_attempt(tmp_path / "actual-reduction" / f"attempt-{index:04d}") == (submitted, result)
    bundle = export_bundle(attempt, tmp_path / "actual-export.zip")
    assert bundle.is_file()
    neighbor_data = experiment.model_dump(mode="json")
    neighbor_data["experiment_id"] = "actual-neighbor"
    neighbor_data["seed"] = (neighbor_data["seed"] + 1) % (2 ** 32)
    # Perturb write values while preserving legal FIFO operations.
    for op in neighbor_data["operations"]:
        if op["op"] == "write":
            op["value"] = (op["value"] + 1) % (2 ** experiment.parameters.width)
    neighbor_data["operations"].append({"op": "idle", "cycles": 2})
    neighbor = Experiment.model_validate(neighbor_data)
    # Exercise full/empty boundaries, simultaneous traffic and complete drain;
    # distinct from both the original reset witness and its neighboring values.
    depth = experiment.parameters.depth
    values = [value % (2 ** experiment.parameters.width) for value in range(depth)]
    regression_operations = ([{"op": "reset"}]
        + [{"op": "write", "value": value} for value in values]
        + [{"op": "cycle", "write": True, "read": True, "value": 1}, {"op": "read"}]
        + [{"op": "read"} for _ in range(depth)] + [{"op": "idle", "cycles": 2}])
    regression = Experiment.model_validate(dict(experiment_id="actual-regression", fixture_id="fifo_reset_bug",
        budget=experiment.budget.model_dump(mode="json"), parameters=experiment.parameters.model_dump(mode="json"),
        objective_ids=["FIFO_BEHAVIOR"], operations=regression_operations))
    assert len({json.dumps(p.model_dump(mode="json")["operations"], sort_keys=True)
                for p in (experiment, neighbor, regression)}) == 3
    validated = validate_candidate(attempt, tmp_path / "actual-validation", candidate_fixture_id="fifo_correct",
        original_runner=run_container_experiment, candidate_runner=run_container_experiment,
        neighboring_plans=(neighbor,), regression_plans=(regression,))
    assert validated.status == "validated_under_checks", validated.model_dump_json()
    assert load_attempt(tmp_path / "actual-validation" / "original-replay") == (experiment, validated.original_replay)
    for index, (submitted, result) in enumerate(zip(validated.candidate_experiments,
            (validated.witness, *validated.neighbors, *validated.regressions))):
        assert load_attempt(tmp_path / "actual-validation" / f"candidate-{index:03d}") == (submitted, result)
    assert load_attempt(attempt) == (experiment, original)
