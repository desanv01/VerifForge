"""Bounded local evaluation records, not a benchmark or planner context source.

Callers supply an access-controlled private directory. POSIX creation requests
0600; Windows privacy depends on the directory ACL. Hash linkage detects changed
history relative to a retained head, not an adversary rewriting the entire log.
Writers serialize using an exclusive sidecar; a crash can leave a lock or partial
tail which must be preserved and reviewed, never automatically truncated/retried.
"""
from __future__ import annotations

from contextlib import contextmanager
from datetime import datetime
import hashlib
import json
import os
from pathlib import Path
import re
import stat
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, StrictBool, StrictInt, TypeAdapter, field_validator, model_validator

from veriforge.contracts import ArtifactReference, AttemptResult, Identifier, Sha256, _safe_path

MAX_LINE_BYTES = 256 * 1024
MAX_LOG_BYTES = 16 * 1024 * 1024
MAX_RECORDS = 4096
Count = Annotated[StrictInt, Field(ge=0, le=10**12)]
Text = Annotated[str, Field(min_length=1, max_length=2000)]
Seconds = Annotated[float, Field(strict=True, ge=0, le=10**9)]


class RecordModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, validate_default=True, allow_inf_nan=False)


class RecordReference(RecordModel):
    record_id: Identifier
    entry_sha256: Sha256


class Amounts(RecordModel):
    """Null means unknown, including failed/ambiguous provider accounting."""
    attempts: Count | None
    cycles: Count | None
    tool_seconds: Seconds | None
    artifact_bytes: Count | None
    model_tokens: Count | None


class Budget(RecordModel):
    """Frozen campaign allocation with per-record reservation/consumption snapshots."""
    profile_sha256: Sha256
    allocated: Amounts
    reserved: Amounts
    consumed: Amounts


class Timings(RecordModel):
    end_to_end_seconds: Seconds | None
    model_seconds: Seconds | None
    compile_seconds: Seconds | None
    execute_seconds: Seconds | None
    check_seconds: Seconds | None
    replay_seconds: Seconds | None
    reduction_seconds: Seconds | None
    repair_seconds: Seconds | None


class Scope(RecordModel):
    evaluation_revision: Sha256
    campaign_id: Identifier
    case_id: Identifier
    domain: Literal["rtl", "riscv", "firmware", "vivado", "kicad", "cross_domain"]
    profile: Identifier
    strategy: Literal["directed_manual", "constrained_random", "meta_one_pass", "meta_feedback"]
    strategy_sha256: Sha256
    replicate: Count
    seed: Count | None
    source_sha256: Sha256
    checker_sha256: Sha256
    contract_sha256: Sha256
    environment_sha256: Sha256
    information_policy_sha256: Sha256
    visibility: Literal["disclosed_development", "held_out"]
    context_audit: ArtifactReference | None = None
    cache_state: Literal["cold", "warm", "mixed", "unknown"]

    @model_validator(mode="after")
    def held_out_needs_audit(self):
        if self.visibility == "held_out" and self.context_audit is None:
            raise ValueError("held-out evaluation requires a context-audit reference")
        return self


class Measurement(RecordModel):
    schema_version: Literal["evaluation.v1"] = "evaluation.v1"
    record_id: Identifier
    occurred_at: datetime
    scope: Scope
    evidence_kind: Literal["actual", "mock", "recorded"]
    budget: Budget
    timings: Timings
    uncertainties: Annotated[tuple[Text, ...], Field(max_length=32)] = ()

    @field_validator("occurred_at", mode="before")
    @classmethod
    def no_numeric_timestamp(cls, value):
        if not isinstance(value, (datetime, str)) or isinstance(value, str) and "T" not in value:
            raise ValueError("timestamp must be an aware datetime or ISO string")
        return value

    @field_validator("occurred_at")
    @classmethod
    def aware(cls, value):
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("timezone-aware observation required")
        return value


