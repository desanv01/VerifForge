"""One service-owned Meta call from immutable admission; no durable state authority.

The coordinator owns generation/lease validation, reservations and retry policy.
After invocation starts, timeout/cancellation conservatively means remote outcome
unknown, even when the HTTP client has settled. No fallback or retry occurs here.
"""

from __future__ import annotations

import asyncio
from collections.abc import Callable, Mapping
from decimal import Decimal
import hashlib
import json
import math
import re
import time
from typing import Annotated, Literal

from pydantic import Field, StrictBool, StrictInt, model_validator

from veriforge.contracts import WireModel
from .models import InvestigationLimits, ModelUsage, PlannerContext, PlannerDecision
from .provider import MetaProvider, ProviderError, encode_payload, request_payload
from .steps import ModelAdmission, PlannerStepError, build_context, require_planner_capability, validate_completion


class AdmittedModelRequest(WireModel):
    schema_version: Literal["planner.meta-call.v1"] = "planner.meta-call.v1"
    call_id: Annotated[str, Field(min_length=1, max_length=128, pattern=r"^[A-Za-z0-9][A-Za-z0-9_.:-]*$")]
    generation: Annotated[StrictInt, Field(ge=0)]
    requested_model: Annotated[str, Field(min_length=1, max_length=128)]
    admission: ModelAdmission
    limits: InvestigationLimits
    timeout_seconds: Annotated[float, Field(gt=0, le=120)]
    settlement_seconds: Annotated[float, Field(gt=0, le=5)] = 1.0


class TransportOutcome(WireModel):
    call_id: str
    generation: int
    request_hash: Annotated[str, Field(pattern=r"^(?:[0-9a-f]{64})?$")]
    requested_model: str
    state: Literal["success", "rejected", "uncertain", "cancelled"]
    code: Annotated[str, Field(pattern=(
        r"^(accepted|admission_invalid|deadline_expired|cancelled|model_unavailable|"
        r"model_identity_mismatch|model_usage_unknown|model_usage_limit_violated|invalid_proposal|"
        r"model_timeout|model_transport_error|model_response_invalid|model_response_too_large|"
        r"model_provider_error|model_http_[1-5][0-9]{2})$"))]
    invocation_started: StrictBool
    remote_outcome_unknown: StrictBool
    cleanup_pending: StrictBool = False
    elapsed_seconds: Annotated[float, Field(ge=0)]
    transient_error: StrictBool = False
    usage: ModelUsage | None = None
    decision: PlannerDecision | None = None

    @model_validator(mode="after")
    def consistent(self):
        if self.state == "success" and (self.decision is None or self.usage is None or
                self.remote_outcome_unknown or not self.invocation_started):
            raise ValueError("success requires a known validated completion")
        if self.state != "success" and self.decision is not None:
            raise ValueError("only success carries a decision")
        if self.remote_outcome_unknown and not self.invocation_started:
            raise ValueError("unstarted call cannot have an unknown remote outcome")
        return self


def _decode_request(request: AdmittedModelRequest) -> PlannerContext:
    """Reject body/schema/model/budget drift before reaching the credentialed client."""
    admission, limits = request.admission, request.limits
    body = admission.request_body
    if len(body) > limits.max_input_bytes or hashlib.sha256(body).hexdigest() != admission.request_hash:
        raise ValueError
    capability = require_planner_capability("rtl.fifo.v1")
    if admission.schema_hash != capability.decision_schema_hash:
        raise ValueError
    if (admission.input_allowance != len(body) + 1024 or
        admission.reserved_tokens != admission.input_allowance + limits.max_output_tokens or
        admission.reserved_tokens > limits.max_model_tokens):
        raise ValueError
    if admission.reserved_cost_usd is not None and (not isinstance(admission.reserved_cost_usd, Decimal) or
            not admission.reserved_cost_usd.is_finite() or admission.reserved_cost_usd < 0):
        raise ValueError
    if limits.max_cost_usd is not None and (admission.reserved_cost_usd is None or
                                           admission.reserved_cost_usd > limits.max_cost_usd):
        raise ValueError
    payload = json.loads(body)
    context = PlannerContext.model_validate_json(payload["messages"][1]["content"])
    bounded = build_context(context.contract, limits,
                            reserved_cycles=limits.max_total_cycles - context.remaining_cycles,
                            feedback=context.feedback, correction=context.correction)
    if context != bounded or body != encode_payload(request_payload(context, request.requested_model,
                                                                    limits.max_output_tokens)):
        raise ValueError
    return context


