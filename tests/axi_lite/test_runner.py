"""Pure source/runner checks with fabricated observations, never simulator evidence."""
import hashlib
import importlib.util
import json
import sys
from threading import Event
from types import ModuleType, SimpleNamespace

import pytest
from pydantic import ValidationError

from veriforge.axi_lite import AxiLiteExperiment, AxiLiteObservation, AxiLiteTrace, check, reviewed_contract
from veriforge.axi_lite import runner
from veriforge.contracts import AttemptResult, ToolVersion, canonical_hash


def hand_trace(experiment, updates):
    return AxiLiteTrace(observations=tuple(AxiLiteObservation(cycle=i, **{
        "aw_ready": False, "w_ready": False, "b_valid": False,
        "ar_ready": False, "r_valid": False, **updates.get(i, {}),
    }) for i in range(experiment.cycle_count)))


def witness_rows(case):
    if case == "order":
        return {2: {"aw_ready": True}, 3: {"w_ready": True}, 4: {"b_valid": True},
                8: {"ar_ready": True}, 9: {"r_valid": True, "r_data": 0x11223344}}
    if case == "strobe":
        return {2: {"aw_ready": True, "w_ready": True}, 3: {"b_valid": True},
                4: {"aw_ready": True, "w_ready": True}, 5: {"b_valid": True},
                6: {"ar_ready": True}, 7: {"r_valid": True, "r_data": 0x11BB33DD}}
    return {2: {"aw_ready": True, "w_ready": True},
            3: {"b_valid": True}, 4: {"b_valid": True}, 5: {"b_valid": True}}


@pytest.mark.parametrize("case,fixture,cycle,requirement,failure_class", [
    ("order", "axil_aw_w_order_bug", 7, "AXIL_PROGRESS", "axil.response_bound"),
    ("strobe", "axil_strobe_bug", 7, "AXIL_READ", "axil.read_value"),
    ("response", "axil_response_stability_bug", 4, "AXIL_HANDSHAKE", "axil.stalled_response"),
])
def test_independent_hand_derived_witnesses(case, fixture, cycle, requirement, failure_class):
    experiment = runner.witness_experiment(case, fixture)
    rows = witness_rows(case)
    assert check(experiment, hand_trace(experiment, rows)).verdict == "pass_within_scope"
    if case == "order":
        rows.pop(4)  # RTL variant saves halves but only commits concurrent handshakes.
    elif case == "strobe":
        rows[7]["r_data"] = 0xAABBCCDD  # Full word replaces all lanes.
    else:
        rows.pop(4)  # BVALID dropped after the first stalled response row.
    failure = check(experiment, hand_trace(experiment, rows)).failure
    assert (failure.cycle, failure.requirement_id, failure.failure_class) == (cycle, requirement, failure_class)


@pytest.fixture
def fake_tools(monkeypatch, tmp_path):
    # Every caller using this fixture is a mocked runner test, never a live gate.
    monkeypatch.setattr(runner, "_tools", lambda: [ToolVersion(name="verilator", version="mock-not-executed")])
    monkeypatch.setattr(runner.tempfile, "tempdir", str(tmp_path))


def mock_job(monkeypatch, *, case="strobe", mutate=False, code=0, reason=None, built=True, raw=None):
    def execute(command, output, env, seconds, cancel_event, byte_limit):
        assert command[1:3] == ["-m", "veriforge.axi_lite.sim_job"]
        assert 0 < seconds <= 30
        assert "VF_SECRET" not in env
        job = json.loads((output / "job.json").read_text())
        assert job["parameters"] == {"MUTANT": runner.FIXTURES[experiment.fixture_id]}
        assert json.loads((output / "stimulus.json").read_text()) == [row.model_dump() for row in experiment.cycles]
        if built:
            (output / "built.marker").write_text("mock build marker; no tool execution")
        rows = witness_rows(case)
        if mutate:
            rows[7]["r_data"] = 0xAABBCCDD
        payload = hand_trace(experiment, rows).model_dump_json() if raw is None else raw
        (output / "observed.json").write_text(payload)
        return code, reason
    experiment = runner.witness_experiment(case)
    monkeypatch.setattr(runner, "_bounded", execute)
    return experiment


