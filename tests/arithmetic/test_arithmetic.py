import json
from pathlib import Path
import pytest
from pydantic import ValidationError
from veriforge.contracts import Experiment, canonical_hash
from veriforge.arithmetic import ArithmeticExperiment, Parameters, directed_experiment, reviewed_contract
from veriforge.arithmetic import oracle, runner


def test_hand_derived_edges_bubbles_and_reset():
    # At 8 bits: 255*255=(256-1)^2=65536-512+1=65025.
    stimulus = [
        (True, False, 0, 0), (False, True, 255, 255),
        (False, True, 0, 255), (False, False, 0, 0),
        (False, False, 0, 0), (False, True, 128, 2),
        (True, True, 17, 19), (False, False, 0, 0),
        (False, False, 0, 0), (False, True, 1, 255),
        (False, False, 0, 0), (False, False, 0, 0)]
    rows = oracle.expected_trace([dict(reset=r, valid=v, a=a, b=b, operation_index=i)
                                  for i, (r, v, a, b) in enumerate(stimulus)])
    assert [(row["cycle"], row["product"]) for row in rows if row["valid"]] == [(3, 65025), (4, 0), (11, 255)]
    assert rows[6]["product"] == 0
    assert not rows[7]["valid"] and not rows[8]["valid"]


@pytest.mark.parametrize("width,maximum,product", [(1, 1, 1), (8, 255, 65025), (16, 65535, 4294836225), (32, 4294967295, 18446744065119617025)])
def test_hand_derived_maximum_widths(width, maximum, product):
    experiment = ArithmeticExperiment(experiment_id="max", parameters=Parameters(width=width), operations=(
        {"op": "reset"}, {"op": "multiply", "a": maximum, "b": maximum}, {"op": "idle", "cycles": 2}))
    assert oracle.expected_trace(oracle.expand(experiment))[-1]["product"] == product


def observations(expected):
    return [{"cycle": row["cycle"], "valid": row["valid"], "product": row["product"] or 0} for row in expected]


def test_checker_exact_latency_and_product_and_invalid_data():
    expected = oracle.expected_trace(oracle.expand(directed_experiment()))
    got = observations(expected)
    assert oracle.compare(expected, got) is None
    got[2]["valid"] = True
    assert oracle.compare(expected, got)["requirement"] == "ARITH_LATENCY"
    got = observations(expected)
    got[4]["product"] += 1
    assert oracle.compare(expected, got)["requirement"] == "ARITH_PRODUCT"
    got = observations(expected)
    got[0]["product"] = 1
    assert oracle.compare(expected, got)["requirement"] == "ARITH_RESET"
    got = observations(expected)
    got[1]["product"] = 99  # Invalid-cycle data is unspecified.
    assert oracle.compare(expected, got) is None


@pytest.mark.parametrize("damage", ["short", "order", "bool", "missing", "late_malformed"])
def test_malformed_trace_is_harness_failure(damage):
    expected = oracle.expected_trace(oracle.expand(directed_experiment()))
    got = observations(expected)
    if damage == "short": got.pop()
    if damage == "order": got[0]["cycle"] = 1
    if damage == "bool": got[0]["valid"] = 0
    if damage == "missing": del got[0]["product"]
    if damage == "late_malformed":
        got[0]["valid"] = True
        got[-1]["product"] = "bad"
    with pytest.raises(ValueError): oracle.compare(expected, got)


@pytest.mark.parametrize("updates", [
    {"schema_version": "1"}, {"target": "fifo"}, {"parameters": {"width": 0}},
    {"parameters": {"latency": 3}}, {"objective_ids": []},
    {"operations": [{"op": "multiply", "a": 1, "b": 2}]},
    {"operations": [{"op": "reset"}, {"op": "multiply", "a": 256, "b": 2}, {"op": "idle", "cycles": 2}]},
    {"operations": [{"op": "reset"}, {"op": "multiply", "a": 1, "b": 2}]},
    {"operations": [{"op": "reset"}, {"op": "multiply", "a": True, "b": 2}, {"op": "idle", "cycles": 2}]},
    {"budget": {"max_cycles": 1}},
])
def test_reject_invalid_experiments(updates):
    payload = directed_experiment().model_dump(mode="json")
    payload.update(updates)
    with pytest.raises(ValidationError): ArithmeticExperiment.model_validate(payload)


