from __future__ import annotations

import hashlib
import json
from datetime import timedelta, timezone

from sqlalchemy import select, update
from veriforge.contracts import AttemptResult, Verdict, canonical_hash
from veriforge.adapters import get_adapter
from .families import FIFO_ADAPTER, project_adapter
from .planner_experiments import record_experiment, resolve_attempt_experiment, queue_experiment, validate_record

from .database import Attempt, Campaign, Contract, ExperimentRecord, Finding, Project, ReplayLink, RunEvent, identifier, now

TERMINAL = {"completed", "cancelled", "interrupted", "infrastructure_error"}
VERDICTS = {v.value for v in Verdict}


class NotFound(Exception):
    pass


class Conflict(Exception):
    pass


def digest(value: dict) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def owned(session, campaign_id: str) -> Campaign:
    campaign = session.get(Campaign, campaign_id)
    if campaign is None or campaign.owner != "local":
        raise NotFound(campaign_id)
    return campaign


def lock(session, campaign_id: str) -> Campaign:
    # An UPDATE serializes writers in PostgreSQL and SQLite, including cancellation
    # racing a completion. SELECT FOR UPDATE alone would not lock SQLite rows.
    found = session.execute(update(Campaign).where(Campaign.id == campaign_id, Campaign.owner == "local").values(version=Campaign.version))
    if not found.rowcount:
        raise NotFound(campaign_id)
    session.expire_all()
    return owned(session, campaign_id)


def emit(session, campaign: Campaign, kind: str, payload: dict | None = None):
    campaign.event_sequence += 1
    campaign.version += 1
    campaign.updated_at = now()
    session.add(RunEvent(campaign_id=campaign.id, sequence=campaign.event_sequence, kind=kind, payload=payload or {}))


def create_campaign(session, project_id: str, plan: dict, key: str, *, require_new: bool = False, adapter_id: str = FIFO_ADAPTER, completion_policy: str = "single", experiment_origin: str = "manual") -> Campaign:
    if completion_policy not in {"single", "sequential"}:
        raise ValueError("Unknown campaign completion policy")
    project = session.get(Project, project_id)
    if not project or project.owner != "local":
        raise NotFound(project_id)
    from .engineering_catalog import project_view
    if project_view(project)["kind"] != "rtl_fixture":
        raise Conflict("Recorded engineering projects do not support RTL execution")
    request_hash = digest({"project_id": project_id, "plan": plan})
    if adapter_id != FIFO_ADAPTER:
        request_hash = digest({"adapter_id": adapter_id, "project_id": project_id, "plan": plan})
    if completion_policy != "single":
        request_hash = digest({"request_hash": request_hash, "completion_policy": completion_policy})
    if experiment_origin != "manual":
        request_hash = digest({"request_hash": request_hash, "experiment_origin": experiment_origin})
    prior = session.scalar(select(Campaign).where(Campaign.owner == "local", Campaign.create_key == key))
    if prior:
        if require_new:
            raise Conflict("Fresh campaign creation cannot reuse an existing record")
        if prior.request_hash != request_hash or prior.adapter_id != adapter_id or prior.completion_policy != completion_policy:
            raise Conflict("Idempotency key already used for a different request")
        return prior
    if plan.get("contract_id") != project.contract_id:
        raise Conflict("Plan must reference the project's reviewed contract")
    descriptor = project_adapter(project)
    if descriptor.adapter_id != adapter_id:
        raise Conflict("Campaign adapter does not match the curated project")
    experiment = descriptor.validate_experiment(plan)
    if experiment.fixture_id != project.id:
        raise Conflict("Experiment fixture does not match project")
    contract = session.get(Contract, project.contract_id)
    source_hash = digest({"source": project.data["source_file_hash"], "variant": project.data["variant"], "parameters": experiment.parameters.model_dump()})
    reviewed = descriptor.contract_model.model_validate(contract.data).model_copy(update={"parameters": experiment.parameters})
    identity = {**contract.identity, "contract_hash": canonical_hash(reviewed), "source_hash": source_hash, "experiment_hash": canonical_hash(experiment)}
    campaign = Campaign(project_id=project.id, contract_id=project.contract_id, adapter_id=adapter_id, completion_policy=completion_policy, create_key=key, request_hash=request_hash, plan=plan, identity=identity)
    session.add(campaign)
    session.flush()
    record_experiment(session, campaign, plan, "initial", origin=experiment_origin, initial=True)
    emit(session, campaign, "campaign.created")
    return campaign


