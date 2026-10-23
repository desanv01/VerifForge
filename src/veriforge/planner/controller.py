"""Explicit sequential investigation state machine. No checker or shell capability."""

from __future__ import annotations

import asyncio
import hashlib
import json
import random
import time
from collections.abc import Awaitable, Callable
from decimal import Decimal

from pydantic import ValidationError

from veriforge.contracts import (
    AttemptResult, BehavioralContract, ExecutionBudget, ExecutionStatus,
    Experiment, ProjectRevision, Verdict, canonical_hash,
)
from .models import (
    Feedback, InvestigationLimits, InvestigationReport, ModelCallRecord,
    PlannerContext, PlannerDecision, Pricing, StopProposal,
)
from .provider import (
    PlannerProvider, ProviderError, encode_payload, request_payload,
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
        if self._cancelled.is_set():
            raise _Halt("cancelled")
        if self._stopped.is_set():
            raise _Halt("stopped")
        if self._control() != "RUNNING":
            raise _Halt("paused")

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
        if result.experiment_id != experiment.experiment_id:
            raise _Halt("result_identity_mismatch")
        if result.identity is not None:
            identity = result.identity
            if (identity.source_hash != self._revision.source_hash or
                identity.checker_hash != self._contract.checker.sha256 or
                identity.contract_hash != canonical_hash(self._contract) or
                identity.experiment_hash != canonical_hash(experiment)):
                raise _Halt("result_identity_mismatch")
        requirements = {r.requirement_id for r in self._contract.requirements}
        if (not set(result.requirement_coverage) <= requirements or
            result.failure_signature is not None and
                result.failure_signature.requirement_id not in requirements):
            raise _Halt("result_requirement_mismatch")
        if result.usage.cycles > experiment.budget.max_cycles:
            raise _Halt("executor_cycle_limit_violated")

    async def run(self) -> InvestigationReport:
        if self._used:
            raise RuntimeError("controller is single-use; create a new controller for a new run")
        self._used = True
        limits = self._limits
        deadline = time.monotonic() + limits.max_wall_seconds
        experiments: list[Experiment] = []
        results: list[AttemptResult] = []
        calls: list[ModelCallRecord] = []
        cycles = tokens = invalid = duplicates = transient = 0
        cost = Decimal(0) if self._pricing else None
        fingerprints: set[str] = set()
        correction = None
        reason = "round_limit"
        schema_hash = hashlib.sha256(json.dumps(PlannerDecision.model_json_schema(),
                                               sort_keys=True).encode()).hexdigest()
        try:
            self._check_control()
            if limits.max_cost_usd is not None and self._pricing is None:
                raise _Halt("price_unknown")
            for round_number in range(1, limits.max_rounds + 1):
                self._check_control()
                remaining_time = deadline - time.monotonic()
                if remaining_time <= 0:
                    raise _Halt("wall_time_limit")
                remaining = limits.max_total_cycles - cycles
                if remaining <= 0:
                    raise _Halt("cycle_limit")
                context = PlannerContext(
                    contract=self._contract, remaining_cycles=remaining,
                    max_cycles_per_experiment=min(remaining, limits.max_cycles_per_experiment),
                    feedback=tuple(Feedback.from_result(r) for r in results[-limits.max_feedback_items:]),
                    correction=correction,
                )
                payload = encode_payload(request_payload(context, self._provider.model,
                                                         limits.max_output_tokens))
                if len(payload) > limits.max_input_bytes:
                    raise _Halt("input_size_limit")
                # Conservative admission estimate, not claimed as tokenizer measurement.
                # Keep the full reservation even on failed calls/retries/invalid output.
                input_reservation = len(payload) + 1024
                reservation = input_reservation + limits.max_output_tokens
                if tokens + reservation > limits.max_model_tokens:
                    raise _Halt("model_token_limit")
                call_cost = (self._pricing.cost(input_reservation, limits.max_output_tokens)
                             if self._pricing else None)
                if (limits.max_cost_usd is not None and cost + call_cost > limits.max_cost_usd):
                    raise _Halt("cost_limit")
                tokens += reservation
                if call_cost is not None:
                    cost += call_cost
                start = time.monotonic()
                record = dict(round=round_number, model=self._provider.model,
                              request_hash=hashlib.sha256(payload).hexdigest(), schema_hash=schema_hash,
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
                        if transient < limits.max_transient_errors:
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
                outcome = "accepted"
                decision = None
                if usage is None:
                    outcome = "model_usage_unknown"
                elif (usage.input_tokens > input_reservation or
                      usage.output_tokens > limits.max_output_tokens):
                    outcome = "model_usage_limit_violated"
                elif completion.model != self._provider.model:
                    outcome = "model_identity_mismatch"
                else:
                    try:
                        if completion.finish_reason != "stop":
                            raise ValueError("incomplete response")
                        decision = PlannerDecision.model_validate_json(completion.content)
                    except (ValidationError, ValueError):
                        outcome = "invalid_proposal"
                calls.append(ModelCallRecord(
                    **record, latency_seconds=time.monotonic() - start, usage=usage,
                    cost_usd=(self._pricing.cost(usage.input_tokens, usage.output_tokens)
                              if self._pricing and usage else None), outcome=outcome,
                ))
                self._check_control()
                if outcome not in ("accepted", "invalid_proposal"):
                    raise _Halt(outcome)
                if decision is not None and isinstance(decision.proposal, StopProposal):
                    raise _Halt("model_stopped")
                try:
                    if decision is None:
                        raise ValueError("invalid proposal")
                    proposal = decision.proposal
                    requirements = {r.requirement_id for r in self._contract.requirements}
                    if not set(proposal.objective_ids) <= requirements:
                        raise ValueError("unknown requirement")
                    experiment = Experiment(
                        experiment_id=f"planner-{round_number}", fixture_id=self._revision.fixture_id,
                        contract_id=self._contract.contract_id,
                        objective_ids=proposal.objective_ids, seed=proposal.seed,
                        operations=proposal.operations, parameters=self._contract.parameters,
                        budget=ExecutionBudget(
                            max_cycles=context.max_cycles_per_experiment,
                            wall_time_seconds=float(min(limits.max_execution_seconds,
                                                        max(0.001, deadline - time.monotonic()))),
                        ),
                    )
                except (ValidationError, ValueError):
                    invalid += 1
                    # No raw invalid output or exception strings enter feedback/logs.
                    correction = "Proposal rejected. Use only legal typed operations, reviewed requirement IDs, reset before traffic, and the supplied cycle/width limits."
                    if calls[-1].outcome == "accepted":
                        calls[-1] = calls[-1].model_copy(update={"outcome": "invalid_proposal"})
                    if invalid >= limits.max_invalid_proposals:
                        raise _Halt("invalid_proposal_limit")
                    continue
                fingerprint = canonical_hash(proposal)
                if fingerprint in fingerprints:
                    duplicates += 1
                    correction = "Repeated proposal rejected. Propose different stimulus or stop."
                    if duplicates >= limits.max_duplicate_proposals:
                        raise _Halt("no_progress")
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
                self._check_control()
                if result.execution_status != ExecutionStatus.COMPLETED:
                    raise _Halt("execution_unavailable")
                if result.verdict in {Verdict.DESIGN_MISMATCH, Verdict.PROPERTY_FAILURE}:
                    if limits.stop_on_finding:
                        raise _Halt("finding_observed")
                elif result.verdict != Verdict.PASS_WITHIN_SCOPE:
                    raise _Halt("execution_blocked")
        except _Halt as exc:
            reason = exc.reason
        return InvestigationReport(
            stop_reason=reason, experiments=tuple(experiments), results=tuple(results),
            model_calls=tuple(calls), reserved_cycles=cycles, reserved_model_tokens=tokens,
            reserved_cost_usd=cost, price_status="known" if self._pricing else "unknown",
        )
