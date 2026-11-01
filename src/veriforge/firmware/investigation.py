"""Connected, bounded views over admitted firmware evidence, never tool execution."""
from veriforge.contracts import canonical_hash
from veriforge.contracts.native_tools import FirmwareRecordedCase, FirmwareRecordedReport
from veriforge.riscv.investigation import ProjectionEvidence, Resolver, bounded_projection
from .contracts import PlatformProfile, RegisterContract
from .executable import FirmwarePreparation, extract_native_decode
from .extract import extract_elf, extract_disassembly, extract_header, corroborate_instruction_bytes
from .harness import EXPECTED, HarnessOutput, check_observation, lane_mask
from .workflow import check_elf_layout


def _source(ref, value=None, location=None):
    return dict(reference=ref.model_dump(mode='json'), value=value, location=location)


def _candidate(case, report, evidence):
    if report is None:
        return None
    if not isinstance(report, FirmwareRecordedReport) or not any(row == case for row in report.cases):
        raise ValueError('candidate report does not contain admitted case')
    candidate = report.candidate
    bad = next(row for row in report.cases if row.locator == candidate.bad)
    fixed = next(row for row in report.cases if row.locator == candidate.candidate)
    # Typed admission established this relation; never associate by filename/title.
    if candidate.protected_checker_hash != bad.identity.checker_hash or candidate.protected_checker_hash != fixed.identity.checker_hash or \
            candidate.protected_contract_hash != bad.identity.contract_hash or candidate.protected_contract_hash != fixed.identity.contract_hash or \
            candidate.protected_profile_hash != bad.profile_hash or candidate.protected_profile_hash != fixed.profile_hash:
        raise ValueError('candidate changed protected checker/contract/profile')
    diff = evidence.read(candidate.diff_ref, 'candidate_diff')
    original = evidence.read(candidate.original_bad_header_ref, 'candidate_base_header')
    selected = evidence.read(candidate.candidate_header_ref, 'candidate_header')
    text = diff.decode('utf-8') if diff is not None and len(diff) <= 16384 else None
    return dict(origin=candidate.kind, outcome=candidate.outcome,
        validation_status='recorded_scoped_validation' if candidate.outcome == 'scoped_curated_correction' else 'inconclusive',
        original=bad.locator.model_dump(mode='json'), repeated=fixed.locator.model_dump(mode='json'),
        source_hash=case.identity.source_hash, checker_hash=candidate.protected_checker_hash,
        contract_hash=candidate.protected_contract_hash, profile_hash=candidate.protected_profile_hash,
        bad_verdict=bad.verdict, candidate_verdict=fixed.verdict,
        covered_mmio_indices=list(candidate.covered_mmio_indices),
        diff=dict(reference=candidate.diff_ref.model_dump(mode='json'), text=text,
                  truncated=diff is not None and len(diff) > 16384),
        base_header=_source(candidate.original_bad_header_ref), candidate_header=_source(candidate.candidate_header_ref),
        source_bytes_available=original is not None and selected is not None,
        limitations=['Curated header restoration; not a model-generated repair or general ISA/peripheral qualification.'])