def start(session, campaign_id: str) -> Campaign:
    campaign = lock(session, campaign_id)
    if campaign.execution_status != "draft":
        return campaign
    record = session.scalar(select(ExperimentRecord).where(ExperimentRecord.campaign_id == campaign.id, ExperimentRecord.ordinal == 1))
    if record is None:
        raise ValueError("Campaign has no immutable experiment; backfill is required")
    attempt = queue_experiment(session, campaign, record)
    campaign.execution_status = "queued"
    emit(session, campaign, "attempt.queued", {"attempt_id": attempt.id})
    return campaign


def cancel(session, campaign_id: str) -> Campaign:
    campaign = lock(session, campaign_id)
    if campaign.execution_status in TERMINAL:
        return campaign
    for attempt in session.scalars(select(Attempt).where(Attempt.campaign_id == campaign.id, Attempt.execution_status.in_(["queued", "running"]))):
        attempt.execution_status = "cancelled"
        attempt.lease = identifier()  # revoke authorization of already running worker
        attempt.finished_at = now()
    campaign.execution_status = "cancelled"
    campaign.verdict = None
    emit(session, campaign, "campaign.cancelled")
    return campaign


def claim(session, attempt_id: str) -> tuple[str, dict, str] | None:
    attempt = session.get(Attempt, attempt_id)
    if not attempt:
        return None
    campaign = lock(session, attempt.campaign_id)
    attempt = session.get(Attempt, attempt_id)
    eligible = {"queued", "running"} if campaign.completion_policy == "sequential" else {"queued"}
    if campaign.execution_status not in eligible or attempt.execution_status != "queued":
        return None
    record = resolve_attempt_experiment(session, attempt)
    attempt.execution_status = "running"
    attempt.heartbeat_at = now()
    campaign.execution_status = "running"
    emit(session, campaign, "attempt.running", {"attempt_id": attempt.id})
    return attempt.lease, record.plan, campaign.project_id


def authorized(session, attempt_id: str, lease: str) -> bool:
    attempt = session.get(Attempt, attempt_id)
    return bool(attempt and attempt.lease == lease and attempt.execution_status == "running")


def heartbeat(session, attempt_id: str, lease: str) -> bool:
    return bool(session.execute(update(Attempt).where(Attempt.id == attempt_id, Attempt.lease == lease, Attempt.execution_status == "running").values(heartbeat_at=now())).rowcount)


def finalize(session, attempt_id: str, lease: str, result: dict, infrastructure_error: bool = False) -> bool:
    attempt = session.get(Attempt, attempt_id)
    if not attempt:
        return False
    campaign = lock(session, attempt.campaign_id)
    attempt = session.get(Attempt, attempt_id)
    if attempt.lease != lease or attempt.execution_status != "running" or campaign.execution_status != "running":
        return False
    record = resolve_attempt_experiment(session, attempt)
    if not infrastructure_error:
        checked = AttemptResult.model_validate(result)
        if checked.experiment_id != record.plan["experiment_id"]:
            raise ValueError("Result belongs to a different experiment")
        if checked.execution_status not in TERMINAL:
            raise ValueError("Result is not terminal")
        if get_adapter(record.adapter_id).trace_schema is not None and checked.trace:
            raise ValueError("Family-specific trace must not be coerced into FIFO trace")
        if checked.identity is not None and any(getattr(checked.identity, key) != expected for key, expected in record.identity.items()):
            raise ValueError("Result source/checker/contract/stimulus identity differs from the immutable campaign snapshot")
    verdict = result.get("verdict")
    status = "infrastructure_error" if infrastructure_error else result["execution_status"]
    if not infrastructure_error and status == "completed" and verdict not in VERDICTS:
        raise ValueError("Worker result has no recognized independent-checker verdict")
    attempt.result = result
    attempt.verdict = None if infrastructure_error else verdict
    attempt.execution_status = status
    attempt.finished_at = now()
    if campaign.completion_policy == "single":
        campaign.execution_status = attempt.execution_status
        campaign.verdict = attempt.verdict
    else:
        # Child outcomes never become an aggregate functional verdict or close
        # the parent. A future bounded coordinator owns explicit settlement.
        campaign.verdict = None
    if verdict in {"design_mismatch", "property_failure"} and not infrastructure_error:
        session.add(Finding(campaign_id=campaign.id, attempt_id=attempt.id, data={"status": "observed", "verdict": verdict, "result": result, "contract_id": campaign.contract_id, "plan_hash": record.plan_hash, "experiment_record_id": record.id}))
    emit(session, campaign, "attempt." + attempt.execution_status, {"attempt_id": attempt.id, "verdict": attempt.verdict})
    return True


