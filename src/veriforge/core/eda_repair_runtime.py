"""Per-process trusted EDA installation and ID-only transactional worker entry."""
from dataclasses import dataclass,replace
from copy import copy
from hashlib import sha256
from pathlib import Path
from sqlalchemy.exc import IntegrityError
from sqlalchemy import select
from veriforge.contracts import canonical_hash
from . import eda_repair_jobs as jobs,service
from .eda_schema import require_eda_schema

class VivadoDriver:
    domain='vivado'
    def __init__(self,runtime,*,original,candidate,baseline,pins,configuration_hash):
        from veriforge.vivado.repair_runtime import VivadoRepairRuntime
        if type(runtime) is not VivadoRepairRuntime:raise TypeError('installed Vivado runtime required')
        self.runtime,self.original,self.candidate,self.baseline,self.pins=runtime,original,candidate,baseline,pins
        self.configuration_hash=configuration_hash
    def prepare(self,app_job_id):
        t=self.runtime.prepare(self.original,self.candidate,self.baseline,self.pins)
        # All current missing-I/O conditions map to a closed API blocker label.
        return jobs.EdaTicket(native_job_id=t.job_id,configuration_hash=self.configuration_hash,baseline_hash=t.baseline_sha256,
            candidate_plan_sha256=t.candidate_plan_sha256,blockers=('missing_io_requirements',) if t.blockers else ())
    def execute(self,ticket,guard):
        local=copy(self.runtime);factory=self.runtime.hooks_factory
        def hooks(native_id):
            h=factory(native_id)
            return replace(h,control_running=lambda:guard() and h.control_running(),cancelled=lambda:not guard() or h.cancelled())
        local.hooks_factory=hooks
        if ticket.baseline_hash!=self.baseline.investigation_sha256:raise service.Conflict('Vivado baseline drift')
        pinned=dict(expected_candidate_plan_sha256=ticket.candidate_plan_sha256)
        inspected=local.inspect(ticket.native_job_id,self.baseline,**pinned)
        if inspected['execution_status']!='prepared' or inspected['blockers']:raise service.Conflict('Vivado ticket no longer dispatchable')
        result=local.execute(ticket.native_job_id,self.baseline,**pinned)
        if result['execution_status']!='completed' or result['result_sha256'] is None:raise service.Conflict('Vivado completion unproved')
        return jobs.EdaCompletion(ticket_hash=canonical_hash(ticket),configuration_hash=self.configuration_hash,
            native_result_sha256=result['result_sha256'],retained=True,settled=True,execution_status='completed')

class KiCadDriver:
    domain='kicad'
    def __init__(self,*,root,original,candidate,review,configuration,configuration_hash,backend,reservation):
        from veriforge.kicad.native_backend import NativeBackend
        if type(backend) is not NativeBackend:raise TypeError('exact native KiCad backend required')
        self.root,self.original,self.candidate,self.review=Path(root),original,candidate,review
        self.configuration,self.configuration_hash,self.backend,self.reservation=configuration,configuration_hash,backend,reservation
    def prepare(self,app_job_id):
        from veriforge.kicad.repair_execution import digest,encode
        self.original.checked();self.candidate.checked();self.review.checked()
        baseline=digest(encode(dict(self.original.expected)))
        plan=digest(encode(dict(candidate=dict(self.candidate.expected),baseline=baseline,review=self.review.expected_sha256,configuration=self.configuration_hash)))
        return jobs.EdaTicket(native_job_id='kicad-'+app_job_id,configuration_hash=self.configuration_hash,baseline_hash=baseline,candidate_plan_sha256=plan)
    def execute(self,ticket,guard):
        from veriforge.kicad.repair_execution import execute_candidate
        if self.prepare(ticket.native_job_id.removeprefix('kicad-'))!=ticket:raise service.Conflict('KiCad ticket drift')
        root=self.root/ticket.native_job_id
        result=execute_candidate(root=root,operation_id=ticket.native_job_id,original=self.original,candidate=self.candidate,
            configuration=self.configuration,configuration_sha256=self.configuration_hash,review=self.review,backend=self.backend,
            control=guard,reservation=lambda op:guard() and self.reservation(op),cancelled=lambda:not guard())
        if result.get('execution_status')!='native_completed' or result.get('errors'):raise service.Conflict('KiCad completion unproved')
        raw=(root/'receipt.json').read_bytes()
        from veriforge.kicad.repair_execution import encode
        if raw!=encode(result):raise service.Conflict('KiCad retained result differs')
        return jobs.EdaCompletion(ticket_hash=canonical_hash(ticket),configuration_hash=self.configuration_hash,
            native_result_sha256=sha256(raw).hexdigest(),retained=True,settled=True,execution_status='completed')

