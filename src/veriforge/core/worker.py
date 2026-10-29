"""Separate RQ process entrypoint. Never invoked by an HTTP request."""
from __future__ import annotations

import os
import threading
import time
from pathlib import Path

from veriforge.contracts import AttemptResult
from veriforge.adapters import get_adapter
from .families import experiment_adapter
from .database import make_engine, session_factory
from . import service


def installed_identity(experiment) -> dict:
    return experiment_adapter(experiment).expected_identity(experiment)


def validate_identity(experiment, result: AttemptResult):
    if result.identity is None:
        return
    if any(getattr(result.identity, key) != value for key, value in installed_identity(experiment).items()):
        raise ValueError("Worker evidence does not match trusted experiment/source/checker/contract")


class LeaseCancellation:
    def __init__(self, sessions, attempt_id, lease):
        self.sessions, self.attempt_id, self.lease = sessions, attempt_id, lease
        self.last_check = 0.0
        self.cancelled = False

    def is_set(self):
        if time.monotonic() - self.last_check > 0.2:
            with self.sessions() as session:
                self.cancelled = not service.authorized(session, self.attempt_id, self.lease)
            self.last_check = time.monotonic()
        return self.cancelled


def execute_attempt(attempt_id: str, *, sessions=None, runner=None, artifact_root=None):
    sessions = sessions or session_factory(make_engine())
    with sessions.begin() as session:
        claimed = service.claim(session, attempt_id)
    if claimed is None:
        return {"accepted": False}
    lease, plan, project_id = claimed
    stop = threading.Event()

    def keepalive():
        while not stop.wait(10):
            try:
                with sessions.begin() as session:
                    if not service.heartbeat(session, attempt_id, lease):
                        return
            except Exception:
                return  # stale lease reconciliation will interrupt; cannot report success

    heartbeat_thread = threading.Thread(target=keepalive, daemon=True)
    heartbeat_thread.start()
    try:
        from .database import Campaign, ReplayLink
        with sessions() as session:
            from .database import Attempt
            campaign = session.get(Campaign, session.get(Attempt, attempt_id).campaign_id)
            descriptor = get_adapter(campaign.adapter_id)
            experiment = descriptor.validate_experiment(plan)
            is_replay = session.get(ReplayLink, campaign.id) is not None
            if campaign.identity != installed_identity(experiment):
                raise ValueError("Installed execution provenance changed since campaign creation")
        if runner is None:
            runner = descriptor.resolve_runner("container" if os.name == "nt" else "native")
        output = Path(artifact_root or os.getenv("VERIFORGE_ARTIFACT_ROOT", "artifacts")) / attempt_id
        result = AttemptResult.model_validate(runner(experiment, output, cancel_event=LeaseCancellation(sessions, attempt_id, lease)))
        validate_identity(experiment, result)
        if is_replay:
            from .artifacts import verify_replay_output
            verify_replay_output(output, experiment, result)
        with sessions.begin() as session:
            accepted = service.finalize(session, attempt_id, lease, result.model_dump(mode="json"))
        return {"accepted": accepted}
    except Exception as error:
        with sessions.begin() as session:
            accepted = service.finalize(session, attempt_id, lease, {"error_type": type(error).__name__, "uncertainties": ["Worker failed before a checked result was finalized"]}, infrastructure_error=True)
        return {"accepted": accepted, "infrastructure_error": True}
    finally:
        stop.set()
        heartbeat_thread.join(timeout=2)


def reconcile_once():
    from .dispatch import RQDispatcher, dispatch_pending
    from .artifacts import dispatch_exports, recover_exports
    sessions = session_factory(make_engine())
    with sessions.begin() as session:
        interrupted = service.recover_stale(session)
        interrupted_exports = recover_exports(session)
    dispatcher = RQDispatcher()
    return {**dispatch_pending(sessions, dispatcher), "interrupted": interrupted,
            "exports": dispatch_exports(sessions, dispatcher), "interrupted_exports": interrupted_exports}


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description="Reconcile durable VerifForge dispatch intent")
    parser.add_argument("--loop", action="store_true")
    args = parser.parse_args()
    while True:
        print(reconcile_once(), flush=True)
        if not args.loop:
            break
        time.sleep(2)