def recover_stale(session, stale_seconds: int = 300) -> int:
    cutoff = now() - timedelta(seconds=stale_seconds)
    ids = list(session.scalars(select(Attempt.id).where(Attempt.execution_status == "running", Attempt.heartbeat_at < cutoff)))
    recovered = 0
    for attempt_id in ids:
        attempt = session.get(Attempt, attempt_id)
        campaign = lock(session, attempt.campaign_id)
        attempt = session.get(Attempt, attempt_id)
        # Re-check after acquiring the campaign write lock.
        stamp = attempt.heartbeat_at
        if stamp and stamp.tzinfo is None:
            stamp = stamp.replace(tzinfo=cutoff.tzinfo)
        if attempt.execution_status != "running" or not stamp or stamp >= cutoff:
            continue
        attempt.execution_status = "interrupted"
        attempt.lease = identifier()
        attempt.finished_at = now()
        if campaign.completion_policy == "single":
            campaign.execution_status = "interrupted"
        campaign.verdict = None
        emit(session, campaign, "attempt.interrupted", {"attempt_id": attempt.id, "reason": "worker lease expired"})
        recovered += 1
    return recovered


def campaign_view(session, campaign: Campaign) -> dict:
    def utc(value):
        return value.replace(tzinfo=timezone.utc) if value is not None and value.tzinfo is None else value
    replay = session.get(ReplayLink, campaign.id)
    initial = session.scalar(select(ExperimentRecord).where(ExperimentRecord.campaign_id == campaign.id, ExperimentRecord.ordinal == 1))
    if initial is None:
        raise ValueError("Campaign has no immutable initial experiment")
    experiment = validate_record(initial)
    descriptor = get_adapter(initial.adapter_id)
    return {
        "id": campaign.id, "project_id": campaign.project_id, "contract_id": campaign.contract_id,
        "adapter_id": initial.adapter_id, "experiment_schema_version": experiment.schema_version,
        "completion_policy": campaign.completion_policy,
        "trace_schema": descriptor.trace_schema, "trace_artifact": descriptor.trace_artifact,
        "execution_status": campaign.execution_status, "verdict": campaign.verdict,
        "version": campaign.version, "plan": initial.plan,
        "source_attempt_id": replay.source_attempt_id if replay else None,
        "identity": initial.identity,
        "contract_snapshot": initial.contract_snapshot,
        "created_at": utc(campaign.created_at), "updated_at": utc(campaign.updated_at),
        "attempts": [{"id": a.id, "experiment_record_id": resolve_attempt_experiment(session, a).id, "execution_status": a.execution_status, "verdict": a.verdict, "result": a.result, "created_at": utc(a.created_at), "finished_at": utc(a.finished_at)} for a in session.scalars(select(Attempt).where(Attempt.campaign_id == campaign.id).order_by(Attempt.created_at))],
        "findings": [{"id": f.id, "attempt_id": f.attempt_id, **f.data} for f in session.scalars(select(Finding).where(Finding.campaign_id == campaign.id))],
    }
