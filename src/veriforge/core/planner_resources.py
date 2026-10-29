"""Single provider slot, explicit fencing and cleanup proof; no expiry or I/O.

Trusted service inputs only. All mutations require caller-owned transactions and
must be the FIRST authority lock: resource -> campaign -> round/cleanup. Never
enter these APIs holding campaign/cleanup locks or acknowledge cleanup and then
acquire a resource in the same transaction. Commit a grant before provider I/O;
revalidate it after commit and before invoking/applying a result. No public API.
"""
from dataclasses import dataclass, field
from datetime import datetime, timezone
from uuid import UUID

from sqlalchemy import select, update

from . import planner_cleanup as cleanup, planner_rounds as rounds, service
from .database import PlannerCleanup, PlannerModelCall, PlannerRound, PlannerRuntimeSlot, PlannerState, identifier

RESOURCE = 'local_planner_provider'
ROLE = 'planner_runtime'
MAX_FENCE = 2 ** 63 - 1


@dataclass(frozen=True)
class ProviderGrant:
    resource: str
    fence: int
    incarnation: str = field(repr=False)
    lease: rounds.ModelLease = field(repr=False)
    cleanup_ticket: cleanup.CleanupTicket = field(repr=False)


@dataclass(frozen=True)
class ResourceSnapshot:
    resource: str
    fence: int
    state: str
    active_round_id: str | None
    campaign_id: str | None
    acquired_at: datetime | None
    owner_lost_at: datetime | None
    changed_at: datetime
    unresolved_cleanup: int


def new_incarnation():
    """Trusted runtime creates one opaque incarnation per service process."""
    return identifier()


def _at(value):
    if not isinstance(value, datetime) or value.tzinfo is None:
        raise ValueError('Resource service time must be timezone-aware')
    return value.astimezone(timezone.utc)


def _utc(value):
    return value.replace(tzinfo=timezone.utc) if value.tzinfo is None else value.astimezone(timezone.utc)


def _role(role):
    return type(role) is str and role == ROLE


def _lock(session):
    dialect = session.get_bind().dialect.name
    if dialect == 'sqlite':
        result = session.execute(update(PlannerRuntimeSlot).where(PlannerRuntimeSlot.resource == RESOURCE).values(fence=PlannerRuntimeSlot.fence))
        if not result.rowcount:
            raise service.Conflict('Runtime resource is not provisioned')
        session.expire_all()
        slot = session.get(PlannerRuntimeSlot, RESOURCE)
    elif dialect == 'postgresql':
        slot = session.scalar(select(PlannerRuntimeSlot).where(PlannerRuntimeSlot.resource == RESOURCE).with_for_update().execution_options(populate_existing=True))
    else:
        raise ValueError('Runtime resource supports SQLite and PostgreSQL only')
    if slot is None:
        raise service.Conflict('Runtime resource is not provisioned')
    return slot


def lock_resource(session) -> None:
    """Acquire the global transaction lock only; grants NO admission authority.

    Trusted recovery/pause callers take this before any campaign lock. The lock
    remains held until the caller commits/rolls back; no slot/proof is changed.
    """
    _lock(session)


def _unresolved(session, *, except_round=None):
    query = select(PlannerCleanup.round_id).where(PlannerCleanup.local_state.in_(['pending', 'unknown']))
    if except_round is not None:
        query = query.where(PlannerCleanup.round_id != except_round)
    return list(session.scalars(query))


def _next(slot, at):
    if at < _utc(slot.changed_at) or slot.fence >= MAX_FENCE:
        raise ValueError('Non-monotonic resource time or exhausted fence')
    slot.fence += 1
    slot.changed_at = at


def _typed_grant(grant):
    if (type(grant) is not ProviderGrant or type(grant.fence) is not int or grant.fence <= 0 or
        type(grant.lease) is not rounds.ModelLease or type(grant.lease.generation) is not int or grant.lease.generation < 0 or
        type(grant.cleanup_ticket) is not cleanup.CleanupTicket or type(grant.cleanup_ticket.generation) is not int or grant.cleanup_ticket.generation < 0):
        return False
    return True


def _grant_matches(slot, grant):
    if not _typed_grant(grant):
        return False
    return (grant.resource == RESOURCE and slot.fence == grant.fence and slot.incarnation == grant.incarnation and
        slot.active_round_id == grant.lease.round_id == grant.cleanup_ticket.round_id and
        slot.campaign_id == grant.lease.campaign_id == grant.cleanup_ticket.campaign_id and
        grant.lease.generation == grant.cleanup_ticket.generation and
        grant.lease.lease == grant.cleanup_ticket.execution_lease and grant.lease.request_hash == grant.cleanup_ticket.request_hash)


def _cleanup_matches(session, grant):
    row = session.get(PlannerCleanup, grant.lease.round_id)
    ticket = grant.cleanup_ticket
    return row is not None and (row.campaign_id, row.generation, row.execution_lease, row.request_hash, row.owner_token) == (
        ticket.campaign_id, ticket.generation, ticket.execution_lease, ticket.request_hash, ticket.owner_token)


class _NoGrant(Exception):
    pass


