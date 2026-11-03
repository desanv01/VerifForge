"""Pure durable-state/session fakes; no engine, SQLite, queue or native job."""
from contextlib import contextmanager
from datetime import datetime,timezone,timedelta
from types import SimpleNamespace
import pytest
from veriforge.contracts import canonical_hash
from veriforge.core import eda_repair_jobs as j,eda_repair_runtime as r,service
from veriforge.core.database import Project,InvestigationVersion
from veriforge.core.eda_repair_models import EdaRepairJob

AT=datetime.now(timezone.utc)
P=j.EdaRepairPolicy(enabled=True,runtime_identity='a'*64)
T=j.EdaTicket(native_job_id='native-one',configuration_hash='b'*64,baseline_hash='c'*64,candidate_plan_sha256='d'*64)

class Sessions:
    def __init__(self):self.jobs={}
    @contextmanager
    def begin(self):yield self
    def __call__(self):return self.begin()
    def get(self,model,key):
        if model is EdaRepairJob:return self.jobs.get(key)
        if model is Project:return SimpleNamespace(id='p',owner='local') if key=='p' else None
        if model is InvestigationVersion and key in ('old','new'):
            return SimpleNamespace(id=key,project_id='p',investigation_id='i',content_hash=('1' if key=='old' else '2')*64)
    def scalar(self,query):
        args=query.compile().params
        if 'create_key_1' in args:return next((v for v in self.jobs.values() if v.create_key==args['create_key_1']),None)
        return self.jobs.get(args.get('id_1'))
    def add(self,row):self.jobs[row.id]=row
    def flush(self):pass

@pytest.fixture(autouse=True)
def no_effects(monkeypatch):
    import socket,sqlite3,subprocess,ctypes
    def denied(*args,**kwargs):pytest.fail('External/native/DB operation forbidden')
    for obj,name in ((socket,'create_connection'),(sqlite3,'connect'),(subprocess,'Popen'),(subprocess,'run'),(ctypes,'WinDLL')):
        monkeypatch.setattr(obj,name,denied,raising=False)
    # Schema inspection has its own fake-reflection and boundary tests.
    monkeypatch.setattr(r,'require_eda_schema',lambda sessions:None)
    monkeypatch.setattr(j.investigations,'_version',lambda *a:None)
    monkeypatch.setattr(j.investigations,'_envelope',lambda *a:SimpleNamespace(schema_version='engineering.admission.v3',record=SimpleNamespace(domain='vivado')))
    r.install_runtime(None)
    yield
    r.install_runtime(None)

def request(**updates):return j.EdaRepairRequest(project_id='p',original_version_id='old',candidate_version_id='new',**updates)
def reserved(s,key='key'):
    return j.reserve(s,request(),'vivado',key,policy=P,configuration_hash='b'*64,at=AT)[0]

def test_request_only_ids_and_disabled_policy():
    with pytest.raises(ValueError):j.EdaRepairRequest(**request().model_dump(),command='shell')
    with pytest.raises(service.Conflict):j.reserve(Sessions(),request(),'vivado','k',policy=j.EdaRepairPolicy(),configuration_hash='b'*64)

def test_idempotency_changed_domain_conflict_and_foreign_versions():
    s=Sessions();job=reserved(s)
    same,fresh=j.reserve(s,request(),'vivado','key',policy=P,configuration_hash='b'*64)
    assert same.id==job.id and fresh is False
    with pytest.raises(service.Conflict):j.reserve(s,request(),'kicad','key',policy=P,configuration_hash='b'*64)
    with pytest.raises(service.NotFound):j.reserve(s,request().model_copy(update={'candidate_version_id':'foreign'}),'vivado','x',policy=P,configuration_hash='b'*64)
    assert len(s.jobs)==1

def test_blocked_ticket_never_claimed_or_dispatched():
    s=Sessions();job=reserved(s);j.prepared(job,T.model_copy(update={'blockers':('missing_io_requirements',)}))
    assert job.status=='blocked' and job.dispatch_pending is False
    assert j.claim(job,policy=P) is None