@dataclass(frozen=True)
class EdaRepairRuntime:
    sessions:object
    resolver:object
    policy_reader:object
    control:object
    def policy(self):
        p=self.policy_reader()
        return jobs.EdaRepairPolicy.model_validate_json(p.model_dump_json())
    def enabled(self):
        p=self.policy()
        if not p.enabled or p.runtime_identity is None or self.control()!='RUNNING':raise service.Conflict('EDA runtime disabled')
        return p
    def ready(self):
        policy=self.enabled()
        require_eda_schema(self.sessions)
        return policy
    def driver(self,session,request,domain):
        old,new=jobs.versions(session,request,domain)
        driver=self.resolver(domain,old,new)
        if type(driver) not in (VivadoDriver,KiCadDriver) or driver.domain!=domain:raise service.Conflict('Unsupported installed EDA driver')
        return driver
    def submit(self,request,domain,key):
        policy=self.ready()
        try:
            with self.sessions.begin() as session:
                driver=self.driver(session,request,domain)
                job,fresh=jobs.reserve(session,request,domain,key,policy=policy,configuration_hash=driver.configuration_hash)
                job_id=job.id
        except IntegrityError:
            # A concurrent identical admission won the unique key. Reload in a
            # new transaction; reserve still rejects changed request identities.
            with self.sessions.begin() as session:
                driver=self.driver(session,request,domain)
                job,fresh=jobs.reserve(session,request,domain,key,policy=policy,configuration_hash=driver.configuration_hash)
                job_id=job.id
        if fresh:
            try:
                if canonical_hash(self.enabled())!=canonical_hash(policy):raise service.Conflict('EDA policy withdrawn')
                ticket=driver.prepare(job_id)
                with self.sessions.begin() as session:
                    job=jobs.owned(session,job_id,lock=True)
                    jobs.prepared(job,ticket)
                    try:allowed=canonical_hash(self.enabled())==job.policy_hash
                    except Exception:allowed=False
                    if not allowed:job.status='cancelled';job.dispatch_pending=False
            except Exception:
                with self.sessions.begin() as session:
                    job=jobs.owned(session,job_id,lock=True)
                    if job.status=='preparing':job.status='quarantined'
        return self.status(job_id)
    def status(self,job_id):
        # Installed runtimes retain observation/cancellation after policy withdrawal.
        # A default-uninstalled process is refused by configured() before DB access.
        require_eda_schema(self.sessions)
        with self.sessions() as session:return jobs.view(jobs.owned(session,job_id))
    def cancel(self,job_id):
        require_eda_schema(self.sessions)
        with self.sessions.begin() as session:return jobs.cancel(session,job_id)

_runtime=None
def install_runtime(runtime=None):
    """Trusted startup only; default disabled in every API/dispatcher/worker."""
    global _runtime
    if runtime is not None and type(runtime) is not EdaRepairRuntime:raise TypeError('trusted EDA runtime required')
    _runtime=runtime

def configured():
    if _runtime is None:raise service.Conflict('EDA runtime disabled')
    return _runtime

