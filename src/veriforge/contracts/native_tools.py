"""Bounded recorded-tool data, not execution authority or remote attestation.

Raw blob hashes, parsed identities and semantic hashes remain distinct. These
leaf contracts deliberately have no local campaign/attempt association fields.
Byte parsing and registered-checker validation belong to the importer.
"""
from typing import Annotated, Literal

from collections.abc import Mapping

from pydantic import BeforeValidator, Field, StrictBool, StrictInt, field_validator, model_validator

from . import CheckerIdentity as _CheckerIdentity, EvidenceIdentity as _EvidenceIdentity
from . import Identifier as _Identifier, Sha256 as _Sha256, WireModel, _safe_path
from .engineering import EvidenceReference as _EvidenceReference


def _strict_shared_strings(value):
    """Keep shared v1 models compatible without their bytes-to-text coercion.

    All fields in these three shared records are strings or null. Inspect raw
    mappings before their shared validators normalize them; also support existing
    shared model instances without changing their type or wire representation.
    """
    if isinstance(value, Mapping):
        values = value.values()
    elif isinstance(value, (_CheckerIdentity, _EvidenceIdentity, _EvidenceReference)):
        values = (getattr(value, name) for name in type(value).model_fields)
    else:
        return value
    if any(item is not None and not isinstance(item, str) for item in values):
        raise ValueError("shared identity/reference fields require strings or null")
    return value


Identifier = Annotated[_Identifier, Field(strict=True)]
Sha256 = Annotated[_Sha256, Field(strict=True)]
CheckerIdentity = Annotated[_CheckerIdentity, BeforeValidator(_strict_shared_strings)]
EvidenceIdentity = Annotated[_EvidenceIdentity, BeforeValidator(_strict_shared_strings)]
EvidenceReference = Annotated[_EvidenceReference, BeforeValidator(_strict_shared_strings)]

U32 = Annotated[StrictInt, Field(ge=0, le=0xFFFFFFFF)]
Count = Annotated[StrictInt, Field(ge=0, le=10000)]
Cycles = Annotated[StrictInt, Field(ge=1, le=1000000)]
Order = Annotated[StrictInt, Field(ge=0, lt=2**64)]
Size = Annotated[StrictInt, Field(ge=0, le=1048576)]
Path = Annotated[str, Field(strict=True, min_length=1, max_length=512)]
Verdict = Literal["pass_within_scope", "design_mismatch", "inconclusive"]
RiscvLabel = Literal["control", "mutant", "control-replay"]
FirmwareLabel = Literal["control", "bad", "candidate", "bad-replay", "candidate-replay"]
Phase = Literal["assemble_start", "assemble_probe", "compile_main", "compiler", "binary", "disassembly", "elf_headers", "simulator"]

_RUNS = {
    40920450439: ("44b8b5e40ce7841f076e4178df13f38ecdbd05fd", 12919420477),
    40929665294: ("5565985bbe2bb2a0be869d366baa48c715057657", 12922737365),
}
_ATTEMPTS = {
    "control": ("attempt-ba01be2c331649828d66ffcca63365ba", "rv32i-control-v1"),
    "mutant": ("attempt-3507c622a1f143d894f819a0958c0f6a", "rv32i-functional-register-mutant-v1"),
    "control-replay": ("attempt-e845135395204d61b526d707bae1ef2f", "rv32i-control-v1"),
}
_FIXTURES = {
    "control": "fw-regmap-control-v1", "bad": "fw-regmap-offset-bug-v1",
    "bad-replay": "fw-regmap-offset-bug-v1", "candidate": "fw-regmap-offset-candidate-v1",
    "candidate-replay": "fw-regmap-offset-candidate-v1",
}


def _unique(values, label):
    if len(values) != len(set(values)):
        raise ValueError("duplicate " + label)


def _ref_key(ref):
    return ref.artifact_id, ref.revision_id, ref.attempt_id


class TransportDigest(WireModel):
    sha256: Sha256
    verification: Literal["github_reported", "locally_verified"]


