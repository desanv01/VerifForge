"""Trusted fixed native bundle preparation; default CLI never opens a database.

Only the two reviewed extracted directory inventories are supported. No archive
extraction, copied-code loading, process dispatch or native tool execution.
"""
from __future__ import annotations

import argparse
from dataclasses import dataclass, field
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import stat
import sys
from types import MappingProxyType

from pydantic import TypeAdapter

from veriforge.contracts import _safe_path
from veriforge.contracts.engineering import EvidenceReference, SystemManifest
from veriforge.contracts.native_tools import (
    ArtifactSelection, FirmwareInputs, FirmwareProgram, FirmwareRecordedReport, FirmwareSources,
    HostedOrigin, Identifier, ProducerAttemptLocator, ProducerCaseLocator, RecordedArtifactBinding,
    RiscvInputs, RiscvRecordedReport,
)
from . import native_tool_records as native, records

_VALIDATOR_HASH = "083e9202c0f601ff1b80de7b6010e2aca250c1e5d33bf4de6f92c15a4996dea8"
_LEAF_HASH = "7060474806480b7a3cd92a63ce0bc4d99b46c4731c258a42190605d8832e9471"
_IDENTIFIER = TypeAdapter(Identifier)
_SEAL = object()
_RV_CASES = (
    ("control", "attempt-ba01be2c331649828d66ffcca63365ba", "rv32i-control-v1"),
    ("mutant", "attempt-3507c622a1f143d894f819a0958c0f6a", "rv32i-functional-register-mutant-v1"),
    ("control-replay", "attempt-e845135395204d61b526d707bae1ef2f", "rv32i-control-v1"),
)
_FW_CASES = (("control", "fw-regmap-control-v1"), ("bad", "fw-regmap-offset-bug-v1"),
    ("candidate", "fw-regmap-offset-candidate-v1"), ("bad-replay", "fw-regmap-offset-bug-v1"),
    ("candidate-replay", "fw-regmap-offset-candidate-v1"))
_RV_INPUTS = MappingProxyType(dict(plan="experiment.json", profile="profile.json", source_manifest="source-manifest.json",
    checker_manifest="checker-manifest.json", environment="environment.json", raw_rvfi="dut/rvfi.json",
    raw_spike="reference/commit.log", normalized_pair="retirement.json", comparison="comparison.json",
    object="smoke.o", elf="smoke.elf", binary="smoke.bin"))
_FW_PROGRAM = MappingProxyType(dict(start_object="start.o", probe_object="independent_probe.o", main_object="main.o",
    elf="firmware.elf", binary="firmware.bin", map="firmware.map", disassembly="disassembly.txt", elf_attributes="elf_headers.txt"))
_FW_SOURCES = MappingProxyType(dict(main="main.c", start="start.S", probe="independent_probe.S", linker_script="link.ld",
    wrapper="firmware_top.sv", core_wrapper="picorv32_top.sv", core="picorv32.v", peripheral="native_registers.sv"))


@dataclass(frozen=True)
class _Case:
    family: str
    domain: str
    source_commit: str
    run_id: int
    artifact_id: int
    receipt_hash: str
    source_manifest_hash: str


_CASES = MappingProxyType({
    "riscv-40920450439": _Case("riscv-40920450439", "riscv", "44b8b5e40ce7841f076e4178df13f38ecdbd05fd", 40920450439, 12919420477,
        "616d07c02df1d437c09c2d6b578fcc0b6e1fd8ef7eb9b2ea60c33c9107247429", "474e91456137cc12aecae1b28bd64fae43c8a701381041460bb52282685e3f23"),
    "firmware-40929665294": _Case("firmware-40929665294", "firmware", "5565985bbe2bb2a0be869d366baa48c715057657", 40929665294, 12922737365,
        "324291029545e9783a040452e7698067e09c29717d21c5c52a4ec842dda3ee13", "fc727ccb8d52b1fb1b76e1dddf95b647954a7f0779b95f31254addc36b131bb3"),
})