def dispatch_one(job_id,queue):
    runtime=configured();runtime.ready()
    with runtime.sessions.begin() as session:
        job=jobs.owned(session,job_id,lock=True)
        if job.status!='queued' or not job.dispatch_pending:return False
        policy=runtime.enabled()
        if job.cancel_requested or canonical_hash(policy)!=job.policy_hash or jobs.utc()>=job.deadline_at:
            job.status='cancelled';job.dispatch_pending=False;return False
        # Repeated delivery after uncertain enqueue is safe: only one durable claim.
        queue.enqueue('veriforge.core.eda_repair_runtime.run_eda_job',job.id,job_id='eda-'+job.id,
                      job_timeout=min(1860,policy.max_wall_seconds+60),result_ttl=0)
        job.dispatch_pending=False
    return True

def dispatch_ready(queue,*,limit=16):
    """Bounded hook for the existing dispatcher tick, not a new scheduler."""
    from .eda_repair_models import EdaRepairJob
    if type(limit) is not int or not 1<=limit<=64:raise ValueError('bounded EDA dispatch batch required')
    runtime=configured();runtime.ready()
    with runtime.sessions() as session:
        ids=list(session.scalars(select(EdaRepairJob.id).where(EdaRepairJob.status=='queued',EdaRepairJob.dispatch_pending.is_(True)).order_by(EdaRepairJob.created_at,EdaRepairJob.id).limit(limit)))
    sent=failed=0
    for identity in ids:
        try:sent+=int(dispatch_one(identity,queue))
        except Exception:failed+=1
    return {'examined':len(ids),'sent':sent,'failed':failed}

def run_eda_job(job_id):
    runtime=configured()
    runtime.ready()
    with runtime.sessions.begin() as session:
        job=jobs.owned(session,job_id,lock=True)
        token=jobs.claim(job,policy=runtime.policy(),running=runtime.control()=='RUNNING')
        if token is None:return jobs.view(job)
        request=jobs.EdaRepairRequest(project_id=job.project_id,original_version_id=job.original_version_id,candidate_version_id=job.candidate_version_id)
        ticket=jobs.EdaTicket.model_validate(job.ticket);domain=job.domain
    def guard():
        try:
            p=runtime.enabled()
            with runtime.sessions() as session:
                row=jobs.owned(session,job_id)
                return row.status=='claimed' and row.claim_token==token and not row.cancel_requested and jobs.utc()<row.deadline_at and canonical_hash(p)==row.policy_hash and canonical_hash(jobs.EdaTicket.model_validate(row.ticket))==row.ticket_hash
        except Exception:return False
    try:
        with runtime.sessions() as session:
            driver=runtime.driver(session,request,domain)
            row=jobs.owned(session,job_id)
            if driver.configuration_hash!=row.configuration_hash:raise service.Conflict('EDA configuration drift')
        if not guard():raise service.Conflict('EDA grant withdrawn')
        result=driver.execute(ticket,guard)
        with runtime.sessions.begin() as session:
            row=jobs.owned(session,job_id,lock=True)
            # Lock acquisition can block. Read all mutable authority AFTER it;
            # retain a validated native effect even when completion is fenced.
            try:
                policy=runtime.enabled()
                authorized=(not row.cancel_requested and jobs.utc()<row.deadline_at and
                    canonical_hash(policy)==row.policy_hash)
            except Exception:authorized=False
            jobs.finish(row,token,result,authorized=authorized)
    except Exception:
        with runtime.sessions.begin() as session:
            row=jobs.owned(session,job_id,lock=True)
            if row.status=='claimed' and row.claim_token==token:row.status='quarantined'
    return runtime.status(job_id)

def quarantine_uncertain(job_id):
    """Explicit recovery only; never steals a claim or re-enqueues native work."""
    runtime=configured()
    require_eda_schema(runtime.sessions)
    with runtime.sessions.begin() as session:
        row=jobs.owned(session,job_id,lock=True)
        if row.status in ('claimed','preparing'):row.status='quarantined';row.dispatch_pending=False
        return jobs.view(row)
