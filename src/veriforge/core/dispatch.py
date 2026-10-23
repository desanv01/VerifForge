"""Queue delivery is retriable; PostgreSQL/SQLite is the source of truth."""
from __future__ import annotations

import os
from datetime import timedelta
from typing import Protocol

from sqlalchemy import or_, select

from .database import Attempt, Dispatch, now


class Dispatcher(Protocol):
    def enqueue(self, attempt_id: str) -> None: ...


class RQDispatcher:
    def __init__(self, redis_url: str | None = None):
        from redis import Redis
        from rq import Queue
        self.queue = Queue("veriforge", connection=Redis.from_url(redis_url or os.getenv("VERIFORGE_REDIS_URL", "redis://127.0.0.1:6379/0")))

    def enqueue(self, attempt_id: str) -> None:
        # Broker may receive a duplicate if process crashes before sent_at commits.
        # Claiming the durable queued attempt makes duplicate delivery harmless.
        self.queue.enqueue("veriforge.core.worker.execute_attempt", attempt_id, job_timeout=3720, result_ttl=0)


def dispatch_pending(sessions, dispatcher: Dispatcher) -> dict:
    sent = failed = 0
    with sessions() as session:
        ids = list(session.scalars(select(Dispatch.attempt_id).join(Attempt).where(or_(Dispatch.sent_at.is_(None), Dispatch.sent_at < now() - timedelta(seconds=60)), Attempt.execution_status == "queued").limit(100)))
    for attempt_id in ids:
        try:
            dispatcher.enqueue(attempt_id)
        except Exception as error:
            with sessions.begin() as session:
                row = session.get(Dispatch, attempt_id)
                # Store type only: broker URLs/credentials must never enter evidence.
                row.error = type(error).__name__
            failed += 1
        else:
            with sessions.begin() as session:
                row = session.get(Dispatch, attempt_id)
                row.sent_at, row.error = now(), None
            sent += 1
    return {"sent": sent, "failed": failed}
