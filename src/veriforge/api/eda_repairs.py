"""ID-only EDA repair router; no public startup/runtime/grant configuration."""
from typing import Annotated,Literal
from fastapi import APIRouter,Header,HTTPException
from sqlalchemy.exc import IntegrityError
from veriforge.core import eda_repair_jobs as jobs,eda_repair_runtime as runtime,service

def create_router():
    router=APIRouter()
    def call(function,*args):
        try:return function(*args)
        except service.NotFound:raise HTTPException(404,'EDA version or job not found') from None
        except (service.Conflict,IntegrityError):raise HTTPException(409,'EDA request conflicts or runtime unavailable') from None
        except (ValueError,TypeError):raise HTTPException(422,'Unsupported EDA request') from None
    @router.post('/eda-repairs/{domain}',status_code=202)
    def submit(domain:Literal['vivado','kicad'],body:jobs.EdaRepairRequest,
               idempotency_key:Annotated[str,Header(min_length=1,max_length=128)]):
        return call(lambda:runtime.configured().submit(body,domain,idempotency_key))
    @router.get('/eda-repairs/jobs/{job_id}')
    def status(job_id:str):return call(lambda:runtime.configured().status(job_id))
    @router.get('/eda-repairs/jobs/{job_id}/report')
    def report(job_id:str):return call(lambda:runtime.configured().status(job_id))
    @router.post('/eda-repairs/jobs/{job_id}/cancel')
    def cancel(job_id:str):return call(lambda:runtime.configured().cancel(job_id))
    return router
