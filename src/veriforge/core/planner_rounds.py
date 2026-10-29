"""Transactional FIFO planner steps; no provider I/O, clock lookup or hidden loop.

Call each mutation in a caller-owned transaction. `at` is trusted service time.
The next provider adapter transports the frozen ModelLease; this module never
reads keys or invokes a provider. A claimed uncertain call is not retried.
"""
from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation
import hashlib
import re

from sqlalchemy import and_, or_, select

from veriforge.adapters import get_adapter
from veriforge.contracts import AttemptResult, BehavioralContract, ProjectRevision, SourceFile
from veriforge.planner import steps
from veriforge.planner.models import Feedback, InvestigationLimits, Pricing, ProviderCompletion
from . import service
from .database import (Attempt, AttemptExperiment, Campaign, ExperimentRecord, PlannerFeedback,
    PlannerIntent, PlannerModelCall, PlannerReservation, PlannerRound, PlannerState, identifier)
from .planner_experiments import admit_experiment, resolve_attempt_experiment, validate_record

TERMINAL = {"settled", "cancelled", "interrupted"}


@dataclass(frozen=True)
class ModelLease:
    campaign_id: str
    round_id: str
    generation: int
    lease: str
    requested_model: str
    request_hash: str
    schema_hash: str
    input_allowance: int
    request_body: bytes = field(repr=False)
    context: dict = field(repr=False)
    timeout_seconds: float
    strategy: str


def _utc(value):
    return value.replace(tzinfo=timezone.utc) if value.tzinfo is None else value.astimezone(timezone.utc)


def _at(value):
    if not isinstance(value, datetime) or value.tzinfo is None:
        raise ValueError("Planner service time must be timezone-aware")
    return value.astimezone(timezone.utc)


def _snapshot(state):
    return (InvestigationLimits.model_validate(state.policy),
            ProjectRevision.model_validate(state.binding["revision"]),
            BehavioralContract.model_validate(state.binding["contract"]),
            Pricing.model_validate(state.pricing) if state.pricing is not None else None)


def totals(session, campaign_id):
    entries = list(session.scalars(select(PlannerReservation).where(PlannerReservation.campaign_id == campaign_id)))
    for row in entries:
        try:
            amounts = (Decimal(row.reserved_seconds), Decimal(row.reserved_cost_usd) if row.reserved_cost_usd is not None else Decimal(0))
        except (InvalidOperation, TypeError):
            raise ValueError("Invalid immutable reservation amount") from None
        if (type(row.reserved_tokens) is not int or type(row.reserved_cycles) is not int or row.reserved_tokens < 0 or row.reserved_cycles < 0 or
            any(not amount.is_finite() or amount < 0 for amount in amounts)):
            raise ValueError("Invalid immutable reservation amount")
    return {"tokens": sum(row.reserved_tokens for row in entries), "cycles": sum(row.reserved_cycles for row in entries),
            "seconds": sum((Decimal(row.reserved_seconds) for row in entries), Decimal(0)),
            "cost": sum((Decimal(row.reserved_cost_usd) for row in entries if row.reserved_cost_usd is not None), Decimal(0))}


def _intent(session, state, kind, *, round_id=None):
    prior = session.scalar(select(PlannerIntent).where(PlannerIntent.campaign_id == state.campaign_id, PlannerIntent.generation == state.generation, PlannerIntent.kind == kind))
    if prior is not None:
        return prior
    row = PlannerIntent(campaign_id=state.campaign_id, generation=state.generation, kind=kind, round_id=round_id)
    session.add(row)
    session.flush()
    return row


def _close(session, campaign, state, reason, at, *, status="completed", preserve_parent=False):
    active = session.scalar(select(Attempt.id).where(Attempt.campaign_id == campaign.id, Attempt.execution_status.in_(["queued", "running"])))
    if active and not preserve_parent:
        service.cancel(session, campaign.id)
    for round_row in session.scalars(select(PlannerRound).where(PlannerRound.campaign_id == campaign.id, PlannerRound.status.in_(["queued", "running"]))):
        call = session.get(PlannerModelCall, round_row.id)
        uncertain = round_row.status == "running"
        round_row.status, round_row.lease, round_row.finished_at = "uncertain" if uncertain else "cancelled", identifier(), at
        if call is not None and call.outcome is None:
            call.outcome = "remote_outcome_unknown" if uncertain else "not_invoked"
    if not preserve_parent:
        campaign.execution_status, campaign.verdict = status, None
    state.phase = "cancelled" if status == "cancelled" else "interrupted" if status in {"interrupted", "infrastructure_error"} else "settled"
    state.stop_reason, state.generation = reason, state.generation + 1
    service.emit(session, campaign, "planner.settled", {"reason": reason, "phase": state.phase})


