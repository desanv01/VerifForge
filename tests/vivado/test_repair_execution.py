"""Real temporary source snapshots, synthetic reports and fake tool boundary only."""
from dataclasses import replace
from hashlib import sha256
import json
from pathlib import Path

import pytest

from veriforge.vivado.investigation import (
    IMPLEMENTATION_SHA256, SYNTH_REPORTS, VivadoProjectIdentity, VivadoExecution, investigate_vivado_reports,
)
from veriforge.vivado.readiness import SourceIdentity
from veriforge.vivado.repair_execution import (
    FixedToolOutput, PrivateFixedBatchAdapter, RepairHooks, RepairPins, PROFILES, prepare_repair, execute_repair,
)
from veriforge.vivado import repair_execution


@pytest.fixture(autouse=True)
def synthetic_xdc_admission_for_fake_tool_tests(monkeypatch):
    # Test-only admission identity. Production admits only the actual frozen XDC;
    # no native tool consumes these synthetic files.
    monkeypatch.setattr(repair_execution,'APPROVED_XDC_SHA256',sha256(b'synthetic nexys_video_exercise3.xdc').hexdigest())


def report_bytes(missing=0, wns=1):
    bodies={'clocks':'sys_clk_pin 10.000 {0 5}\n',
            'check_timing':f'checking no_input_delay ({missing})\nchecking no_output_delay ({missing})\n',
            'methodology':f'| TIMING-18 | Warning | Missing IO | {missing} |\n',
            'timing_summary':f' WNS(ns) TNS(ns)\n -----\n {wns} 0 0 14 0.2 0 0 14 4.5 0 0 17\n'}
    return {n+'.rpt':('| Tool Version : Vivado v.2026.1 Build 6511674\n| Design : exercise3_nexys_video_top\n'
        '| Device : xc7a200tsbg484-1\n| Design State : Synthesized\n| Command : '+
        ('check_timing'if n=='check_timing'else'report_'+n)+'\n'+bodies.get(n,'Synthetic report\n')).encode()for n in SYNTH_REPORTS}


class FakeTool:
    kind='fake_tool_boundary_not_native_execution'
    def __init__(self,reason=None,settled=True):self.calls=0;self.reason=reason;self.settled=settled
    def execute(self,prepared,hooks,deadline):
        self.calls+=1
        data={'success':self.reason is None,'exit_code':0 if self.reason is None else 1,'limit_reason':self.reason,
              'cleanup':{'active_owned_processes_after_cleanup':0 if self.settled else None,
                         'job_handle_closed':True,'reader_settled':True,'pipe_closed':True,'errors':[]}}
        return FixedToolOutput(json.dumps(data).encode(),report_bytes())


def setup(tmp_path, missing=0, io=True):
    original=tmp_path/'source';candidate=tmp_path/'proposal';original.mkdir();candidate.mkdir()
    names=('exercise3_core.v','exercise3_nexys_video_top.v','nexys_video_exercise3.xdc')
    originals=[];candidates=[]
    for name in names:
        old=('synthetic '+name).encode();new=old+(b' candidate'if name.endswith('core.v')else b'')
        (original/name).write_bytes(old);(candidate/name).write_bytes(new)
        originals.append(SourceIdentity(name,sha256(old).hexdigest(),len(old)))
        candidates.append(SourceIdentity(name,sha256(new).hexdigest(),len(new)))
    identity=VivadoProjectIdentity('xc7a200tsbg484-1','exercise3_nexys_video_top',10,tuple(originals),PROFILES['synthesis'][2])
    baseline=investigate_vivado_reports(identity,VivadoExecution('completed',0,'b'*64),report_bytes(missing,wns=-0.1))
    pins=RepairPins('synthesis',tuple(originals),tuple(candidates),'c'*64 if io else None,IMPLEMENTATION_SHA256)
    prepared=prepare_repair(original,candidate,tmp_path/'execution',baseline,pins,project_root=tmp_path)
    hooks=RepairHooks(lambda:True,lambda:True,lambda:False,lambda:2**31,lambda:0)
    return prepared,baseline,hooks


@pytest.mark.parametrize('missing,io',[(12,True),(0,False)])
def test_missing_io_is_preflight_blocker_without_tool_or_memory_probe(tmp_path,missing,io):
    prepared,baseline,hooks=setup(tmp_path,missing=missing,io=io)
    hooks=replace(hooks,available_physical_bytes=lambda:pytest.fail('memory probe before requirements clear'))
    tool=FakeTool();result=execute_repair(prepared,baseline,tool,hooks)
    assert result['blocker']=='missing_io_requirements'and tool.calls==0
    assert result['repair_status']=='not_validated'
    assert (prepared.directory/'repair-execution.json').exists()


def test_complete_fake_boundary_preserves_snapshots_and_feeds_comparison(tmp_path):
    prepared,baseline,hooks=setup(tmp_path)
    before={p:p.read_bytes()for label in ('original','candidate')for p in(prepared.directory/label).iterdir()}
    tool=FakeTool();result=execute_repair(prepared,baseline,tool,hooks)
    assert result['execution_status']=='completed'and tool.calls==1
    assert result['runner_kind']=='fake_tool_boundary_not_native_execution'
    assert result['candidate_projection']['candidate_comparison']['status']=='reported_internal_timing_improvement'
    assert result['repair_status']=='not_validated'
    assert all(p.read_bytes()==data for p,data in before.items())
    raw=(prepared.directory/'native-receipt.json').read_bytes()
    assert sha256(raw).hexdigest()==result['native_receipt_sha256']
    with pytest.raises(ValueError):execute_repair(prepared,baseline,tool,hooks)
    assert tool.calls==1


