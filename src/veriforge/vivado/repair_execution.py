"""Trusted fixed-protocol candidate orchestration; never an HTTP/model runner.

The current Exercise3 evidence is blocked by missing I/O requirements. Native
dispatch additionally needs main's installation binding and exclusive grant.
Fake boundaries are explicitly labeled and do not establish live execution.
"""
from dataclasses import asdict, dataclass
from hashlib import sha256
import json
import math
from pathlib import Path
import time
import types
from typing import Callable

from . import investigation as checker
from .investigation import VivadoExecution, VivadoInvestigation, VivadoProjectIdentity
from .readiness import BatchRecipe, EXERCISE3_HASHES, SourceIdentity, render_batch_recipe

CAP = 1048576
IMPLEMENTATION_SHA256 = sha256(Path(__file__).read_bytes()).hexdigest()
NAMES = ('exercise3_core.v', 'exercise3_nexys_video_top.v', 'nexys_video_exercise3.xdc')
APPROVED_XDC_SHA256 = EXERCISE3_HASHES[NAMES[2]]
STORAGE_SHA256 = 'c0362f40428804eef23f337fe869155ef84f105fe92e5c098173cd77ffe47a8d'
# Host-installed immutable Python only, not a caller-supplied executable/script.
PROFILES = {
    'synthesis': ('tests/vivado/.tmp-u6-run-post-synth-retry-v3-20261030.py',
                  '95abfb9d302485315b79232d828803b4e480affbdd649fa3171837d4d4bfff7d',
                  '73da33982dba6a9666462a94ec63cd78866ce23d29450c77efc2877f85727045', 300),
    'routed': ('.coordination/vivado-routed-nov1/controller.py',
               '85ccce4d85d91cd8ee09d2d061a56a96dd2c0d871b964e5c1248899d929c1568',
               '192be10a6d3228773c03600e3b0d682c5b86cff2c0ba2024d94dae3876764613', 600),
}


def _digest(raw): return sha256(raw).hexdigest()


def _json(value): return (json.dumps(value, sort_keys=True, indent=2, allow_nan=False)+'\n').encode()


def _read(path):
    if path.is_symlink() or path.is_junction() or not path.is_file(): raise ValueError('regular unlinked file required')
    with path.open('rb') as stream: raw = stream.read(CAP+1)
    if len(raw)>CAP: raise ValueError('input byte limit')
    return raw


def _sha(value):
    import re
    if not isinstance(value,str) or not re.fullmatch('[a-f0-9]{64}',value): raise ValueError('SHA256 required')


@dataclass(frozen=True)
class RepairPins:
    profile: str
    original: tuple[SourceIdentity, ...]
    candidate: tuple[SourceIdentity, ...]
    io_requirements_sha256: str | None
    checker_sha256: str

    def __post_init__(self):
        if self.profile not in PROFILES: raise ValueError('unsupported fixed recipe')
        for group in (self.original,self.candidate):
            if not isinstance(group,tuple) or len(group)!=3 or {s.name for s in group}!=set(NAMES):
                raise ValueError('exact source pins required')
            for source in group:
                _sha(source.sha256)
                if type(source.size_bytes)is not int or not 0<source.size_bytes<=CAP: raise ValueError('source size')
        old,new = ({s.name:s for s in group} for group in (self.original,self.candidate))
        if old[NAMES[2]]!=new[NAMES[2]]: raise ValueError('constraint change is not an unchanged-requirement candidate')
        if old[NAMES[2]].sha256!=APPROVED_XDC_SHA256:raise ValueError('XDC is outside the fixed admitted profile')
        _sha(self.checker_sha256)
        if self.io_requirements_sha256 is not None: _sha(self.io_requirements_sha256)


@dataclass(frozen=True)
class PreparedRepair:
    directory: Path
    pins: RepairPins
    baseline_sha256: str
    original_plan_sha256: str
    candidate_plan_sha256: str