def claim_provider_slot(session, round_id, *, incarnation, role, at, control):
    """Atomic slot+round claim+cleanup registration; None never grants invocation.

    No lazy provisioning: migration must seed the one server-fixed resource.
    Failed partial mutations roll back to a savepoint, even if caller commits.
    """
    at = _at(at)
    if type(incarnation) is not str:
        raise ValueError('Runtime incarnation must be a server UUID')
    try:
        if str(UUID(incarnation)) != incarnation:
            raise ValueError
    except ValueError:
        raise ValueError('Runtime incarnation must be a server UUID') from None
    if not _role(role) or control != 'RUNNING':
        return None
    slot = _lock(session)
    if slot.state != 'idle' or _unresolved(session) or session.get(PlannerCleanup, round_id) is not None:
        return None
    try:
        with session.begin_nested():
            row = session.get(PlannerRound, round_id)
            if row is None:
                raise _NoGrant
            state = session.get(PlannerState, row.campaign_id)
            if state is None or state.strategy != 'meta':
                raise _NoGrant
            lease = rounds.claim_model_round(session, round_id, at=at, control=control)
            if lease is None:
                raise _NoGrant
            ticket = cleanup.record_pending(session, lease, at=at, control=control)
            if ticket is None:
                raise _NoGrant
            # Legacy code may have registered unrelated cleanup before the
            # campaign lock; reject it without committing a partial round claim.
            if _unresolved(session, except_round=round_id):
                raise _NoGrant
            _next(slot, at)
            slot.state, slot.incarnation = 'active', incarnation
            slot.active_round_id, slot.campaign_id = round_id, lease.campaign_id
            slot.acquired_at, slot.owner_lost_at = at, None
            session.flush()
            grant = ProviderGrant(RESOURCE, slot.fence, incarnation, lease, ticket)
        return grant
    except _NoGrant:
        return None


def validate_provider_slot(session, grant, *, role, at, control, phase='pre_io'):
    """Repeat authority; pre_io needs pending, running/completion allow settled.

    An exact current grant on stopped control commits existing round revocation
    under the global lock. Stale or malformed grants cannot mutate any round.
    Validation never releases capacity or invents local settlement proof.
    """
    at = _at(at)
    if type(phase) is not str or phase not in {'pre_io', 'running', 'completion'}:
        raise ValueError('Unknown provider validation phase')
    if not _role(role) or not _typed_grant(grant):
        return False
    slot = _lock(session)
    if slot.state != 'active' or not _grant_matches(slot, grant):
        return False
    # Cleanup callbacks take only the campaign lock. Acquire it before reading
    # mutable cleanup/phase state, then refresh under both authority locks.
    service.lock(session, slot.campaign_id)
    if slot.state != 'active' or not _grant_matches(slot, grant) or not _cleanup_matches(session, grant):
        return False
    if control != 'RUNNING':
        rounds.heartbeat_model_round(session, grant.lease, at=at, control=control)
        return False
    if at < _utc(slot.acquired_at) or _unresolved(session, except_round=slot.active_round_id):
        return False
    allowed = {'pending'} if phase == 'pre_io' else {'pending', 'settled'}
    if session.get(PlannerCleanup, slot.active_round_id).local_state not in allowed:
        return False
    return rounds.heartbeat_model_round(session, grant.lease, at=at, control=control)


def mark_owner_lost(session, grant, *, role, at):
    """Explicit observation only; revoke fence, quarantine this cleanup owner."""
    at = _at(at)
    if not _role(role):
        return False
    slot = _lock(session)
    if slot.state != 'active' or not _grant_matches(slot, grant) or not _cleanup_matches(session, grant):
        return False
    with session.begin_nested():
        cleanup.mark_owner_lost(session, slot.active_round_id, at=at)
        # Existing round API preserves known completed outcomes, and marks only
        # still-authorized running work uncertain. No replay or refund follows.
        rounds.fail_model_round(session, grant.lease, code='model_transport_error', at=at, remote_unknown=True)
        _next(slot, at)
        slot.state, slot.owner_lost_at = 'unknown', at
        session.flush()
    return True


def release_settled_slot(session, *, expected_fence, expected_round_id, role, at):
    """Explicit reconciliation after exact associated cleanup is already settled.

    Supply current snapshot fence/round. Old grants cannot release a new owner.
    This observes proof; it NEVER acknowledges cleanup or clears unknown tasks.
    """
    at = _at(at)
    if type(expected_fence) is not int or expected_fence < 0:
        raise ValueError('Expected a nonnegative integer resource fence')
    if not _role(role):
        return False
    slot = _lock(session)
    if slot.state not in {'active', 'unknown'} or slot.fence != expected_fence or slot.active_round_id != expected_round_id:
        return False
    service.lock(session, slot.campaign_id)
    row = session.get(PlannerCleanup, slot.active_round_id)
    call = session.get(PlannerModelCall, slot.active_round_id)
    round_row = session.get(PlannerRound, slot.active_round_id)
    if (row is None or row.local_state != 'settled' or row.campaign_id != slot.campaign_id or
        round_row is None or call is None or row.generation != round_row.generation or row.request_hash != call.request_hash or
        at < _utc(row.settled_at)):
        return False
    _next(slot, at)
    slot.state = 'idle'
    slot.incarnation = slot.active_round_id = slot.campaign_id = slot.acquired_at = slot.owner_lost_at = None
    session.flush()
    return True


def admission_allowed(session, *, role, at, control):
    """Capacity gate only; caller must also enforce campaign/attempt authority."""
    at = _at(at)
    if not _role(role) or control != 'RUNNING':
        return False
    slot = _lock(session)
    return at >= _utc(slot.changed_at) and slot.state == 'idle' and not _unresolved(session)


def safe_snapshot(session):
    slot = session.get(PlannerRuntimeSlot, RESOURCE)
    if slot is None:
        raise service.Conflict('Runtime resource is not provisioned')
    return ResourceSnapshot(RESOURCE, slot.fence, slot.state, slot.active_round_id, slot.campaign_id,
        _utc(slot.acquired_at) if slot.acquired_at else None, _utc(slot.owner_lost_at) if slot.owner_lost_at else None,
        _utc(slot.changed_at), len(_unresolved(session)))
