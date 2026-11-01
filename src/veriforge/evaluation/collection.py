"""Offline private collection of existing FIFO evaluation ledgers and blobs.

Never dispatches a provider/tool or scores a dataset. Manifest validation checks
retained bytes and frozen metadata, not execution authenticity or clean replay.
The caller supplies a trusted private directory/ACL and independently retained
heads. No credentials, shell commands or executable plugins are accepted.
"""
from __future__ import annotations

import hashlib
import os
from pathlib import Path
import re
import stat
from typing import Annotated, Literal

from pydantic import BaseModel, Field, field_validator, model_validator

from veriforge.contracts import ArtifactReference, Identifier, Sha256, canonical_hash
from . import harness as h, records as r, storage

MAX_PACKAGE_BYTES = 64 * 1024 * 1024
MAX_PACKAGE_FILES = 4096


class CollectionError(ValueError):
    """Fixed diagnostics without private paths or raw payloads."""


def require(condition, code):
    if not condition:
        raise CollectionError(code)


def relative_path(value):
    # Portable, bounded, no Windows aliases/drives or linked-path traversal.
    require(isinstance(value, str) and 1 <= len(value) <= 240, "invalid_relative_path")
    parts = value.split("/")
    reserved = {"CON", "PRN", "AUX", "NUL", *(f"COM{i}" for i in range(1, 10)), *(f"LPT{i}" for i in range(1, 10))}
    require(all(re.fullmatch(r"[A-Za-z0-9_][A-Za-z0-9_.-]*", p) and p not in (".", "..")
                and not p.endswith(".") and p.split(".")[0].upper() not in reserved for p in parts), "invalid_relative_path")
    return value


def directory(path):
    path = Path(path).absolute()
    for part in (*reversed(path.parents), path):
        info = part.lstat()
        require(stat.S_ISDIR(info.st_mode) and not stat.S_ISLNK(info.st_mode)
                and not getattr(info, "st_file_attributes", 0) & 0x400, "private_directory_required")
    return path


def read_bytes(path, maximum):
    before = storage.regular_file(path)
    require(before.st_size <= maximum, "file_bound")
    with Path(path).open("rb") as stream:
        require(storage.file_identity(os.fstat(stream.fileno())) == storage.file_identity(before), "file_changed")
        raw = stream.read(maximum + 1)
        require(storage.file_identity(os.fstat(stream.fileno())) == storage.file_identity(before), "file_changed")
    require(storage.file_identity(storage.regular_file(path)) == storage.file_identity(before)
            and len(raw) == before.st_size, "file_changed")
    return raw


def read_json(path, maximum=1024 * 1024):
    return h.ledger_json(read_bytes(path, maximum))


class CaseBinding(r.RecordModel):
    case_id: h.Alias
    source_sha256: Sha256
    checker_sha256: Sha256
    contract_sha256: Sha256
    classification: Literal["control", "seeded_defect", "user_reported_defect", "upstream_defect", "equivalent_mutant", "invalid", "unresolved"]
    ground_truth_review_sha256: Sha256


class Version(r.RecordModel):
    name: Identifier
    version: Annotated[str, Field(min_length=1, max_length=256)] | None
    identity_sha256: Sha256 | None = None


class ReleaseIdentity(r.RecordModel):
    source_commit: Annotated[str, Field(pattern=r"^[0-9a-f]{40}$")]
    source_state: Literal["clean", "dirty", "unknown"]
    dirty_patch_sha256: Sha256 | None
    versions: Annotated[tuple[Version, ...], Field(min_length=1, max_length=32)]

    @model_validator(mode="after")
    def distinct_versions(self):
        require(len({v.name for v in self.versions}) == len(self.versions), "duplicate_version")
        require((self.source_state == "dirty") == (self.dirty_patch_sha256 is not None), "source_patch_identity")
        return self


class Capture(r.RecordModel):
    trial: h.Trial
    directory: str
    expected_head: Sha256
    _path = field_validator("directory")(relative_path)


