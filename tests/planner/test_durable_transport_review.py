"""Independent one-call transport checks. Scripted provider only; no HTTP or keys."""
import asyncio
import hashlib
import json
import time
import pytest
from veriforge.contracts import BehavioralContract, CheckerIdentity, Requirement
from veriforge.planner.durable_transport import invoke_admitted, AdmittedModelRequest, reconstruct_admitted_request
from veriforge.planner.models import InvestigationLimits, ModelUsage, PlannerContext, ProviderCompletion
from veriforge.planner.provider import MetaProvider, ProviderError
from veriforge.planner.steps import PlannerStepError, build_context, prepare_model_call

MODEL = "independent-fake-only"
SENTINEL = "synthetic-private-error-must-not-escape"


def admitted():
    contract = BehavioralContract(review_status="reviewed", checker=CheckerIdentity(checker_id="review-fixture", version="1", sha256="c" * 64),
        requirements=(Requirement(requirement_id="FIFO_BEHAVIOR", description="Synthetic transport review; no DUT"),))
    limits = InvestigationLimits(max_model_seconds=2.0)
    context = build_context(contract, limits, reserved_cycles=0, feedback=())
    admission = prepare_model_call(context, MODEL, limits, reserved_tokens=0, reserved_cost_usd=None, pricing=None)
    return AdmittedModelRequest(call_id="review-round-42", generation=42, requested_model=MODEL, admission=admission,
        limits=limits, timeout_seconds=1.0, settlement_seconds=0.02), context


def stop_completion():
    return ProviderCompletion(model=MODEL, usage=ModelUsage(input_tokens=1, output_tokens=1), finish_reason="stop",
        content='{"proposal":{"kind":"stop","reason":"scripted fixture completed"}}')


class ScriptedMeta(MetaProvider):
    # No MetaConfig/from_env/credential lookup/HTTP client exists on this test path.
    def __init__(self, effect=None):
        self.model, self.calls, self.effect = MODEL, 0, effect
    @property
    def available(self): return True
    async def complete(self, context, **kwargs):
        self.calls += 1
        return await self.effect(context, kwargs) if self.effect else stop_completion()


@pytest.mark.parametrize("change", ["whitespace", "developer", "model", "output_tokens", "stream", "extra_field", "role"])
def test_rehashing_tampered_body_and_reservation_does_not_authorize_wire_drift(change):
    request, _ = admitted()
    payload = json.loads(request.admission.request_body)
    if change == "developer": payload["messages"][0]["content"] = SENTINEL
    if change == "model": payload["model"] = "not-admitted"
    if change == "output_tokens": payload["max_completion_tokens"] += 1
    if change == "stream": payload["stream"] = True
    if change == "extra_field": payload["unreviewed_option"] = True
    if change == "role": payload["messages"][1]["role"] = "developer"
    body = json.dumps(payload, indent=2 if change == "whitespace" else None, separators=None if change == "whitespace" else (",", ":")).encode()
    allowance = len(body) + 1024
    changed = request.admission.model_copy(update={"request_body": body, "request_hash": hashlib.sha256(body).hexdigest(),
        "input_allowance": allowance, "reserved_tokens": allowance + request.limits.max_output_tokens})
    provider = ScriptedMeta()
    outcome = asyncio.run(invoke_admitted(request.model_copy(update={"admission": changed}), provider=provider, remaining_seconds=2))
    assert outcome.state == "rejected" and outcome.code == "admission_invalid"
    assert provider.calls == 0 and not outcome.invocation_started and not outcome.remote_outcome_unknown
    assert outcome.decision is None and SENTINEL not in outcome.model_dump_json()


def test_private_reconstruction_rejects_supplied_body_and_preserves_persisted_identity():
    request, context = admitted()
    metadata = json.loads(request.admission.model_dump_json())
    restored_context = PlannerContext.model_validate_json(context.model_dump_json())
    values = dict(call_id=request.call_id, generation=request.generation, requested_model=MODEL, admission_metadata=metadata,
        context=restored_context, limits=request.limits, timeout_seconds=request.timeout_seconds, settlement_seconds=request.settlement_seconds)
    restored = reconstruct_admitted_request(**values)
    assert restored == request and restored.admission.request_body == request.admission.request_body
    assert "request_body" not in request.model_dump_json()
    with pytest.raises(PlannerStepError, match="admission_invalid"):
        reconstruct_admitted_request(**{**values, "admission_metadata": {**metadata, "request_body": request.admission.request_body}})
    with pytest.raises(PlannerStepError, match="admission_invalid"):
        reconstruct_admitted_request(**{**values, "requested_model": "other-model"})


