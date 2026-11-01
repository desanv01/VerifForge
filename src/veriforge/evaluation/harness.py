"""Bounded local FIFO evaluation protocol; no native/provider implementation.

Journal persistence is local, bounded and preservation oriented. Unsettled
external effects require reviewed recovery; this is not a durable broker/runtime.
Only FIFO structured-contract input is supported. Real neutral transport,
cancellation, process settlement, calibrated limits and scoring are not provided.
"""
from __future__ import annotations

from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import datetime
import hashlib
import json
import os
from pathlib import Path
import random
from types import MappingProxyType
from threading import get_ident
from typing import Annotated, Literal, Protocol

from pydantic import Field, StrictBool, StrictInt, TypeAdapter, model_validator

from veriforge.contracts import (ArtifactReference, AttemptResult, BehavioralContract, EvidenceIdentity, ExecutionBudget,
    Experiment, FailureSignature, FifoParameters, ProjectRevision, ResourceUsage, canonical_hash)
from veriforge.evaluation import records as ledger
from . import storage
from veriforge.planner.models import ExperimentProposal
from veriforge.planner.steps import validate_binding, validate_result
from veriforge.rtl import oracle

MAX_BYTES, MAX_EVENTS = 8*1024*1024, 512
STRATEGIES = ("directed_manual", "constrained_random", "meta_one_pass", "meta_feedback")
Strategy = Literal["directed_manual", "constrained_random", "meta_one_pass", "meta_feedback"]
Evidence = Literal["actual", "mock", "recorded"]
N = Annotated[StrictInt, Field(ge=0, le=10**9)]
Seed = Annotated[StrictInt, Field(ge=0, le=2**32-1)]
Alias = Annotated[str, Field(pattern=r"^case-[0-9]{4}$")]


def encoded(value):
    return storage.canonical_json(value.model_dump(mode="json") if hasattr(value, "model_dump") else value)


def digest(data):
    return hashlib.sha256(data).hexdigest()


def identity(*parts):
    return "eval-"+digest(encoded(parts))[:40]


class HarnessError(ValueError):
    """Fixed diagnostics; never propagate adapter text or private binding data."""


def require(condition, code):
    if not condition:
        raise HarnessError(code)


class PublicCase(ledger.RecordModel):
    case_id: Alias
    parameters: FifoParameters
    domain: Literal["rtl"] = "rtl"
    profile: Literal["fifo-v1"] = "fifo-v1"
    objective_ids: Annotated[tuple[Literal["FIFO_BEHAVIOR"], ...], Field(min_length=1, max_length=1)] = ("FIFO_BEHAVIOR",)


class Limits(ledger.RecordModel):
    # Values are operator-frozen protocol inputs, never calibrated defaults.
    attempts: Annotated[N, Field(ge=1, le=16)]
    cycles: Annotated[N, Field(ge=1, le=16000)]
    tool_seconds: Annotated[float, Field(strict=True, gt=0, le=3600)]
    artifact_bytes: Annotated[N, Field(ge=1024, le=4*1024*1024)]
    model_tokens: Annotated[N, Field(ge=1, le=10**6)]
    per_attempt_cycles: Annotated[N, Field(ge=3, le=1000)]
    per_attempt_seconds: Annotated[float, Field(strict=True, gt=0, le=120)]
    per_attempt_artifact_bytes: Annotated[N, Field(ge=1024, le=256*1024)]
    per_model_seconds: Annotated[float, Field(strict=True, gt=0, le=120)]
    response_bytes: Annotated[N, Field(ge=128, le=64000)]
    feedback_output_tokens: Annotated[N, Field(ge=1, le=32000)]
    end_to_end_seconds: Annotated[float, Field(strict=True, gt=0, le=3600)]


class Study(ledger.RecordModel):
    schema_version: Literal["evaluation.harness-study.v1"] = "evaluation.harness-study.v1"
    cohort_id: Annotated[str, Field(pattern=r"^study-[0-9]{8}-[0-9]{4}$")]
    cases: Annotated[tuple[PublicCase, ...], Field(min_length=1, max_length=8)]
    registry_sha256: ledger.Sha256
    information_policy_sha256: ledger.Sha256
    environment_sha256: ledger.Sha256
    strategy_sha256: ledger.Sha256
    seeds: Annotated[tuple[Seed, ...], Field(min_length=1, max_length=8)]
    repeats: Annotated[N, Field(ge=1, le=8)]
    strategies: Annotated[tuple[Strategy, ...], Field(min_length=1, max_length=4)]
    limits: Limits
    requested_model: Annotated[str, Field(strict=True, min_length=1, max_length=128)] = "meta-evaluation-operator-pinned"
    model_identity_sha256: ledger.Sha256
    generation_parameters_sha256: ledger.Sha256
    tool_evidence_kind: Evidence
    model_evidence_kind: Evidence
    visibility: Literal["disclosed_development", "held_out"]
    context_audit: ArtifactReference | None = None
    calibration: Literal["uncalibrated_protocol_candidate"] = "uncalibrated_protocol_candidate"

    @model_validator(mode="after")
    def frozen_matrix(self):
        if len({x.case_id for x in self.cases}) != len(self.cases) or len(set(self.seeds)) != len(self.seeds) or len(set(self.strategies)) != len(self.strategies):
            raise ValueError("duplicate_matrix_coordinate")
        if len(self.cases)*len(self.seeds)*self.repeats*len(self.strategies) > 128:
            raise ValueError("matrix_bound")
        if self.visibility == "held_out" and self.context_audit is None:
            raise ValueError("context_audit_required")
        return self


@dataclass(frozen=True)
class TrustedCase:
    """Private fixture/truth binding; deliberately has no public serialization."""
    public: PublicCase
    revision: ProjectRevision = field(repr=False)
    contract: BehavioralContract = field(repr=False)
    truth: ledger.GroundTruthRecord = field(repr=False)
    directed: tuple[ExperimentProposal, ...] = field(repr=False)


