"""Durable finite repair admission/reconciliation; never runs tools or providers."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Annotated, Literal

from pydantic import Field, StrictBool, StrictInt, model_validator
from sqlalchemy import select, update

from veriforge.adapters import get_adapter
from veriforge.contracts import AttemptResult, Identifier, Sha256, WireModel, canonical_hash
from veriforge.evidence import (CandidateProvenance, CandidateValidation, FamilyCandidateReport,
    FamilyValidationSuite, build_family_validation_suite, checked_family_attempt, compare_candidate_sources, same_failure)
from veriforge.evidence.families import _axi_reduction_cycles
from veriforge.evidence.workflow import _candidates
from . import artifacts, service
from .database import Attempt, Campaign, ExperimentRecord, now
from .planner_experiments import resolve_attempt_experiment
from .repair_models import RepairCase, RepairJob


class RepairDisabled(service.Conflict):
    pass


class _BudgetExhausted(service.Conflict):
    pass


class RepairPolicy(WireModel):
    enabled: StrictBool = False
    runtime_configured: StrictBool = False
    runtime_identity: Sha256 | None = None
    max_cases: Annotated[StrictInt, Field(ge=3, le=101)] = 16
    max_reduction_attempts: Annotated[StrictInt, Field(ge=1, le=100)] = 12
    max_wall_seconds: Annotated[StrictInt, Field(ge=1, le=3600)] = 600
    max_total_cycles: Annotated[StrictInt, Field(ge=1, le=1_000_000)] = 100_000
    max_reserved_artifact_bytes: Annotated[StrictInt, Field(ge=1024, le=1_073_741_824)] = 268_435_456


class RepairRequest(WireModel):
    kind: Literal["candidate", "reduction"]
    candidate_fixture_id: Identifier | None = None
    max_attempts: Annotated[StrictInt, Field(ge=1, le=100)] | None = None

    @model_validator(mode="after")
    def bounded_shape(self):
        if self.kind == "candidate" and (self.candidate_fixture_id is None or self.max_attempts is not None):
            raise ValueError("candidate requires fixture ID and server-derived check count")
        if self.kind == "reduction" and self.candidate_fixture_id is not None:
            raise ValueError("reduction cannot replace source fixture")
        return self


def _at(value=None):
    value = now() if value is None else value
    if not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() is None:
        raise service.Conflict("Repair clock unavailable")
    return value.astimezone(timezone.utc)


def _utc(value):
    # SQLite test storage loses timezone; persisted production timestamps are UTC.
    return value.replace(tzinfo=timezone.utc) if value.tzinfo is None else value.astimezone(timezone.utc)


def _running(policy, control):
    if not policy.enabled or not policy.runtime_configured or policy.runtime_identity is None:
        return False
    try:
        return control is not None and control() == "RUNNING"
    except Exception:
        return False


def _owned(session, job_id):
    job = session.get(RepairJob, job_id)
    if job is None or job.owner != "local":
        raise service.NotFound(job_id)
    artifacts.owned_attempt(session, job.original_attempt_id)
    return job


def _lock(session, job_id):
    changed = session.execute(update(RepairJob).where(RepairJob.id == job_id, RepairJob.owner == "local")
        .values(version=RepairJob.version + 1))
    if not changed.rowcount:
        raise service.NotFound(job_id)
    session.expire_all()
    return _owned(session, job_id)


def _cases(session, job):
    return list(session.scalars(select(RepairCase).where(RepairCase.job_id == job.id).order_by(RepairCase.ordinal)))


def _original(session, attempt_id, root):
    directory, plan, result, _ = artifacts.attempt_snapshot(session, attempt_id, root=root, checked_required=True)
    try:
        checked_plan, checked = checked_family_attempt(directory)
        if checked_plan != plan or checked != result or not same_failure(result, result):
            raise ValueError("not a checked original failure")
    except (ValueError, OSError) as error:
        raise service.Conflict("Repair requires unchanged installed checked family failure") from error
    return directory, plan, result


def _frozen_original(session, job, root):
    directory, plan, result = _original(session, job.original_attempt_id, root)
    if (plan.model_dump(mode="json") != job.original_plan or result.model_dump(mode="json") != job.original_result
            or service.digest(job.original_result) != job.original_result_hash):
        raise service.Conflict("Original repair evidence changed")
    return directory, plan, result


def _reserve_case(session, job, plan, role, label):
    policy = RepairPolicy.model_validate(job.policy)
    if (job.next_ordinal > policy.max_cases or job.reserved_cycles + plan.cycle_count > policy.max_total_cycles
            or job.reserved_bytes + plan.budget.max_artifact_bytes > policy.max_reserved_artifact_bytes):
        raise _BudgetExhausted("Repair aggregate reservation exhausted")
    ordinal = job.next_ordinal
    campaign = service.create_campaign(session, plan.fixture_id, plan.model_dump(mode="json"),
        "repair:" + job.id + ":" + str(ordinal), adapter_id=get_adapter(plan.adapter).adapter_id,
        require_new=True, experiment_origin="replay" if role == "reproduce" else "manual")
    experiment = session.scalar(select(ExperimentRecord).where(ExperimentRecord.campaign_id == campaign.id))
    if experiment.identity != get_adapter(plan.adapter).expected_identity(plan):
        raise service.Conflict("Registered repair fixture identity is stale")
    case = RepairCase(job_id=job.id, ordinal=ordinal, role=role, label=label,
        plan=plan.model_dump(mode="json"), plan_hash=canonical_hash(plan), campaign_id=campaign.id,
        experiment_id=experiment.id)
    session.add(case)
    job.next_ordinal += 1
    job.reserved_cycles += plan.cycle_count
    job.reserved_bytes += plan.budget.max_artifact_bytes
    session.flush()
    return case


def _queue(session, job, case, at, policy, control):
    at = max(at, _at())
    plan = get_adapter(job.original_plan["adapter"]).validate_experiment(case.plan)
    if (job.active_case_id is not None or case.status != "planned" or not _running(policy, control)
            or canonical_hash(policy) != job.policy_hash
            or at + timedelta(seconds=plan.budget.wall_time_seconds) > _utc(job.deadline_at)):
        raise service.Conflict("Repair child is not eligible for dispatch")
    if (session.get(Campaign, case.campaign_id).execution_status != "draft" or
            session.scalar(select(Attempt.id).where(Attempt.campaign_id == case.campaign_id)) is not None):
        raise service.Conflict("Repair child campaign was modified outside its operation")
    # start() creates immutable AttemptExperiment + Dispatch in this transaction.
    service.start(session, case.campaign_id)
    attempt = session.scalar(select(Attempt).where(Attempt.campaign_id == case.campaign_id))
    case.attempt_id, case.execution_lease, case.status = attempt.id, attempt.lease, "queued"
    job.active_case_id, job.cleanup_status = case.id, "worker_ownership_required"
    job.updated_at = at
    session.flush()


def admit(session, original_attempt_id, key, request, *, policy=None, control=None, root=None, at=None):
    """Caller owns transaction; ordinary dispatch occurs only after its commit."""
    policy = RepairPolicy.model_validate((policy or RepairPolicy()).model_dump(mode="json"))
    if not _running(policy, control):
        raise RepairDisabled("Repair runtime is disabled or not granted")
    request = RepairRequest.model_validate(request.model_dump(mode="json") if isinstance(request, RepairRequest) else request)
    if not isinstance(key, str) or not 1 <= len(key) <= 128:
        raise ValueError("Invalid repair idempotency key")
    artifacts.owned_attempt(session, original_attempt_id)
    fingerprint = service.digest({"original_attempt_id": original_attempt_id, "request": request.model_dump(mode="json"),
                                  "policy": canonical_hash(policy)})
    prior = session.scalar(select(RepairJob).where(RepairJob.owner == "local", RepairJob.create_key == key))
    if prior:
        if prior.request_hash != fingerprint:
            raise service.Conflict("Repair idempotency key identifies another request")
        return prior
    _, plan, original = _original(session, original_attempt_id, root)
    at = _at(at)
    maximum = request.max_attempts or policy.max_reduction_attempts
    if maximum > policy.max_reduction_attempts:
        raise service.Conflict("Reduction attempt limit exceeds server policy")
    suite = build_family_validation_suite(plan) if request.kind == "candidate" else None
    if suite is not None:
        descriptor = get_adapter(plan.adapter)
        if request.candidate_fixture_id == plan.fixture_id:
            raise service.Conflict("Candidate must select a different installed fixture")
        descriptor.validate_experiment({**plan.model_dump(mode="json"), "fixture_id": request.candidate_fixture_id})
    job = RepairJob(kind=request.kind, create_key=key, request_hash=fingerprint, original_attempt_id=original_attempt_id,
        original_plan=plan.model_dump(mode="json"), original_result=original.model_dump(mode="json"),
        original_result_hash=service.digest(original.model_dump(mode="json")), candidate_fixture_id=request.candidate_fixture_id,
        suite=None if suite is None else suite.model_dump(mode="json"), suite_hash=None if suite is None else canonical_hash(suite),
        provenance=CandidateProvenance().model_dump(mode="json"), policy=policy.model_dump(mode="json"),
        policy_hash=canonical_hash(policy), max_attempts=maximum, deadline_at=at + timedelta(seconds=policy.max_wall_seconds))
    session.add(job)
    session.flush()
    reproduce = _reserve_case(session, job, plan, "reproduce", "original-failure-replay")
    if suite is not None:
        for item in suite.cases:
            candidate = get_adapter(plan.adapter).validate_experiment({**item.experiment.model_dump(mode="json"),
                                                                      "fixture_id": request.candidate_fixture_id})
            _reserve_case(session, job, candidate, item.role, item.label)
    # Revalidate grant after evidence/plan construction, before any dispatch intent.
    if not _running(policy, control):
        raise RepairDisabled("Repair admission revoked")
    _queue(session, job, reproduce, at, policy, control)
    return job


def attempt_allowed(session, attempt_id, *, policy=None, control=None, at=None):
    """Main worker/dispatcher gate: None=ordinary attempt, False=repair fenced."""
    attempt = session.get(Attempt, attempt_id)
    case = session.scalar(select(RepairCase).where(RepairCase.campaign_id == attempt.campaign_id)) if attempt else None
    if case is None:
        return None
    policy = policy or RepairPolicy()
    job = session.get(RepairJob, case.job_id)
    try:
        at = _at(at)
        return bool(job.owner == "local" and job.status == "active" and job.active_case_id == case.id
            and case.status == "queued" and attempt is not None and attempt.lease == case.execution_lease
            and case.attempt_id == attempt_id
            and attempt.execution_status in {"queued", "running"} and _running(policy, control)
            and canonical_hash(policy) == job.policy_hash and at < _utc(job.deadline_at))
    except (ValueError, service.Conflict, AttributeError):
        return False


def _finish(session, job, status, reason, at, extra=None):
    job.status, job.reason, job.updated_at = status, reason, at
    rows = _cases(session, job)
    best = next((row for row in rows if row.id == job.best_case_id), None)
    job.report = {"schema_version": "repair.report.v1", "job_id": job.id, "kind": job.kind,
        "execution_status": status, "reason": reason, "original_attempt_id": job.original_attempt_id,
        "original_result_hash": job.original_result_hash, "original_plan": job.original_plan,
        "original_result": job.original_result, "request_hash": job.request_hash,
        "policy_hash": job.policy_hash, "limits": job.policy, "max_attempts": job.max_attempts,
        "deadline_at": _utc(job.deadline_at).isoformat(), "reserved_cycles": job.reserved_cycles,
        "reserved_artifact_bytes": job.reserved_bytes,
        "original_identity": job.original_result["identity"], "candidate_fixture_id": job.candidate_fixture_id,
        "suite": job.suite, "suite_hash": job.suite_hash, "provenance": job.provenance,
        "cases": [_case_view(row, session.get(Attempt, row.attempt_id) if row.attempt_id else None) for row in rows],
        "best_case_id": job.best_case_id,
        "claim": "bounded_search_no_minimality_guarantee" if job.kind == "reduction" else "validation_under_exact_recorded_checks",
        "cleanup_status": job.cleanup_status, "physical_cleanup_verified": False,
        "operational_acceptance": False}
    if job.kind == "reduction":
        job.report = {**job.report, "reduced_experiment": best.plan if best else job.original_plan,
            "reduced_result": best.result if best else job.original_result, "improved": best is not None}
    if extra:
        job.report = {**job.report, **extra}
    job.report_hash = service.digest(job.report)


def _stop(session, job, status, reason, at):
    for case in _cases(session, job):
        if case.status in {"planned", "queued"}:
            service.cancel(session, case.campaign_id)  # revokes active attempt lease
            case.status = "cancelled" if status == "cancelled" else "interrupted"
    job.cleanup_status = ("external_settlement_required" if job.active_case_id else
        "worker_finalized_external_cleanup_unverified" if any(c.attempt_id for c in _cases(session, job)) else "not_started")
    _finish(session, job, status, reason, at)


def _fresh(job, policy, control, at):
    return (_running(policy, control) and canonical_hash(policy) == job.policy_hash
            and max(at, _at()) < _utc(job.deadline_at))


def _complete(session, job, reason, at, policy, control, extra=None):
    if not _fresh(job, policy, control, at):
        _stop(session, job, "interrupted", "terminal_admission_fenced", at)
    else:
        _finish(session, job, "completed", reason, at, extra)


def cancel(session, job_id, *, at=None):
    job = _lock(session, job_id)
    if job.status == "active":
        _stop(session, job, "cancelled", "cancel_requested_no_automatic_retry", _at(at))
    return job


def _checked_case(session, case, root):
    directory, plan, result, _ = artifacts.attempt_snapshot(session, case.attempt_id, root=root, checked_required=True)
    checked_plan, checked = checked_family_attempt(directory)
    if (plan.model_dump(mode="json") != case.plan or canonical_hash(plan) != case.plan_hash
            or checked_plan != plan or checked != result):
        raise service.Conflict("Repair child result identity changed")
    binding = resolve_attempt_experiment(session, session.get(Attempt, case.attempt_id))
    if binding.id != case.experiment_id or binding.campaign_id != case.campaign_id:
        raise service.Conflict("Repair child experiment association changed")
    return result


def _next_reduction(session, job, original_plan):
    rows = _cases(session, job)
    tried = {row.plan_hash for row in rows}
    if sum(row.role == "reduction" for row in rows) >= job.max_attempts:
        return None, "attempt_limit"
    best_case = session.get(RepairCase, job.best_case_id) if job.best_case_id else None
    best = get_adapter(original_plan.adapter).validate_experiment(best_case.plan if best_case else job.original_plan)
    field = "cycles" if best.target == "axi_lite" else "operations"
    choices = _axi_reduction_cycles(best) if best.target == "axi_lite" else _candidates(best)
    for stimulus in choices:
        try:
            proposed = get_adapter(best.adapter).validate_experiment({**best.model_dump(mode="json"), field: stimulus})
        except ValueError:
            continue
        if canonical_hash(proposed) not in tried:
            return proposed, None
    return None, "bounded_candidates_exhausted"


def _candidate_report(session, job, root):
    rows = _cases(session, job)
    original, reproduced = AttemptResult.model_validate(job.original_result), AttemptResult.model_validate(rows[0].result)
    suite = FamilyValidationSuite.model_validate(job.suite)
    candidates = rows[1:]
    results = [AttemptResult.model_validate(case.result) for case in candidates]
    original_dir = artifacts.contained(artifacts.storage_root(root), job.original_attempt_id)
    witness_dir = artifacts.contained(artifacts.storage_root(root), candidates[0].attempt_id)
    review = compare_candidate_sources(original_dir, witness_dir, provenance=CandidateProvenance.model_validate(job.provenance))
    passed = all(result.verdict == "pass_within_scope" and result.failure_signature is None for result in results)
    n = sum(c.role == "neighbor" for c in suite.cases)
    record = CandidateValidation(original_experiment=get_adapter(suite.adapter_id).validate_experiment(job.original_plan),
        original_result=original, original_replay=reproduced, candidate_fixture_id=job.candidate_fixture_id,
        candidate_experiments=tuple(get_adapter(suite.adapter_id).validate_experiment(case.plan) for case in candidates),
        neighboring_plan_count=n, regression_plan_count=len(candidates) - 1 - n, witness=results[0],
        neighbors=tuple(results[1:n+1]), regressions=tuple(results[n+1:]),
        status="validated_under_checks" if passed else "failed_under_checks",
        provenance=CandidateProvenance.model_validate(job.provenance), source_review=review,
        source_review_status="available", source_review_sha256=canonical_hash(review))
    return FamilyCandidateReport(suite=suite, suite_sha256=job.suite_hash, validation=record).model_dump(mode="json")


def advance(session, job_id, *, policy=None, control=None, root=None, at=None):
    """One reconciliation transition under DB lock; commit before dispatching.

    Never requeues an existing attempt or reexecutes a tried plan. Main must wire
    attempt_allowed into claim/dispatch/cancellation before enabling the policy.
    """
    job = _lock(session, job_id)
    if job.status != "active":
        return job
    at, policy = _at(at), policy or RepairPolicy()
    if not _running(policy, control) or canonical_hash(policy) != job.policy_hash or at >= _utc(job.deadline_at):
        _stop(session, job, "interrupted", "control_policy_or_deadline_fenced", at)
        return job
    try:
        _, original_plan, original_result = _frozen_original(session, job, root)
    except (ValueError, OSError, service.Conflict):
        _stop(session, job, "interrupted", "original_evidence_changed", at)
        return job
    case = session.get(RepairCase, job.active_case_id) if job.active_case_id else None
    if case is not None:
        attempt = session.get(Attempt, case.attempt_id)
        if attempt is None or attempt.lease != case.execution_lease or attempt.execution_status in {
                "interrupted", "infrastructure_error", "cancelled"}:
            _stop(session, job, "interrupted", "child_effect_unknown_no_retry", at)
            return job
        if attempt.execution_status in {"queued", "running"}:
            return job
        if attempt.execution_status != "completed" or attempt.verdict not in {"pass_within_scope", "design_mismatch", "property_failure"}:
            _stop(session, job, "incomplete", "child_not_independently_checked", at)
            return job
        try:
            result = _checked_case(session, case, root)
        except (ValueError, OSError, service.Conflict):
            _stop(session, job, "interrupted", "child_evidence_invalid", at)
            return job
        if not _fresh(job, policy, control, at):
            _stop(session, job, "interrupted", "terminal_admission_fenced", at)
            return job
        case.result, case.result_hash, case.status = result.model_dump(mode="json"), service.digest(result.model_dump(mode="json")), "completed"
        job.active_case_id = None
        job.cleanup_status = "worker_finalized_external_cleanup_unverified"
        session.flush()  # release the one-active-child unique slot before successor
        if case.role == "reproduce" and not same_failure(original_result, result):
            _complete(session, job, "original_not_reproduced", at, policy, control)
            return job
        if case.role == "reduction" and same_failure(original_result, result):
            case.accepted_progress = True
            job.best_case_id = case.id
    if job.kind == "candidate":
        pending = next((row for row in _cases(session, job) if row.status == "planned"), None)
        if pending is None:
            try:
                report = _candidate_report(session, job, root)
            except (ValueError, OSError, service.Conflict):
                _finish(session, job, "incomplete", "candidate_report_evidence_invalid", at)
                return job
            _complete(session, job, report["validation"]["status"], at, policy, control,
                      {"family_candidate_report": report})
        else:
            try:
                _queue(session, job, pending, at, policy, control)
            except service.Conflict:
                _stop(session, job, "interrupted", "next_child_admission_fenced", at)
    else:
        proposed, reason = _next_reduction(session, job, original_plan)
        if proposed is None:
            _complete(session, job, reason, at, policy, control)
        else:
            try:
                with session.begin_nested():
                    pending = _reserve_case(session, job, proposed, "reduction", "trial-" + str(job.next_ordinal))
            except _BudgetExhausted:
                _complete(session, job, "aggregate_budget_exhausted", at, policy, control)
                return job
            except (ValueError, service.Conflict):
                _stop(session, job, "interrupted", "reduction_case_admission_invalid", at)
                return job
            try:
                _queue(session, job, pending, at, policy, control)
            except service.Conflict:
                _stop(session, job, "interrupted", "next_child_admission_fenced", at)
    return job


def _case_view(case, attempt=None):
    return {"case_id": case.id, "ordinal": case.ordinal, "role": case.role, "label": case.label,
        "plan": case.plan, "plan_hash": case.plan_hash, "campaign_id": case.campaign_id,
        "experiment_id": case.experiment_id, "attempt_id": case.attempt_id, "status": case.status,
        "execution_lease": case.execution_lease,
        "execution_status": attempt.execution_status if attempt else "not_run" if case.attempt_id is None else case.status,
        "design_verdict": attempt.verdict if attempt else case.result.get("verdict") if case.result else None,
        "result_hash": case.result_hash, "result": case.result, "accepted_progress": case.accepted_progress}


def status(session, job_id):
    job = _owned(session, job_id)
    return {"job_id": job.id, "kind": job.kind, "execution_status": job.status, "reason": job.reason,
        "version": job.version, "request_hash": job.request_hash, "policy_hash": job.policy_hash,
        "limits": job.policy, "max_attempts": job.max_attempts,
        "original_attempt_id": job.original_attempt_id, "original_result_hash": job.original_result_hash,
        "candidate_fixture_id": job.candidate_fixture_id, "suite_hash": job.suite_hash,
        "provenance": job.provenance, "active_case_id": job.active_case_id, "best_case_id": job.best_case_id,
        "deadline_at": _utc(job.deadline_at).isoformat(), "reserved_cycles": job.reserved_cycles,
        "reserved_artifact_bytes": job.reserved_bytes, "cleanup_status": job.cleanup_status,
        "cases": [_case_view(case, session.get(Attempt, case.attempt_id) if case.attempt_id else None)
                  for case in _cases(session, job)], "report_ready": job.report is not None,
        "report_hash": job.report_hash}


def report(session, job_id):
    job = _owned(session, job_id)
    if job.report is None:
        raise service.Conflict("Repair is not settled; report unavailable")
    if service.digest(job.report) != job.report_hash:
        raise service.Conflict("Repair report identity changed")
    return {"report": job.report, "sha256": job.report_hash}
