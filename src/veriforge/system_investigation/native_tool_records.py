"""Pure native-v2 byte admission. No filesystem, DB, network, jobs or copied code.

The host pins the installed validator's actual source hash outside this pure
boundary. This is host-asserted provenance, never self-authentication or remote
attestation. Reads must obtain fresh host authority, not trust stored assertions.
"""
from dataclasses import dataclass, field
from dataclasses import asdict
import hashlib
import json
import math
import re
import struct
from typing import Annotated, Literal

from pydantic import Field, StrictInt, TypeAdapter, model_validator

from veriforge.contracts import AttemptResult, CheckerIdentity, WireModel, canonical_hash
from veriforge.contracts import _safe_path
from veriforge.contracts.engineering import SystemManifest
from veriforge.contracts.native_tools import (
    EvidenceReference, FirmwareRecordedCase, FirmwareRecordedReport, HostedOrigin,
    Identifier, NativeCaseLocator, ProducerAttemptLocator, RecordedToolReport,
    RiscvRecordedAttempt, RiscvRecordedReport, Sha256,
)
from . import records as v1

MAX_ARTIFACT_BYTES = v1.MAX_ARTIFACT_BYTES
MAX_TOTAL_BYTES, MAX_ARTIFACTS, MAX_ENVELOPE_BYTES = v1.MAX_TOTAL_BYTES, v1.MAX_ARTIFACTS, v1.MAX_ENVELOPE_BYTES
VALIDATOR_ID, VALIDATOR_VERSION = "veriforge.native-tool-admission", "1"
RISCV_CHECKER_HASH = "c52097ccc32725797a6dbec73dea022e5dbba0fd7238b847a25de9ccbf522d50"
FIRMWARE_CHECKER_HASH = "e492402996430dd2a88478a040b98c7c976c4c42a8049b417055d57cfe6468c4"
_REGISTERED_SOURCES = {
    "src/veriforge/riscv/profile.py": "f2944d49eaf356bdc3078dd35cc9dc201f025159bcac3274a8bc6f12f5294680",
    "src/veriforge/riscv/memory.py": "5aabc7af95c3f6be622059f8c475bbd0f463466f729267ca699c93f8ed53d182",
    "src/veriforge/riscv/sim_test.py": "be2075afff67c507cd2a289f5ff7e9423b423a074d83e72923d6cfd651d9dd26",
    "src/veriforge/riscv/sim_job.py": "c0804267d2e2f56976e92c488a9395ea98d9ca809b01379c4fe99f8842dc69a5",
    "targets/riscv/upstream/picorv32.v": "0836050971b3c6cdd28ac3b1e5719a67fb645161912bef1e472e63995ceb0622",
    "targets/riscv/picorv32_top.sv": "7cf8a728f22ed45aec2cca2c09f6927e1697736e35b0e36b833b97b894f42ed3",
    "targets/riscv/smoke.S": "1ff4673cbaa387d94c60a2e6bcb403882a50e0321628fa6ebb17265e45f0642d",
    "targets/riscv/link.ld": "3d5347079c47f1e914cacb58c95451dc6ea27f4f7de3b55fd230a7c6d27b601c",
    "targets/firmware/executable/main.c": "798ee2735f1e9099a3f854be9b38d97a1706b606f1a39cb7c541568d8c300a00",
    "targets/firmware/executable/start.S": "b80dfbf24304a6f4a34e13d73cb34f13c7d89ee55b7220a909d846c0bbb374ea",
    "targets/firmware/executable/independent_probe.S": "7a906f59907135fbc129c47e83137779e85b647de5566596bed2063ad8ade5a6",
    "targets/firmware/executable/link.ld": "cbfe225815b286b615f7439db2899665eb0b10f6a3ef5364dad37a075f87ae36",
    "targets/firmware/executable/firmware_top.sv": "a0b1177f02f505eb23d08fdb2700b76682605ad3532f6143ac3c4cee7b127326",
    "targets/firmware/executable/native_registers.sv": "06d9c04c8beff2863e98a09428edbaa7a763b875ea946011a4f0972996b198eb",
    "targets/firmware/executable/register-contract.json": "ef88127351533f39e46fb924a58443b486ba03f07f6f605fe83bc7e1fbce5759",
    "targets/firmware/executable/control/registers.h": "552749250f2edae188589e9c62d7ef6bcc203bbb310ac68756b327a38573a755",
    "targets/firmware/executable/candidate/registers.h": "552749250f2edae188589e9c62d7ef6bcc203bbb310ac68756b327a38573a755",
    "targets/firmware/executable/offset_bug/registers.h": "89891862308d9069b94647c8bdc2a6605afbf6973c4e3383b727bad5de6788b8",
}
ReportKind = Literal["riscv.recorded-report.v1", "firmware.recorded-report.v1"]
Role = Literal["project_revision", "project_source", "native_report", "native_case", "provenance_source",
    "receipt", "manifest", "native_result", "native_execution", "native_check", "native_trace",
    "reference_trace", "program", "source_snapshot", "configuration", "replay_record", "candidate_diff"]
NATIVE_SCHEMA_ALLOWLIST = frozenset({("riscv", "riscv.recorded-attempt.v1"), ("firmware", "firmware.recorded-case.v1")})
_REPORT = TypeAdapter(RecordedToolReport)
_LOCATOR = TypeAdapter(NativeCaseLocator)
_AUTHORITY_SEAL, _ADMISSION_SEAL = object(), object()


class NativeToolAttachment(WireModel):
    fact_id: Identifier
    domain: Literal["riscv", "firmware"]
    native_schema_id: Literal["riscv.recorded-attempt.v1", "firmware.recorded-case.v1"]
    reference: EvidenceReference


class EngineeringInvestigationV2(WireModel):
    schema_version: Literal["engineering.investigation.v2"] = "engineering.investigation.v2"
    investigation_id: Identifier
    project_id: Identifier
    revision_id: Identifier
    project_revision_ref: EvidenceReference
    manifest_ref: EvidenceReference
    report_kind: ReportKind
    report_ref: EvidenceReference
    native_records: Annotated[tuple[NativeToolAttachment, ...], Field(min_length=3, max_length=5)]

    @model_validator(mode="after")
    def fixed_family(self):
        expected = ("riscv", "riscv.recorded-attempt.v1", 3) if self.report_kind == "riscv.recorded-report.v1" else ("firmware", "firmware.recorded-case.v1", 5)
        if len(self.native_records) != expected[2] or len({x.fact_id for x in self.native_records}) != len(self.native_records):
            raise ValueError("complete unique native case attachments required")
        if any((x.domain, x.native_schema_id) != expected[:2] for x in self.native_records):
            raise ValueError("native case schema differs from report family")
        return self


class VerifiedArtifactV2(WireModel):
    reference: EvidenceReference
    size_bytes: Annotated[StrictInt, Field(ge=0, le=MAX_ARTIFACT_BYTES)]
    roles: Annotated[tuple[Role, ...], Field(min_length=1, max_length=12)]
    canonical_record_hash: Sha256 | None = None