class Registry:
    def __init__(self, cases: tuple[TrustedCase, ...]):
        require(type(cases) is tuple and 1 <= len(cases) <= 8, "registry_bound")
        cases = tuple(TrustedCase(public=PublicCase.model_validate_json(encoded(c.public)),
            revision=ProjectRevision.model_validate_json(encoded(c.revision)), contract=BehavioralContract.model_validate_json(encoded(c.contract)),
            truth=ledger.GroundTruthRecord.model_validate_json(encoded(c.truth)),
            directed=tuple(ExperimentProposal.model_validate_json(encoded(p)) for p in c.directed)) for c in cases)
        self._cases = MappingProxyType({x.public.case_id: x for x in cases})
        require(len(self._cases) == len(cases), "registry_duplicate")
        for case in cases:
            try:
                validate_binding(case.revision, case.contract)
            except Exception:
                raise HarnessError("binding_invalid") from None
            require(case.contract.parameters == case.public.parameters and case.contract.requirements[0].requirement_id == "FIFO_BEHAVIOR", "binding_invalid")
            require(tuple(r.requirement_id for r in case.contract.requirements) == ("FIFO_BEHAVIOR",), "binding_invalid")
            require(case.truth.case_id == case.public.case_id and 1 <= len(case.directed) <= 16, "binding_invalid")
        private = [dict(public=c.public.model_dump(mode="json"), revision=c.revision.model_dump(mode="json"),
            contract=c.contract.model_dump(mode="json"), truth=c.truth.model_dump(mode="json"), directed=[x.model_dump(mode="json") for x in c.directed])
            for c in sorted(cases, key=lambda x: x.public.case_id)]
        self.sha256 = digest(encoded(private))

    def resolve(self, alias):
        require(alias in self._cases, "unknown_case")
        return self._cases[alias]

    def metadata_safe(self, data):
        # No private fixture IDs or truth-reference names in projected metadata.
        lowered = data.lower()
        for case in self._cases.values():
            forbidden = [case.revision.fixture_id]
            forbidden += [ref.path for ref in (case.truth.provenance, case.truth.witness, case.truth.expected_repair) if ref]
            require(not any(token.lower().encode() in lowered for token in forbidden), "private_context_blocked")


class Trial(ledger.RecordModel):
    case_id: Alias
    strategy: Strategy
    seed: Seed
    replicate: N


def matrix(study):
    return tuple(Trial(case_id=case.case_id, strategy=strategy, seed=seed, replicate=replicate)
        for case in study.cases for seed in study.seeds for replicate in range(study.repeats) for strategy in study.strategies)


class Feedback(ledger.RecordModel):
    execution_status: Literal["completed", "infrastructure_error"]
    verdict: Literal["pass_within_scope", "design_mismatch", "build_error", "harness_error", "timeout", "unsupported", "inconclusive"]
    first_cycle: N | None


class PlannerInput(ledger.RecordModel):
    schema_version: Literal["evaluation.neutral-fifo-input.v1"] = "evaluation.neutral-fifo-input.v1"
    case: PublicCase
    seed: Seed
    max_cycles: N
    max_output_tokens: N
    legal_operations: tuple[Literal["reset", "write", "read", "idle", "cycle"], ...] = ("reset", "write", "read", "idle", "cycle")
    semantics: Literal["sync_reset_clears_queue_output_preedge_acceptance_registered_oldest_postedge_flags"] = "sync_reset_clears_queue_output_preedge_acceptance_registered_oldest_postedge_flags"
    feedback: Annotated[tuple[Feedback, ...], Field(max_length=16)] = ()


class ProposalReceipt(ledger.RecordModel):
    operation_id: ledger.Identifier
    request_sha256: ledger.Sha256
    evidence_kind: Evidence
    state: Literal["completed", "failed", "unknown"]
    response: Annotated[bytes, Field(max_length=64000)] | None
    returned_model: Annotated[str, Field(min_length=1, max_length=256)] | None
    model_version: ledger.Text | None
    usage: ledger.ModelUsage
    elapsed_seconds: ledger.Seconds


class ObservedRow(ledger.RecordModel):
    cycle: N
    data: Annotated[StrictInt, Field(ge=0, le=2**64-1)]
    empty: StrictBool
    full: StrictBool


class ToolReceipt(ledger.RecordModel):
    operation_id: ledger.Identifier
    experiment_sha256: ledger.Sha256
    environment_sha256: ledger.Sha256
    source_sha256: ledger.Sha256
    contract_sha256: ledger.Sha256
    evidence_kind: Evidence
    status: Literal["completed", "build_error", "harness_error", "timeout", "unsupported"]
    rows: Annotated[tuple[ObservedRow, ...], Field(max_length=1000)]
    cycles: N
    elapsed_seconds: ledger.Seconds
    # Referenced native artifact bytes, already captured by the trusted runtime.
    artifacts: Annotated[tuple[ArtifactReference, ...], Field(max_length=32)] = ()


class Provider(Protocol):
    evidence_kind: Evidence
    model_identity_sha256: str
    requested_model: str
    generation_parameters_sha256: str
    def propose(self, operation_id: str, request: PlannerInput, *, timeout_seconds: float) -> ProposalReceipt: ...


class Runtime(Protocol):
    evidence_kind: Evidence
    def execute(self, operation_id: str, experiment: Experiment, *, revision: ProjectRevision, contract: BehavioralContract) -> ToolReceipt: ...


class ProposalCall(ledger.RecordModel):
    call_id: ledger.Identifier
    request: PlannerInput
    request_sha256: ledger.Sha256
    requested_model: Annotated[str, Field(min_length=1, max_length=128)]
    model_identity_sha256: ledger.Sha256
    generation_parameters_sha256: ledger.Sha256
    timeout_seconds: Annotated[float, Field(strict=True, gt=0, le=120)]


class ExecutionCall(ledger.RecordModel):
    dispatch_id: ledger.Identifier
    experiment: Experiment
    revision: ProjectRevision
    contract: BehavioralContract
    # The trusted runtime must provide admission/settlement, immutable source
    # verification and bounded process cleanup. This class dispatches no job.
    execution_policy: Literal["trusted_fixed_fifo_runtime_no_arbitrary_commands"] = "trusted_fixed_fifo_runtime_no_arbitrary_commands"