class NativeImportError(ValueError):
    """Fixed diagnostic codes only; never emit paths, bytes or driver text."""

    def __init__(self, stage, code):
        self.stage, self.code = stage, code
        super().__init__(f"{stage}: {code}")


def _require(condition, stage, code):
    if not condition:
        raise NativeImportError(stage, code)


def _sha(data):
    return hashlib.sha256(data).hexdigest()


def _identity(info):
    # Windows path stat and descriptor fstat expose different ctime semantics.
    return info.st_dev, info.st_ino, info.st_mode, info.st_size, info.st_mtime_ns


def _no_links(path):
    for part in (*reversed(path.parents), path):
        info = part.lstat()
        _require(not stat.S_ISLNK(info.st_mode) and not getattr(info, "st_file_attributes", 0) & 0x400,
            "input", "link_or_reparse_point")


def _read_regular(path):
    _no_links(path)
    before = path.lstat()
    _require(stat.S_ISREG(before.st_mode) and before.st_nlink == 1, "input", "single_regular_file_required")
    _require(before.st_size <= records.MAX_ARTIFACT_BYTES, "input", "artifact_size_limit")
    flags = os.O_RDONLY | getattr(os, "O_BINARY", 0) | getattr(os, "O_NOFOLLOW", 0)
    descriptor = os.open(path, flags)
    with os.fdopen(descriptor, "rb") as stream:
        _require(_identity(os.fstat(stream.fileno())) == _identity(before), "input", "file_changed_during_open")
        data = stream.read(records.MAX_ARTIFACT_BYTES + 1)
        _require(_identity(os.fstat(stream.fileno())) == _identity(before), "input", "file_changed_during_read")
    _no_links(path)
    _require(_identity(path.lstat()) == _identity(before) and len(data) == before.st_size, "input", "file_changed_during_read")
    _require(len(data) <= records.MAX_ARTIFACT_BYTES, "input", "artifact_size_limit")
    return data


def _snapshot(root):
    """Bounded directory snapshot; paths remain data identifiers, not commands."""
    _require(".." not in Path(root).parts, "input", "parent_traversal_forbidden")
    root = Path(root).absolute()
    raw, folded, directory_states = {}, set(), []
    total, entries = 0, 0
    _no_links(root)
    _require(stat.S_ISDIR(root.lstat().st_mode), "input", "directory_required")
    pending = [(root, 0)]
    while pending:
        directory, depth = pending.pop()
        _require(depth <= 16 and len(directory_states) < 4096, "inventory", "directory_bound")
        _no_links(directory)
        before = directory.lstat()
        _require(stat.S_ISDIR(before.st_mode), "input", "directory_changed")
        with os.scandir(directory) as listing:
            for item in listing:
                entries += 1
                _require(entries <= 8192, "inventory", "entry_count_limit")
                path = directory / item.name
                relative = path.relative_to(root).as_posix()
                _safe_path(relative)
                _require(len(relative) <= 512 and relative.casefold() not in folded, "inventory", "path_collision_or_length")
                folded.add(relative.casefold())
                info = item.stat(follow_symlinks=False)
                _require(not stat.S_ISLNK(info.st_mode) and not getattr(info, "st_file_attributes", 0) & 0x400, "input", "link_or_reparse_point")
                if stat.S_ISDIR(info.st_mode):
                    pending.append((path, depth + 1))
                else:
                    _require(len(raw) < 4096, "inventory", "file_count_limit")
                    _require(total + info.st_size <= records.MAX_TOTAL_BYTES, "inventory", "total_read_limit")
                    data = _read_regular(path)
                    _require(total + len(data) <= records.MAX_TOTAL_BYTES, "inventory", "total_read_limit")
                    total += len(data)
                    raw[relative] = data
        directory_states.append((directory, before))
    for directory, before in directory_states:
        _no_links(directory)
        _require(_identity(directory.lstat()) == _identity(before), "input", "directory_changed_during_snapshot")
    return MappingProxyType(dict(sorted(raw.items())))