def build_firmware_investigation(case: FirmwareRecordedCase, resolve: Resolver, *, report=None, window=5):
    """Join exact admitted ELF/instruction/header/decode/MMIO identities.

    resolve is supplied by the version owner. No path or artifact identifier is
    used as authority, and no artifact source code is imported or executed.
    """
    if not isinstance(case, FirmwareRecordedCase):
        raise ValueError('typed firmware recorded case required')
    if type(window) is not int or not 0 <= window <= 16:
        raise ValueError('retirement window must be 0..16')
    evidence = ProjectionEvidence(resolve)
    preparation = evidence.record(case.inputs.preparation, 'preparation')
    profile = PlatformProfile.model_validate(preparation['profile']) if preparation else None
    if profile is not None and canonical_hash(profile) != case.profile_hash:
        raise ValueError('platform assumptions differ from admitted profile')
    contract_raw = evidence.record(case.inputs.register_contract, 'register_contract')
    contract = RegisterContract.model_validate(contract_raw) if contract_raw is not None else None
    elf_bytes = evidence.read(case.inputs.program.elf, 'elf')
    elf = extract_elf(elf_bytes, case.inputs.program.elf.artifact_id) if elf_bytes is not None else None
    disassembly_bytes = evidence.read(case.inputs.program.disassembly, 'disassembly')
    instructions = extract_disassembly(disassembly_bytes, case.inputs.program.disassembly.artifact_id) if disassembly_bytes is not None else ()
    corroborated = False
    if elf is not None and instructions:
        corroborated = corroborate_instruction_bytes(elf_bytes, elf, instructions)
    header_bytes = evidence.read(case.inputs.selected_header, 'selected_header')
    peripheral_bytes = evidence.read(case.inputs.sources.peripheral, 'rtl_decode')
    header = extract_header(header_bytes, case.inputs.selected_header.artifact_id,
        tuple(row.header_macro for row in contract.registers)) if header_bytes is not None and contract is not None else ()
    rtl = extract_native_decode(peripheral_bytes, case.inputs.sources.peripheral.artifact_id) if peripheral_bytes is not None else ()
    header_by_name, rtl_by_name = {row.name: row for row in header}, {row.name: row for row in rtl}
    observation_raw = evidence.record(case.inputs.observation, 'observation')
    check_raw = evidence.record(case.inputs.check, 'check')
    observation = HarnessOutput.model_validate(observation_raw) if observation_raw is not None else None
    checked = None
    if observation is not None:
        if observation.cycles != case.cycles or len(observation.events) != case.retired_count or len(observation.transfers) != case.transfer_count:
            raise ValueError('observation counts differ from admitted firmware case')
        if preparation is not None:
            checked = check_observation(observation, FirmwarePreparation.model_validate(preparation['preparation']))
            if checked.execution_status == 'invalid_evidence':
                raise ValueError('invalid MMIO/retirement evidence')
            if checked.verdict != case.verdict or checked.execution_status != case.checker_evidence_status:
                raise ValueError('MMIO checker differs from admitted case')
            if check_raw is not None and check_raw.get('check') != checked.model_dump(mode='json'):
                raise ValueError('saved firmware check differs from immutable checker')
    # Pair the checked ordered data transfers with memory-retiring RVFI events.
    # Instruction fetches do not consume a data retirement. This is not a PC guess.
    transfers, mmio, retirement_by_bus = [], [], {}
    if observation is not None:
        transfers = [row for row in observation.transfers if not row.instruction]
        events = [row for row in observation.events if not row.rvfi_trap and (row.rvfi_mem_rmask or row.rvfi_mem_wmask)]
        if checked is not None:
            retirement_by_bus = {bus.sequence: event for bus, event in zip(transfers, events)}
        mmio = [row for row in transfers if 0x40000000 <= row.address < 0x40000010]
        if len(mmio) != case.mmio_count:
            raise ValueError('MMIO count differs from admitted case')
    by_address = {row.address: row for row in instructions}
    registers, transactions = [], []
    by_register = {row.address: row.logical_name for row in contract.registers} if contract else {}
    for index, bus in enumerate(mmio):
        expected = EXPECTED[index] if index < len(EXPECTED) else None
        event = retirement_by_bus.get(bus.sequence)
        instruction = by_address.get(event.rvfi_pc_rdata) if event else None
        instruction_matches = bool(instruction and instruction.encoding == event.rvfi_insn)
        transactions.append(dict(index=index, bus_sequence=bus.sequence, cycle=bus.cycle,
            operation='write' if bus.write_strobes else 'read', address=bus.address,
            data=bus.write_data & lane_mask(bus.write_strobes) if bus.write_strobes else bus.read_data,
            write_strobes=bus.write_strobes, expected_address=expected[0] if expected else None,
            expected_register=by_register.get(expected[0]) if expected else None, observed_register=by_register.get(bus.address),
            retirement_order=event.rvfi_order if event else None, pc=event.rvfi_pc_rdata if event else None,
            instruction=instruction.model_dump(mode='json') if instruction_matches and corroborated else None,
            instruction_link='corroborated' if instruction_matches and corroborated else 'unavailable',
            reference=case.inputs.observation.model_dump(mode='json')))
    if contract is not None:
        for binding in contract.registers:
            h, r = header_by_name.get(binding.header_macro), rtl_by_name.get(binding.rtl_signal)
            registers.append(dict(name=binding.logical_name, expected_address=binding.address,
                width_bits=binding.width_bits, access=binding.access, reset_value=binding.reset_value,
                byte_strobes=binding.byte_strobes, side_effects=binding.side_effects,
                header=_source(case.inputs.selected_header,h.value if h else None,h.provenance.location if h else None),
                rtl=_source(case.inputs.sources.peripheral,r.value if r else None,r.provenance.location if r else None),
                mmio_indices=[row['index'] for row in transactions if row['expected_register'] == binding.logical_name],
                address_status='missing' if h is None or r is None else 'agrees' if h.value == r.value == binding.address else 'mismatch'))
    conflict = case.first_conflict.model_dump(mode='json') if case.first_conflict else None
    if conflict is not None and observation is not None:
        index=conflict['mmio_index']
        if index>=len(mmio) or (mmio[index].sequence,mmio[index].cycle,mmio[index].address) != \
                (conflict['bus_sequence'],conflict['cycle'],conflict['observed_address']) or \
                checked is not None and checked.first_mmio_sequence != index:
            raise ValueError('first MMIO conflict differs from admitted observed location')
    center = next((row['retirement_order'] for row in transactions if conflict and row['index'] == conflict['mmio_index']),0) or 0
    events = observation.events if observation else []
    start, stop = max(0,center-window), min(len(events),center+window+1)
    rows = [dict(order=event.rvfi_order, pc=event.rvfi_pc_rdata, instruction=event.rvfi_insn,
        cycle=event.cycle, rd=event.rvfi_rd_addr, rd_value=event.rvfi_rd_wdata if event.rvfi_rd_addr else 0)
        for event in events[start:stop]]
    candidate = _candidate(case, report, evidence)
    if candidate is None:
        evidence.gaps.append(dict(code='candidate_report_not_supplied',role='candidate_validation',reference=None))
    replay = dict(original_execution=case.replay_of_execution_ref.model_dump(mode='json') if case.replay_of_execution_ref else None,
                  status='not_a_replay' if case.replay_of_execution_ref is None else 'recorded_saved_program_replay')
    if case.replay_of_execution_ref is not None:
        if evidence.record(case.replay_of_execution_ref,'original_execution') is None:
            replay['status']='original_bytes_missing'
    return bounded_projection(dict(schema_version='firmware.investigation.v1',domain='firmware',
        case_label=case.locator.case_label,locator=case.locator.model_dump(mode='json'),
        identity=case.identity.model_dump(mode='json'),checker=case.checker.model_dump(mode='json'),
        execution=dict(new_execution='not_run',producer_status='completed' if case.producer_completed else 'incomplete',
            termination=case.termination,design_verdict=case.verdict,checker_status=case.checker_evidence_status),
        assumptions=dict(profile=profile.model_dump(mode='json') if profile else None,
            profile_hash=case.profile_hash,register_contract_hash=canonical_hash(contract) if contract else None,
            reset='reset vector and register reset values are declared platform/contract assumptions',
            reference_support=case.reference_support,scope='fixed native MMIO sequence, byte lanes and retired mailbox; not ISA certification'),
        elf=dict(reference=case.inputs.program.elf.model_dump(mode='json'),facts=elf.model_dump(mode='json') if elf else None,
            layout=check_elf_layout(elf,profile).model_dump(mode='json') if elf and profile else None),
        disassembly=dict(reference=case.inputs.program.disassembly.model_dump(mode='json'),
            corroborated=corroborated,total=len(instructions),rows=[row.model_dump(mode='json') for row in instructions[:64]],
            truncated=len(instructions)>64),
        registers=registers,mmio=transactions,first_conflict=conflict,
        retirements=dict(start_order=start,end_order=stop,rows=rows,total_observed=case.retired_count),
        candidate=candidate,lineage=replay,projection_status='partial' if evidence.gaps else 'complete',
        evidence=evidence.evidence,gaps=evidence.gaps))
