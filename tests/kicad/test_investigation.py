"""Focused production integration; synthetic facts/reports, no native execution."""
from dataclasses import replace
import json
import runpy
from pathlib import Path
import pytest
from veriforge.kicad import (bind_report, compare_configured_checks, ViewEvidence,
    index_views, investigate_candidate, investigate_mapping, parse_board, parse_netlist,
    reference_requirements, reference_support)
_helpers = runpy.run_path(str(Path(__file__).with_name('test_baseline.py')))
report, violation = _helpers['report'], _helpers['violation']

ROOT=Path(__file__).resolve().parents[2]

def bound(data, source='a'*64, config='b'*64):
    return bind_report(json.dumps(data).encode(), 'drc', source_sha256=source,
                       configuration_sha256=config,lineage='synthetic common CAD ancestry')

def test_comparison_tracks_resolved_introduced_and_exclusion_transitions():
    old=bound(report(violations=[violation()]))
    new=bound(report(violations=[violation(excluded=True)]),source='c'*64)
    result=compare_configured_checks(old,new)
    assert result['status']=='comparable_configured_observations'
    assert result['resolved'][0]['excluded'] is False
    assert result['introduced'][0]['excluded'] is True
    assert result['design_verdict']=='inconclusive'
    assert result['execution_status']=='not_observed'

@pytest.mark.parametrize('change',['ignore','config','severity','version'])
def test_changed_check_scope_does_not_claim_resolution(change):
    data=report()
    if change=='ignore': data['ignored_checks']=[{'key':'clearance','description':'ignored'}]
    if change=='severity': data['included_severities']=['error']
    if change=='version': data['kicad_version']='10.0.7'
    result=compare_configured_checks(bound(report(violations=[violation()])),
        bound(data,config='c'*64 if change=='config' else 'b'*64))
    assert result['status']=='inconclusive'
    assert result['resolved']==[]

def test_views_bind_source_and_do_not_dereference_paths():
    view=ViewEvidence('artifact_1','d'*64,'e'*64,'schematic','image/svg+xml','synthetic source')
    result=index_views((view,),source_sha256='f'*64)
    assert result['views'][0]['freshness']=='stale_source'
    assert 'schematic' in result['missing_views']
    with pytest.raises(ValueError): index_views((replace(view,artifact_id='../private.svg'),),source_sha256='f'*64)
    with pytest.raises(ValueError): index_views((view,view),source_sha256='f'*64)

def public_sources():
    path=ROOT/'targets/kicad/carrier/control_corrected_v1'
    # Public carrier source plus a path-sanitized XML test fixture; not a new candidate.
    xml=ROOT/'tests/kicad/fixtures/carrier-control.netlist.xml'
    return xml.read_bytes(),(path/'carrier.kicad_pcb').read_bytes()

def test_public_reference_connected_projection_preserves_unknown_identity():
    xml,pcb=public_sources()
    req=reference_requirements(datasheet_sha256='1'*64,intent_sha256='2'*64,reviewed_by='synthetic test reviewer')
    result=investigate_candidate(schematic_xml=xml,board_source=pcb,requirements=req)
    assert len(result['mapping']['rows'])==24
    assert result['mapping']['status']!='consistent_with_reviewed_mapping'
    assert result['missing_comparisons']==['drc','erc']
    assert result['design_verdict']=='inconclusive'
    assert reference_support()['fabrication_approved'] is False

def test_synthetic_exact_parts_match_then_pad_swap_fails_and_coverage_is_enforced():
    xml,pcb=public_sources(); sch=parse_netlist(xml); board=parse_board(pcb)
    req=reference_requirements(datasheet_sha256='1'*64,intent_sha256='2'*64,reviewed_by='synthetic reviewer')
    sch=replace(sch,components=tuple(replace(c,manufacturer='Samtec',mpn='TSW-106-07-G-D') for c in sch.components))
    board=replace(board,components=tuple(replace(c,manufacturer='Samtec',mpn='TSW-106-07-G-D') for c in board.components))
    assert investigate_mapping(sch,board,req)['status']=='consistent_with_reviewed_mapping'
    swapped=replace(board,endpoints=tuple(replace(p,net='JA7') if p.reference=='J1' and p.number=='1' else p for p in board.endpoints))
    assert investigate_mapping(sch,swapped,req)['status']=='mismatch'
    assert investigate_mapping(sch,board,req[:-1])['unexpected_symbol_pins']==[('J2','12')]
    with pytest.raises(ValueError): investigate_mapping(sch,board,req+req[:1])

def test_candidate_report_wrong_source_is_not_attached():
    xml,pcb=public_sources()
    with pytest.raises(ValueError,match='source binding'):
        investigate_candidate(schematic_xml=xml,board_source=pcb,
            requirements=reference_requirements(datasheet_sha256='1'*64,intent_sha256='2'*64,reviewed_by='test'),
            candidate_reports=(bound(report()),))