@pytest.mark.parametrize("mutate,verdict", [(False, "pass_within_scope"), (True, "design_mismatch")])
def test_mocked_runner_records_exact_identity_and_artifact_hashes(tmp_path, monkeypatch, fake_tools, mutate, verdict):
    monkeypatch.setenv("VF_SECRET", "never-forward")
    experiment = mock_job(monkeypatch, mutate=mutate)
    result = runner.run_experiment(experiment, tmp_path)
    assert result.execution_status == "completed" and result.verdict == verdict
    assert result.identity.contract_hash == canonical_hash(reviewed_contract(experiment.parameters))
    assert result.identity.experiment_hash == canonical_hash(experiment)
    assert result.identity.checker_hash == hashlib.sha256((tmp_path / "checker.py").read_bytes()).hexdigest()
    assert result.trace == () and result.usage.cycles == experiment.cycle_count
    assert result.tool_versions[0].version == "mock-not-executed"
    assert result.identity.environment_hash == hashlib.sha256((tmp_path / "environment.json").read_bytes()).hexdigest()
    assert json.loads((tmp_path / "check.json").read_text())["verdict"] == verdict
    for artifact in result.artifacts:
        path = tmp_path / artifact.path
        assert path.stat().st_size == artifact.size_bytes
        assert hashlib.sha256(path.read_bytes()).hexdigest() == artifact.sha256
    assert AttemptResult.model_validate_json((tmp_path / "result.json").read_text()) == result
    if mutate:
        assert result.failure_signature.requirement_id == "AXIL_READ"
        assert result.failure_signature.first_cycle == 7


@pytest.mark.parametrize("code,reason,built,raw,verdict,status", [
    (1, None, False, None, "build_error", "completed"),
    (1, None, True, None, "harness_error", "completed"),
    (0, "timeout", True, None, "timeout", "completed"),
    (0, "resource_limit", True, None, "resource_limit", "completed"),
    (0, "cancelled", True, None, "inconclusive", "cancelled"),
    (0, None, True, "not JSON", "harness_error", "completed"),
    (0, None, True, '{"observations":[]}', "harness_error", "completed"),
])
def test_mocked_outcomes_never_turn_harness_or_limits_into_dut_failure(tmp_path, monkeypatch, fake_tools,
                                                                    code, reason, built, raw, verdict, status):
    experiment = mock_job(monkeypatch, code=code, reason=reason, built=built, raw=raw)
    result = runner.run_experiment(experiment, tmp_path)
    assert (result.verdict, result.execution_status) == (verdict, status)
    assert result.failure_signature is None


def test_unsupported_cancelled_and_immutable_directory(tmp_path, monkeypatch):
    def missing():
        raise NotImplementedError("mock missing tools; no execution")
    monkeypatch.setattr(runner, "_tools", missing)
    experiment = runner.witness_experiment()
    result = runner.run_experiment(experiment, tmp_path / "unsupported")
    assert result.verdict == "unsupported" and result.identity is None
    event = Event(); event.set()
    result = runner.run_experiment(experiment, tmp_path / "cancelled", cancel_event=event)
    assert result.execution_status == "cancelled" and result.failure_signature is None
    with pytest.raises(ValueError, match="empty"):
        runner.run_experiment(experiment, tmp_path / "unsupported")


def test_curated_source_ids_and_directed_plan_are_bounded():
    source = (runner.ROOT / "targets/axi_lite/registers.sv").read_text()
    assert list(runner.FIXTURES.values()) == [0, 1, 2, 3]
    assert "MUTANT != 1" in source and "MUTANT == 2" in source and "MUTANT == 3" in source
    experiment = runner.directed_experiment()
    assert experiment.cycle_count < 4096
    assert {row.w_strb for row in experiment.cycles if row.w_valid} == set(range(16))
    with pytest.raises(ValidationError):
        AxiLiteExperiment.model_validate({**experiment.model_dump(), "fixture_id": "../../uploaded.sv"})