class HostedOrigin(WireModel):
    kind: Literal["github_actions"] = "github_actions"
    repository: Literal["desanv01/VerifForge"] = "desanv01/VerifForge"
    source_commit: Annotated[str, Field(strict=True, pattern=r"^[0-9a-f]{40}$")]
    run_id: Annotated[StrictInt, Field(gt=0, lt=2**63)]
    run_attempt: Annotated[StrictInt, Field(ge=1, le=1)] = 1
    artifact_id: Annotated[StrictInt, Field(gt=0, lt=2**63)]
    extracted_receipt_ref: EvidenceReference
    transport_digest: TransportDigest | None = None

    @model_validator(mode="after")
    def curated(self):
        if _RUNS.get(self.run_id) != (self.source_commit, self.artifact_id):
            raise ValueError("unsupported hosted origin")
        if self.extracted_receipt_ref.attempt_id is not None:
            raise ValueError("receipt has no native attempt")
        return self


class ProducerAttemptLocator(WireModel):
    kind: Literal["producer_attempt"] = "producer_attempt"
    origin: HostedOrigin
    case_label: RiscvLabel
    native_attempt_id: Identifier
    wire_experiment_id: Identifier
    raw_result_ref: EvidenceReference

    @model_validator(mode="after")
    def original_identity(self):
        if self.origin.run_id != 40920450439 or _ATTEMPTS[self.case_label] != (self.native_attempt_id, self.wire_experiment_id):
            raise ValueError("unsupported RISC-V case identity")
        if self.raw_result_ref.attempt_id != self.native_attempt_id:
            raise ValueError("result native attempt differs")
        return self


class ProducerCaseLocator(WireModel):
    kind: Literal["producer_case"] = "producer_case"
    origin: HostedOrigin
    case_label: FirmwareLabel
    fixture_id: Identifier
    execution_ref: EvidenceReference

    @model_validator(mode="after")
    def original_identity(self):
        if self.origin.run_id != 40929665294 or _FIXTURES[self.case_label] != self.fixture_id:
            raise ValueError("unsupported firmware case identity")
        if self.execution_ref.attempt_id is not None:
            raise ValueError("firmware has no producer attempt ID")
        return self


NativeCaseLocator = Annotated[ProducerAttemptLocator | ProducerCaseLocator, Field(discriminator="kind")]
ArtifactScope = Annotated[HostedOrigin | ProducerAttemptLocator | ProducerCaseLocator, Field(discriminator="kind")]
Role = Literal["receipt", "manifest", "native_result", "native_execution", "native_check", "native_trace", "reference_trace", "program", "source_snapshot", "configuration", "replay_record", "candidate_diff"]


class RecordedArtifactBinding(WireModel):
    ref: EvidenceReference
    scope: ArtifactScope
    provenance: Literal["producer", "adapter_generated"] = "producer"
    original_relative_path: Path | None = None
    original_artifact_id: Identifier | None = None
    role: Role
    media_type: Literal["application/json", "application/octet-stream", "text/plain", "text/x-diff", "text/x-c", "text/x-asm", "application/xml"]
    size_bytes: Size

    @field_validator("original_relative_path")
    @classmethod
    def safe_path(cls, value):
        return _safe_path(value) if value is not None else None

    @model_validator(mode="after")
    def raw_scope(self):
        if self.provenance == "adapter_generated":
            if not isinstance(self.scope, HostedOrigin) or self.original_relative_path is not None or self.original_artifact_id is not None or self.ref.attempt_id is not None:
                raise ValueError("adapter metadata must not invent producer provenance")
        else:
            if self.original_relative_path is None:
                raise ValueError("producer artifact requires original path")
            native_id = self.scope.native_attempt_id if isinstance(self.scope, ProducerAttemptLocator) else None
            if self.ref.attempt_id != native_id:
                raise ValueError("artifact native attempt differs from scope")
            if not isinstance(self.scope, HostedOrigin):
                label = self.scope.case_label
                if not (self.original_relative_path.startswith(label + "/") or self.original_relative_path == label + ".check.json"):
                    raise ValueError("artifact path crosses producer case")
        return self


class InventoryEntry(WireModel):
    original_relative_path: Path
    sha256: Sha256
    size_bytes: Annotated[StrictInt, Field(ge=0, lt=2**63)]
    _path = field_validator("original_relative_path")(_safe_path)


