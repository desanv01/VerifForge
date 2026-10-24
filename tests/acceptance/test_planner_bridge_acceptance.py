"""Independent bridge checks with fake provider/runner only; no subprocess/HDL."""
import asyncio
import json
from pathlib import Path
import threading

import pytest

from veriforge.contracts import AttemptResult, EvidenceIdentity, Experiment, canonical_hash
from veriforge.planner import (
    InvestigationLimits, LocalRunnerExecutor, LocalWorkspace, ModelUsage, ProviderCompletion,
    curated_fifo_inputs, run_local_investigation,
)


class FakeProvider:
    model = "independent-bridge-stub"

    async def complete(self, context, **kwargs):
        return ProviderCompletion(model=self.model, usage=ModelUsage(input_tokens=1, output_tokens=1),
            finish_reason="stop", content=json.dumps({"proposal": {"kind": "experiment",
                "objective_ids": ["FIFO_BEHAVIOR"], "seed": 12, "operations": [{"op": "reset"}]}}))


def checked_stub(plan, *, changed=None):
    revision, contract = curated_fifo_inputs(plan.fixture_id, plan.parameters)
    identity = dict(source_hash=revision.source_hash, checker_hash=contract.checker.sha256,
        contract_hash=canonical_hash(contract), experiment_hash=canonical_hash(plan), environment_hash="a" * 64)
    identity.update(changed or {})
    return AttemptResult(attempt_id="independent-scripted-only", experiment_id=plan.experiment_id,
        execution_status="completed", verdict="pass_within_scope", identity=EvidenceIdentity(**identity))


@pytest.mark.parametrize("field", ["source_hash", "checker_hash", "contract_hash"])
def test_bridge_keeps_raw_mismatched_evidence_but_controller_accepts_none(tmp_path, field):
    def runner(plan, output, *, cancel_event):
        assert not list(output.iterdir())
        result = checked_stub(plan, changed={field: "f" * 64})
        (output / "result.json").write_text(result.model_dump_json())
        return result
    workspace, report = asyncio.run(run_local_investigation(tmp_path, provider=FakeProvider(),
        runner=runner, control=lambda: "RUNNING", limits=InvestigationLimits(max_rounds=1)))
    assert report.stop_reason == "result_identity_mismatch"
    assert not report.results
    directory = workspace.path / "rounds/round-0001"
    raw = json.loads((directory / "attempt/result.json").read_text())
    checkpoint = json.loads((directory / "runner-result.json").read_text())
    assert raw == checkpoint and raw["identity"][field] == "f" * 64
    assert json.loads((workspace.path / "report.json").read_text())["results"] == []


def test_cancelled_erroring_runner_settles_before_reuse_and_rejects_overlap(tmp_path):
    async def scenario():
        entered, release, token_seen, settled = (threading.Event() for _ in range(4))
        invocations = []
        def runner(plan, output, *, cancel_event):
            invocations.append(output)
            if len(invocations) == 1:
                entered.set()
                assert cancel_event.wait(5)
                token_seen.set()
                assert release.wait(5)
                settled.set()
                raise RuntimeError("synthetic-private-runner-error")
            return checked_stub(plan)
        revision, contract = curated_fifo_inputs("fifo_correct")
        workspace = LocalWorkspace(tmp_path, revision=revision, contract=contract,
            limits=InvestigationLimits(), pricing=None, model=FakeProvider.model)
        executor = LocalRunnerExecutor(workspace, runner)
        plan = Experiment(experiment_id="independent-cancel", fixture_id="fifo_correct",
            objective_ids=("FIFO_BEHAVIOR",), operations=({"op": "reset"},))
        task = asyncio.create_task(executor(plan))
        async def wait_for(event):
            async with asyncio.timeout(6):
                while not event.is_set():
                    await asyncio.sleep(0.01)
        try:
            await wait_for(entered)
            task.cancel()
            await wait_for(token_seen)
            task.cancel()
            with pytest.raises(RuntimeError, match="executor_already_active"):
                await executor(plan)
            assert not task.done() and len(invocations) == 1
        finally:
            release.set()
            with pytest.raises(asyncio.CancelledError):
                await task
        assert settled.is_set()
        assert (await executor(plan)).verdict == "pass_within_scope"
        assert len(invocations) == 2
        rounds = workspace.path / "rounds"
        record = json.loads((rounds / "round-0001/settlement.json").read_text())
        assert record["state"] == "discarded_after_cancel"
        assert record["runner_settled"] and not record["delivered"]
        assert not (rounds / "round-0001/runner-result.json").exists()
        all_json = "\n".join(p.read_text() for p in workspace.path.rglob("*.json"))
        assert "synthetic-private-runner-error" not in all_json
        assert not any(t.name.startswith("vf-planner-") for t in threading.enumerate())
    asyncio.run(scenario())