class NativeValidation(WireModel):
    validator_id: Literal["veriforge.native-tool-admission"] = VALIDATOR_ID
    version: Literal["1"] = VALIDATOR_VERSION
    implementation_hash: Sha256
    implementation_hash_provenance: Literal["host_pinned_installed_source_hash"] = "host_pinned_installed_source_hash"
    scope: Literal["exact_bytes_and_registered_pure_checkers_not_remote_attestation"] = "exact_bytes_and_registered_pure_checkers_not_remote_attestation"


class AdmissionEnvelopeV2(WireModel):
    schema_version: Literal["engineering.admission.v2"] = "engineering.admission.v2"
    operation: Literal["admission_only"] = "admission_only"
    new_execution: Literal["not_run"] = "not_run"
    claim_scope: Literal["resolved_bytes_and_registered_native_checks_only"] = "resolved_bytes_and_registered_native_checks_only"
    external_context_scope: Literal["host_pinned_external_locator_not_local_execution_origin"] = "host_pinned_external_locator_not_local_execution_origin"
    validation: NativeValidation
    record: EngineeringInvestigationV2
    record_hash: Sha256
    artifacts: Annotated[tuple[VerifiedArtifactV2, ...], Field(max_length=MAX_ARTIFACTS)]
    total_artifact_bytes: Annotated[StrictInt, Field(ge=0, le=MAX_TOTAL_BYTES)]


class _Pins(WireModel):
    project_id: Identifier
    revision_id: Identifier
    project_revision_ref: EvidenceReference
    manifest_ref: EvidenceReference
    report_kind: ReportKind
    report_ref: EvidenceReference
    locators: Annotated[tuple[NativeCaseLocator, ...], Field(min_length=3, max_length=5)]
    validator_implementation_hash: Sha256


@dataclass(frozen=True, init=False)
class TrustedExternalAdmissionAuthority:
    pins: _Pins
    _seal: object = field(repr=False)

    def __new__(cls, *args, **kwargs):
        raise TypeError("external authority requires trusted host factory")


def create_external_admission_authority(*, project_id, revision_id, project_revision_ref,
        manifest_ref, report_kind, report_ref, locators, validator_implementation_hash):
    """Host only: freshly hash the installed validator outside this pure module.

    Do not use a stored envelope's hash or public payload as fresh host authority.
    A version/hash mismatch must fail closed in the host's installed-code policy.
    No local campaign/attempt lookup, association or authority is represented.
    """
    if type(locators) is not tuple:
        raise ValueError("bounded typed external locator tuple required")
    pins = _Pins(project_id=project_id, revision_id=revision_id, project_revision_ref=project_revision_ref,
        manifest_ref=manifest_ref, report_kind=report_kind, report_ref=report_ref,
        locators=locators, validator_implementation_hash=validator_implementation_hash)
    pins = v1._decode(v1._encode(pins), _Pins, MAX_ENVELOPE_BYTES)
    expected = 3 if report_kind == "riscv.recorded-report.v1" else 5
    if len(pins.locators) != expected or len({x.case_label for x in pins.locators}) != expected:
        raise ValueError("complete external locator authority required")
    if any(isinstance(x, ProducerAttemptLocator) != (expected == 3) for x in pins.locators):
        raise ValueError("external locator family differs")
    value = object.__new__(TrustedExternalAdmissionAuthority)
    object.__setattr__(value, "pins", pins)
    object.__setattr__(value, "_seal", _AUTHORITY_SEAL)
    return value


@dataclass(frozen=True, init=False)
class AdmittedNativeInvestigation:
    envelope: AdmissionEnvelopeV2
    project_revision: v1.EngineeringProjectRevision
    manifest: SystemManifest
    native_report: RiscvRecordedReport | FirmwareRecordedReport = field(repr=False)
    native_records: tuple[RiscvRecordedAttempt | FirmwareRecordedCase, ...] = field(repr=False)
    artifacts: tuple[v1.ArtifactBytes, ...] = field(repr=False)
    _seal: object = field(repr=False)

    def __new__(cls, *args, **kwargs):
        raise TypeError("native admission requires complete byte validation")


def _require(condition, code):
    if not condition:
        raise ValueError(code)


def _json(data):
    _require(type(data) is bytes and len(data) <= MAX_ARTIFACT_BYTES, "bounded exact native JSON bytes required")
    def unique(pairs):
        result = {}
        for key, value in pairs:
            _require(key not in result, "duplicate native JSON key")
            result[key] = value
        return result
    def invalid(_):
        raise ValueError("nonfinite native JSON constant")
    def finite(number):
        parsed = float(number)
        _require(math.isfinite(parsed), "nonfinite native JSON number")
        return parsed
    try:
        result = json.loads(data, object_pairs_hook=unique, parse_constant=invalid, parse_float=finite)
        _require(type(result) is dict, "native JSON object required")
        return result
    except (UnicodeError, RecursionError, json.JSONDecodeError):
        raise ValueError("malformed bounded native JSON") from None


def _canonical(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False).encode()


def _hash(data):
    return hashlib.sha256(data).hexdigest()


def _semantic(value):
    return _hash(_canonical(value))


def _keys(value, required, optional=()):
    _require(type(value) is dict and set(required) <= set(value) <= set(required) | set(optional), "unsupported native object shape")


def _validate_inventory(report, path):
    # Receipts bind original inventory hashes, not the transport ZIP's authenticity.
    raw = path(next(x.original_relative_path for x in report.selection.selected
        if x.ref == report.selection.origin.extracted_receipt_ref))
    receipt = _json(raw)
    inventory = {x.original_relative_path: x for x in report.selection.inventory}
    receipt_path = next(x.original_relative_path for x in report.selection.selected
        if x.ref == report.selection.origin.extracted_receipt_ref)
    _require(set(receipt) | {receipt_path} == set(inventory), "original inventory differs from retained receipt")
    for name, digest in receipt.items():
        _safe_path(name)
        _require(type(digest) is str and re.fullmatch(r"[0-9a-f]{64}", digest) is not None and inventory[name].sha256 == digest,
            "original inventory digest differs")


def _source_closure(path):
    source = _json(path("source-manifest.json"))
    _require(1 <= len(source) <= 256, "bounded registered source manifest required")
    for name, digest in source.items():
        _safe_path(name)
        _require(type(digest) is str and re.fullmatch(r"[0-9a-f]{64}", digest) is not None, "invalid source manifest hash")
        _require(name not in _REGISTERED_SOURCES or digest == _REGISTERED_SOURCES[name], "unsupported native source/configuration version")
        retained = "source/workflow/" + name.rsplit("/", 1)[1] if name in (
            ".github/workflows/riscv-acceptance.yml", ".github/workflows/firmware-acceptance.yml") else "source/" + name
        path(retained, digest)
    return source


def _named_inputs(locator, inputs, resolve, path, names):
    values = {}
    for field_name, filename in names.items():
        ref = getattr(inputs, field_name)
        role = ("program" if field_name in (*_FW_PROGRAM, "object") else "source_snapshot" if field_name in (*_FW_SOURCES, "source_manifest", "selected_header", "bad_base_header")
            else "configuration" if field_name in ("plan", "profile", "environment", "checker_manifest", "preparation", "register_contract")
            else "native_check" if field_name == "comparison" else "reference_trace" if field_name == "raw_spike" else "native_trace")
        values[field_name] = resolve(ref, role)
        _require(path(locator.case_label + "/" + filename, ref.sha256) == values[field_name], "named native input path differs")
    return values