class Exclusion(r.RecordModel):
    trial: h.Trial
    reason: Literal["unsupported", "equivalent_mutant", "invalid", "infrastructure", "not_run", "cancelled", "missing_evidence"]
    explanation: Annotated[str, Field(min_length=1, max_length=512)]
    review_sha256: Sha256


class CollectionRequest(r.RecordModel):
    schema_version: Literal["evaluation.collection-request.v1"] = "evaluation.collection-request.v1"
    study: h.Study
    cases: Annotated[tuple[CaseBinding, ...], Field(min_length=1, max_length=8)]
    release: ReleaseIdentity
    captures: Annotated[tuple[Capture, ...], Field(max_length=128)]
    exclusions: Annotated[tuple[Exclusion, ...], Field(max_length=128)]

    @model_validator(mode="after")
    def complete_matrix(self):
        require(len({c.case_id for c in self.cases}) == len(self.cases)
                and {c.case_id for c in self.cases} == {c.case_id for c in self.study.cases}, "case_binding_matrix")
        coordinates = [canonical_hash(c.trial) for c in (*self.captures, *self.exclusions)]
        require(len(set(coordinates)) == len(coordinates)
                and set(coordinates) == {canonical_hash(t) for t in h.matrix(self.study)}, "trial_matrix_incomplete_or_duplicate")
        require(len({c.directory.casefold() for c in self.captures}) == len(self.captures), "duplicate_capture_directory")
        versions = {v.name for v in self.release.versions}
        required = {"python", "verilator", "cocotb", "gcc", "runtime_image"}
        if any(s.startswith("meta_") for s in self.study.strategies):
            required.add("model")
        require(required <= versions, "version_inventory_incomplete")
        return self


class FileIdentity(r.RecordModel):
    path: str
    sha256: Sha256
    size_bytes: Annotated[int, Field(strict=True, ge=0, le=MAX_PACKAGE_BYTES)]
    _path = field_validator("path")(relative_path)


class ReproductionManifest(r.RecordModel):
    schema_version: Literal["evaluation.reproduction.v1"] = "evaluation.reproduction.v1"
    execution: Literal["not_run"] = "not_run"
    disclosure: Literal["private_unreviewed_export"] = "private_unreviewed_export"
    request: CollectionRequest
    files: Annotated[tuple[FileIdentity, ...], Field(min_length=1, max_length=MAX_PACKAGE_FILES)]

    @model_validator(mode="after")
    def unique_inventory(self):
        require(len({f.path.casefold() for f in self.files}) == len(self.files), "duplicate_package_path")
        require(sum(f.size_bytes for f in self.files) <= MAX_PACKAGE_BYTES, "package_bound")
        return self


def artifact_refs(value):
    """Walk typed measurement records only, never private ground-truth files."""
    if isinstance(value, ArtifactReference):
        yield value
    elif isinstance(value, BaseModel):
        for name in type(value).model_fields:
            yield from artifact_refs(getattr(value, name))
    elif isinstance(value, (tuple, list)):
        for item in value:
            yield from artifact_refs(item)


