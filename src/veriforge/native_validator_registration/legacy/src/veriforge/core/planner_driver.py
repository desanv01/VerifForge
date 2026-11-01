"""Bounded ID-only outbox steps. No provider construction or service event loop.

The injected model handler is a trusted runtime boundary and must use the
mandatory resource authority in planner_transport. Queue delivery is not a grant.
"""
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from uuid import UUID

from sqlalchemy import and_, case, or_, select

from . import planner_resources as resources, planner_rounds as rounds, service
from .database import (Attempt, AttemptExperiment, PlannerIntent, PlannerRound,
    PlannerState, make_engine, now as service_now, session_factory)


def running_control():
    return "RUNNING"


def read_control(reader):
    try:
        return "RUNNING" if reader() == "RUNNING" else "STOPPED"
    except Exception:
        return "STOPPED"


def utc(value):
    return value.replace(tzinfo=timezone.utc) if value.tzinfo is None else value.astimezone(timezone.utc)


def stamp(clock):
    value = clock()
    if not isinstance(value, datetime) or value.tzinfo is None:
        raise ValueError("Driver requires timezone-aware service time")
    return value.astimezone(timezone.utc)


def _bound(value, maximum):
    if type(value) is not int or not 1 <= value <= maximum:
        raise ValueError("Invalid driver bound")


def _id(value):
    try:
        return type(value) is str and str(UUID(value)) == value
    except (ValueError, TypeError, AttributeError):
        return False


@dataclass(frozen=True)
class IntentStep:
    intent_id: str
    campaign_id: str
    generation: int
    kind: str
    round_id: str | None


def _intent_step(session, intent_id, *, at, control):
    row = session.get(PlannerIntent, intent_id)
    if row is None or row.kind not in {"advance", "model"}:
        return None
    if not resources.admission_allowed(session, role=resources.ROLE, at=at, control=control):
        return None
    campaign = service.lock(session, row.campaign_id)
    row = session.get(PlannerIntent, intent_id)
    state = session.get(PlannerState, campaign.id)
    if (state is None or state.strategy != "meta" or campaign.execution_status != "running" or
            row.generation != state.generation or at < utc(state.started_at) or at >= utc(state.deadline_at) or
            state.not_before is not None and at < utc(state.not_before)):
        return None
    if row.kind == "advance":
        if state.phase != "ready" or row.round_id is not None:
            return None
    else:
        model_round = session.get(PlannerRound, row.round_id)
        if (state.phase != "round_queued" or model_round is None or model_round.status != "queued" or
                model_round.campaign_id != campaign.id or model_round.generation != row.generation):
            return None
    return IntentStep(row.id, campaign.id, row.generation, row.kind, row.round_id)


def release_settled_resource(sessions, *, now=service_now):
    """Separate current-association CAS; never manufactures cleanup proof."""
    with sessions() as session:
        snapshot = resources.safe_snapshot(session)
    if snapshot.active_round_id is None:
        return False
    with sessions.begin() as session:
        return resources.release_settled_slot(session, expected_fence=snapshot.fence,
            expected_round_id=snapshot.active_round_id, role=resources.ROLE, at=stamp(now))


def publish_pending(sessions, dispatcher, *, now=service_now, control=running_control,
                    limit=100, retry_seconds=30):
    _bound(limit, 100)
    _bound(retry_seconds, 3600)
    sent = failed = 0
    at = stamp(now)
    if read_control(control) != "RUNNING":
        return {"sent": 0, "failed": 0}
    with sessions() as session:
        ids = list(session.scalars(select(PlannerIntent.id).join(PlannerState,
            PlannerState.campaign_id == PlannerIntent.campaign_id).where(
            PlannerIntent.generation == PlannerState.generation,
            PlannerState.strategy == "meta",
            or_(and_(PlannerIntent.kind == "advance", PlannerState.phase == "ready"),
                and_(PlannerIntent.kind == "model", PlannerState.phase == "round_queued")),
            or_(PlannerState.not_before.is_(None), PlannerState.not_before <= at),
            or_(PlannerIntent.sent_at.is_(None), PlannerIntent.sent_at <= at - timedelta(seconds=retry_seconds))
        ).order_by(PlannerIntent.id).limit(limit)))
    for intent_id in ids:
        with sessions.begin() as session:
            step = _intent_step(session, intent_id, at=stamp(now), control=read_control(control))
        if step is None:
            continue
        # Session/transaction is closed before the broker call.
        try:
            dispatcher.enqueue_planner(intent_id)
        except Exception:
            failed += 1  # No broker URL/exception body enters durable evidence.
        else:
            with sessions.begin() as session:
                rounds.acknowledge_intent(session, intent_id, at=stamp(now))
            sent += 1
    return {"sent": sent, "failed": failed}


