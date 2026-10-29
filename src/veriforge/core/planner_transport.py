"""One committed durable Meta intent, with no broker, retry, or credential setup.

Service-owned synchronous session/time/control factories are trusted inputs. Each
database transaction closes before any await. Provider tasks never own a Session
or settlement authority; only this invocation can attempt a fenced settlement.
"""
from __future__ import annotations

import asyncio
from dataclasses import dataclass
from decimal import Decimal
import math
import re
import time

from sqlalchemy import select

from veriforge.planner.durable_transport import (
    TransportOutcome, invoke_admitted, reconstruct_admitted_request,
)
from veriforge.planner.models import InvestigationLimits, PlannerContext, ProviderCompletion
from veriforge.planner.provider import MetaProvider
from veriforge.planner.steps import validate_completion
from . import planner_rounds as rounds
from . import planner_cleanup as cleanup
from .database import PlannerReservation, PlannerRound, PlannerState


@dataclass(frozen=True)
class ModelIntentResult:
    round_id: str
    generation: int | None = None
    claimed: bool = False
    applied: bool = False
    reason: str = "not_claimed"
    cleanup_pending: bool = False
    remote_outcome_unknown: bool = False
    outcome: TransportOutcome | None = None


class _LocalCleanupOwner:
    """In-process task ownership; only exact local completion grants cleanup ack.

    Runs on the invocation's event loop. Callbacks open fresh Sessions, contain
    their failures, and have no round finalization or provider invocation path.
    """
    def __init__(self, ticket, sessions, now):
        self.ticket, self.sessions, self.now = ticket, sessions, now
        self.tasks = set()
        self.sealed = self.acknowledged = self.observation_failed = False

    def track(self, task):
        try:
            self.tasks.add(task)
            task.add_done_callback(self._ready)
        except Exception:
            self.observation_failed = True
            raise

    def observe(self, task):
        try:
            self.track(task)
        except (Exception, asyncio.CancelledError):
            self.observation_failed = True
            raise

    def _ready(self, task=None):
        if (not self.sealed or self.acknowledged or self.observation_failed or
                any(not item.done() for item in self.tasks)):
            return
        try:
            with self.sessions.begin() as session:
                acknowledged = cleanup.acknowledge_settled(session, self.ticket,
                    proof="cooperative_tasks_settled", at=self.now())
            # Do not acknowledge in memory until the transaction fully commits.
            self.acknowledged = acknowledged
        except (Exception, asyncio.CancelledError):
            # Lost service/DB ownership remains durable pending/unknown. No
            # exception text, false proof or unhandled callback is emitted.
            pass

    def seal(self):
        self.sealed = True
        self._ready()


def _control(read):
    try:
        return "RUNNING" if read() == "RUNNING" else "STOPPED"
    except Exception:
        return "STOPPED"


def _discard(task):
    if not task.cancelled():
        task.exception()


async def _settle(task, seconds):
    """A repeated caller cancellation cannot extend local cleanup indefinitely."""
    deadline = time.monotonic() + seconds
    while not task.done() and time.monotonic() < deadline:
        try:
            await asyncio.wait({task}, timeout=max(0, deadline - time.monotonic()))
        except asyncio.CancelledError:
            continue
    if not task.done():
        task.cancel()
        task.add_done_callback(_discard)
        return None
    try:
        return task.result()
    except (Exception, asyncio.CancelledError):
        return None


def _completion(outcome, request):
    """Revalidate even frozen/model_construct'ed transport data before DB use."""
    checked = TransportOutcome.model_validate(outcome.model_dump(mode="json"))
    if ((checked.call_id, checked.generation, checked.request_hash, checked.requested_model) !=
            (request.call_id, request.generation, request.admission.request_hash, request.requested_model)
            or not math.isfinite(checked.elapsed_seconds)):
        raise ValueError("Transport correlation mismatch")
    completion = None
    if checked.state == "success":
        if checked.code != "accepted" or checked.cleanup_pending or checked.transient_error:
            raise ValueError("Invalid transport success")
        completion = ProviderCompletion(content=checked.decision.model_dump_json(), model=checked.requested_model,
                                        usage=checked.usage, finish_reason="stop")
        if validate_completion(completion, requested_model=request.requested_model,
                input_allowance=request.admission.input_allowance, limits=request.limits).outcome != "accepted":
            raise ValueError("Invalid transport completion")
    elif checked.code == "accepted" or checked.state == "cancelled" and checked.code != "cancelled":
        raise ValueError("Invalid transport failure")
    if checked.state == "uncertain" and not checked.remote_outcome_unknown:
        raise ValueError("Uncertain outcome requires explicit remote uncertainty")
    if checked.cleanup_pending and not checked.remote_outcome_unknown:
        raise ValueError("Pending cleanup cannot establish a known remote outcome")
    return checked, completion


