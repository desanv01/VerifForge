"""Unit evidence only: all model and execution responses here are explicitly fake."""

import asyncio
import json
from decimal import Decimal

import httpx
import pytest
from pydantic import SecretStr, ValidationError

from veriforge.contracts import (
    AttemptResult, BehavioralContract, CheckerIdentity, EvidenceIdentity,
    ExecutionStatus, FailureSignature, ProjectRevision, Requirement, ResourceUsage,
    SourceFile, Verdict, canonical_hash,
)
from veriforge.planner import (
    InvestigationController, InvestigationLimits, MetaConfig, MetaProvider,
    ModelUsage, PlannerContext, Pricing, ProviderCompletion, ProviderError,
)
from veriforge.planner.provider import ENDPOINT

HASH = "a" * 64
CONTRACT = BehavioralContract(
    review_status="reviewed", checker=CheckerIdentity(checker_id="fifo", version="1", sha256=HASH),
    requirements=(Requirement(requirement_id="FIFO_ORDER", description="Preserve accepted order"),),
)
REVISION = ProjectRevision(project_id="p", revision_id="r", source_hash=HASH,
                           fixture_id="fifo_correct", sources=(SourceFile(path="fifo.sv", sha256=HASH),))


def proposal(seed=1, **changes):
    value = {"kind": "experiment", "objective_ids": ["FIFO_ORDER"], "seed": seed,
             "operations": [{"op": "reset"}, {"op": "write", "value": 42}, {"op": "read"}]}
    value.update(changes)
    return json.dumps({"proposal": value})


def completion(content=None, **changes):
    values = dict(content=content or proposal(), model="explicit-fake-provider",
                  usage=ModelUsage(input_tokens=10, output_tokens=20), finish_reason="stop")
    values.update(changes)
    return ProviderCompletion(**values)


class FakeProvider:
    """Deliberate test double, never a real model claim."""

    model = "explicit-fake-provider"

    def __init__(self, *responses):
        self.responses = list(responses or [completion()])
        self.contexts = []

    async def complete(self, context, **kwargs):
        self.contexts.append(context)
        response = self.responses[min(len(self.contexts) - 1, len(self.responses) - 1)]
        if isinstance(response, Exception):
            raise response
        return response


def fake_result(experiment, *, verdict=Verdict.PASS_WITHIN_SCOPE, **changes):
    values = dict(
        attempt_id="fake-attempt", experiment_id=experiment.experiment_id,
        execution_status=ExecutionStatus.COMPLETED, verdict=verdict,
        identity=EvidenceIdentity(source_hash=HASH, checker_hash=HASH,
                                  contract_hash=canonical_hash(CONTRACT),
                                  experiment_hash=canonical_hash(experiment), environment_hash=HASH),
        usage=ResourceUsage(cycles=experiment.cycle_count), requirement_coverage=("FIFO_ORDER",),
    )
    if verdict in {Verdict.DESIGN_MISMATCH, Verdict.PROPERTY_FAILURE}:
        values["failure_signature"] = FailureSignature(
            requirement_id="FIFO_ORDER", failure_class="order", first_cycle=2,
            expected="42", observed="0", signature_hash=HASH,
        )
    values.update(changes)
    return AttemptResult(**values)


async def fake_executor(experiment):
    return fake_result(experiment)


def controller(provider=None, executor=fake_executor, limits=None, **kwargs):
    return InvestigationController(provider or FakeProvider(), executor, revision=REVISION,
                                   contract=CONTRACT, limits=limits or InvestigationLimits(max_rounds=1),
                                   **kwargs)


def run(value):
    return asyncio.run(value)


def test_valid_stimulus_is_bound_by_controller():
    report = run(controller().run())
    assert report.stop_reason == "round_limit"
    assert report.experiments[0].fixture_id == "fifo_correct"
    assert report.experiments[0].parameters == CONTRACT.parameters
    assert report.results[0].verdict == Verdict.PASS_WITHIN_SCOPE
    assert report.reserved_cycles == 10000
    assert report.price_status == "unknown" and report.reserved_cost_usd is None


