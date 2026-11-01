"""Pure planner decisions; callers own clocks, leases, admission and persistence.

Nothing here performs I/O, increments counters, refunds reservations, or calls a
provider/runner. Execution registry membership is deliberately not planner support.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from decimal import Decimal
import hashlib
import json
import math
import re
from types import MappingProxyType
from typing import Literal

from pydantic import Field, ValidationError

from veriforge.contracts import (
    AttemptResult, BehavioralContract, ExecutionBudget, ExecutionStatus, Experiment,
    ProjectRevision, Verdict, WireModel, canonical_hash,
)
from .models import (
    ExperimentProposal, Feedback, InvestigationLimits, PlannerContext, PlannerDecision,
    Pricing, ProviderCompletion, StopProposal,
)
from .provider import encode_payload, request_payload

STEPS_VERSION = "planner.steps.v1"
FIFO_ADAPTER = "rtl.fifo.v1"
INVALID_CORRECTION = "Proposal rejected. Use only legal typed operations, reviewed requirement IDs, reset before traffic, and the supplied cycle/width limits."
DUPLICATE_CORRECTION = "Repeated proposal rejected. Propose different stimulus or stop."


class PlannerStepError(ValueError):
    """Fixed safe codes only; never include invalid content or raw parse errors."""

    def __init__(self, code: str):
        self.code = code
        super().__init__(code)


class PlannerCapability(WireModel):
    adapter_id: Literal["rtl.fifo.v1"] = FIFO_ADAPTER
    steps_version: Literal["planner.steps.v1"] = STEPS_VERSION
    prompt_version: Literal["vf-planner-1"] = "vf-planner-1"
    decision_schema_hash: str


def _schema_hash() -> str:
    # Preserve the existing controller's byte-for-byte schema hash convention.
    return hashlib.sha256(json.dumps(PlannerDecision.model_json_schema(), sort_keys=True).encode()).hexdigest()


PLANNER_CAPABILITIES: Mapping[str, PlannerCapability] = MappingProxyType({
    FIFO_ADAPTER: PlannerCapability(decision_schema_hash=_schema_hash()),
})


def require_planner_capability(adapter_id: str) -> PlannerCapability:
    if not isinstance(adapter_id, str):
        raise PlannerStepError("planner_unsupported")
    try:
        return PLANNER_CAPABILITIES[adapter_id]
    except KeyError:
        raise PlannerStepError("planner_unsupported") from None


def validate_binding(revision: ProjectRevision, contract: BehavioralContract, *,
                     adapter_id: str = FIFO_ADAPTER) -> None:
    require_planner_capability(adapter_id)
    if not isinstance(revision, ProjectRevision) or not isinstance(contract, BehavioralContract):
        raise PlannerStepError("planner_binding_invalid")
    if revision.adapter != adapter_id or contract.target != "fifo":
        raise PlannerStepError("planner_unsupported")
    if contract.review_status != "reviewed":
        raise PlannerStepError("contract_not_reviewed")


def _count(value: int) -> None:
    if type(value) is not int or value < 0:
        raise PlannerStepError("planner_state_invalid")


def _time(value: float) -> None:
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
        raise PlannerStepError("planner_state_invalid")


def control_stop_reason(*, control: str, cancelled: bool, stopped: bool) -> str | None:
    if cancelled:
        return "cancelled"
    if stopped:
        return "stopped"
    return "paused" if control != "RUNNING" else None


def admission_stop_reason(limits: InvestigationLimits, *, rounds_used: int,
                          reserved_cycles: int, remaining_wall_seconds: float) -> str | None:
    _count(rounds_used)
    _count(reserved_cycles)
    _time(remaining_wall_seconds)
    if rounds_used >= limits.max_rounds:
        return "round_limit"
    if remaining_wall_seconds <= 0:
        return "wall_time_limit"
    if reserved_cycles >= limits.max_total_cycles:
        return "cycle_limit"
    return None


def proposal_stop_reason(limits: InvestigationLimits, *, invalid_proposals: int,
                         duplicate_proposals: int) -> str | None:
    _count(invalid_proposals)
    _count(duplicate_proposals)
    if invalid_proposals >= limits.max_invalid_proposals:
        return "invalid_proposal_limit"
    if duplicate_proposals >= limits.max_duplicate_proposals:
        return "no_progress"
    return None


def transient_retry_allowed(limits: InvestigationLimits, *, transient_errors: int) -> bool:
    _count(transient_errors)
    return transient_errors < limits.max_transient_errors


def build_context(contract: BehavioralContract, limits: InvestigationLimits, *,
                  reserved_cycles: int, feedback: Sequence[Feedback],
                  correction: str | None = None, adapter_id: str = FIFO_ADAPTER) -> PlannerContext:
    """Feedback must come from validated_feedback / trusted persisted receipts."""
    require_planner_capability(adapter_id)
    if not isinstance(contract, BehavioralContract) or contract.review_status != "reviewed":
        raise PlannerStepError("contract_not_reviewed")
    _count(reserved_cycles)
    remaining = limits.max_total_cycles - reserved_cycles
    if remaining <= 0:
        raise PlannerStepError("cycle_limit")
    if correction not in (None, INVALID_CORRECTION, DUPLICATE_CORRECTION):
        raise PlannerStepError("correction_invalid")
    return PlannerContext(
        contract=contract, remaining_cycles=remaining,
        max_cycles_per_experiment=min(remaining, limits.max_cycles_per_experiment),
        feedback=tuple(feedback[-limits.max_feedback_items:]), correction=correction,
    )


class ModelAdmission(WireModel):
    """A proposed reservation, not an applied ledger change or provider receipt."""

    request_body: bytes = Field(repr=False, exclude=True)
    request_hash: str
    schema_hash: str
    input_allowance: int
    reserved_tokens: int
    reserved_cost_usd: Decimal | None


def prepare_model_call(context: PlannerContext, model: str, limits: InvestigationLimits, *,
                       reserved_tokens: int, reserved_cost_usd: Decimal | None,
                       pricing: Pricing | None, adapter_id: str = FIFO_ADAPTER) -> ModelAdmission:
    capability = require_planner_capability(adapter_id)
    _count(reserved_tokens)
    expected_context = build_context(
        context.contract, limits, reserved_cycles=limits.max_total_cycles - context.remaining_cycles,
        feedback=context.feedback, correction=context.correction, adapter_id=adapter_id,
    )
    if context != expected_context:
        raise PlannerStepError("planner_context_invalid")
    if pricing is not None and (not isinstance(reserved_cost_usd, Decimal) or
                                not reserved_cost_usd.is_finite() or reserved_cost_usd < 0):
        raise PlannerStepError("planner_state_invalid")
    if limits.max_cost_usd is not None and pricing is None:
        raise PlannerStepError("price_unknown")
    body = encode_payload(request_payload(context, model, limits.max_output_tokens))
    if len(body) > limits.max_input_bytes:
        raise PlannerStepError("input_size_limit")
    input_allowance = len(body) + 1024
    reservation = input_allowance + limits.max_output_tokens
    if reserved_tokens + reservation > limits.max_model_tokens:
        raise PlannerStepError("model_token_limit")
    cost = pricing.cost(input_allowance, limits.max_output_tokens) if pricing else None
    if limits.max_cost_usd is not None and reserved_cost_usd + cost > limits.max_cost_usd:
        raise PlannerStepError("cost_limit")
    return ModelAdmission(request_body=body, request_hash=hashlib.sha256(body).hexdigest(),
                          schema_hash=capability.decision_schema_hash, input_allowance=input_allowance,
                          reserved_tokens=reservation, reserved_cost_usd=cost)


class CompletionDecision(WireModel):
    outcome: Literal["accepted", "model_usage_unknown", "model_usage_limit_violated",
                     "model_identity_mismatch", "invalid_proposal"]
    decision: PlannerDecision | None = None


def validate_completion(completion: ProviderCompletion, *, requested_model: str,
                        input_allowance: int, limits: InvestigationLimits,
                        adapter_id: str = FIFO_ADAPTER) -> CompletionDecision:
    require_planner_capability(adapter_id)
    _count(input_allowance)
    if completion.usage is None:
        return CompletionDecision(outcome="model_usage_unknown")
    if (completion.usage.input_tokens > input_allowance or
        completion.usage.output_tokens > limits.max_output_tokens):
        return CompletionDecision(outcome="model_usage_limit_violated")
    if completion.model != requested_model:
        return CompletionDecision(outcome="model_identity_mismatch")
    try:
        if completion.finish_reason != "stop":
            raise ValueError
        decision = PlannerDecision.model_validate_json(completion.content)
    except (ValidationError, ValueError):
        return CompletionDecision(outcome="invalid_proposal")
    return CompletionDecision(outcome="accepted", decision=decision)


def decision_stop_reason(decision: PlannerDecision) -> str | None:
    return "model_stopped" if isinstance(decision.proposal, StopProposal) else None


def build_experiment(proposal: ExperimentProposal, *, experiment_id: str,
                     revision: ProjectRevision, contract: BehavioralContract,
                     limits: InvestigationLimits, reserved_cycles: int,
                     remaining_wall_seconds: float, adapter_id: str = FIFO_ADAPTER) -> Experiment:
    validate_binding(revision, contract, adapter_id=adapter_id)
    _time(remaining_wall_seconds)
    _count(reserved_cycles)
    remaining = limits.max_total_cycles - reserved_cycles
    if remaining <= 0:
        raise PlannerStepError("cycle_limit")
    if not isinstance(proposal, ExperimentProposal):
        raise PlannerStepError("invalid_proposal")
    if not set(proposal.objective_ids) <= {r.requirement_id for r in contract.requirements}:
        raise PlannerStepError("invalid_proposal")
    try:
        return Experiment(
            experiment_id=experiment_id, fixture_id=revision.fixture_id, contract_id=contract.contract_id,
            objective_ids=proposal.objective_ids, seed=proposal.seed, operations=proposal.operations,
            parameters=contract.parameters,
            budget=ExecutionBudget(max_cycles=min(remaining, limits.max_cycles_per_experiment),
                                   wall_time_seconds=float(min(limits.max_execution_seconds,
                                                               max(0.001, remaining_wall_seconds)))),
        )
    except ValidationError:
        raise PlannerStepError("invalid_proposal") from None


def proposal_fingerprint(proposal: ExperimentProposal, *, adapter_id: str = FIFO_ADAPTER) -> str:
    require_planner_capability(adapter_id)
    if not isinstance(proposal, ExperimentProposal):
        raise PlannerStepError("invalid_proposal")
    return canonical_hash(proposal)


def validate_result(result: AttemptResult, *, experiment: Experiment, revision: ProjectRevision,
                    contract: BehavioralContract, adapter_id: str = FIFO_ADAPTER) -> None:
    validate_binding(revision, contract, adapter_id=adapter_id)
    if not isinstance(result, AttemptResult):
        raise PlannerStepError("executor_result_invalid")
    if (experiment.fixture_id != revision.fixture_id or experiment.parameters != contract.parameters or
        experiment.contract_id != contract.contract_id):
        raise PlannerStepError("result_identity_mismatch")
    if result.experiment_id != experiment.experiment_id:
        raise PlannerStepError("result_identity_mismatch")
    if result.identity is not None:
        identity = result.identity
        if (identity.source_hash != revision.source_hash or identity.checker_hash != contract.checker.sha256 or
            identity.contract_hash != canonical_hash(contract) or identity.experiment_hash != canonical_hash(experiment)):
            raise PlannerStepError("result_identity_mismatch")
    requirements = {r.requirement_id for r in contract.requirements}
    if (not set(experiment.objective_ids) <= requirements or
        not set(result.requirement_coverage) <= requirements or result.failure_signature is not None and
        result.failure_signature.requirement_id not in requirements):
        raise PlannerStepError("result_requirement_mismatch")
    if result.usage.cycles > experiment.budget.max_cycles:
        raise PlannerStepError("executor_cycle_limit_violated")


def validated_feedback(result: AttemptResult, *, experiment: Experiment,
                       revision: ProjectRevision, contract: BehavioralContract,
                       artifact_ids: Mapping[str, str] | None = None,
                       adapter_id: str = FIFO_ADAPTER) -> Feedback:
    """Optional mapping is trusted DB artifact identity, never client-supplied paths."""
    validate_result(result, experiment=experiment, revision=revision, contract=contract, adapter_id=adapter_id)
    if result.execution_status in {ExecutionStatus.QUEUED, ExecutionStatus.PREPARING,
                                   ExecutionStatus.RUNNING, ExecutionStatus.FINALIZING}:
        raise PlannerStepError("feedback_not_finalized")
    feedback = Feedback.from_result(result)
    if artifact_ids is not None:
        if set(artifact_ids) != set(feedback.artifact_ids) or any(
            not isinstance(value, str) or len(value) > 128 or
            re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.:-]*", value) is None for value in artifact_ids.values()
        ):
            raise PlannerStepError("feedback_artifact_mismatch")
        feedback = feedback.model_copy(update={"artifact_ids": tuple(artifact_ids[key] for key in feedback.artifact_ids)})
    return feedback


def execution_stop_reason(result: AttemptResult, limits: InvestigationLimits) -> str | None:
    """Apply only after validate_result; this policy does not authenticate a result."""
    if result.execution_status != ExecutionStatus.COMPLETED:
        return "execution_unavailable"
    if result.verdict in {Verdict.DESIGN_MISMATCH, Verdict.PROPERTY_FAILURE}:
        return "finding_observed" if limits.stop_on_finding else None
    return "execution_blocked" if result.verdict != Verdict.PASS_WITHIN_SCOPE else None
