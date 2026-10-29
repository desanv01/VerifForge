"""Durable experiment foundation; no provider, scheduler, or hidden model loop."""
from __future__ import annotations

from copy import deepcopy
from sqlalchemy import func, select

from veriforge.adapters import get_adapter
from veriforge.contracts import canonical_hash
from .database import Attempt, AttemptExperiment, Campaign, Contract, Dispatch, ExperimentRecord


def validate_record(record):
    from . import service
    descriptor = get_adapter(record.adapter_id)
    experiment = descriptor.validate_experiment(record.plan)
    contract = descriptor.contract_model.model_validate(record.contract_snapshot)
    if (experiment.experiment_id != record.wire_id or service.digest(record.plan) != record.plan_hash or
        canonical_hash(experiment) != record.identity.get("experiment_hash") or
        canonical_hash(contract) != record.identity.get("contract_hash") or
        contract.checker.sha256 != record.identity.get("checker_hash") or
        contract.parameters != experiment.parameters or contract.contract_id != experiment.contract_id):
        raise ValueError("Immutable experiment snapshot is inconsistent")
    return experiment


def resolve_attempt_experiment(session, attempt):
    link = session.get(AttemptExperiment, attempt.id)
    if link is None or link.campaign_id != attempt.campaign_id:
        raise ValueError("Attempt has no authorized experiment binding")
    record = session.get(ExperimentRecord, link.experiment_id)
    if record is None or record.campaign_id != attempt.campaign_id:
        raise ValueError("Attempt experiment belongs to another campaign")
    validate_record(record)
    return record


def record_experiment(session, campaign, plan, admission_key, *, origin="manual", initial=False):
    """Caller holds campaign write lock (or owns its uncommitted creation)."""
    from . import service
    descriptor = get_adapter(campaign.adapter_id)
    experiment = descriptor.validate_experiment(plan)
    prior = session.scalar(select(ExperimentRecord).where(ExperimentRecord.campaign_id == campaign.id, ExperimentRecord.admission_key == admission_key))
    if prior is not None:
        if prior.plan != plan or prior.origin != origin:
            raise service.Conflict("Experiment admission key already identifies different input")
        validate_record(prior)
        return prior
    if origin not in {"manual", "replay", "planner"} or not 1 <= len(admission_key) <= 128:
        raise ValueError("Invalid experiment admission identity")
    if session.scalar(select(ExperimentRecord.id).where(ExperimentRecord.campaign_id == campaign.id, ExperimentRecord.wire_id == experiment.experiment_id)):
        raise service.Conflict("Experiment ID already bound to an immutable record")
    if initial:
        snapshot = descriptor.contract_model.model_validate(session.get(Contract, campaign.contract_id).data).model_copy(update={"parameters": experiment.parameters})
        identity = deepcopy(campaign.identity)
    else:
        first = session.scalar(select(ExperimentRecord).where(ExperimentRecord.campaign_id == campaign.id).order_by(ExperimentRecord.ordinal))
        if first is None:
            raise ValueError("Campaign has no immutable initial binding")
        base = validate_record(first)
        if (experiment.fixture_id != base.fixture_id or experiment.parameters != base.parameters or
            experiment.contract_id != base.contract_id or experiment.target != base.target):
            raise service.Conflict("New experiment changes the frozen campaign binding")
        snapshot = descriptor.contract_model.model_validate(first.contract_snapshot)
        identity = {**first.identity, "experiment_hash": canonical_hash(experiment)}
    requirements = {item.requirement_id for item in snapshot.requirements}
    if (not initial or campaign.completion_policy == "sequential") and not set(experiment.objective_ids) <= requirements:
        raise service.Conflict("Experiment objectives are outside reviewed requirements")
    ordinal = (session.scalar(select(func.max(ExperimentRecord.ordinal)).where(ExperimentRecord.campaign_id == campaign.id)) or 0) + 1
    row = ExperimentRecord(campaign_id=campaign.id, adapter_id=campaign.adapter_id, ordinal=ordinal, admission_key=admission_key, wire_id=experiment.experiment_id, origin=origin,
        plan=deepcopy(plan), plan_hash=service.digest(plan), identity=identity, contract_snapshot=snapshot.model_dump(mode="json"))
    validate_record(row)
    session.add(row)
    session.flush()
    return row


def queue_experiment(session, campaign, record):
    from . import service
    if campaign.execution_status not in {"draft", "queued", "running"}:
        raise service.Conflict("Settled campaign cannot queue execution")
    if session.scalar(select(Attempt.id).where(Attempt.campaign_id == campaign.id, Attempt.execution_status.in_(["queued", "running"]))):
        raise service.Conflict("Campaign already has active execution")
    if record.campaign_id != campaign.id or record.adapter_id != campaign.adapter_id:
        raise ValueError("Experiment is not owned by campaign")
    validate_record(record)
    attempt = Attempt(campaign_id=campaign.id)
    session.add(attempt)
    session.flush()
    session.add(AttemptExperiment(attempt_id=attempt.id, campaign_id=campaign.id, experiment_id=record.id))
    session.add(Dispatch(attempt_id=attempt.id))
    session.flush()
    return attempt


def admit_experiment(session, campaign_id, plan, admission_key):
    """Trusted sequential continuation only; no budget/scheduler guarantee yet."""
    from . import service
    campaign = service.lock(session, campaign_id)
    if campaign.completion_policy != "sequential" or campaign.execution_status != "running":
        raise service.Conflict("Campaign does not accept sequential continuation")
    prior = session.scalar(select(ExperimentRecord).where(ExperimentRecord.campaign_id == campaign.id, ExperimentRecord.admission_key == admission_key))
    if prior is not None:
        record_experiment(session, campaign, plan, admission_key, origin="planner")
        return prior
    active = session.scalar(select(Attempt.id).where(Attempt.campaign_id == campaign.id, Attempt.execution_status.in_(["queued", "running"])))
    if active:
        raise service.Conflict("Sequential campaign already has an active experiment")
    previous = session.scalar(select(Attempt).where(Attempt.campaign_id == campaign.id).order_by(Attempt.created_at.desc()))
    if previous and (previous.execution_status != "completed" or previous.verdict not in {"pass_within_scope", "design_mismatch", "property_failure"}):
        raise service.Conflict("Previous execution blocks continuation")
    record = record_experiment(session, campaign, plan, admission_key, origin="planner")
    attempt = queue_experiment(session, campaign, record)
    service.emit(session, campaign, "experiment.queued", {"experiment_id": record.id, "attempt_id": attempt.id})
    return record


def settle_sequence(session, campaign_id, *, reason):
    from . import service
    campaign = service.lock(session, campaign_id)
    if campaign.completion_policy != "sequential":
        raise service.Conflict("Not a sequential campaign")
    if campaign.execution_status in service.TERMINAL:
        return campaign
    if campaign.execution_status != "running":
        raise service.Conflict("Sequence is not running")
    active = session.scalar(select(Attempt.id).where(Attempt.campaign_id == campaign.id, Attempt.execution_status.in_(["queued", "running"])))
    if active:
        raise service.Conflict("Sequence still has active execution")
    if reason not in {"budget_exhausted", "finding_observed", "stopped", "execution_blocked"}:
        raise ValueError("Unknown sequence settlement reason")
    campaign.execution_status, campaign.verdict = "completed", None
    service.emit(session, campaign, "sequence.completed", {"reason": reason})
    return campaign