@pytest.mark.parametrize("remaining", [True, float("nan"), float("inf"), "1", -1])
def test_invalid_or_expired_remaining_budget_never_hands_off(remaining):
    request, _ = admitted()
    provider = ScriptedMeta()
    result = asyncio.run(invoke_admitted(request, provider=provider, remaining_seconds=remaining))
    assert result.state == "rejected" and provider.calls == 0
    assert not result.invocation_started and not result.remote_outcome_unknown


def test_success_keeps_exact_call_generation_hash_model_and_reservation_immutable():
    request, _ = admitted()
    before = request.model_dump()
    body = request.admission.request_body
    provider = ScriptedMeta()
    result = asyncio.run(invoke_admitted(request, provider=provider, remaining_seconds=2))
    assert (result.call_id, result.generation, result.request_hash, result.requested_model) == (request.call_id, 42, hashlib.sha256(body).hexdigest(), MODEL)
    assert result.state == "success" and result.decision.proposal.kind == "stop" and provider.calls == 1
    assert request.model_dump() == before and request.admission.request_body == body
    assert not result.cleanup_pending and not result.remote_outcome_unknown


@pytest.mark.parametrize("code", ["model_http_429", "model_http_502", "model_http_408", "model_transport_error"])
def test_failure_never_retries_or_refunds_admission_and_redacts_exception(code):
    request, _ = admitted()
    async def fail(context, kwargs):
        error = ProviderError(code, transient=True)
        error.args = (SENTINEL,)
        raise error
    before = request.model_dump()
    provider = ScriptedMeta(fail)
    result = asyncio.run(invoke_admitted(request, provider=provider, remaining_seconds=2))
    assert provider.calls == 1 and request.model_dump() == before and result.decision is None
    assert result.state == ("rejected" if code == "model_http_429" else "uncertain")
    assert result.remote_outcome_unknown == (code != "model_http_429")
    assert result.usage is None and SENTINEL not in result.model_dump_json()


def test_late_exception_after_repeated_cancellation_is_consumed_and_cannot_replace_outcome():
    async def scenario():
        entered, release, cancelled = asyncio.Event(), asyncio.Event(), asyncio.Event()
        provider_tasks, loop_errors = [], []
        loop = asyncio.get_running_loop()
        previous_handler = loop.get_exception_handler()
        loop.set_exception_handler(lambda loop, details: loop_errors.append(details))
        async def ignore_cancel(context, kwargs):
            provider_tasks.append(asyncio.current_task())
            entered.set()
            try: await asyncio.Event().wait()
            except asyncio.CancelledError:
                cancelled.set()
                await release.wait()
            raise RuntimeError(SENTINEL)
        request, _ = admitted()
        provider = ScriptedMeta(ignore_cancel)
        request_before = request.model_dump()
        wrapper = asyncio.create_task(invoke_admitted(request, provider=provider, remaining_seconds=2))
        await asyncio.wait_for(entered.wait(), 1)
        start = time.monotonic()
        wrapper.cancel()
        await asyncio.wait_for(cancelled.wait(), 1)
        for _ in range(20):
            if wrapper.done(): break
            wrapper.cancel()
            await asyncio.sleep(0.001)
        result = await asyncio.wait_for(wrapper, 0.5)
        assert time.monotonic() - start < 0.5
        assert result.state == "cancelled" and result.cleanup_pending and result.remote_outcome_unknown
        assert result.decision is None and provider.calls == 1
        before = result.model_dump()
        release.set()
        await asyncio.wait(provider_tasks, timeout=0.5)
        await asyncio.sleep(0)
        assert all(task.done() for task in provider_tasks)
        assert loop_errors == [] and result.model_dump() == before and request.model_dump() == request_before
        assert SENTINEL not in result.model_dump_json()
        loop.set_exception_handler(previous_handler)
    asyncio.run(scenario())
