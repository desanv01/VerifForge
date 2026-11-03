"""Transactional ID-only native candidate state machine; no native dispatch."""
from datetime import datetime,timedelta,timezone
from typing import Annotated,Literal
from pydantic import Field,StrictBool,StrictInt
from sqlalchemy import select
from veriforge.contracts import Identifier,Sha256,WireModel,canonical_hash
from .database import InvestigationVersion,Project,identifier,now
from . import investigations,service
from .eda_repair_models import EdaRepairJob

class EdaRepairRequest(WireModel):
    project_id:Identifier
    original_version_id:Identifier
    candidate_version_id:Identifier

class EdaRepairPolicy(WireModel):
    enabled:StrictBool=False
    runtime_identity:Sha256|None=None
    max_wall_seconds:Annotated[StrictInt,Field(ge=1,le=1800)]=600

class EdaTicket(WireModel):
    native_job_id:Identifier
    configuration_hash:Sha256
    baseline_hash:Sha256
    candidate_plan_sha256:Sha256
    blockers:Annotated[tuple[Literal['missing_io_requirements','unreviewed_mapping','unsupported_configuration'],...],Field(max_length=3)]=()

class EdaCompletion(WireModel):
    ticket_hash:Sha256
    configuration_hash:Sha256
    native_result_sha256:Sha256
    retained:Literal[True]
    settled:Literal[True]
    execution_status:Literal['completed','failed','cancelled']
    design_verdict:Literal['not_evaluated']='not_evaluated'

def utc(at=None):
    at=now() if at is None else at
    if not isinstance(at,datetime) or at.tzinfo is None:raise service.Conflict('EDA clock unavailable')
    return at.astimezone(timezone.utc)

def versions(session,request,domain=None):
    project=session.get(Project,request.project_id)
    if project is None or project.owner!='local':raise service.NotFound(request.project_id)
    rows=[]
    for key in (request.original_version_id,request.candidate_version_id):
        row=session.get(InvestigationVersion,key)
        if row is None or row.project_id!=project.id:raise service.NotFound(key)
        investigations._version(session,row.investigation_id,row.id)
        envelope=investigations._envelope(row)
        if envelope.schema_version!='engineering.admission.v3' or (domain is not None and envelope.record.domain!=domain):
            raise service.Conflict('Unsupported EDA version domain/schema')
        rows.append(row)
    if rows[0].id==rows[1].id:raise service.Conflict('Distinct EDA versions required')
    return rows

def owned(session,job_id,*,lock=False):
    job=session.scalar(select(EdaRepairJob).where(EdaRepairJob.id==job_id).with_for_update()) if lock else session.get(EdaRepairJob,job_id)
    if job is None or job.owner!='local':raise service.NotFound(job_id)
    old,new=versions(session,EdaRepairRequest(project_id=job.project_id,original_version_id=job.original_version_id,candidate_version_id=job.candidate_version_id),job.domain)
    if (old.content_hash,new.content_hash)!=(job.original_hash,job.candidate_hash):raise service.Conflict('EDA version drift')
    return job

def reserve(session,request,domain,key,*,policy,configuration_hash,at=None):
    request=EdaRepairRequest.model_validate_json(request.model_dump_json())
    if domain not in ('vivado','kicad') or not policy.enabled or policy.runtime_identity is None:raise service.Conflict('EDA runtime disabled')
    # Key validation uses the same bounded ID rules as all other request identities.
    from pydantic import TypeAdapter
    key=TypeAdapter(Identifier).validate_python(key)
    old,new=versions(session,request,domain)
    identity=canonical_hash(request)
    prior=session.scalar(select(EdaRepairJob).where(EdaRepairJob.owner=='local',EdaRepairJob.create_key==key))
    if prior:
        if (prior.request_hash,prior.domain)!=(identity,domain):raise service.Conflict('EDA idempotency conflict')
        return prior,False
    from veriforge.contracts.engineering import EvidenceReference
    EvidenceReference(artifact_id='configuration',revision_id='host',sha256=configuration_hash)
    job=EdaRepairJob(id=identifier(),owner='local',create_key=key,request_hash=identity,project_id=request.project_id,
        original_version_id=old.id,candidate_version_id=new.id,original_hash=old.content_hash,candidate_hash=new.content_hash,
        domain=domain,configuration_hash=configuration_hash,policy_hash=canonical_hash(policy),status='preparing',
        dispatch_pending=False,cancel_requested=False,deadline_at=utc(at)+timedelta(seconds=policy.max_wall_seconds))
    session.add(job);session.flush()
    return job,True

def prepared(job,ticket):
    ticket=EdaTicket.model_validate_json(ticket.model_dump_json())
    if job.status not in ('preparing','cancelled'):raise service.Conflict('EDA preparation no longer owned')
    if ticket.configuration_hash!=job.configuration_hash:raise service.Conflict('EDA configuration drift')
    job.ticket=ticket.model_dump(mode='json');job.ticket_hash=canonical_hash(ticket)
    job.status='cancelled' if job.cancel_requested else 'blocked' if ticket.blockers else 'queued'
    job.dispatch_pending=job.status=='queued'

def claim(job,*,policy,at=None,running=True):
    if job.status!='queued':return None
    if not running or job.cancel_requested or canonical_hash(policy)!=job.policy_hash or not policy.enabled or utc(at)>=job.deadline_at:
        job.status='cancelled';job.dispatch_pending=False;return None
    ticket=EdaTicket.model_validate(job.ticket)
    if canonical_hash(ticket)!=job.ticket_hash or ticket.configuration_hash!=job.configuration_hash or ticket.blockers:
        job.status='quarantined';job.dispatch_pending=False;return None
    job.claim_token=identifier();job.status='claimed';job.dispatch_pending=False
    return job.claim_token

def finish(job,token,result,*,authorized=True):
    if job.status!='claimed' or job.claim_token!=token:raise service.Conflict('Stale EDA claim')
    result=EdaCompletion.model_validate_json(result.model_dump_json())
    if result.ticket_hash!=job.ticket_hash or result.configuration_hash!=job.configuration_hash:raise service.Conflict('EDA result binding drift')
    job.result=result.model_dump(mode='json')
    job.status='quarantined' if authorized is not True else 'cancelled' if job.cancel_requested else result.execution_status

def cancel(session,job_id):
    job=owned(session,job_id,lock=True);job.cancel_requested=True
    if job.status in ('preparing','queued','blocked'):job.status='cancelled';job.dispatch_pending=False
    return view(job)

def view(job):
    ticket=EdaTicket.model_validate(job.ticket) if job.ticket else None
    result=EdaCompletion.model_validate(job.result).model_dump(mode='json') if job.result else None
    return dict(job_id=job.id,domain=job.domain,project_id=job.project_id,original_version_id=job.original_version_id,
        candidate_version_id=job.candidate_version_id,status=job.status,cancel_requested=job.cancel_requested,
        blockers=list(ticket.blockers) if ticket else [],result=result,
        design_verdict='not_evaluated',physical_electrical_verdict='inconclusive')