class ArtifactSelection(WireModel):
    origin: HostedOrigin
    inventory: Annotated[tuple[InventoryEntry, ...], Field(min_length=1, max_length=4096)]
    selected: Annotated[tuple[RecordedArtifactBinding, ...], Field(min_length=1, max_length=256)]
    omitted_paths: Annotated[tuple[Path, ...], Field(max_length=4096)] = ()

    @model_validator(mode="after")
    def closure(self):
        inventory = {x.original_relative_path: x for x in self.inventory}
        _unique([x.original_relative_path for x in self.inventory], "inventory path")
        _unique([_ref_key(x.ref) for x in self.selected], "artifact reference")
        paths = [x.original_relative_path for x in self.selected if x.provenance == "producer"]
        _unique(paths, "selected path")
        _unique(self.omitted_paths, "omitted path")
        for path in self.omitted_paths:
            _safe_path(path)
        if set(paths) & set(self.omitted_paths) or set(paths) | set(self.omitted_paths) != set(inventory):
            raise ValueError("selected and omitted paths must partition original inventory")
        if sum(x.size_bytes for x in self.selected) > 16 * 1048576:
            raise ValueError("selected byte limit exceeded")
        for binding in self.selected:
            origin = binding.scope if isinstance(binding.scope, HostedOrigin) else binding.scope.origin
            if origin != self.origin:
                raise ValueError("artifact origin differs")
            if binding.provenance == "producer":
                entry = inventory[binding.original_relative_path]
                if (entry.sha256, entry.size_bytes) != (binding.ref.sha256, binding.size_bytes):
                    raise ValueError("selected artifact differs from inventory")
        return self


class RiscvInputs(WireModel):
    plan: EvidenceReference
    profile: EvidenceReference
    source_manifest: EvidenceReference
    checker_manifest: EvidenceReference
    environment: EvidenceReference
    raw_rvfi: EvidenceReference
    raw_spike: EvidenceReference
    normalized_pair: EvidenceReference
    comparison: EvidenceReference
    object: EvidenceReference
    elf: EvidenceReference
    binary: EvidenceReference


class MemoryEffect(WireModel):
    kind: Literal["read", "write"]
    address: U32
    width: Annotated[StrictInt, Field(ge=1, le=4)]
    byte_mask: Annotated[StrictInt, Field(ge=1, le=15)]
    value: U32

    @model_validator(mode="after")
    def width_value(self):
        if self.width not in (1, 2, 4) or self.value >= 2 ** (8 * self.width):
            raise ValueError("invalid memory width/value")
        return self


class _Divergence(WireModel):
    order: Order
    cycle: Cycles
    requirement: Literal["RISCV_ARCH_RETIREMENT"] = "RISCV_ARCH_RETIREMENT"

    @model_validator(mode="after")
    def unequal(self):
        if self.expected == self.observed:
            raise ValueError("divergence values must differ")
        return self


class IntegerDivergence(_Divergence):
    field: Literal["pc", "instruction", "rd", "rd_value"]
    expected: U32
    observed: U32

    @model_validator(mode="after")
    def register_index(self):
        if self.field == "rd" and max(self.expected, self.observed) > 31:
            raise ValueError("register index exceeds x31")
        return self


class BooleanDivergence(_Divergence):
    field: Literal["trap", "interrupt"]
    expected: StrictBool
    observed: StrictBool


class MemoryDivergence(_Divergence):
    field: Literal["memory"]
    expected: MemoryEffect | None
    observed: MemoryEffect | None


RetirementDivergence = Annotated[IntegerDivergence | BooleanDivergence | MemoryDivergence, Field(discriminator="field")]