def reconstruct_admitted_request(*, call_id: str, generation: int, requested_model: str,
                                 admission_metadata: Mapping, context: PlannerContext,
                                 limits: InvestigationLimits, timeout_seconds: float,
                                 settlement_seconds: float = 1.0) -> AdmittedModelRequest:
    """Explicit in-process reconstruction from trusted hash-bound private DB state.

    Model serialization omits the body. A blind JSON round trip must fail rather
    than invent an unbound request. Changed schema/codec/context fails closed.
    """
    try:
        if "request_body" in admission_metadata:
            raise ValueError
        body = encode_payload(request_payload(context, requested_model, limits.max_output_tokens))
        request = AdmittedModelRequest(
            call_id=call_id, generation=generation, requested_model=requested_model,
            admission=ModelAdmission.model_validate({**admission_metadata, "request_body": body}),
            limits=limits, timeout_seconds=timeout_seconds, settlement_seconds=settlement_seconds,
        )
        _decode_request(request)
        return request
    except (ValueError, TypeError, KeyError, IndexError, OverflowError, RecursionError):
        raise PlannerStepError("admission_invalid") from None


def _discard_terminal(task: asyncio.Task) -> None:
    if not task.cancelled():
        task.exception()


async def _settle_cancelled(task: asyncio.Task, seconds: float) -> bool:
    """Wait a finite cleanup interval; late completions are consumed, never applied."""
    deadline = time.monotonic() + seconds
    if not task.done():
        task.cancel()
    while not task.done():
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            break
        try:
            await asyncio.wait({task}, timeout=remaining)
        except asyncio.CancelledError:
            continue
    if task.done():
        _discard_terminal(task)
        return True
    task.add_done_callback(_discard_terminal)
    return False


class _CancelledBeforeInvocation(Exception):
    pass


class _ExpiredBeforeInvocation(Exception):
    pass


class _TaskObservationError(Exception):
    pass