@pytest.mark.parametrize('gate',('control','reservation','cancel','memory','deadline'))
def test_resource_control_and_deadline_hooks_fail_closed(tmp_path,gate):
    prepared,baseline,hooks=setup(tmp_path)
    changes={'control':{'control_running':lambda:False},'reservation':{'reservation_owned':lambda:False},
             'cancel':{'cancelled':lambda:True},'memory':{'available_physical_bytes':lambda:2**31-1}}
    if gate=='deadline':
        clock=iter((0,301));hooks=replace(hooks,monotonic=lambda:next(clock,301))
    else:hooks=replace(hooks,**changes[gate])
    tool=FakeTool();result=execute_repair(prepared,baseline,tool,hooks)
    assert result['execution_status']=='blocked'and result['blocker']and tool.calls==0


def test_checker_and_xdc_pins_cannot_be_changed(tmp_path):
    prepared,baseline,hooks=setup(tmp_path)
    with pytest.raises(ValueError):replace(prepared.pins,candidate=tuple(replace(s,sha256='d'*64)if s.name.endswith('.xdc')else s for s in prepared.pins.candidate))
    forged=replace(prepared,pins=replace(prepared.pins,checker_sha256='d'*64))
    tool=FakeTool();result=execute_repair(forged,baseline,tool,hooks)
    assert result['execution_status']=='infrastructure_error'and tool.calls==0


def test_source_tamper_blocks_before_execution(tmp_path):
    prepared,baseline,hooks=setup(tmp_path)
    (prepared.directory/'original/exercise3_core.v').write_bytes(b'changed')
    tool=FakeTool();result=execute_repair(prepared,baseline,tool,hooks)
    assert result['execution_status']=='infrastructure_error'and tool.calls==0


def test_protected_pin_object_cannot_disagree_with_frozen_plan(tmp_path):
    prepared,baseline,hooks=setup(tmp_path)
    forged=replace(prepared,pins=replace(prepared.pins,io_requirements_sha256='d'*64))
    tool=FakeTool();result=execute_repair(forged,baseline,tool,hooks)
    assert result['execution_status']=='infrastructure_error'and tool.calls==0


def test_post_run_source_mutation_prevents_candidate_validation(tmp_path):
    prepared,baseline,hooks=setup(tmp_path)
    class MutatingFake(FakeTool):
        def execute(self,prepared,hooks,deadline):
            value=super().execute(prepared,hooks,deadline)
            (prepared.directory/'original/exercise3_core.v').write_bytes(b'corrupted private copy')
            return value
    result=execute_repair(prepared,baseline,MutatingFake(),hooks)
    assert result['execution_status']=='infrastructure_error'
    assert result['candidate_projection']is None and result['native_receipt_sha256']


def test_crashed_operation_claim_does_not_allow_automatic_retry(tmp_path):
    prepared,baseline,hooks=setup(tmp_path)
    (prepared.directory/'operation-claim.json').write_bytes(b'prior uncertain operation')
    tool=FakeTool()
    with pytest.raises(FileExistsError):execute_repair(prepared,baseline,tool,hooks)
    assert tool.calls==0


def test_resource_failure_and_unknown_cleanup_preserve_native_first_cause(tmp_path):
    prepared,baseline,hooks=setup(tmp_path)
    result=execute_repair(prepared,baseline,FakeTool('result_directory_limit',settled=False),hooks)
    assert result['execution_status']=='cleanup_incomplete'
    assert result['blocker']=='result_directory_limit'
    assert result['native_exit_code']==1 and result['repair_status']=='not_validated'


def test_terminal_revocation_never_counts_as_completion(tmp_path):
    prepared,baseline,hooks=setup(tmp_path);states=iter((True,False))
    hooks=replace(hooks,control_running=lambda:next(states,False))
    result=execute_repair(prepared,baseline,FakeTool(),hooks)
    assert result['execution_status']=='failed'and result['blocker']=='control_not_running'


def test_missing_private_installation_does_not_launch_anything(tmp_path):
    prepared,baseline,hooks=setup(tmp_path)
    result=execute_repair(prepared,baseline,PrivateFixedBatchAdapter(tmp_path,'U6-FAKE-GRANT'),hooks)
    assert result['execution_status']=='infrastructure_error'
    assert result['native_receipt_sha256']is None


def test_native_prelaunch_failure_is_not_invented_cleanup_or_zero_process_count(tmp_path):
    prepared,baseline,hooks=setup(tmp_path)
    class NoProcess(FakeTool):
        def execute(self,*args):
            return FixedToolOutput(json.dumps({'success':False,'launcher_pid':None,'exit_code':None,
                'cleanup':None,'limit_reason':'headroom_precondition_failed'}).encode(),{})
    result=execute_repair(prepared,baseline,NoProcess(),hooks)
    assert result['execution_status']=='failed'
    assert result['blocker']=='headroom_precondition_failed'and result['native_exit_code']is None