class RiscvRecordedAttempt(WireModel):
    schema_version: Literal["riscv.recorded-attempt.v1"] = "riscv.recorded-attempt.v1"
    locator: ProducerAttemptLocator
    inputs: RiscvInputs
    identity: EvidenceIdentity
    checker: CheckerIdentity
    configuration_hash: Sha256
    producer_execution_status: Literal["completed", "bounded_incomplete", "failed"]
    termination: Literal["completion_store_retired", "trap", "cycle_limit", "retirement_limit"]
    checker_evidence_status: Literal["valid", "invalid", "unsupported"]
    verdict: Verdict
    cycles: Cycles
    cycle_limit: Cycles
    retirement_limit: Annotated[StrictInt, Field(ge=1, le=10000)]
    retired_count: Count
    reference_count: Count
    compared_count: Count
    first_divergence: RetirementDivergence | None = None
    producer_phases: Annotated[tuple[Literal["assembler", "compiler", "objcopy", "dut", "reference"], ...], Field(min_length=1, max_length=5)]

    @model_validator(mode="after")
    def outcomes(self):
        _unique(self.producer_phases, "RISC-V phase")
        if self.checker.sha256 != self.identity.checker_hash or self.inputs.checker_manifest.sha256 != self.identity.checker_hash or self.inputs.environment.sha256 != self.identity.environment_hash:
            raise ValueError("raw checker/environment identity differs")
        if self.cycles > self.cycle_limit or self.retired_count > self.retirement_limit or self.compared_count > min(self.retired_count, self.reference_count):
            raise ValueError("observed counts exceed bounds")
        if (self.verdict == "design_mismatch") != (self.first_divergence is not None):
            raise ValueError("mismatch requires exactly one first divergence")
        if self.first_divergence and (self.first_divergence.order + 1 != self.compared_count or self.first_divergence.cycle > self.cycles):
            raise ValueError("first divergence lies outside compared prefix")
        if self.checker_evidence_status != "valid" and self.verdict != "inconclusive":
            raise ValueError("unvalidated checker evidence is inconclusive")
        if self.verdict == "pass_within_scope" and (not self.compared_count or self.producer_execution_status != "completed" or self.termination != "completion_store_retired" or self.compared_count != self.retired_count or self.compared_count != self.reference_count):
            raise ValueError("pass requires completed full comparison")
        return self


class FirmwareProgram(WireModel):
    start_object: EvidenceReference
    probe_object: EvidenceReference
    main_object: EvidenceReference
    elf: EvidenceReference
    binary: EvidenceReference
    map: EvidenceReference
    disassembly: EvidenceReference
    elf_attributes: EvidenceReference


class FirmwareSources(WireModel):
    main: EvidenceReference
    start: EvidenceReference
    probe: EvidenceReference
    linker_script: EvidenceReference
    wrapper: EvidenceReference
    core_wrapper: EvidenceReference
    core: EvidenceReference
    peripheral: EvidenceReference


class FirmwareInputs(WireModel):
    preparation: EvidenceReference
    observation: EvidenceReference
    check: EvidenceReference
    register_contract: EvidenceReference
    selected_header: EvidenceReference
    bad_base_header: EvidenceReference
    program: FirmwareProgram
    sources: FirmwareSources
    source_manifest: EvidenceReference
    tool_metadata: EvidenceReference


class MmioConflict(WireModel):
    mmio_index: Annotated[StrictInt, Field(ge=0, le=8)]
    bus_sequence: Annotated[StrictInt, Field(ge=0, le=65535)]
    cycle: Annotated[StrictInt, Field(ge=5, lt=1000000)]
    requirement: Literal["FIRMWARE_STATUS_ADDRESS"] = "FIRMWARE_STATUS_ADDRESS"
    operation: Literal["read", "write"]
    expected_address: U32
    observed_address: U32
    write_strobes: Annotated[StrictInt, Field(ge=0, le=15)]
    active_lane_value: U32
    expected_probe: U32
    observed_probe: U32

    @model_validator(mode="after")
    def peripheral_bounds(self):
        if any(x not in range(0x40000000, 0x40000010, 4) for x in (self.expected_address, self.observed_address)):
            raise ValueError("MMIO address outside aligned curated peripheral")
        mask = sum(0xFF << (8 * i) for i in range(4) if self.write_strobes & (1 << i))
        if self.operation == "write" and (not self.write_strobes or self.active_lane_value & ~mask):
            raise ValueError("invalid active write lanes")
        if self.operation == "read" and self.write_strobes:
            raise ValueError("read has write strobes")
        if self.expected_address == self.observed_address and self.expected_probe == self.observed_probe:
            raise ValueError("MMIO conflict must differ")
        return self


class MailboxObservation(WireModel):
    observed: StrictBool
    address: Annotated[StrictInt, Field(ge=0x8000FF00, le=0x8000FF00)] = 0x8000FF00
    value: U32 | None = None
    cycle: Cycles | None = None

    @model_validator(mode="after")
    def presence(self):
        if self.observed != (self.value is not None and self.cycle is not None) or (not self.observed and (self.value is not None or self.cycle is not None)):
            raise ValueError("mailbox presence differs from value/cycle")
        return self


