"""Separate RQ process entrypoint. Never invoked by an HTTP request."""
from __future__ import annotations

import hashlib
import json
import os
import threading
import time
from pathlib import Path

from veriforge.contracts import AttemptResult, Experiment, canonical_hash
from .database import make_engine, session_factory
from . import service


def validate_identity(experiment: Experiment, result: AttemptResult):
    if result.identity is None:
        return
    from veriforge.rtl import oracle, reviewed_contract
    from veriforge.rtl.runner import FIXTURES, ROOT
    def hash_bytes(data):
        return hashlib.sha256(data).hexdigest()
    def encode(value):
        return json.dumps(value, sort_keys=True, separators=(",", ":")).encode()
    expected = {
        "experiment_hash": canonical_hash(experiment),
        "checker_hash": hash_bytes(Path(oracle.__file__).read_bytes()),
        "contract_hash": canonical_hash(reviewed_contract(experiment.parameters)),
        "source_hash": hash_bytes(encode({"source": hash_bytes((ROOT / "targets/fifo/fifo.sv").read_bytes()), "variant": FIXTURES[experiment.fixture_id], "parameters": experiment.parameters.model_dump()})),
    }
    if any(getattr(result.identity, key) != value for key, value in expected.items()):
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
        if runner is None:
            if os.name == "nt":
                from veriforge.rtl import run_container_experiment
                runner = run_container_experiment
            else:
                from veriforge.rtl import run_experiment
                runner = run_experiment
        experiment = Experiment.model_validate(plan)
        output = Path(artifact_root or os.getenv("VERIFORGE_ARTIFACT_ROOT", "artifacts")) / attempt_id
        result = AttemptResult.model_validate(runner(experiment, output, cancel_event=LeaseCancellation(sessions, attempt_id, lease)))
        validate_identity(experiment, result)
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
    sessions = session_factory(make_engine())
    with sessions.begin() as session:
        interrupted = service.recover_stale(session)
    return {**dispatch_pending(sessions, RQDispatcher()), "interrupted": interrupted}


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