def execute_intent(intent_id, *, sessions=None, now=service_now, control=running_control, model_handler=None):
    """RQ-safe ID entry point; generic processes never construct/invoke Meta.

    model_handler(intent_id) is explicitly injected by the future trusted runtime,
    whose own resource claim must recheck DB authority after this transaction.
    """
    if not _id(intent_id):
        return {"accepted": False, "reason": "invalid_intent"}
    sessions = sessions or session_factory(make_engine())
    with sessions.begin() as session:
        step = _intent_step(session, intent_id, at=stamp(now), control=read_control(control))
        if step is None:
            return {"accepted": False, "reason": "not_eligible"}
        if step.kind == "advance":
            row = rounds.advance(session, step.campaign_id, at=stamp(now), control=read_control(control),
                                 expected_generation=step.generation)
            return {"accepted": row is not None, "reason": "advanced" if row is not None else "settled"}
    if model_handler is None:
        return {"accepted": False, "reason": "runtime_unavailable"}
    # No result/body/policy in queue args; no Session crosses runtime execution.
    model_handler(intent_id)
    return {"accepted": True, "reason": "runtime_dispatched"}


def planner_attempt_allowed(session, attempt_id, *, at, control):
    """Dispatch preview only; execution repeats authority in claim_execution_step."""
    attempt = session.get(Attempt, attempt_id)
    if attempt is None:
        return False
    state = session.get(PlannerState, attempt.campaign_id)
    if state is None:
        return True  # Legacy manual/replay path has no provider-resource demand.
    if not resources.admission_allowed(session, role=resources.ROLE, at=at, control=control):
        return False
    campaign = service.lock(session, attempt.campaign_id)
    state, attempt = session.get(PlannerState, campaign.id), session.get(Attempt, attempt_id)
    if (state.phase != "waiting_execution" or attempt.execution_status != "queued" or
            at < utc(state.started_at) or at >= utc(state.deadline_at)):
        return False
    link = session.get(AttemptExperiment, attempt_id)
    parent_round = session.scalar(select(PlannerRound).where(PlannerRound.campaign_id == campaign.id,
        PlannerRound.experiment_id == link.experiment_id)) if link else None
    return bool(state.phase == "waiting_execution" and campaign.execution_status == "running" and
        attempt.execution_status == "queued" and parent_round and parent_round.status == "accepted" and
        state.generation == parent_round.generation + 1)


def claim_attempt(session, attempt_id, *, at, control):
    attempt = session.get(Attempt, attempt_id)
    if attempt is None:
        return None, False
    planner_owned = session.get(PlannerState, attempt.campaign_id) is not None
    if not planner_owned:
        return service.claim(session, attempt_id), False
    if not resources.admission_allowed(session, role=resources.ROLE, at=at, control=control):
        return None, True
    return rounds.claim_execution_step(session, attempt_id, at=at, control=control), True


def reconcile_once(sessions, dispatcher, *, now=service_now, control=running_control, limit=100):
    """Bounded DB recovery then outbox publish; never invokes a model handler.

    Pending/unknown cleanup remains unresolved across terminal parents and
    process loss. Only runtime ownership observations may mark owner loss;
    elapsed recovery time never supplies the original local task proof.
    """
    _bound(limit, 100)
    released = release_settled_resource(sessions, now=now)
    at = stamp(now)
    with sessions() as session:
        ids = list(session.scalars(select(PlannerState.campaign_id).where(PlannerState.phase.not_in(rounds.TERMINAL))
            .order_by(case((PlannerState.deadline_at <= at, 0),
                           (PlannerState.phase.in_(["waiting_execution", "model_running"]), 1), else_=2),
                      PlannerState.deadline_at, PlannerState.campaign_id).limit(limit)))
    recovered = 0
    for campaign_id in ids:
        with sessions.begin() as session:
            resources.lock_resource(session)  # Lock only, including stopped control.
            recovered += int(rounds.recover_campaign(session, campaign_id, at=stamp(now), control=read_control(control)))
    published = publish_pending(sessions, dispatcher, now=now, control=control, limit=limit)
    return {"recovered": recovered, "released": released, **published}
