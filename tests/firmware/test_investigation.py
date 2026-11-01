"""Connected projections use retained checked bytes; no compiler/core execution."""
import json
from pathlib import Path
import subprocess

import pytest
from veriforge.firmware.investigation import build_firmware_investigation

ROOT=Path(__file__).resolve().parents[2]

@pytest.fixture(autouse=True)
def no_tools(monkeypatch):
    def forbidden(*a,**k):pytest.fail('projection attempted a native process')
    monkeypatch.setattr(subprocess,'run',forbidden);monkeypatch.setattr(subprocess,'Popen',forbidden)

@pytest.fixture(scope='module')
def retained():
    root=ROOT/'.artifacts/u5-firmware/ci-40929665294/firmware-evidence-5565985bbe2bb2a0be869d366baa48c715057657-40929665294-1'
    if not root.is_dir():pytest.skip('accepted private MMIO bundle not distributed in this checkout')
    from veriforge.system_investigation.native_tool_import import prepare_native_bundle
    admitted=prepare_native_bundle(family='firmware-40929665294',bundle_root=root,
        project_id='projection-firmware',revision_id='projection-firmware-v1').admitted
    blobs={item.reference.model_dump_json():item.data for item in admitted.artifacts}
    return admitted.native_report,lambda ref:blobs.get(ref.model_dump_json())

def test_bad_exact_connected_header_rtl_bus_retirement(retained):
    report,resolve=retained;case=next(row for row in report.cases if row.locator.case_label=='bad')
    view=build_firmware_investigation(case,resolve,report=report)
    conflict=view['first_conflict'];assert (conflict['mmio_index'],conflict['bus_sequence'],conflict['cycle'])==(2,31,95)
    status=next(row for row in view['registers'] if row['name']=='status')
    assert status['header']['value']==0x40000008 and status['rtl']['value']==status['expected_address']==0x40000004
    assert status['address_status']=='mismatch'
    bus=view['mmio'][2]
    assert bus['expected_register']=='status' and bus['observed_register']=='data'
    assert bus['pc'] is not None and bus['retirement_order'] is not None
    assert bus['instruction_link']=='corroborated'
    assert view['elf']['layout']['verdict']=='layout_agrees'
    assert '/tmp/' not in json.dumps(view)

def test_curated_candidate_protected_validation_and_saved_replay(retained):
    report,resolve=retained;views={row.locator.case_label:build_firmware_investigation(row,resolve,report=report) for row in report.cases}
    candidate=views['candidate']['candidate']
    assert candidate['origin']=='curated_header_correction' and candidate['validation_status']=='recorded_scoped_validation'
    assert candidate['bad_verdict']=='design_mismatch' and candidate['candidate_verdict']=='pass_within_scope'
    assert candidate['checker_hash']==views['bad']['identity']['checker_hash']
    assert candidate['contract_hash']==views['candidate']['identity']['contract_hash']
    assert 'STATUS_ADDR' in candidate['diff']['text']
    assert len(views['candidate']['mmio'])==9
    assert views['candidate-replay']['lineage']['status']=='recorded_saved_program_replay'
    assert views['bad-replay']['first_conflict']==views['bad']['first_conflict']

def test_missing_or_tampered_inputs_never_become_success(retained):
    report,resolve=retained;case=report.cases[0]
    view=build_firmware_investigation(case,lambda ref:None)
    assert view['projection_status']=='partial' and view['elf']['facts'] is None
    assert view['mmio']==[] and view['candidate'] is None and view['assumptions']['profile'] is None
    assert view['execution']['new_execution']=='not_run'
    with pytest.raises(ValueError,match='hash differs'):build_firmware_investigation(case,lambda ref:b'wrong')

def test_conflict_cannot_point_to_another_bus_transaction(retained):
    report,resolve=retained;bad=next(row for row in report.cases if row.locator.case_label=='bad')
    altered=bad.model_copy(update={'first_conflict':bad.first_conflict.model_copy(update={'bus_sequence':32})})
    with pytest.raises(ValueError,match='observed location'):build_firmware_investigation(altered,resolve)

@pytest.mark.parametrize('window',[-1,17,True])
def test_bad_window_rejected_before_resolution(retained,window):
    report,_=retained
    with pytest.raises(ValueError,match='window'):build_firmware_investigation(report.cases[0],lambda ref:None,window=window)