def _elf_program(elf_data, binary, *, firmware=False, disassembly=None, attributes=None):
    # Installed pure ELF/layout parsers, plus bounded executable-section coverage.
    from veriforge.firmware.extract import extract_elf, extract_disassembly, corroborate_instruction_bytes
    from veriforge.firmware.contracts import PlatformProfile
    from veriforge.firmware.workflow import check_elf_layout
    elf = extract_elf(elf_data, "firmware.elf" if firmware else "smoke.elf")
    _require(check_elf_layout(elf, PlatformProfile()).verdict == "layout_agrees", "native ELF outside registered layout")
    _require(0 < len(binary) <= 0xE000, "native binary outside registered image bounds")
    expected = bytearray(len(binary))
    for segment in elf.load_segments:
        _require(segment.virtual_address == segment.physical_address and segment.physical_address + segment.memory_size <= 0x8000E000,
            "native ELF aliases or reserved segments unsupported")
        offset = segment.physical_address - 0x80000000
        if segment.file_size:
            _require(0 <= offset and offset + segment.file_size <= len(binary), "native binary omits ELF load bytes")
            expected[offset:offset+segment.file_size] = elf_data[segment.offset:segment.offset+segment.file_size]
    _require(bytes(expected) == binary, "native binary differs from retained ELF")
    _require(len(elf_data) >= 52, "native ELF header truncated")
    header = struct.unpack_from("<16sHHIIIIIHHHHHH", elf_data)
    offset, size, count = header[6], header[11], header[12]
    _require(0 < count <= 128 and size == 40 and offset >= 52 and offset + size * count <= len(elf_data), "unsupported executable section table")
    words = {}
    for index in range(count):
        _, kind, flags, address, fileoff, length, *_ = struct.unpack_from("<IIIIIIIIII", elf_data, offset + index * size)
        if not flags & 4:
            continue
        _require(kind == 1 and flags & 2 and not flags & 1 and address % 4 == 0 and length % 4 == 0 and fileoff + length <= len(elf_data), "unsupported executable section")
        for step in range(0, length, 4):
            pc = address + step
            _require(pc not in words, "overlapping executable sections")
            instruction = int.from_bytes(elf_data[fileoff+step:fileoff+step+4], "little")
            opcode, f3, f7 = instruction & 127, instruction >> 12 & 7, instruction >> 25
            valid = (opcode in (0x37, 0x17, 0x6F) or opcode == 0x67 and f3 == 0 or opcode == 0x63 and f3 in (0,1,4,5,6,7)
                or opcode == 3 and f3 in (0,1,2,4,5) or opcode == 0x23 and f3 in (0,1,2)
                or opcode == 0x13 and (f3 not in (1,5) or f7 == 0 or f3 == 5 and f7 == 32)
                or opcode == 0x33 and (f7 == 0 or f7 == 32 and f3 in (0,5)))
            _require(instruction & 3 == 3 and valid, "native executable exceeds registered RV32I subset")
            words[pc] = instruction
    _require(0 < len(words) <= 4096, "empty or oversized native executable")
    if firmware:
        instructions = extract_disassembly(disassembly, "disassembly.txt")
        corroborate_instruction_bytes(elf_data, elf, instructions)
        _require({x.address: x.encoding for x in instructions} == words and all(x.width_bits == 32 for x in instructions), "disassembly omits executable bytes")
        _require(re.findall(r'Tag_RISCV_arch:\s*"([^"]+)"', attributes.decode()) == ["rv32i2p1"], "unsupported native ELF ISA attributes")
    return words


