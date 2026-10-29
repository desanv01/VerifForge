from __future__ import annotations

import asyncio
import json
from contextlib import asynccontextmanager
from typing import Annotated

from fastapi import FastAPI, Header, HTTPException, Query, Request
from fastapi.encoders import jsonable_encoder
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, ConfigDict, Field, model_validator
from typing import Literal
from veriforge.adapters import ValidatedExperiment, get_adapter
from sqlalchemy import select, text
from sqlalchemy.exc import IntegrityError

from veriforge.core import service
from veriforge.core.database import Campaign, Contract, Finding, Project, RunEvent, make_engine, session_factory


class CreateCampaign(BaseModel):
    model_config = ConfigDict(extra="forbid")
    project_id: Annotated[str, Field(min_length=1, max_length=128)]
    adapter_id: Literal["rtl.fifo.v1", "rtl.arithmetic.v1", "rtl.axi_lite.v1"] = "rtl.fifo.v1"
    plan: Annotated[ValidatedExperiment, Field(discriminator="schema_version")]

    @model_validator(mode="before")
    @classmethod
    def legacy_fifo(cls, value):
        if isinstance(value, dict) and value.get("adapter_id", "rtl.fifo.v1") == "rtl.fifo.v1" and isinstance(value.get("plan"), dict):
            value = {**value, "plan": {"schema_version": "1", **value["plan"]}}
        return value

    @model_validator(mode="after")
    def installed_family(self):
        get_adapter(self.adapter_id).validate_experiment(self.plan)
        return self


Key = Annotated[str, Header(alias="Idempotency-Key", min_length=1, max_length=128)]


def create_app(engine=None, *, initialize_catalog=True, artifact_root=None):
    engine = engine if engine is not None else make_engine()
    sessions = session_factory(engine)

    @asynccontextmanager
    async def lifespan(app):
        if initialize_catalog:
            from veriforge.core.catalog import seed_catalog
            with sessions.begin() as session:
                seed_catalog(session)
        yield

    app = FastAPI(title="VerifForge local API", version="0.1.0", lifespan=lifespan)
    app.state.sessions = sessions
    from .evidence import evidence_router
    app.include_router(evidence_router(sessions, artifact_root))
    from .investigations import investigations_router
    app.include_router(investigations_router(sessions))

    @app.exception_handler(service.NotFound)
    async def missing(request, error):
        from fastapi.responses import JSONResponse
        return JSONResponse({"detail": "Not found"}, status_code=404)

    @app.exception_handler(service.Conflict)
    async def conflict(request, error):
        from fastapi.responses import JSONResponse
        return JSONResponse({"detail": str(error)}, status_code=409)

    @app.get("/api/health")
    def health():
        with sessions() as session:
            session.execute(text("SELECT 1"))
        from veriforge.core.artifacts import EVIDENCE_ADAPTERS
        return {"status": "ok", "mode": "local", "execution": "separate_worker", "model": "not_integrated", "evidence_api": "1", "investigation_api": "1", "evidence_adapters": sorted(EVIDENCE_ADAPTERS)}

    @app.get("/api/projects")
    def projects():
        with sessions() as session:
            return {"items": [p.data for p in session.scalars(select(Project).where(Project.owner == "local").order_by(Project.id))]}

    @app.get("/api/projects/{project_id}")
    def project(project_id: str):
        with sessions() as session:
            row = session.get(Project, project_id)
            if not row or row.owner != "local":
                raise service.NotFound(project_id)
            return row.data

    @app.get("/api/contracts/{contract_id}")
    def contract(contract_id: str):
        with sessions() as session:
            row = session.get(Contract, contract_id)
            if not row:
                raise service.NotFound(contract_id)
            return row.data

    @app.post("/api/campaigns", status_code=201)
    def create(payload: CreateCampaign, idempotency_key: Key):
        if payload.plan.fixture_id != payload.project_id:
            raise HTTPException(422, "Plan fixture_id must match project_id")
        # A duplicate racing the unique key insert must retrieve the committed row.
        for attempt in range(2):
            try:
                with sessions.begin() as session:
                    row = service.create_campaign(session, payload.project_id, payload.plan.model_dump(mode="json"), idempotency_key, adapter_id=payload.adapter_id)
                    session.flush()
                    return service.campaign_view(session, row)
            except IntegrityError:
                if attempt:
                    raise

    @app.get("/api/campaigns")
    def campaigns(limit: int = Query(50, ge=1, le=200)):
        with sessions() as session:
            rows = session.scalars(select(Campaign).where(Campaign.owner == "local").order_by(Campaign.created_at.desc()).limit(limit))
            return {"items": [service.campaign_view(session, row) for row in rows]}

    @app.get("/api/campaigns/{campaign_id}")
    def campaign(campaign_id: str):
        with sessions() as session:
            return service.campaign_view(session, service.owned(session, campaign_id))

    @app.post("/api/campaigns/{campaign_id}/start")
    def start(campaign_id: str, idempotency_key: Key):
        with sessions.begin() as session:
            row = service.start(session, campaign_id)
            session.flush()
            return service.campaign_view(session, row)

    @app.post("/api/campaigns/{campaign_id}/cancel")
    def cancel(campaign_id: str, idempotency_key: Key):
        with sessions.begin() as session:
            row = service.cancel(session, campaign_id)
            session.flush()
            return service.campaign_view(session, row)

    def read_events(campaign_id, after):
        with sessions() as session:
            service.owned(session, campaign_id)
            rows = session.scalars(select(RunEvent).where(RunEvent.campaign_id == campaign_id, RunEvent.sequence > after).order_by(RunEvent.sequence).limit(200))
            return [{"sequence": r.sequence, "kind": r.kind, "payload": r.payload, "created_at": r.created_at} for r in rows]

    @app.get("/api/campaigns/{campaign_id}/events")
    def events(campaign_id: str, after: int = Query(0, ge=0)):
        return {"items": read_events(campaign_id, after)}

    @app.get("/api/campaigns/{campaign_id}/events/stream")
    def stream(campaign_id: str, request: Request, after: int = Query(0, ge=0), last_event_id: Annotated[str | None, Header()] = None):
        if last_event_id is not None:
            try:
                after = max(after, int(last_event_id))
            except ValueError:
                raise HTTPException(422, "Last-Event-ID must be an integer")
        with sessions() as session:
            service.owned(session, campaign_id)

        async def generate():
            cursor = after
            while not await request.is_disconnected():
                batch = await asyncio.to_thread(read_events, campaign_id, cursor)
                for event in batch:
                    cursor = event["sequence"]
                    yield f"id: {cursor}\nevent: {event['kind']}\ndata: {json.dumps(jsonable_encoder(event))}\n\n"
                with sessions() as session:
                    row = service.owned(session, campaign_id)
                    if row.execution_status in service.TERMINAL and cursor >= row.event_sequence:
                        break
                yield ": keepalive\n\n"
                await asyncio.sleep(1)

        return StreamingResponse(generate(), media_type="text/event-stream", headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})

    @app.get("/api/findings/{finding_id}")
    def finding(finding_id: str):
        with sessions() as session:
            row = session.get(Finding, finding_id)
            if not row:
                raise service.NotFound(finding_id)
            service.owned(session, row.campaign_id)
            return {"id": row.id, "campaign_id": row.campaign_id, "attempt_id": row.attempt_id, **row.data}

    return app


app = create_app()