def _installed(source=None):
    """Freshly inspect fixed installed files; never import a bundle's code."""
    validator = Path(native.__file__).absolute()
    _require(_sha(_read_regular(validator)) == _VALIDATOR_HASH, "implementation", "unsupported_validator_version")
    leaf = validator.parents[1] / "contracts/native_tools.py"
    _require(_sha(_read_regular(leaf)) == _LEAF_HASH, "implementation", "unsupported_leaf_version")
    if source is not None:
        repository = validator.parents[3]
        for name, digest in source.items():
            if (name.startswith("src/veriforge/riscv/") or name.startswith("src/veriforge/firmware/") or
                    name == "scripts/ci_firmware_acceptance.py"):
                _safe_path(name)
                _require(_sha(_read_regular(repository / name)) == digest, "implementation", "installed_domain_version_differs")
    return _VALIDATOR_HASH


def _inventory(spec, raw):
    _require("receipt-sha256.json" in raw and "source-manifest.json" in raw, "inventory", "fixed_manifest_missing")
    _require(_sha(raw["receipt-sha256.json"]) == spec.receipt_hash and _sha(raw["source-manifest.json"]) == spec.source_manifest_hash,
        "inventory", "reviewed_receipt_or_source_differs")
    receipt = native._json(raw["receipt-sha256.json"])
    _require(set(raw) == set(receipt) | {"receipt-sha256.json"}, "inventory", "exact_original_inventory_required")
    for name, digest in receipt.items():
        _safe_path(name)
        _require(type(digest) is str and _sha(raw[name]) == digest, "inventory", "original_file_digest_differs")
    source = native._json(raw["source-manifest.json"])
    _installed(source)
    return source


@dataclass(frozen=True, init=False)
class PreparedNativeBundle:
    family: str
    admitted: native.AdmittedNativeInvestigation = field(repr=False)
    authority: native.TrustedExternalAdmissionAuthority = field(repr=False)
    _seal: object = field(repr=False)

    def __new__(cls, *args, **kwargs):
        raise TypeError("prepared native bundles require fixed trusted preparation")


