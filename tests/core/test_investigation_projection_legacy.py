"""Focused synthetic checks for the authenticated legacy projection bridge.

Everything in this module is synthetic: bytes, reports, envelopes, blobs and
the registered-policy backend double. Curated producer identity strings are used
only as fixture labels. No store/database read, registrar, legacy worker,
compiler, simulator, network, browser or application is created or started.
"""
from dataclasses import asdict
import contextlib
import hashlib
import json
import os
from pathlib import Path
import tempfile
import struct
from types import ModuleType, SimpleNamespace
import unittest

from veriforge.contracts import canonical_hash
from veriforge.contracts.engineering import (
    EvidenceReference, FactProvenance, NativeFactReference, SystemManifest,
)
from veriforge.contracts.native_tools import (
    ArtifactSelection, CandidateValidation, FirmwareInputs, FirmwareProgram, FirmwareSources,
    FirmwareRecordedCase, FirmwareRecordedReport, HostedOrigin, MailboxObservation, MmioConflict,
    ProducerAttemptLocator, ProducerCaseLocator, RecordedArtifactBinding, RecompiledControlComparison,
    RiscvInputs, RiscvRecordedAttempt, RiscvRecordedReport, SavedProgramReplay, IntegerDivergence,
)
from veriforge.core import investigation_projection as projection
from veriforge.core import investigations, native_validator_policy as policy, service
from veriforge.riscv.checker import compare_retirements
from veriforge.riscv.contracts import SimulationOutput
from veriforge.riscv.profile import PLATFORM
from veriforge.riscv.trace import normalize_rvfi, parse_spike_commits
from veriforge.system_investigation import native_tool_records as native
from veriforge.system_investigation import records as v1

PROJECT = 'synthetic-legacy-project'
REVISION = 'synthetic-legacy-revision'
RISCV_COMMIT = '44b8b5e40ce7841f076e4178df13f38ecdbd05fd'
FIRMWARE_COMMIT = '5565985bbe2bb2a0be869d366baa48c715057657'
RV_LABELS = ('control', 'mutant', 'control-replay')
FW_LABELS = ('control', 'bad', 'candidate', 'bad-replay', 'candidate-replay')
RV_ATTEMPTS = {
    'control': ('attempt-ba01be2c331649828d66ffcca63365ba', 'rv32i-control-v1'),
    'mutant': ('attempt-3507c622a1f143d894f819a0958c0f6a', 'rv32i-functional-register-mutant-v1'),
    'control-replay': ('attempt-e845135395204d61b526d707bae1ef2f', 'rv32i-control-v1'),
}
FW_FIXTURES = {
    'control': 'fw-regmap-control-v1', 'bad': 'fw-regmap-offset-bug-v1',
    'candidate': 'fw-regmap-offset-candidate-v1', 'bad-replay': 'fw-regmap-offset-bug-v1',
    'candidate-replay': 'fw-regmap-offset-candidate-v1',
}
RV_REFERENCE_LOG = (
    b"core 0: 3 0x80000000 (0x00100093) x1 0x00000001\n"
    b"core 0: 3 0x80000004 (0x00112023) mem 0x8000ff00 0x00000001\n"
)


def sha(data):
    return hashlib.sha256(data).hexdigest()


def encoded(value, *, pretty=False):
    if hasattr(value, 'model_dump'):
        value = value.model_dump(mode='json')
    return json.dumps(value, sort_keys=True, separators=None if pretty else (',', ':'),
        indent=2 if pretty else None, ensure_ascii=False).encode()


def fake_elf(instructions):
    code = b''.join(value.to_bytes(4, 'little') for value in instructions)
    shoff = 256 + len(code)
    header = struct.pack('<16sHHIIIIIHHHHHH', b'\x7fELF\x01\x01\x01' + b'\0' * 9,
        2, 243, 1, 0x80000000, 52, shoff, 0, 52, 32, 1, 40, 2, 0)
    program = struct.pack('<IIIIIIII', 1, 256, 0x80000000, 0x80000000, len(code), len(code), 5, 4)
    section = struct.pack('<IIIIIIIIII', 0, 1, 6, 0x80000000, 256, len(code), 0, 0, 4, 0)
    return header + program + b'\0' * (256 - len(header) - len(program)) + code + b'\0' * 40 + section


def rv_event(order, instruction, *, value=0, address=0, mask=0, data=0):
    return dict(rvfi_order=order, cycle=6 + order, rvfi_insn=instruction,
        rvfi_pc_rdata=0x80000000 + 4 * order, rvfi_rd_addr=1 if order == 0 else 0,
        rvfi_rd_wdata=value, rvfi_rs1_rdata=address, rvfi_mem_addr=address,
        rvfi_mem_rmask=0, rvfi_mem_wmask=mask, rvfi_mem_rdata=0, rvfi_mem_wdata=data,
        rvfi_trap=False, rvfi_intr=False)


class _SyntheticUnavailable(ValueError):
    pass


class _SyntheticLegacyReadView:
    """Stand-in for the exact class a registered legacy policy would provide."""
    def __init__(self, *, envelope, project_revision, manifest, native_report):
        self.envelope = envelope
        self.project_revision = project_revision
        self.manifest = manifest
        self.native_report = native_report


class _LookAlikeReadView:
    """Same attribute shape and name suffix, but never the registered class."""
    def __init__(self, *, envelope, project_revision, manifest, native_report):
        self.envelope = envelope
        self.project_revision = project_revision
        self.manifest = manifest
        self.native_report = native_report


class _Refs:
    """Synthetic reference bookkeeping; keys are exactly the admission identity."""
    def __init__(self):
        self.entries = {}

    def make(self, artifact_id, data, *, revision_id=REVISION, attempt_id=None):
        reference = EvidenceReference(artifact_id=artifact_id, sha256=sha(data),
            revision_id=revision_id, attempt_id=attempt_id)
        key = (reference.artifact_id, reference.revision_id, reference.attempt_id)
        assert key not in self.entries, key
        self.entries[key] = (reference, data)
        return reference

    def blobs(self):
        return tuple(_blob(reference, data) for reference, data in self.entries.values())


def _blob(reference, data, *, revision_id=None, attempt_id='keep', digest=None, size=None,
          role='source_snapshot', media_type='application/octet-stream'):
    return SimpleNamespace(artifact_id=reference.artifact_id,
        native_revision_id=reference.revision_id if revision_id is None else revision_id,
        native_attempt_id=reference.attempt_id if attempt_id == 'keep' else attempt_id,
        sha256=reference.sha256 if digest is None else digest,
        size_bytes=len(data) if size is None else size,
        media_type=media_type, role=role, data=data)


