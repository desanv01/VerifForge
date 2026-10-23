"""Opt-in real HDL evidence workflow using the prebuilt, network-isolated image."""
import json
import os
from pathlib import Path

import pytest

from veriforge.contracts import Experiment
from veriforge.evidence import export_bundle, reduce_failure, replay_attempt, validate_candidate
from veriforge.rtl import run_container_experiment

pytestmark = pytest.mark.skipif(os.environ.get("VF_REAL_RTL") != "1", reason="opt-in real Docker simulator check")


def test_real_replay_reduce_export_and_curated_candidate(tmp_path):
    root = Path(__file__).resolve().parents[2]
    data = json.loads((root / "targets/fifo/directed.json").read_text())
    data["fixture_id"] = "fifo_reset_bug"
    data["budget"] = {"wall_time_seconds": 120.0}
    experiment = Experiment.model_validate(data)
    attempt = tmp_path / "actual-original"
    original = run_container_experiment(experiment, attempt)
    assert original.verdict == "design_mismatch", original.model_dump_json()
    replay = replay_attempt(attempt, tmp_path / "actual-replay", runner=run_container_experiment)
    assert replay.same_semantic_failure
    reduced = reduce_failure(attempt, tmp_path / "actual-reduction", runner=run_container_experiment, max_attempts=4)
    assert reduced.reduced_result.failure_signature.failure_class == original.failure_signature.failure_class
    assert reduced.reduced_experiment.cycle_count <= experiment.cycle_count
    bundle = export_bundle(attempt, tmp_path / "actual-export.zip")
    assert bundle.is_file()
    neighbor_data = experiment.model_dump(mode="json")
    neighbor_data["experiment_id"] = "actual-neighbor"
    neighbor_data["seed"] += 1
    # Perturb write values while preserving legal FIFO operations.
    for op in neighbor_data["operations"]:
        if op["op"] == "write":
            op["value"] = (op["value"] + 1) % (2 ** experiment.parameters.width)
    neighbor = Experiment.model_validate(neighbor_data)
    regression = Experiment.model_validate(dict(experiment_id="actual-regression", fixture_id="fifo_reset_bug",
        budget={"wall_time_seconds": 120.0},
        objective_ids=["FIFO_BEHAVIOR"], operations=[{"op": "reset"}, {"op": "write", "value": 23}, {"op": "read"}]))
    validated = validate_candidate(attempt, tmp_path / "actual-validation", candidate_fixture_id="fifo_correct",
        original_runner=run_container_experiment, candidate_runner=run_container_experiment,
        neighboring_plans=(neighbor,), regression_plans=(regression,))
    assert validated.status == "validated_under_checks", validated.model_dump_json()