@dataclass(frozen=True)
class RepairHooks:
    control_running: Callable[[], bool]
    reservation_owned: Callable[[], bool]
    cancelled: Callable[[], bool]
    available_physical_bytes: Callable[[], int]
    monotonic: Callable[[], float] = time.monotonic


@dataclass(frozen=True)
class ControllerInstallation:
    """Trusted startup configuration, never deserialized from an API request.

    Controller identities remain compiled in. Paths may relocate their exact
    bytes; they do not admit another controller or a new engineering profile.
    An explicitly relocated launcher additionally requires its host-reviewed hash.
    """
    project_root: Path
    synthesis_controller: Path
    routed_controller: Path | None = None
    routed_recipe: Path | None = None
    launcher: Path | None = None
    launcher_sha256: str | None = None

    def __post_init__(self):
        for path in (self.project_root, self.synthesis_controller, self.routed_controller,
                     self.routed_recipe, self.launcher):
            if path is not None and (not isinstance(path,Path) or not path.is_absolute()):
                raise ValueError('absolute trusted installation paths required')
        for path in (self.synthesis_controller,self.routed_controller):
            if path is not None and len(path.parents)<3:
                raise ValueError('installed fixed controller requires at least three parent levels')
        if (self.launcher is None)!=(self.launcher_sha256 is None):
            raise ValueError('relocated launcher requires an identity')
        if self.launcher is not None:
            _sha(self.launcher_sha256)
            if self.launcher.name.lower()!='vivado.bat' or any(c in str(self.launcher)for c in '&|<>^%!"\r\n'):
                raise ValueError('unsupported fixed batch launcher path')

    @classmethod
    def legacy(cls, root):
        root=Path(root).resolve(strict=True)
        return cls(root,root/PROFILES['synthesis'][0],root/PROFILES['routed'][0],
                   root/'.coordination/vivado-routed-nov1/attempt/recipe.tcl')

    @property
    def identity_sha256(self):
        return _digest(_json({key:str(value)if isinstance(value,Path)else value
                              for key,value in asdict(self).items()}))

    def controller(self, profile):
        path=self.synthesis_controller if profile=='synthesis'else self.routed_controller
        if profile not in PROFILES or path is None:raise ValueError('profile is not installed')
        raw=_read(path)
        if _digest(raw)!=PROFILES[profile][1]:raise ValueError('trusted private installation unavailable/changed')
        return path,raw

    def recipe(self, profile):
        if profile=='synthesis':raw=render_batch_recipe(BatchRecipe.EXERCISE3_POST_SYNTH).encode()
        elif profile=='routed'and self.routed_recipe is not None:raw=_read(self.routed_recipe)
        else:raise ValueError('fixed recipe is not installed')
        if _digest(raw)!=PROFILES[profile][2]:raise ValueError('fixed recipe identity changed/unavailable')
        return raw

    def verify_launcher(self):
        if self.launcher is not None and _digest(_read(self.launcher))!=self.launcher_sha256:
            raise ValueError('installed launcher identity changed')


@dataclass(frozen=True)
class FixedToolOutput:
    receipt: bytes
    reports: dict[str, bytes]
    infrastructure_error: str | None = None
    receipt_origin: str = 'saved_native_receipt'

    def __post_init__(self):
        if self.infrastructure_error not in (None,'native_receipt_unavailable','native_receipt_persistence_mismatch',
                                             'report_manifest_unavailable','report_identity_unavailable'):
            raise ValueError('unsupported fixed protocol error')
        if self.receipt_origin not in ('saved_native_receipt','runner_return_fallback'):raise ValueError('receipt origin')


def _gate(hooks, deadline):
    try:
        if hooks.cancelled() is not False: return 'cancelled'
        if hooks.control_running() is not True: return 'control_not_running'
        if hooks.reservation_owned() is not True: return 'exclusive_reservation_missing'
        now=hooks.monotonic()
        if type(now)not in(int,float)or not math.isfinite(now):return 'control_unavailable'
        if now>=deadline: return 'timeout'
    except Exception: return 'control_unavailable'
    return None