def _bindings(origin, bindings):
    selected = tuple(RecordedArtifactBinding(ref=item['ref'], scope=item['scope'],
        provenance=item.get('provenance', 'producer'),
        original_relative_path=item.get('path'), role=item['role'],
        media_type=item['media'], size_bytes=item['size']) for item in bindings)
    inventory = tuple(dict(original_relative_path=item['path'], sha256=item['ref'].sha256,
        size_bytes=item['size']) for item in bindings if item.get('path') is not None)
    return ArtifactSelection(origin=origin, inventory=inventory,
        selected=selected, omitted_paths=())


def _manifest(domain, schema, case_refs):
    """Small synthetic manifest; no real provenance claim is made."""
    facts = tuple(NativeFactReference(fact_id='synthetic-fact-' + str(index),
        domain=domain, native_schema_id=schema, native_record_hash=reference.sha256,
        native_record=reference, category='observation',
        provenance=FactProvenance(state='missing',
            uncertainties=('Synthetic fixture; no producer provenance read.',)))
        for index, reference in enumerate(case_refs))
    return SystemManifest(manifest_id='synthetic-manifest', revision_id=REVISION, facts=facts)


def _revision(manifest_ref):
    return v1.EngineeringProjectRevision(project_id=PROJECT, revision_id=REVISION,
        source_refs=(manifest_ref,), configuration_hash=manifest_ref.sha256)


def _envelope(record, refs, *, version='1'):
    artifacts = tuple(native.VerifiedArtifactV2(reference=reference, size_bytes=len(data),
        roles=('source_snapshot',)) for reference, data in refs.entries.values())
    return native.AdmissionEnvelopeV2(
        validation=native.NativeValidation(version=version, implementation_hash=policy.LEGACY_HASH),
        record=record, record_hash=canonical_hash(record), artifacts=artifacts,
        total_artifact_bytes=sum(item.size_bytes for item in artifacts))


def _summary(domains):
    return dict(id='synthetic-version-1', investigation_id='synthetic-investigation',
        project_id=PROJECT, revision_id=REVISION, content_hash='f' * 64,
        domains=list(domains), schema_version='engineering.admission.v2')


def _riscv_fixture(*, include_all_bytes=True):
    refs = _Refs()
    bindings = []
    receipt = refs.make('synthetic-riscv-receipt', b'synthetic receipt\n', revision_id=RISCV_COMMIT)
    origin = HostedOrigin(source_commit=RISCV_COMMIT, run_id=40920450439,
        artifact_id=12919420477, extracted_receipt_ref=receipt)
    summary_ref = refs.make('synthetic-riscv-summary', b'synthetic summary\n', revision_id=RISCV_COMMIT)
    bindings.append(dict(path='receipt-sha256.json', ref=receipt, scope=origin, role='receipt',
        size=len(b'synthetic receipt\n'), media='application/json'))
    bindings.append(dict(path='summary.json', ref=summary_ref, scope=origin, role='replay_record',
        size=len(b'synthetic summary\n'), media='application/json'))
    reference = parse_spike_commits(RV_REFERENCE_LOG.decode('utf-8'))
    checker_manifest = encoded({'synthetic': 'checker'})
    environment = encoded({'synthetic': 'environment'})
    identity = dict(source_hash=sha(encoded({'synthetic': 'source'})),
        checker_hash=sha(checker_manifest), contract_hash=sha(encoded(asdict(PLATFORM))),
        experiment_hash='4' * 64, environment_hash=sha(environment))
    profile = encoded(asdict(PLATFORM))
    files = {}
    cases = []
    for label in RV_LABELS:
        attempt_id, experiment_id = RV_ATTEMPTS[label]
        values = 2 if label == 'mutant' else 1
        output = SimulationOutput(termination='completion_store_retired', cycles=8, events=[
            rv_event(0, 0x00100093, value=values),
            rv_event(1, 0x00112023, address=0x8000FF00, mask=15, data=1)])
        observed = normalize_rvfi(output.events)
        comparison = compare_retirements(reference, observed)
        case_files = {
            'experiment.json': encoded({'synthetic': True, 'label': label}),
            'profile.json': profile,
            'source-manifest.json': encoded({'synthetic': 'source'}),
            'checker-manifest.json': encoded({'synthetic': 'checker'}),
            'environment.json': encoded({'synthetic': 'environment'}),
            'dut/rvfi.json': encoded(output),
            'reference/commit.log': RV_REFERENCE_LOG,
            'retirement.json': encoded({'schema_version': 'riscv.retirement-pair.v1',
                'reference': [row.model_dump(mode='json') for row in reference],
                'observed': [row.model_dump(mode='json') for row in observed]}),
            'comparison.json': encoded(comparison),
            'smoke.o': b'synthetic object bytes',
            'smoke.elf': fake_elf((0x00100093, 0x00112023)),
            'smoke.bin': b'synthetic binary bytes',
        }
        inputs = {}
        for name, data in case_files.items():
            inputs[name.replace('/', '_').replace('.', '_')] = data
        mapping = {
            'plan': 'experiment.json', 'profile': 'profile.json',
            'source_manifest': 'source-manifest.json', 'checker_manifest': 'checker-manifest.json',
            'environment': 'environment.json', 'raw_rvfi': 'dut/rvfi.json',
            'raw_spike': 'reference/commit.log', 'normalized_pair': 'retirement.json',
            'comparison': 'comparison.json', 'object': 'smoke.o', 'elf': 'smoke.elf',
            'binary': 'smoke.bin',
        }
        refs_by_input = {}
        for field, name in mapping.items():
            data = case_files[name]
            path = label + '/' + name
            reference_value = refs.make('synthetic-' + path.replace('/', '-'), data,
                revision_id=RISCV_COMMIT, attempt_id=attempt_id)
            refs_by_input[field] = reference_value
            files[path] = data
            bindings.append(dict(path=path, ref=reference_value, scope=None, role=None,
                size=len(data), media='application/octet-stream'))
        result_ref = refs.make('synthetic-' + label + '-result', b'synthetic result ' + label.encode(),
            revision_id=RISCV_COMMIT, attempt_id=attempt_id)
        files[label + '/result.json'] = b'synthetic result ' + label.encode()
        locator = ProducerAttemptLocator(origin=origin, case_label=label,
            native_attempt_id=attempt_id, wire_experiment_id=experiment_id,
            raw_result_ref=result_ref)
        case_identity = dict(identity)
        divergence = None
        if comparison.first_divergence is not None:
            first = comparison.first_divergence
            divergence = IntegerDivergence(order=first.order, cycle=6, field='rd_value',
                expected=json.loads(first.expected), observed=json.loads(first.observed))
        case = RiscvRecordedAttempt(locator=locator, inputs=RiscvInputs(**refs_by_input),
            identity=case_identity, checker=dict(checker_id='synthetic.riscv.checker',
                version='1', sha256=case_identity['checker_hash']),
            configuration_hash='6' * 64, producer_execution_status='completed',
            termination='completion_store_retired', checker_evidence_status='valid',
            verdict=comparison.verdict, cycles=8, cycle_limit=100, retirement_limit=100,
            retired_count=len(observed), reference_count=len(reference),
            compared_count=comparison.compared, first_divergence=divergence,
            producer_phases=('assembler', 'compiler', 'objcopy', 'reference', 'dut'))
        cases.append(case)
        for field, reference_value in refs_by_input.items():
            binding = next(item for item in bindings if item['ref'] == reference_value)
            binding['scope'] = locator
            binding['role'] = {
                'plan': 'configuration', 'profile': 'configuration',
                'source_manifest': 'source_snapshot', 'checker_manifest': 'configuration',
                'environment': 'configuration', 'raw_rvfi': 'native_trace',
                'raw_spike': 'reference_trace', 'normalized_pair': 'native_trace',
                'comparison': 'native_check', 'object': 'program', 'elf': 'program',
                'binary': 'program',
            }[field]
        bindings.append(dict(path=label + '/result.json', ref=result_ref, scope=locator,
            role='native_result', size=len(files[label + '/result.json']),
            media='application/json'))
    manifest_ref = refs.make('synthetic-riscv-manifest', b'synthetic manifest\n')
    bindings.append(dict(path=None, ref=manifest_ref, scope=origin, role='manifest',
        size=len(b'synthetic manifest\n'), media='application/json', provenance='adapter_generated'))
    selection = _bindings(origin, bindings)
    report = RiscvRecordedReport(manifest_ref=manifest_ref, selection=selection, cases=tuple(cases),
        recompiled_control=RecompiledControlComparison(original=cases[0].locator,
            repeated=cases[2].locator, record_ref=summary_ref,
            outcome='byte_identical_recompiled_control'))
    case_refs = tuple(refs.make('synthetic-riscv-case-' + case.locator.case_label, encoded(case))
        for case in cases)
    manifest = _manifest('riscv', 'riscv.recorded-attempt.v1', case_refs)
    revision = _revision(manifest_ref)
    revision_ref = refs.make('synthetic-riscv-project-revision', encoded(revision))
    report_ref = refs.make('synthetic-riscv-report', encoded(report))
    record = native.EngineeringInvestigationV2(investigation_id='synthetic-investigation',
        project_id=PROJECT, revision_id=REVISION, project_revision_ref=revision_ref,
        manifest_ref=manifest_ref, report_kind='riscv.recorded-report.v1', report_ref=report_ref,
        native_records=tuple(native.NativeToolAttachment(fact_id='synthetic-fact-' + case.locator.case_label,
            domain='riscv', native_schema_id='riscv.recorded-attempt.v1', reference=reference_value)
            for case, reference_value in zip(cases, case_refs)))
    envelope = _envelope(record, refs)
    view = _SyntheticLegacyReadView(envelope=envelope, project_revision=revision,
        manifest=manifest, native_report=report)
    blobs = refs.blobs() if include_all_bytes else ()
    return _summary(['riscv']), view, blobs, refs, report


