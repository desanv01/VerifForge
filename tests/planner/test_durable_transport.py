"""One-call transport unit evidence. Fake Meta subclasses/HTTP MockTransport only."""

import asyncio
import hashlib
import json

import httpx
import pytest
from pydantic import SecretStr, ValidationError

from veriforge.contracts import BehavioralContract, CheckerIdentity, Requirement
from veriforge.planner.durable_transport import (
    AdmittedModelRequest, invoke_admitted, reconstruct_admitted_request,
)
from veriforge.planner.models import InvestigationLimits, ModelUsage, ProviderCompletion
from veriforge.planner.provider import MetaConfig, MetaProvider, ProviderError, encode_payload
from veriforge.planner.steps import PlannerStepError, build_context, prepare_model_call

MODEL = "mock-admitted-model"
TEST_SECRET = "unit-test-not-a-real-credential"


@pytest.mark.parametrize("fail_on", [1, 2])
@pytest.mark.parametrize("error", [RuntimeError, asyncio.CancelledError, ProviderError])
def test_task_observer_failure_cancels_created_tasks_before_io(fail_on, error):
    async def scenario():
        request, _ = admitted()
        provider, tasks = ExplicitFakeMeta(), []
        def observer(task):
            tasks.append(task)
            if len(tasks) == fail_on:
                raise error("model_transport_error")
        result = await invoke_admitted(request, provider=provider, remaining_seconds=1,
            cancel_event=asyncio.Event(), task_observer=observer)
        await asyncio.sleep(0)
        assert provider.calls == 0 and not result.invocation_started
        assert result.code == "model_provider_error" and all(task.done() for task in tasks)
    asyncio.run(scenario())


def admitted(**changes):
    contract = BehavioralContract(review_status="reviewed",
        checker=CheckerIdentity(checker_id="mock", version="1", sha256="a" * 64),
        requirements=(Requirement(requirement_id="ORDER", description="Synthetic test requirement"),))
    limits = InvestigationLimits(max_model_seconds=1.0)
    context = build_context(contract, limits, reserved_cycles=0, feedback=())
    admission = prepare_model_call(context, MODEL, limits,
        reserved_tokens=0, reserved_cost_usd=None, pricing=None)
    values = dict(call_id="round-uuid-1", generation=7, requested_model=MODEL,
                  admission=admission, limits=limits, timeout_seconds=0.5, settlement_seconds=0.02)
    values.update(changes)
    return AdmittedModelRequest(**values), context


def completion(**changes):
    values = dict(model=MODEL, usage=ModelUsage(input_tokens=2, output_tokens=3), finish_reason="stop",
                  content='{"proposal":{"kind":"stop","reason":"finite fake task finished"}}')
    values.update(changes)
    return ProviderCompletion(**values)


class ExplicitFakeMeta(MetaProvider):
    """Scripted test subclass, never an alternative configured production provider."""

    def __init__(self, effect=None):
        super().__init__(MetaConfig(model=MODEL, api_key=SecretStr(TEST_SECRET)))
        self.calls = 0
        self.effect = effect

    async def complete(self, context, **kwargs):
        self.calls += 1
        return await self.effect(self, context, kwargs) if self.effect else completion()


def test_success_correlates_immutable_admission_without_ledger_mutation():
    request, _ = admitted()
    original = request.model_dump()
    provider = ExplicitFakeMeta()
    result = asyncio.run(invoke_admitted(request, provider=provider, remaining_seconds=1))
    assert result.state == "success" and result.code == "accepted" and provider.calls == 1
    assert result.call_id == request.call_id and result.generation == 7
    assert result.request_hash == request.admission.request_hash and result.requested_model == MODEL
    assert result.invocation_started and not result.remote_outcome_unknown
    assert result.usage == ModelUsage(input_tokens=2, output_tokens=3)
    assert result.decision.proposal.kind == "stop" and request.model_dump() == original
    assert TEST_SECRET not in result.model_dump_json() and TEST_SECRET not in repr(request)


