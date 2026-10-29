"""Opt-in dedicated planner role. No live construction in imports/job fallback.

The RQ main thread owns delivery; one continuously running event-loop thread
owns provider tasks and late cleanup. All DB Sessions are created at the point
of use. Only the explicit enabled entry point may construct an env provider.
"""
from __future__ import annotations

import asyncio
from concurrent.futures import TimeoutError as FutureTimeout
from dataclasses import dataclass
import math
import threading
import time
from uuid import UUID

from . import planner_driver as driver, planner_resources as resources
from . import planner_cleanup as cleanup, planner_transport
from .database import PlannerIntent, PlannerModelCall, PlannerRound, PlannerState, now as service_now
from .runtime_control import ControlReader
from veriforge.planner.provider import MetaConfig, MetaProvider

QUEUE = "veriforge-planner"
TARGET = "veriforge.core.planner_runtime.execute_intent"
_SERVICE = None
_SERVICE_LOCK = threading.Lock()


def _bound(value, maximum):
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or not 0 < value <= maximum:
        raise ValueError("Invalid runtime bound")


def _id(value):
    try:
        return type(value) is str and str(UUID(value)) == value
    except (ValueError, TypeError, AttributeError):
        return False


def allowed_job(job, queue) -> bool:
    """Server-fixed target/ID only; broker itself remains a trusted boundary."""
    try:
        return (queue.name == QUEUE and job.func_name == TARGET and type(job.args) is tuple and
                len(job.args) == 1 and _id(job.args[0]) and type(job.kwargs) is dict and not job.kwargs)
    except Exception:
        return False


@dataclass(frozen=True)
class RuntimeStatus:
    running: bool
    accepting: bool
    reason: str
    cleanup_pending: bool = False


class _OwnedAuthority:
    def __init__(self, incarnation):
        self.inner = planner_transport.ResourceAuthority(incarnation)
        self.grants = {}  # Private, possibly tentative until caller commits.

    def claim(self, session, round_id, **kwargs):
        grant = self.inner.claim(session, round_id, **kwargs)
        if grant is not None:
            self.grants[round_id] = grant
        return grant

    def validate(self, session, grant, **kwargs):
        return self.inner.validate(session, grant, **kwargs)