def _sources(directory, expected):
    actual=[]
    for item in sorted(expected,key=lambda s:s.name):
        raw=_read(directory/item.name)
        if len(raw)!=item.size_bytes or _digest(raw)!=item.sha256: raise ValueError('source identity changed')
        actual.append(item)
    return tuple(actual)


def prepare_repair(original: Path, candidate: Path, destination: Path,
                   baseline: VivadoInvestigation, pins: RepairPins, *, project_root: Path,
                   installation: ControllerInstallation | None = None) -> PreparedRepair:
    """Copy three exact approved files into new separate snapshots; no execution.

    Pins and project_root are trusted host configuration, never model authority.
    Baseline source/config/clock identities are frozen into each plan.
    """
    checker.vivado_projection(baseline)  # Validate baseline's immutable digest.
    if baseline.project.clock_period_ns!=10 or baseline.project.recipe_sha256!=PROFILES[pins.profile][2]:
        raise ValueError('baseline configuration is outside the fixed profile')
    if pins.checker_sha256!=checker.IMPLEMENTATION_SHA256 or _digest(_read(Path(checker.__file__)))!=pins.checker_sha256:
        raise ValueError('checker pin changed')
    if sorted(baseline.project.sources,key=lambda s:s.name)!=sorted(pins.original,key=lambda s:s.name):
        raise ValueError('original differs from baseline source identity')
    if baseline.design_phase!=pins.profile: raise ValueError('baseline phase differs from fixed recipe')
    _sources(Path(original),pins.original);_sources(Path(candidate),pins.candidate)
    installation=installation or ControllerInstallation.legacy(project_root)
    if installation.project_root.resolve()!=Path(project_root).resolve():raise ValueError('installation root mismatch')
    recipe=installation.recipe(pins.profile)
    destination=Path(destination).absolute()
    if destination.is_symlink() or destination.is_junction() or destination.exists(): raise ValueError('fresh destination required')
    destination.mkdir(parents=True)
    (destination/'baseline-investigation.json').write_bytes(_json(asdict(baseline)))
    plans=[]
    for label,source,identities in [('original',Path(original),pins.original),('candidate',Path(candidate),pins.candidate)]:
        directory=destination/label;directory.mkdir()
        for item in identities:
            raw=_read(source/item.name)
            if _digest(raw)!=item.sha256 or len(raw)!=item.size_bytes: raise ValueError('source changed during copy')
            (directory/item.name).write_bytes(raw)
        (directory/'recipe.tcl').write_bytes(recipe)
        plan={'schema':'vivado.repair-snapshot.v1','role':label,'baseline_sha256':baseline.investigation_sha256,
              'orchestration_sha256':IMPLEMENTATION_SHA256,
              'project':asdict(baseline.project),'sources':[asdict(s)for s in identities],
              'pins':asdict(pins),'recipe_sha256':_digest(recipe),'wall_seconds':PROFILES[pins.profile][3]}
        data=_json(plan);(directory/'plan.json').write_bytes(data);plans.append(_digest(data))
    _sources(Path(original),pins.original);_sources(Path(candidate),pins.candidate)
    return PreparedRepair(destination,pins,baseline.investigation_sha256,*plans)


def _verify(prepared, fresh=False):
    if (prepared.pins.checker_sha256!=checker.IMPLEMENTATION_SHA256 or
        _digest(_read(Path(checker.__file__)))!=prepared.pins.checker_sha256 or
        _digest(_read(Path(__file__)))!=IMPLEMENTATION_SHA256):raise ValueError('checker/orchestration implementation changed')
    for label,identities,planhash in [('original',prepared.pins.original,prepared.original_plan_sha256),
                                      ('candidate',prepared.pins.candidate,prepared.candidate_plan_sha256)]:
        directory=prepared.directory/label
        if directory.is_symlink() or directory.is_junction() or not directory.is_dir(): raise ValueError('snapshot containment')
        _sources(directory,identities)
        if _digest(_read(directory/'plan.json'))!=planhash: raise ValueError('snapshot configuration changed')
        plan=json.loads(_read(directory/'plan.json'))
        if _json(plan['pins'])!=_json(asdict(prepared.pins)) or plan['baseline_sha256']!=prepared.baseline_sha256 or plan['orchestration_sha256']!=IMPLEMENTATION_SHA256:
            raise ValueError('protected pins do not match frozen configuration')
        if _digest(_read(directory/'recipe.tcl'))!=PROFILES[prepared.pins.profile][2]: raise ValueError('recipe changed')
        if fresh and {p.name for p in directory.iterdir()}!=set(NAMES)|{'recipe.tcl','plan.json'}:
            raise ValueError('snapshot is not fresh')


