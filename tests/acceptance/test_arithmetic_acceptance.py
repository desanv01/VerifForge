"""Hand-derived arithmetic acceptance. No simulator or Docker process is started."""

import json
from types import SimpleNamespace

import pytest
from pydantic import ValidationError

from veriforge.arithmetic import ArithmeticExperiment, Parameters, reviewed_contract
from veriforge.arithmetic import oracle, runner
from veriforge.contracts import AttemptResult, Experiment, canonical_hash


def plan(operations, **changes):
    values = dict(experiment_id="arithmetic-independent", operations=operations)
    values.update(changes)
    return ArithmeticExperiment(**values)


def expected(operations):
    return oracle.expected_trace(oracle.expand(plan(operations)))


def test_two_edge_offset_with_back_to_back_and_bubbles():
    rows = expected([
        {"op": "reset"},
        {"op": "multiply", "a": 2, "b": 3},
        {"op": "multiply", "a": 3, "b": 4},
        {"op": "multiply", "a": 4, "b": 5},
        {"op": "idle", "cycles": 3},
    ])
    assert [r["valid"] for r in rows] == [False, False, False, True, True, True, False]
    assert [r["product"] for r in rows] == [0, None, None, 6, 12, 20, None]
    assert [r["operation_index"] for r in rows] == [0, 1, 2, 3, 4, 4, 4]


def test_reset_on_due_edge_cancels_output_and_simultaneous_new_input():
    rows = expected([
        {"op": "reset"}, {"op": "multiply", "a": 7, "b": 9},
        {"op": "multiply", "a": 11, "b": 13},
        {"op": "cycle", "reset": True, "valid": True, "a": 17, "b": 19},
        {"op": "idle", "cycles": 3},
        {"op": "multiply", "a": 23, "b": 29}, {"op": "idle", "cycles": 2},
    ])
    assert rows[3]["reset"] and rows[3]["product"] == 0 and not rows[3]["valid"]
    assert [(r["cycle"], r["product"]) for r in rows if r["valid"]] == [(9, 667)]


@pytest.mark.parametrize("a,b,product", [
    (0, 4294967295, 0), (1, 4294967295, 4294967295),
    (32768, 2, 65536), (65535, 65535, 4294836225),
    (4294967295, 4294967295, 18446744065119617025),
])
def test_full_unsigned_products_from_hand_derived_constants(a, b, product):
    experiment = plan([{"op": "reset"}, {"op": "multiply", "a": a, "b": b}, {"op": "idle", "cycles": 2}],
                      parameters=Parameters(width=32))
    trace = oracle.expected_trace(oracle.expand(experiment))
    assert trace[1]["valid"] is False and trace[2]["valid"] is False
    assert trace[3]["valid"] is True and trace[3]["product"] == product


@pytest.mark.parametrize("tail,accepted", [
    ([{"op": "idle"}], False),
    ([{"op": "cycle"}], False),
    ([{"op": "idle", "cycles": 2}], True),
    ([{"op": "cycle"}, {"op": "cycle"}], True),
    ([{"op": "reset"}], True),
    ([{"op": "cycle", "reset": True, "valid": True, "a": 1, "b": 1}], True),
    ([{"op": "idle"}, {"op": "multiply", "a": 1, "b": 1}, {"op": "idle"}], False),
])
def test_tail_must_observe_or_cancel_the_latest_input(tail, accepted):
    operations = [{"op": "reset"}, {"op": "multiply", "a": 3, "b": 5}, *tail]
    if accepted:
        plan(operations)
    else:
        with pytest.raises(ValidationError):
            plan(operations)


