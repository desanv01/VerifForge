"""Controlled runners/child processes only. No simulator, Docker, or live model runs."""

import asyncio
import ctypes
import json
import os
from pathlib import Path
import sys
import threading

import pytest

from veriforge.contracts import (
    AttemptResult, EvidenceIdentity, ExecutionStatus, Experiment, FifoParameters,
    ResourceUsage, Verdict, canonical_hash,
)
from veriforge.planner import (
    InvestigationLimits, LocalRunnerExecutor, LocalWorkspace, MetaConfig, MetaProvider,
    ModelUsage, ProviderCompletion, curated_fifo_inputs, run_local_investigation,
)
from veriforge.planner.__main__ import main


def experiment():
    return Experiment(experiment_id="controlled-1", fixture_id="fifo_correct",
                      objective_ids=("FIFO_BEHAVIOR",),
                      operations=({"op": "reset"}, {"op": "write", "value": 7}, {"op": "read"}))


def fake_checked_result(plan):
    revision, contract = curated_fifo_inputs(plan.fixture_id, plan.parameters)
    return AttemptResult(
        attempt_id="explicit-controlled-runner", experiment_id=plan.experiment_id,
        execution_status=ExecutionStatus.COMPLETED, verdict=Verdict.PASS_WITHIN_SCOPE,
        identity=EvidenceIdentity(source_hash=revision.source_hash, checker_hash=contract.checker.sha256,
                                  contract_hash=canonical_hash(contract), experiment_hash=canonical_hash(plan),
                                  environment_hash="a" * 64),
        usage=ResourceUsage(cycles=plan.cycle_count), requirement_coverage=("FIFO_BEHAVIOR",),
    )


def fake_runner(plan, output_dir, *, cancel_event):
    assert not any(output_dir.iterdir())
    result = fake_checked_result(plan)
    (output_dir / "experiment.json").write_text(plan.model_dump_json())
    (output_dir / "result.json").write_text(result.model_dump_json())
    return result


def workspace(parent):
    revision, contract = curated_fifo_inputs("fifo_correct")
    return LocalWorkspace(parent, revision=revision, contract=contract,
                          limits=InvestigationLimits(), pricing=None, model="explicit-fake-model")


class ExplicitFakeProvider:
    model = "explicit-fake-model"

    def __init__(self):
        self.contexts = []

    async def complete(self, context, **kwargs):
        self.contexts.append(context)
        return ProviderCompletion(
            model=self.model, usage=ModelUsage(input_tokens=10, output_tokens=20), finish_reason="stop",
            content=json.dumps({"proposal": {"kind": "experiment", "objective_ids": ["FIFO_BEHAVIOR"],
                "seed": len(self.contexts), "operations": [{"op": "reset"}, {"op": "write", "value": 7},
                                                          {"op": "read"}]}}),
        )


async def eventually(predicate, seconds=5):
    async with asyncio.timeout(seconds):
        while not predicate():
            await asyncio.sleep(0.01)


def test_curated_inputs_match_runner_identity():
    from veriforge.rtl import reviewed_contract
    from veriforge.rtl.runner import ROOT, FIXTURES, digest, encoded
    parameters = FifoParameters(width=5, depth=7)
    revision, contract = curated_fifo_inputs("fifo_reset_bug", parameters)
    assert contract == reviewed_contract(parameters)
    source = digest((ROOT / "targets/fifo/fifo.sv").read_bytes())
    assert revision.source_hash == digest(encoded({"source": source, "variant": FIXTURES["fifo_reset_bug"],
                                                   "parameters": parameters.model_dump()}))
    with pytest.raises(ValueError, match="not installed"):
        curated_fifo_inputs("not-a-curated-fixture")


def test_attempt_directories_unique_and_runner_evidence_untouched(tmp_path):
    async def scenario():
        first = workspace(tmp_path)
        second = workspace(tmp_path)
        assert first.path != second.path
        executor = LocalRunnerExecutor(first, fake_runner)
        result1 = await executor(experiment())
        result2 = await executor(experiment())
        rounds = sorted((first.path / "rounds").iterdir())
        assert len(rounds) == 2 and result1 == result2
        for directory in rounds:
            assert (directory / "attempt/result.json").read_text() == result1.model_dump_json()
            assert json.loads((directory / "experiment.json").read_text()) == experiment().model_dump(mode="json")
            assert json.loads((directory / "settlement.json").read_text())["runner_settled"]
    asyncio.run(scenario())


