"""Two-stage local admission only. No provider construction or model execution."""
from __future__ import annotations

from collections.abc import Callable
from datetime import datetime, timezone
from hashlib import sha256
from typing import Annotated, Literal

from fastapi import APIRouter, Header, HTTPException, Query
from pydantic import Field, StrictBool, StrictInt, model_validator
from sqlalchemy import select, update
from sqlalchemy.exc import IntegrityError

from veriforge.adapters import get_adapter
from veriforge.contracts import Experiment, Identifier, WireModel, canonical_hash
from veriforge.core import planner_rounds as rounds, service
from veriforge.core.database import (
    Attempt, Contract, ExperimentRecord, PlannerCleanup, PlannerRound, PlannerState, Project,
)
from veriforge.core.families import FIFO_ADAPTER, project_adapter
from veriforge.core.planner_experiments import validate_record
from veriforge.planner.models import InvestigationLimits, Pricing


class PlannerPolicy(WireModel):
    """Server configuration, never accepted from a public request."""

    enabled: StrictBool = False
    strategy: Literal["meta"] = "meta"
    requested_model: Annotated[str, Field(pattern=r"^[A-Za-z0-9][A-Za-z0-9_.:/-]{0,127}$")] | None = None
    limits: InvestigationLimits = Field(default_factory=InvestigationLimits)
    pricing: Pricing | None = None
    max_seed_artifact_bytes: Annotated[StrictInt, Field(ge=1024, le=1_073_741_824)] = 10_485_760

    @model_validator(mode="after")
    def explicit_model_and_price(self):
        if self.enabled and self.requested_model is None:
            raise ValueError("Enabled admission requires an explicit server model")
        if self.limits.max_cost_usd is not None and self.pricing is None:
            raise ValueError("A dollar cap requires explicit server pricing")
        return self


class PlannerSeedRequest(WireModel):
    schema_version: Literal["planner.seed.v1"]
    project_id: Identifier
    plan: Experiment


class PlannerAttachRequest(WireModel):
    schema_version: Literal["planner.attach.v1"]


Key = Annotated[str, Header(alias="Idempotency-Key", min_length=1, max_length=128)]
# Round ordinals use SQL Integer, including PostgreSQL's signed 32-bit range.
Cursor = Annotated[int, Query(ge=0, le=2**31 - 1)]
PageSize = Annotated[int, Query(ge=1, le=100)]


def _project(session, project_id):
    project = session.get(Project, project_id)
    if project is None or project.owner != "local":
        raise service.NotFound(project_id)
    descriptor = get_adapter(FIFO_ADAPTER)
    if project_adapter(project).adapter_id != FIFO_ADAPTER or project.id not in descriptor.fixture_ids:
        raise service.Conflict("Planner admission supports curated FIFO fixtures only")
    return project


def _lock_project(session, project_id):
    """Hold current project authority through commit; take before Campaign.

    HTTP admission never takes the global resource lock. When present, the
    authority order is resource -> project -> campaign, never its reverse.
    The owned UPDATE blocks concurrent ownership changes in PostgreSQL as well
    as serializing SQLite writers; a reread alone does not hold that authority.
    """
    found = session.execute(update(Project).where(Project.id == project_id, Project.owner == "local").values(id=Project.id))
    if not found.rowcount:
        raise service.NotFound(project_id)
    session.expire_all()
    return _project(session, project_id)


def _seed_binding(session, project, experiment, policy):
    descriptor = get_adapter(FIFO_ADAPTER)
    descriptor.validate_experiment(experiment)
    if experiment.fixture_id != project.id or experiment.contract_id != project.contract_id:
        raise service.Conflict("Seed does not match the curated project")
    source = descriptor.fixture_source(project.id)
    if any(project.data.get(key) != value for key, value in source.items()):
        raise service.Conflict("Curated source identity changed")
    row = session.get(Contract, project.contract_id)
    if row is None:
        raise service.Conflict("Reviewed contract is unavailable")
    contract = descriptor.contract_model.model_validate(row.data).model_copy(update={"parameters": experiment.parameters})
    native = descriptor.contract_for(experiment)
    if (contract.review_status != "reviewed" or canonical_hash(contract) != canonical_hash(native)
            or row.identity.get("contract_hash") != canonical_hash(descriptor.reviewed_contract())
            or row.identity.get("checker_hash") != native.checker.sha256):
        raise service.Conflict("Reviewed contract identity changed")
    if not set(experiment.objective_ids) <= {item.requirement_id for item in contract.requirements}:
        raise service.Conflict("Seed objectives are outside reviewed requirements")
    limits, budget = policy.limits, experiment.budget
    if (budget.max_cycles > min(limits.max_cycles_per_experiment, limits.max_total_cycles)
            or budget.wall_time_seconds > min(limits.max_execution_seconds, limits.max_wall_seconds)
            or budget.max_artifact_bytes > policy.max_seed_artifact_bytes):
        raise service.Conflict("Seed exceeds the server execution allowance")