class PlannerRuntime:
    def __init__(self, *, sessions, provider, control, now=service_now,
                 poll_seconds=0.1, settlement_seconds=1.0, operation_seconds=180):
        _bound(poll_seconds, 1)
        _bound(settlement_seconds, 5)
        _bound(operation_seconds, 180)
        if not isinstance(provider, MetaProvider):
            raise ValueError("Explicit Meta provider required")
        self.sessions, self.provider, self.control, self.now = sessions, provider, control, now
        self.poll_seconds, self.settlement_seconds, self.operation_seconds = poll_seconds, settlement_seconds, operation_seconds
        self._model = provider.model
        self._incarnation = resources.new_incarnation()
        self._authority = _OwnedAuthority(self._incarnation)
        self._operations = {}
        self._loop = self._thread = self._watcher = None
        self._started = threading.Event()
        self._loop_failed = threading.Event()
        self._stopping = threading.Event()
        self._blocked_cleanup = False
        self._closed = False
        self._clean_shutdown = False

    @property
    def incarnation(self):
        return self._incarnation

    def _control(self):
        return "STOPPED" if self._stopping.is_set() or self._loop_failed.is_set() else driver.read_control(self.control)

    def start(self):
        if self._closed or self._stopping.is_set():
            alive = self._thread is not None and self._thread.is_alive()
            return RuntimeStatus(alive, False, "cleanup_pending" if alive else "stopped", alive or self._blocked_cleanup)
        if self._thread is not None and self._thread.is_alive():
            ready = self._loop is not None and self._loop.is_running() and self._started.is_set()
            return RuntimeStatus(True, ready and self._control() == "RUNNING", "running" if ready else "initializing", not ready)
        if self._control() != "RUNNING":
            return RuntimeStatus(False, False, "paused")
        try:
            with self.sessions.begin() as session:
                ready = resources.admission_allowed(session, role=resources.ROLE,
                    at=driver.stamp(self.now), control=self._control())
        except Exception:
            return RuntimeStatus(False, False, "state_unavailable", True)
        if not ready:
            self._blocked_cleanup = True
            return RuntimeStatus(False, False, "resource_blocked", True)
        self._thread = threading.Thread(target=self._run_loop, name="veriforge-planner-owner", daemon=True)
        self._thread.start()
        if not self._started.wait(2) or self._loop_failed.is_set():
            self._stopping.set()
            return RuntimeStatus(False, False, "loop_unavailable", True)
        return RuntimeStatus(True, True, "running")

    def _run_loop(self):
        loop = None
        try:
            loop = asyncio.new_event_loop()
            self._loop = loop
            asyncio.set_event_loop(loop)
            loop.set_exception_handler(lambda owner_loop, context: self._loop_failed.set())
            self._watcher = loop.create_task(self._watch_control())
            loop.call_soon(self._started.set)
            if self._stopping.is_set():
                self._watcher.cancel()
                loop.call_soon(loop.stop)
            loop.run_forever()
        except Exception:
            self._loop_failed.set()
            self._started.set()
        finally:
            # shutdown only stops the loop after cooperative watcher settlement.
            # A pending provider keeps this daemon loop alive until supervisor
            # loss or an explicit later drain; never call run() teardown here.
            if loop is not None:
                loop.close()

    async def _watch_control(self):
        while True:
            if self._control() != "RUNNING":
                for event in tuple(self._operations.values()):
                    event.set()
            await asyncio.sleep(self.poll_seconds)

    def request_stop(self):
        """Nonblocking signal/timeout request; no cross-thread Event mutation."""
        self._stopping.set()
        if self._loop is not None and self._thread is not None and self._thread.is_alive():
            try:
                self._loop.call_soon_threadsafe(self._cancel_operations)
            except RuntimeError:
                self._loop_failed.set()

    def _cancel_operations(self):
        for event in tuple(self._operations.values()):
            event.set()

    async def _invoke(self, intent_id):
        if self._control() != "RUNNING" or intent_id in self._operations:
            return {"reason": "not_eligible"}
        with self.sessions() as session:
            intent = session.get(PlannerIntent, intent_id)
            row = session.get(PlannerRound, intent.round_id) if intent and intent.kind == "model" else None
            state = session.get(PlannerState, row.campaign_id) if row else None
            call = session.get(PlannerModelCall, row.id) if row else None
            if (state is None or call is None or state.strategy != "meta" or intent.campaign_id != row.campaign_id or
                    intent.generation != row.generation or state.generation != row.generation or
                    state.phase != "round_queued" or row.status != "queued"):
                return {"reason": "not_eligible"}
            if call.requested_model != self._model or self.provider.model != self._model:
                return {"reason": "configured_model_mismatch"}
            round_id = row.id
        event = asyncio.Event()
        self._operations[intent_id] = event
        try:
            result = await planner_transport.run_model_intent(round_id, sessions=self.sessions, provider=self.provider,
                authority=self._authority, now=self.now, control=self._control, cancel_event=event,
                poll_seconds=self.poll_seconds, settlement_seconds=self.settlement_seconds)
            return {"reason": result.reason, "applied": result.applied, "cleanup_pending": result.cleanup_pending}
        finally:
            self._operations.pop(intent_id, None)

    def handle_model_intent(self, intent_id):
        if (not _id(intent_id) or self._control() != "RUNNING" or self._loop is None or
                self._thread is None or not self._thread.is_alive()):
            return {"reason": "runtime_unavailable"}
        if threading.current_thread() is self._thread:
            return {"reason": "runtime_thread_invalid"}
        coroutine = self._invoke(intent_id)
        try:
            future = asyncio.run_coroutine_threadsafe(coroutine, self._loop)
        except RuntimeError:
            coroutine.close()
            return {"reason": "runtime_unavailable"}
        try:
            return future.result(self.operation_seconds)
        except FutureTimeout:
            # Keep the root/children on the owner loop. Cancelling only the
            # concurrent Future would hide its eventual durable safe outcome.
            def cancel_one():
                event = self._operations.get(intent_id)
                if event is not None:
                    event.set()
            self._loop.call_soon_threadsafe(cancel_one)
            future.add_done_callback(lambda completed: completed.exception() if not completed.cancelled() else None)
            return {"reason": "runtime_timeout", "cleanup_pending": True}
        except Exception:
            return {"reason": "runtime_error", "cleanup_pending": True}

    def execute(self, intent_id):
        if not _id(intent_id) or self._control() != "RUNNING" or self._loop is None:
            return {"accepted": False, "reason": "runtime_unavailable"}
        try:
            return driver.execute_intent(intent_id, sessions=self.sessions, now=self.now,
                control=self._control, model_handler=self.handle_model_intent)
        except Exception:
            return {"accepted": False, "reason": "runtime_error"}

    def _local_pending(self):
        with self.sessions() as session:
            for grant in tuple(self._authority.grants.values()):
                view = cleanup.read_cleanup(session, grant.lease.round_id)
                if view is not None and view.local_state != "settled":
                    return True
        return False

    async def _drain(self, seconds):
        self._cancel_operations()
        deadline = time.monotonic() + seconds
        try:
            while self._operations or self._local_pending():
                if time.monotonic() >= deadline:
                    for grant in tuple(self._authority.grants.values()):
                        with self.sessions.begin() as session:
                            # Exact own-grant association only. A replacement
                            # runtime never reconstructs the old owner's proof.
                            resources.mark_owner_lost(session, grant, role=resources.ROLE, at=driver.stamp(self.now))
                    return False
                await asyncio.sleep(min(0.01, seconds))
            self._watcher.cancel()
            await asyncio.gather(self._watcher, return_exceptions=True)
            return True
        except Exception:
            return False  # DB/clock ownership failure retains unresolved state.

    def shutdown(self, *, grace_seconds=1.0):
        _bound(grace_seconds, 5)
        self.request_stop()
        if self._thread is None or not self._thread.is_alive():
            pending = self._blocked_cleanup or bool(self._authority.grants) and not self._clean_shutdown
            self._closed = True
            return RuntimeStatus(False, False, "stopped", pending)
        if threading.current_thread() is self._thread:
            return RuntimeStatus(True, False, "cleanup_pending", True)
        # Thread liveness does not imply that new_event_loop has returned. Do
        # not even construct a drain coroutine until a running owned loop exists.
        # A late initializer observes _stopping and drains its startup watcher
        # before exiting; no task/cleanup proof is inferred from this timeout.
        if self._loop is None or not self._started.is_set() or not self._loop.is_running():
            return RuntimeStatus(self._thread.is_alive(), False, "cleanup_pending", True)
        deadline = time.monotonic() + grace_seconds
        coroutine = self._drain(grace_seconds * 0.8)
        try:
            future = asyncio.run_coroutine_threadsafe(coroutine, self._loop)
        except RuntimeError:
            coroutine.close()
            return RuntimeStatus(self._thread.is_alive(), False, "cleanup_pending", True)
        try:
            settled = future.result(max(0.001, deadline - time.monotonic()))
        except Exception:
            settled = False
        if settled:
            self._loop.call_soon_threadsafe(self._loop.stop)
            self._thread.join(max(0, deadline - time.monotonic()))
            self._closed = not self._thread.is_alive()
            self._clean_shutdown = self._closed
            return RuntimeStatus(not self._closed, False, "stopped" if self._closed else "cleanup_pending", not self._closed)
        # Leave the loop alive for original-owner late acknowledgment. A role
        # supervisor may terminate the process; pending/unknown remains durable.
        return RuntimeStatus(True, False, "cleanup_pending", True)