def _failure_code(code):
    if code in {"model_transport_error", "model_response_invalid", "model_unavailable", "model_response_too_large"}:
        return code
    if re.fullmatch(r"model_http_[1-5][0-9]{2}", code):
        return code
    return "model_response_invalid"


async def run_model_intent(round_id, *, sessions, now, control, provider,
                           cancel_event=None, poll_seconds=0.1, settlement_seconds=1.0):
    """Claim/commit once, invoke once, then settle using a new fenced transaction.

    This adapter dispatches only explicit ``meta`` strategy through MetaProvider.
    ``mock_planner`` is refused before claim. ``now`` returns aware service UTC
    time; ``control`` returns RUNNING to authorize work. Caller cancellation is
    settled as data, with finite local cleanup and explicit remote uncertainty.
    A crash after the claim commit is for existing round recovery, never resend.
    """
    if (isinstance(poll_seconds, bool) or not isinstance(poll_seconds, (int, float)) or
            not math.isfinite(poll_seconds) or not 0 < poll_seconds <= 1 or
            isinstance(settlement_seconds, bool) or not isinstance(settlement_seconds, (int, float)) or
            not math.isfinite(settlement_seconds) or not 0 < settlement_seconds <= 5):
        raise ValueError("Invalid transport polling or cleanup bound")
    if cancel_event is not None and cancel_event.is_set():
        return ModelIntentResult(round_id, reason="cancelled")
    began = time.monotonic()
    with sessions.begin() as session:
        row = session.get(PlannerRound, round_id)
        state = session.get(PlannerState, row.campaign_id) if row else None
        if state is None:
            return ModelIntentResult(round_id)
        if state.strategy != "meta":
            return ModelIntentResult(round_id, reason="strategy_mismatch")
        if not isinstance(provider, MetaProvider):
            return ModelIntentResult(round_id, reason="provider_invalid")
        lease = rounds.claim_model_round(session, round_id, at=now(), control=_control(control))
        if lease is None:
            return ModelIntentResult(round_id)
        # Snapshot only immutable metadata; no attached ORM object crosses IO.
        policy = dict(state.policy)
        reservation = session.scalar(select(PlannerReservation).where(
            PlannerReservation.round_id == round_id, PlannerReservation.attempt_id.is_(None)))
        metadata = dict(request_hash=lease.request_hash, schema_hash=lease.schema_hash,
            input_allowance=lease.input_allowance, reserved_tokens=reservation.reserved_tokens,
            reserved_cost_usd=Decimal(reservation.reserved_cost_usd) if reservation.reserved_cost_usd is not None else None)
        ticket = cleanup.record_pending(session, lease, at=now(), control=_control(control))
        if ticket is None:
            return ModelIntentResult(round_id, lease.generation, True, reason="cleanup_registration_refused",
                                     cleanup_pending=True)
    # Commit failure propagates above and cannot reach provider invocation.
    owner = _LocalCleanupOwner(ticket, sessions, now)
    revoked = asyncio.Event()
    outcome = None
    reason, unknown, cleanup_pending = "admission_invalid", False, False
    try:
        request = reconstruct_admitted_request(call_id=lease.round_id, generation=lease.generation,
            requested_model=lease.requested_model, admission_metadata=metadata,
            context=PlannerContext.model_validate(lease.context), limits=InvestigationLimits.model_validate(policy),
            timeout_seconds=lease.timeout_seconds, settlement_seconds=settlement_seconds)
        if request.admission.request_body != lease.request_body or lease.strategy != "meta":
            raise ValueError("Committed admitted request differs")
    except (ValueError, TypeError, KeyError, ArithmeticError):
        request = None
    if request is not None:
        # A post-commit revocation/deadline must not reach even the first IO.
        with sessions.begin() as session:
            valid = rounds.heartbeat_model_round(session, lease, at=now(), control=_control(control))
        if (not valid or cancel_event is not None and cancel_event.is_set() or
                asyncio.current_task().cancelling()):
            # Claim/commit hooks may revoke authorization synchronously. Consume
            # pending caller cancellation before constructing any provider task.
            try:
                await asyncio.sleep(0)
            except asyncio.CancelledError:
                pass
            with sessions.begin() as session:
                applied = rounds.fail_model_round(session, lease, code="model_transport_error", at=now(),
                    transient=False, remote_unknown=False, control=_control(control))
            owner.seal()  # No provider/wrapper tasks were created.
            return ModelIntentResult(round_id, lease.generation, True, applied,
                                     "cancelled" if valid else "lease_revoked",
                                     cleanup_pending=not owner.acknowledged)
        remaining = lease.timeout_seconds - (time.monotonic() - began)
        task = asyncio.create_task(invoke_admitted(request, provider=provider,
            remaining_seconds=remaining, cancel_event=revoked, task_observer=owner.observe))
        # A task cannot run until our first await. Include the wrapper before
        # discovery starts; seal only after inspecting the returned task scope.
        try:
            owner.observe(task)
        except (Exception, asyncio.CancelledError):
            task.cancel()
            await _settle(task, settlement_seconds)
            with sessions.begin() as session:
                applied = rounds.fail_model_round(session, lease, code="model_transport_error", at=now(),
                    transient=False, remote_unknown=False, control=_control(control))
            return ModelIntentResult(round_id, lease.generation, True, applied,
                                     "cleanup_observation_failed", cleanup_pending=True)
        interrupted = False
        try:
            while not task.done():
                if cancel_event is not None and cancel_event.is_set():
                    revoked.set()
                if revoked.is_set():
                    outcome = await _settle(task, min(5, settlement_seconds + 0.05))
                    break
                done, _ = await asyncio.wait({task}, timeout=poll_seconds)
                if task not in done:
                    with sessions.begin() as session:
                        valid = rounds.heartbeat_model_round(session, lease, at=now(), control=_control(control))
                    if not valid:
                        revoked.set()
            else:
                outcome = task.result()
            if outcome is None and task.done():
                outcome = task.result()
        except asyncio.CancelledError:
            interrupted = True
            revoked.set()
            # Deliver directly as well: cancellation cannot wait for another poll.
            task.cancel()
            outcome = await _settle(task, min(5, settlement_seconds + 0.05))
        except Exception:
            interrupted = True
            revoked.set()
            task.cancel()
            outcome = await _settle(task, min(5, settlement_seconds + 0.05))
        # The wrapper's finally cancels its Event waiter without awaiting it.
        # Permit that cooperative local task to become done before reporting
        # durable cleanup. A still-live provider child remains unresolved.
        try:
            await asyncio.sleep(0)
        except asyncio.CancelledError:
            interrupted = True
            revoked.set()
        try:
            outcome, completion = _completion(outcome, request)
            cleanup_pending = outcome.cleanup_pending
            if cleanup_pending and len(owner.tasks) == 1:
                owner.observation_failed = True
            unknown = outcome.remote_outcome_unknown
            reason = outcome.code
            # A revoked/cancelled step never applies a success, even if it races.
            if interrupted or revoked.is_set():
                completion = None
                unknown = unknown or outcome.invocation_started
                reason = "cancelled" if interrupted else "lease_revoked"
        except (ValueError, TypeError, AttributeError):
            completion = None
            reason, unknown = "outcome_invalid", True
            cleanup_pending = not task.done() or (
                isinstance(outcome, TransportOutcome) and outcome.cleanup_pending is True)
            if cleanup_pending and len(owner.tasks) == 1:
                owner.observation_failed = True
            outcome = None
    else:
        completion = None
        owner.seal()
    # Only the round API can grant durable authority, after all lease checks.
    with sessions.begin() as session:
        if cancel_event is not None and cancel_event.is_set():
            completion = None
            unknown = unknown or bool(outcome and outcome.invocation_started)
            reason = "cancelled"
        if completion is not None:
            applied = rounds.finish_model_round(session, lease, completion, at=now(), control=_control(control))
        else:
            known_transient = bool(outcome and outcome.transient_error and not unknown and
                outcome.state == "rejected" and (reason == "model_http_429" or
                re.fullmatch(r"model_http_5[0-9]{2}", reason)))
            applied = rounds.fail_model_round(session, lease, code=_failure_code(reason), at=now(),
                transient=known_transient, remote_unknown=unknown, control=_control(control))
    owner.seal()
    cleanup_pending = not owner.acknowledged
    return ModelIntentResult(round_id, lease.generation, True, applied, reason,
                             cleanup_pending, unknown, outcome)