def _riscv_case(locator, inputs, resolve, path, source):
    from veriforge.riscv.contracts import RiscvExperiment, SimulationOutput, Comparison
    from veriforge.riscv.trace import normalize_rvfi, parse_spike_commits
    from veriforge.riscv.checker import compare_retirements
    from veriforge.riscv.profile import PLATFORM, CORE_REVISION, REFERENCE_REVISION, ASSEMBLER_FLAGS, COMPILER_FLAGS
    raw = _named_inputs(locator, inputs, resolve, path, {
        "plan": "experiment.json", "profile": "profile.json", "source_manifest": "source-manifest.json",
        "checker_manifest": "checker-manifest.json", "environment": "environment.json", "raw_rvfi": "dut/rvfi.json",
        "raw_spike": "reference/commit.log", "normalized_pair": "retirement.json", "comparison": "comparison.json",
        "object": "smoke.o", "elf": "smoke.elf", "binary": "smoke.bin"})
    result_raw = resolve(locator.raw_result_ref, "native_result")
    _require(path(locator.case_label + "/result.json", locator.raw_result_ref.sha256) == result_raw, "native result path differs")
    result = v1._decode(result_raw, AttemptResult)
    for artifact in result.artifacts:
        path(locator.case_label + "/" + artifact.path, artifact.sha256, size=artifact.size_bytes, original_id=artifact.artifact_id)
    plan = v1._decode(raw["plan"], RiscvExperiment)
    _require((result.attempt_id, result.experiment_id, plan.experiment_id) == (locator.native_attempt_id, locator.wire_experiment_id, locator.wire_experiment_id), "producer attempt/experiment identity differs")
    _require(plan.fixture_id == ("picorv32_reg_write_bug" if locator.case_label == "mutant" else "picorv32_rv32i"), "unsupported native fixture")
    profile, native_source, checker, environment = (_json(raw[x]) for x in ("profile", "source_manifest", "checker_manifest", "environment"))
    _require(_canonical(profile) == _canonical(asdict(PLATFORM)), "unsupported native platform profile")
    _keys(native_source, ("core_revision", "fixture_id", "profile_code_hash", "upstream/picorv32.v", "picorv32_top.sv", "smoke.S", "link.ld"))
    _require(native_source["core_revision"] == CORE_REVISION and native_source["fixture_id"] == plan.fixture_id, "native source configuration differs")
    for name in ("upstream/picorv32.v", "picorv32_top.sv", "smoke.S", "link.ld"):
        _require(native_source[name] == source["targets/riscv/" + name], "native DUT/program source differs")
    _require(native_source["profile_code_hash"] == source["src/veriforge/riscv/profile.py"], "native profile implementation differs")
    _keys(checker, ("checker.py", "trace.py", "contracts.py"))
    _require(_semantic(checker) == RISCV_CHECKER_HASH and all(checker[x] == source["src/veriforge/riscv/" + x] for x in checker), "unsupported retirement checker version")
    _keys(environment, ("tools", "reference_revision", "reference_submodules", "elf_hash", "binary_hash", "object_hash", "assembly_command", "compiler_command", "reference_command", "driver_hash", "memory_hash", "job_hash", "spike_binary_hash", "image_package_hash"))
    _require(environment["tools"] == [tool.model_dump(mode="json") for tool in result.tool_versions]
        and {tool.name for tool in result.tool_versions} == {"verilator", "riscv-gcc", "spike-revision", "cocotb", "python"}, "native tool version record differs")
    _require(environment["assembly_command"] == ["riscv64-unknown-elf-gcc", *ASSEMBLER_FLAGS, "-c", "/workspace/targets/riscv/smoke.S", "-o", "/output/smoke.o"]
        and environment["compiler_command"] == ["riscv64-unknown-elf-gcc", *COMPILER_FLAGS, "-T", "/workspace/targets/riscv/link.ld", "/output/smoke.o", "-o", "/output/smoke.elf"]
        and environment["reference_command"] == ["spike", "--isa=RV32I", "--priv=m", "--pcs=0:0x80000000", "-m0x80000000:0x10000", "--log-commits", "--log=/output/reference/commit.log", "--instructions="+str(plan.max_retired), "/output/smoke.elf"], "unsupported native tool command configuration")
    _require(environment["spike_binary_hash"] == "7020158c1a7b39119a6b1a08b6f43bc6097f95db883ea53a79cf98b4994765e7"
        and environment["image_package_hash"] == "e43d1b9d3074e5c9b6a7718a68b81096fef1559b2ad100b6bb58bb97e21de994"
        and type(environment["reference_submodules"]) is str, "unsupported recorded reference/toolchain version")
    for name, field_name in (("sim_test.py", "driver_hash"), ("memory.py", "memory_hash"), ("sim_job.py", "job_hash")):
        _require(environment[field_name] == source["src/veriforge/riscv/" + name], "native runtime configuration differs")
    _require(environment["reference_revision"] == REFERENCE_REVISION, "unsupported reference implementation")
    for field_name in ("elf", "binary", "object"):
        _require(environment[field_name + "_hash"] == _hash(raw[field_name]), "native environment/program digest differs")
    words = _elf_program(raw["elf"], raw["binary"])
    observed_raw = v1._decode(raw["raw_rvfi"], SimulationOutput)
    _require(observed_raw.cycles <= plan.max_cycles and len(observed_raw.events) <= plan.max_retired, "retirement evidence exceeds plan")
    previous = -1
    for event in observed_raw.events:
        _require(event.cycle is not None and previous < event.cycle < observed_raw.cycles and words.get(event.rvfi_pc_rdata) == event.rvfi_insn, "retirement chronology or instruction bytes differ")
        previous = event.cycle
    def stopped(event):
        return event.rvfi_trap or event.rvfi_mem_addr == PLATFORM.completion and event.rvfi_mem_wmask
    _require(not any(stopped(event) for event in observed_raw.events[:-1]), "retirement continues after stopping event")
    _require(observed_raw.events and observed_raw.termination == "completion_store_retired" and stopped(observed_raw.events[-1]) and not observed_raw.events[-1].rvfi_trap and observed_raw.events[-1].cycle + 1 == observed_raw.cycles,
        "unsupported or inconsistent native stopping envelope")
    observed = normalize_rvfi(observed_raw.events, max_retired=plan.max_retired)
    reference = parse_spike_commits(raw["raw_spike"].decode(), max_retired=plan.max_retired)
    pair = _json(raw["normalized_pair"])
    _keys(pair, ("schema_version", "reference", "observed"))
    _require(_canonical(pair) == _canonical({"schema_version": "riscv.retirement-pair.v1", "reference": [x.model_dump(mode="json") for x in reference], "observed": [x.model_dump(mode="json") for x in observed]}), "normalized native pair differs from full raw traces")
    comparison = compare_retirements(reference, observed)
    _require(comparison.verdict == ("design_mismatch" if locator.case_label == "mutant" else "pass_within_scope"), "native curated case outcome differs")
    _require(v1._decode(raw["comparison"], Comparison) == comparison, "native comparison differs from registered checker")
    identity = dict(source_hash=_semantic(native_source), checker_hash=_semantic(checker), contract_hash=_semantic(profile),
        experiment_hash=canonical_hash(plan), environment_hash=_semantic(environment))
    _require(result.identity is not None and result.identity.model_dump(mode="json") == identity and result.execution_status == "completed" and result.verdict == comparison.verdict and result.usage.cycles == observed_raw.cycles,
        "native result identity/status differs from revalidated evidence")
    divergence = None
    if comparison.first_divergence is not None:
        first = comparison.first_divergence
        divergence = dict(field=first.field, order=first.order, cycle=observed[first.order].source_cycle,
            expected=json.loads(first.expected), observed=json.loads(first.observed))
        signature = result.failure_signature
        _require(signature is not None and (signature.requirement_id, signature.failure_class, signature.first_cycle, signature.expected, signature.observed,
            signature.signature_hash) == (first.requirement_id, "riscv." + first.field, divergence["cycle"], first.expected, first.observed,
            _semantic({"checker": identity["checker_hash"], "contract": identity["contract_hash"], "field": first.field})), "native failure signature differs")
    else:
        _require(result.failure_signature is None, "unexpected native failure signature")
    phases = ("assembler", "compiler", "objcopy", "reference", "dut")
    for phase in phases:
        _require(any(x.path.startswith(phase + "/") for x in result.artifacts), "native phase evidence missing")
    return RiscvRecordedAttempt(locator=locator, inputs=inputs, identity=identity,
        checker=CheckerIdentity(checker_id="riscv-retirement", version="1", sha256=identity["checker_hash"]),
        configuration_hash=_semantic({"profile": profile, "source": native_source, "checker": checker, "environment": environment}),
        producer_execution_status="completed", termination=observed_raw.termination, checker_evidence_status="valid", verdict=comparison.verdict,
        cycles=observed_raw.cycles, cycle_limit=plan.max_cycles, retirement_limit=plan.max_retired,
        retired_count=len(observed), reference_count=len(reference), compared_count=comparison.compared,
        first_divergence=divergence, producer_phases=phases)


def _firmware_recipes():
    # Fixed Linux producer argv as inert data; never call executable.commands
    # (it resolves filesystem paths) or execute any saved/copied command.
    from veriforge.firmware.executable import FLAGS
    root = "/tmp/firmware-snapshot/"
    gcc = "riscv64-unknown-elf-gcc"
    compile_flags = [x for x in FLAGS if not x.startswith("-Wl,") and x not in ("-nostdlib", "-nostartfiles")]
    return {
        "assemble_start": [gcc, *compile_flags, "-c", root+"start.S", "-o", root+"start.o"],
        "assemble_probe": [gcc, *compile_flags, "-c", root+"independent_probe.S", "-o", root+"independent_probe.o"],
        "compile_main": [gcc, *compile_flags, "-I", root[:-1], "-c", root+"main.c", "-o", root+"main.o"],
        "compiler": [gcc, *FLAGS, "-I", root[:-1], "-T", root+"link.ld", root+"start.o", root+"independent_probe.o", root+"main.o", "-Wl,-Map="+root+"firmware.map", "-o", root+"firmware.elf"],
        "binary": ["riscv64-unknown-elf-objcopy", "-O", "binary", root+"firmware.elf", root+"firmware.bin"],
        "disassembly": ["riscv64-unknown-elf-objdump", "-d", "-M", "no-aliases,numeric", root+"firmware.elf"],
        "elf_headers": ["riscv64-unknown-elf-readelf", "-h", "-l", "-A", root+"firmware.elf"],
        "simulator": ["/opt/venv/bin/python", "-m", "veriforge.firmware.sim_job", root[:-1]],
    }