class ControlledProposalAdapter:
    """Typed adapter to a host-injected neutral transport; never an HTTP client."""
    def __init__(self, *, evidence_kind, requested_model, model_identity_sha256, generation_parameters_sha256, invoke):
        require(evidence_kind in ("actual", "mock", "recorded") and callable(invoke), "adapter_identity_invalid")
        self.evidence_kind, self.requested_model = evidence_kind, requested_model
        self.model_identity_sha256, self.generation_parameters_sha256 = model_identity_sha256, generation_parameters_sha256
        self._invoke = invoke

    def propose(self, operation_id, request, *, timeout_seconds):
        call = ProposalCall(call_id=operation_id, request=PlannerInput.model_validate_json(encoded(request)),
            request_sha256=digest(encoded(request)), requested_model=self.requested_model,
            model_identity_sha256=self.model_identity_sha256, generation_parameters_sha256=self.generation_parameters_sha256,
            timeout_seconds=timeout_seconds)
        return ProposalReceipt.model_validate_json(encoded(self._invoke(call)))


class ControlledRuntimeAdapter:
    """Typed bridge to an injected admitted runtime, with stable dispatch IDs."""
    def __init__(self, *, evidence_kind, submit):
        require(evidence_kind in ("actual", "mock", "recorded") and callable(submit), "adapter_identity_invalid")
        self.evidence_kind, self._submit = evidence_kind, submit

    def execute(self, operation_id, experiment, *, revision, contract):
        call = ExecutionCall(dispatch_id=operation_id, experiment=experiment, revision=revision, contract=contract)
        return ToolReceipt.model_validate_json(encoded(self._submit(call)))


class Freeze(ledger.RecordModel):
    kind: Literal["freeze"] = "freeze"
    study: Study
    trial: Trial
    scope: ledger.Scope
    admitted_at: datetime


class Intent(ledger.RecordModel):
    kind: Literal["intent"] = "intent"
    operation_id: ledger.Identifier
    stage: Literal["model", "tool"]
    round_index: N
    reserve: ledger.Amounts
    request_sha256: ledger.Sha256
    proposal: ExperimentProposal | None = None
    replay_parent: ledger.RecordReference | None = None


class Receipt(ledger.RecordModel):
    kind: Literal["receipt"] = "receipt"
    operation_id: ledger.Identifier
    record: Annotated[ledger.AttemptRecord | ledger.ModelRoundRecord, Field(discriminator="kind")]
    proposal: ExperimentProposal | None = None


class Terminal(ledger.RecordModel):
    kind: Literal["terminal"] = "terminal"
    record: ledger.CampaignRecord


Event = Annotated[Freeze | Intent | Receipt | Terminal, Field(discriminator="kind")]
_EVENT = TypeAdapter(Event)


class Journal:
    """Locked snapshots and explicit, same-thread journal transactions.

    Mutating sessions exist only while locked() is active. Session methods
    refuse use after exit or from another thread. No unlocked read/write API is
    exposed, and callbacks must not reenter the campaign's exclusive lock.
    """
    def __init__(self, root):
        self._lock = storage.PrivateFileLock(root, "dispatch.jsonl")
        self.path, self.lock_path = self._lock.path, self._lock.lock_path

    @contextmanager
    def locked(self):
        with self._lock.acquire():
            session = _JournalSession(self.path, _JOURNAL_SEAL)
            try:
                yield session
            finally:
                session.close()

    def create(self):
        with self.locked() as session:
            session.create()

    def read(self):
        with self.locked() as session:
            return session.read()


_JOURNAL_SEAL = object()


class _JournalSession:
    def __init__(self, path, seal):
        require(seal is _JOURNAL_SEAL, "journal_session_invalid")
        self._path, self._active, self._owner = path, True, get_ident()

    @property
    def path(self):
        return self._path

    def _check(self):
        require(self._active and get_ident() == self._owner, "journal_session_inactive")

    def close(self):
        self._active = False

    def create(self):
        self._check()
        try:
            fd = os.open(self.path, os.O_CREAT | os.O_EXCL | os.O_WRONLY | getattr(os, "O_BINARY", 0), 0o600)
        except FileExistsError:
            raise storage.StorageError("journal_already_exists") from None
        os.close(fd)

    def read(self):
        self._check()
        before = storage.regular_file(self.path)
        require(before.st_size <= MAX_BYTES, "journal_bound")
        fd = os.open(self.path, os.O_RDONLY | getattr(os, "O_BINARY", 0) | getattr(os, "O_NOFOLLOW", 0))
        with os.fdopen(fd, "rb") as stream:
            require(storage.file_identity(os.fstat(stream.fileno())) == storage.file_identity(before), "journal_changed")
            raw = stream.read(MAX_BYTES+1)
            require(storage.file_identity(os.fstat(stream.fileno())) == storage.file_identity(before), "journal_changed")
        require(storage.file_identity(storage.regular_file(self.path)) == storage.file_identity(before) and len(raw) == before.st_size, "journal_changed")
        require(not raw or raw.endswith(b"\n"), "journal_truncated")
        lines = raw.splitlines(keepends=True)
        require(len(lines) <= MAX_EVENTS, "journal_bound")
        events, head = [], None
        for index, line in enumerate(lines):
            require(len(line) <= 256*1024, "journal_bound")
            row = storage.canonical_json(ledger_json(line))
            require(row+b"\n" == line, "journal_noncanonical")
            value = ledger_json(line)
            require(set(value) == {"sequence", "previous", "event", "sha256"}, "journal_invalid")
            require(type(value["sequence"]) is int and value["sequence"] == index and value["previous"] == head, "journal_chain")
            require(value["sha256"] == digest(encoded({key: value[key] for key in ("sequence", "previous", "event")})), "journal_chain")
            event = _EVENT.validate_python(value["event"])
            require(encoded(event) == encoded(value["event"]), "journal_invalid")
            events.append(event)
            head = value["sha256"]
        return tuple(events), head

    def append(self, event):
        self._check()
        try:
            event = _EVENT.validate_json(encoded(event))
        except (AttributeError, TypeError, ValueError):
            raise HarnessError("journal_event_invalid") from None
        events, head = self.read()
        body = dict(sequence=len(events), previous=head, event=event.model_dump(mode="json"))
        line = encoded({**body, "sha256": digest(encoded(body))})+b"\n"
        before = storage.regular_file(self.path)
        require(len(events) < MAX_EVENTS and len(line) <= 256*1024 and before.st_size+len(line) <= MAX_BYTES, "journal_bound")
        fd = os.open(self.path, os.O_WRONLY | os.O_APPEND | getattr(os, "O_BINARY", 0) | getattr(os, "O_NOFOLLOW", 0))
        try:
            require(storage.file_identity(os.fstat(fd)) == storage.file_identity(before), "journal_changed")
            offset = 0
            while offset < len(line):
                count = os.write(fd, line[offset:])
                require(count > 0, "journal_write_failed")
                offset += count
            os.fsync(fd)
            after = os.fstat(fd)
            require(after.st_size == before.st_size+len(line) and storage.file_identity(storage.regular_file(self.path)) == storage.file_identity(after), "journal_changed")
        finally:
            os.close(fd)
        return digest(encoded(body))