class _Assembler:
    """Fixed producer mappings with dependency discovery by registered inspectors."""

    def __init__(self, spec, raw, project_id, revision_id):
        self.spec, self.raw = spec, raw
        self.project_id, self.revision_id = project_id, revision_id
        self.needed, self.derived, self.original_ids = set(), {}, {}
        self.references = {}
        case_ids = {row[0]: row[1] for row in _RV_CASES}
        for name, data in raw.items():
            label = name.split("/", 1)[0]
            self.references[name] = EvidenceReference(artifact_id="native-" + spec.domain + "-" + _sha(name.encode())[:24],
                revision_id=spec.source_commit, attempt_id=case_ids.get(label) if spec.domain == "riscv" else None, sha256=_sha(data))
        self.names = {records._key(ref): name for name, ref in self.references.items()}
        self.origin = HostedOrigin(source_commit=spec.source_commit, run_id=spec.run_id, artifact_id=spec.artifact_id,
            extracted_receipt_ref=self.ref("receipt-sha256.json"))
        if spec.domain == "riscv":
            self.locators = {label: ProducerAttemptLocator(origin=self.origin, case_label=label, native_attempt_id=attempt,
                wire_experiment_id=experiment, raw_result_ref=self.ref(label + "/result.json")) for label, attempt, experiment in _RV_CASES}
            for label in self.locators:
                result = native._json(self.path(label + "/result.json"))
                for artifact in result["artifacts"]:
                    self.original_ids[label + "/" + artifact["path"]] = artifact["artifact_id"]
        else:
            self.locators = {label: ProducerCaseLocator(origin=self.origin, case_label=label, fixture_id=fixture,
                execution_ref=self.ref(label + "/execution.json")) for label, fixture in _FW_CASES}

    def ref(self, name):
        _require(name in self.references, "closure", "required_producer_file_missing")
        return self.references[name]

    def path(self, name, digest=None, *, size=None, role=None, original_id=None):
        ref = self.ref(name)
        _require(digest is None or ref.sha256 == digest, "closure", "raw_dependency_hash_differs")
        _require(size is None or len(self.raw[name]) == size, "closure", "raw_dependency_size_differs")
        _require(original_id is None or self.original_ids.get(name) == original_id, "closure", "original_artifact_id_differs")
        self.needed.add(name)
        return self.raw[name]

    def resolve(self, reference, role):
        name = self.names.get(records._key(reference))
        _require(name is not None and self.ref(name) == reference, "closure", "reference_differs")
        return self.path(name)

    def metadata(self, label, value):
        data = records._encode(value)
        ref = EvidenceReference(artifact_id="native-" + self.spec.domain + "-" + label, revision_id=self.revision_id, sha256=_sha(data))
        self.derived[records._key(ref)] = records.ArtifactBytes(reference=ref, data=data)
        return ref

    def inspect_cases(self):
        source = native._source_closure(self.path)
        cases = []
        for label, locator in self.locators.items():
            if self.spec.domain == "riscv":
                inputs = RiscvInputs(**{field: self.ref(label + "/" + name) for field, name in _RV_INPUTS.items()})
                cases.append(native._riscv_case(locator, inputs, self.resolve, self.path, source))
            else:
                inputs = FirmwareInputs(preparation=self.ref(label + "/preparation.json"), observation=self.ref(label + "/observation.json"),
                    check=self.ref(label + ".check.json"), register_contract=self.ref(label + "/register-contract.json"), selected_header=self.ref(label + "/registers.h"),
                    bad_base_header=self.ref(label + "/original-bad-registers.h"), source_manifest=self.ref("source-manifest.json"), tool_metadata=self.ref("tool-metadata.json"),
                    program=FirmwareProgram(**{field: self.ref(label + "/" + name) for field, name in _FW_PROGRAM.items()}),
                    sources=FirmwareSources(**{field: self.ref(label + "/" + name) for field, name in _FW_SOURCES.items()}))
                original = self.locators[label.removesuffix("-replay")].execution_ref if label.endswith("-replay") else None
                cases.append(native._firmware_case(locator, inputs, self.resolve, self.path, source, original_ref=original))
        self.needed.update(("receipt-sha256.json", "summary.json"))
        if self.spec.domain == "firmware":
            self.needed.add("candidate/candidate.diff")
        return tuple(cases)

    def role(self, name):
        relative = name.split("/", 1)[-1]
        if name == "receipt-sha256.json": return "receipt"
        if name == "summary.json": return "replay_record"
        if name.startswith("source/") or name == "source-manifest.json": return "source_snapshot"
        if name.endswith("/result.json"): return "native_result"
        if name.endswith("/execution.json"): return "native_execution"
        if relative == "comparison.json" or name.endswith(".check.json"): return "native_check"
        if relative in ("dut/rvfi.json", "retirement.json", "observation.json"): return "native_trace"
        if relative == "reference/commit.log": return "reference_trace"
        if relative == "candidate.diff": return "candidate_diff"
        if relative in (*_FW_PROGRAM.values(), "smoke.o", "smoke.elf", "smoke.bin"): return "program"
        if relative in (*_FW_SOURCES.values(), "registers.h", "original-bad-registers.h", "source-manifest.json"): return "source_snapshot"
        return "configuration"

    def finish(self, validator_hash):
        cases = self.inspect_cases()
        facts, attachments = [], []
        source_refs = [self.ref("source-manifest.json")]
        for case in cases:
            ref = self.metadata("case-" + case.locator.case_label, case)
            primary = case.locator.raw_result_ref if self.spec.domain == "riscv" else case.inputs.check
            source_refs.append(primary)
            fact_id = "native-" + self.spec.domain + "-" + case.locator.case_label
            attachments.append(native.NativeToolAttachment(fact_id=fact_id, domain=self.spec.domain, native_schema_id=case.schema_version, reference=ref))
            facts.append(dict(fact_id=fact_id, domain=self.spec.domain, native_schema_id=case.schema_version, native_record_hash=ref.sha256,
                native_record=ref, category="observation", provenance=dict(state="complete", sources=[dict(artifact=primary, location=dict(kind="json_pointer", pointer=""))],
                    extraction=dict(method="native_parser", extractor_id=native.VALIDATOR_ID, extractor_version=native.VALIDATOR_VERSION, implementation_hash=validator_hash))))
        manifest = SystemManifest(manifest_id="native-" + self.spec.domain + "-observations", revision_id=self.revision_id, facts=tuple(facts))
        manifest_ref = self.metadata("manifest", manifest)
        revision = records.EngineeringProjectRevision(project_id=self.project_id, revision_id=self.revision_id,
            source_refs=tuple(source_refs), configuration_hash=self.ref("source-manifest.json").sha256)
        revision_ref = self.metadata("project-revision", revision)
        selected = []
        for name in sorted(self.needed):
            label = name.split("/", 1)[0]
            if self.spec.domain == "firmware" and name.endswith(".check.json"):
                label = name.removesuffix(".check.json")
            selected.append(RecordedArtifactBinding(ref=self.ref(name), scope=self.locators.get(label, self.origin),
                original_relative_path=name, original_artifact_id=self.original_ids.get(name), role=self.role(name),
                media_type="application/json" if name.endswith(".json") else "application/octet-stream", size_bytes=len(self.raw[name])))
        selected.append(RecordedArtifactBinding(ref=manifest_ref, scope=self.origin, provenance="adapter_generated", role="manifest",
            media_type="application/json", size_bytes=len(self.derived[records._key(manifest_ref)].data)))
        selection = ArtifactSelection(origin=self.origin, inventory=tuple(dict(original_relative_path=name, sha256=_sha(data), size_bytes=len(data)) for name, data in self.raw.items()),
            selected=tuple(selected), omitted_paths=tuple(sorted(set(self.raw) - self.needed)))
        if self.spec.domain == "riscv":
            report = RiscvRecordedReport(manifest_ref=manifest_ref, selection=selection, cases=cases, recompiled_control=dict(
                original=self.locators["control"], repeated=self.locators["control-replay"], record_ref=self.ref("summary.json"), outcome="byte_identical_recompiled_control"))
        else:
            bad, candidate = {case.locator.case_label: case for case in cases}["bad"], {case.locator.case_label: case for case in cases}["candidate"]
            report = FirmwareRecordedReport(manifest_ref=manifest_ref, selection=selection, cases=cases,
                candidate=dict(bad=bad.locator, candidate=candidate.locator, diff_ref=self.ref("candidate/candidate.diff"), original_bad_header_ref=bad.inputs.selected_header,
                    candidate_header_ref=candidate.inputs.selected_header, protected_checker_hash=bad.identity.checker_hash, protected_contract_hash=bad.identity.contract_hash,
                    protected_profile_hash=bad.profile_hash, covered_mmio_indices=tuple(range(9)), outcome="scoped_curated_correction"),
                replays=tuple(dict(original=self.locators[label], repeated=self.locators[label + "-replay"], record_ref=self.ref("summary.json"), outcome="semantic_saved_program_replay") for label in ("bad", "candidate")))
        report_ref = self.metadata("report", report)
        record = native.EngineeringInvestigationV2(investigation_id="recorded-" + self.spec.family, project_id=self.project_id,
            revision_id=self.revision_id, project_revision_ref=revision_ref, manifest_ref=manifest_ref, report_kind=report.schema_version,
            report_ref=report_ref, native_records=tuple(attachments))
        authority = native.create_external_admission_authority(project_id=self.project_id, revision_id=self.revision_id, project_revision_ref=revision_ref,
            manifest_ref=manifest_ref, report_kind=report.schema_version, report_ref=report_ref, locators=tuple(self.locators.values()), validator_implementation_hash=validator_hash)
        artifacts = tuple(records.ArtifactBytes(reference=self.ref(name), data=self.raw[name]) for name in sorted(self.needed)) + tuple(self.derived.values())
        admitted = records.admit_investigation(record, artifacts, authority=authority)
        admitted = records.read_investigation(records.serialize_investigation(admitted), artifacts, authority=authority)
        value = object.__new__(PreparedNativeBundle)
        for name, content in dict(family=self.spec.family, admitted=admitted, authority=authority, _seal=_SEAL).items():
            object.__setattr__(value, name, content)
        return value