@pytest.mark.parametrize("content", [
    "not json", proposal(command="echo bad"), proposal(budget={"max_cycles": 999999}),
    proposal(checker="replacement"), proposal(fixture_id="fifo_order_bug"),
    proposal(objective_ids=["INVENTED"]), proposal(operations=[{"op": "read"}]),
    proposal(operations=[{"op": "reset"}, {"op": "write", "value": 256}]),
    proposal(operations=[{"op": "reset", "cycles": 10001}]),
    proposal(operations=[{"op": "shell", "command": "anything"}]),
])
def test_invalid_proposals_never_execute_and_correction_is_bounded(content):
    provider = FakeProvider(completion(content))
    report = run(controller(provider, limits=InvestigationLimits(max_rounds=5)).run())
    assert report.stop_reason == "invalid_proposal_limit"
    assert not report.experiments and len(report.model_calls) == 2
    assert provider.contexts[1].correction
    assert all(call.outcome == "invalid_proposal" for call in report.model_calls)


def test_feedback_comes_from_checker_and_hides_fixture_labels():
    provider = FakeProvider(completion(), completion(proposal(seed=2)))
    report = run(controller(provider, limits=InvestigationLimits(max_rounds=2)).run())
    assert len(report.results) == 2
    feedback = provider.contexts[1].feedback[0]
    assert feedback.verdict == "pass_within_scope" and feedback.coverage == ("FIFO_ORDER",)
    assert "fifo_correct" not in provider.contexts[1].model_dump_json()
    assert "fifo_order_bug" not in provider.contexts[1].model_dump_json()


def test_cycle_limit_reserves_full_execution_budget():
    report = run(controller(limits=InvestigationLimits(max_rounds=5, max_total_cycles=3)).run())
    assert report.stop_reason == "cycle_limit" and report.reserved_cycles == 3


@pytest.mark.parametrize("limit,reason", [
    ({"max_model_tokens": 1}, "model_token_limit"),
    ({"max_input_bytes": 1}, "input_size_limit"),
    ({"max_cost_usd": Decimal("1")}, "price_unknown"),
])
def test_admission_limits_prevent_calls(limit, reason):
    provider = FakeProvider()
    report = run(controller(provider, limits=InvestigationLimits(**limit)).run())
    assert report.stop_reason == reason and not provider.contexts


def test_known_cost_limit_and_ledger():
    pricing = Pricing(input_usd_per_million=Decimal("1"), output_usd_per_million=Decimal("2"))
    report = run(controller(pricing=pricing).run())
    assert report.price_status == "known"
    assert report.model_calls[0].cost_usd == Decimal("0.00005")
    provider = FakeProvider()
    report = run(controller(provider, pricing=pricing,
                            limits=InvestigationLimits(max_cost_usd=Decimal("0.000001"))).run())
    assert report.stop_reason == "cost_limit" and not provider.contexts


@pytest.mark.parametrize("changes,reason", [
    ({"usage": None}, "model_usage_unknown"),
    ({"usage": ModelUsage(input_tokens=1, output_tokens=99999)}, "model_usage_limit_violated"),
    ({"model": "unexpected"}, "model_identity_mismatch"),
])
def test_model_accounting_fails_closed(changes, reason):
    report = run(controller(FakeProvider(completion(**changes))).run())
    assert report.stop_reason == reason and not report.experiments


def test_duplicate_proposal_stops_no_progress():
    report = run(controller(limits=InvestigationLimits(max_rounds=5)).run())
    assert report.stop_reason == "no_progress" and len(report.experiments) == 1


def test_model_can_stop_without_execution():
    report = run(controller(FakeProvider(completion('{"proposal":{"kind":"stop","reason":"done"}}'))).run())
    assert report.stop_reason == "model_stopped" and not report.experiments


def test_transient_errors_are_bounded_and_reserved():
    provider = FakeProvider(ProviderError("model_http_429", transient=True))
    report = run(controller(provider, limits=InvestigationLimits(max_rounds=5)).run())
    assert len(report.model_calls) == 2 and report.stop_reason == "model_http_429"
    assert report.reserved_model_tokens == sum(c.reserved_tokens for c in report.model_calls)


