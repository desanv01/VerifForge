"""Read-only projections of admitted native cases; never an admission or executor.

The host supplies an already admitted typed case and a version-scoped byte
resolver. Missing bytes remain explicit; supplied bytes must match their ref.
"""
from collections.abc import Callable
from dataclasses import asdict
import hashlib
import json

from veriforge.contracts.engineering import EvidenceReference
from veriforge.contracts.native_tools import RiscvRecordedAttempt
from .checker import compare_retirements
from .contracts import Retirement, SimulationOutput
from .profile import PLATFORM
from .trace import normalize_rvfi, parse_spike_commits

Resolver = Callable[[EvidenceReference], bytes | None]
MAX_OUTPUT = 512 * 1024


class ProjectionEvidence:
    """One bounded, hash-checked cache shared by family projection functions."""
    def __init__(self, resolve: Resolver):
        self.resolve, self.cache, self.evidence, self.gaps = resolve, {}, [], []
        self.total = 0

    def read(self, ref, role):
        ref = EvidenceReference.model_validate(ref)
        key = ref.model_dump_json()
        if key in self.cache:
            return self.cache[key]
        if len(self.cache) >= 64:
            raise ValueError('projection artifact count exceeds 64')
        data = self.resolve(ref)
        if data is not None:
            if type(data) is not bytes or len(data) > 16 * 1024 * 1024:
                raise ValueError('projection artifact must be bounded bytes')
            if hashlib.sha256(data).hexdigest() != ref.sha256:
                raise ValueError('projection artifact hash differs from admitted reference')
            self.total += len(data)
            if self.total > 32 * 1024 * 1024:
                raise ValueError('projection byte budget exceeds 32 MiB')
        self.cache[key] = data
        self.evidence.append(dict(role=role, reference=ref.model_dump(mode='json'),
            size_bytes=len(data) if data is not None else None, status='available' if data is not None else 'missing'))
        if data is None:
            self.gaps.append(dict(code='missing_artifact', role=role, reference=ref.model_dump(mode='json')))
        return data

    def record(self, ref, role):
        data = self.read(ref, role)
        if data is None:
            return None
        def pairs(items):
            out = {}
            for key, value in items:
                if key in out:
                    raise ValueError('duplicate projection JSON key')
                out[key] = value
            return out
        def invalid(_):
            raise ValueError('nonfinite projection JSON')
        return json.loads(data, object_pairs_hook=pairs, parse_constant=invalid)


def bounded_projection(value):
    if len(json.dumps(value, allow_nan=False, separators=(',', ':')).encode()) > MAX_OUTPUT:
        raise ValueError('investigation projection exceeds 512 KiB')
    return value


def _registers(events, order):
    values = [dict(register=i, value=0 if i == 0 else None, last_order=None) for i in range(32)]
    for event in events[:order + 1]:
        if event.rd and not event.trap:
            values[event.rd] = dict(register=event.rd, value=event.rd_value, last_order=event.order)
    return values


def _retirements(pair):
    if type(pair) is not dict or set(pair) != {'schema_version', 'reference', 'observed'} or pair['schema_version'] != 'riscv.retirement-pair.v1':
        raise ValueError('unsupported normalized retirement pair')
    sides = []
    for name in ('reference', 'observed'):
        rows = pair[name]
        if type(rows) is not list or len(rows) > 10000:
            raise ValueError('retirement list exceeds supported bound')
        events = tuple(Retirement.model_validate(row) for row in rows)
        if any(event.order != i for i, event in enumerate(events)):
            raise ValueError('retirement orders are not contiguous')
        sides.append(events)
    return sides