class PrivateFixedBatchAdapter:
    """Concrete relocated invocation of installed accepted private run_attempt.

    Only the two compiled-in controller hashes may be loaded. File bytes remain
    unchanged. A fresh module instance binds ATTEMPT/OWNER/PLAN_HASH to the new
    reviewed snapshot; no handle/process primitive is edited. Main must qualify
    this new startup binding before a native grant. No arbitrary Python/Tcl.
    """
    kind='private_fixed_native_protocol_relocated'

    def __init__(self,project_root:Path,owner:str,*,installation:ControllerInstallation|None=None):
        if not owner.startswith('U6-') or len(owner)>100 or not all(c.isalnum()or c=='-'for c in owner):
            raise ValueError('explicit main reservation owner required')
        self.root=Path(project_root).resolve(strict=True);self.owner=owner
        self.installation=installation or ControllerInstallation.legacy(self.root)
        if self.installation.project_root.resolve()!=self.root:raise ValueError('installation root mismatch')

    def _load_controller(self,prepared):
        """Load verified installed bytes; separate from invocation for host checks."""
        path,raw=self.installation.controller(prepared.pins.profile)
        self.installation.verify_launcher()
        if _digest(_read(Path(__file__).with_name('storage.py')))!=STORAGE_SHA256:
            raise ValueError('private storage implementation changed')
        module=types.ModuleType('_veriforge_fixed_vivado_repair');module.__file__=str(path)
        exec(compile(raw,str(path),'exec'),module.__dict__)
        if module.STORAGE_HASH!=STORAGE_SHA256 or _digest(_read(Path(module.storage_module.__file__)))!=STORAGE_SHA256:
            raise ValueError('private storage implementation changed')
        # Exact accepted controllers are import-inert. All operational globals
        # below are bound before invoking any controller function.
        module.ROOT=self.root
        module.ATTEMPT=prepared.directory/'candidate';module.OWNER=self.owner
        module.PLAN_HASH=prepared.candidate_plan_sha256
        if self.installation.launcher is not None:module.LAUNCHER=str(self.installation.launcher)
        return module

    def execute(self, prepared, hooks, deadline):
        def native_gate():
            state=json.loads(_read(self.root/'.coordination/current-state.json'))
            return (state['control']=='RUNNING' and state.get('resource_reservations',{}).get('engineering_tool',{}).get('owner')==self.owner
                    and _gate(hooks,deadline)is None)
        if not native_gate():raise ValueError('exact canonical main grant unavailable')
        profile=prepared.pins.profile
        module=self._load_controller(prepared)
        def check():
            _verify(prepared)
            return prepared.pins.candidate
        native=module.run_attempt(control_probe=native_gate,
            memory_probe=hooks.available_physical_bytes,validate=check,source_probe=check,
            clock=hooks.monotonic)
        filename='routed-receipt.json'if profile=='routed'else'synthesis-receipt.json'
        try:receipt=_read(module.ATTEMPT/filename)
        except (OSError,ValueError):
            return FixedToolOutput(_json(native),{},'native_receipt_unavailable','runner_return_fallback')
        decoded=json.loads(receipt)
        if _json(decoded)!=_json(native) or native.get('receipt_persisted')is not True:
            return FixedToolOutput(receipt,{},'native_receipt_persistence_mismatch')
        reports={}
        if not isinstance(decoded.get('artifacts'),dict):return FixedToolOutput(receipt,reports,'report_manifest_unavailable')
        for name in (checker.REPORTS if profile=='routed'else checker.SYNTH_REPORTS):
            p=module.ATTEMPT/(name+'.rpt')
            try:
                if p.is_symlink()or p.is_junction():raise ValueError('linked report')
                if not p.exists():continue
                data=_read(p);ref=decoded['artifacts'].get(name+'.rpt')
                if ref!={'bytes':len(data),'sha256':_digest(data)}:raise ValueError('native report identity mismatch')
            except (OSError,ValueError):return FixedToolOutput(receipt,reports,'report_identity_unavailable')
            reports[name+'.rpt']=data
        return FixedToolOutput(receipt,reports)