def test_cancellation_waits_repeated_cancels_and_discards_late_success(tmp_path):
    async def scenario():
        started, token_seen, release, cleaned = (threading.Event() for _ in range(4))
        def controlled_runner(plan, output_dir, *, cancel_event):
            started.set()
            assert cancel_event.wait(5)
            token_seen.set()
            assert release.wait(5)
            result = fake_runner(plan, output_dir, cancel_event=cancel_event)
            cleaned.set()
            return result  # Deliberately late success must never be accepted.
        local = workspace(tmp_path)
        executor = LocalRunnerExecutor(local, controlled_runner)
        task = asyncio.create_task(executor(experiment()))
        await eventually(started.is_set)
        task.cancel()
        await eventually(token_seen.is_set)
        task.cancel()
        await asyncio.sleep(0.02)
        assert not task.done() and not cleaned.is_set()
        release.set()
        with pytest.raises(asyncio.CancelledError):
            await task
        assert cleaned.is_set()
        directory = next((local.path / "rounds").iterdir())
        settlement = json.loads((directory / "settlement.json").read_text())
        assert settlement["state"] == "discarded_after_cancel" and not settlement["delivered"]
        assert (directory / "attempt/result.json").exists()  # Retained original evidence.
        assert not (directory / "runner-result.json").exists()
    asyncio.run(scenario())


def _running(pid):
    if os.name == "nt":
        kernel = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel.OpenProcess.argtypes = [ctypes.c_ulong, ctypes.c_bool, ctypes.c_ulong]
        kernel.OpenProcess.restype = ctypes.c_void_p
        kernel.WaitForSingleObject.argtypes = [ctypes.c_void_p, ctypes.c_ulong]
        kernel.CloseHandle.argtypes = [ctypes.c_void_p]
        handle = kernel.OpenProcess(0x00100000, False, pid)
        if not handle:
            return False
        try:
            return kernel.WaitForSingleObject(handle, 0) == 258
        finally:
            kernel.CloseHandle(handle)
    stat = Path(f"/proc/{pid}/stat")
    if stat.exists() and stat.read_text().split()[2] == "Z":
        return False
    try:
        os.kill(pid, 0)
        return True
    except ProcessLookupError:
        return False


def test_controlled_child_and_grandchild_terminate_before_cancel_returns(tmp_path):
    """Real OS process cleanup through the RTL owner's bounded primitive, no HDL."""
    from veriforge.rtl.runner import _bounded, _environment

    async def scenario():
        local = workspace(tmp_path)
        child_file = tmp_path / "controlled-pids.json"
        cleaned = threading.Event()
        command = (
            "import json,os,pathlib,subprocess,sys,time; "
            "child=subprocess.Popen([sys.executable,'-c','import time; time.sleep(60)']); "
            "pathlib.Path(sys.argv[1]).write_text(json.dumps([os.getpid(),child.pid])); time.sleep(60)"
        )
        def controlled_process_runner(plan, output_dir, *, cancel_event):
            code, reason = _bounded([sys.executable, "-u", "-c", command, str(child_file)],
                                    output_dir, _environment(), 5, cancel_event, 1_000_000)
            assert reason == "cancelled"
            cleaned.set()
            return fake_checked_result(plan)
        executor = LocalRunnerExecutor(local, controlled_process_runner)
        task = asyncio.create_task(executor(experiment()))
        await eventually(lambda: child_file.exists() and child_file.stat().st_size > 0)
        pids = json.loads(child_file.read_text())
        assert all(_running(pid) for pid in pids)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        assert cleaned.is_set()
        assert all(not _running(pid) for pid in pids)
    asyncio.run(scenario())


def test_runner_failure_is_safe_and_next_attempt_can_run(tmp_path):
    async def scenario():
        def fail(plan, output_dir, *, cancel_event):
            raise RuntimeError("unit-test-sensitive-error-never-persist")
        local = workspace(tmp_path)
        executor = LocalRunnerExecutor(local, fail)
        with pytest.raises(RuntimeError, match="runner_error"):
            await executor(experiment())
        content = (local.path / "rounds/round-0001/settlement.json").read_text()
        assert "sensitive" not in content and '"runner_settled": true' in content
    asyncio.run(scenario())


def test_local_workflow_persists_feedback_and_report(tmp_path):
    provider = ExplicitFakeProvider()
    local, report = asyncio.run(run_local_investigation(
        tmp_path, provider=provider, runner=fake_runner, control=lambda: "RUNNING",
        limits=InvestigationLimits(max_rounds=2, max_total_cycles=6, max_cycles_per_experiment=3),
    ))
    assert report.stop_reason == "round_limit" and len(report.results) == 2
    assert provider.contexts[1].feedback[0].verdict == "pass_within_scope"
    assert json.loads((local.path / "report.json").read_text()) == report.model_dump(mode="json")
    assert json.loads((local.path / "state.json").read_text())["state"] == "settled"
    assert json.loads((local.path / "manifest.json").read_text())["strategy"] == "meta"