def _guard(session, campaign, state, at, control):
    if state.phase in TERMINAL:
        return False
    if at < _utc(state.started_at):
        raise ValueError("Planner service time precedes its immutable start")
    if state.steps_version != steps.require_planner_capability(campaign.adapter_id).steps_version:
        _close(session, campaign, state, "planner_version_changed", at, status="interrupted")
        return False
    if campaign.execution_status in service.TERMINAL:
        status = campaign.execution_status
        _close(session, campaign, state, "cancelled" if status == "cancelled" else "parent_settled", at, status=status, preserve_parent=True)
        return False
    if control != "RUNNING":
        _close(session, campaign, state, "paused", at, status="interrupted")
        return False
    if at >= _utc(state.deadline_at):
        _close(session, campaign, state, "wall_time_limit", at, status="interrupted")
        return False
    try:
        totals(session, campaign.id)
    except ValueError:
        _close(session, campaign, state, "reservation_invalid", at, status="interrupted")
        return False
    first = session.scalar(select(ExperimentRecord).where(ExperimentRecord.campaign_id == campaign.id, ExperimentRecord.ordinal == 1))
    if first is None or get_adapter(first.adapter_id).expected_identity(validate_record(first)) != first.identity:
        _close(session, campaign, state, "provenance_changed", at, status="interrupted")
        return False
    return True


def attach_planner(session, campaign_id, *, limits, requested_model, at, strategy="mock_planner", pricing=None):
    """Adopt an idle sequential seed; charge all its prior attempts conservatively."""
    at = _at(at)
    campaign = service.lock(session, campaign_id)
    limits = InvestigationLimits.model_validate(limits.model_dump(mode="json") if isinstance(limits, InvestigationLimits) else limits)
    if not isinstance(requested_model, str) or re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.:/-]{0,127}", requested_model) is None:
        raise ValueError("Invalid configured model identity")
    if strategy not in {"mock_planner", "meta"}:
        raise ValueError("Unknown planner strategy")
    pricing = Pricing.model_validate(pricing.model_dump(mode="json") if isinstance(pricing, Pricing) else pricing) if pricing is not None else None
    prior = session.get(PlannerState, campaign_id)
    if prior is not None:
        if (prior.policy != limits.model_dump(mode="json") or prior.requested_model != requested_model or prior.strategy != strategy or
            prior.pricing != (pricing.model_dump(mode="json") if pricing else None)):
            raise service.Conflict("Planner policy is already frozen")
        return prior
    capability = steps.require_planner_capability(campaign.adapter_id)
    if campaign.completion_policy != "sequential" or campaign.execution_status != "running":
        raise service.Conflict("Planner adoption requires a running sequential parent")
    records = list(session.scalars(select(ExperimentRecord).where(ExperimentRecord.campaign_id == campaign_id)))
    attempts = list(session.scalars(select(Attempt).where(Attempt.campaign_id == campaign_id).order_by(Attempt.created_at, Attempt.id)))
    if len(records) != 1 or not attempts or any(a.execution_status in {"queued", "running"} for a in attempts):
        raise service.Conflict("Planner adoption requires only a settled seed experiment")
    first = records[0]
    experiment = validate_record(first)
    descriptor = get_adapter(first.adapter_id)
    if descriptor.expected_identity(experiment) != first.identity:
        raise service.Conflict("Seed execution provenance changed")
    source = descriptor.fixture_source(experiment.fixture_id)
    revision = ProjectRevision(project_id=campaign.project_id, revision_id="snapshot-" + first.id, source_hash=first.identity["source_hash"], fixture_id=experiment.fixture_id,
        sources=(SourceFile(path=descriptor.source_artifact, sha256=source["source_file_hash"]),))
    contract = BehavioralContract.model_validate(first.contract_snapshot)
    steps.validate_binding(revision, contract, adapter_id=campaign.adapter_id)
    cycles = sum(validate_record(resolve_attempt_experiment(session, attempt)).budget.max_cycles for attempt in attempts)
    seconds = sum((Decimal(str(validate_record(resolve_attempt_experiment(session, attempt)).budget.wall_time_seconds)) for attempt in attempts), Decimal(0))
    if cycles > limits.max_total_cycles:
        raise service.Conflict("Seed attempts exceed the proposed cycle budget")
    if seconds > Decimal(str(limits.max_wall_seconds)):
        raise service.Conflict("Seed attempts exceed the proposed time allowance")
    state = PlannerState(campaign_id=campaign_id, policy=limits.model_dump(mode="json"), binding={"revision": revision.model_dump(mode="json"), "contract": contract.model_dump(mode="json")},
        pricing=pricing.model_dump(mode="json") if pricing else None, requested_model=requested_model, strategy=strategy, steps_version=capability.steps_version,
        started_at=at, deadline_at=at + timedelta(seconds=limits.max_wall_seconds))
    session.add(state)
    session.flush()
    for attempt in attempts:
        record = resolve_attempt_experiment(session, attempt)
        session.add(PlannerReservation(campaign_id=campaign_id, admission_key="execution:" + attempt.id, attempt_id=attempt.id,
            reserved_cycles=validate_record(record).budget.max_cycles, reserved_tokens=0, reserved_seconds=str(validate_record(record).budget.wall_time_seconds)))
    _intent(session, state, "advance")
    service.emit(session, campaign, "planner.attached", {"strategy": strategy, "steps_version": capability.steps_version})
    return state