class FirmwareRecordedCase(WireModel):
    schema_version: Literal["firmware.recorded-case.v1"] = "firmware.recorded-case.v1"
    locator: ProducerCaseLocator
    inputs: FirmwareInputs
    identity: EvidenceIdentity
    checker: CheckerIdentity
    configuration_hash: Sha256
    profile_hash: Sha256
    semantic_hash: Sha256
    producer_completed: StrictBool
    termination: Literal["completion_store_retired", "trap", "cycle_limit", "retirement_limit", "protocol_error", "transfer_limit"]
    checker_evidence_status: Literal["completed", "bounded_incomplete", "invalid_evidence"]
    verdict: Verdict
    cycles: Cycles
    cycle_limit: Cycles
    retired_count: Count
    retirement_limit: Annotated[StrictInt, Field(ge=1, le=10000)]
    transfer_count: Annotated[StrictInt, Field(ge=0, le=65536)]
    mmio_count: Annotated[StrictInt, Field(ge=0, le=9)]
    first_conflict: MmioConflict | None = None
    mailbox: MailboxObservation
    producer_phases: Annotated[tuple[Phase, ...], Field(min_length=1, max_length=8)]
    replay_of_execution_ref: EvidenceReference | None = None
    reference_support: Literal["independent_literal_mmio_checker_spike_unsupported"] = "independent_literal_mmio_checker_spike_unsupported"

    @model_validator(mode="after")
    def outcomes(self):
        _unique(self.producer_phases, "firmware phase")
        # The native checker hash is a canonical source-manifest subset hash,
        # not the raw SHA of preparation/check JSON or any single Python file.
        if self.checker.sha256 != self.identity.checker_hash:
            raise ValueError("checker identity differs")
        if self.cycles > self.cycle_limit or self.retired_count > self.retirement_limit or self.mmio_count > self.transfer_count:
            raise ValueError("observed counts exceed bounds")
        if self.first_conflict and (self.verdict != "design_mismatch" or self.first_conflict.mmio_index >= self.mmio_count or self.first_conflict.bus_sequence >= self.transfer_count or self.first_conflict.cycle > self.cycles):
            raise ValueError("first conflict outside mismatch trace")
        if self.mailbox.cycle is not None and self.mailbox.cycle > self.cycles:
            raise ValueError("mailbox outside trace")
        if self.checker_evidence_status != "completed" and self.verdict != "inconclusive":
            raise ValueError("incomplete/invalid evidence is inconclusive")
        if self.verdict == "pass_within_scope" and (not self.producer_completed or self.termination != "completion_store_retired" or self.mmio_count != 9 or self.mailbox.value != 1 or self.first_conflict is not None):
            raise ValueError("pass requires complete MMIO scope and success mailbox")
        replay = self.locator.case_label.endswith("-replay")
        if replay != (self.replay_of_execution_ref is not None) or (replay and self.producer_phases != ("simulator",)):
            raise ValueError("saved-program replay must bind original and simulator-only phases")
        if not replay and set(self.producer_phases) != {"assemble_start", "assemble_probe", "compile_main", "compiler", "binary", "disassembly", "elf_headers", "simulator"}:
            raise ValueError("original firmware phases incomplete")
        return self


class RecompiledControlComparison(WireModel):
    kind: Literal["recompiled_control"] = "recompiled_control"
    original: ProducerAttemptLocator
    repeated: ProducerAttemptLocator
    record_ref: EvidenceReference
    outcome: Literal["byte_identical_recompiled_control", "inconclusive"]


class CandidateValidation(WireModel):
    kind: Literal["curated_header_correction"] = "curated_header_correction"
    bad: ProducerCaseLocator
    candidate: ProducerCaseLocator
    diff_ref: EvidenceReference
    original_bad_header_ref: EvidenceReference
    candidate_header_ref: EvidenceReference
    protected_checker_hash: Sha256
    protected_contract_hash: Sha256
    protected_profile_hash: Sha256
    covered_mmio_indices: Annotated[tuple[Annotated[StrictInt, Field(ge=0, le=8)], ...], Field(min_length=9, max_length=9)]
    outcome: Literal["scoped_curated_correction", "inconclusive"]


class SavedProgramReplay(WireModel):
    kind: Literal["saved_program_replay"] = "saved_program_replay"
    original: ProducerCaseLocator
    repeated: ProducerCaseLocator
    record_ref: EvidenceReference
    outcome: Literal["semantic_saved_program_replay", "inconclusive"]


