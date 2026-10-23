"""Independent planner acceptance: fake provider/executor and HTTP mocks only."""

import asyncio
import json

import httpx
import pytest
from pydantic import SecretStr

from veriforge.contracts import (
    AttemptResult, BehavioralContract, CheckerIdentity, EvidenceIdentity,
    ProjectRevision, Requirement, ResourceUsage, SourceFile, canonical_hash,
)
from veriforge.planner import (
    InvestigationController, InvestigationLimits, MetaConfig, MetaProvider,
    ModelUsage, PlannerContext, ProviderCompletion, ProviderError,
)

HASH = "a" * 64
CONTRACT = BehavioralContract(
    review_status="reviewed", checker=CheckerIdentity(checker_id="checker", version="1", sha256=HASH),
    requirements=(Requirement(requirement_id="ORDER", description="Preserve accepted order"),),
)
REVISION = ProjectRevision(project_id="p", revision_id="r", source_hash=HASH,
                           fixture_id="fifo_correct", sources=(SourceFile(path="fifo.sv", sha256=HASH),))


def completion():
    return ProviderCompletion(
        content=json.dumps({"proposal": {"kind": "experiment", "objective_ids": ["ORDER"], "seed": 1,
                                         "operations": [{"op": "reset"}, {"op": "write", "value": 3}]}}),
        model="acceptance-fake", usage=ModelUsage(input_tokens=1, output_tokens=1), finish_reason="stop",
    )


class FakeProvider:
    model = "acceptance-fake"

    def __init__(self):
        self.calls = 0

    async def complete(self, context, **kwargs):
        self.calls += 1
        return completion()


def result_for(experiment, **changes):
    values = dict(
        attempt_id="test-attempt", experiment_id=experiment.experiment_id,
        execution_status="completed", verdict="pass_within_scope",
        identity=EvidenceIdentity(source_hash=HASH, checker_hash=HASH, contract_hash=canonical_hash(CONTRACT),
                                  experiment_hash=canonical_hash(experiment), environment_hash=HASH),
        usage=ResourceUsage(cycles=experiment.cycle_count),
    )
    values.update(changes)
    return AttemptResult(**values)


def subject(executor, provider=None, **kwargs):
    return InvestigationController(provider or FakeProvider(), executor, revision=REVISION, contract=CONTRACT,
                                   limits=InvestigationLimits(max_rounds=3), **kwargs)


@pytest.mark.parametrize("field", ["source_hash", "checker_hash", "contract_hash", "experiment_hash"])
def test_each_wrong_identity_is_rejected_before_a_followup_call(field):
    async def execute(experiment):
        result = result_for(experiment)
        return result.model_copy(update={"identity": result.identity.model_copy(update={field: "b" * 64})})

    provider = FakeProvider()
    report = asyncio.run(subject(execute, provider).run())
    assert report.stop_reason == "result_identity_mismatch"
    assert provider.calls == 1 and report.results == ()


@pytest.mark.parametrize("case,reason", [
    ("wrong_experiment", "result_identity_mismatch"),
    ("unknown_coverage", "result_requirement_mismatch"),
    ("excess_cycles", "executor_cycle_limit_violated"),
    ("wrong_type", "executor_result_invalid"),
    ("raises", "executor_error"),
])
def test_invalid_executor_evidence_stops_before_feedback(case, reason):
    async def execute(experiment):
        if case == "wrong_type":
            return {"verdict": "pass_within_scope"}
        if case == "raises":
            raise RuntimeError("fake-private-error-do-not-export")
        changes = {
            "wrong_experiment": {"experiment_id": "unrelated"},
            "unknown_coverage": {"requirement_coverage": ("INVENTED",)},
            "excess_cycles": {"usage": ResourceUsage(cycles=experiment.budget.max_cycles + 1)},
        }
        return result_for(experiment, **changes[case])

    provider = FakeProvider()
    report = asyncio.run(subject(execute, provider).run())
    assert report.stop_reason == reason and not report.results and provider.calls == 1
    assert "fake-private-error-do-not-export" not in report.model_dump_json()


def test_direct_caller_cancellation_waits_for_executor_cleanup():
    async def scenario():
        started, cleaned = asyncio.Event(), asyncio.Event()

        async def execute(experiment):
            started.set()
            try:
                await asyncio.Event().wait()
            finally:
                await asyncio.sleep(0)
                cleaned.set()

        task = asyncio.create_task(subject(execute).run())
        await asyncio.wait_for(started.wait(), timeout=1)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await asyncio.wait_for(task, timeout=1)
        assert cleaned.is_set()

    asyncio.run(scenario())


@pytest.mark.parametrize("control_kind", ["stop", "pause"])
def test_control_change_during_provider_prevents_executor(control_kind):
    async def scenario():
        started, release = asyncio.Event(), asyncio.Event()
        state = ["RUNNING"]
        executions = []

        class HeldProvider(FakeProvider):
            async def complete(self, context, **kwargs):
                started.set()
                await release.wait()
                return completion()

        async def execute(experiment):
            executions.append(experiment)
            return result_for(experiment)

        controller = subject(execute, HeldProvider(), control=lambda: state[0])
        task = asyncio.create_task(controller.run())
        await asyncio.wait_for(started.wait(), timeout=1)
        if control_kind == "stop":
            controller.stop()
        else:
            state[0] = "PAUSED"
        release.set()
        report = await asyncio.wait_for(task, timeout=1)
        assert report.stop_reason == ("stopped" if control_kind == "stop" else "paused")
        assert not executions and not report.experiments

    asyncio.run(scenario())


@pytest.mark.parametrize("body", [
    [], {"choices": []}, {"choices": None},
    {"choices": [{"message": {"content": None}, "finish_reason": "stop"}], "model": "acceptance-fake"},
    {"choices": [{"message": {"content": "{}"}, "finish_reason": "stop"}], "model": "acceptance-fake",
     "usage": {"prompt_tokens": True, "completion_tokens": 1}},
])
def test_malformed_200_responses_are_safe_public_errors(body):
    provider = MetaProvider(MetaConfig(api_key=SecretStr("acceptance-fake-secret")),
                            transport=httpx.MockTransport(lambda request: httpx.Response(200, json=body)))
    context = PlannerContext(contract=CONTRACT, remaining_cycles=10, max_cycles_per_experiment=10)
    with pytest.raises(ProviderError) as error:
        asyncio.run(provider.complete(context, output_tokens=10, timeout_seconds=1))
    assert str(error.value) == "model_response_invalid" and not error.value.transient
    assert "acceptance-fake-secret" not in str(error.value)


def test_redirect_never_forwards_the_fake_credential():
    requests = []

    def respond(request):
        requests.append(request)
        return httpx.Response(307, headers={"location": "https://unrelated.invalid/steal"},
                              text="acceptance-fake-secret")

    provider = MetaProvider(MetaConfig(api_key=SecretStr("acceptance-fake-secret")),
                            transport=httpx.MockTransport(respond))
    context = PlannerContext(contract=CONTRACT, remaining_cycles=10, max_cycles_per_experiment=10)
    with pytest.raises(ProviderError, match="model_http_307"):
        asyncio.run(provider.complete(context, output_tokens=10, timeout_seconds=1))
    assert len(requests) == 1 and requests[0].url.host == "api.meta.ai"