def _validated_execution_feedback(attempt_id, result, record, revision, contract):
    if len({item.artifact_id for item in result.artifacts}) != len(result.artifacts):
        raise steps.PlannerStepError("feedback_artifact_mismatch")
    mapping = {item.artifact_id: "artifact:" + hashlib.sha256((attempt_id + ":" + item.artifact_id).encode()).hexdigest() for item in result.artifacts}
    return steps.validated_feedback(result, experiment=validate_record(record), revision=revision, contract=contract, artifact_ids=mapping)


def _consume_results(session, campaign, state, at):
    limits, revision, contract, _ = _snapshot(state)
    changed = False
    for attempt in session.scalars(select(Attempt).where(Attempt.campaign_id == campaign.id).order_by(Attempt.created_at, Attempt.id)):
        reserved = session.scalar(select(PlannerReservation.id).where(PlannerReservation.campaign_id == campaign.id, PlannerReservation.attempt_id == attempt.id))
        if reserved is None:
            _close(session, campaign, state, "unreserved_execution", at, status="interrupted")
            return False
        prior = session.get(PlannerFeedback, attempt.id)
        if prior is not None:
            if prior.result_hash != service.digest(attempt.result):
                _close(session, campaign, state, "finalized_result_changed", at, status="interrupted")
                return False
            continue
        if attempt.execution_status in {"queued", "running"}:
            continue
        record = resolve_attempt_experiment(session, attempt)
        try:
            if attempt.execution_status == "completed":
                result = AttemptResult.model_validate(attempt.result)
                feedback = _validated_execution_feedback(attempt.id, result, record, revision, contract)
                stop = steps.execution_stop_reason(result, limits)
            else:
                feedback = Feedback(experiment_id=record.wire_id, execution_status=attempt.execution_status, verdict=None, coverage=(), failure_signature=None, artifact_ids=())
                stop = "execution_unavailable"
        except (ValueError, TypeError):
            _close(session, campaign, state, "executor_result_invalid", at, status="interrupted")
            return False
        session.add(PlannerFeedback(attempt_id=attempt.id, campaign_id=campaign.id, experiment_id=record.id, result_hash=service.digest(attempt.result), feedback=feedback.model_dump(mode="json"), created_at=at))
        session.flush()
        changed = True
        if stop:
            _close(session, campaign, state, stop, at)
            return False
    if changed:
        state.phase, state.generation = "ready", state.generation + 1
        _intent(session, state, "advance")
    return True