def execute_repair(prepared:PreparedRepair, baseline:VivadoInvestigation, runner, hooks:RepairHooks) -> dict:
    """Reuse retained original reports, execute candidate once, preserve receipts.

    The runner is a trusted host dependency. Fake test boundaries must label kind
    accordingly. Neither a callable nor a marker constitutes runtime authority.
    """
    output=prepared.directory/'repair-execution.json'
    if runner.kind not in ('private_fixed_native_protocol_relocated','fake_tool_boundary_not_native_execution'):
        raise ValueError('explicit trusted/fake boundary provenance required')
    if runner.kind=='private_fixed_native_protocol_relocated'and not isinstance(runner,PrivateFixedBatchAdapter):
        raise ValueError('native provenance requires installed fixed adapter')
    if output.exists():raise ValueError('one-shot execution receipt already exists')
    with (prepared.directory/'operation-claim.json').open('xb')as stream:
        stream.write(_json({'baseline':prepared.baseline_sha256,'candidate_plan':prepared.candidate_plan_sha256}))
    result={'schema':'vivado.repair-execution.v1','baseline_sha256':prepared.baseline_sha256,
            'candidate_plan_sha256':prepared.candidate_plan_sha256,
            'runner_kind':runner.kind,'execution_status':'blocked','blocker':None,
            'native_receipt_sha256':None,'candidate_projection':None,'repair_status':'not_validated','receipt_persisted':False}
    deadline=None
    try:
        started=hooks.monotonic()
        if type(started)not in(int,float)or not math.isfinite(started):raise ValueError('invalid deadline clock')
        deadline=started+PROFILES[prepared.pins.profile][3]
        checker.vivado_projection(baseline)
        if baseline.investigation_sha256!=prepared.baseline_sha256:raise ValueError('baseline lineage changed')
        if _read(prepared.directory/'baseline-investigation.json')!=_json(asdict(baseline)):
            raise ValueError('saved original report lineage changed')
        _verify(prepared,fresh=True)
        if prepared.pins.io_requirements_sha256 is None or baseline.missing_input_delays!=0 or baseline.missing_output_delays!=0 or baseline.methodology_missing_io!=0:
            result['blocker']='missing_io_requirements';return result
        if not baseline.complete_report_set or baseline.execution.status!='completed':
            result['blocker']='baseline_evidence_incomplete';return result
        reason=_gate(hooks,deadline)
        if reason:result['blocker']=reason;return result
        memory=hooks.available_physical_bytes()
        if type(memory)is not int or memory<2**31:result['blocker']='startup_memory_floor';return result
        # Atomic one-shot claim precedes any runner call, including setup failures.
        with (prepared.directory/'execution-started.json').open('xb')as stream:
            stream.write(_json({'baseline':prepared.baseline_sha256,'candidate_plan':prepared.candidate_plan_sha256}))
        returned=runner.execute(prepared,hooks,deadline)
        if not isinstance(returned,FixedToolOutput):raise ValueError('fixed native protocol output required')
        raw,reports=returned.receipt,returned.reports
        if type(raw)is not bytes or len(raw)>CAP:raise ValueError('native receipt byte cap')
        (prepared.directory/'native-receipt.json').write_bytes(raw)  # Exact immutable received bytes.
        result['native_receipt_sha256']=_digest(raw)
        if (not isinstance(reports,dict)or set(reports)-{n+'.rpt'for n in checker.REPORTS}or
            any(type(value)is not bytes or len(value)>CAP for value in reports.values())or
            sum(map(len,reports.values()))>8*CAP):raise ValueError('report byte/name limit')
        saved_reports=prepared.directory/'received-reports';saved_reports.mkdir()
        for name,data in reports.items():
            with (saved_reports/name).open('xb')as stream:stream.write(data)
        native=json.loads(raw);_verify(prepared)
        native_reason=native.get('limit_reason')
        allowed={'timeout','control_or_reservation_changed','control_unavailable','preparation_validation_failed',
            'headroom_precondition_failed','backend_initialization_failed','job_setup_failed','native_start_failed',
            'native_monitor_failed','cleanup_incomplete','capture_status_unavailable','storage_measurement_error',
            'entry_scan_limit','linked_output_rejected','stdout_missing','stdout_nonregular','stdout_unreadable',
            'stdout_limit_or_capture_error','artifact_manifest_limit','artifact_manifest_changed','artifact_manifest_unavailable',
            'source_identity_changed','source_identity_unavailable','receipt_byte_limit','receipt_persistence_failed',
            'file_count_limit','initialization_cache_limit','result_directory_limit','total_directory_limit'}
        if native_reason is not None and (not isinstance(native_reason,str)or native_reason not in allowed):
            raise ValueError('unknown native infrastructure status')
        reason=native_reason or _gate(hooks,deadline)or returned.infrastructure_error
        result['blocker']=reason
        result['native_receipt_origin']=returned.receipt_origin
        cleanup=native.get('cleanup')or{}
        settled=(type(cleanup.get('active_owned_processes_after_cleanup'))is int and cleanup['active_owned_processes_after_cleanup']==0
                 and cleanup.get('job_handle_closed')is True and cleanup.get('reader_settled')is True
                 and cleanup.get('pipe_closed')is True and cleanup.get('errors')==[])
        completed=(native.get('success')is True and type(native.get('exit_code'))is int and native['exit_code']==0 and settled and reason is None)
        resources={'entry_scan_limit','file_count_limit','result_directory_limit','initialization_cache_limit',
                   'total_directory_limit','stdout_limit_or_capture_error','artifact_manifest_limit','receipt_byte_limit'}
        no_process=('launcher_pid'in native and native['launcher_pid']is None and native.get('cleanup')is None)
        status=('completed'if completed else'timed_out'if reason=='timeout'else'cleanup_incomplete'if not settled and not no_process else
                'resource_limited'if reason in resources else'cancelled'if reason in ('cancelled','control_or_reservation_changed')else'failed')
        exit_code=native.get('exit_code')if type(native.get('exit_code'))is int else None
        project=VivadoProjectIdentity(baseline.project.part,baseline.project.top,baseline.project.clock_period_ns,
            prepared.pins.candidate,PROFILES[prepared.pins.profile][2],baseline.project.functional_specification_sha256)
        candidate=checker.investigate_vivado_reports(project,VivadoExecution(status,exit_code,_digest(raw)),
            reports,parent_investigation_sha256=baseline.investigation_sha256)
        comparison=checker.compare_vivado_candidate(baseline,candidate)
        result.update(execution_status=status,blocker=reason,native_exit_code=exit_code,candidate_projection=checker.vivado_projection(candidate,comparison))
        if not settled and not no_process:result['blocker']=result['blocker']or'cleanup_incomplete'
        return result
    except Exception as error:
        result.update(execution_status='infrastructure_error',blocker=result['blocker']or'fixed_protocol_or_identity_failure',error_type=type(error).__name__)
        return result
    finally:
        try:
            result['receipt_persisted']=True
            data=_json(result)
            if len(data)>CAP:raise ValueError('execution projection byte cap')
            with output.open('xb')as stream:stream.write(data)
        except (OSError,ValueError):
            result.update(receipt_persisted=False,execution_status='infrastructure_error',
                          blocker=result['blocker']or'receipt_persistence_failed')