def test_identity_mismatch_never_becomes_feedback():
    async def executor(experiment):
        return fake_result(experiment, identity=EvidenceIdentity(
            source_hash="b" * 64, checker_hash=HASH, contract_hash=canonical_hash(CONTRACT),
            experiment_hash=canonical_hash(experiment), environment_hash=HASH))
    report = run(controller(executor=executor).run())
    assert report.stop_reason == "result_identity_mismatch" and not report.results


def test_timeout_is_execution_blocked_not_a_finding():
    async def executor(experiment):
        return fake_result(experiment, verdict=Verdict.TIMEOUT)
    report = run(controller(executor=executor).run())
    assert report.stop_reason == "execution_blocked"
    assert report.results[0].verdict == Verdict.TIMEOUT
    assert report.results[0].failure_signature is None


def test_independent_finding_stops_investigation():
    async def executor(experiment):
        return fake_result(experiment, verdict=Verdict.DESIGN_MISMATCH)
    report = run(controller(executor=executor).run())
    assert report.stop_reason == "finding_observed"


def test_cancel_active_executor_runs_cleanup():
    async def scenario():
        started = asyncio.Event()
        settled = asyncio.Event()
        async def executor(experiment):
            started.set()
            try:
                await asyncio.Event().wait()
            finally:
                settled.set()
        subject = controller(executor=executor)
        task = asyncio.create_task(subject.run())
        await started.wait()
        subject.cancel()
        report = await task
        assert settled.is_set() and report.stop_reason == "cancelled"
    run(scenario())


def test_stop_and_pause_prevent_new_operations():
    subject = controller()
    subject.stop()
    assert run(subject.run()).stop_reason == "stopped"
    assert run(controller(control=lambda: "PAUSED").run()).stop_reason == "paused"


def test_executor_wall_timeout_runs_cleanup():
    async def scenario():
        settled = asyncio.Event()
        async def executor(experiment):
            try:
                await asyncio.Event().wait()
            finally:
                settled.set()
        subject = controller(executor=executor, limits=InvestigationLimits(max_execution_seconds=0.01))
        report = await subject.run()
        assert report.stop_reason == "wall_time_limit" and settled.is_set()
    run(scenario())


def test_draft_contract_and_limit_mutation_rejected():
    with pytest.raises(ValueError, match="reviewed"):
        InvestigationController(FakeProvider(), fake_executor, revision=REVISION,
                                contract=CONTRACT.model_copy(update={"review_status": "draft"}))
    limits = InvestigationLimits()
    with pytest.raises(ValidationError):
        limits.max_rounds = 999


def test_controller_cannot_be_reused():
    subject = controller()
    run(subject.run())
    with pytest.raises(RuntimeError, match="single-use"):
        run(subject.run())


def context():
    return PlannerContext(contract=CONTRACT, remaining_cycles=100, max_cycles_per_experiment=100)


def test_missing_credential_is_model_unavailable(monkeypatch):
    monkeypatch.delenv("MODEL_API_KEY", raising=False)
    provider = MetaProvider()
    assert not provider.available
    with pytest.raises(ProviderError, match="model_unavailable"):
        run(provider.complete(context(), output_tokens=100, timeout_seconds=1))


def test_documented_model_override(monkeypatch):
    monkeypatch.setenv("VERIFORGE_META_MODEL", "unit-test-model-override")
    monkeypatch.delenv("MODEL_API_KEY", raising=False)
    config = MetaConfig.from_env()
    assert config.model == "unit-test-model-override" and config.api_key is None