def _input_refs(model):
    for name in type(model).model_fields:
        value = getattr(model, name)
        if isinstance(value, _EvidenceReference):
            yield value
        elif isinstance(value, (FirmwareProgram, FirmwareSources)):
            yield from _input_refs(value)


def _report_closure(report):
    if len(report.model_dump_json().encode("utf-8")) > 1048576:
        raise ValueError("recorded report exceeds blob byte limit")
    selected = {_ref_key(x.ref): x for x in report.selection.selected}

    def require(ref, scope, role=None):
        binding = selected.get(_ref_key(ref))
        if binding is None or binding.ref != ref or binding.scope != scope or (role and binding.role != role):
            raise ValueError("missing, swapped or wrongly scoped artifact reference")

    require(report.manifest_ref, report.selection.origin, "manifest")
    require(report.selection.origin.extracted_receipt_ref, report.selection.origin, "receipt")
    labels = [case.locator.case_label for case in report.cases]
    _unique(labels, "case label")
    for binding in report.selection.selected:
        if not isinstance(binding.scope, HostedOrigin) and binding.scope not in tuple(x.locator for x in report.cases):
            raise ValueError("selected artifact scope is not an included producer case")
    for case in report.cases:
        if case.locator.origin != report.selection.origin:
            raise ValueError("case origin differs")
        if isinstance(case, RiscvRecordedAttempt):
            require(case.locator.raw_result_ref, case.locator, "native_result")
        else:
            require(case.locator.execution_ref, case.locator, "native_execution")
        roles = {
            "plan": "configuration", "profile": "configuration", "source_manifest": "source_snapshot",
            "checker_manifest": "configuration", "environment": "configuration", "raw_rvfi": "native_trace",
            "raw_spike": "reference_trace", "normalized_pair": "native_trace", "comparison": "native_check",
            "object": "program", "elf": "program", "binary": "program", "preparation": "configuration",
            "observation": "native_trace", "check": "native_check", "register_contract": "configuration",
            "selected_header": "source_snapshot", "bad_base_header": "source_snapshot", "tool_metadata": "configuration",
        }
        for name in type(case.inputs).model_fields:
            value = getattr(case.inputs, name)
            if isinstance(value, (FirmwareProgram, FirmwareSources)):
                for ref in _input_refs(value):
                    require(ref, case.locator, "program" if isinstance(value, FirmwareProgram) else "source_snapshot")
            else:
                scope = report.selection.origin if isinstance(case, FirmwareRecordedCase) and name in ("source_manifest", "tool_metadata") else case.locator
                require(value, scope, roles[name])
    return require


class RiscvRecordedReport(WireModel):
    schema_version: Literal["riscv.recorded-report.v1"] = "riscv.recorded-report.v1"
    family: Literal["riscv_retirement"] = "riscv_retirement"
    operation: Literal["admission_only"] = "admission_only"
    new_execution: Literal["not_run"] = "not_run"
    manifest_ref: EvidenceReference
    selection: ArtifactSelection
    cases: Annotated[tuple[RiscvRecordedAttempt, ...], Field(min_length=3, max_length=3)]
    recompiled_control: RecompiledControlComparison

    @model_validator(mode="after")
    def comparison_closure(self):
        require = _report_closure(self)
        by_label = {x.locator.case_label: x for x in self.cases}
        if set(by_label) != set(_ATTEMPTS):
            raise ValueError("RISC-V case set incomplete")
        a, b = by_label["control"], by_label["control-replay"]
        comparison = self.recompiled_control
        if (comparison.original, comparison.repeated) != (a.locator, b.locator):
            raise ValueError("recompiled control references wrong cases")
        require(comparison.record_ref, self.selection.origin, "replay_record")
        if comparison.outcome == "byte_identical_recompiled_control":
            fields = ("object", "elf", "binary", "raw_rvfi", "raw_spike", "normalized_pair", "comparison", "environment")
            if a.identity != b.identity or a.configuration_hash != b.configuration_hash or any(getattr(a.inputs, name).sha256 != getattr(b.inputs, name).sha256 for name in fields) or any(not {"assembler", "compiler"}.issubset(x.producer_phases) or x.verdict != "pass_within_scope" for x in (a, b)):
                raise ValueError("recompiled control equality not established")
        return self