def test_artifact_cap_includes_report_and_cannot_leave_a_design_finding(tmp_path, monkeypatch, fake_tools):
    experiment = mock_job(monkeypatch, mutate=True)
    result = runner.run_experiment(experiment, tmp_path / "attempt")
    assert result.verdict == "design_mismatch"
    limited = runner.finalize_result(result, tmp_path / "attempt", 1024)
    assert limited.verdict == "resource_limit" and limited.failure_signature is None
    assert sum(p.stat().st_size for p in (tmp_path / "attempt").rglob("*") if p.is_file()) <= 1024


def test_dynamic_illegal_manager_is_harness_error(tmp_path, monkeypatch, fake_tools):
    experiment = runner.witness_experiment("order")
    def execute(command, output, *args):
        (output / "built.marker").write_text("mock only")
        # AW row not accepted, then exact submitted sequence withdraws AWVALID.
        (output / "observed.json").write_text(hand_trace(experiment, {}).model_dump_json())
        return 0, None
    monkeypatch.setattr(runner, "_bounded", execute)
    result = runner.run_experiment(experiment, tmp_path / "attempt")
    assert result.verdict == "harness_error" and result.failure_signature is None


def test_observation_driver_plays_exact_cycles_and_samples_before_edge(tmp_path, monkeypatch):
    """Fake scheduler tests Python driver sequence only, never HDL behavior."""
    events = []
    cocotb = ModuleType("cocotb")
    cocotb.test = lambda: lambda function: function
    triggers = ModuleType("cocotb.triggers")
    async def timer(duration, *, unit):
        events.append(("timer", duration, unit))
    async def readonly():
        events.append(("readonly",))
    triggers.Timer, triggers.ReadOnly = timer, readonly
    monkeypatch.setitem(sys.modules, "cocotb", cocotb)
    monkeypatch.setitem(sys.modules, "cocotb.triggers", triggers)
    path = runner.Path(runner.__file__).with_name("sim_test.py")
    spec = importlib.util.spec_from_file_location("observation_driver_under_test", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    class Signal:
        def __init__(self, name): self.name, self._value = name, 0
        @property
        def value(self):
            events.append(("read", self.name))
            return self._value
        @value.setter
        def value(self, value):
            self._value = value
            events.append(("write", self.name, value))
    dut = SimpleNamespace(**{name: Signal(name) for name in ("clk", *module.INPUTS, *module.BOOL_OUTPUTS, *module.INT_OUTPUTS)})
    experiment = runner.witness_experiment("strobe")
    stimulus = tmp_path / "stimulus.json"
    observed = tmp_path / "observed.json"
    stimulus.write_text(json.dumps([row.model_dump() for row in experiment.cycles]))
    monkeypatch.setenv("VF_STIMULUS", str(stimulus)); monkeypatch.setenv("VF_OBSERVED", str(observed))
    # Fake triggers complete synchronously; no Windows event-loop/socket needed.
    with pytest.raises(StopIteration):
        module.observe(dut).send(None)
    assert len(AxiLiteTrace.model_validate_json(observed.read_text()).observations) == experiment.cycle_count
    for i, row in enumerate(experiment.cycles):
        start = events.index(("write", "clk", 0))
        stop = events.index(("timer", 5, "ns"), start)
        assert events[start + 1:stop] == [("write", name, int(row.model_dump()[name])) for name in module.INPUTS]
        edge = events.index(("write", "clk", 1), stop)
        assert events[stop + 1] == ("readonly",)
        assert all(event[0] == "read" for event in events[stop + 2:edge - 1])
        assert events[edge - 1] == ("timer", 1, "ns")
        events = events[edge + 2:]
    assert events == []