class AttemptRecord(Measurement):
    kind: Literal["attempt"] = "attempt"
    experiment_record_id: Identifier
    accepted_plan: ArtifactReference
    attempt: AttemptResult
    parent_attempt: RecordReference | None = None
    purpose: Literal["detection", "replay", "reduction", "witness", "neighbor", "regression"]

    @model_validator(mode="after")
    def bounded_result(self):
        # Raw traces belong in independently hashed artifacts, not this ledger.
        if self.attempt.trace or len(self.attempt.artifacts) > 128 or len(self.attempt.tool_versions) > 32:
            raise ValueError("attempt summary exceeds evaluation bounds")
        identity = self.attempt.identity
        if identity is not None and (
            identity.source_hash, identity.checker_hash, identity.contract_hash, identity.environment_hash
        ) != (self.scope.source_sha256, self.scope.checker_sha256, self.scope.contract_sha256, self.scope.environment_sha256):
            raise ValueError("attempt identity differs from frozen scope")
        if self.purpose == "replay" and self.parent_attempt is None:
            raise ValueError("replay requires original attempt reference")
        return self


class ModelUsage(RecordModel):
    state: Literal["known", "unknown", "not_called"]
    input_tokens: Count | None
    output_tokens: Count | None
    billed_usd: Annotated[float, Field(strict=True, ge=0, le=10**9)] | None

    @model_validator(mode="after")
    def no_invented_zero(self):
        if self.state == "unknown" and any(value is not None for value in (self.input_tokens, self.output_tokens, self.billed_usd)):
            raise ValueError("unknown usage must remain null")
        if self.state == "known" and (self.input_tokens is None or self.output_tokens is None):
            raise ValueError("known token usage needs both counts")
        if self.state == "not_called" and (self.input_tokens, self.output_tokens, self.billed_usd) != (0, 0, 0.0):
            raise ValueError("not-called usage must explicitly be zero")
        return self


class ModelRoundRecord(Measurement):
    kind: Literal["model_round"] = "model_round"
    model_round_id: Identifier
    provider: Literal["meta"]
    requested_model: Annotated[str, Field(min_length=1, max_length=256)]
    returned_model: Annotated[str, Field(min_length=1, max_length=256)] | None
    model_version: Text | None
    prompt_sha256: Sha256
    response_schema_sha256: Sha256
    generation_parameters_sha256: Sha256
    provider_seed: Count | None
    status: Literal["completed", "rejected", "failed", "unknown", "not_called"]
    usage: ModelUsage
    response: ArtifactReference | None
    accepted_plan: ArtifactReference | None
    rejection_reason: Text | None
    feedback: Annotated[tuple[RecordReference, ...], Field(max_length=32)] = ()

    @model_validator(mode="after")
    def outcome(self):
        if (self.status == "not_called") != (self.usage.state == "not_called"):
            raise ValueError("not-called status and usage differ")
        if self.status == "completed" and self.response is None:
            raise ValueError("completed model call needs recorded response")
        if self.status == "rejected" and (self.response is None or self.rejection_reason is None):
            raise ValueError("rejected proposal requires retained response and reason")
        if self.status != "completed" and self.accepted_plan is not None:
            raise ValueError("only completed round can have an accepted plan")
        if self.status == "not_called" and self.response is not None:
            raise ValueError("not-called round cannot have response evidence")
        if self.status != "not_called" and self.scope.strategy not in ("meta_one_pass", "meta_feedback"):
            raise ValueError("non-model strategy cannot claim a model call")
        return self


class ReviewDisposition(RecordModel):
    status: Literal["pending", "included", "excluded", "inconclusive"]
    reason: Text
    missing_fields: Annotated[tuple[Identifier, ...], Field(max_length=32)] = ()


