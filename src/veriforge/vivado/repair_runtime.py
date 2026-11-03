"""Trusted host/worker entry point for fixed-profile Vivado candidate jobs.

The HTTP layer resolves authorized versions/artifacts before calling prepare.
Queue payloads retain job_id, baseline and admitted candidate-plan identity. Execution is
worker-only; this module supplies no HTTP route, global enable switch or DB.
"""
from dataclasses import asdict, dataclass
import json
from pathlib import Path
import re
from typing import Callable
from uuid import uuid4

from .investigation import (VivadoInvestigation, VivadoProjectIdentity, VivadoExecution,
                            REPORTS, investigate_vivado_reports, compare_vivado_candidate, vivado_projection)
from .readiness import SourceIdentity
from .repair_execution import (
    ControllerInstallation, PrivateFixedBatchAdapter, PreparedRepair, RepairHooks,
    RepairPins, PROFILES, _digest, _json, _read, _sha, _verify, execute_repair, prepare_repair,
)


def _requirements_blockers(baseline, pins):
    return (['missing_io_requirements'] if pins.io_requirements_sha256 is None or
            baseline.missing_input_delays != 0 or baseline.missing_output_delays != 0 or
            baseline.methodology_missing_io != 0 else [])


@dataclass(frozen=True)
class RepairTicket:
    job_id: str
    baseline_sha256: str
    candidate_plan_sha256: str
    blockers: tuple[str, ...]