def _utc(value):
    # SQLite drops timezone metadata from DateTime(timezone=True) on reload.
    return value.replace(tzinfo=timezone.utc) if value is not None and value.tzinfo is None else value


def _status(campaign, state, policy):
    return {
        "schema_version": "planner.status.v1", "campaign_id": campaign.id,
        "execution_status": campaign.execution_status, "verdict": campaign.verdict,
        "admission_enabled": policy.enabled, "strategy": state.strategy,
        "requested_model": state.requested_model, "phase": state.phase,
        "generation": state.generation, "stop_reason": state.stop_reason,
        "started_at": _utc(state.started_at), "deadline_at": _utc(state.deadline_at),
        "limits": state.policy, "price_status": "known" if state.pricing is not None else "unknown",
    }


def planner_router(sessions, *, policy: PlannerPolicy | None = None,
                   control: Callable[[], str] = lambda: "STOPPED",
                   now: Callable[[], datetime] = lambda: datetime.now(timezone.utc)):
    """Build an inert-by-default router with a detached, revalidated policy.

    Enabling admission does not establish provider availability. Caller owns App
    integration, planner-aware cancellation, and a separately configured runtime.
    """
    policy = PlannerPolicy.model_validate_json((policy or PlannerPolicy()).model_dump_json())
    router = APIRouter(prefix="/api", tags=["planner"])

    def admission():
        if not policy.enabled:
            raise HTTPException(503, "Planner admission is disabled")
        try:
            running = control() == "RUNNING"
            at = now()
            aware = isinstance(at, datetime) and at.tzinfo is not None and at.utcoffset() is not None
        except Exception:
            raise HTTPException(503, "Planner admission control is unavailable") from None
        if not running or not aware:
            raise HTTPException(503, "Planner admission is paused or unavailable")
        return at.astimezone(timezone.utc)

    def lookup(session, campaign_id):
        campaign = service.owned(session, campaign_id)
        state = session.get(PlannerState, campaign_id)
        if state is None:
            raise service.NotFound(campaign_id)
        return campaign, state

    @router.post("/planner-seeds")
    def seed(body: PlannerSeedRequest, idempotency_key: Key):
        try:
            with sessions.begin() as session:
                project = _project(session, body.project_id)
                admission()
                project = _lock_project(session, body.project_id)
                _seed_binding(session, project, body.plan, policy)
                admission()
                key = "planner-seed.v1:" + sha256(idempotency_key.encode("utf-8")).hexdigest()
                campaign = service.create_campaign(session, project.id, body.plan.model_dump(mode="json"), key,
                    adapter_id=FIFO_ADAPTER, completion_policy="sequential", experiment_origin="manual")
                campaign = service.start(session, campaign.id)
                return {"schema_version": "planner.seed.v1", "campaign_id": campaign.id,
                    "execution_status": campaign.execution_status, "verdict": campaign.verdict,
                    "completion_policy": "sequential", "planner_attached": session.get(PlannerState, campaign.id) is not None}
        except IntegrityError:
            raise HTTPException(409, "Seed admission conflicts with an existing request") from None
        except ValueError:
            raise HTTPException(409, "Seed binding is unavailable or inconsistent") from None

    @router.post("/campaigns/{campaign_id}/planner")
    def attach(campaign_id: str, body: PlannerAttachRequest):
        with sessions.begin() as session:
            campaign = service.owned(session, campaign_id)
            project_id = campaign.project_id
            _project(session, project_id)
            admission()
            _lock_project(session, project_id)
            campaign = service.lock(session, campaign_id)
            if campaign.project_id != project_id:
                raise service.Conflict("Campaign project binding changed")
            project = _project(session, project_id)
            if campaign.adapter_id != FIFO_ADAPTER:
                raise service.Conflict("Planner admission supports curated FIFO fixtures only")
            if session.get(PlannerState, campaign_id) is None:
                first = session.scalar(select(ExperimentRecord).where(ExperimentRecord.campaign_id == campaign_id, ExperimentRecord.ordinal == 1))
                try:
                    if first is None:
                        raise ValueError("Missing seed")
                    _seed_binding(session, project, validate_record(first), policy)
                except ValueError:
                    raise HTTPException(409, "Seed binding is unavailable or inconsistent") from None
                attempts = list(session.scalars(select(Attempt).where(Attempt.campaign_id == campaign_id)))
                if not attempts or any(a.execution_status != "completed" or a.verdict not in
                        {"pass_within_scope", "design_mismatch", "property_failure"} for a in attempts):
                    raise service.Conflict("Planner attachment requires an eligible settled seed")
            at = admission()
            state = rounds.attach_planner(session, campaign_id, limits=policy.limits,
                requested_model=policy.requested_model, strategy=policy.strategy, pricing=policy.pricing, at=at)
            return _status(campaign, state, policy)

    @router.get("/campaigns/{campaign_id}/planner")
    def status(campaign_id: str):
        with sessions() as session:
            campaign, state = lookup(session, campaign_id)
            return _status(campaign, state, policy)

    @router.get("/campaigns/{campaign_id}/planner/budget")
    def budget(campaign_id: str):
        with sessions() as session:
            _, state = lookup(session, campaign_id)
            reserved = rounds.totals(session, campaign_id)
            # Absent pricing means unknown cost, even when no model ran yet.
            reserved["cost"] = str(reserved["cost"]) if state.pricing is not None else None
            reserved["seconds"] = str(reserved["seconds"])
            return {"schema_version": "planner.budget.v1", "campaign_id": campaign_id,
                "limits": state.policy, "reserved": reserved,
                "price_status": "known" if state.pricing is not None else "unknown"}

    @router.get("/campaigns/{campaign_id}/planner/rounds")
    def history(campaign_id: str, after: Cursor = 0, limit: PageSize = 25):
        with sessions() as session:
            lookup(session, campaign_id)
            rows = list(session.scalars(select(PlannerRound).where(PlannerRound.campaign_id == campaign_id,
                PlannerRound.ordinal > after).order_by(PlannerRound.ordinal).limit(limit + 1)))
            items = [{"round_id": r.id, "ordinal": r.ordinal, "generation": r.generation,
                "status": r.status, "experiment_id": r.experiment_id,
                "heartbeat_at": _utc(r.heartbeat_at), "finished_at": _utc(r.finished_at)} for r in rows[:limit]]
            return {"items": items, "next_cursor": items[-1]["ordinal"] if len(rows) > limit else None}

    @router.get("/campaigns/{campaign_id}/planner/cleanup")
    def cleanup(campaign_id: str, after: Cursor = 0, limit: PageSize = 25):
        with sessions() as session:
            lookup(session, campaign_id)
            rows = list(session.execute(select(PlannerRound.ordinal, PlannerCleanup).join(PlannerCleanup,
                (PlannerCleanup.round_id == PlannerRound.id) & (PlannerCleanup.campaign_id == PlannerRound.campaign_id))
                .where(PlannerRound.campaign_id == campaign_id, PlannerRound.ordinal > after)
                .order_by(PlannerRound.ordinal).limit(limit + 1)))
            items = [{"round_id": r.round_id, "ordinal": ordinal, "generation": r.generation,
                "local_state": r.local_state, "registered_at": _utc(r.registered_at),
                "owner_lost_at": _utc(r.owner_lost_at), "settled_at": _utc(r.settled_at)} for ordinal, r in rows[:limit]]
            return {"items": items, "next_cursor": items[-1]["ordinal"] if len(rows) > limit else None}

    return router