def _plain_candidate(label):
    return dict(outcome='scoped_curated_correction', covered_mmio_indices=tuple(range(9)))


def _firmware_fixture():
    refs = _Refs()
    bindings = []
    receipt = refs.make('synthetic-firmware-receipt', b'synthetic firmware receipt\n',
        revision_id=FIRMWARE_COMMIT)
    origin = HostedOrigin(source_commit=FIRMWARE_COMMIT, run_id=40929665294,
        artifact_id=12922737365, extracted_receipt_ref=receipt)
    summary_ref = refs.make('synthetic-firmware-summary', b'synthetic firmware summary\n',
        revision_id=FIRMWARE_COMMIT)
    bindings.append(dict(path='receipt-sha256.json', ref=receipt, scope=origin, role='receipt',
        size=len(b'synthetic firmware receipt\n'), media='application/json'))
    bindings.append(dict(path='summary.json', ref=summary_ref, scope=origin,
        role='replay_record', size=len(b'synthetic firmware summary\n'), media='application/json'))
    source_manifest_ref = refs.make('synthetic-firmware-source-manifest',
        b'synthetic firmware source manifest\n', revision_id=FIRMWARE_COMMIT)
    tool_metadata_ref = refs.make('synthetic-firmware-tool-metadata',
        b'synthetic firmware tool metadata\n', revision_id=FIRMWARE_COMMIT)
    bindings.append(dict(path='source-manifest.json', ref=source_manifest_ref, scope=origin,
        role='source_snapshot', size=len(b'synthetic firmware source manifest\n'),
        media='application/json'))
    bindings.append(dict(path='tool-metadata.json', ref=tool_metadata_ref, scope=origin,
        role='configuration', size=len(b'synthetic firmware tool metadata\n'),
        media='application/json'))
    identity = dict(source_hash='7' * 64, checker_hash='8' * 64, contract_hash='9' * 64,
        experiment_hash='a' * 64, environment_hash='b' * 64)
    bad_header = b'synthetic bad registers header\n'
    good_header = b'synthetic candidate registers header\n'
    originals, locators = {}, {}
    cases = []
    for label in ('control', 'bad', 'candidate'):
        paths = {
            'preparation.json': encoded({'synthetic': 'preparation'}),
            'observation.json': encoded({'synthetic': 'observation'}),
            'check.json': encoded({'synthetic': 'check'}),
            'register-contract.json': encoded({'synthetic': 'contract'}),
            'registers.h': bad_header if label == 'bad' else good_header,
            'original-bad-registers.h': bad_header,
            'start.o': b'synthetic start object',
            'independent_probe.o': b'synthetic probe object',
            'main.o': b'synthetic main object',
            'firmware.elf': b'synthetic firmware elf',
            'firmware.bin': b'synthetic firmware bin',
            'firmware.map': b'synthetic firmware map',
            'disassembly.txt': b'synthetic disassembly',
            'elf_headers.txt': b'synthetic elf headers',
            'execution.json': ('synthetic execution ' + label).encode(),
        }
        path_refs = {}
        for name, data in paths.items():
            path = label + '/' + name
            value = refs.make('synthetic-' + path.replace('/', '-'), data,
                revision_id=FIRMWARE_COMMIT)
            path_refs[name] = value
            bindings.append(dict(path=path, ref=value, scope=None, role=None,
                size=len(data), media='application/json' if name.endswith('.json') else 'application/octet-stream'))
        locator = ProducerCaseLocator(origin=origin, case_label=label, fixture_id=FW_FIXTURES[label],
            execution_ref=path_refs['execution.json'])
        locators[label] = locator
        bad = label == 'bad'
        inputs = FirmwareInputs(
            preparation=path_refs['preparation.json'], observation=path_refs['observation.json'],
            check=path_refs['check.json'], register_contract=path_refs['register-contract.json'],
            selected_header=path_refs['registers.h'], bad_base_header=path_refs['original-bad-registers.h'],
            program=FirmwareProgram(start_object=path_refs['start.o'],
                probe_object=path_refs['independent_probe.o'], main_object=path_refs['main.o'],
                elf=path_refs['firmware.elf'], binary=path_refs['firmware.bin'],
                map=path_refs['firmware.map'], disassembly=path_refs['disassembly.txt'],
                elf_attributes=path_refs['elf_headers.txt']),
            sources=FirmwareSources(main=refs.make('synthetic-' + label + '-main.c', b'synthetic main source\n', revision_id=FIRMWARE_COMMIT),
                start=refs.make('synthetic-' + label + '-start.S', b'synthetic start source\n', revision_id=FIRMWARE_COMMIT),
                probe=refs.make('synthetic-' + label + '-probe.S', b'synthetic probe source\n', revision_id=FIRMWARE_COMMIT),
                linker_script=refs.make('synthetic-' + label + '-link.ld', b'synthetic linker script\n', revision_id=FIRMWARE_COMMIT),
                wrapper=refs.make('synthetic-' + label + '-wrapper.sv', b'synthetic wrapper\n', revision_id=FIRMWARE_COMMIT),
                core_wrapper=refs.make('synthetic-' + label + '-core-wrapper.sv', b'synthetic core wrapper\n', revision_id=FIRMWARE_COMMIT),
                core=refs.make('synthetic-' + label + '-core.v', b'synthetic core\n', revision_id=FIRMWARE_COMMIT),
                peripheral=refs.make('synthetic-' + label + '-native-registers.sv', b'synthetic peripheral\n', revision_id=FIRMWARE_COMMIT)),
            source_manifest=source_manifest_ref, tool_metadata=tool_metadata_ref)
        for name, value in path_refs.items():
            binding = next(item for item in bindings if item['ref'] == value)
            binding['scope'] = locator
            binding['role'] = {
                'preparation.json': 'configuration', 'observation.json': 'native_trace',
                'check.json': 'native_check', 'register-contract.json': 'configuration',
                'registers.h': 'source_snapshot', 'original-bad-registers.h': 'source_snapshot',
                'execution.json': 'native_execution',
            }.get(name, 'program' if name in ('start.o', 'independent_probe.o', 'main.o',
                'firmware.elf', 'firmware.bin', 'firmware.map', 'disassembly.txt', 'elf_headers.txt')
                else 'source_snapshot')
        for field in type(inputs.sources).model_fields:
            reference_value = getattr(inputs.sources, field)
            bindings.append(dict(path=label + '/' + field, ref=reference_value, scope=locator,
                role='source_snapshot', size=len(refs.entries[(reference_value.artifact_id,
                    reference_value.revision_id, reference_value.attempt_id)][1]),
                media='application/octet-stream'))
        conflict = None
        mailbox = MailboxObservation(observed=True, value=1, cycle=20)
        if bad:
            conflict = MmioConflict(mmio_index=0, bus_sequence=0, cycle=7, operation='write',
                expected_address=0x40000000, observed_address=0x40000004, write_strobes=15,
                active_lane_value=0x11223344, expected_probe=0x11223344, observed_probe=0x55667788)
            mailbox = MailboxObservation(observed=False)
        case = FirmwareRecordedCase(locator=locator, inputs=inputs,
            identity=identity, checker=dict(checker_id='synthetic.firmware.checker',
                version='1', sha256=identity['checker_hash']),
            configuration_hash='c' * 64, profile_hash='d' * 64, semantic_hash='e' * 64,
            producer_completed=True, termination='completion_store_retired',
            checker_evidence_status='completed',
            verdict='design_mismatch' if bad else 'pass_within_scope',
            cycles=30, cycle_limit=100, retired_count=10, retirement_limit=100,
            transfer_count=10, mmio_count=9, first_conflict=conflict, mailbox=mailbox,
            producer_phases=('assemble_start', 'assemble_probe', 'compile_main', 'compiler',
                'binary', 'disassembly', 'elf_headers', 'simulator'))
        originals[label] = case
        cases.append(case)
    for label in ('bad-replay', 'candidate-replay'):
        original = originals[label.removesuffix('-replay')]
        paths = {
            'preparation.json': encoded({'synthetic': 'preparation'}),
            'observation.json': encoded({'synthetic': 'observation'}),
            'check.json': encoded({'synthetic': 'check'}),
            'register-contract.json': encoded({'synthetic': 'contract'}),
            'registers.h': bad_header if label.startswith('bad') else good_header,
            'original-bad-registers.h': bad_header,
            'start.o': b'synthetic start object',
            'independent_probe.o': b'synthetic probe object',
            'main.o': b'synthetic main object',
            'firmware.elf': b'synthetic firmware elf',
            'firmware.bin': b'synthetic firmware bin',
            'firmware.map': b'synthetic firmware map',
            'disassembly.txt': b'synthetic disassembly',
            'elf_headers.txt': b'synthetic elf headers',
            'execution.json': ('synthetic execution ' + label).encode(),
        }
        path_refs = {}
        for name, data in paths.items():
            path = label + '/' + name
            value = refs.make('synthetic-' + path.replace('/', '-'), data,
                revision_id=FIRMWARE_COMMIT)
            path_refs[name] = value
            bindings.append(dict(path=path, ref=value, scope=None, role=None, size=len(data),
                media='application/json' if name.endswith('.json') else 'application/octet-stream'))
        locator = ProducerCaseLocator(origin=origin, case_label=label,
            fixture_id=FW_FIXTURES[label], execution_ref=path_refs['execution.json'])
        locators[label] = locator
        inputs = FirmwareInputs(
            preparation=path_refs['preparation.json'], observation=path_refs['observation.json'],
            check=path_refs['check.json'], register_contract=path_refs['register-contract.json'],
            selected_header=path_refs['registers.h'], bad_base_header=path_refs['original-bad-registers.h'],
            program=FirmwareProgram(start_object=path_refs['start.o'],
                probe_object=path_refs['independent_probe.o'], main_object=path_refs['main.o'],
                elf=path_refs['firmware.elf'], binary=path_refs['firmware.bin'],
                map=path_refs['firmware.map'], disassembly=path_refs['disassembly.txt'],
                elf_attributes=path_refs['elf_headers.txt']),
            sources=FirmwareSources(main=refs.make('synthetic-' + label + '-main.c', b'synthetic main source\n', revision_id=FIRMWARE_COMMIT),
                start=refs.make('synthetic-' + label + '-start.S', b'synthetic start source\n', revision_id=FIRMWARE_COMMIT),
                probe=refs.make('synthetic-' + label + '-probe.S', b'synthetic probe source\n', revision_id=FIRMWARE_COMMIT),
                linker_script=refs.make('synthetic-' + label + '-link.ld', b'synthetic linker script\n', revision_id=FIRMWARE_COMMIT),
                wrapper=refs.make('synthetic-' + label + '-wrapper.sv', b'synthetic wrapper\n', revision_id=FIRMWARE_COMMIT),
                core_wrapper=refs.make('synthetic-' + label + '-core-wrapper.sv', b'synthetic core wrapper\n', revision_id=FIRMWARE_COMMIT),
                core=refs.make('synthetic-' + label + '-core.v', b'synthetic core\n', revision_id=FIRMWARE_COMMIT),
                peripheral=refs.make('synthetic-' + label + '-native-registers.sv', b'synthetic peripheral\n', revision_id=FIRMWARE_COMMIT)),
            source_manifest=source_manifest_ref, tool_metadata=tool_metadata_ref)
        for name, value in path_refs.items():
            binding = next(item for item in bindings if item['ref'] == value)
            binding['scope'] = locator
            binding['role'] = {
                'preparation.json': 'configuration', 'observation.json': 'native_trace',
                'check.json': 'native_check', 'register-contract.json': 'configuration',
                'registers.h': 'source_snapshot', 'original-bad-registers.h': 'source_snapshot',
                'execution.json': 'native_execution',
            }.get(name, 'program' if name in ('start.o', 'independent_probe.o', 'main.o',
                'firmware.elf', 'firmware.bin', 'firmware.map', 'disassembly.txt', 'elf_headers.txt')
                else 'source_snapshot')
        for field in type(inputs.sources).model_fields:
            reference_value = getattr(inputs.sources, field)
            bindings.append(dict(path=label + '/' + field, ref=reference_value, scope=locator,
                role='source_snapshot', size=len(refs.entries[(reference_value.artifact_id,
                    reference_value.revision_id, reference_value.attempt_id)][1]),
                media='application/octet-stream'))
        cases.append(original.model_copy(update=dict(locator=locator, inputs=inputs,
            producer_phases=('simulator',),
            replay_of_execution_ref=original.locator.execution_ref)))
    by_label = {case.locator.case_label: case for case in cases}
    bad_case, candidate_case = by_label['bad'], by_label['candidate']
    diff_ref = refs.make('synthetic-firmware-candidate-diff', b'synthetic diff\n',
        revision_id=FIRMWARE_COMMIT)
    bindings.append(dict(path='candidate/candidate.diff', ref=diff_ref,
        scope=candidate_case.locator, role='candidate_diff', size=len(b'synthetic diff\n'),
        media='text/x-diff'))
    manifest_ref = refs.make('synthetic-firmware-manifest', b'synthetic firmware manifest\n')
    bindings.append(dict(path=None, ref=manifest_ref, scope=origin, role='manifest',
        size=len(b'synthetic firmware manifest\n'), media='application/json',
        provenance='adapter_generated'))
    selection = _bindings(origin, bindings)
    report = FirmwareRecordedReport(manifest_ref=manifest_ref, selection=selection,
        cases=tuple(cases),
        candidate=CandidateValidation(bad=bad_case.locator, candidate=candidate_case.locator,
            diff_ref=diff_ref, original_bad_header_ref=bad_case.inputs.selected_header,
            candidate_header_ref=candidate_case.inputs.selected_header,
            protected_checker_hash=bad_case.identity.checker_hash,
            protected_contract_hash=bad_case.identity.contract_hash,
            protected_profile_hash=bad_case.profile_hash,
            covered_mmio_indices=tuple(range(9)), outcome='scoped_curated_correction'),
        replays=tuple(SavedProgramReplay(original=originals[label].locator,
            repeated=locators[label + '-replay'], record_ref=summary_ref,
            outcome='semantic_saved_program_replay') for label in ('bad', 'candidate')))
    case_refs = tuple(refs.make('synthetic-firmware-case-' + case.locator.case_label, encoded(case))
        for case in cases)
    manifest = _manifest('firmware', 'firmware.recorded-case.v1', case_refs)
    revision = _revision(manifest_ref)
    revision_ref = refs.make('synthetic-firmware-project-revision', encoded(revision))
    report_ref = refs.make('synthetic-firmware-report', encoded(report))
    record = native.EngineeringInvestigationV2(investigation_id='synthetic-investigation',
        project_id=PROJECT, revision_id=REVISION, project_revision_ref=revision_ref,
        manifest_ref=manifest_ref, report_kind='firmware.recorded-report.v1', report_ref=report_ref,
        native_records=tuple(native.NativeToolAttachment(fact_id='synthetic-fact-' + case.locator.case_label,
            domain='firmware', native_schema_id='firmware.recorded-case.v1', reference=reference_value)
            for case, reference_value in zip(cases, case_refs)))
    envelope = _envelope(record, refs)
    view = _SyntheticLegacyReadView(envelope=envelope, project_revision=revision,
        manifest=manifest, native_report=report)
    return _summary(['firmware']), view, (), refs, report