class CampaignRecord(Measurement):
    kind: Literal["campaign"] = "campaign"
    evidence_kind: Literal["actual", "mock", "recorded", "mixed"]
    attempts: Annotated[tuple[RecordReference, ...], Field(max_length=128)]
    model_rounds: Annotated[tuple[RecordReference, ...], Field(max_length=128)]
    stop_reason: Literal["completed", "budget_exhausted", "cancelled", "infrastructure_failure", "unsupported", "blocked", "interrupted"]
    right_censored: StrictBool
    review: ReviewDisposition

    @model_validator(mode="after")
    def exhaustion_visible(self):
        if self.stop_reason == "budget_exhausted" and not self.right_censored:
            raise ValueError("budget exhaustion must remain right-censored")
        return self


class GroundTruthRecord(RecordModel):
    kind: Literal["ground_truth"] = "ground_truth"
    schema_version: Literal["evaluation.truth.v1"] = "evaluation.truth.v1"
    record_id: Identifier
    evaluation_revision: Sha256
    case_id: Identifier
    classification: Literal["control", "seeded_defect", "user_reported_defect", "upstream_defect", "equivalent_mutant", "invalid", "unresolved"]
    provenance: ArtifactReference
    reviewed_by: Identifier
    review: ArtifactReference
    witness: ArtifactReference | None
    expected_repair: ArtifactReference | None


class CorrectionRecord(RecordModel):
    """Reviewed annotation only: measurements and frozen identities stay intact."""
    kind: Literal["correction"] = "correction"
    schema_version: Literal["evaluation.correction.v1"] = "evaluation.correction.v1"
    record_id: Identifier
    target: RecordReference
    reviewed_by: Identifier
    review: ArtifactReference
    disposition: ReviewDisposition
    explanation: Text


Record = Annotated[AttemptRecord | ModelRoundRecord | CampaignRecord | GroundTruthRecord | CorrectionRecord, Field(discriminator="kind")]
_RECORD = TypeAdapter(Record)


class Entry(RecordModel):
    schema_version: Literal["evaluation.entry.v1"] = "evaluation.entry.v1"
    channel: Literal["measurements", "ground_truth"]
    sequence: Count
    previous_sha256: Sha256 | None
    record: Record
    sha256: Sha256


class EvaluationLogError(ValueError):
    """Fixed diagnostics omit private paths and measurement content."""


def _canonical(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False).encode("utf-8")


def _digest(value):
    return hashlib.sha256(value).hexdigest()


def _stat_identity(info):
    return info.st_dev, info.st_ino, info.st_mode, info.st_size, info.st_mtime_ns


def _regular(path):
    for part in (*reversed(path.parents), path):
        info = part.lstat()
        if stat.S_ISLNK(info.st_mode) or getattr(info, "st_file_attributes", 0) & 0x400:
            raise EvaluationLogError("linked_path")
    info = path.stat()
    if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
        raise EvaluationLogError("single_regular_file_required")
    return info


def _references(record):
    if isinstance(record, CampaignRecord):
        return tuple((ref, AttemptRecord) for ref in record.attempts) + tuple((ref, ModelRoundRecord) for ref in record.model_rounds)
    if isinstance(record, ModelRoundRecord):
        return tuple((ref, AttemptRecord) for ref in record.feedback)
    if isinstance(record, AttemptRecord) and record.parent_attempt:
        return ((record.parent_attempt, AttemptRecord),)
    if isinstance(record, CorrectionRecord):
        return ((record.target, (AttemptRecord, ModelRoundRecord, CampaignRecord, GroundTruthRecord)),)
    return ()