def trial_records(request, capture, root):
    source = directory(root / capture.directory)
    entries = r.EvaluationLog(source, "measurements.jsonl").read(expected_head=capture.expected_head)
    require(bool(entries), "empty_capture")
    records = tuple(e.record for e in entries)
    require(all(isinstance(x, (r.AttemptRecord, r.ModelRoundRecord, r.CampaignRecord, r.CorrectionRecord)) for x in records), "wrong_collection_channel")
    binding = next(c for c in request.cases if c.case_id == capture.trial.case_id)
    study, trial = request.study, capture.trial
    expected = dict(evaluation_revision=canonical_hash(study), campaign_id=h.identity(canonical_hash(study), trial.model_dump(mode="json")),
                    case_id=trial.case_id, strategy=trial.strategy, seed=trial.seed, replicate=trial.replicate,
                    domain="rtl", profile="fifo-v1", source_sha256=binding.source_sha256,
                    checker_sha256=binding.checker_sha256, contract_sha256=binding.contract_sha256,
                    environment_sha256=study.environment_sha256, strategy_sha256=study.strategy_sha256,
                    information_policy_sha256=study.information_policy_sha256, visibility=study.visibility,
                    context_audit=study.context_audit)
    allocated = h.amounts(attempts=study.limits.attempts, cycles=study.limits.cycles, tool_seconds=study.limits.tool_seconds,
                          artifact_bytes=study.limits.artifact_bytes, model_tokens=study.limits.model_tokens if trial.strategy.startswith("meta_") else 0)
    for record in records:
        if isinstance(record, r.CorrectionRecord):
            continue
        require(all(getattr(record.scope, k) == v for k, v in expected.items()), "frozen_scope_differs")
        require(record.budget.profile_sha256 == canonical_hash(study.limits) and record.budget.allocated == allocated, "frozen_budget_differs")
        if isinstance(record, r.AttemptRecord):
            require(record.evidence_kind == study.tool_evidence_kind, "tool_evidence_kind_differs")
            versions = {v.name: v.version for v in request.release.versions}
            require(all(versions.get(v.name) == v.version for v in record.attempt.tool_versions), "captured_version_differs")
        if isinstance(record, r.ModelRoundRecord) and record.status != "not_called":
            require(record.evidence_kind == study.model_evidence_kind and record.requested_model == study.requested_model
                    and record.generation_parameters_sha256 == study.generation_parameters_sha256, "model_identity_differs")
    campaigns = [x for x in records if isinstance(x, r.CampaignRecord)]
    require(len(campaigns) <= 1, "multiple_campaign_summaries")
    if campaigns:
        campaign = campaigns[0]
        require({ref.record_id for ref in campaign.attempts} == {x.record_id for x in records if isinstance(x, r.AttemptRecord)}
                and {ref.record_id for ref in campaign.model_rounds} == {x.record_id for x in records if isinstance(x, r.ModelRoundRecord)}, "unaccounted_measurement")
        index = records.index(campaign)
        require(all(isinstance(x, r.CorrectionRecord) for x in records[index + 1:]), "measurement_after_terminal")
    refs = {}
    for record in records:
        for ref in artifact_refs(record):
            require(ref.path == "blob-" + ref.sha256 + ".bin" and ref.artifact_id == "blob-" + ref.sha256, "non_blob_evidence_reference")
            if ref.path in refs:
                require(refs[ref.path] == ref, "conflicting_blob_reference")
            refs[ref.path] = ref
            require(len(refs) <= MAX_PACKAGE_FILES and sum(x.size_bytes for x in refs.values()) <= MAX_PACKAGE_BYTES,
                    "package_bound")
    return entries, {name: h.BlobStore(source).read(ref) for name, ref in refs.items()}