def advance(session, campaign_id, *, at, control="RUNNING", expected_generation=None):
    at = _at(at)
    campaign = service.lock(session, campaign_id)
    state = session.get(PlannerState, campaign_id)
    if state is None:
        raise service.NotFound(campaign_id)
    if expected_generation is not None and expected_generation != state.generation:
        return None
    if not _guard(session, campaign, state, at, control) or not _consume_results(session, campaign, state, at):
        return None
    if state.phase in {"round_queued", "model_running"}:
        return session.scalar(select(PlannerRound).where(PlannerRound.campaign_id == campaign_id, PlannerRound.generation == state.generation))
    if state.not_before is not None and at < _utc(state.not_before):
        return None
    active = session.scalar(select(Attempt.id).where(Attempt.campaign_id == campaign_id, Attempt.execution_status.in_(["queued", "running"])))
    if active:
        state.phase = "waiting_execution"
        return None
    limits, _, contract, pricing = _snapshot(state)
    rounds = list(session.scalars(select(PlannerRound).where(PlannerRound.campaign_id == campaign_id)))
    reserved = totals(session, campaign_id)
    remaining = (_utc(state.deadline_at) - at).total_seconds()
    time_allowance = min(remaining, float(Decimal(str(limits.max_wall_seconds)) - reserved["seconds"]))
    stop = steps.admission_stop_reason(limits, rounds_used=len(rounds), reserved_cycles=reserved["cycles"], remaining_wall_seconds=remaining)
    stop = stop or steps.proposal_stop_reason(limits, invalid_proposals=state.invalid_count, duplicate_proposals=state.duplicate_count)
    stop = stop or ("wall_time_limit" if time_allowance <= 0 else None)
    if stop:
        _close(session, campaign, state, stop, at)
        return None
    receipts = list(session.scalars(select(PlannerFeedback).join(Attempt, Attempt.id == PlannerFeedback.attempt_id).where(PlannerFeedback.campaign_id == campaign_id).order_by(Attempt.created_at, Attempt.id)))
    try:
        context = steps.build_context(contract, limits, reserved_cycles=reserved["cycles"], feedback=tuple(Feedback.model_validate(row.feedback) for row in receipts), correction=state.correction)
        admission = steps.prepare_model_call(context, state.requested_model, limits, reserved_tokens=reserved["tokens"], reserved_cost_usd=reserved["cost"] if pricing else None, pricing=pricing)
    except steps.PlannerStepError as error:
        _close(session, campaign, state, error.code, at)
        return None
    state.generation += 1
    row = PlannerRound(campaign_id=campaign_id, ordinal=len(rounds) + 1, generation=state.generation)
    session.add(row)
    session.flush()
    session.add(PlannerModelCall(round_id=row.id, requested_model=state.requested_model, request_body=admission.request_body, request_hash=admission.request_hash,
        schema_hash=admission.schema_hash, input_allowance=admission.input_allowance, context=context.model_dump(mode="json")))
    session.add(PlannerReservation(campaign_id=campaign_id, round_id=row.id, admission_key="model:" + row.id, reserved_cycles=0,
        reserved_tokens=admission.reserved_tokens, reserved_cost_usd=str(admission.reserved_cost_usd) if admission.reserved_cost_usd is not None else None,
        reserved_seconds=str(min(limits.max_model_seconds, time_allowance))))
    state.phase = "round_queued"
    _intent(session, state, "model", round_id=row.id)
    service.emit(session, campaign, "planner.round_queued", {"round_id": row.id, "ordinal": row.ordinal})
    return row


def claim_model_round(session, round_id, *, at, control="RUNNING"):
    at = _at(at)
    row = session.get(PlannerRound, round_id)
    if row is None:
        return None
    campaign = service.lock(session, row.campaign_id)
    state = session.get(PlannerState, campaign.id)
    row = session.get(PlannerRound, round_id)
    if not _guard(session, campaign, state, at, control) or not _consume_results(session, campaign, state, at) or row.status != "queued" or state.phase != "round_queued" or row.generation != state.generation:
        return None
    call = session.get(PlannerModelCall, row.id)
    if (hashlib.sha256(call.request_body).hexdigest() != call.request_hash or call.requested_model != state.requested_model or
        call.schema_hash != steps.require_planner_capability(campaign.adapter_id).decision_schema_hash):
        _close(session, campaign, state, "admitted_request_changed", at, status="interrupted")
        return None
    row.status, row.heartbeat_at, call.started_at = "running", at, at
    state.phase = "model_running"
    limits, _, _, _ = _snapshot(state)
    reservation = session.scalar(select(PlannerReservation).where(PlannerReservation.round_id == row.id, PlannerReservation.attempt_id.is_(None)))
    timeout = min(float(Decimal(reservation.reserved_seconds)), (_utc(state.deadline_at) - at).total_seconds())
    call.deadline_at = at + timedelta(seconds=timeout)
    return ModelLease(campaign.id, row.id, row.generation, row.lease, call.requested_model, call.request_hash, call.schema_hash,
        call.input_allowance, bytes(call.request_body), deepcopy(call.context), timeout, state.strategy)