def prepare_native_bundle(*, family, bundle_root, project_id, revision_id):
    """Fixed local directory authority; no database is imported or opened."""
    try:
        _require(type(family) is str and family in _CASES, "arguments", "unsupported_family")
        project_id, revision_id = _IDENTIFIER.validate_python(project_id), _IDENTIFIER.validate_python(revision_id)
        validator_hash = _installed()
        raw = _snapshot(bundle_root)
        _inventory(_CASES[family], raw)
        return _Assembler(_CASES[family], raw, project_id, revision_id).finish(validator_hash)
    except NativeImportError:
        raise
    except OSError:
        raise NativeImportError("input", "unavailable_or_changed_input") from None
    except (ValueError, TypeError, KeyError, IndexError, AttributeError, StopIteration):
        raise NativeImportError("admission", "native_consistency_failed") from None


def preparation_summary(prepared):
    _require(type(prepared) is PreparedNativeBundle and getattr(prepared, "_seal", None) is _SEAL, "admission", "sealed_preparation_required")
    admitted = prepared.admitted
    return dict(operation="admission_only", new_execution="not_run", family=prepared.family, project_id=admitted.envelope.record.project_id,
        revision_id=admitted.envelope.record.revision_id, content_sha256=_sha(records.serialize_investigation(admitted)),
        report_kind=admitted.envelope.record.report_kind, artifacts=len(admitted.artifacts), total_bytes=admitted.envelope.total_artifact_bytes,
        omitted_paths=len(admitted.native_report.selection.omitted_paths))


