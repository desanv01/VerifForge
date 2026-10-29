"""Durable local-provider cleanup, independent of execution and remote outcomes.

Caller owns each transaction. Commit record_pending BEFORE any provider I/O.
Only the owner that observes all cooperative local tasks settled may acknowledge;
elapsed time, process death and a completed wrapper task are not that proof.
The proof string records a trusted internal owner's assertion, not an independent
task inspection or remote-abort proof. Never accept it from public client input.
This API never invokes/retries a provider or grants execution authority.
"""
from dataclasses import dataclass, field
from datetime import datetime, timezone
import hashlib
from typing import Literal

from sqlalchemy import select

from . import service
from .database import PlannerCleanup, PlannerModelCall, PlannerRound, PlannerState
from .planner_rounds import ModelLease

CleanupState = Literal["pending", "unknown", "settled"]
CleanupProof = Literal["cooperative_tasks_settled"]


@dataclass(frozen=True)
class CleanupTicket:
    round_id: str
    campaign_id: str
    generation: int
    request_hash: str
    execution_lease: str = field(repr=False)
    owner_token: str = field(repr=False)


@dataclass(frozen=True)
class CleanupView:
    round_id: str
    campaign_id: str
    generation: int
    local_state: CleanupState
    registered_at: datetime
    owner_lost_at: datetime | None
    settled_at: datetime | None


def _at(value):
    if not isinstance(value, datetime) or value.tzinfo is None:
        raise ValueError("Cleanup service time must be timezone-aware")
    return value.astimezone(timezone.utc)


def _utc(value):
    return value.replace(tzinfo=timezone.utc) if value.tzinfo is None else value.astimezone(timezone.utc)


def _ticket(row):
    return CleanupTicket(row.round_id, row.campaign_id, row.generation, row.request_hash, row.execution_lease, row.owner_token)


def record_pending(session, lease: ModelLease, *, at, control="RUNNING") -> CleanupTicket | None:
    """Create once for a currently claimed call; duplicate registration returns None.

    A returned ticket is a cleanup fence, not permission to invoke. Callers must
    still respect round authority/cancellation after committing this transaction.
    """
    at = _at(at)
    if not isinstance(lease, ModelLease) or type(lease.generation) is not int or lease.generation < 0:
        raise ValueError("Expected a claimed model lease with a nonnegative integer generation")
    campaign = service.lock(session, lease.campaign_id)
    state = session.get(PlannerState, campaign.id)
    row = session.get(PlannerRound, lease.round_id)
    call = session.get(PlannerModelCall, lease.round_id)
    if session.get(PlannerCleanup, lease.round_id) is not None:
        return None
    if (control != "RUNNING" or state is None or row is None or call is None or
        campaign.execution_status != "running" or state.phase != "model_running" or
        row.campaign_id != campaign.id or row.status != "running" or
        row.generation != lease.generation or state.generation != lease.generation or row.lease != lease.lease or
        call.started_at is None or call.deadline_at is None or at < _utc(call.started_at) or
        at >= min(_utc(call.deadline_at), _utc(state.deadline_at)) or
        call.request_hash != lease.request_hash or hashlib.sha256(call.request_body).hexdigest() != lease.request_hash or
        call.request_body != lease.request_body or call.schema_hash != lease.schema_hash or
        call.requested_model != lease.requested_model or state.requested_model != lease.requested_model or
        call.input_allowance != lease.input_allowance or call.context != lease.context or state.strategy != lease.strategy):
        return None
    cleanup = PlannerCleanup(round_id=row.id, campaign_id=campaign.id, generation=lease.generation,
        execution_lease=lease.lease, request_hash=lease.request_hash, registered_at=at)
    session.add(cleanup)
    session.flush()
    return _ticket(cleanup)


def acknowledge_settled(session, ticket: CleanupTicket, *, proof: CleanupProof, at) -> bool:
    """Trusted owner's proof that ALL cooperative local tasks have settled.

    Exact original ticket remains valid after cancellation/owner-loss; this never
    consults or restores the revoked execution lease, nor changes remote outcome.
    """
    at = _at(at)
    if (type(ticket) is not CleanupTicket or type(ticket.generation) is not int or
        any(type(value) is not str for value in (ticket.round_id, ticket.campaign_id, ticket.request_hash, ticket.execution_lease, ticket.owner_token)) or
        proof != "cooperative_tasks_settled"):
        raise ValueError("Expected original cleanup ticket and cooperative settlement proof")
    row = session.get(PlannerCleanup, ticket.round_id)
    if row is None or row.campaign_id != ticket.campaign_id:
        return False
    service.lock(session, row.campaign_id)
    row = session.get(PlannerCleanup, ticket.round_id)
    if row is None or _ticket(row) != ticket:
        return False
    if at < _utc(row.owner_lost_at or row.registered_at):
        raise ValueError("Cleanup acknowledgment precedes recorded ownership")
    if row.local_state == "settled":
        return True
    row.local_state, row.settled_at, row.settlement_proof = "settled", at, proof
    session.flush()
    return True


def mark_owner_lost(session, round_id, *, at) -> bool:
    """Explicit reconciler observation of lost local ownership, never settlement."""
    at = _at(at)
    row = session.get(PlannerCleanup, round_id)
    if row is None:
        return False
    service.lock(session, row.campaign_id)
    session.refresh(row)
    if at < _utc(row.registered_at):
        raise ValueError("Owner loss precedes registration")
    if row.local_state != "pending":
        return False
    row.local_state, row.owner_lost_at = "unknown", at
    session.flush()
    return True


def read_cleanup(session, round_id) -> CleanupView | None:
    row = session.get(PlannerCleanup, round_id)
    if row is None:
        return None
    return CleanupView(row.round_id, row.campaign_id, row.generation, row.local_state,
        _utc(row.registered_at), _utc(row.owner_lost_at) if row.owner_lost_at else None,
        _utc(row.settled_at) if row.settled_at else None)


def unresolved_cleanup(session) -> tuple[CleanupView, ...]:
    """Recovery inventory only. Never schedules a remote retry or declares done."""
    ids = session.scalars(select(PlannerCleanup.round_id).where(PlannerCleanup.local_state.in_(["pending", "unknown"])).order_by(PlannerCleanup.registered_at, PlannerCleanup.round_id))
    return tuple(read_cleanup(session, round_id) for round_id in ids)