def test_local_missing_credentials_never_starts_runner(tmp_path, monkeypatch):
    monkeypatch.delenv("MODEL_API_KEY", raising=False)
    def forbidden(*args, **kwargs):
        pytest.fail("runner must not start without a model proposal")
    local, report = asyncio.run(run_local_investigation(
        tmp_path, provider=MetaProvider(MetaConfig()), runner=forbidden, control=lambda: "RUNNING",
    ))
    assert report.stop_reason == "model_unavailable" and not report.results
    assert not list((local.path / "rounds").iterdir())
    assert (local.path / "report.json").exists()


def test_controller_cancel_persists_final_report_without_late_result(tmp_path):
    async def scenario():
        started = threading.Event()
        cleaned = threading.Event()
        controllers = []
        def slow_runner(plan, output_dir, *, cancel_event):
            started.set()
            assert cancel_event.wait(5)
            result = fake_runner(plan, output_dir, cancel_event=cancel_event)
            cleaned.set()
            return result
        operation = asyncio.create_task(run_local_investigation(
            tmp_path, provider=ExplicitFakeProvider(), runner=slow_runner, control=lambda: "RUNNING",
            on_controller=controllers.append,
        ))
        await eventually(started.is_set)
        controllers[0].cancel()
        local, report = await operation
        assert cleaned.is_set() and report.stop_reason == "cancelled" and not report.results
        assert json.loads((local.path / "report.json").read_text())["stop_reason"] == "cancelled"
    asyncio.run(scenario())


def test_external_task_cancellation_settles_runner_and_preserves_checkpoint(tmp_path):
    async def scenario():
        started, cleaned = threading.Event(), threading.Event()
        def slow_runner(plan, output_dir, *, cancel_event):
            started.set()
            assert cancel_event.wait(5)
            cleaned.set()
            return fake_checked_result(plan)
        operation = asyncio.create_task(run_local_investigation(
            tmp_path, provider=ExplicitFakeProvider(), runner=slow_runner, control=lambda: "RUNNING",
        ))
        await eventually(started.is_set)
        operation.cancel()
        with pytest.raises(asyncio.CancelledError):
            await operation
        assert cleaned.is_set()
        path = next(tmp_path.iterdir())
        state = json.loads((path / "state.json").read_text())
        assert state["state"] == "interrupted" and not state["report_available"]
        assert (path / "rounds/round-0001/experiment.json").exists()
    asyncio.run(scenario())


def test_installed_source_change_prevents_runner_start(tmp_path, monkeypatch):
    from veriforge.planner import local
    original = local.curated_fifo_inputs
    calls = []
    def changed(*args):
        revision, contract = original(*args)
        calls.append(1)
        if len(calls) > 1:
            revision = revision.model_copy(update={"source_hash": "f" * 64})
        return revision, contract
    monkeypatch.setattr(local, "curated_fifo_inputs", changed)
    def forbidden(*args, **kwargs):
        pytest.fail("changed installed identity must stop before runner")
    path, report = asyncio.run(local.run_local_investigation(
        tmp_path, provider=ExplicitFakeProvider(), runner=forbidden, control=lambda: "RUNNING",
    ))
    assert report.stop_reason == "executor_error" and not report.results
    state = json.loads((path.path / "rounds/round-0001/settlement.json").read_text())
    assert state["state"] == "executor_error" and not state["delivered"]


def test_cli_unavailable_state_and_safe_json(tmp_path, monkeypatch, capsys):
    monkeypatch.delenv("MODEL_API_KEY", raising=False)
    code = main(["--output", str(tmp_path), "--rounds", "1"])
    output = json.loads(capsys.readouterr().out)
    assert code == 2 and output["stop_reason"] == "model_unavailable"
    assert Path(output["report"]).exists()


@pytest.mark.parametrize("args", [["--rounds", "0"], ["--cycles", "-1"], ["--seconds", "nan"],
                                      ["--fixture", "arbitrary"], ["--input-usd-per-million", "1"]])
def test_cli_rejects_unbounded_or_invalid_arguments(args, tmp_path):
    with pytest.raises(SystemExit) as failure:
        main(["--output", str(tmp_path), *args])
    assert failure.value.code == 2
    assert not list(tmp_path.iterdir())