def _validate(entries, channel):
    seen, scopes, measurements = {}, {}, set()
    for index, entry in enumerate(entries):
        record = entry.record
        if entry.sequence != index or entry.previous_sha256 != (entries[index-1].sha256 if index else None):
            raise EvaluationLogError("broken_chain")
        body = entry.model_dump(mode="json", exclude={"sha256"})
        if entry.sha256 != _digest(_canonical(body)):
            raise EvaluationLogError("entry_hash_differs")
        if entry.channel != channel or ((isinstance(record, GroundTruthRecord)) != (channel == "ground_truth") and not isinstance(record, CorrectionRecord)):
            raise EvaluationLogError("wrong_private_channel")
        if record.record_id in seen:
            raise EvaluationLogError("duplicate_record_id")
        references = _references(record)
        if len({ref.record_id for ref, _ in references}) != len(references):
            raise EvaluationLogError("duplicate_reference")
        for ref, expected in references:
            original = seen.get(ref.record_id)
            if original is None or original.sha256 != ref.entry_sha256 or not isinstance(original.record, expected):
                raise EvaluationLogError("unbound_record_reference")
            if isinstance(record, Measurement) and record.scope != original.record.scope:
                raise EvaluationLogError("cross_campaign_reference")
        if isinstance(record, CampaignRecord) and references:
            kinds = {seen[ref.record_id].record.evidence_kind for ref, _ in references}
            required = next(iter(kinds)) if len(kinds) == 1 else "mixed"
            if record.evidence_kind != required:
                raise EvaluationLogError("campaign_evidence_kind_differs")
        if isinstance(record, Measurement):
            key = (record.scope.evaluation_revision, record.scope.campaign_id)
            frozen = (record.scope, record.budget.profile_sha256, record.budget.allocated)
            if key in scopes and scopes[key] != frozen:
                raise EvaluationLogError("frozen_scope_changed")
            scopes[key] = frozen
            subject = record.attempt.attempt_id if isinstance(record, AttemptRecord) else record.model_round_id if isinstance(record, ModelRoundRecord) else record.scope.campaign_id
            identity = (*key, record.kind, subject)
            if identity in measurements:
                raise EvaluationLogError("duplicate_measurement_identity")
            measurements.add(identity)
        if isinstance(record, GroundTruthRecord):
            identity = (record.evaluation_revision, record.case_id, "ground_truth")
            if identity in measurements:
                raise EvaluationLogError("duplicate_ground_truth_identity")
            measurements.add(identity)
        seen[record.record_id] = entry


