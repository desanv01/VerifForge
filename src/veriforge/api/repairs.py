"""ID-only local repair routes. No tool/dispatcher/worker runs in HTTP handlers."""
from typing import Annotated

from fastapi import APIRouter, Header, HTTPException
from sqlalchemy.exc import IntegrityError

from veriforge.contracts import canonical_hash
from veriforge.core import repair_jobs as jobs, service
from veriforge.core.database import now

Key = Annotated[str, Header(alias="Idempotency-Key", min_length=1, max_length=128)]


def repairs_router(sessions, *, policy=None, policy_reader=None, control=None, root=None, clock=now):
    policy = policy or jobs.RepairPolicy()
    router = APIRouter(prefix="/api")

    def current_policy():
        if policy_reader is None:
            return policy
        try:
            value = policy_reader()
            if type(value) is jobs.RepairPolicy:
                return jobs.RepairPolicy.model_validate(value.model_dump(mode='json'))
        except Exception:
            pass
        return jobs.RepairPolicy()

    def error(problem):
        if isinstance(problem, jobs.RepairDisabled):
            return HTTPException(503, "Repair runtime is disabled or not granted")
        if isinstance(problem, service.NotFound):
            return HTTPException(404, "Repair or original attempt not found")
        if isinstance(problem, (service.Conflict, IntegrityError)):
            return HTTPException(409, "Repair request or evidence conflicts with durable state")
        return HTTPException(422, "Unsupported repair request")

    @router.post("/attempts/{attempt_id}/repairs", status_code=202)
    def admit(attempt_id: str, body: jobs.RepairRequest, idempotency_key: Key):
        try:
            admitted_policy = current_policy()

            def admission_control():
                # Evidence/suite validation can take time. Policy withdrawal or
                # a changed runtime/budget identity must fence the final check
                # before the service persists its dispatch intent, too.
                if canonical_hash(current_policy()) != canonical_hash(admitted_policy):
                    return "STOPPED"
                return control() if callable(control) else "STOPPED"

            with sessions.begin() as session:
                job = jobs.admit(session, attempt_id, idempotency_key, body, policy=admitted_policy,
                                 control=admission_control, root=root, at=clock())
                return jobs.status(session, job.id)
        except (jobs.RepairDisabled, service.NotFound, service.Conflict, ValueError, IntegrityError) as problem:
            raise error(problem) from None

    @router.get("/repairs/{job_id}")
    def status(job_id: str):
        try:
            with sessions() as session:
                return jobs.status(session, job_id)
        except (service.NotFound, service.Conflict) as problem:
            raise error(problem) from None

    @router.get("/repairs/{job_id}/report")
    def report(job_id: str):
        try:
            with sessions() as session:
                return jobs.report(session, job_id)
        except (service.NotFound, service.Conflict) as problem:
            raise error(problem) from None

    @router.post("/repairs/{job_id}/cancel")
    def cancel(job_id: str):
        try:
            with sessions.begin() as session:
                jobs.cancel(session, job_id, at=clock())
                return jobs.status(session, job_id)
        except (service.NotFound, service.Conflict, ValueError) as problem:
            raise error(problem) from None

    return router