def test_distinct_hand_written_latency_and_product_witnesses():
    # Expected input at edge 1 is due at 3: 255*2=510. These are hand-written
    # traces of intended mutant transformations, not executions of their HDL.
    want = expected([{"op": "reset"}, {"op": "multiply", "a": 255, "b": 2}, {"op": "idle", "cycles": 2}])
    correct = [dict(cycle=0, valid=False, product=0), dict(cycle=1, valid=False, product=0),
               dict(cycle=2, valid=False, product=0), dict(cycle=3, valid=True, product=510)]
    assert oracle.compare(want, correct) is None
    early = [dict(row) for row in correct]
    early[2].update(valid=True, product=510)
    early[3].update(valid=False, product=0)
    assert oracle.compare(want, early) == dict(cycle=2, signal="valid", requirement="ARITH_LATENCY", expected=False, observed=True)
    truncated = [dict(row) for row in correct]
    truncated[3]["product"] = 254
    assert oracle.compare(want, truncated) == dict(cycle=3, signal="product", requirement="ARITH_PRODUCT", expected=510, observed=254)


def test_reset_data_checked_but_bubble_data_unspecified():
    want = expected([{"op": "reset"}, {"op": "idle"}])
    observations = [dict(cycle=0, valid=False, product=0), dict(cycle=1, valid=False, product=65535)]
    assert oracle.compare(want, observations) is None
    observations[0]["product"] = 1
    assert oracle.compare(want, observations)["requirement"] == "ARITH_RESET"


def test_malformed_final_observation_preempts_earlier_design_mismatch():
    want = expected([{"op": "reset"}, {"op": "idle"}])
    with pytest.raises(ValueError, match="malformed"):
        oracle.compare(want, [dict(cycle=0, valid=True, product=1), dict(cycle=1, valid=False, product=True)])


def test_family_schema_and_common_result_remain_distinct():
    experiment = plan([{"op": "reset"}])
    assert ArithmeticExperiment.model_validate_json(experiment.model_dump_json()) == experiment
    with pytest.raises(ValidationError):
        Experiment.model_validate_json(experiment.model_dump_json())
    outcome = AttemptResult(attempt_id="unsupported-test", experiment_id=experiment.experiment_id,
                            execution_status="completed", verdict="unsupported")
    assert AttemptResult.model_validate_json(outcome.model_dump_json()) == outcome
    assert canonical_hash(reviewed_contract(Parameters(width=8))) != canonical_hash(reviewed_contract(Parameters(width=16)))


@pytest.mark.parametrize("mismatch", [False, True])
def test_final_trace_and_report_bytes_cannot_receive_a_design_verdict_over_budget(tmp_path, monkeypatch, mismatch):
    """A mocked fast child stays under cap; parent-generated evidence exceeds it."""
    monkeypatch.setattr(runner.tempfile, "tempdir", str(tmp_path))
    monkeypatch.setattr(runner.platform, "system", lambda: "Linux")
    monkeypatch.setattr(runner.shutil, "which", lambda _: "/fake-tool")
    monkeypatch.setattr(runner.importlib.metadata, "version", lambda _: "2.0.1")
    monkeypatch.setattr(runner.subprocess, "run", lambda command, **kwargs: SimpleNamespace(
        stdout="Verilator 5.042" if command[0] == "verilator" else "fake-tool 1"))

    def fake_child(command, directory, env, seconds, cancel_event, byte_limit):
        observations = [dict(cycle=i, valid=i == 3, product=65025 if i == 3 else 0) for i in range(1002)]
        if mismatch:
            observations[3]["product"] = 1
        (directory / "observed.json").write_text(json.dumps(observations))
        assert sum(p.stat().st_size for p in directory.rglob("*") if p.is_file()) <= byte_limit
        return 0, None

    monkeypatch.setattr(runner, "_bounded", fake_child)
    experiment = plan([{"op": "reset"}, {"op": "multiply", "a": 255, "b": 255}, {"op": "idle", "cycles": 1000}],
                      budget={"max_artifact_bytes": 200000})
    result = runner.run_experiment(experiment, tmp_path / "attempt")
    assert sum(p.stat().st_size for p in (tmp_path / "attempt").rglob("*") if p.is_file()) > 200000
    assert result.verdict == "resource_limit"
    assert result.failure_signature is None
    assert AttemptResult.model_validate_json((tmp_path / "attempt/result.json").read_text()) == result