@pytest.mark.parametrize('change',['config','ticket','policy','pause','expiry','cancel'])
def test_fences_before_effect(change):
    s=Sessions();job=reserved(s)
    if change=='config':
        with pytest.raises(service.Conflict):j.prepared(job,T.model_copy(update={'configuration_hash':'e'*64}))
        return
    j.prepared(job,T)
    p=P;running=True;at=AT
    if change=='ticket':job.ticket['candidate_plan_sha256']='f'*64
    elif change=='policy':p=P.model_copy(update={'runtime_identity':'e'*64})
    elif change=='pause':running=False
    elif change=='expiry':at=AT+timedelta(seconds=601)
    elif change=='cancel':job.cancel_requested=True
    assert j.claim(job,policy=p,running=running,at=at) is None
    assert job.status in ('cancelled','quarantined')

def test_duplicate_and_stale_claim_never_take_over():
    s=Sessions();job=reserved(s);j.prepared(job,T)
    token=j.claim(job,policy=P,at=AT)
    assert token and j.claim(job,policy=P,at=AT) is None
    result=j.EdaCompletion(ticket_hash=canonical_hash(T),configuration_hash='b'*64,native_result_sha256='9'*64,retained=True,settled=True,execution_status='completed')
    with pytest.raises(service.Conflict):j.finish(job,'stale',result)
    j.finish(job,token,result)
    assert j.view(job)['physical_electrical_verdict']=='inconclusive'

def test_worker_cancellation_reaches_guard_and_uncertainty_quarantines():
    s=Sessions();job=reserved(s);j.prepared(job,T)
    driver=object.__new__(r.VivadoDriver);driver.configuration_hash='b'*64
    seen=[]
    def execute(ticket,guard):
        assert ticket.candidate_plan_sha256=='d'*64 and guard()
        j.cancel(s,job.id)
        seen.append(guard())
        raise RuntimeError('ambiguous native effect')
    driver.execute=execute
    runtime=r.EdaRepairRuntime(s,lambda *a:driver,lambda:P,lambda:'RUNNING')
    r.install_runtime(runtime)
    result=r.run_eda_job(job.id)
    assert seen==[False] and result['status']=='quarantined'
    assert r.run_eda_job(job.id)['status']=='quarantined'

def test_id_only_outbox_dispatch_and_no_resend_after_claim():
    s=Sessions();job=reserved(s);j.prepared(job,T)
    r.install_runtime(r.EdaRepairRuntime(s,None,lambda:P,lambda:'RUNNING'))
    calls=[];queue=SimpleNamespace(enqueue=lambda *a,**k:calls.append((a,k)))
    assert r.dispatch_one(job.id,queue) and not r.dispatch_one(job.id,queue)
    assert calls[0][0]==('veriforge.core.eda_repair_runtime.run_eda_job',job.id)
    assert calls[0][1]['job_timeout']==P.max_wall_seconds+60 and calls[0][1]['result_ttl']==0
    assert 'candidate_plan_sha256' not in str(calls)

def test_vivado_driver_passes_external_plan_identity_to_inspect_and_execute():
    driver=object.__new__(r.VivadoDriver);driver.configuration_hash='b'*64
    driver.baseline=SimpleNamespace(investigation_sha256='c'*64)
    seen=[]
    class NativeFake:
        hooks_factory=None
        def inspect(self,*args,**kwargs):seen.append(kwargs);return {'execution_status':'prepared','blockers':[]}
        def execute(self,*args,**kwargs):seen.append(kwargs);return {'execution_status':'completed','result_sha256':'9'*64}
    driver.runtime=NativeFake()
    assert driver.execute(T,lambda:True).retained is True
    assert seen==[{'expected_candidate_plan_sha256':'d'*64}]*2

def test_application_submit_persists_one_ticket_and_cancelled_preparation_identity():
    s=Sessions();calls=[]
    driver=object.__new__(r.VivadoDriver);driver.configuration_hash='b'*64
    driver.prepare=lambda identity:calls.append(identity) or T
    runtime=r.EdaRepairRuntime(s,lambda *a:driver,lambda:P,lambda:'RUNNING')
    first=runtime.submit(request(),'vivado','request-one')
    assert runtime.submit(request(),'vivado','request-one')['job_id']==first['job_id']
    assert len(calls)==1 and s.jobs[first['job_id']].ticket['candidate_plan_sha256']=='d'*64
    job=reserved(s,'request-two');j.cancel(s,job.id);j.prepared(job,T)
    assert job.status=='cancelled' and job.ticket_hash==canonical_hash(T) and not job.dispatch_pending

