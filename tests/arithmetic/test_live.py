"""Opt-in serialized acceptance. Never enabled by the ordinary pure suite."""
import os
import hashlib
import json
from pathlib import Path
import pytest
from veriforge.arithmetic import ArithmeticExperiment, directed_experiment, reviewed_contract
from veriforge.arithmetic.container import run_container_experiment
from veriforge.arithmetic import oracle
from veriforge.contracts import AttemptResult, canonical_hash

pytestmark = pytest.mark.skipif(os.environ.get("VF_ARITHMETIC_SIM_GATE") != "granted", reason="requires coordinator simulator gate")


@pytest.mark.parametrize("fixture,verdict,requirement", [
    ("multiplier_correct", "pass_within_scope", None),
    ("multiplier_latency_bug", "design_mismatch", "ARITH_LATENCY"),
    ("multiplier_data_bug", "design_mismatch", "ARITH_PRODUCT"),
])
def test_live_curated_multiplier(tmp_path, fixture, verdict, requirement):
    # Cold C++ compilation exceeded the ordinary 30s experiment default on this host.
    payload = directed_experiment(fixture).model_dump()
    payload["budget"]["wall_time_seconds"] = 120.0
    experiment = ArithmeticExperiment.model_validate(payload)
    result = run_container_experiment(experiment, tmp_path)
    assert result.execution_status == "completed", result.model_dump_json()
    assert result.verdict == verdict, result.model_dump_json()
    assert result.identity is not None
    assert result.usage.cycles > 0
    assert {tool.name for tool in result.tool_versions} >= {"verilator", "cocotb"}
    if requirement: assert result.failure_signature.requirement_id == requirement
    assert AttemptResult.model_validate_json((tmp_path / "result.json").read_text()) == result
    assert result.identity.contract_hash == canonical_hash(reviewed_contract(experiment.parameters))
    assert result.identity.experiment_hash == canonical_hash(experiment)
    assert experiment.parameters.width == 8 and experiment.parameters.latency == 2
    for artifact in result.artifacts:
        path = (tmp_path / artifact.path).resolve()
        assert path.is_relative_to(tmp_path.resolve())
        assert path.stat().st_size == artifact.size_bytes
        assert hashlib.sha256(path.read_bytes()).hexdigest() == artifact.sha256
    assert result.identity.checker_hash == hashlib.sha256(Path(oracle.__file__).read_bytes()).hexdigest()
    assert json.loads((tmp_path / "stimulus.json").read_text()) == oracle.expand(experiment)
    trace = json.loads((tmp_path / "arithmetic-trace.json").read_text())
    assert len(trace["observed"]) == experiment.cycle_count == result.usage.cycles
    difference = oracle.compare(oracle.expected_trace(oracle.expand(experiment)), trace["observed"])
    assert (difference is None) == (verdict == "pass_within_scope")
    if difference:
        assert difference["requirement"] == requirement
        assert difference["cycle"] == result.failure_signature.first_cycle
