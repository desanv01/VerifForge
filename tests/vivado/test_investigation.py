"""Synthetic report fixtures only. Retained report integration is separately recorded."""
from dataclasses import asdict, replace
import json

import pytest

from veriforge.vivado import (
    VivadoProjectIdentity, VivadoExecution, investigate_vivado_reports, compare_vivado_candidate, vivado_projection,
)
from veriforge.vivado.readiness import EXERCISE3_HASHES, SourceIdentity
from veriforge.vivado.investigation import REPORTS, SYNTH_REPORTS


def project(**changes):
    value = VivadoProjectIdentity('xc7a200tsbg484-1', 'exercise3_nexys_video_top', 10.0,
        tuple(SourceIdentity(n, h, size) for (n, h), size in zip(EXERCISE3_HASHES.items(), (1546, 709, 2282))), 'a'*64)
    return replace(value, **changes)


def reports(phase='Routed', wns=7.074):
    header = ('| Tool Version : Vivado v.2026.1 (win64) Build 6511674\n'
              '| Design : exercise3_nexys_video_top\n| Device : 7a200t-sbg484\n'
              f'| Design State : {phase}\n')
    bodies = {'clocks':'sys_clk_pin 10.000 {0.000 5.000} P {clk}\n',
              'check_timing':'5. checking no_input_delay (12)\n6. checking no_output_delay (6)\n',
              'methodology':'| TIMING-18 | Warning | Missing input or output delay | 20 |\n',
              'timing_summary':f' WNS(ns) TNS(ns) Failing Total WHS(ns) THS(ns)\n -----\n {wns} 0 0 14 0.216 0 0 14 4.500 0 0 17\n',
              'route_status':'# of fully routed nets............. : 35\n# of nets with routing errors...... : 0\n',
              'drc':' Checks found: 0\n'}
    names = REPORTS if phase != 'Synthesized' else SYNTH_REPORTS
    return {n+'.rpt':(header+'| Command : '+('check_timing' if n=='check_timing' else 'report_'+n)+' -file '+n+'.rpt\n'+bodies.get(n, 'Synthetic report only\n')).encode() for n in names}


def inspect(data=None, identity=None, parent=None, status='completed'):
    return investigate_vivado_reports(identity or project(), VivadoExecution(status, 0 if status=='completed' else 1, 'b'*64),
                                     reports() if data is None else data, parent_investigation_sha256=parent)


def test_routed_facts_keep_missing_requirements_and_no_verdict():
    value = inspect()
    assert value.design_phase == 'routed' and value.complete_report_set
    assert dict(value.internal_timing)['wns_ns'] == 7.074
    assert value.tool_version == '2026.1+build.6511674'
    assert (value.missing_input_delays, value.missing_output_delays, value.methodology_missing_io) == (12,6,20)
    assert (value.fully_routed_nets, value.routing_errors, value.drc_reported_checks) == (35,0,0)
    assert {'MISSING_INPUT_TIMING_REQUIREMENTS','RAW_ASYNC_INPUTS_UNVALIDATED','RESET_LATENCY_AND_WORD_COHERENCE_UNSPECIFIED'} <= {f.code for f in value.findings}
    assert value.design_verdict == 'not_evaluated' and value.cdc_status == 'not_established'
    assert not value.new_execution and value.repair_status == 'not_validated'


def test_synthesis_routed_transition_is_characterization_not_repair():
    before = inspect(reports('Synthesized'))
    after = inspect(parent=before.investigation_sha256)
    result = compare_vivado_candidate(before, after)
    assert result.unchanged_requirements and result.status == 'not_comparable'
    assert 'design_phase_changed' in result.blockers
    assert result.repair_status == 'not_validated'


def test_same_phase_reported_improvement_with_lineage_and_all_rechecks():
    before = inspect(reports(wns=-0.25))
    after = inspect(parent=before.investigation_sha256)
    result = compare_vivado_candidate(before,after)
    assert result.status == 'reported_internal_timing_improvement'
    assert result.required_rechecks == REPORTS and not result.blockers
    assert result.functional_status == 'not_established' and result.repair_status == 'not_validated'