_FW_PROGRAM = {"start_object": "start.o", "probe_object": "independent_probe.o", "main_object": "main.o",
    "elf": "firmware.elf", "binary": "firmware.bin", "map": "firmware.map", "disassembly": "disassembly.txt", "elf_attributes": "elf_headers.txt"}
_FW_SOURCES = {"main": "main.c", "start": "start.S", "probe": "independent_probe.S", "linker_script": "link.ld",
    "wrapper": "firmware_top.sv", "core_wrapper": "picorv32_top.sv", "core": "picorv32.v", "peripheral": "native_registers.sv"}


def _firmware_case(locator, inputs, resolve, path, source, *, original_ref=None):
    from veriforge.contracts import FailureSignature
    from veriforge.firmware.contracts import RegisterContract, PlatformProfile, FactComparison
    from veriforge.firmware.executable import FirmwarePreparation, extract_native_decode
    from veriforge.firmware.extract import extract_header
    from veriforge.firmware.workflow import compare_register_facts
    from veriforge.firmware.harness import HarnessOutput, HarnessCheck, check_observation, lane_mask
    raw = _named_inputs(locator, inputs, resolve, path, {"preparation": "preparation.json", "observation": "observation.json", "register_contract": "register-contract.json", "selected_header": "registers.h", "bad_base_header": "original-bad-registers.h"})
    check_raw = resolve(inputs.check, "native_check")
    _require(path(locator.case_label + ".check.json", inputs.check.sha256) == check_raw, "native firmware check path differs")
    checked_record = _json(check_raw)
    _keys(checked_record, ("candidate_diff_sha256", "check", "cycles", "execution_sha256", "failure_signature", "first_conflict", "identity", "observation_sha256", "original_bad_hash", "program_hashes", "reference_support", "replay_original_hash", "selected_header_hash", "semantic_hash"))
    execution_raw = resolve(locator.execution_ref, "native_execution")
    _require(path(locator.case_label+"/execution.json", locator.execution_ref.sha256) == execution_raw, "native execution path differs")
    execution = _json(execution_raw)
    _keys(execution, ("schema_version", "completed", "fixture_id", "replay", "commands", "files", "wall_seconds"), ("replay_original_hash",))
    replay = locator.case_label.endswith("-replay")
    _require(execution["schema_version"] == "firmware.ci-execution.v1" and execution["completed"] is True and execution["fixture_id"] == locator.fixture_id and execution["replay"] is replay,
        "unsupported native execution envelope")
    _require(type(execution["wall_seconds"]) in (int, float) and math.isfinite(execution["wall_seconds"]) and 0 <= execution["wall_seconds"] <= 120, "native execution wall bound exceeded")
    prep = _json(raw["preparation"])
    _keys(prep, ("schema_version", "preparation", "execution_status", "contract_hash", "profile", "profile_hash", "selected_header_hash", "original_bad_hash", "candidate_provenance", "preparer_hash", "fact_extractor_hash", "address_checker_hash", "core_revision", "runtime_files", "shared_runtime_files", "source_files", "recipes", "recipe_status", "expected_observation", "limitations"))
    _require(prep["schema_version"] == "firmware.executable-preparation.v1" and prep["execution_status"] == "not_run" and prep["recipe_status"] == "prepared_not_executed", "unsupported preparation schema")
    plan = FirmwarePreparation.model_validate(prep["preparation"])
    _require(plan.fixture_id == locator.fixture_id and plan == FirmwarePreparation(fixture_id=locator.fixture_id), "unsupported native firmware configuration/bounds")
    profile = PlatformProfile.model_validate(prep["profile"])
    _require(_canonical(prep["profile"]) == _canonical(PlatformProfile().model_dump(mode="json")) and prep["profile_hash"] == canonical_hash(profile), "native firmware profile differs")
    for field_name, name in (("preparer_hash", "executable.py"), ("fact_extractor_hash", "extract.py"), ("address_checker_hash", "workflow.py")):
        _require(prep[field_name] == source["src/veriforge/firmware/"+name], "native firmware preparer version differs")
    for name, digest in prep["runtime_files"].items():
        _require(name in ("harness.py", "sim_test.py", "sim_job.py") and digest == source["src/veriforge/firmware/"+name], "native firmware runtime differs")
    _require(set(prep["runtime_files"]) == {"harness.py", "sim_test.py", "sim_job.py"}, "native runtime closure incomplete")
    _require(set(prep["shared_runtime_files"]) == {"memory.py", "profile.py", "contracts.py"}, "shared native runtime closure incomplete")
    for name, digest in prep["shared_runtime_files"].items():
        _require(digest == source["src/veriforge/riscv/"+name], "native shared runtime differs")
    checker_subset = {key: value for key, value in source.items() if key.startswith("src/veriforge/firmware/") or key == "scripts/ci_firmware_acceptance.py"}
    _require(_semantic(checker_subset) == FIRMWARE_CHECKER_HASH, "unsupported literal MMIO checker version")
    _require(prep["core_revision"] == profile.core_revision, "native core revision differs")
    candidate = locator.case_label in ("candidate", "candidate-replay")
    _require(prep["candidate_provenance"] == ("curated_header_offset_restoration" if candidate else None), "native candidate provenance differs")
    expected_sources = set(_FW_SOURCES.values()) | {"registers.h", "original-bad-registers.h", "register-contract.json", "address-check.json"} | ({"candidate.diff"} if candidate else set())
    _require(set(prep["source_files"]) == expected_sources, "native prepared source closure differs")
    for name, identity in prep["source_files"].items():
        _keys(identity, ("sha256", "size_bytes"))
        _require(type(identity["size_bytes"]) is int, "strict native source size required")
        path(locator.case_label+"/"+name, identity["sha256"], size=identity["size_bytes"])
    program = _named_inputs(locator, inputs.program, resolve, path, _FW_PROGRAM)
    sources = _named_inputs(locator, inputs.sources, resolve, path, _FW_SOURCES)
    for field_name, filename in _FW_SOURCES.items():
        source_name = "targets/riscv/" + ("upstream/picorv32.v" if field_name == "core" else filename) if field_name in ("core", "core_wrapper") else "targets/firmware/executable/" + filename
        _require(_hash(sources[field_name]) == source[source_name], "native DUT/program snapshot differs from source closure")
    header_kind = "offset_bug" if locator.case_label in ("bad", "bad-replay") else "candidate" if candidate else "control"
    _require(_hash(raw["selected_header"]) == source["targets/firmware/executable/"+header_kind+"/registers.h"] and _hash(raw["bad_base_header"]) == source["targets/firmware/executable/offset_bug/registers.h"], "native curated header differs")
    contract = v1._decode(raw["register_contract"], RegisterContract)
    _require(_hash(raw["register_contract"]) == source["targets/firmware/executable/register-contract.json"] and canonical_hash(contract) == prep["contract_hash"], "native register requirements differ")
    address_check = compare_register_facts(contract, extract_header(raw["selected_header"], "registers.h", tuple(x.header_macro for x in contract.registers)), extract_native_decode(sources["peripheral"]))
    _require(v1._decode(path(locator.case_label+"/address-check.json"), FactComparison) == address_check, "native address fact check differs")
    _require(prep["selected_header_hash"] == _hash(raw["selected_header"]) and prep["original_bad_hash"] == _hash(raw["bad_base_header"]), "native header identity differs")
    recipes = _firmware_recipes()
    _require(prep["recipes"] == {key: value for key, value in recipes.items() if key != "simulator"}, "unsupported firmware compile recipes")
    phases = ("simulator",) if replay else tuple(recipes)
    _require(type(execution["commands"]) is list and len(execution["commands"]) == len(phases), "native phase closure differs")
    for row, phase in zip(execution["commands"], phases, strict=True):
        _keys(row, ("argv", "phase", "returncode", "wall_seconds"))
        _require(row["phase"] == phase and type(row["returncode"]) is int and row["returncode"] == 0 and row["argv"] == recipes[phase], "native command phase/argv/outcome differs")
        _require(type(row["wall_seconds"]) in (int, float) and math.isfinite(row["wall_seconds"]) and 0 <= row["wall_seconds"] <= 120, "native phase wall bound exceeded")
    _require(type(execution["files"]) is dict and 1 <= len(execution["files"]) <= 64, "bounded native execution dependency map required")
    for name, digest in execution["files"].items():
        _safe_path(name)
        path(locator.case_label+"/"+name, digest)
    _require(expected_sources | set(_FW_PROGRAM.values()) | {"observation.json", "execution-identities.json", "simulation-job.json"} <= set(execution["files"]), "native execution omits required dependencies")
    expected_identities = {"elf_sha256": _hash(program["elf"]), "binary_sha256": _hash(program["binary"]), "preparation_sha256": _hash(raw["preparation"])}
    _require(_json(path(locator.case_label+"/execution-identities.json")) == expected_identities, "native executable identities differ")
    _require(_json(path(locator.case_label+"/simulation-job.json")) == {"preparation": plan.model_dump(mode="json"), "binary": "/tmp/firmware-snapshot/firmware.bin", "trace": "/tmp/firmware-snapshot/observation.json"}, "native simulation job differs")
    words = _elf_program(program["elf"], program["binary"], firmware=True, disassembly=program["disassembly"], attributes=program["elf_attributes"])
    observation = v1._decode(raw["observation"], HarnessOutput)
    _require(all(words.get(event.rvfi_pc_rdata) == event.rvfi_insn for event in observation.events), "native observation instruction differs from executable")
    checked = check_observation(observation, plan)
    _require(HarnessCheck.model_validate(checked_record["check"]) == checked and checked.execution_status == "completed", "native MMIO verdict differs from registered checker")
    mmio = [bus for bus in observation.transfers if not bus.instruction and 0x40000000 <= bus.address < 0x40000010]
    bad_case = locator.case_label in ("bad", "bad-replay")
    _require(checked.verdict == ("design_mismatch" if bad_case else "pass_within_scope")
        and checked.first_mmio_sequence == (2 if bad_case else None) and len(mmio) == (4 if bad_case else 9), "native curated MMIO case outcome differs")
    first, conflict, signature = None, None, None
    if checked.first_mmio_sequence is not None:
        index = checked.first_mmio_sequence
        _require(index == 2 and len(mmio) == 4, "unsupported first MMIO discrepancy")
        bus, probe = mmio[index:index+2]
        _require((bus.address, bus.write_strobes, bus.write_data, probe.address, probe.write_strobes, probe.read_data) == (0x40000008, 15, 0x55667788, 0x40000004, 0, 0), "native STATUS discrepancy differs")
        first = dict(requirement_id="FIRMWARE_STATUS_ADDRESS", mmio_sequence=index, bus_sequence=bus.sequence, cycle=bus.cycle,
            expected_address=0x40000004, observed_address=bus.address, expected_probe=0x55667788, observed_probe=probe.read_data)
        conflict = dict(mmio_index=index, bus_sequence=bus.sequence, cycle=bus.cycle, operation="write", expected_address=first["expected_address"],
            observed_address=bus.address, write_strobes=bus.write_strobes, active_lane_value=bus.write_data & lane_mask(bus.write_strobes), expected_probe=first["expected_probe"], observed_probe=probe.read_data)
    tools_raw = resolve(inputs.tool_metadata, "configuration")
    _require(path("tool-metadata.json", inputs.tool_metadata.sha256) == tools_raw, "native tool metadata path differs")
    _require(path("source-manifest.json", inputs.source_manifest.sha256) == _canonical(source) or _json(path("source-manifest.json", inputs.source_manifest.sha256)) == source, "native source manifest reference differs")
    tools = _json(tools_raw)
    identity = dict(source_hash=_semantic(prep), checker_hash=_semantic(checker_subset), contract_hash=_semantic([prep["contract_hash"], prep["profile_hash"]]), experiment_hash=canonical_hash(plan), environment_hash=_semantic(tools))
    if first:
        signature = FailureSignature(requirement_id=first["requirement_id"], failure_class="firmware.status_offset", first_cycle=first["cycle"],
            expected=json.dumps({"address": first["expected_address"], "probe": first["expected_probe"]}, sort_keys=True), observed=json.dumps({"address": first["observed_address"], "probe": first["observed_probe"]}, sort_keys=True),
            signature_hash=_semantic({"checker": identity["checker_hash"], "contract": identity["contract_hash"], "requirement": first["requirement_id"]})).model_dump(mode="json")
    expected_check = dict(identity=identity, program_hashes={name: _hash(program[field_name]) for field_name, name in _FW_PROGRAM.items()},
        observation_sha256=_hash(raw["observation"]), semantic_hash=_semantic(observation.model_dump(mode="json")), check=checked.model_dump(mode="json"),
        first_conflict=first, failure_signature=signature, cycles=observation.cycles, selected_header_hash=prep["selected_header_hash"], original_bad_hash=prep["original_bad_hash"],
        candidate_diff_sha256=_hash(path(locator.case_label+"/candidate.diff")) if candidate else None, replay_original_hash=execution.get("replay_original_hash"),
        execution_sha256=_hash(execution_raw), reference_support="Spike MMIO unsupported; independent literal MMIO checker only")
    _require(_canonical(checked_record) == _canonical(expected_check), "native independent check record differs from complete revalidation")
    _require(observation.events and observation.events[-1].rvfi_mem_addr == 0x8000FF00 and observation.events[-1].rvfi_mem_wmask == 15, "native completion mailbox absent")
    mailbox = observation.events[-1]
    return FirmwareRecordedCase(locator=locator, inputs=inputs, identity=identity,
        checker=CheckerIdentity(checker_id="firmware-literal-mmio", version="1", sha256=identity["checker_hash"]),
        configuration_hash=_semantic({"preparation": prep, "program_hashes": expected_check["program_hashes"], "environment_hash": identity["environment_hash"]}),
        profile_hash=prep["profile_hash"], semantic_hash=expected_check["semantic_hash"], producer_completed=True,
        termination=observation.termination, checker_evidence_status=checked.execution_status, verdict=checked.verdict,
        cycles=observation.cycles, cycle_limit=plan.max_cycles, retired_count=len(observation.events), retirement_limit=plan.max_retired,
        transfer_count=len(observation.transfers), mmio_count=len(mmio), first_conflict=conflict,
        mailbox={"observed": True, "value": mailbox.rvfi_mem_wdata, "cycle": mailbox.cycle}, producer_phases=phases,
        replay_of_execution_ref=original_ref)