class VivadoRepairRuntime:
    """Explicit per-process installation; no caller-provided runner or script.

    Paths, pins, requirements and callbacks are trusted startup dependencies.
    Workers must install the same root/configuration, resolve the exact retained
    baseline, and call execute with an opaque job ID. No simulator runs at prepare.
    """

    def __init__(self, artifact_root: Path, installation: ControllerInstallation,
                 owner: str, hooks_factory: Callable[[str], RepairHooks], *,
                 requirements_document: Path | None = None):
        artifact_root=Path(artifact_root)
        if not artifact_root.is_absolute():raise ValueError('absolute artifact root required')
        if any(p.is_symlink() or p.is_junction() for p in (artifact_root,*artifact_root.parents)):
            raise ValueError('unlinked artifact root required')
        self.root=artifact_root.resolve()
        self.installation=installation
        self.runner=PrivateFixedBatchAdapter(installation.project_root,owner,installation=installation)
        self.hooks_factory=hooks_factory
        self.requirements_document=requirements_document
        if requirements_document is not None and not Path(requirements_document).is_absolute():
            raise ValueError('absolute trusted requirements path required')

    def _directory(self, job_id):
        if any(p.is_symlink()or p.is_junction()for p in (self.root,*self.root.parents)):
            raise ValueError('artifact root binding changed')
        if not isinstance(job_id,str) or not re.fullmatch(r'vivado-repair-[0-9a-f]{32}',job_id):
            raise ValueError('opaque repair job ID required')
        directory=self.root/job_id
        if directory.is_symlink()or directory.is_junction():raise ValueError('linked job rejected')
        return directory

    def _requirements(self, pins):
        if pins.io_requirements_sha256 is not None:
            if self.requirements_document is None or _digest(_read(Path(self.requirements_document)))!=pins.io_requirements_sha256:
                raise ValueError('trusted requirements document unavailable/changed')

    def prepare(self, original: Path, candidate: Path, baseline: VivadoInvestigation,
                pins: RepairPins) -> RepairTicket:
        self._requirements(pins)
        for source in (original,candidate):
            if self.root.is_relative_to(Path(source).resolve()):
                raise ValueError('artifact root must be separate from source directories')
        job_id='vivado-repair-'+uuid4().hex
        prepared=prepare_repair(original,candidate,self._directory(job_id),baseline,pins,
                                project_root=self.installation.project_root,installation=self.installation)
        manifest={'schema':'vivado.repair-job.v1','job_id':job_id,'pins':asdict(pins),
                  'installation_sha256':self.installation.identity_sha256,
                  'baseline_sha256':prepared.baseline_sha256,
                  'original_plan_sha256':prepared.original_plan_sha256,
                  'candidate_plan_sha256':prepared.candidate_plan_sha256}
        with (prepared.directory/'job.json').open('xb')as stream:stream.write(_json(manifest))
        return RepairTicket(job_id,prepared.baseline_sha256,prepared.candidate_plan_sha256,
                            tuple(_requirements_blockers(baseline,pins)))

    def _load(self,job_id,baseline,expected_candidate_plan_sha256):
        _sha(expected_candidate_plan_sha256)
        vivado_projection(baseline)
        directory=self._directory(job_id)
        manifest=json.loads(_read(directory/'job.json'))
        if (manifest['schema']!='vivado.repair-job.v1' or manifest['job_id']!=job_id or
            manifest['installation_sha256']!=self.installation.identity_sha256 or
            manifest['baseline_sha256']!=baseline.investigation_sha256 or
            manifest['candidate_plan_sha256']!=expected_candidate_plan_sha256):
            raise ValueError('job binding/lineage mismatch')
        data=manifest['pins']
        pins=RepairPins(data['profile'],tuple(SourceIdentity(**s)for s in data['original']),
                        tuple(SourceIdentity(**s)for s in data['candidate']),
                        data['io_requirements_sha256'],data['checker_sha256'])
        prepared=PreparedRepair(directory,pins,manifest['baseline_sha256'],
                                manifest['original_plan_sha256'],manifest['candidate_plan_sha256'])
        if _read(directory/'baseline-investigation.json')!=_json(asdict(baseline)):
            raise ValueError('saved baseline changed')
        _verify(prepared)
        return prepared

    def _result(self,prepared,baseline,raw):
        result=json.loads(raw)
        required={'schema','baseline_sha256','candidate_plan_sha256','runner_kind','execution_status',
                  'blocker','native_receipt_sha256','candidate_projection','repair_status','receipt_persisted'}
        optional={'native_receipt_origin','native_exit_code','error_type'}
        if not isinstance(result,dict)or not required<=set(result)or set(result)-required-optional:
            raise ValueError('closed execution receipt schema required')
        if (result['schema']!='vivado.repair-execution.v1' or
            result['baseline_sha256']!=prepared.baseline_sha256 or
            result['candidate_plan_sha256']!=prepared.candidate_plan_sha256 or
            result['runner_kind']!=self.runner.kind or result['receipt_persisted']is not True or
            result['repair_status']!='not_validated'):
            raise ValueError('execution receipt binding mismatch')
        statuses={'blocked','infrastructure_error','completed','failed','resource_limited','timed_out','cancelled','cleanup_incomplete'}
        blockers={'missing_io_requirements','baseline_evidence_incomplete','cancelled','control_not_running',
                  'exclusive_reservation_missing','startup_memory_floor','fixed_protocol_or_identity_failure',
                  'native_receipt_unavailable','native_receipt_persistence_mismatch','report_manifest_unavailable',
                  'report_identity_unavailable','timeout','control_or_reservation_changed','control_unavailable',
                  'preparation_validation_failed','headroom_precondition_failed','backend_initialization_failed',
                  'job_setup_failed','native_start_failed','native_monitor_failed','cleanup_incomplete',
                  'capture_status_unavailable','storage_measurement_error','entry_scan_limit','linked_output_rejected',
                  'stdout_missing','stdout_nonregular','stdout_unreadable','stdout_limit_or_capture_error',
                  'artifact_manifest_limit','artifact_manifest_changed','artifact_manifest_unavailable',
                  'source_identity_changed','source_identity_unavailable','receipt_byte_limit','receipt_persistence_failed',
                  'file_count_limit','initialization_cache_limit','result_directory_limit','total_directory_limit'}
        if not isinstance(result['execution_status'],str)or result['execution_status']not in statuses or (result['blocker']is not None and
            (not isinstance(result['blocker'],str)or result['blocker']not in blockers)):
            raise ValueError('unsupported execution state')
        if 'error_type'in result and (not isinstance(result['error_type'],str)or result['error_type']not in {
            'ValueError','TypeError','OSError','FileNotFoundError','PermissionError','RuntimeError','KeyError',
            'AttributeError','StopIteration','JSONDecodeError','AssertionError','FileExistsError',
            'NotADirectoryError','IsADirectoryError','Exception'}):
            raise ValueError('unsupported fixed error type')
        if 'native_exit_code'in result and result['native_exit_code']is not None and type(result['native_exit_code'])is not int:
            raise ValueError('integer native exit code required')
        if 'native_receipt_origin'in result and result['native_receipt_origin']not in ('saved_native_receipt','runner_return_fallback'):
            raise ValueError('unsupported native receipt origin')
        claim={'baseline':prepared.baseline_sha256,'candidate_plan':prepared.candidate_plan_sha256}
        if json.loads(_read(prepared.directory/'operation-claim.json'))!=claim:raise ValueError('operation claim mismatch')
        native=None
        if result['native_receipt_sha256']is not None:
            _sha(result['native_receipt_sha256'])
            native_raw=_read(prepared.directory/'native-receipt.json')
            if _digest(native_raw)!=result['native_receipt_sha256']:raise ValueError('native receipt identity changed')
            native=json.loads(native_raw)
            if not isinstance(native,dict):raise ValueError('native receipt object required')
        projection=result['candidate_projection']
        if projection is not None:
            if native is None:raise ValueError('candidate projection has no native receipt')
            directory=prepared.directory/'received-reports'
            if directory.is_symlink()or directory.is_junction()or not directory.is_dir():raise ValueError('received report identity unavailable')
            names={p.name for p in directory.iterdir()}
            if names-{name+'.rpt'for name in REPORTS}:raise ValueError('unknown received report')
            reports={name:_read(directory/name)for name in names}
            project=VivadoProjectIdentity(baseline.project.part,baseline.project.top,baseline.project.clock_period_ns,
                prepared.pins.candidate,PROFILES[prepared.pins.profile][2],baseline.project.functional_specification_sha256)
            candidate=investigate_vivado_reports(project,VivadoExecution(result['execution_status'],
                result.get('native_exit_code'),result['native_receipt_sha256']),reports,
                parent_investigation_sha256=baseline.investigation_sha256)
            canonical=vivado_projection(candidate,compare_vivado_candidate(baseline,candidate))
            if _json(projection)!=_json(canonical):raise ValueError('candidate projection is not canonical report evidence')
            result['candidate_projection']=canonical
        status=result['execution_status']
        if status=='completed':
            cleanup={}if native is None else native.get('cleanup')or{}
            if not isinstance(cleanup,dict):raise ValueError('native cleanup object required')
            if (_requirements_blockers(baseline,prepared.pins)or not baseline.complete_report_set or
                baseline.execution.status!='completed' or result['blocker']is not None or
                native is None or native.get('success')is not True or type(native.get('exit_code'))is not int or native['exit_code']!=0 or
                native.get('limit_reason')is not None or type(result.get('native_exit_code'))is not int or result['native_exit_code']!=0 or
                projection is None or not candidate.complete_report_set or
                type(cleanup.get('active_owned_processes_after_cleanup'))is not int or cleanup['active_owned_processes_after_cleanup']!=0 or
                any(cleanup.get(key)is not True for key in ('job_handle_closed','reader_settled','pipe_closed'))or cleanup.get('errors')!=[] or
                result.get('native_receipt_origin')!='saved_native_receipt' or
                (self.runner.kind=='private_fixed_native_protocol_relocated'and native.get('receipt_persisted')is not True)):
                raise ValueError('contradictory completed execution')
            if self.runner.kind=='private_fixed_native_protocol_relocated':
                if (native.get('plan_sha256')!=prepared.candidate_plan_sha256 or
                    native.get('script_sha256')!=PROFILES[prepared.pins.profile][2] or
                    not isinstance(native.get('artifacts'),dict)):
                    raise ValueError('native completion plan/recipe binding mismatch')
                for name,data in reports.items():
                    if native['artifacts'].get(name)!={'sha256':_digest(data),'bytes':len(data)}:
                        raise ValueError('native completion report manifest mismatch')
        elif result['blocker']is None:raise ValueError('non-completion requires a fixed failure reason')
        if status=='blocked'and (native is not None or projection is not None or
                                any(key in result for key in ('native_exit_code','native_receipt_origin','error_type'))):
            raise ValueError('blocked execution cannot have candidate evidence')
        if status not in ('blocked','infrastructure_error'):
            if native is None or projection is None:raise ValueError('terminal execution evidence missing')
        if native is not None or status not in ('blocked','infrastructure_error'):
            if json.loads(_read(prepared.directory/'execution-started.json'))!=claim:raise ValueError('execution claim mismatch')
        return result

    def inspect(self,job_id:str,baseline:VivadoInvestigation,*,expected_candidate_plan_sha256:str)->dict:
        prepared=self._load(job_id,baseline,expected_candidate_plan_sha256)
        output=prepared.directory/'repair-execution.json'
        result=None;receipt_sha=None
        if output.exists():
            raw=_read(output);result=self._result(prepared,baseline,raw);receipt_sha=_digest(raw)
            status=result['execution_status']
        elif (prepared.directory/'operation-claim.json').exists():status='quarantined'
        else:status='prepared'
        return {'schema':'vivado.repair-job-projection.v1','job_id':job_id,
                'installation_sha256':self.installation.identity_sha256,
                'baseline_sha256':prepared.baseline_sha256,'candidate_plan_sha256':prepared.candidate_plan_sha256,
                'execution_status':status,'blockers':_requirements_blockers(baseline,prepared.pins),
                'repair_status':'not_validated','result':result,'result_sha256':receipt_sha}

    def execute(self,job_id:str,baseline:VivadoInvestigation,*,expected_candidate_plan_sha256:str)->dict:
        """Worker-only, one invocation. Duplicate/uncertain delivery never reruns."""
        current=self.inspect(job_id,baseline,expected_candidate_plan_sha256=expected_candidate_plan_sha256)
        if current['execution_status']!='prepared':return current
        prepared=self._load(job_id,baseline,expected_candidate_plan_sha256)
        self._requirements(prepared.pins)
        hooks=self.hooks_factory(job_id)
        if not isinstance(hooks,RepairHooks):raise ValueError('trusted RepairHooks required')
        try:
            result=execute_repair(prepared,baseline,self.runner,hooks)
        except FileExistsError:
            # Another delivery claimed it; this worker must not start another job.
            return self.inspect(job_id,baseline,expected_candidate_plan_sha256=expected_candidate_plan_sha256)
        if result['receipt_persisted']is not True:
            # Never return a stale on-disk success after a persistence failure.
            return {**current,'execution_status':'quarantined','result':None,'blockers':['receipt_persistence_failed']}
        return self.inspect(job_id,baseline,expected_candidate_plan_sha256=expected_candidate_plan_sha256)