def execute_intent(intent_id):
    """Only fixed ID queue target. No default provider/config/DB construction."""
    if _SERVICE is None:
        return {"accepted": False, "reason": "runtime_unavailable"}
    return _SERVICE.execute(intent_id)


def _environment_provider():
    return MetaProvider(MetaConfig.from_env())


def _rq_worker(runtime):
    # Deliberately deferred imports/connections: import/disabled/test paths do
    # not construct an RQ worker or access broker environment configuration.
    import os
    from redis import Redis
    from rq import Queue, SimpleWorker
    connection = Redis.from_url(os.environ.get("VERIFORGE_REDIS_URL", "redis://127.0.0.1:6379/0"))
    queue = Queue(QUEUE, connection=connection)
    class DedicatedWorker(SimpleWorker):
        def execute_job(self, job, queue):
            if not allowed_job(job, queue):
                raise RuntimeError("runtime_job_rejected")
            return super().execute_job(job, queue)
        def request_stop(self, signum, frame):
            runtime.request_stop()
            return super().request_stop(signum, frame)
        def request_force_stop(self, signum, frame):
            runtime.request_stop()
            return super().request_force_stop(signum, frame)
    return DedicatedWorker([queue], connection=connection)


def serve(*, enabled=False, sessions=None, control=None, provider_factory=None, worker_factory=None, now=service_now):
    """Explicit enabled live entrypoint; all other construction is injection-only."""
    global _SERVICE
    if enabled is not True:
        return RuntimeStatus(False, False, "disabled")
    if control is None or driver.read_control(control) != "RUNNING":
        return RuntimeStatus(False, False, "paused")
    if _SERVICE is not None:
        return RuntimeStatus(False, False, "runtime_already_installed")
    if not _SERVICE_LOCK.acquire(blocking=False):
        return RuntimeStatus(False, False, "runtime_already_installed")
    runtime = None
    try:
        if sessions is None:
            from .database import make_engine, session_factory
            sessions = session_factory(make_engine())
        with sessions.begin() as session:
            ready = resources.admission_allowed(session, role=resources.ROLE, at=driver.stamp(now), control=driver.read_control(control))
        if not ready:
            return RuntimeStatus(False, False, "resource_blocked", True)
        provider = (provider_factory if provider_factory is not None else _environment_provider)()
        runtime = PlannerRuntime(sessions=sessions, provider=provider, control=control, now=now)
        status = runtime.start()
        if not status.accepting:
            return status
        _SERVICE = runtime
        try:
            worker = (worker_factory if worker_factory is not None else _rq_worker)(runtime)
            worker.work()
        finally:
            _SERVICE = None
            status = runtime.shutdown(grace_seconds=5)
        return status
    except Exception:
        return RuntimeStatus(False, False, "runtime_error", True)
    finally:
        _SERVICE_LOCK.release()


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description="Dedicated opt-in trusted planner runtime")
    parser.add_argument("--enable-live", action="store_true")
    parser.add_argument("--control-path", required=True)
    options = parser.parse_args()
    result = serve(enabled=options.enable_live, control=ControlReader(options.control_path))
    print(result.reason, flush=True)