def test_private_body_round_trip_requires_explicit_hashbound_reconstruction():
    request, context = admitted()
    assert "request_body" not in request.model_dump_json()
    with pytest.raises(ValidationError):
        AdmittedModelRequest.model_validate_json(request.model_dump_json())
    restored = reconstruct_admitted_request(
        call_id=request.call_id, generation=request.generation, requested_model=MODEL,
        admission_metadata=request.admission.model_dump(), context=context,
        limits=request.limits, timeout_seconds=request.timeout_seconds,
        settlement_seconds=request.settlement_seconds)
    assert restored == request and restored.admission.request_body == request.admission.request_body
    changed = context.model_copy(update={"remaining_cycles": context.remaining_cycles - 1})
    with pytest.raises(PlannerStepError, match="admission_invalid"):
        reconstruct_admitted_request(call_id=request.call_id, generation=7, requested_model=MODEL,
            admission_metadata=request.admission.model_dump(), context=changed, limits=request.limits,
            timeout_seconds=0.5)


@pytest.mark.parametrize("field,value", [
    ("request_hash", "b" * 64), ("schema_hash", "c" * 64),
    ("input_allowance", 1), ("reserved_tokens", 1),
    ("request_body", b'{"raw":"secret-like-invalid-body"}'),
])
def test_admission_drift_never_invokes_provider(field, value):
    request, _ = admitted()
    request = request.model_copy(update={"admission": request.admission.model_copy(update={field: value})})
    provider = ExplicitFakeMeta()
    result = asyncio.run(invoke_admitted(request, provider=provider, remaining_seconds=1))
    assert result.state == "rejected" and result.code == "admission_invalid"
    assert not result.invocation_started and not result.remote_outcome_unknown and provider.calls == 0
    assert "secret-like-invalid-body" not in result.model_dump_json()


def test_expired_or_pre_cancelled_call_has_no_remote_uncertainty():
    async def scenario():
        request, _ = admitted()
        provider = ExplicitFakeMeta()
        expired = await invoke_admitted(request, provider=provider, remaining_seconds=0)
        assert expired.code == "deadline_expired" and expired.state == "rejected"
        event = asyncio.Event()
        event.set()
        cancelled = await invoke_admitted(request, provider=provider, remaining_seconds=1, cancel_event=event)
        assert cancelled.state == "cancelled" and not cancelled.remote_outcome_unknown
        assert not cancelled.invocation_started and provider.calls == 0
    asyncio.run(scenario())


def test_missing_key_is_rejected_without_invocation():
    request, _ = admitted()
    provider = MetaProvider(MetaConfig(model=MODEL))
    result = asyncio.run(invoke_admitted(request, provider=provider, remaining_seconds=1))
    assert result.code == "model_unavailable" and not result.invocation_started


def test_timeout_after_send_is_uncertain_with_one_invocation_and_cleanup():
    async def scenario():
        cleaned = asyncio.Event()
        async def effect(provider, context, kwargs):
            try:
                await asyncio.Event().wait()
            finally:
                cleaned.set()
        request, _ = admitted(timeout_seconds=0.2)
        provider = ExplicitFakeMeta(effect)
        result = await invoke_admitted(request, provider=provider, remaining_seconds=1)
        assert result.state == "uncertain" and result.code == "model_timeout"
        assert result.invocation_started and result.remote_outcome_unknown and provider.calls == 1
        assert cleaned.is_set() and not result.cleanup_pending and result.decision is None
    asyncio.run(scenario())


@pytest.mark.parametrize("external_cancel", [False, True])
def test_after_handoff_cancellation_waits_bounded_then_discards_late_success(external_cancel):
    async def scenario():
        entered, cancel_seen, release, settled = (asyncio.Event() for _ in range(4))
        worker_tasks = []
        async def effect(provider, context, kwargs):
            worker_tasks.append(asyncio.current_task())
            entered.set()
            try:
                await asyncio.Event().wait()
            except asyncio.CancelledError:
                cancel_seen.set()
                await release.wait()  # Deliberately ignores cancellation until released.
            finally:
                settled.set()
            return completion()
        request, _ = admitted()
        provider = ExplicitFakeMeta(effect)
        cancel_event = asyncio.Event()
        task = asyncio.create_task(invoke_admitted(request, provider=provider,
                                                  remaining_seconds=1, cancel_event=cancel_event))
        await entered.wait()
        if external_cancel:
            task.cancel()
        else:
            cancel_event.set()
        await cancel_seen.wait()
        if external_cancel:
            task.cancel()  # Repeated cancellation must not extend settlement or expose a result.
        result = await asyncio.wait_for(task, timeout=1)
        assert result.state == "cancelled" and result.remote_outcome_unknown
        assert result.cleanup_pending and result.decision is None and not settled.is_set()
        before = result.model_dump()
        release.set()
        await worker_tasks[0]
        await asyncio.sleep(0)
        assert settled.is_set() and provider.calls == 1 and result.model_dump() == before
    asyncio.run(scenario())