def test_unknown_version_fails_before_trusted_resolver_or_preparation():
    calls=[];runtime=r.EdaRepairRuntime(Sessions(),lambda *a:calls.append(a),lambda:P,lambda:'RUNNING')
    with pytest.raises(service.NotFound):runtime.submit(request().model_copy(update={'candidate_version_id':'foreign'}),'vivado','key')
    assert calls==[]

def test_expired_or_cancelled_queued_job_never_enqueues():
    for expired in (True,False):
        s=Sessions();job=reserved(s);j.prepared(job,T)
        if expired:job.deadline_at=AT-timedelta(seconds=1)
        else:job.cancel_requested=True
        r.install_runtime(r.EdaRepairRuntime(s,None,lambda:P,lambda:'RUNNING'))
        calls=[]
        assert not r.dispatch_one(job.id,SimpleNamespace(enqueue=lambda *a,**kw:calls.append(a)))
        assert calls==[] and job.status=='cancelled' and not job.dispatch_pending

@pytest.mark.parametrize('withdrawal',['policy','control','deadline','cancel'])
def test_completion_rechecks_after_final_lock_and_retains_effect(monkeypatch,withdrawal):
    s=Sessions();job=reserved(s);j.prepared(job,T);policy=[P];running=['RUNNING'];executed=[]
    driver=object.__new__(r.VivadoDriver);driver.configuration_hash='b'*64
    def execute(ticket,guard):
        assert guard();executed.append(True)
        return j.EdaCompletion(ticket_hash=canonical_hash(ticket),configuration_hash='b'*64,native_result_sha256='9'*64,
                               retained=True,settled=True,execution_status='completed')
    driver.execute=execute
    original=j.owned
    def acquire(session,identity,*,lock=False):
        row=original(session,identity,lock=lock)
        if lock and executed:
            if withdrawal=='policy':policy[0]=j.EdaRepairPolicy()
            elif withdrawal=='control':running[0]='STOPPED'
            elif withdrawal=='deadline':row.deadline_at=AT-timedelta(seconds=1)
            else:row.cancel_requested=True
        return row
    monkeypatch.setattr(j,'owned',acquire)
    r.install_runtime(r.EdaRepairRuntime(s,lambda *a:driver,lambda:policy[0],lambda:running[0]))
    result=r.run_eda_job(job.id)
    assert result['status']=='quarantined' and result['result']['native_result_sha256']=='9'*64
    assert job.claim_token is not None and not job.dispatch_pending

def test_immutable_nullable_fields_preserve_null_transition_and_refuse_unknown_prior():
    from sqlalchemy import inspect
    from sqlalchemy.orm import attributes,make_transient_to_detached
    from sqlalchemy.orm.exc import DetachedInstanceError
    from veriforge.core.eda_repair_models import _immutable
    for field in ('ticket','ticket_hash','claim_token','result'):
        replacement={'record':'new'} if field in ('ticket','result') else 'new'
        row=EdaRepairJob(id='null-row');attributes.set_committed_value(row,field,None)
        make_transient_to_detached(row);setattr(row,field,replacement)
        _immutable(None,None,row) # Known NULL -> first value is allowed.
        row=EdaRepairJob(id='existing');attributes.set_committed_value(row,field,replacement)
        make_transient_to_detached(row);inspect(row)._expire_attributes(row.__dict__,[field])
        with pytest.raises((DetachedInstanceError,ValueError)):
            setattr(row,field,replacement);_immutable(None,None,row)
        # A history with an unknown old value must also refuse, regardless of
        # whether a caller bypassed the normal active-history attribute setter.
        row.__dict__[field]=replacement;attributes.flag_modified(row,field)
        with pytest.raises(ValueError,match='immutable EDA'):_immutable(None,None,row)