def test_missing_parent_changed_requirement_or_dropped_check_never_validates():
    before = inspect()
    assert 'candidate_parent_identity_missing_or_changed' in compare_vivado_candidate(before,inspect()).blockers
    after = inspect(identity=project(clock_period_ns=11),parent=before.investigation_sha256)
    assert 'requirements_changed' in compare_vivado_candidate(before,after).blockers
    missing = reports(); del missing['drc.rpt']
    after = inspect(missing,parent=before.investigation_sha256)
    assert 'report_set_incomplete' in compare_vivado_candidate(before,after).blockers


@pytest.mark.parametrize('kind',['phase','build','clock','top','command'])
def test_mixed_report_identity_never_combines_slack(kind):
    data = reports()
    target = 'clocks.rpt'
    if kind=='phase': data[target]=data[target].replace(b'Routed',b'Synthesized')
    elif kind=='build': data[target]=data[target].replace(b'6511674',b'6511675')
    elif kind=='clock': data[target]=data[target].replace(b'10.000',b'11.000')
    elif kind=='top': data[target]=data[target].replace(b'exercise3_nexys_video_top',b'other_design')
    else: data[target]=data[target].replace(b'report_clocks',b'report_cdc')
    value = inspect(data)
    assert not value.complete_report_set and not value.internal_timing


def test_resource_failure_is_not_design_failure_even_with_reports():
    value = inspect(status='resource_limited')
    assert value.execution.status == 'resource_limited' and value.design_verdict == 'not_evaluated'
    assert 'EXECUTION_NOT_COMPLETED' in {f.code for f in value.findings}
    assert inspect({}).design_phase == 'unknown'


def test_headerless_route_status_is_context_bound_not_its_own_phase_proof():
    data = reports(); data['route_status.rpt'] = b'Design Route Status\n# of fully routed nets......... : 35\n# of nets with routing errors...... : 0\n'
    record = inspect(data)
    assert record.complete_report_set and record.fully_routed_nets == 35
    fact = next(f for f in record.reports if f.name=='route_status')
    assert fact.availability == 'parsed_context_bound' and fact.design_phase is None
    assert not inspect({'route_status.rpt':data['route_status.rpt']}).complete_report_set


def test_safe_projection_rejects_paths_bounds_and_never_copies_raw_text():
    data = reports();data['cdc.rpt']+=b'host=C:/Users/Private/secret token=never-show\nVF_PART private-token 1\n'
    raw = json.dumps(asdict(inspect(data)))
    assert all(t not in raw for t in ('Private','never-show','private-token','C:/Users'))
    with pytest.raises(ValueError):inspect({'../../secret':b'x'})
    with pytest.raises(ValueError):inspect({'cdc.rpt':b'x'*1048577})
    with pytest.raises(ValueError):project(clock_period_ns=float('nan'))


def test_tampered_record_and_requirement_changes_reject():
    before = inspect(); after = inspect(parent=before.investigation_sha256)
    with pytest.raises(ValueError,match='identity changed'):
        compare_vivado_candidate(before,replace(after,missing_input_delays=0))
    changed = tuple(replace(s,sha256='c'*64) if s.name.endswith('.xdc') else s for s in project().sources)
    result = compare_vivado_candidate(before,inspect(identity=project(sources=changed),parent=before.investigation_sha256))
    assert not result.unchanged_requirements and 'nexys_video_exercise3.xdc' in result.changed_sources


def test_version_scoped_projection_binds_candidate_and_exposes_json_metrics():
    before = inspect(reports(wns=-0.1)); after = inspect(parent=before.investigation_sha256)
    comparison = compare_vivado_candidate(before, after)
    projection = vivado_projection(after, comparison)
    assert projection['schema_version'] == 'vivado.projection.v1'
    assert projection['investigation']['internal_timing']['wns_ns'] == 7.074
    assert len(projection['investigation']['parser_sha256']) == 64
    assert 'external_io_requirements' in projection['candidate_comparison']['external_validation_required']
    json.dumps(projection,allow_nan=False)
    with pytest.raises(ValueError,match='lineage'):
        vivado_projection(before,comparison)
