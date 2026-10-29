"""Prepared opt-in HDL gates. No live run belongs to VF-055B source delivery."""
import hashlib
import json
import os

import pytest

from veriforge.axi_lite import AxiLiteExperiment, AxiLiteTrace, check, reviewed_contract
from veriforge.axi_lite.container import run_container_experiment
from veriforge.axi_lite.runner import directed_experiment, witness_experiment
from veriforge.contracts import canonical_hash

pytestmark = pytest.mark.skipif(os.environ.get("VF_AXI_LITE_SIM_GATE") != "granted",
                                reason="requires a separate finite coordinator simulator gate")


@pytest.mark.parametrize("case,fixture,verdict,requirement", [
    ("directed", "axil_registers_correct", "pass_within_scope", None),
    ("order", "axil_registers_correct", "pass_within_scope", None),
    ("strobe", "axil_registers_correct", "pass_within_scope", None),
    ("response", "axil_registers_correct", "pass_within_scope", None),
    ("order", "axil_aw_w_order_bug", "property_failure", "AXIL_PROGRESS"),
    ("strobe", "axil_strobe_bug", "design_mismatch", "AXIL_READ"),
    ("response", "axil_response_stability_bug", "property_failure", "AXIL_HANDSHAKE"),
])
def test_live_curated_registers(tmp_path, case, fixture, verdict, requirement):
    experiment = directed_experiment(fixture) if case == "directed" else witness_experiment(case, fixture)
    payload = experiment.model_dump()
    payload["budget"]["wall_time_seconds"] = 120.0
    experiment = AxiLiteExperiment.model_validate(payload)
    result = run_container_experiment(experiment, tmp_path)
    assert result.execution_status == "completed", result.model_dump_json()
    assert result.verdict == verdict, result.model_dump_json()
    assert {t.name for t in result.tool_versions} >= {"verilator", "cocotb", "python", "gcc", "make"}
    assert result.identity.contract_hash == canonical_hash(reviewed_contract(experiment.parameters))
    assert result.identity.experiment_hash == canonical_hash(experiment)
    evidence = tmp_path / "evidence"
    assert json.loads((evidence / "stimulus.json").read_text()) == [r.model_dump() for r in experiment.cycles]
    observed = AxiLiteTrace.model_validate_json((evidence / "observed.json").read_text())
    checked = check(experiment, observed)
    assert checked.verdict == verdict and result.trace == ()
    assert result.usage.cycles == experiment.cycle_count == len(observed.observations)
    if requirement:
        assert result.failure_signature.requirement_id == requirement
        assert result.failure_signature.first_cycle == checked.failure.cycle
    for artifact in result.artifacts:
        path = tmp_path / artifact.path
        assert path.resolve().is_relative_to(tmp_path.resolve())
        assert path.stat().st_size == artifact.size_bytes
        assert hashlib.sha256(path.read_bytes()).hexdigest() == artifact.sha256
    assert result.identity.checker_hash == hashlib.sha256((evidence / "checker.py").read_bytes()).hexdigest()