def _validate_native_report(report, resolve, path):
    source = _source_closure(path)
    by_label = {case.locator.case_label: case for case in report.cases}
    for case in report.cases:
        if isinstance(case, RiscvRecordedAttempt):
            derived = _riscv_case(case.locator, case.inputs, resolve, path, source)
        else:
            original_ref = by_label[case.locator.case_label.removesuffix("-replay")].locator.execution_ref if case.locator.case_label.endswith("-replay") else None
            derived = _firmware_case(case.locator, case.inputs, resolve, path, source, original_ref=original_ref)
            if case.locator.case_label.endswith("-replay"):
                original = by_label[case.locator.case_label.removesuffix("-replay")]
                execution = _json(resolve(case.locator.execution_ref, "native_execution"))
                _require(execution.get("replay_original_hash") == original.locator.execution_ref.sha256, "saved replay binds a different original execution")
        _require(derived == case, "native derived case metadata differs from registered byte revalidation")
    if isinstance(report, RiscvRecordedReport):
        comparison_raw = resolve(report.recompiled_control.record_ref, "replay_record")
        _require(path("summary.json", report.recompiled_control.record_ref.sha256) == comparison_raw, "recompiled comparison path differs")
        _validate_summary(report, _json(comparison_raw), resolve)
    else:
        bad, fixed = by_label["bad"], by_label["candidate"]
        control = by_label["control"]
        _require(control.semantic_hash == fixed.semantic_hash and all(getattr(control.inputs.program, name).sha256 == getattr(fixed.inputs.program, name).sha256 for name in _FW_PROGRAM), "native candidate differs from control executable/observation")
        import difflib
        original = resolve(bad.inputs.selected_header, "source_snapshot")
        corrected = resolve(fixed.inputs.selected_header, "source_snapshot")
        expected = "".join(difflib.unified_diff(original.decode().splitlines(keepends=True), corrected.decode().splitlines(keepends=True), fromfile="offset_bug/registers.h", tofile="candidate/registers.h")).encode()
        _require(resolve(report.candidate.diff_ref, "candidate_diff") == expected, "curated candidate diff differs from exact original/corrected bytes")
        for replay in report.replays:
            comparison_raw = resolve(replay.record_ref, "replay_record")
            _require(path("summary.json", replay.record_ref.sha256) == comparison_raw, "saved replay comparison path differs")
            _validate_summary(report, _json(comparison_raw), resolve)