class LegacyProjectionChecks(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(self.enterContext(tempfile.TemporaryDirectory(
            prefix='b6-case-', dir=os.environ.get('B6_CANDIDATE_TMP'))))
        self.addCleanup(policy.configure_registered_validator, None)
        self.source = self.tmp / 'synthetic_registered_policy_source.txt'
        self.source.write_bytes(b'# synthetic host registration identity; no worker\n')
        self.module = self._backend()
        policy.configure_registered_validator(self.module, source_sha256=sha(self.source.read_bytes()))

    def _backend(self):
        module = ModuleType('synthetic_registered_legacy_policy')
        module.__file__ = str(self.source)
        for name in ('LEGACY_HASH', 'CURRENT_HASH', 'REPORT_KINDS', 'MAX_NATIVE_PAGE',
                'MAX_OPERATION_BYTES', 'OPERATION_SECONDS'):
            setattr(module, name, getattr(policy, name))
        module.PolicyUnavailable = _SyntheticUnavailable
        module.LegacyReadView = _SyntheticLegacyReadView
        module._verify_sources = lambda: None

        @contextlib.contextmanager
        def operation():
            yield

        def unavailable(raw, artifacts):
            raise _SyntheticUnavailable('Synthetic backend does not read evidence')

        module.validation_operation = operation
        module.select_route = lambda envelope: ('legacy' if envelope.validation.implementation_hash
            == policy.LEGACY_HASH else 'current')
        module.read_legacy = unavailable
        module.queue_legacy = unavailable
        module.flush_legacy = lambda: None
        return module

    def _response(self, fixture):
        original = investigations.projection_inputs
        investigations.projection_inputs = lambda session, investigation_id, version_id: fixture[:3]
        try:
            return projection.projection_response(object(), 'synthetic-investigation',
                'synthetic-version-1')
        finally:
            investigations.projection_inputs = original

    def test_registered_exact_legacy_riscv_three_case_route(self):
        summary, view, blobs, refs, report = _riscv_fixture()
        document = projection.build_projection(summary, view, blobs)
        self.assertEqual(document['schema_version'], 'engineering.projection.v1')
        self.assertEqual(document['scope'], 'recorded_import')
        self.assertEqual(document['execution_status'], 'not_run')
        self.assertEqual((document['project_id'], document['investigation_id'],
            document['version_id'], document['content_hash']),
            (PROJECT, 'synthetic-investigation', 'synthetic-version-1', 'f' * 64))
        self.assertEqual(document['gaps'], [])
        [riscv] = document['projections']
        self.assertEqual(riscv['domain'], 'riscv')
        self.assertEqual(riscv['data']['report_kind'], 'riscv.recorded-report.v1')
        self.assertEqual([case['case_label'] for case in riscv['data']['cases']],
            ['control', 'mutant', 'control-replay'])
        replay = riscv['data']['cases'][2]['lineage']
        self.assertEqual(replay['mode'], 'recompiled')
        self.assertEqual(replay['status'], 'exact_recompiled_replay')
        self.assertEqual(replay['original'], report.cases[0].locator.model_dump(mode='json'))
        self.assertEqual(riscv['data']['cases'][1]['execution']['design_verdict'],
            'design_mismatch')
        self.assertEqual(riscv['data']['cases'][0]['projection_status'], 'complete')

    def test_registered_exact_legacy_firmware_five_case_candidate_replay_route(self):
        summary, view, blobs, refs, report = _firmware_fixture()
        self.assertEqual(blobs, ())
        document = projection.build_projection(summary, view, blobs)
        [firmware] = document['projections']
        self.assertEqual(firmware['domain'], 'firmware')
        self.assertEqual(firmware['data']['report_kind'], 'firmware.recorded-report.v1')
        cases = firmware['data']['cases']
        self.assertEqual([case['case_label'] for case in cases], list(FW_LABELS))
        self.assertEqual(cases[1]['execution']['design_verdict'], 'design_mismatch')
        self.assertEqual(cases[2]['execution']['design_verdict'], 'pass_within_scope')
        self.assertIsNotNone(cases[2]['candidate'])
        self.assertEqual(cases[2]['candidate']['original']['case_label'], 'bad')
        self.assertEqual(cases[2]['candidate']['repeated']['case_label'], 'candidate')
        self.assertFalse(cases[2]['candidate']['source_bytes_available'])
        self.assertEqual(cases[3]['lineage']['status'], 'original_bytes_missing')
        self.assertEqual(cases[4]['lineage']['status'], 'original_bytes_missing')
        self.assertEqual(cases[0]['projection_status'], 'partial')
        self.assertTrue(any(gap['code'] == 'missing_artifact' for gap in cases[0]['gaps']))

    def test_unregistered_stale_and_look_alike_refuse(self):
        summary, view, blobs, refs, _ = _riscv_fixture()
        # Registered exact class succeeds once as the positive control.
        self.assertIn('riscv', projection.build_projection(summary, view, blobs)['projections'][0]['domain'])
        look_alike = _LookAlikeReadView(envelope=view.envelope,
            project_revision=view.project_revision, manifest=view.manifest,
            native_report=view.native_report)
        with self.assertRaises(ValueError):
            projection.build_projection(summary, look_alike, blobs)
        with self.assertRaises(service.Conflict):
            self._response((summary, look_alike, blobs))
        # Stale source pin: registration is captured but its source changes.
        self.source.write_bytes(b'# changed synthetic host registration source\n')
        with self.assertRaises(ValueError):
            projection.build_projection(summary, view, blobs)
        with self.assertRaises(service.Conflict):
            self._response((summary, view, blobs))
        # Restore a fresh registration, then remove it entirely.
        policy.configure_registered_validator(None)
        self.source.write_bytes(b'# synthetic host registration identity; no worker\n')
        self.module = self._backend()
        policy.configure_registered_validator(self.module,
            source_sha256=sha(self.source.read_bytes()))
        policy.configure_registered_validator(None)
        with self.assertRaises(ValueError):
            projection.build_projection(summary, view, blobs)
        with self.assertRaises(service.Conflict):
            self._response((summary, view, blobs))

    def test_wrong_hash_and_size_blobs_refuse(self):
        summary, view, blobs, refs, _ = _riscv_fixture()
        reference, data = next(iter(refs.entries.values()))
        for damaged in (
                _blob(reference, data, digest='0' * 64),
                _blob(reference, data, size=len(data) + 1),
                _blob(reference, b'x' + data),
                _blob(reference, data, media_type='application/json'),
                _blob(reference, data, role='program')):
            fixtures = tuple(damaged if item.artifact_id == reference.artifact_id else item
                for item in blobs)
            with self.assertRaises(ValueError):
                projection.build_projection(summary, view, fixtures)

    def test_duplicate_foreign_and_cross_version_blobs_refuse(self):
        summary, view, blobs, refs, _ = _riscv_fixture()
        reference, data = next(iter(refs.entries.values()))
        with self.assertRaises(ValueError):
            projection.build_projection(summary, view, blobs + (_blob(reference, data),))
        with self.assertRaises(ValueError):
            projection.build_projection(summary, view, blobs + (
                _blob(EvidenceReference(artifact_id='synthetic-foreign', sha256='1' * 64,
                    revision_id=RISCV_COMMIT, attempt_id='synthetic-attempt'), b'foreign'),))
        with self.assertRaises(ValueError):
            projection.build_projection(summary, view, blobs + (
                _blob(reference, data, revision_id='other-synthetic-version'),))

    def test_cross_version_report_reference_refuses(self):
        summary, view, blobs, refs, report = _riscv_fixture()
        case = report.cases[0]
        other = EvidenceReference(artifact_id=case.inputs.environment.artifact_id,
            sha256=case.inputs.environment.sha256, revision_id='other-synthetic-version',
            attempt_id=case.inputs.environment.attempt_id)
        changed = case.model_copy(update={'inputs': case.inputs.model_copy(
            update={'environment': other})})
        changed_report = report.model_copy(update={'cases': (changed,) + report.cases[1:]})
        changed_view = _SyntheticLegacyReadView(envelope=view.envelope,
            project_revision=view.project_revision, manifest=view.manifest,
            native_report=changed_report)
        with self.assertRaises(ValueError):
            projection.build_projection(summary, changed_view, blobs)
        # And an envelope/report from another version cannot borrow this summary.
        swapped = view.envelope.model_copy(update={'record': view.envelope.record.model_copy(
            update={'revision_id': 'other-synthetic-version'})})
        swapped_view = _SyntheticLegacyReadView(envelope=swapped,
            project_revision=view.project_revision, manifest=view.manifest,
            native_report=report)
        with self.assertRaises(ValueError):
            projection.build_projection(summary, swapped_view, blobs)

    def test_missing_optional_bytes_remain_explicit_partial(self):
        summary, view, blobs, refs, _ = _riscv_fixture()
        raw_spike = view.native_report.cases[0].inputs.raw_spike
        reduced = tuple(blob for blob in blobs if (blob.artifact_id, blob.native_revision_id,
            blob.native_attempt_id) != (raw_spike.artifact_id, raw_spike.revision_id,
            raw_spike.attempt_id))
        self.assertEqual(len(reduced), len(blobs) - 1)
        document = projection.build_projection(summary, view, reduced)
        case = document['projections'][0]['data']['cases'][0]
        self.assertEqual(case['projection_status'], 'partial')
        reference = case['evidence']
        missing = next(row for row in reference if row['role'] == 'raw_reference')
        self.assertEqual(missing['status'], 'missing')
        self.assertIn(dict(code='missing_artifact', role='raw_reference',
            reference=raw_spike.model_dump(mode='json')), case['gaps'])
        complete = projection.build_projection(summary, view, blobs)
        self.assertNotEqual(json.dumps(document, sort_keys=True),
            json.dumps(complete, sort_keys=True))

    def test_projection_response_carries_blobs_preserves_etag_and_bounds(self):
        summary, view, blobs, refs, _ = _riscv_fixture()
        encoded_response, digest = self._response((summary, view, blobs))
        self.assertLessEqual(len(encoded_response), projection.MAX_PROJECTION_BYTES)
        self.assertEqual(digest, sha(encoded_response))
        document = projection.build_projection(summary, view, blobs)
        self.assertEqual(json.loads(encoded_response.decode('utf-8')), document)
        self.assertEqual((document['scope'], document['execution_status']),
            ('recorded_import', 'not_run'))
        # The old response discarded the verified blobs; a duplicate blob is
        # rejected only when the verified tuple is actually carried.
        self.assertIn('riscv', {item['domain'] for item in document['projections']})
        with self.assertRaises(service.Conflict):
            self._response((summary, view, blobs + (_blob(*next(iter(refs.entries.values()))),)))
        previous = projection.MAX_PROJECTION_BYTES
        projection.MAX_PROJECTION_BYTES = 1
        try:
            with self.assertRaises(service.Conflict):
                self._response((summary, view, blobs))
        finally:
            projection.MAX_PROJECTION_BYTES = previous

    def test_current_admission_type_remains_on_the_current_route(self):
        summary, view, blobs, refs, report = _riscv_fixture()
        current = object.__new__(native.AdmittedNativeInvestigation)
        for name, value in dict(envelope=view.envelope, project_revision=view.project_revision,
                manifest=view.manifest, native_report=report,
                native_records=(report.cases[0],), artifacts=(), _seal=object()).items():
            object.__setattr__(current, name, value)
        self.assertTrue(policy.is_native_view(current))
        # Passing registered-legacy blobs must not move an exact current
        # admission onto the legacy adapter: the current branch ignores blobs
        # and iterates its own admitted records (one synthetic record here).
        document = projection.build_projection(summary, current, blobs)
        cases = document['projections'][0]['data']['cases']
        self.assertEqual(len(cases), 1)
        self.assertEqual(cases[0]['case_label'], 'control')
        self.assertEqual(document['projections'][0]['data']['report_kind'],
            'riscv.recorded-report.v1')


class _OperationProbe:
    """Test-only view of the captured supervisor operation identity."""

    @staticmethod
    def identity():
        return id(policy._OPERATION.get())


def _policy_operation_backend(source_path, state):
    """Synthetic registered policy that models the retained operation lifecycle.

    One operation owns a deadline derived from the deterministic clock. Nested
    supervisor calls reuse it; the outer final exit rechecks the registered source
    and the deadline. No registrar, worker, store or real policy code is run.
    """
    module = ModuleType('synthetic_registered_operation_policy')
    module.__file__ = str(source_path)
    for name in ('LEGACY_HASH', 'CURRENT_HASH', 'REPORT_KINDS', 'MAX_NATIVE_PAGE',
            'MAX_OPERATION_BYTES', 'OPERATION_SECONDS'):
        setattr(module, name, getattr(policy, name))
    module.PolicyUnavailable = _SyntheticUnavailable
    module.LegacyReadView = _SyntheticLegacyReadView
    module.pinned_source_sha256 = sha(source_path.read_bytes())

    def verify_sources():
        state['source_checks'] += 1
        if sha(source_path.read_bytes()) != module.pinned_source_sha256:
            raise _SyntheticUnavailable('Synthetic registered source changed')

    module._verify_sources = verify_sources

    @contextlib.contextmanager
    def operation():
        if state['operation'] is not None:
            state['nested'] += 1
            yield state['operation']
            return
        state['entries'] += 1
        current = {'deadline': state['clock'] + state['deadline_seconds']}
        state['operation'] = current
        try:
            yield current
        finally:
            state['final_exits'] += 1
            try:
                verify_sources()
                if state['clock'] >= current['deadline']:
                    raise _SyntheticUnavailable('Synthetic operation deadline exceeded')
            finally:
                state['operation'] = None

    module.validation_operation = operation
    module.select_route = lambda envelope: ('legacy' if envelope.validation.implementation_hash
        == policy.LEGACY_HASH else 'current')

    def unavailable(raw, artifacts):
        raise _SyntheticUnavailable('Synthetic backend does not read evidence')

    module.read_legacy = unavailable
    module.queue_legacy = unavailable
    module.flush_legacy = lambda: None
    return module


class PolicyOperationCorrectionTests(unittest.TestCase):
    """B6-F1 regressions: one captured policy operation spans the whole response."""

    def setUp(self):
        self.tmp = Path(self.enterContext(tempfile.TemporaryDirectory(
            prefix='b6-policy-', dir=os.environ.get('B6_CANDIDATE_TMP'))))
        self.addCleanup(policy.configure_registered_validator, None)
        self.source = self.tmp / 'synthetic_registered_policy_source.txt'
        self.source.write_bytes(b'# synthetic host registration identity; no worker\n')
        self.state = dict(clock=0.0, deadline_seconds=policy.OPERATION_SECONDS, operation=None,
            entries=0, nested=0, final_exits=0, source_checks=0, encoded=0,
            read_calls=0, build_calls=0, op_ids=[])
        self.module = _policy_operation_backend(self.source, self.state)
        policy.configure_registered_validator(self.module,
            source_sha256=sha(self.source.read_bytes()))

    def _response(self, fixture, *, read_seconds=0.0, build_seconds=0.0,
            build_error=None, change_source_on_encode=False):
        real_inputs = investigations.projection_inputs
        real_build = projection.build_projection
        real_json = projection.json
        state = self.state

        def read(session, investigation_id, version_id):
            state['read_calls'] += 1
            state['op_ids'].append(_OperationProbe.identity())
            with policy.validation_operation():
                result = fixture[:3]
            state['clock'] += read_seconds
            return result

        def build(summary, admitted, blobs=()):
            state['build_calls'] += 1
            state['op_ids'].append(_OperationProbe.identity())
            with policy.validation_operation():
                if build_error is not None:
                    raise build_error
                result = real_build(summary, admitted, blobs)
            state['clock'] += build_seconds
            return result

        class EncodingProxy:
            def dumps(proxy, *args, **kwargs):
                state['encoded'] += 1
                state['op_ids'].append(_OperationProbe.identity())
                if change_source_on_encode:
                    self.source.write_bytes(b'# changed synthetic registered source\n')
                return real_json.dumps(*args, **kwargs)

        investigations.projection_inputs = read
        projection.build_projection = build
        projection.json = EncodingProxy()
        try:
            return projection.projection_response(object(), 'synthetic-investigation',
                'synthetic-version-1')
        finally:
            investigations.projection_inputs = real_inputs
            projection.build_projection = real_build
            projection.json = real_json

    def test_one_captured_operation_spans_read_build_and_encoding(self):
        summary, view, blobs, refs, _ = _riscv_fixture()
        encoded_response, digest = self._response((summary, view, blobs),
            read_seconds=2.0, build_seconds=1.0)
        self.assertEqual(digest, sha(encoded_response))
        self.assertEqual((self.state['read_calls'], self.state['build_calls'],
            self.state['encoded']), (1, 1, 1))
        self.assertEqual(len(set(self.state['op_ids'])), 1)
        self.assertNotIn(None, self.state['op_ids'])
        self.assertEqual(self.state['entries'], 1)
        self.assertEqual(self.state['final_exits'], 1)
        self.assertIsNone(self.state['operation'])
        self.assertIsNone(policy._OPERATION.get())

    def test_cumulative_elapsed_work_beyond_deadline_conflicts(self):
        summary, view, blobs, refs, _ = _riscv_fixture()
        with self.assertRaises(service.Conflict) as raised:
            self._response((summary, view, blobs), read_seconds=5.0, build_seconds=3.5)
        self.assertEqual(str(raised.exception),
            'Investigation projection is unavailable or changed')
        self.assertEqual(self.state['clock'], 8.5)
        self.assertEqual(self.state['entries'], 1)
        self.assertEqual(self.state['final_exits'], 1)
        self.assertEqual(len(set(self.state['op_ids'])), 1)
        self.assertIsNone(self.state['operation'])
        self.assertIsNone(policy._OPERATION.get())

    def test_source_change_during_encoding_conflicts_at_final_exit(self):
        summary, view, blobs, refs, _ = _riscv_fixture()
        with self.assertRaises(service.Conflict):
            self._response((summary, view, blobs), change_source_on_encode=True)
        self.assertEqual(self.state['encoded'], 1)
        self.assertEqual(self.state['final_exits'], 1)
        self.assertEqual(self.state['entries'], 1)
        self.assertGreaterEqual(self.state['source_checks'], 2)
        self.assertIsNone(self.state['operation'])
        self.assertIsNone(policy._OPERATION.get())

    def test_final_policy_unavailable_is_translated(self):
        summary, view, blobs, refs, _ = _riscv_fixture()
        with self.assertRaises(service.Conflict) as raised:
            self._response((summary, view, blobs),
                build_error=policy.PolicyUnavailable('synthetic final exit failure'))
        self.assertEqual(str(raised.exception),
            'Investigation projection is unavailable or changed')
        self.assertIsNone(raised.exception.__cause__)
        self.assertIsNone(policy._OPERATION.get())


if __name__ == '__main__':
    unittest.main(verbosity=2)