def apply_prepared(session, prepared, *, idempotency_key, at, investigation_id=None):
    """Trusted caller transaction only; never commits or creates a project."""
    from veriforge.core import investigations, recorded_import, service
    from veriforge.core.engineering_catalog import get_engineering_project
    from sqlalchemy.exc import SQLAlchemyError
    try:
        preparation_summary(prepared)
        _require(session.in_transaction() and not (session.new or session.dirty or session.deleted), "transaction", "clean_caller_transaction_required")
        _require(type(idempotency_key) is str and 1 <= len(idempotency_key) <= 128, "arguments", "bounded_key_required")
        _require(isinstance(at, datetime) and at.tzinfo is not None and at.utcoffset() is not None, "arguments", "aware_time_required")
        recorded_import.require_supported_schema(session)
        current_hash = _installed()
        admitted = prepared.admitted
        _require(admitted.envelope.validation.implementation_hash == current_hash, "implementation", "prepared_validator_differs")
        source_ref = next(binding.ref for binding in admitted.native_report.selection.selected if binding.original_relative_path == "source-manifest.json")
        _installed(native._json(next(item.data for item in admitted.artifacts if item.reference == source_ref)))
        record = admitted.envelope.record
        authority = native.create_external_admission_authority(**{**prepared.authority.pins.model_dump(mode="python"), "validator_implementation_hash": current_hash})
        admitted = records.read_investigation(records.serialize_investigation(admitted), admitted.artifacts, authority=authority)
        get_engineering_project(session, record.project_id)
        connection = session.connection()
        if connection.dialect.name == "sqlite" and not connection.connection.driver_connection.in_transaction:
            connection.exec_driver_sql("BEGIN")
        with session.begin_nested():
            summary = investigations.persist_investigation(session, record, admitted.artifacts, authority=authority,
                idempotency_key=idempotency_key, at=at, investigation_id=investigation_id)
            view = investigations.version_view(session, summary["investigation_id"], summary["id"])
            _require(view["envelope"] == admitted.envelope.model_dump(mode="json") and view["content_hash"] == _sha(records.serialize_investigation(admitted))
                and view["native_report"] == admitted.native_report.model_dump(mode="json") and "execution_contexts" not in view and "bridge_report" not in view,
                "readback", "stored_native_admission_differs")
            return {**preparation_summary(prepared), "db_investigation_id": summary["investigation_id"], "db_version_id": summary["id"], "database": "pending_caller_commit"}
    except NativeImportError:
        raise
    except recorded_import.RecordedImportError as error:
        raise NativeImportError(error.stage, error.code) from None
    except (service.NotFound, service.Conflict, SQLAlchemyError):
        raise NativeImportError("storage", "owned_storage_or_content_unavailable") from None
    except (ValueError, TypeError, KeyError, AttributeError, StopIteration):
        raise NativeImportError("admission", "native_readback_or_authority_differs") from None