class EvaluationLog:
    """One private JSONL channel with explicit create/read/append boundaries.

    Measurement and ground-truth logs use different files/channels. There is no
    planner export API. Callers must not pass these private records to a model.
    A retained expected head detects suffix removal and stale concurrent callers.
    """

    def __init__(self, private_root: Path, name: str, *, channel: Literal["measurements", "ground_truth"] = "measurements"):
        if channel not in ("measurements", "ground_truth") or not isinstance(name, str) or len(name) > 128:
            raise EvaluationLogError("invalid_log_location")
        try:
            _safe_path(name)
        except ValueError:
            raise EvaluationLogError("invalid_log_location") from None
        reserved = {"CON", "PRN", "AUX", "NUL", *(f"COM{i}" for i in range(1, 10)), *(f"LPT{i}" for i in range(1, 10))}
        if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]*\.jsonl", name) or name.split(".")[0].upper() in reserved:
            raise EvaluationLogError("single_jsonl_name_required")
        self.root = Path(private_root).absolute()
        self.path, self.lock = self.root / name, self.root / (name + ".lock")
        self.channel = channel

    @contextmanager
    def _locked(self):
        for part in (*reversed(self.root.parents), self.root):
            info = part.lstat()
            if not stat.S_ISDIR(info.st_mode) or stat.S_ISLNK(info.st_mode) or getattr(info, "st_file_attributes", 0) & 0x400:
                raise EvaluationLogError("private_directory_required")
        try:
            fd = os.open(self.lock, os.O_CREAT | os.O_EXCL | os.O_WRONLY | getattr(os, "O_BINARY", 0), 0o600)
        except FileExistsError:
            raise EvaluationLogError("writer_locked") from None
        owned = os.fstat(fd)
        try:
            yield
        finally:
            os.close(fd)
            current = self.lock.lstat()
            if (current.st_dev, current.st_ino) != (owned.st_dev, owned.st_ino):
                raise EvaluationLogError("lock_identity_changed")
            self.lock.unlink()

    def create(self):
        """Never open an existing file for truncation or replacement."""
        with self._locked():
            try:
                fd = os.open(self.path, os.O_CREAT | os.O_EXCL | os.O_WRONLY | getattr(os, "O_BINARY", 0), 0o600)
            except FileExistsError:
                raise EvaluationLogError("log_already_exists") from None
            os.close(fd)

    def _read(self):
        before = _regular(self.path)
        if before.st_size > MAX_LOG_BYTES:
            raise EvaluationLogError("log_size_limit")
        fd = os.open(self.path, os.O_RDONLY | getattr(os, "O_BINARY", 0) | getattr(os, "O_NOFOLLOW", 0))
        with os.fdopen(fd, "rb") as stream:
            if _stat_identity(os.fstat(stream.fileno())) != _stat_identity(before):
                raise EvaluationLogError("file_identity_changed")
            data = stream.read(MAX_LOG_BYTES + 1)
            if _stat_identity(os.fstat(stream.fileno())) != _stat_identity(before):
                raise EvaluationLogError("file_changed_during_read")
        if _stat_identity(_regular(self.path)) != _stat_identity(before) or len(data) != before.st_size:
            raise EvaluationLogError("file_changed_during_read")
        if data and not data.endswith(b"\n"):
            raise EvaluationLogError("truncated_tail")
        lines = data.splitlines(keepends=True)
        if len(lines) > MAX_RECORDS or any(len(line) > MAX_LINE_BYTES for line in lines):
            raise EvaluationLogError("record_bound_exceeded")
        entries = []
        for line in lines:
            try:
                entry = Entry.model_validate_json(line)
                if _canonical(entry.model_dump(mode="json")) + b"\n" != line:
                    raise ValueError("noncanonical line")
            except ValueError:
                raise EvaluationLogError("invalid_record_line") from None
            entries.append(entry)
        _validate(entries, self.channel)
        return tuple(entries), before

    def read(self, *, expected_head: str | None):
        with self._locked():
            entries, _ = self._read()
            if (entries[-1].sha256 if entries else None) != expected_head:
                raise EvaluationLogError("head_changed")
            return entries

    def append(self, record: Record, *, expected_head: str | None):
        # Revalidate frozen models too: model_construct/model_copy can bypass
        # Pydantic validation, and caller-owned nested containers must not pass.
        try:
            validated = _RECORD.validate_json(_canonical(record.model_dump(mode="json")))
        except (AttributeError, ValueError, TypeError):
            raise EvaluationLogError("invalid_record") from None
        with self._locked():
            entries, before = self._read()
            if (entries[-1].sha256 if entries else None) != expected_head:
                raise EvaluationLogError("head_changed")
            body = dict(schema_version="evaluation.entry.v1", channel=self.channel, sequence=len(entries),
                previous_sha256=expected_head, record=validated.model_dump(mode="json"))
            entry = Entry.model_validate({**body, "sha256": _digest(_canonical(body))})
            line = _canonical(entry.model_dump(mode="json")) + b"\n"
            if len(line) > MAX_LINE_BYTES or before.st_size + len(line) > MAX_LOG_BYTES or len(entries) >= MAX_RECORDS:
                raise EvaluationLogError("record_bound_exceeded")
            _validate((*entries, entry), self.channel)
            fd = os.open(self.path, os.O_WRONLY | os.O_APPEND | getattr(os, "O_BINARY", 0) | getattr(os, "O_NOFOLLOW", 0))
            try:
                if _stat_identity(os.fstat(fd)) != _stat_identity(before) or _stat_identity(_regular(self.path)) != _stat_identity(before):
                    raise EvaluationLogError("file_identity_changed")
                offset = 0
                while offset < len(line):
                    count = os.write(fd, line[offset:])
                    if count <= 0:
                        raise EvaluationLogError("append_incomplete")
                    offset += count
                os.fsync(fd)
                after = os.fstat(fd)
                if after.st_size != before.st_size + len(line) or _stat_identity(_regular(self.path)) != _stat_identity(after):
                    raise EvaluationLogError("file_changed_during_append")
            finally:
                os.close(fd)
            return entry
