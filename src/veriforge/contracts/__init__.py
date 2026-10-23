"""Version 1 immutable wire contracts. No executable commands cross this boundary."""

from __future__ import annotations

import hashlib
import json
from enum import StrEnum
from pathlib import PurePosixPath
from typing import Annotated, Literal, Protocol
from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field, StrictBool, StrictInt, field_validator, model_validator

Identifier = Annotated[str, Field(min_length=1, max_length=128, pattern=r"^[A-Za-z0-9][A-Za-z0-9_.:-]*$")]
Sha256 = Annotated[str, Field(pattern=r"^[0-9a-f]{64}$")]
NonNegative = Annotated[StrictInt, Field(ge=0)]
CycleCount = Annotated[StrictInt, Field(ge=1, le=1_000_000)]
FixtureId = Literal["fifo_correct", "fifo_reset_bug", "fifo_full_bug", "fifo_order_bug"]


class WireModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, validate_default=True, allow_inf_nan=False)


def canonical_hash(model: BaseModel) -> str:
    """Hash the exact serialized record; callers choose which identity record to hash."""
    payload = json.dumps(model.model_dump(mode="json"), sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _safe_path(value: str) -> str:
    if not value or "\\" in value or ":" in value or "\x00" in value:
        raise ValueError("path must be a relative POSIX snapshot path")
    path = PurePosixPath(value)
    if path.is_absolute() or any(part in ("", ".", "..") for part in value.split("/")):
        raise ValueError("path must remain within snapshot")
    return value


class SourceFile(WireModel):
    path: Annotated[str, Field(max_length=512)]
    sha256: Sha256
    _path = field_validator("path")(_safe_path)


class ProjectRevision(WireModel):
    schema_version: Literal["1"] = "1"
    project_id: Identifier
    revision_id: Identifier
    source_hash: Sha256
    fixture_id: FixtureId
    top_module: Annotated[str, Field(pattern=r"^[A-Za-z_][A-Za-z0-9_]*$", max_length=128)] = "fifo"
    sources: Annotated[tuple[SourceFile, ...], Field(min_length=1, max_length=100)]
    adapter: Literal["rtl.fifo.v1"] = "rtl.fifo.v1"

    @model_validator(mode="after")
    def unique_sources(self):
        if len({s.path for s in self.sources}) != len(self.sources):
            raise ValueError("duplicate source path")
        return self


class CheckerIdentity(WireModel):
    checker_id: Identifier
    version: Identifier
    sha256: Sha256


class Requirement(WireModel):
    requirement_id: Identifier
    description: Annotated[str, Field(min_length=1, max_length=2000)]


class FifoParameters(WireModel):
    width: Annotated[StrictInt, Field(ge=1, le=64)] = 8
    depth: Annotated[StrictInt, Field(ge=2, le=1024)] = 4


class BehavioralContract(WireModel):
    schema_version: Literal["1"] = "1"
    contract_id: Literal["fifo-v1"] = "fifo-v1"
    version: Identifier = "1"
    target: Literal["fifo"] = "fifo"
    review_status: Literal["draft", "reviewed"] = "draft"
    checker: CheckerIdentity
    parameters: FifoParameters = Field(default_factory=FifoParameters)
    requirements: Annotated[tuple[Requirement, ...], Field(min_length=1, max_length=100)]
    reset_priority: Literal["reset_over_read_write"] = "reset_over_read_write"
    acceptance: Literal["pre_edge_flags"] = "pre_edge_flags"
    read_semantics: Literal["oldest_on_accepted_edge"] = "oldest_on_accepted_edge"
    assumptions: Annotated[tuple[Annotated[str, Field(min_length=1, max_length=2000)], ...], Field(max_length=100)] = ()

    @model_validator(mode="after")
    def unique_requirements(self):
        ids = [r.requirement_id for r in self.requirements]
        if len(ids) != len(set(ids)):
            raise ValueError("duplicate requirement ID")
        return self


class ResetOperation(WireModel):
    op: Literal["reset"] = "reset"
    cycles: CycleCount = 1


class WriteOperation(WireModel):
    op: Literal["write"] = "write"
    value: Annotated[StrictInt, Field(ge=0, lt=2**64)]


class ReadOperation(WireModel):
    op: Literal["read"] = "read"


class IdleOperation(WireModel):
    op: Literal["idle"] = "idle"
    cycles: CycleCount = 1


class CycleOperation(WireModel):
    op: Literal["cycle"] = "cycle"
    reset: StrictBool = False
    write: StrictBool = False
    read: StrictBool = False
    value: Annotated[StrictInt, Field(ge=0, lt=2**64)] = 0


FifoOperation = Annotated[ResetOperation | WriteOperation | ReadOperation | IdleOperation | CycleOperation, Field(discriminator="op")]


class ExecutionBudget(WireModel):
    max_cycles: CycleCount = 10_000
    wall_time_seconds: Annotated[float, Field(gt=0, le=3600, strict=True)] = 30.0
    max_artifact_bytes: Annotated[StrictInt, Field(ge=1024, le=1_073_741_824)] = 10_485_760


class Experiment(WireModel):
    schema_version: Literal["1"] = "1"
    experiment_id: Identifier
    fixture_id: FixtureId
    contract_id: Literal["fifo-v1"] = "fifo-v1"
    target: Literal["fifo"] = "fifo"
    objective_ids: Annotated[tuple[Identifier, ...], Field(min_length=1, max_length=100)]
    seed: Annotated[StrictInt, Field(ge=0, le=2**32 - 1)] = 0
    operations: Annotated[tuple[FifoOperation, ...], Field(min_length=1, max_length=100_000)]
    parameters: FifoParameters = Field(default_factory=FifoParameters)
    budget: ExecutionBudget = Field(default_factory=ExecutionBudget)

    @property
    def cycle_count(self) -> int:
        return sum(op.cycles if isinstance(op, (ResetOperation, IdleOperation)) else 1 for op in self.operations)

    @model_validator(mode="after")
    def legal_stimulus(self):
        if self.cycle_count > self.budget.max_cycles:
            raise ValueError("stimulus exceeds max_cycles")
        reset_seen = False
        for op in self.operations:
            if isinstance(op, ResetOperation) or isinstance(op, CycleOperation) and op.reset:
                reset_seen = True
            if isinstance(op, (WriteOperation, CycleOperation)) and op.value >= 2**self.parameters.width:
                raise ValueError("value exceeds FIFO width")
            traffic = isinstance(op, (WriteOperation, ReadOperation)) or isinstance(op, CycleOperation) and (op.read or op.write)
            if traffic and not reset_seen:
                raise ValueError("reset required before traffic")
        if not reset_seen:
            raise ValueError("experiment requires a reset")
        return self


class ExecutionStatus(StrEnum):
    QUEUED = "queued"
    PREPARING = "preparing"
    RUNNING = "running"
    FINALIZING = "finalizing"
    COMPLETED = "completed"
    INTERRUPTED = "interrupted"
    CANCELLED = "cancelled"
    INFRASTRUCTURE_ERROR = "infrastructure_error"


class Verdict(StrEnum):
    PASS_WITHIN_SCOPE = "pass_within_scope"
    DESIGN_MISMATCH = "design_mismatch"
    PROPERTY_FAILURE = "property_failure"
    BUILD_ERROR = "build_error"
    HARNESS_ERROR = "harness_error"
    TIMEOUT = "timeout"
    RESOURCE_LIMIT = "resource_limit"
    UNSUPPORTED = "unsupported"
    INCONCLUSIVE = "inconclusive"


class EvidenceIdentity(WireModel):
    source_hash: Sha256
    checker_hash: Sha256
    contract_hash: Sha256
    experiment_hash: Sha256
    environment_hash: Sha256


class ToolVersion(WireModel):
    name: Identifier
    version: Annotated[str, Field(min_length=1, max_length=256)]


class ArtifactReference(WireModel):
    artifact_id: Identifier
    path: Annotated[str, Field(max_length=512)]
    sha256: Sha256
    size_bytes: NonNegative
    media_type: Annotated[str, Field(min_length=1, max_length=128)]
    _path = field_validator("path")(_safe_path)


class TraceSample(WireModel):
    cycle: NonNegative
    operation_index: NonNegative
    write_accepted: StrictBool = False
    read_accepted: StrictBool = False
    expected_data: NonNegative | None = None
    observed_data: NonNegative | None = None
    expected_full: StrictBool | None = None
    observed_full: StrictBool | None = None
    expected_empty: StrictBool | None = None
    observed_empty: StrictBool | None = None


class FailureSignature(WireModel):
    requirement_id: Identifier
    failure_class: Identifier
    first_cycle: NonNegative
    expected: Annotated[str, Field(max_length=2000)]
    observed: Annotated[str, Field(max_length=2000)]
    signature_hash: Sha256


class ResourceUsage(WireModel):
    cycles: NonNegative = 0
    wall_time_seconds: Annotated[float, Field(ge=0, strict=True)] = 0.0
    artifact_bytes: NonNegative = 0


class AttemptResult(WireModel):
    schema_version: Literal["1"] = "1"
    attempt_id: Identifier
    experiment_id: Identifier
    execution_status: ExecutionStatus
    verdict: Verdict | None = None
    identity: EvidenceIdentity | None = None
    tool_versions: Annotated[tuple[ToolVersion, ...], Field(max_length=100)] = ()
    trace: Annotated[tuple[TraceSample, ...], Field(max_length=1_000_000)] = ()
    artifacts: Annotated[tuple[ArtifactReference, ...], Field(max_length=1000)] = ()
    failure_signature: FailureSignature | None = None
    requirement_coverage: Annotated[tuple[Identifier, ...], Field(max_length=1000)] = ()
    usage: ResourceUsage = Field(default_factory=ResourceUsage)
    uncertainties: Annotated[tuple[Annotated[str, Field(min_length=1, max_length=2000)], ...], Field(max_length=100)] = ()

    @model_validator(mode="after")
    def consistent_outcome(self):
        active = {ExecutionStatus.QUEUED, ExecutionStatus.PREPARING, ExecutionStatus.RUNNING, ExecutionStatus.FINALIZING}
        design = {Verdict.PASS_WITHIN_SCOPE, Verdict.DESIGN_MISMATCH, Verdict.PROPERTY_FAILURE}
        if self.execution_status in active and self.verdict is not None:
            raise ValueError("in-flight attempt cannot have a verdict")
        if self.execution_status == ExecutionStatus.COMPLETED and self.verdict is None:
            raise ValueError("completed attempt requires a verdict")
        if self.execution_status != ExecutionStatus.COMPLETED and self.verdict in design:
            raise ValueError("uncompleted attempt cannot claim a design verdict")
        if self.verdict in design and self.identity is None:
            raise ValueError("design verdict requires evidence identity")
        if self.verdict in {Verdict.DESIGN_MISMATCH, Verdict.PROPERTY_FAILURE} and self.failure_signature is None:
            raise ValueError("design failure requires a signature")
        if self.failure_signature is not None and self.verdict not in {Verdict.DESIGN_MISMATCH, Verdict.PROPERTY_FAILURE}:
            raise ValueError("failure signature is reserved for design failures")
        return self


class Finding(WireModel):
    schema_version: Literal["1"] = "1"
    finding_id: Identifier
    project_id: Identifier
    revision_id: Identifier
    contract_id: Identifier
    status: Literal["observed", "reproduced", "reduced", "candidate_patch_available", "validated_under_checks", "unresolved"] = "observed"
    signature: FailureSignature
    identity: EvidenceIdentity
    attempt_ids: Annotated[tuple[Identifier, ...], Field(min_length=1, max_length=10_000)]
    artifact_ids: Annotated[tuple[Identifier, ...], Field(max_length=1000)] = ()
    summary: Annotated[str, Field(min_length=1, max_length=4000)]
    uncertainties: tuple[Annotated[str, Field(max_length=2000)], ...] = ()


class ExperimentRunner(Protocol):
    def __call__(self, experiment: Experiment, output_dir: Path) -> AttemptResult: ...


__all__ = [
    "ArtifactReference", "AttemptResult", "BehavioralContract", "CheckerIdentity", "CycleOperation",
    "EvidenceIdentity", "ExecutionBudget", "ExecutionStatus", "Experiment", "ExperimentRunner",
    "FailureSignature", "Finding", "FixtureId", "FifoOperation", "FifoParameters", "IdleOperation",
    "ProjectRevision", "ReadOperation", "Requirement", "ResetOperation", "ResourceUsage", "SourceFile",
    "ToolVersion", "TraceSample", "Verdict", "WireModel", "WriteOperation", "canonical_hash",
]