def _validate_summary(report, summary, resolve):
    riscv = isinstance(report, RiscvRecordedReport)
    _keys(summary, ("schema_version", "accepted", "builder_settled", "commit", "run_id", "run_attempt", "image_id", "wall_seconds", "attempts" if riscv else "cases"), () if riscv else ("reference_support",))
    origin = report.selection.origin
    _require(summary["schema_version"] == ("riscv.ci.v1" if riscv else "firmware.ci.v1") and summary["accepted"] is True and summary["builder_settled"] is True
        and (summary["commit"], summary["run_id"], summary["run_attempt"]) == (origin.source_commit, str(origin.run_id), str(origin.run_attempt)), "native summary origin/status differs")
    _require(type(summary["image_id"]) is str and re.fullmatch(r"sha256:[0-9a-f]{64}", summary["image_id"]) is not None and type(summary["wall_seconds"]) in (int,float)
        and math.isfinite(summary["wall_seconds"]) and 0 <= summary["wall_seconds"] <= 3600, "native summary metadata unsupported")
    cases = summary["attempts" if riscv else "cases"]
    _require(type(cases) is dict and set(cases) == {x.locator.case_label for x in report.cases}, "native summary case set differs")
    for case in report.cases:
        if riscv:
            pair = _json(resolve(case.inputs.normalized_pair, "native_trace"))
            expected = dict(binary_hash=case.inputs.binary.sha256, compared=case.compared_count, cycles=case.cycles, elf_hash=case.inputs.elf.sha256,
                first_divergence=_json(resolve(case.inputs.comparison, "native_check"))["first_divergence"], identity=case.identity.model_dump(mode="json"), object_hash=case.inputs.object.sha256,
                pair_hash=_semantic(pair), reference_hash=_semantic(pair["reference"]), verdict=case.verdict)
        else:
            expected = _json(resolve(case.inputs.check, "native_check"))
            tools = _json(resolve(case.inputs.tool_metadata, "configuration"))
            _require(summary["image_id"] == tools["image_id"] and summary.get("reference_support") == "Spike MMIO unsupported", "native firmware summary environment differs")
        _require(_canonical(cases[case.locator.case_label]) == _canonical(expected), "native summary differs from included revalidated case")