class FirmwareRecordedReport(WireModel):
    schema_version: Literal["firmware.recorded-report.v1"] = "firmware.recorded-report.v1"
    family: Literal["firmware_mmio"] = "firmware_mmio"
    operation: Literal["admission_only"] = "admission_only"
    new_execution: Literal["not_run"] = "not_run"
    manifest_ref: EvidenceReference
    selection: ArtifactSelection
    cases: Annotated[tuple[FirmwareRecordedCase, ...], Field(min_length=5, max_length=5)]
    candidate: CandidateValidation
    replays: Annotated[tuple[SavedProgramReplay, ...], Field(min_length=2, max_length=2)]

    @model_validator(mode="after")
    def comparison_closure(self):
        require = _report_closure(self)
        by_label = {x.locator.case_label: x for x in self.cases}
        if set(by_label) != set(_FIXTURES):
            raise ValueError("firmware case set incomplete")
        bad, fixed = by_label["bad"], by_label["candidate"]
        candidate = self.candidate
        if (candidate.bad, candidate.candidate) != (bad.locator, fixed.locator):
            raise ValueError("candidate references wrong cases")
        require(candidate.diff_ref, fixed.locator, "candidate_diff")
        if candidate.original_bad_header_ref != bad.inputs.selected_header or candidate.original_bad_header_ref.sha256 != fixed.inputs.bad_base_header.sha256 or candidate.candidate_header_ref != fixed.inputs.selected_header:
            raise ValueError("candidate header/base binding differs")
        if set(candidate.covered_mmio_indices) != set(range(9)):
            raise ValueError("candidate MMIO coverage incomplete")
        if any(x.identity.checker_hash != candidate.protected_checker_hash or x.identity.contract_hash != candidate.protected_contract_hash or x.profile_hash != candidate.protected_profile_hash for x in (bad, fixed)):
            raise ValueError("candidate protected checker/requirements/profile changed")
        if candidate.outcome == "scoped_curated_correction" and (bad.verdict != "design_mismatch" or fixed.verdict != "pass_within_scope" or candidate.original_bad_header_ref.sha256 == candidate.candidate_header_ref.sha256):
            raise ValueError("curated correction outcome unsupported")
        if candidate.outcome == "scoped_curated_correction" and any(x.sha256 != y.sha256 for x, y in zip(_input_refs(bad.inputs.sources), _input_refs(fixed.inputs.sources), strict=True)):
            raise ValueError("curated header correction changed protected source files")
        _unique([x.original.case_label for x in self.replays], "replay original")
        for replay in self.replays:
            if replay.original.case_label not in ("bad", "candidate"):
                raise ValueError("unsupported replay original")
            a, b = by_label[replay.original.case_label], by_label[replay.original.case_label + "-replay"]
            if (replay.original, replay.repeated) != (a.locator, b.locator) or b.replay_of_execution_ref != a.locator.execution_ref:
                raise ValueError("replay original execution binding differs")
            require(replay.record_ref, self.selection.origin, "replay_record")
            if replay.outcome == "semantic_saved_program_replay":
                protected = ("preparation", "register_contract", "selected_header", "bad_base_header", "source_manifest", "tool_metadata")
                retained = tuple(_input_refs(a.inputs.program)) + tuple(_input_refs(a.inputs.sources))
                repeated = tuple(_input_refs(b.inputs.program)) + tuple(_input_refs(b.inputs.sources))
                if any(getattr(a.inputs, name).sha256 != getattr(b.inputs, name).sha256 for name in protected) or any(x.sha256 != y.sha256 for x, y in zip(retained, repeated, strict=True)) or a.identity != b.identity or a.configuration_hash != b.configuration_hash or a.profile_hash != b.profile_hash or (a.semantic_hash, a.verdict, a.first_conflict, a.mailbox, a.cycles, a.retired_count, a.transfer_count, a.mmio_count, a.termination, a.producer_completed) != (b.semantic_hash, b.verdict, b.first_conflict, b.mailbox, b.cycles, b.retired_count, b.transfer_count, b.mmio_count, b.termination, b.producer_completed):
                    raise ValueError("saved program/semantic equality not established")
        return self


RecordedToolReport = Annotated[RiscvRecordedReport | FirmwareRecordedReport, Field(discriminator="schema_version")]