def _valid_lease(session, lease, at, control):
    campaign = service.lock(session, lease.campaign_id)
    state = session.get(PlannerState, campaign.id)
    if not _guard(session, campaign, state, at, control):
        return campaign, state, None, None
    row = session.get(PlannerRound, lease.round_id)
    call = session.get(PlannerModelCall, lease.round_id)
    if (row is None or call is None or row.campaign_id != campaign.id or row.status != "running" or state.phase != "model_running" or
        row.generation != state.generation or lease.generation != row.generation or row.lease != lease.lease or
        call.request_hash != lease.request_hash or call.requested_model != lease.requested_model or call.schema_hash != lease.schema_hash or
        call.request_body != lease.request_body or call.context != lease.context or call.input_allowance != lease.input_allowance or state.strategy != lease.strategy):
        return campaign, state, None, None
    if call.started_at is not None and at < _utc(call.started_at):
        return campaign, state, None, None
    if call.deadline_at is None or at >= _utc(call.deadline_at):
        _close(session, campaign, state, "model_call_uncertain", at, status="interrupted")
        return campaign, state, None, None
    return campaign, state, row, call


def heartbeat_model_round(session, lease, *, at, control="RUNNING"):
    at = _at(at)
    _, _, row, _ = _valid_lease(session, lease, at, control)
    if row is None:
        return False
    row.heartbeat_at = at
    return True


def finish_model_round(session, lease, completion, *, at, control="RUNNING"):
    at = _at(at)
    campaign, state, row, call = _valid_lease(session, lease, at, control)
    if row is None:
        return False
    completion = ProviderCompletion.model_validate(completion.model_dump(mode="json") if isinstance(completion, ProviderCompletion) else completion)
    limits, revision, contract, pricing = _snapshot(state)
    checked = steps.validate_completion(completion, requested_model=call.requested_model, input_allowance=call.input_allowance, limits=limits)
    call.outcome, row.finished_at = checked.outcome, at
    call.usage = completion.usage.model_dump(mode="json") if completion.usage else None
    call.returned_model = completion.model if completion.model == call.requested_model else None
    call.actual_cost_usd = str(pricing.cost(completion.usage.input_tokens, completion.usage.output_tokens)) if pricing and completion.usage else None
    if checked.outcome not in {"accepted", "invalid_proposal"}:
        row.status = "failed"
        _close(session, campaign, state, checked.outcome, at)
        return True
    if checked.decision is not None and steps.decision_stop_reason(checked.decision):
        row.status = "stopped"
        _close(session, campaign, state, "model_stopped", at)
        return True
    reserved = totals(session, campaign.id)
    remaining = (_utc(state.deadline_at) - at).total_seconds()
    remaining = min(remaining, float(Decimal(str(limits.max_wall_seconds)) - reserved["seconds"]))
    if remaining <= 0:
        row.status = "failed"
        _close(session, campaign, state, "wall_time_limit", at)
        return True
    experiment = None
    try:
        if checked.decision is None:
            raise steps.PlannerStepError("invalid_proposal")
        experiment = steps.build_experiment(checked.decision.proposal, experiment_id="round-" + row.id, revision=revision, contract=contract,
            limits=limits, reserved_cycles=reserved["cycles"], remaining_wall_seconds=remaining)
    except steps.PlannerStepError:
        state.invalid_count += 1
        state.correction, row.status, call.outcome = steps.INVALID_CORRECTION, "invalid", "invalid_proposal"
    if experiment is not None:
        if experiment.budget.wall_time_seconds > remaining:
            row.status = "failed"
            _close(session, campaign, state, "wall_time_limit", at)
            return True
        fingerprint = steps.proposal_fingerprint(checked.decision.proposal)
        prior = session.scalar(select(PlannerRound.id).where(PlannerRound.campaign_id == campaign.id, PlannerRound.id != row.id, PlannerRound.fingerprint == fingerprint, PlannerRound.status == "accepted"))
        row.fingerprint = fingerprint
        if prior is not None:
            state.duplicate_count += 1
            state.correction, row.status, call.outcome = steps.DUPLICATE_CORRECTION, "duplicate", "duplicate_proposal"
        else:
            record = admit_experiment(session, campaign.id, experiment.model_dump(mode="json"), "round:" + row.id)
            attempt_id = session.scalar(select(AttemptExperiment.attempt_id).where(AttemptExperiment.experiment_id == record.id))
            session.add(PlannerReservation(campaign_id=campaign.id, round_id=row.id, attempt_id=attempt_id, admission_key="execution:" + attempt_id,
                reserved_cycles=experiment.budget.max_cycles, reserved_tokens=0, reserved_seconds=str(experiment.budget.wall_time_seconds)))
            row.experiment_id, row.status = record.id, "accepted"
            state.correction, state.phase, state.generation, state.not_before = None, "waiting_execution", state.generation + 1, None
            service.emit(session, campaign, "planner.experiment_admitted", {"round_id": row.id, "experiment_record_id": record.id, "attempt_id": attempt_id})
            return True
    state.phase, state.generation, state.not_before = "ready", state.generation + 1, at
    _intent(session, state, "advance")
    return True