def admit_native_investigation(record, artifacts, *, authority):
    if type(authority) is not TrustedExternalAdmissionAuthority or getattr(authority, "_seal", None) is not _AUTHORITY_SEAL:
        raise ValueError("sealed external host authority required")
    _require(type(record) is EngineeringInvestigationV2, "typed native-v2 investigation required")
    record = v1._decode(v1._encode(record), EngineeringInvestigationV2, MAX_ENVELOPE_BYTES)
    pins = authority.pins
    _require(all(getattr(record, name) == getattr(pins, name) for name in (
        "project_id", "revision_id", "project_revision_ref", "manifest_ref", "report_kind", "report_ref")), "native record differs from host-pinned authority")
    _require(type(artifacts) is tuple and 1 <= len(artifacts) <= MAX_ARTIFACTS, "bounded native artifact tuple required")
    supplied, roles, canonical = {}, {}, {}
    total = 0
    for item in artifacts:
        _require(type(item) is v1.ArtifactBytes and type(item.data) is bytes, "typed exact artifact bytes required")
        checked = v1.ArtifactBytes(reference=item.reference, data=item.data)
        key = v1._key(checked.reference)
        _require(key not in supplied, "duplicate immutable native artifact identity")
        supplied[key] = checked
        total += len(checked.data)
    _require(total <= MAX_TOTAL_BYTES, "native artifact total byte limit exceeded")

    def resolve(ref, role, model=None, *, canonical_bytes=False):
        key = v1._key(ref)
        _require(key in supplied and supplied[key].reference == ref, "missing or conflicting native reference")
        roles.setdefault(key, set()).add(role)
        raw = supplied[key].data
        if model is None:
            return raw
        parsed = v1._decode(raw, model)
        digest = canonical_hash(parsed)
        _require(key not in canonical or canonical[key] == digest, "conflicting native typed interpretation")
        canonical[key] = digest
        _require(not canonical_bytes or raw == v1._encode(parsed), "derived native metadata must be canonical")
        return parsed

    revision = resolve(record.project_revision_ref, "project_revision", v1.EngineeringProjectRevision, canonical_bytes=True)
    _require((revision.project_id, revision.revision_id) == (record.project_id, record.revision_id), "native project revision differs")
    _require(revision.configuration_hash in {ref.sha256 for ref in revision.source_refs}, "configuration has no retained source")
    for ref in revision.source_refs:
        resolve(ref, "project_source")
    manifest = resolve(record.manifest_ref, "manifest", SystemManifest, canonical_bytes=True)
    _require(manifest.revision_id == record.revision_id and not manifest.relations and not manifest.adapters,
        "native-v2 manifest must contain observations without inferred cross-domain relations")
    report_raw = resolve(record.report_ref, "native_report")
    try:
        report = _REPORT.validate_python(_json(report_raw))
    except Exception as error:
        if isinstance(error, ValueError):
            raise ValueError("native report consistency validation failed") from None
        raise
    _require(report.schema_version == record.report_kind and report.manifest_ref == record.manifest_ref, "native report discriminator or manifest differs")
    _require(report_raw == v1._encode(report), "derived native report must be canonical")
    _require(tuple(case.locator for case in report.cases) == pins.locators, "native cases differ from external authority")
    canonical[v1._key(record.report_ref)] = canonical_hash(report)
    selected = {}
    for binding in report.selection.selected:
        key = v1._key(binding.ref)
        _require(key in supplied and supplied[key].reference == binding.ref and len(supplied[key].data) == binding.size_bytes, "selected native artifact bytes/size differ")
        if binding.provenance == "producer":
            selected[binding.original_relative_path] = binding
            if binding.original_relative_path.endswith(".json"):
                _json(supplied[key].data)

    def path(name, digest=None, *, size=None, role=None, original_id=None):
        _require(name in selected, "required raw dependency omitted")
        binding = selected[name]
        _require(digest is None or binding.ref.sha256 == digest, "raw dependency digest differs")
        _require(original_id is None or binding.original_artifact_id == original_id, "original producer artifact ID differs")
        raw = resolve(binding.ref, role or binding.role)
        _require(size is None or len(raw) == size, "raw dependency size differs")
        return raw

    _validate_inventory(report, path)
    try:
        _validate_native_report(report, resolve, path)
    except (ValueError, KeyError, TypeError, IndexError, OverflowError, UnicodeError, struct.error):
        raise ValueError("native dependency or registered checker revalidation failed") from None
    _require(tuple(a.fact_id for a in record.native_records) == tuple(f.fact_id for f in manifest.facts), "complete ordered native manifest coverage required")
    _require(len(report.cases) == len(manifest.facts), "native manifest case count differs")
    source_refs = {v1._key(ref): ref for ref in revision.source_refs}
    decoded = []
    for attachment, fact, case in zip(record.native_records, manifest.facts, report.cases, strict=True):
        _require((attachment.domain, attachment.native_schema_id, attachment.reference) == (fact.domain, fact.native_schema_id, fact.native_record), "native fact attachment differs")
        _require(fact.category == "observation" and fact.provenance.state == "complete", "native case requires complete observation provenance")
        native = resolve(attachment.reference, "native_case", type(case), canonical_bytes=True)
        _require(native == case and fact.native_record_hash == canonical_hash(native), "native wrapper differs from report case")
        proof = fact.provenance.extraction
        _require(proof is not None and proof.method == "native_parser" and proof.implementation_hash == pins.validator_implementation_hash and proof.extractor_id == VALIDATOR_ID and proof.extractor_version == VALIDATOR_VERSION,
            "native extraction differs from pinned registered validator")
        raw_ref = case.locator.raw_result_ref if isinstance(case, RiscvRecordedAttempt) else case.inputs.check
        _require(raw_ref in tuple(x.artifact for x in fact.provenance.sources), "native provenance omits primary producer bytes")
        for provenance in fact.provenance.sources:
            _require(source_refs.get(v1._key(provenance.artifact)) == provenance.artifact, "native provenance outside project sources")
            resolve(provenance.artifact, "provenance_source")
        decoded.append(native)
    _require(set(roles) == set(supplied), "unreferenced native artifact bytes")
    native_ids = {x.native_attempt_id for x in pins.locators if isinstance(x, ProducerAttemptLocator)}
    _require(all(key[2] is None or key[2] in native_ids for key in roles), "unbound external native attempt namespace")
    ordered = sorted(supplied, key=lambda x: (x[0], x[1], x[2] or ""))
    verified = tuple(VerifiedArtifactV2(reference=supplied[key].reference, size_bytes=len(supplied[key].data),
        roles=tuple(sorted(roles[key])), canonical_record_hash=canonical.get(key)) for key in ordered)
    envelope = AdmissionEnvelopeV2(validation=NativeValidation(implementation_hash=pins.validator_implementation_hash),
        record=record, record_hash=canonical_hash(record), artifacts=verified, total_artifact_bytes=total)
    _require(len(v1._encode(envelope)) <= MAX_ENVELOPE_BYTES, "native admission envelope byte limit exceeded")
    value = object.__new__(AdmittedNativeInvestigation)
    for name, content in dict(envelope=envelope, project_revision=revision, manifest=manifest, native_report=report,
            native_records=tuple(decoded), artifacts=tuple(supplied[key] for key in ordered), _seal=_ADMISSION_SEAL).items():
        object.__setattr__(value, name, content)
    return value


def serialize_native_investigation(admitted):
    _require(type(admitted) is AdmittedNativeInvestigation and getattr(admitted, "_seal", None) is _ADMISSION_SEAL,
        "unsealed native admission")
    encoded = v1._encode(admitted.envelope)
    _require(len(encoded) <= MAX_ENVELOPE_BYTES, "native admission envelope byte limit exceeded")
    return encoded


def read_native_investigation(envelope_bytes, artifacts, *, authority):
    _require(type(envelope_bytes) is bytes and len(envelope_bytes) <= MAX_ENVELOPE_BYTES, "bounded native envelope bytes required")
    _require(_json(envelope_bytes).get("schema_version") == "engineering.admission.v2", "explicit native admission version required")
    envelope = v1._decode(envelope_bytes, AdmissionEnvelopeV2, MAX_ENVELOPE_BYTES)
    admitted = admit_native_investigation(envelope.record, artifacts, authority=authority)
    _require(admitted.envelope == envelope, "stored native admission differs from revalidated bytes")
    return admitted


__all__ = ["EngineeringInvestigationV2", "NativeToolAttachment", "VerifiedArtifactV2", "NativeValidation", "AdmissionEnvelopeV2",
    "TrustedExternalAdmissionAuthority", "AdmittedNativeInvestigation", "NATIVE_SCHEMA_ALLOWLIST", "VALIDATOR_ID", "VALIDATOR_VERSION",
    "create_external_admission_authority", "admit_native_investigation", "read_native_investigation", "serialize_native_investigation"]