def test_meta_wire_request_and_usage_with_explicit_mock_transport():
    def handle(request):
        assert str(request.url) == ENDPOINT
        assert request.headers["authorization"] == "Bearer unit-test-not-a-credential"
        payload = json.loads(request.content)
        assert payload["max_completion_tokens"] == 100
        assert payload["response_format"]["type"] == "json_schema"
        assert payload["messages"][0]["role"] == "developer"
        assert "unit-test-not-a-credential" not in request.content.decode()
        return httpx.Response(200, json={
            "model": "muse-spark-1.3", "choices": [{"message": {"content": proposal()}, "finish_reason": "stop"}],
            "usage": {"prompt_tokens": 12, "completion_tokens": 34},
        })
    config = MetaConfig(api_key=SecretStr("unit-test-not-a-credential"))
    provider = MetaProvider(config, transport=httpx.MockTransport(handle))
    response = run(provider.complete(context(), output_tokens=100, timeout_seconds=1))
    assert response.usage == ModelUsage(input_tokens=12, output_tokens=34)
    assert "unit-test-not-a-credential" not in repr(config)
    assert "unit-test-not-a-credential" not in config.model_dump_json()


@pytest.mark.parametrize("status,code,transient", [
    (401, "model_unavailable", False), (400, "model_http_400", False),
    (429, "model_http_429", True), (503, "model_http_503", True),
    (302, "model_http_302", False),
])
def test_http_errors_are_safe_and_do_not_leak_body(status, code, transient):
    provider = MetaProvider(MetaConfig(api_key=SecretStr("unit-test-secret")),
                            transport=httpx.MockTransport(lambda request: httpx.Response(
                                status, text="unit-test-secret", headers={"location": "https://example.org"})))
    with pytest.raises(ProviderError) as caught:
        run(provider.complete(context(), output_tokens=100, timeout_seconds=1))
    assert str(caught.value) == code and caught.value.transient == transient
    assert caught.value.__cause__ is None


def test_malformed_response_is_safe():
    provider = MetaProvider(MetaConfig(api_key=SecretStr("unit-test-secret")),
                            transport=httpx.MockTransport(lambda request: httpx.Response(200, json={})))
    with pytest.raises(ProviderError, match="model_response_invalid"):
        run(provider.complete(context(), output_tokens=100, timeout_seconds=1))


def test_oversized_response_rejected_before_parsing():
    provider = MetaProvider(MetaConfig(api_key=SecretStr("unit-test-secret")),
                            transport=httpx.MockTransport(lambda request: httpx.Response(
                                200, content=b"x" * 512001)))
    with pytest.raises(ProviderError, match="model_response_too_large"):
        run(provider.complete(context(), output_tokens=100, timeout_seconds=1))


def test_transport_timeout_is_safe_and_retryable():
    def handle(request):
        raise httpx.ReadTimeout("unit-test-secret", request=request)
    provider = MetaProvider(MetaConfig(api_key=SecretStr("unit-test-secret")),
                            transport=httpx.MockTransport(handle))
    with pytest.raises(ProviderError) as caught:
        run(provider.complete(context(), output_tokens=100, timeout_seconds=1))
    assert str(caught.value) == "model_transport_error" and caught.value.transient


def test_incomplete_model_response_never_executes():
    report = run(controller(FakeProvider(completion(finish_reason="length"))).run())
    assert not report.experiments and report.model_calls[0].outcome == "invalid_proposal"


def test_cancel_active_provider_prevents_execution():
    async def scenario():
        started = asyncio.Event()
        settled = asyncio.Event()
        class SlowFakeProvider:
            model = "explicit-fake-provider"
            async def complete(self, context, **kwargs):
                started.set()
                try:
                    await asyncio.Event().wait()
                finally:
                    settled.set()
        subject = controller(SlowFakeProvider())
        task = asyncio.create_task(subject.run())
        await started.wait()
        subject.cancel()
        report = await task
        assert settled.is_set() and report.stop_reason == "cancelled"
        assert not report.experiments and report.model_calls[0].outcome == "cancelled"
    run(scenario())


def test_independent_failure_signature_enters_followup_context():
    async def executor(experiment):
        return fake_result(experiment, verdict=Verdict.DESIGN_MISMATCH)
    provider = FakeProvider(completion(), completion(proposal(seed=2)))
    report = run(controller(provider, executor=executor,
                            limits=InvestigationLimits(max_rounds=2, stop_on_finding=False)).run())
    assert len(report.results) == 2
    signature = provider.contexts[1].feedback[0].failure_signature
    assert signature.requirement_id == "FIFO_ORDER"
    assert signature.expected == "42" and signature.observed == "0"