class _Parser(argparse.ArgumentParser):
    def error(self, message):
        raise NativeImportError("arguments", "invalid_arguments")


def main(argv=None):
    engine = None
    database_outcome = "not_opened"
    try:
        parser = _Parser(description=__doc__)
        parser.add_argument("--family", choices=tuple(_CASES), required=True)
        parser.add_argument("--bundle-root", type=Path, required=True)
        parser.add_argument("--project-id", required=True)
        parser.add_argument("--revision-id", required=True)
        parser.add_argument("--apply", action="store_true")
        parser.add_argument("--idempotency-key")
        parser.add_argument("--investigation-id")
        args = vars(parser.parse_args(argv))
        apply, key, parent = args.pop("apply"), args.pop("idempotency_key"), args.pop("investigation_id")
        _require(apply and key is not None and 1 <= len(key) <= 128 or not apply and key is None and parent is None,
            "arguments", "apply_requires_key_preparation_accepts_no_key_or_parent")
        if parent is not None:
            _IDENTIFIER.validate_python(parent)
        prepared = prepare_native_bundle(**args)
        if apply:
            from veriforge.core.recorded_import import _configured_engine, RecordedImportError
            from veriforge.core.database import session_factory
            try:
                engine = _configured_engine()
            except RecordedImportError as error:
                raise NativeImportError(error.stage, error.code) from None
            database_outcome = "commit_not_confirmed"
            with session_factory(engine).begin() as session:
                result = apply_prepared(session, prepared, idempotency_key=key, at=datetime.now(timezone.utc), investigation_id=parent)
            database_outcome = "committed"
            result["database"] = "committed"
        else:
            result = {**preparation_summary(prepared), "database": "not_opened"}
        print(json.dumps({"status": "ok", **result}, sort_keys=True))
        return 0
    except NativeImportError as error:
        print(json.dumps({"status": "rejected", "stage": error.stage, "code": error.code}, sort_keys=True))
    except Exception:
        print(json.dumps({"status": "rejected", "stage": "operation", "code": "preparation_or_commit_failed"}, sort_keys=True))
    finally:
        if engine is not None:
            try:
                engine.dispose()
            except Exception:
                # Cleanup cannot undo a known commit or expose driver text.
                # Keep stdout's single operation result and its return code.
                print(json.dumps({"status": "warning", "stage": "cleanup", "code": "engine_disposal_failed",
                    "database": database_outcome}, sort_keys=True), file=sys.stderr)
    return 2


__all__ = ["NativeImportError", "PreparedNativeBundle", "prepare_native_bundle", "preparation_summary", "apply_prepared", "main"]
