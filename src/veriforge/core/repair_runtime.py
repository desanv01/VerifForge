"""Trusted process repair configuration and bounded durable continuation.

No environment switches or provider/tool construction. Main installs the same
fresh policy/control/root binding in dispatcher, worker and reconciler roles.
Unconfigured processes fence repair work while ordinary jobs retain their path.
"""
from dataclasses import dataclass
from pathlib import Path

from sqlalchemy import inspect, select

from . import repair_jobs as jobs
from .database import Attempt, now
from .repair_models import RepairCase, RepairJob


@dataclass(frozen=True)
class RepairRuntime:
    policy_reader: object = None
    control: object = None
    root: Path | None = None

    def __post_init__(self):
        if self.root is not None:
            path = Path(self.root)
            if not path.is_absolute():
                raise ValueError('Repair artifact root must be absolute')
            object.__setattr__(self, 'root', path)

    def policy(self):
        try:
            if not callable(self.policy_reader) or not callable(self.control) or self.root is None:
                return jobs.RepairPolicy()
            value = self.policy_reader()
            if type(value) is not jobs.RepairPolicy:
                return jobs.RepairPolicy()
            return jobs.RepairPolicy.model_validate(value.model_dump(mode='json'))
        except Exception:
            return jobs.RepairPolicy()

    def running(self):
        try:
            return 'RUNNING' if callable(self.control) and self.control() == 'RUNNING' else 'STOPPED'
        except Exception:
            return 'STOPPED'


_INSTALLED = RepairRuntime()


def install(runtime):
    """Trusted process startup only; never called by HTTP or queue arguments."""
    if type(runtime) is not RepairRuntime:
        raise ValueError('Expected trusted repair runtime')
    global _INSTALLED
    _INSTALLED = runtime


def resolve(runtime=None):
    value = _INSTALLED if runtime is None else runtime
    if type(value) is not RepairRuntime:
        raise ValueError('Expected trusted repair runtime')
    return value


def _schema(session):
    # No cache: a schema13 engine may later receive main's additive migration.
    inspector = inspect(session.connection())
    tables = [inspector.has_table(name) for name in ('repair_jobs', 'repair_cases')]
    if any(tables) and not all(tables):
        raise ValueError('Incomplete repair schema')
    return all(tables)


def allowed(session, attempt_id, *, runtime=None, at=None, lock=False):
    """None ordinary; bool repair. Unavailable/partial schema fails closed."""
    try:
        if not _schema(session):
            return None  # Confirmed legacy schema has no repair state to bypass.
        if lock:
            attempt = session.get(Attempt, attempt_id)
            case = session.scalar(select(RepairCase).where(RepairCase.campaign_id == attempt.campaign_id)) if attempt else None
            if case is not None:
                jobs._lock(session, case.job_id)  # Repair job -> campaign authority order.
        runtime = resolve(runtime)
        return jobs.attempt_allowed(session, attempt_id, policy=runtime.policy(),
                                    control=runtime.running, at=at)
    except Exception:
        return False


def advance_batch(sessions, *, runtime=None, clock=now, limit=100, attempt_id=None):
    if type(limit) is not int or not 1 <= limit <= 100:
        raise ValueError('Invalid repair batch bound')
    runtime = resolve(runtime)
    try:
        with sessions() as session:
            if not _schema(session):
                return {'examined': 0, 'failed': 0}
            query = select(RepairJob.id).where(RepairJob.status == 'active')
            if attempt_id is not None:
                attempt = session.get(Attempt, attempt_id)
                if attempt is None:
                    return {'examined': 0, 'failed': 0}
                query = query.join(RepairCase, RepairCase.job_id == RepairJob.id).where(
                    RepairCase.campaign_id == attempt.campaign_id)
            ids = list(session.scalars(query.order_by(RepairJob.updated_at, RepairJob.id).limit(limit)))
    except Exception:
        return {'examined': 0, 'failed': 1}
    failed = 0
    for identity in ids:
        try:
            with sessions.begin() as session:
                jobs.advance(session, identity, policy=runtime.policy(), control=runtime.running,
                             root=runtime.root, at=clock())
            # Transaction is committed before caller may dispatch new children.
        except Exception:
            failed += 1  # Existing durable state stays for bounded reconciliation.
    return {'examined': len(ids), 'failed': failed}