def test_timeout_ignoring_cancellation_cannot_block_or_accept_late_completion():
    async def scenario():
        release = asyncio.Event()
        tasks = []
        async def effect(provider, context, kwargs):
            tasks.append(asyncio.current_task())
            try:
                await asyncio.Event().wait()
            except asyncio.CancelledError:
                await release.wait()
            return completion()
        request, _ = admitted(timeout_seconds=0.2, settlement_seconds=0.01)
        provider = ExplicitFakeMeta(effect)
        result = await asyncio.wait_for(invoke_admitted(request, provider=provider, remaining_seconds=1), timeout=1)
        assert result.state == "uncertain" and result.cleanup_pending and result.remote_outcome_unknown
        assert result.decision is None and provider.calls == 1
        release.set()
        await tasks[0]
    asyncio.run(scenario())


def test_model_drift_before_and_during_await_never_authorizes_decision():
    async def scenario():
        request, _ = admitted()
        before = ExplicitFakeMeta()
        before.model = "other-model"
        rejected = await invoke_admitted(request, provider=before, remaining_seconds=1)
        assert rejected.code == "model_identity_mismatch" and before.calls == 0
        async def change(provider, context, kwargs):
            provider.model = "different-model"
            return completion(model="different-model")
        during = ExplicitFakeMeta(change)
        outcome = await invoke_admitted(request, provider=during, remaining_seconds=1)
        assert outcome.code == "model_identity_mismatch" and outcome.requested_model == MODEL
        assert outcome.state == "rejected" and outcome.decision is None and during.calls == 1
    asyncio.run(scenario())


@pytest.mark.parametrize("code,state,unknown", [
    ("model_http_400", "rejected", False), ("model_http_402", "rejected", False),
    ("model_http_429", "rejected", False), ("model_http_503", "uncertain", True),
    ("model_http_302", "uncertain", True), ("model_http_408", "uncertain", True),
    ("model_transport_error", "uncertain", True), ("model_response_invalid", "uncertain", True),
    (TEST_SECRET, "uncertain", True),
])
def test_provider_error_classification_redacts_and_never_retries(code, state, unknown):
    async def effect(provider, context, kwargs):
        raise ProviderError(code, transient=True)
    request, _ = admitted()
    provider = ExplicitFakeMeta(effect)
    result = asyncio.run(invoke_admitted(request, provider=provider, remaining_seconds=1))
    assert result.state == state and result.remote_outcome_unknown == unknown
    assert result.decision is None and provider.calls == 1
    assert TEST_SECRET not in result.model_dump_json()


@pytest.mark.parametrize("values,code,state", [
    ({"content": "secret-like-invalid-content"}, "invalid_proposal", "rejected"),
    ({"usage": None}, "model_usage_unknown", "uncertain"),
    ({"usage": ModelUsage(input_tokens=99999999, output_tokens=2)}, "model_usage_limit_violated", "rejected"),
])
def test_response_validation_never_returns_raw_output(values, code, state):
    async def effect(provider, context, kwargs):
        return completion(**values)
    request, _ = admitted()
    provider = ExplicitFakeMeta(effect)
    result = asyncio.run(invoke_admitted(request, provider=provider, remaining_seconds=1))
    assert result.code == code and result.state == state and result.decision is None
    assert "secret-like-invalid-content" not in result.model_dump_json()


