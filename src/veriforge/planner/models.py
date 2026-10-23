"""Planner-only types. Model proposals contain no commands, verdicts, or budgets."""

from __future__ import annotations

from decimal import Decimal
from typing import Annotated, Literal

from pydantic import Field, StrictInt

from veriforge.contracts import (
    AttemptResult, BehavioralContract, Experiment, FailureSignature, FifoOperation, WireModel,
)


class ExperimentProposal(WireModel):
    kind: Literal["experiment"]
    objective_ids: Annotated[tuple[str, ...], Field(min_length=1, max_length=100)]
    seed: Annotated[StrictInt, Field(ge=0, le=2**32 - 1)]
    operations: Annotated[tuple[FifoOperation, ...], Field(min_length=1, max_length=1000)]


class StopProposal(WireModel):
    kind: Literal["stop"]
    reason: Annotated[str, Field(min_length=1, max_length=500)]


class PlannerDecision(WireModel):
    proposal: Annotated[ExperimentProposal | StopProposal, Field(discriminator="kind")]


class Pricing(WireModel):
    """Operator-supplied current USD rates; None elsewhere means unknown, not free."""

    input_usd_per_million: Annotated[Decimal, Field(ge=0)]
    output_usd_per_million: Annotated[Decimal, Field(ge=0)]

    def cost(self, input_tokens: int, output_tokens: int) -> Decimal:
        return (self.input_usd_per_million * input_tokens +
                self.output_usd_per_million * output_tokens) / Decimal(1_000_000)


class InvestigationLimits(WireModel):
    max_rounds: Annotated[StrictInt, Field(ge=1, le=100)] = 15
    max_total_cycles: Annotated[StrictInt, Field(ge=1, le=10_000_000)] = 100_000
    max_cycles_per_experiment: Annotated[StrictInt, Field(ge=1, le=1_000_000)] = 10_000
    max_wall_seconds: Annotated[float, Field(gt=0, le=3600)] = 300.0
    max_execution_seconds: Annotated[float, Field(gt=0, le=3600)] = 30.0
    max_model_seconds: Annotated[float, Field(gt=0, le=120)] = 30.0
    max_input_bytes: Annotated[StrictInt, Field(ge=1, le=1_000_000)] = 64_000
    max_output_tokens: Annotated[StrictInt, Field(ge=1, le=32_000)] = 4096
    max_model_tokens: Annotated[StrictInt, Field(ge=1, le=100_000_000)] = 1_000_000
    max_cost_usd: Annotated[Decimal, Field(gt=0)] | None = None
    max_invalid_proposals: Annotated[StrictInt, Field(ge=1, le=10)] = 2
    max_transient_errors: Annotated[StrictInt, Field(ge=1, le=10)] = 2
    max_duplicate_proposals: Annotated[StrictInt, Field(ge=1, le=10)] = 2
    max_feedback_items: Annotated[StrictInt, Field(ge=1, le=20)] = 5
    stop_on_finding: bool = True


class ModelUsage(WireModel):
    input_tokens: Annotated[StrictInt, Field(ge=0)]
    output_tokens: Annotated[StrictInt, Field(ge=0)]


class ProviderCompletion(WireModel):
    content: Annotated[str, Field(max_length=256_000, repr=False)]
    model: Annotated[str, Field(min_length=1, max_length=128)]
    usage: ModelUsage | None = None
    finish_reason: Annotated[str, Field(max_length=64)]


class Feedback(WireModel):
    """Bounded checker-derived summary; no source labels, raw logs, or credentials."""

    experiment_id: str
    execution_status: str
    verdict: str | None
    coverage: tuple[str, ...]
    failure_signature: FailureSignature | None
    artifact_ids: tuple[str, ...]

    @classmethod
    def from_result(cls, result: AttemptResult) -> Feedback:
        return cls(
            experiment_id=result.experiment_id,
            execution_status=result.execution_status.value,
            verdict=result.verdict.value if result.verdict else None,
            coverage=result.requirement_coverage,
            failure_signature=result.failure_signature,
            artifact_ids=tuple(a.artifact_id for a in result.artifacts),
        )


class PlannerContext(WireModel):
    prompt_version: Literal["vf-planner-1"] = "vf-planner-1"
    contract: BehavioralContract
    remaining_cycles: int
    max_cycles_per_experiment: int
    feedback: tuple[Feedback, ...] = ()
    correction: str | None = None


class ModelCallRecord(WireModel):
    round: int
    model: str
    request_hash: str
    schema_hash: str
    latency_seconds: float
    reserved_tokens: int
    usage: ModelUsage | None = None
    cost_usd: Decimal | None = None
    outcome: str


class InvestigationReport(WireModel):
    stop_reason: str
    experiments: tuple[Experiment, ...]
    results: tuple[AttemptResult, ...]
    model_calls: tuple[ModelCallRecord, ...]
    reserved_cycles: int
    reserved_model_tokens: int
    reserved_cost_usd: Decimal | None
    price_status: Literal["known", "unknown"]