def test_separate_schema_and_canonical_contract():
    with pytest.raises(ValidationError): Experiment.model_validate(directed_experiment().model_dump())
    assert reviewed_contract().target == "arithmetic"
    assert canonical_hash(reviewed_contract()) == canonical_hash(reviewed_contract(Parameters(width=8)))
    assert canonical_hash(reviewed_contract()) != canonical_hash(reviewed_contract(Parameters(width=16)))
    with pytest.raises(ValidationError): directed_experiment().parameters.width = 9


def test_native_unsupported_never_claims_functional_result(tmp_path, monkeypatch):
    monkeypatch.setattr(runner.platform, "system", lambda: "Windows")
    result = runner.run_experiment(directed_experiment(), tmp_path)
    assert result.verdict == "unsupported" and result.failure_signature is None
    assert result.identity is None and result.usage.cycles == 0
    assert json.loads((tmp_path / "contract.json").read_text())["schema_version"] == "arithmetic.contract.v1"
    assert {a.path for a in result.artifacts} >= {"checker.py", "multiplier.sv", "stimulus.json", "contract.json"}
    with pytest.raises(ValueError): runner.run_experiment(directed_experiment(), tmp_path)


def test_driver_has_no_checker_dependency():
    import ast
    driver = Path(runner.__file__).with_name("sim_test.py").read_text()
    imports = [node for node in ast.walk(ast.parse(driver)) if isinstance(node, (ast.Import, ast.ImportFrom))]
    assert all("oracle" not in ast.unparse(node) for node in imports)
    assert "expected_trace(" not in driver


@pytest.mark.parametrize("mode,verdict,status", [
    ("pass", "pass_within_scope", "completed"), ("data", "design_mismatch", "completed"),
    ("malformed", "harness_error", "infrastructure_error"), ("timeout", "timeout", "completed"),
    ("cancelled", "inconclusive", "cancelled"), ("build", "build_error", "completed"),
    ("driver", "harness_error", "completed"),
    ("pass_limited", "resource_limit", "completed"), ("data_limited", "resource_limit", "completed"),
])
def test_runner_result_mapping_without_simulator(tmp_path, monkeypatch, mode, verdict, status):
    from types import SimpleNamespace
    monkeypatch.setattr(runner.tempfile, "tempdir", str(tmp_path))
    monkeypatch.setattr(runner.platform, "system", lambda: "Linux")
    monkeypatch.setattr(runner.shutil, "which", lambda _: "/tool")
    monkeypatch.setattr(runner.importlib.metadata, "version", lambda _: "2.0.1")
    monkeypatch.setattr(runner.subprocess, "run", lambda command, **kw: SimpleNamespace(stdout="Verilator 5.042" if command[0] == "verilator" else "tool 1"))
    def fake_bounded(command, directory, env, seconds, cancel_event, byte_limit):
        if mode in ("timeout", "cancelled"): return -9, mode
        if mode == "build": return 1, None
        (directory / "built.marker").write_text("mock build only")
        if mode == "driver": return 1, None
        got = observations(oracle.expected_trace(json.loads((directory / "stimulus.json").read_text())))
        if mode in ("data", "data_limited"): got[4]["product"] += 1
        if mode == "malformed": got[-1]["valid"] = "bad"
        (directory / "observed.json").write_text(json.dumps(got))
        return 0, None
    monkeypatch.setattr(runner, "_bounded", fake_bounded)
    payload = directed_experiment().model_dump()
    if mode.endswith("_limited"):
        payload["budget"]["max_artifact_bytes"] = 1024
    result = runner.run_experiment(ArithmeticExperiment.model_validate(payload), tmp_path)
    assert result.verdict == verdict and result.execution_status == status
    assert result.identity.contract_hash == canonical_hash(reviewed_contract())
    assert bool(result.failure_signature) == (mode == "data")
    assert not result.trace  # Arithmetic valid is never mislabeled as FIFO flags.
    assert json.loads((tmp_path / "result.json").read_text()) == result.model_dump(mode="json")
    if mode.endswith("_limited"):
        assert any("Final report/evidence exceeded" in note for note in result.uncertainties)
        assert (tmp_path / "arithmetic-trace.json").exists()