def ledger_json(data):
    def unique(pairs):
        result = {}
        for key, value in pairs:
            require(key not in result, "duplicate_json_key")
            result[key] = value
        return result
    try:
        return json.loads(data, object_pairs_hook=unique, parse_constant=lambda _: (_ for _ in ()).throw(HarnessError("json_invalid")))
    except (ValueError, UnicodeError):
        raise HarnessError("json_invalid") from None


class BlobStore:
    def __init__(self, root):
        self.root = Path(root)

    def put(self, raw):
        require(type(raw) is bytes and len(raw) <= 256*1024, "blob_bound")
        sha = digest(raw)
        path = self.root / ("blob-"+sha+".bin")
        if path.exists():
            before = storage.regular_file(path)
            require(before.st_size == len(raw), "blob_conflict")
            with path.open("rb") as stream:
                require(storage.file_identity(os.fstat(stream.fileno())) == storage.file_identity(before), "blob_conflict")
                captured = stream.read(len(raw)+1)
                require(storage.file_identity(os.fstat(stream.fileno())) == storage.file_identity(before), "blob_conflict")
            require(captured == raw and storage.file_identity(storage.regular_file(path)) == storage.file_identity(before), "blob_conflict")
        else:
            fd = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY | getattr(os, "O_BINARY", 0), 0o600)
            try:
                offset = 0
                while offset < len(raw):
                    count = os.write(fd, raw[offset:])
                    require(count > 0, "blob_write_failed")
                    offset += count
                os.fsync(fd)
            finally:
                os.close(fd)
        return ArtifactReference(artifact_id="blob-"+sha, path=path.name, sha256=sha, size_bytes=len(raw), media_type="application/json")

    def read(self, ref):
        require(ref.artifact_id == "blob-"+ref.sha256 and ref.path == "blob-"+ref.sha256+".bin" and ref.size_bytes <= 256*1024, "blob_reference_invalid")
        path = self.root/ref.path
        before = storage.regular_file(path)
        require(before.st_size == ref.size_bytes, "blob_conflict")
        with path.open("rb") as stream:
            require(storage.file_identity(os.fstat(stream.fileno())) == storage.file_identity(before), "blob_conflict")
            raw = stream.read(ref.size_bytes+1)
            require(storage.file_identity(os.fstat(stream.fileno())) == storage.file_identity(before), "blob_conflict")
        require(storage.file_identity(storage.regular_file(path)) == storage.file_identity(before) and len(raw) == ref.size_bytes and digest(raw) == ref.sha256, "blob_conflict")
        return raw


def amounts(**values):
    return ledger.Amounts(**{field: values.get(field, 0.0 if field == "tool_seconds" else 0) for field in ledger.Amounts.model_fields})


def reference(entry):
    return ledger.RecordReference(record_id=entry.record.record_id, entry_sha256=entry.sha256)


def record_ground_truth(log, study, registry, alias):
    """Trusted host only; never called by strategy/provider projection."""
    require(log.channel == "ground_truth" and registry.sha256 == study.registry_sha256, "truth_channel_required")
    truth = registry.resolve(alias).truth.model_copy(update={"record_id": identity(canonical_hash(study), "truth", alias),
        "evaluation_revision": canonical_hash(study)})
    snapshot = log.snapshot()
    prior = next((entry for entry in snapshot.entries if entry.record.record_id == truth.record_id), None)
    if prior:
        require(prior.record == truth, "truth_changed")
        return prior
    return log.append(truth, expected_head=snapshot.current_head)


