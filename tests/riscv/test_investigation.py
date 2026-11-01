"""Focused projections of retained accepted evidence; no new native execution."""
import hashlib
import json
from pathlib import Path
import subprocess

import pytest
from veriforge.contracts.engineering import EvidenceReference
from veriforge.riscv.investigation import ProjectionEvidence, build_riscv_investigation

ROOT=Path(__file__).resolve().parents[2]

@pytest.fixture(autouse=True)
def no_tools(monkeypatch):
    def forbidden(*a,**k):pytest.fail('investigation projection attempted process execution')
    monkeypatch.setattr(subprocess,'run',forbidden);monkeypatch.setattr(subprocess,'Popen',forbidden)

@pytest.fixture(scope='module')
def retained():
    root=ROOT/'.artifacts/u4-riscv/ci-41202693632/riscv-evidence-c14525e3791569215ba385a1c5582d52d0b8858f-41202693632-1'
    if not root.is_dir():pytest.skip('accepted private four-case bundle not distributed in this checkout')
    from veriforge.system_investigation.native_tool_import import prepare_native_bundle
    admitted=prepare_native_bundle(family='riscv-41202693632',bundle_root=root,
        project_id='projection-riscv',revision_id='projection-riscv-v1').admitted
    blobs={item.reference.model_dump_json():item.data for item in admitted.artifacts}
    return {case.locator.case_label:case for case in admitted.native_report.cases},lambda ref:blobs.get(ref.model_dump_json())

def test_retained_mutant_exact_first_difference_and_register_state(retained):
    cases,resolve=retained;view=build_riscv_investigation(cases['mutant'],resolve,window=1)
    first=view['first_divergence']
    assert (first['order'],first['cycle'],first['expected'],first['observed'])==(1,14,0x8000fef0,0x8000fef1)
    row=view['retirements']['rows'][1]
    assert row['observed']['pc']==0x80000004 and row['observed']['rd_value']==0x8000fef1
    assert view['registers']['observed'][0]['value']==0
    assert view['registers']['observed'][31]['value'] is None
    assert view['execution']['new_execution']=='not_run'
    assert view['candidate'] is None
    assert '/tmp/' not in json.dumps(view)

def test_four_cases_kept_and_recompiled_distinct_from_saved(retained):
    cases,resolve=retained;original=cases['control']
    compiled=build_riscv_investigation(cases['control-replay'],resolve,original=original)
    saved=build_riscv_investigation(cases['control-saved-replay'],resolve,original=original)
    assert len(cases)==4 and saved['case_label']=='control-saved-replay'
    assert compiled['lineage']['status']=='exact_recompiled_replay'
    assert saved['lineage']['status']=='saved_program_bound'
    assert compiled['lineage']['mode']=='recompiled' and saved['lineage']['mode']=='saved_program'
    assert saved['program']['elf']['facts']['entry']==0x80000000
    assert saved['retirements']['total_reference']==saved['retirements']['total_observed']==17

def test_missing_bytes_stay_partial_and_missing_program_cannot_bind(retained):
    cases,resolve=retained;case=cases['control-saved-replay']
    missing=case.inputs.elf
    view=build_riscv_investigation(case,lambda ref:None if ref==missing else resolve(ref),original=cases['control'])
    assert view['projection_status']=='partial' and view['lineage']['status']=='program_bytes_missing'
    empty=build_riscv_investigation(cases['mutant'],lambda ref:None)
    assert empty['retirements']['rows']==[] and empty['projection_status']=='partial'
    assert empty['execution']['design_verdict']=='design_mismatch'  # retained case, not a new assertion

def test_swapped_original_or_corrupt_bytes_rejected(retained):
    cases,resolve=retained
    with pytest.raises(ValueError,match='same-origin control'):
        build_riscv_investigation(cases['control-saved-replay'],resolve,original=cases['mutant'])
    with pytest.raises(ValueError,match='hash differs'):
        build_riscv_investigation(cases['control'],lambda ref:b'corrupt')

def test_saved_original_identity_and_missing_run_proof(retained):
    cases,resolve=retained;original=cases['control'];saved=cases['control-saved-replay']
    altered=original.model_copy(update={'identity':original.identity.model_copy(update={'source_hash':'f'*64})})
    with pytest.raises(ValueError,match='saved-program lineage'):
        build_riscv_investigation(saved,resolve,original=altered)
    view=build_riscv_investigation(saved,lambda ref:None if ref==original.inputs.original_run else resolve(ref),original=original)
    assert view['lineage']['status']=='original_run_missing' and view['projection_status']=='partial'

@pytest.mark.parametrize('window',[-1,17,True])
def test_window_bound_is_strict(window):
    with pytest.raises(ValueError,match='window'):build_riscv_investigation(None,lambda ref:None,window=window)

def test_projection_cache_identity_and_strict_json_are_portable():
    raw=b'{"a":1,"a":2}'
    ref=EvidenceReference(artifact_id='artifact',revision_id='revision',sha256=hashlib.sha256(raw).hexdigest())
    evidence=ProjectionEvidence(lambda reference:raw)
    with pytest.raises(ValueError,match='duplicate'):evidence.record(ref,'test')
    with pytest.raises(ValueError,match='hash differs'):ProjectionEvidence(lambda reference:b'wrong').read(ref,'test')