def build_riscv_investigation(case: RiscvRecordedAttempt, resolve: Resolver, *, window=5,
                              original=None, saved_program_ref=None):
    """Project an admitted case; optional original/proof are admitted references too.

    Saved-program evidence is required to claim executable reuse. Equal program
    hashes by themselves establish only byte equality, not execution/replay.
    """
    if type(window) is not int or not 0 <= window <= 16:
        raise ValueError('retirement window must be 0..16')
    if not isinstance(case,RiscvRecordedAttempt) or case.schema_version not in ('riscv.recorded-attempt.v1', 'riscv.recorded-attempt.v2'):
        raise ValueError('typed RISC-V recorded attempt required')
    evidence = ProjectionEvidence(resolve)
    pair = evidence.record(case.inputs.normalized_pair, 'retirements')
    stored = evidence.record(case.inputs.comparison, 'comparison')
    environment = evidence.record(case.inputs.environment, 'environment')
    profile = evidence.record(case.inputs.profile, 'profile')
    evidence.record(case.inputs.source_manifest, 'source_manifest')
    evidence.record(case.inputs.checker_manifest, 'checker_manifest')
    if profile is not None and profile != asdict(PLATFORM):
        raise ValueError('recorded RISC-V platform differs from supported assumptions')
    expected, observed = (), ()
    checked = None
    if pair is not None:
        expected, observed = _retirements(pair)
        checked = compare_retirements(expected, observed)
        if len(expected) != case.reference_count or len(observed) != case.retired_count or checked.compared != case.compared_count:
            raise ValueError('retirement counts differ from admitted case')
        if stored is not None and checked.model_dump(mode='json') != stored:
            raise ValueError('stored comparison differs from immutable checker')
        if case.verdict in ('pass_within_scope', 'design_mismatch') and checked.verdict != case.verdict:
            raise ValueError('design verdict differs from admitted case')
    # Reuse the existing independent normalizers when raw evidence is available.
    raw_dut = evidence.record(case.inputs.raw_rvfi, 'raw_rvfi')
    raw_reference = evidence.read(case.inputs.raw_spike, 'raw_reference')
    if pair is not None and raw_dut is not None:
        output = SimulationOutput.model_validate(raw_dut)
        if tuple(normalize_rvfi(output.events)) != observed:
            raise ValueError('raw DUT trace differs from normalized retirements')
    if pair is not None and raw_reference is not None:
        if tuple(parse_spike_commits(raw_reference.decode('utf-8'))) != expected:
            raise ValueError('raw reference differs from normalized retirements')
    divergence = case.first_divergence.model_dump(mode='json') if case.first_divergence else None
    if checked is not None:
        difference = checked.first_divergence
        if (difference is None) != (divergence is None):
            raise ValueError('first divergence presence differs')
        if difference and (difference.order != divergence['order'] or difference.field != divergence['field'] or
                json.loads(difference.expected) != divergence['expected'] or json.loads(difference.observed) != divergence['observed'] or
                observed[difference.order].source_cycle != divergence['cycle']):
            raise ValueError('first divergence differs from admitted location/value')
    center = divergence['order'] if divergence else 0
    start, stop = max(0, center - window), min(max(len(expected), len(observed)), center + window + 1)
    rows = [dict(order=i, reference=expected[i].model_dump(mode='json') if i < len(expected) else None,
                 observed=observed[i].model_dump(mode='json') if i < len(observed) else None) for i in range(start, stop)]
    program = {}
    for name in ('object', 'elf', 'binary'):
        ref = getattr(case.inputs, name)
        data = evidence.read(ref, 'program_' + name)
        program[name] = dict(reference=ref.model_dump(mode='json'), available=data is not None)
        if name == 'elf' and data is not None:
            from veriforge.firmware.extract import extract_elf
            elf = extract_elf(data, ref.artifact_id)
            program[name]['facts'] = elf.model_dump(mode='json')
            program[name]['retirement_links'] = [dict(order=row['order'],
                segments=[i for i, segment in enumerate(elf.load_segments) if
                    segment.virtual_address <= row['observed']['pc'] < segment.virtual_address + segment.file_size])
                for row in rows if row['observed'] is not None]
    compiled = {'assembler', 'compiler'}.issubset(case.producer_phases)
    lineage = dict(mode='saved_program' if environment and environment.get('program_mode') == 'saved_program_no_compilation'
        else 'recompiled' if compiled and case.locator.case_label == 'control-replay' else 'fresh_compilation' if compiled else 'unknown',
        status='not_supplied', original=None, program_equal=None, proof=None)
    if original is not None:
        if original.locator.case_label != 'control' or original.locator.origin != case.locator.origin:
            raise ValueError('replay original must be the same-origin control')
        equal = all(getattr(case.inputs, name).sha256 == getattr(original.inputs, name).sha256 for name in program)
        lineage.update(original=original.locator.model_dump(mode='json'), program_equal=equal, status='program_bytes_equal' if equal else 'program_bytes_differ')
        if lineage['mode'] == 'recompiled':
            exact = case.identity == original.identity and all(getattr(case.inputs,name).sha256 == getattr(original.inputs,name).sha256
                for name in ('raw_rvfi','raw_spike','normalized_pair','comparison','environment'))
            lineage['status'] = 'exact_recompiled_replay' if exact and equal else 'recompiled_evidence_differs'
    typed_saved_ref = getattr(case.inputs, 'saved_program', None)
    if saved_program_ref is not None and typed_saved_ref is not None and saved_program_ref != typed_saved_ref:
        raise ValueError('saved proof differs from admitted input')
    saved_program_ref = typed_saved_ref or saved_program_ref
    if original is not None and getattr(case, 'replay_of', None) is not None and case.replay_of != original.locator:
        raise ValueError('saved original differs from admitted replay locator')
    if saved_program_ref is not None:
        proof = evidence.record(saved_program_ref, 'saved_program_proof')
        lineage['proof'] = saved_program_ref.model_dump(mode='json')
        if proof is not None and original is not None and environment is not None:
            original_result = evidence.read(original.locator.raw_result_ref, 'original_result')
            hashes = {name: getattr(case.inputs, field).sha256 for name, field in
                      (('smoke.o', 'object'), ('smoke.elf', 'elf'), ('smoke.bin', 'binary'))}
            if proof.get('schema_version') != 'riscv.saved-program.v1' or proof.get('program_hashes') != hashes or not lineage['program_equal'] or \
                    proof.get('original_attempt_id') != original.locator.native_attempt_id or \
                    proof.get('original_result_sha256') != original.locator.raw_result_ref.sha256 or \
                    proof.get('original_experiment_id') != original.locator.wire_experiment_id or \
                    proof.get('original_identity') != original.identity.model_dump(mode='json') or \
                    any(getattr(case.identity,name) != getattr(original.identity,name) for name in
                        ('source_hash','checker_hash','contract_hash','experiment_hash')) or \
                    environment.get('saved_program_sha256') != saved_program_ref.sha256 or \
                    lineage['mode'] != 'saved_program' or environment.get('assembly_command') != [] or environment.get('compiler_command') != []:
                raise ValueError('saved-program lineage does not bind original/program/environment')
            origin_ref = getattr(original.inputs, 'original_run', None)
            origin_available=True
            if origin_ref is not None:
                origin = evidence.record(origin_ref, 'original_run')
                origin_available=origin is not None
                if origin is not None and proof.get('original_run_identity') != origin:
                    raise ValueError('saved original run identity differs')
            lineage['status'] = 'saved_program_bound' if original_result is not None else 'original_bytes_missing'
            if not origin_available:
                lineage['status']='original_run_missing'
            if not all(item['available'] for item in program.values()):
                lineage['status'] = 'program_bytes_missing'
    if lineage['mode'] == 'saved_program' and lineage['status'] != 'saved_program_bound':
        evidence.gaps.append(dict(code='saved_program_lineage_incomplete', role='saved_program_proof', reference=None))
    return bounded_projection(dict(schema_version='riscv.investigation.v1', domain='riscv',
        case_label=case.locator.case_label, locator=case.locator.model_dump(mode='json'),
        identity=case.identity.model_dump(mode='json'), checker=case.checker.model_dump(mode='json'),
        execution=dict(new_execution='not_run', producer_status=case.producer_execution_status,
                       termination=case.termination, design_verdict=case.verdict, checker_status=case.checker_evidence_status),
        assumptions=dict(platform=profile, profile_reference=case.inputs.profile.model_dump(mode='json'), hart=0, privilege='machine', compressed=False, mmio=False,
            reset_vector=PLATFORM.entry, reset='external active-low resetn; entry checked from retirement; reset timing not inferred',
            initial_registers='unknown_except_x0', cycle_relation='DUT diagnostic cycles; no reference cycle comparison',
            scope='bounded RAM smoke; not ISA certification'),
        first_divergence=divergence, retirements=dict(start_order=start, end_order=stop,
            total_reference=case.reference_count, total_observed=case.retired_count, rows=rows),
        registers=dict(at_order=center, timing='after_selected_retirement', reference=_registers(expected,center), observed=_registers(observed,center)),
        program=program, lineage=lineage, candidate=None,
        limitations=['No RISC-V repair candidate/diff/validation is recorded; the seeded mutant is not a repair.'],
        projection_status='partial' if evidence.gaps else 'complete',
        evidence=evidence.evidence, gaps=evidence.gaps))