async def invoke_admitted(request: AdmittedModelRequest, *, provider: MetaProvider,
                          remaining_seconds: float, cancel_event: asyncio.Event | None = None,
                          task_observer: Callable[[asyncio.Task], None] | None = None
                          ) -> TransportOutcome:
    """Invoke exactly once; return cancellation as data after local task settlement.

    Production caller supplies the existing MetaProvider constructed in its service
    process. Test doubles are for unit evidence only. Never expose this callable as
    a public raw-completion endpoint. Caller must validate generation before and
    after the call; receiving success is not authorization to apply its decision.
    Optional task_observer is trusted synchronous service bookkeeping, never a
    public callback. It observes every owned task before its first await. A hook
    failure aborts dispatch and retains/cancels created tasks; task observation
    alone neither acknowledges durable cleanup nor grants execution authority.
    """
    started_at = time.monotonic()
    invocation_started = False
    cleanup_pending = False
    task = cancellation = None
    correlation_hash = request.admission.request_hash
    if not isinstance(correlation_hash, str) or re.fullmatch(r"[0-9a-f]{64}", correlation_hash) is None:
        correlation_hash = ""

    def outcome(state, code, *, unknown=False, transient=False, usage=None, decision=None):
        return TransportOutcome(
            call_id=request.call_id, generation=request.generation,
            request_hash=correlation_hash, requested_model=request.requested_model,
            state=state, code=code, invocation_started=invocation_started,
            remote_outcome_unknown=unknown, transient_error=transient,
            cleanup_pending=cleanup_pending,
            elapsed_seconds=max(0.0, time.monotonic() - started_at), usage=usage, decision=decision,
        )

    if cancel_event is not None and cancel_event.is_set():
        return outcome("cancelled", "cancelled")
    if (isinstance(remaining_seconds, bool) or not isinstance(remaining_seconds, (int, float)) or
        not math.isfinite(remaining_seconds)):
        return outcome("rejected", "admission_invalid")
    if remaining_seconds <= 0:
        return outcome("rejected", "deadline_expired")
    try:
        context = _decode_request(request)
    except (ValueError, TypeError, KeyError, IndexError, OverflowError, RecursionError):
        return outcome("rejected", "admission_invalid")
    if not isinstance(provider, MetaProvider):
        return outcome("rejected", "model_provider_error")
    try:
        available = provider.available
    except Exception:
        return outcome("rejected", "model_provider_error")
    if not available:
        return outcome("rejected", "model_unavailable")
    deadline = started_at + min(remaining_seconds, request.timeout_seconds, request.limits.max_model_seconds)

    async def call_once():
        nonlocal invocation_started
        # Check in the invocation task immediately before MetaProvider builds its
        # request, eliminating a queued-task model-drift window before its first await.
        if provider.model != request.requested_model:
            raise ProviderError("model_identity_mismatch")
        if cancel_event is not None and cancel_event.is_set():
            raise _CancelledBeforeInvocation
        timeout = deadline - time.monotonic()
        if timeout <= 0:
            raise _ExpiredBeforeInvocation
        invocation_started = True
        return await provider.complete(context, output_tokens=request.limits.max_output_tokens,
                                       timeout_seconds=timeout)

    def observe(owned):
        if task_observer is not None:
            try:
                task_observer(owned)
            except (Exception, asyncio.CancelledError):
                raise _TaskObservationError from None

    try:
        task = asyncio.create_task(call_once())
        observe(task)
        waiting = {task}
        if cancel_event is not None:
            cancellation = asyncio.create_task(cancel_event.wait())
            observe(cancellation)
            waiting.add(cancellation)
        done, _ = await asyncio.wait(waiting, timeout=max(0, deadline - time.monotonic()),
                                     return_when=asyncio.FIRST_COMPLETED)
        if cancel_event is not None and cancel_event.is_set():
            cleanup_pending = not await _settle_cancelled(task, request.settlement_seconds)
            return outcome("cancelled", "cancelled", unknown=invocation_started)
        if task not in done:
            cleanup_pending = not await _settle_cancelled(task, request.settlement_seconds)
            return outcome("uncertain" if invocation_started else "rejected",
                           "model_timeout" if invocation_started else "deadline_expired",
                           unknown=invocation_started, transient=invocation_started)
        completion = await task
        checked = validate_completion(completion, requested_model=request.requested_model,
                                      input_allowance=request.admission.input_allowance, limits=request.limits)
        if checked.outcome == "accepted":
            return outcome("success", "accepted", usage=completion.usage, decision=checked.decision)
        if checked.outcome == "model_usage_unknown":
            return outcome("uncertain", checked.outcome, unknown=True)
        return outcome("rejected", checked.outcome, usage=completion.usage)
    except asyncio.CancelledError:
        if task is not None:
            cleanup_pending = not await _settle_cancelled(task, request.settlement_seconds)
        return outcome("cancelled", "cancelled", unknown=invocation_started)
    except _CancelledBeforeInvocation:
        return outcome("cancelled", "cancelled")
    except _ExpiredBeforeInvocation:
        return outcome("rejected", "deadline_expired")
    except ProviderError as error:
        code = error.code if isinstance(error.code, str) else "model_provider_error"
        if code in {"model_unavailable", "model_identity_mismatch"}:
            return outcome("rejected", code)
        http = re.fullmatch(r"model_http_([1-5][0-9]{2})", code)
        if http:
            status = int(http.group(1))
            if 400 <= status < 500 and status != 408:
                return outcome("rejected", code, transient=status == 429)
            return outcome("uncertain", code, unknown=invocation_started, transient=status == 408 or status >= 500)
        if code not in {"model_transport_error", "model_response_invalid", "model_response_too_large"}:
            code = "model_provider_error"
        return outcome("uncertain", code, unknown=invocation_started, transient=bool(error.transient))
    except Exception:
        # A trusted observer can fail before the first await. Retain ownership
        # and cancel any already-created provider task before it can start IO.
        if task is not None and not task.done():
            cleanup_pending = not await _settle_cancelled(task, request.settlement_seconds)
        return outcome("uncertain" if invocation_started else "rejected",
                       "model_provider_error", unknown=invocation_started)
    finally:
        if cancellation is not None:
            # This is only a local Event waiter, not the provider. Avoid a new
            # cancellation point after a final outcome has been constructed.
            cancellation.cancel()
            cancellation.add_done_callback(_discard_terminal)
