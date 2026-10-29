"""Explicit sequential investigation state machine. No checker or shell capability."""

from __future__ import annotations

import asyncio
import random
import time
from collections.abc import Awaitable, Callable
from decimal import Decimal

from veriforge.contracts import (
    AttemptResult, BehavioralContract, Experiment, ProjectRevision,
)
from .models import (
    Feedback, InvestigationLimits, InvestigationReport, ModelCallRecord, Pricing,
)
from .provider import (
    PlannerProvider, ProviderError,
)
from .steps import (
    DUPLICATE_CORRECTION, INVALID_CORRECTION, PlannerStepError, admission_stop_reason,
    build_context, build_experiment, control_stop_reason, decision_stop_reason, execution_stop_reason,
    prepare_model_call, proposal_fingerprint, proposal_stop_reason,
    transient_retry_allowed, validate_binding, validate_completion, validate_result, validated_feedback,
)

Executor = Callable[[Experiment], Awaitable[AttemptResult]]


class _Halt(Exception):
    def __init__(self, reason: str):
        self.reason = reason


class InvestigationController:
    """Single-use, in-memory controller; persistence/dispatch belongs to core/API.

    The trusted async executor must honor cancellation and tear down its process tree
    before returning/raising. Do not adapt blocking simulator calls with to_thread:
    cancelling a Python thread cannot terminate a simulator subprocess.
    """

    def __init__(self, provider: PlannerProvider, executor: Executor, *,
                 revision: ProjectRevision, contract: BehavioralContract,
                 limits: InvestigationLimits | None = None, pricing: Pricing | None = None,
                 control: Callable[[], str] | None = None):
        if contract.review_status != "reviewed":
            raise ValueError("planner requires a reviewed contract")
        validate_binding(revision, contract)
        self._provider = provider
        self._executor = executor
        self._revision = revision
        self._contract = contract
        self._limits = limits or InvestigationLimits()
        self._pricing = pricing
        self._control = control or (lambda: "RUNNING")
        self._cancelled = asyncio.Event()
        self._stopped = asyncio.Event()
        self._used = False

    def cancel(self) -> None:
        self._cancelled.set()

    def stop(self) -> None:
        """Stop safely at the next boundary, preserving any completed result."""
        self._stopped.set()

    def _check_control(self) -> None:
        cancelled, stopped = self._cancelled.is_set(), self._stopped.is_set()
        reason = control_stop_reason(control="RUNNING" if cancelled or stopped else self._control(),
                                     cancelled=cancelled, stopped=stopped)
        if reason:
            raise _Halt(reason)

    async def _bounded(self, operation: Awaitable, timeout: float):
        task = asyncio.ensure_future(operation)
        cancellation = asyncio.create_task(self._cancelled.wait())
        try:
            done, _ = await asyncio.wait({task, cancellation}, timeout=max(0, timeout),
                                         return_when=asyncio.FIRST_COMPLETED)
            if cancellation in done:
                raise _Halt("cancelled")
            if task not in done:
                raise _Halt("wall_time_limit")
            return await task
        finally:
            for pending in (task, cancellation):
                if not pending.done():
                    pending.cancel()
            await asyncio.gather(task, cancellation, return_exceptions=True)

    def _validate_result(self, result: AttemptResult, experiment: Experiment) -> None:
        validate_result(result, experiment=experiment, revision=self._revision, contract=self._contract)

    async def run(self) -> InvestigationReport:
        if self._used:
            raise RuntimeError("controller is single-use; create a new controller for a new run")
        self._used = True
        limits = self._limits
        deadline = time.monotonic() + limits.max_wall_seconds
        experiments: list[Experiment] = []
        results: list[AttemptResult] = []
        feedback: list[Feedback] = []
        calls: list[ModelCallRecord] = []
        cycles = tokens = invalid = duplicates = transient = 0
        cost = Decimal(0) if self._pricing else None
        fingerprints: set[str] = set()
        correction = None
        reason = "round_limit"
        try:
            self._check_control()
            if limits.max_cost_usd is not None and self._pricing is None:
                raise _Halt("price_unknown")
            for round_number in range(1, limits.max_rounds + 1):
                self._check_control()
                remaining_time = deadline - time.monotonic()
                stop = admission_stop_reason(limits, rounds_used=round_number - 1,
                                             reserved_cycles=cycles, remaining_wall_seconds=remaining_time)
                if stop:
                    raise _Halt(stop)
                context = build_context(
                    self._contract, limits, reserved_cycles=cycles, feedback=feedback,
                    correction=correction,
                )
                # Conservative admission estimate, not claimed as tokenizer measurement.
                # Keep the full reservation even on failed calls/retries/invalid output.
                requested_model = self._provider.model
                admission = prepare_model_call(context, requested_model, limits,
                                               reserved_tokens=tokens, reserved_cost_usd=cost,
                                               pricing=self._pricing)
                reservation, call_cost = admission.reserved_tokens, admission.reserved_cost_usd
                tokens += reservation
                if call_cost is not None:
                    cost += call_cost
                start = time.monotonic()
                record = dict(round=round_number, model=requested_model,
                              request_hash=admission.request_hash, schema_hash=admission.schema_hash,
                              reserved_tokens=reservation)
                try:
                    completion = await self._bounded(
                        self._provider.complete(context, output_tokens=limits.max_output_tokens,
                                                timeout_seconds=min(remaining_time, limits.max_model_seconds)),
                        min(remaining_time, limits.max_model_seconds),
                    )
                except ProviderError as exc:
                    calls.append(ModelCallRecord(**record, latency_seconds=time.monotonic() - start,
                                                 outcome=exc.code))
                    if exc.transient:
                        transient += 1
                        if transient_retry_allowed(limits, transient_errors=transient):
                            self._check_control()
                            await self._bounded(asyncio.sleep(min(2.0, 0.25 * 2**transient +
                                                                 random.uniform(0, 0.25))),
                                                deadline - time.monotonic())
                            continue
                    raise _Halt(exc.code)
                except _Halt as exc:
                    calls.append(ModelCallRecord(**record, latency_seconds=time.monotonic() - start,
                                                 outcome=exc.reason))
                    raise
                usage = completion.usage
                validated = validate_completion(completion, requested_model=requested_model,
                                                input_allowance=admission.input_allowance, limits=limits)
                outcome, decision = validated.outcome, validated.decision
                calls.append(ModelCallRecord(
                    **record, latency_seconds=time.monotonic() - start, usage=usage,
                    cost_usd=(self._pricing.cost(usage.input_tokens, usage.output_tokens)
                              if self._pricing and usage else None), outcome=outcome,
                ))
                self._check_control()
                if outcome not in ("accepted", "invalid_proposal"):
                    raise _Halt(outcome)
                if decision is not None:
                    stop = decision_stop_reason(decision)
                    if stop:
                        raise _Halt(stop)
                try:
                    if decision is None:
                        raise ValueError("invalid proposal")
                    proposal = decision.proposal
                    experiment = build_experiment(proposal, experiment_id=f"planner-{round_number}",
                                                  revision=self._revision, contract=self._contract,
                                                  limits=limits, reserved_cycles=cycles,
                                                  remaining_wall_seconds=deadline - time.monotonic())
                except ValueError:
                    invalid += 1
                    # No raw invalid output or exception strings enter feedback/logs.
                    correction = INVALID_CORRECTION
                    if calls[-1].outcome == "accepted":
                        calls[-1] = calls[-1].model_copy(update={"outcome": "invalid_proposal"})
                    stop = proposal_stop_reason(limits, invalid_proposals=invalid, duplicate_proposals=duplicates)
                    if stop:
                        raise _Halt(stop)
                    continue
                fingerprint = proposal_fingerprint(proposal)
                if fingerprint in fingerprints:
                    duplicates += 1
                    correction = DUPLICATE_CORRECTION
                    stop = proposal_stop_reason(limits, invalid_proposals=invalid, duplicate_proposals=duplicates)
                    if stop:
                        raise _Halt(stop)
                    continue
                fingerprints.add(fingerprint)
                correction = None
                self._check_control()
                if deadline <= time.monotonic():
                    raise _Halt("wall_time_limit")
                cycles += experiment.budget.max_cycles
                experiments.append(experiment)
                try:
                    result = await self._bounded(self._executor(experiment),
                                                 min(experiment.budget.wall_time_seconds,
                                                     deadline - time.monotonic()))
                except _Halt:
                    raise
                except Exception:
                    raise _Halt("executor_error") from None
                if not isinstance(result, AttemptResult):
                    raise _Halt("executor_result_invalid")
                self._validate_result(result, experiment)
                results.append(result)
                if result.execution_status.value not in {"queued", "preparing", "running", "finalizing"}:
                    feedback.append(validated_feedback(result, experiment=experiment,
                                                       revision=self._revision, contract=self._contract))
                self._check_control()
                stop = execution_stop_reason(result, limits)
                if stop:
                    raise _Halt(stop)
        except (_Halt, PlannerStepError) as exc:
            reason = exc.reason if isinstance(exc, _Halt) else exc.code
        return InvestigationReport(
            stop_reason=reason, experiments=tuple(experiments), results=tuple(results),
            model_calls=tuple(calls), reserved_cycles=cycles, reserved_model_tokens=tokens,
            reserved_cost_usd=cost, price_status="known" if self._pricing else "unknown",
        )