class Harness:
    def __init__(self, root, study: Study, trial: Trial, registry: Registry, *, now):
        # Detach caller-owned nested structures and reject constructed models.
        self.study = Study.model_validate_json(encoded(study))
        self.trial = Trial.model_validate_json(encoded(trial))
        require(trial in matrix(self.study), "trial_not_frozen")
        require(registry.sha256 == study.registry_sha256, "registry_changed")
        require(study.strategy_sha256 == digest(Path(__file__).read_bytes()), "strategy_source_changed")
        self.registry, self.case, self.now = registry, registry.resolve(trial.case_id), now
        require(self.case.public in study.cases, "case_changed")
        require(digest(Path(oracle.__file__).read_bytes()) == self.case.contract.checker.sha256, "checker_changed")
        self.journal, self.log, self.blobs = Journal(root), ledger.EvaluationLog(root, "measurements.jsonl"), BlobStore(root)
        self.campaign_id = identity(canonical_hash(study), trial.model_dump(mode="json"))
        self.scope = ledger.Scope(evaluation_revision=canonical_hash(study), campaign_id=self.campaign_id, case_id=trial.case_id,
            domain="rtl", profile="fifo-v1", strategy=trial.strategy, strategy_sha256=study.strategy_sha256,
            replicate=trial.replicate, seed=trial.seed, source_sha256=self.case.revision.source_hash,
            checker_sha256=self.case.contract.checker.sha256, contract_sha256=canonical_hash(self.case.contract),
            environment_sha256=study.environment_sha256, information_policy_sha256=study.information_policy_sha256,
            visibility=study.visibility, context_audit=study.context_audit, cache_state="unknown")

    def clock(self):
        at = self.now()
        require(isinstance(at, datetime) and at.tzinfo is not None and at.utcoffset() is not None, "clock_invalid")
        return at

    def create(self):
        self.journal.create()
        self.log.create()
        with self.journal.locked() as journal:
            freeze = Freeze(study=self.study, trial=self.trial, scope=self.scope, admitted_at=self.clock())
            self.registry.metadata_safe(encoded(freeze))
            journal.append(freeze)

    def read(self):
        with self.journal.locked() as journal:
            return self._read_state(journal)

    def _read_state(self, journal):
        events, head = journal.read()
        require(events and isinstance(events[0], Freeze) and events[0].study == self.study and events[0].trial == self.trial and events[0].scope == self.scope, "freeze_changed")
        pending, terminal, seen, counts = None, False, set(), {"model": 0, "tool": 0}
        for event in events[1:]:
            require(not terminal and not isinstance(event, Freeze), "journal_state")
            if isinstance(event, Intent):
                require(pending is None and event.operation_id == identity(self.campaign_id, event.stage, event.round_index), "journal_state")
                require(event.operation_id not in seen and event.round_index == counts[event.stage], "journal_state")
                require(all(getattr(event.reserve, name) is not None for name in ledger.Amounts.model_fields), "journal_state")
                counts[event.stage] += 1
                seen.add(event.operation_id)
                if event.stage == "tool":
                    limits = self.study.limits
                    require(event.proposal is not None and event.reserve == amounts(attempts=1, cycles=limits.per_attempt_cycles,
                        tool_seconds=limits.per_attempt_seconds, artifact_bytes=limits.per_attempt_artifact_bytes), "journal_state")
                    require(canonical_hash(self.experiment(event.proposal, event.operation_id)) == event.request_sha256, "journal_state")
                else:
                    require(event.proposal is None and event.replay_parent is None and event.reserve.attempts == event.reserve.cycles == event.reserve.tool_seconds == 0,
                        "journal_state")
                pending = event
            elif isinstance(event, Receipt):
                require(pending is not None and pending.operation_id == event.operation_id and event.record.scope == self.scope, "journal_state")
                require(event.record.record_id == event.operation_id and isinstance(event.record, ledger.ModelRoundRecord) == (pending.stage == "model"), "journal_state")
                if isinstance(event.record, ledger.AttemptRecord):
                    require(event.proposal == pending.proposal and event.record.attempt.attempt_id == event.operation_id
                        and event.record.attempt.experiment_id == event.operation_id and event.record.parent_attempt == pending.replay_parent, "journal_state")
                    require(self.blobs.read(event.record.accepted_plan) == encoded(self.experiment(event.proposal, event.operation_id)), "saved_plan_changed")
                    raw_receipt = ToolReceipt.model_validate_json(self.blobs.read(event.record.attempt.artifacts[-1]))
                    _, checked, _ = self.check(raw_receipt, self.experiment(event.proposal, event.operation_id), event.operation_id)
                    require(checked == event.record.attempt, "saved_observations_changed")
                else:
                    require(event.record.model_round_id == event.operation_id and event.record.prompt_sha256 == pending.request_sha256, "journal_state")
                    if event.record.response:
                        self.blobs.read(event.record.response)
                    require((event.proposal is not None) == (event.record.accepted_plan is not None), "journal_state")
                    if event.record.accepted_plan:
                        require(self.blobs.read(event.record.accepted_plan) == encoded(event.proposal), "saved_plan_changed")
                pending = None
            elif isinstance(event, Terminal):
                require(pending is None and event.record.scope == self.scope, "journal_state")
                terminal = True
        self.registry.metadata_safe(encoded([event.model_dump(mode="json") for event in events]))
        return events, head, pending

    def sync(self, events):
        projected = tuple(event.record for event in events if isinstance(event, (Receipt, Terminal)))
        snapshot = self.log.snapshot()
        existing = snapshot.entries
        require(len(existing) <= len(projected) and tuple(x.record for x in existing) == projected[:len(existing)], "ledger_projection_differs")
        head = snapshot.current_head
        entries = list(existing)
        for record in projected[len(existing):]:
            entry = self.log.append(record, expected_head=head)
            entries.append(entry)
            head = entry.sha256
        return tuple(entries)

    def reserved(self, events):
        intents = [event for event in events if isinstance(event, Intent)]
        return amounts(**{name: sum(getattr(event.reserve, name) for event in intents) for name in ledger.Amounts.model_fields})

    def allocated(self):
        limits = self.study.limits
        return amounts(attempts=limits.attempts, cycles=limits.cycles, tool_seconds=limits.tool_seconds,
            artifact_bytes=limits.artifact_bytes, model_tokens=limits.model_tokens if self.trial.strategy.startswith("meta_") else 0)

    def budget(self, events):
        records = [event.record for event in events if isinstance(event, Receipt)]
        calls = [r for r in records if isinstance(r, ledger.ModelRoundRecord)]
        attempts = [r for r in records if isinstance(r, ledger.AttemptRecord)]
        tokens = None if any(r.usage.state == "unknown" for r in calls) else sum((r.usage.input_tokens or 0)+(r.usage.output_tokens or 0) for r in calls)
        consumed = amounts(attempts=len(attempts), cycles=sum(r.attempt.usage.cycles for r in attempts),
            tool_seconds=float(sum(r.attempt.usage.wall_time_seconds for r in attempts)),
            artifact_bytes=sum(r.attempt.usage.artifact_bytes for r in attempts)
                +sum((r.response.size_bytes if r.response else 0)+(r.accepted_plan.size_bytes if r.accepted_plan else 0) for r in calls), model_tokens=tokens)
        return ledger.Budget(profile_sha256=canonical_hash(self.study.limits), allocated=self.allocated(), reserved=self.reserved(events), consumed=consumed)

    def common(self, events, record_id, evidence):
        started = events[0].admitted_at
        at = self.clock()
        require(at >= started and all(at >= event.record.occurred_at for event in events if isinstance(event, (Receipt, Terminal))), "clock_invalid")
        return dict(record_id=record_id, occurred_at=at, scope=self.scope, evidence_kind=evidence, budget=self.budget(events),
            timings=ledger.Timings(**{name: float((at-started).total_seconds()) if name == "end_to_end_seconds" else None for name in ledger.Timings.model_fields}))

    def terminal(self, events, entries, reason, *, journal, excluded=False):
        receipts = [entry for entry in entries if isinstance(entry.record, (ledger.AttemptRecord, ledger.ModelRoundRecord))]
        kinds = {entry.record.evidence_kind for entry in receipts}
        evidence = next(iter(kinds)) if len(kinds) == 1 else "mixed" if kinds else "mock"
        common = self.common(events, identity(self.campaign_id, "campaign"), evidence)
        record = ledger.CampaignRecord(**common,
            attempts=tuple(reference(x) for x in receipts if isinstance(x.record, ledger.AttemptRecord)),
            model_rounds=tuple(reference(x) for x in receipts if isinstance(x.record, ledger.ModelRoundRecord)),
            stop_reason=reason, right_censored=reason == "budget_exhausted",
            review=ledger.ReviewDisposition(status="excluded" if excluded else "pending", reason="not_eligible" if excluded else "independent_review_required"))
        event = Terminal(record=record)
        self.registry.metadata_safe(encoded(event))
        journal.append(event)
        self.sync((*events, event))
        return reason

    def proposal(self, index):
        if self.trial.strategy == "directed_manual":
            return self.case.directed[index].model_copy(update={"seed": self.trial.seed}) if index < len(self.case.directed) else None
        rng = random.Random(int(digest(encoded((self.campaign_id, self.trial.seed, index))), 16))
        count = min(32, self.study.limits.per_attempt_cycles)
        operations = [dict(op="reset", cycles=1)]
        for _ in range(count-1):
            op = rng.choice(("write", "read", "idle"))
            operations.append(dict(op=op, value=rng.randrange(2**self.case.public.parameters.width)) if op == "write" else dict(op=op))
        return ExperimentProposal(kind="experiment", objective_ids=("FIFO_BEHAVIOR",), seed=self.trial.seed, operations=tuple(operations))

    def experiment(self, proposal, operation_id):
        limits = self.study.limits
        require(proposal.objective_ids == ("FIFO_BEHAVIOR",) and proposal.seed == self.trial.seed, "proposal_invalid")
        try:
            return Experiment(experiment_id=operation_id, fixture_id=self.case.revision.fixture_id, objective_ids=proposal.objective_ids,
                seed=proposal.seed, operations=proposal.operations, parameters=self.case.public.parameters,
                budget=ExecutionBudget(max_cycles=limits.per_attempt_cycles, wall_time_seconds=limits.per_attempt_seconds,
                    max_artifact_bytes=limits.per_attempt_artifact_bytes))
        except Exception:
            raise HarnessError("proposal_invalid") from None

    def check(self, captured, experiment, operation_id):
        captured = ToolReceipt.model_validate_json(encoded(captured))
        require(captured.operation_id == operation_id and captured.experiment_sha256 == canonical_hash(experiment)
            and captured.environment_sha256 == self.scope.environment_sha256 and captured.source_sha256 == self.scope.source_sha256
            and captured.contract_sha256 == self.scope.contract_sha256, "tool_receipt_invalid")
        require(digest(Path(oracle.__file__).read_bytes()) == self.scope.checker_sha256, "checker_changed")
        limits = self.study.limits
        require(captured.cycles <= limits.per_attempt_cycles and captured.elapsed_seconds <= limits.per_attempt_seconds, "runtime_limit_violated")
        for artifact in captured.artifacts:
            require(artifact.artifact_id == "blob-"+artifact.sha256 and artifact.path == "blob-"+artifact.sha256+".bin", "artifact_namespace_invalid")
        require(len({a.artifact_id for a in captured.artifacts}) == len(captured.artifacts), "artifact_namespace_invalid")
        verdict, signature = captured.status, None
        if captured.status == "completed":
            require(captured.cycles == experiment.cycle_count and len(captured.rows) == captured.cycles, "observations_incomplete")
            observed = [row.model_dump() for row in captured.rows]
            require(all(row.data < 2**experiment.parameters.width for row in captured.rows), "observation_invalid")
            expected = oracle.expected_trace(oracle.expand(experiment), experiment.parameters.depth)
            try:
                difference = oracle.compare(expected, observed)
            except Exception:
                raise HarnessError("observations_invalid") from None
            verdict = "design_mismatch" if difference else "pass_within_scope"
            if difference:
                signature = FailureSignature(requirement_id="FIFO_BEHAVIOR", failure_class="fifo."+difference["signal"],
                    first_cycle=difference["cycle"], expected=str(difference["expected"]), observed=str(difference["observed"]),
                    signature_hash=digest(encoded(dict(checker=self.scope.checker_sha256, contract=self.scope.contract_sha256, signal=difference["signal"]))))
        raw_body, plan_body = encoded(captured), encoded(experiment)
        artifact_bytes = len(raw_body)+len(plan_body)+sum(a.size_bytes for a in captured.artifacts)
        require(artifact_bytes <= limits.per_attempt_artifact_bytes, "runtime_limit_violated")
        artifact_ids = [a.artifact_id for a in captured.artifacts]+["blob-"+digest(plan_body), "blob-"+digest(raw_body)]
        require(len(artifact_ids) == len(set(artifact_ids)), "artifact_namespace_invalid")
        raw_ref = self.blobs.put(raw_body)
        plan_ref = self.blobs.put(plan_body)
        result = AttemptResult(attempt_id=operation_id, experiment_id=experiment.experiment_id, execution_status="completed", verdict=verdict,
            identity=EvidenceIdentity(source_hash=self.scope.source_sha256, checker_hash=self.scope.checker_sha256,
                contract_hash=self.scope.contract_sha256, experiment_hash=canonical_hash(experiment), environment_hash=self.scope.environment_sha256),
            failure_signature=signature, artifacts=(*captured.artifacts, plan_ref, raw_ref), requirement_coverage=("FIFO_BEHAVIOR",) if captured.status == "completed" else (),
            usage=ResourceUsage(cycles=captured.cycles, wall_time_seconds=captured.elapsed_seconds, artifact_bytes=artifact_bytes))
        validate_result(result, experiment=experiment, revision=self.case.revision, contract=self.case.contract)
        return captured, result, plan_ref

    def advance(self, *, expected_head, provider: Provider | None = None, runtime: Runtime | None = None, replay_parent_id=None):
        try:
            return self._advance(expected_head=expected_head, provider=provider, runtime=runtime, replay_parent_id=replay_parent_id)
        except HarnessError:
            raise
        except (ledger.EvaluationLogError, storage.StorageError):
            raise HarnessError("storage_guard_rejected") from None
        except Exception:
            raise HarnessError("storage_or_protocol_invalid") from None

    def _advance(self, *, expected_head, provider: Provider | None = None, runtime: Runtime | None = None, replay_parent_id=None):
        """At most one external invocation; no retry of an ambiguous intent."""
        with self.journal.locked() as journal:
            require(digest(Path(__file__).read_bytes()) == self.study.strategy_sha256, "strategy_source_changed")
            require(digest(Path(oracle.__file__).read_bytes()) == self.scope.checker_sha256, "checker_changed")
            events, head, pending = self._read_state(journal)
            require(head == expected_head, "head_changed")
            entries = self.sync(events)
            if isinstance(events[-1], Terminal):
                return events[-1].record.stop_reason
            if pending:
                raise HarnessError("recovery_required_no_automatic_redispatch")
            at = self.clock()
            require(at >= events[0].admitted_at and all(at >= event.record.occurred_at for event in events if isinstance(event, (Receipt, Terminal))), "clock_invalid")
            if (self.clock()-events[0].admitted_at).total_seconds() >= self.study.limits.end_to_end_seconds:
                return self.terminal(events, entries, "budget_exhausted", journal=journal)
            if self.case.truth.classification in ("equivalent_mutant", "invalid", "unresolved"):
                return self.terminal(events, entries, "unsupported", journal=journal, excluded=True)
            receipts = [event for event in events if isinstance(event, Receipt)]
            tools = [event for event in receipts if isinstance(event.record, ledger.AttemptRecord)]
            models = [event for event in receipts if isinstance(event.record, ledger.ModelRoundRecord)]
            replay_parent = None
            replay_proposal = None
            if replay_parent_id is not None:
                parent = next((x for x in entries if isinstance(x.record, ledger.AttemptRecord) and x.record.record_id == replay_parent_id), None)
                require(parent is not None, "replay_parent_invalid")
                replay_parent = reference(parent)
                replay_proposal = next(x.proposal for x in receipts if x.record.record_id == replay_parent_id)
                require(parent.record.attempt.verdict in ("pass_within_scope", "design_mismatch"), "replay_parent_invalid")
            if tools and replay_parent is None:
                last = tools[-1].record.attempt
                if last.verdict in ("design_mismatch", "property_failure"):
                    return self.terminal(events, entries, "completed", journal=journal)
                if last.verdict != "pass_within_scope":
                    return self.terminal(events, entries, "unsupported" if last.verdict == "unsupported" else "infrastructure_failure", journal=journal)
            if models and replay_parent is None and (models[-1].record.usage.state == "unknown" or models[-1].record.status in ("failed", "unknown")):
                return self.terminal(events, entries, "blocked", journal=journal)
            if len(tools) >= self.study.limits.attempts:
                return self.terminal(events, entries, "budget_exhausted", journal=journal)
            model_strategy = self.trial.strategy.startswith("meta_")
            use_model = replay_parent is None and model_strategy and (not models or models[-1].record.accepted_plan is None or len(tools) >= len(models))
            index = len(models) if use_model else len(tools)
            if use_model and self.trial.strategy == "meta_one_pass" and models:
                return self.terminal(events, entries, "completed", journal=journal)
            if use_model and len(models) >= self.study.limits.attempts:
                return self.terminal(events, entries, "budget_exhausted", journal=journal)
            stage = "model" if use_model else "tool"
            operation_id = identity(self.campaign_id, stage, index)
            limits = self.study.limits
            if use_model:
                feedback = tuple(Feedback(execution_status="completed", verdict=x.record.attempt.verdict.value,
                    first_cycle=x.record.attempt.failure_signature.first_cycle if x.record.attempt.failure_signature else None) for x in tools) if self.trial.strategy == "meta_feedback" else ()
                request = PlannerInput(case=self.case.public, seed=self.trial.seed, max_cycles=limits.per_attempt_cycles,
                    max_output_tokens=limits.feedback_output_tokens, feedback=feedback)
                overhead = len(encoded(request))+1024
                output = min(32000, limits.model_tokens-overhead-16) if self.trial.strategy == "meta_one_pass" else limits.feedback_output_tokens
                require(output > 0, "model_allowance_too_small")
                request = request.model_copy(update={"max_output_tokens": output})
                reserve_tokens = limits.model_tokens if self.trial.strategy == "meta_one_pass" else len(encoded(request))+1024+output
                reserve = amounts(model_tokens=reserve_tokens, artifact_bytes=limits.response_bytes*2)
                proposal = None
                self.registry.metadata_safe(encoded(request))
                request_hash = digest(encoded(request))
            else:
                proposal = replay_proposal if replay_parent else models[-1].proposal if model_strategy and models else self.proposal(index)
                if proposal is None:
                    return self.terminal(events, entries, "completed", journal=journal)
                experiment = self.experiment(proposal, operation_id)
                request_hash = canonical_hash(experiment)
                reserve = amounts(attempts=1, cycles=limits.per_attempt_cycles, tool_seconds=limits.per_attempt_seconds,
                    artifact_bytes=limits.per_attempt_artifact_bytes)
            prior = self.reserved(events)
            allocated = self.allocated()
            if any(getattr(prior, name)+getattr(reserve, name) > getattr(allocated, name) for name in ledger.Amounts.model_fields):
                return self.terminal(events, entries, "budget_exhausted", journal=journal)
            remaining = limits.end_to_end_seconds-(self.clock()-events[0].admitted_at).total_seconds()
            phase_cap = limits.per_model_seconds if use_model else limits.per_attempt_seconds
            admitted_seconds = sum(limits.per_model_seconds if event.stage == "model" else limits.per_attempt_seconds for event in events if isinstance(event, Intent))
            if remaining < phase_cap or admitted_seconds+phase_cap > limits.end_to_end_seconds:
                return self.terminal(events, entries, "budget_exhausted", journal=journal)
            require(provider is not None if use_model else runtime is not None, "adapter_required")
            if use_model:
                require(getattr(provider, "model_identity_sha256", None) == self.study.model_identity_sha256
                    and getattr(provider, "requested_model", None) == self.study.requested_model
                    and getattr(provider, "generation_parameters_sha256", None) == self.study.generation_parameters_sha256
                    and getattr(provider, "evidence_kind", None) == self.study.model_evidence_kind, "adapter_identity_invalid")
            else:
                require(getattr(runtime, "evidence_kind", None) == self.study.tool_evidence_kind, "adapter_identity_invalid")
            intent = Intent(operation_id=operation_id, stage=stage, round_index=index, reserve=reserve, request_sha256=request_hash, proposal=proposal, replay_parent=replay_parent)
            self.registry.metadata_safe(encoded(intent))
            journal.append(intent)
            events = (*events, intent)
            try:
                if use_model:
                    captured = provider.propose(operation_id, request, timeout_seconds=limits.per_model_seconds)
                    captured = ProposalReceipt.model_validate_json(encoded(captured))
                    require(captured.operation_id == operation_id and captured.request_sha256 == request_hash, "provider_receipt_invalid")
                    require(captured.evidence_kind == provider.evidence_kind, "provider_receipt_invalid")
                    require(captured.returned_model is None or captured.returned_model == self.study.requested_model, "provider_receipt_invalid")
                    require(captured.elapsed_seconds <= limits.per_model_seconds and (captured.response is None or len(captured.response) <= limits.response_bytes), "provider_limit_violated")
                    if captured.usage.state == "known":
                        require(captured.usage.input_tokens+captured.usage.output_tokens <= reserve_tokens and captured.usage.output_tokens <= request.max_output_tokens, "provider_limit_violated")
                    require(captured.usage.state != "not_called", "provider_receipt_invalid")
                    response = self.blobs.put(captured.response) if captured.response is not None else None
                    accepted, status, reason = None, captured.state, None
                    if captured.state == "completed":
                        require(response is not None, "provider_receipt_invalid")
                        try:
                            require(len(captured.response) <= 64000, "proposal_invalid")
                            parsed = ledger_json(captured.response)
                            accepted = ExperimentProposal.model_validate(parsed)
                            require(len(encoded(accepted)) <= limits.response_bytes, "proposal_invalid")
                            self.experiment(accepted, identity(self.campaign_id, "tool", len(tools)))
                            require(not any(event.proposal == accepted for event in tools), "duplicate_proposal")
                        except Exception:
                            accepted, status, reason = None, "rejected", "typed_proposal_invalid"
                    plan_ref = self.blobs.put(encoded(accepted)) if accepted else None
                    common = self.common(events, operation_id, captured.evidence_kind)
                    common["timings"] = common["timings"].model_copy(update={"model_seconds": captured.elapsed_seconds})
                    record = ledger.ModelRoundRecord(**common, model_round_id=operation_id, provider="meta", requested_model=self.study.requested_model,
                        returned_model=captured.returned_model, model_version=captured.model_version, prompt_sha256=request_hash,
                        response_schema_sha256=digest(encoded(ExperimentProposal.model_json_schema())), generation_parameters_sha256=self.study.generation_parameters_sha256,
                        provider_seed=None, status=status, usage=captured.usage, response=response, accepted_plan=plan_ref, rejection_reason=reason,
                        feedback=tuple(reference(entry) for entry in entries if isinstance(entry.record, ledger.AttemptRecord)) if self.trial.strategy == "meta_feedback" else ())
                    receipt = Receipt(operation_id=operation_id, record=record, proposal=accepted)
                else:
                    captured, result, plan_ref = self.check(runtime.execute(operation_id, experiment,
                        revision=self.case.revision, contract=self.case.contract), experiment, operation_id)
                    require(captured.evidence_kind == runtime.evidence_kind, "tool_receipt_invalid")
                    common = self.common(events, operation_id, captured.evidence_kind)
                    record = ledger.AttemptRecord(**common, experiment_record_id=operation_id, accepted_plan=plan_ref, attempt=result,
                        purpose="replay" if replay_parent else "detection", parent_attempt=replay_parent)
                    receipt = Receipt(operation_id=operation_id, record=record, proposal=proposal)
                receipt = receipt.model_copy(update={"record": receipt.record.model_copy(update={"budget": self.budget((*events, receipt))})})
                self.registry.metadata_safe(encoded(receipt))
            except Exception:
                # The durable intent retains the entire reservation. Returning a
                # receipt is not assumed if processing or persistence fails.
                raise HarnessError("effect_unsettled_review_required") from None
            journal.append(receipt)
            try:
                self.sync((*events, receipt))
            except Exception:
                raise HarnessError("ledger_sync_required") from None
            return "progress"