def capture_files(request, root):
    """Validate a complete frozen matrix and return a private byte snapshot.

    Missing trials need explicit reviewed exclusions; incomplete captured trials
    remain incomplete. No hidden truth/witness is exported to a planner.
    """
    request = CollectionRequest.model_validate_json(h.encoded(request))
    root = directory(root)
    output, summaries, projected = {}, [], []

    def put(name, raw):
        require(len(output) < MAX_PACKAGE_FILES and sum(map(len, output.values())) + len(raw) <= MAX_PACKAGE_BYTES, "package_bound")
        require(name not in output, "duplicate_package_path")
        output[name] = raw

    for index, item in enumerate(request.captures):
        entries, blobs = trial_records(request, item, root)
        target = f"trials/{index:03d}"
        put(target + "/measurements.jsonl", b"".join(storage.canonical_json(e.model_dump(mode="json")) + b"\n" for e in entries))
        for name, raw in sorted(blobs.items()):
            put(target + "/" + name, raw)
        projected.append(item.model_copy(update={"directory": target}))
        measurements = [e.record for e in entries if isinstance(e.record, r.Measurement)]
        terminal = next((x for x in measurements if isinstance(x, r.CampaignRecord)), None)
        kinds = sorted({x.evidence_kind for x in measurements if not isinstance(x, r.CampaignRecord)})
        summaries.append(dict(trial=item.trial.model_dump(mode="json"), state="terminal" if terminal else "incomplete",
                              evidence_kinds=kinds, campaign=terminal.model_dump(mode="json") if terminal else None,
                              ledger_head=item.expected_head, record_count=len(entries)))
    report = dict(schema_version="evaluation.collection.v1", status="collected_not_scored", execution="not_run",
                  calibration=request.study.calibration, trials=summaries,
                  exclusions=[x.model_dump(mode="json") for x in request.exclusions],
                  planned_trials=len(h.matrix(request.study)), captured_trials=len(summaries),
                  missing_versions=[x.name for x in request.release.versions if x.version is None],
                  limitations=["private_unreviewed_export", "declared_evidence_is_not_remote_attestation",
                               "manifest_validation_is_not_clean_tool_reproduction", "no_comparative_score_computed"])
    put("collection.json", storage.canonical_json(report) + b"\n")
    projected_request = request.model_copy(update={"captures": tuple(projected)})
    files = tuple(FileIdentity(path=name, sha256=h.digest(raw), size_bytes=len(raw)) for name, raw in sorted(output.items()))
    manifest = ReproductionManifest(request=projected_request, files=files)
    return manifest, output


def write_new(path, raw):
    fd = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY | getattr(os, "O_BINARY", 0), 0o600)
    try:
        offset = 0
        while offset < len(raw):
            count = os.write(fd, raw[offset:])
            require(count > 0, "export_write_failed")
            offset += count
        os.fsync(fd)
    finally:
        os.close(fd)


def export_collection(request, root, output):
    """Create a new directory only. Partial output is preserved on failure."""
    manifest, files = capture_files(request, root)
    output = Path(output).absolute()
    directory(output.parent)
    output.mkdir(mode=0o700, exist_ok=False)
    for name, raw in files.items():
        path = output / name
        path.parent.mkdir(parents=True, exist_ok=True)
        directory(path.parent)
        write_new(path, raw)
    raw = storage.canonical_json(manifest.model_dump(mode="json")) + b"\n"
    require(len(raw) <= 1024 * 1024, "manifest_bound")
    write_new(output / "release-manifest.json", raw)
    return manifest, h.digest(raw)


def validate_reproduction(root, *, expected_manifest_sha256):
    """Read/verify a sealed private bundle; never execute reproduction commands."""
    root = directory(root)
    raw = read_bytes(root / "release-manifest.json", 1024 * 1024)
    require(h.digest(raw) == expected_manifest_sha256, "manifest_identity_differs")
    manifest = ReproductionManifest.model_validate(h.ledger_json(raw))
    expected, files = capture_files(manifest.request, root)
    require(expected == manifest, "manifest_projection_differs")
    for name, data in files.items():
        require(read_bytes(root / name, MAX_PACKAGE_BYTES) == data, "package_content_differs")
    paths = set()
    for path in root.rglob("*"):
        require(len(paths) < MAX_PACKAGE_FILES + 256, "package_bound")
        if path.is_dir():
            directory(path)
        else:
            storage.regular_file(path)
        paths.add(path.relative_to(root).as_posix())
    expected_files = set(files) | {"release-manifest.json"}
    actual_files = {p for p in paths if (root / p).is_file()}
    require(actual_files == expected_files, "unlisted_package_file")
    return dict(status="manifest_validation_passed", execution="not_run", clean_tool_reproduction="not_established",
                manifest_sha256=expected_manifest_sha256, files=len(expected_files),
                planned_trials=len(h.matrix(manifest.request.study)), disclosure=manifest.disclosure)