def fail_model_round(session, lease, *, code, at, transient=False, remote_unknown=False, control="RUNNING"):
    if not isinstance(code, str) or (code not in {"model_transport_error", "model_response_invalid", "model_unavailable", "model_response_too_large"} and re.fullmatch(r"model_http_[1-5][0-9]{2}", code) is None):
        raise ValueError("Unsafe provider outcome code")
    if type(transient) is not bool or type(remote_unknown) is not bool:
        raise ValueError("Provider outcome classification must be explicit booleans")
    at = _at(at)
    campaign, state, row, call = _valid_lease(session, lease, at, control)
    if row is None:
        return False
    row.finished_at = at
    if remote_unknown:
        call.outcome = "remote_outcome_unknown"
        _close(session, campaign, state, "model_call_uncertain", at, status="interrupted")
        return True
    classified_transient = code == "model_http_429" or bool(re.fullmatch(r"model_http_5[0-9]{2}", code))
    transient = transient and classified_transient
    row.status, call.outcome = "failed", code
    state.transient_count += int(transient)
    limits, _, _, _ = _snapshot(state)
    if transient and steps.transient_retry_allowed(limits, transient_errors=state.transient_count):
        state.phase, state.generation = "ready", state.generation + 1
        state.not_before = at + timedelta(seconds=min(2.0, 0.25 * 2 ** state.transient_count))
        _intent(session, state, "advance")
    else:
        _close(session, campaign, state, code, at)
    return True


def finish_execution_step(session, attempt_id, lease, result, *, at, infrastructure_error=False, control="RUNNING"):
    attempt = session.get(Attempt, attempt_id)
    if attempt is None:
        return False
    campaign = service.lock(session, attempt.campaign_id)
    state = session.get(PlannerState, campaign.id)
    at = _at(at)
    if state is None or not _guard(session, campaign, state, at, control):
        return False
    if not service.authorized(session, attempt_id, lease):
        return False
    if not infrastructure_error:
        try:
            checked = AttemptResult.model_validate(result)
            _, revision, contract, _ = _snapshot(state)
            record = resolve_attempt_experiment(session, attempt)
            _validated_execution_feedback(attempt_id, checked, record, revision, contract)
        except (ValueError, TypeError):
            _close(session, campaign, state, "executor_result_invalid", at, status="interrupted")
            return False
    if not service.finalize(session, attempt_id, lease, result, infrastructure_error):
        return False
    _consume_results(session, campaign, state, at)
    return True


def cancel_investigation(session, campaign_id, *, at):
    campaign = service.lock(session, campaign_id)
    state = session.get(PlannerState, campaign_id)
    if state is None:
        raise service.NotFound(campaign_id)
    if state.phase not in TERMINAL:
        _close(session, campaign, state, "cancelled", _at(at), status="cancelled")
    return state