def test_existing_meta_http_adapter_sends_exact_admitted_body_via_mock_transport():
    request, _ = admitted()
    calls = []
    def handle(http_request):
        calls.append(http_request)
        assert http_request.content == request.admission.request_body
        assert http_request.headers["authorization"] == f"Bearer {TEST_SECRET}"
        assert TEST_SECRET.encode() not in http_request.content
        return httpx.Response(200, json={"model": MODEL,
            "choices": [{"message": {"content": completion().content}, "finish_reason": "stop"}],
            "usage": {"prompt_tokens": 2, "completion_tokens": 3}})
    provider = MetaProvider(MetaConfig(model=MODEL, api_key=SecretStr(TEST_SECRET)),
                            transport=httpx.MockTransport(handle))
    result = asyncio.run(invoke_admitted(request, provider=provider, remaining_seconds=1))
    assert result.state == "success" and len(calls) == 1 and TEST_SECRET not in result.model_dump_json()


def test_cancel_between_preflight_and_invocation_is_not_remote_uncertainty():
    async def scenario():
        event = asyncio.Event()
        class CancelOnAvailability(ExplicitFakeMeta):
            @property
            def available(self):
                event.set()
                return True
        request, _ = admitted()
        provider = CancelOnAvailability()
        result = await invoke_admitted(request, provider=provider, remaining_seconds=1, cancel_event=event)
        assert result.state == "cancelled" and not result.invocation_started
        assert not result.remote_outcome_unknown and provider.calls == 0
    asyncio.run(scenario())


@pytest.mark.parametrize("status,state,unknown", [(400, "rejected", False),
                                                   (429, "rejected", False), (503, "uncertain", True)])
def test_real_adapter_mock_http_error_never_echoes_body_or_retries(status, state, unknown):
    request, _ = admitted()
    calls = []
    def handle(http_request):
        calls.append(http_request)
        return httpx.Response(status, text=TEST_SECRET)
    provider = MetaProvider(MetaConfig(model=MODEL, api_key=SecretStr(TEST_SECRET)),
                            transport=httpx.MockTransport(handle))
    result = asyncio.run(invoke_admitted(request, provider=provider, remaining_seconds=1))
    assert result.state == state and result.remote_outcome_unknown == unknown and len(calls) == 1
    assert TEST_SECRET not in result.model_dump_json()


def test_existing_meta_adapter_timeout_after_mock_send_stays_unknown():
    async def scenario():
        sent, cleaned = asyncio.Event(), asyncio.Event()
        async def handle(http_request):
            sent.set()
            try:
                await asyncio.Event().wait()
            finally:
                cleaned.set()
        request, _ = admitted(timeout_seconds=0.2)
        provider = MetaProvider(MetaConfig(model=MODEL, api_key=SecretStr(TEST_SECRET)),
                                transport=httpx.MockTransport(handle))
        result = await invoke_admitted(request, provider=provider, remaining_seconds=1)
        assert sent.is_set() and cleaned.is_set() and result.remote_outcome_unknown
        assert result.state == "uncertain" and result.decision is None
    asyncio.run(scenario())


def test_unexpected_exception_and_malformed_hash_cannot_echo_secret():
    async def fail(provider, context, kwargs):
        raise RuntimeError(TEST_SECRET)
    request, _ = admitted()
    result = asyncio.run(invoke_admitted(request, provider=ExplicitFakeMeta(fail), remaining_seconds=1))
    assert result.code == "model_provider_error" and TEST_SECRET not in result.model_dump_json()
    broken = request.model_copy(update={"admission": request.admission.model_copy(update={"request_hash": TEST_SECRET})})
    provider = ExplicitFakeMeta()
    result = asyncio.run(invoke_admitted(broken, provider=provider, remaining_seconds=1))
    assert result.code == "admission_invalid" and result.request_hash == "" and provider.calls == 0
    assert TEST_SECRET not in result.model_dump_json()


def test_deep_malformed_admitted_json_is_safe_rejection_without_invocation():
    request, _ = admitted()
    body = b"[" * 1500 + b"0" + b"]" * 1500
    allowance = len(body) + 1024
    changed = request.admission.model_copy(update={
        "request_body": body, "request_hash": hashlib.sha256(body).hexdigest(),
        "input_allowance": allowance, "reserved_tokens": allowance + request.limits.max_output_tokens,
    })
    provider = ExplicitFakeMeta()
    outcome = asyncio.run(invoke_admitted(request.model_copy(update={"admission": changed}),
                                          provider=provider, remaining_seconds=1))
    assert outcome.state == "rejected" and outcome.code == "admission_invalid"
    assert provider.calls == 0 and not outcome.invocation_started