def claim_execution_step(session, attempt_id, *, at, control="RUNNING"):
    """Claim one exact current planner child; caller first gates global resource.

    No reservation/admission is created here. Never use generic claim as a
    fallback when a planner claim is stale, unreserved or otherwise rejected.
    """
    at = _at(at)
    attempt = session.get(Attempt, attempt_id)
    if attempt is None:
        return None
    campaign = service.lock(session, attempt.campaign_id)
    state = session.get(PlannerState, campaign.id)
    if state is None or not _guard(session, campaign, state, at, control):
        return None
    attempt = session.get(Attempt, attempt_id)
    if state.phase != "waiting_execution" or attempt.execution_status != "queued":
        return None
    if not _consume_results(session, campaign, state, at):
        return None
    binding = session.get(AttemptExperiment, attempt_id)
    row = session.scalar(select(PlannerRound).where(PlannerRound.campaign_id == campaign.id,
        PlannerRound.experiment_id == binding.experiment_id)) if binding else None
    if (state.phase != "waiting_execution" or row is None or row.status != "accepted" or
            state.generation != row.generation + 1):
        return None
    return service.claim(session, attempt_id)


def recover_campaign(session, campaign_id, *, at, control="RUNNING", stale_seconds=120):
    """Recover one parent under caller's resource->campaign lock ordering."""
    at = _at(at)
    if type(stale_seconds) is not int or not 1 <= stale_seconds <= 86400:
        raise ValueError("Invalid planner recovery age")
    campaign = service.lock(session, campaign_id)
    state = session.get(PlannerState, campaign.id)
    if state is None or state.phase in TERMINAL:
        return False
    if not _guard(session, campaign, state, at, control):
        return True
    row = session.scalar(select(PlannerRound).where(PlannerRound.campaign_id == campaign_id, PlannerRound.status == "running"))
    call = session.get(PlannerModelCall, row.id) if row else None
    if row and ((row.heartbeat_at and _utc(row.heartbeat_at) <= at - timedelta(seconds=stale_seconds)) or (call and call.deadline_at and at >= _utc(call.deadline_at))):
        _close(session, campaign, state, "model_call_uncertain", at, status="interrupted")
        return True
    if state.phase == "waiting_execution":
        generation = state.generation
        _consume_results(session, campaign, state, at)
        return generation != state.generation
    return False


def recover_rounds(session, *, at, stale_seconds=120, control="RUNNING", limit=100):
    """Bounded legacy wrapper; driver uses one recovery transaction per parent."""
    at = _at(at)
    if type(limit) is not int or not 1 <= limit <= 100:
        raise ValueError("Invalid planner recovery batch")
    ids = list(session.scalars(select(PlannerState.campaign_id).where(PlannerState.phase.not_in(TERMINAL))
        .order_by(PlannerState.deadline_at, PlannerState.campaign_id).limit(limit)))
    recovered = 0
    for campaign_id in ids:
        recovered += int(recover_campaign(session, campaign_id, at=at, control=control, stale_seconds=stale_seconds))
    return recovered


def pending_intents(session, *, at, retry_seconds=30, limit=100):
    at = _at(at)
    if type(limit) is not int or not 1 <= limit <= 100 or type(retry_seconds) is not int or not 1 <= retry_seconds <= 3600:
        raise ValueError("Invalid planner intent batch")
    rows = session.scalars(select(PlannerIntent).join(PlannerState, PlannerState.campaign_id == PlannerIntent.campaign_id).where(PlannerIntent.generation == PlannerState.generation,
        or_(and_(PlannerIntent.kind == "model", PlannerState.phase == "round_queued"),
            and_(PlannerIntent.kind == "advance", PlannerState.phase == "ready")),
        or_(PlannerState.not_before.is_(None), PlannerState.not_before <= at),
        or_(PlannerIntent.sent_at.is_(None), PlannerIntent.sent_at <= at - timedelta(seconds=retry_seconds))
    ).order_by(PlannerIntent.id).limit(limit))
    return list(rows)


def acknowledge_intent(session, intent_id, *, at):
    row = session.get(PlannerIntent, intent_id)
    if row is None:
        return False
    row.sent_at = _at(at)
    return True
