"""Explicit fake provider and worker interfaces; temporary SQLite, no live role."""
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
import asyncio
import json
import threading
import time
from types import SimpleNamespace

from alembic import command
from alembic.config import Config
from pydantic import SecretStr
import pytest
from sqlalchemy import select

from veriforge.contracts import Experiment
from veriforge.core import planner_cleanup as cleanup, planner_resources as resources, planner_rounds as rounds, service
from veriforge.core import planner_driver as driver, planner_runtime as role
from veriforge.core.catalog import seed_catalog
from veriforge.core.database import Attempt, PlannerCleanup, PlannerIntent, PlannerModelCall, PlannerRound, PlannerState, make_engine, session_factory
from veriforge.core.planner_experiments import resolve_attempt_experiment
from veriforge.planner.models import ProviderCompletion
from veriforge.planner.provider import MetaConfig, MetaProvider

AT = datetime(2026, 10, 30, tzinfo=timezone.utc)
MODEL = "runtime-explicit-fake"
SECRET = "synthetic-key-canary-not-a-credential"


@pytest.fixture(autouse=True)
def no_live_construction(monkeypatch):
    def forbidden(*args, **kwargs): pytest.fail("test constructed live config or RQ worker")
    monkeypatch.setattr(MetaConfig, "from_env", forbidden)
    monkeypatch.setattr(role, "_rq_worker", forbidden)


@pytest.fixture
def db(tmp_path):
    engine = make_engine("sqlite:///" + (tmp_path / "runtime.db").as_posix())
    config = Config("alembic.ini")
    with engine.begin() as connection:
        config.attributes["connection"] = connection
        command.upgrade(config, "head")
    sessions = session_factory(engine)
    with sessions.begin() as session: seed_catalog(session)
    yield sessions
    engine.dispose()


def queued(sessions, key="seed"):
    with sessions.begin() as session:
        exp = Experiment(experiment_id=key, fixture_id="fifo_correct", objective_ids=("FIFO_BEHAVIOR",),
            budget={"max_cycles": 8, "wall_time_seconds": 2}, operations=({"op": "reset"},))
        campaign = service.create_campaign(session, "fifo_correct", exp.model_dump(mode="json"), key, completion_policy="sequential")
        service.start(session, campaign.id)
        attempt = session.scalar(select(Attempt).where(Attempt.campaign_id == campaign.id))
        token, _, _ = service.claim(session, attempt.id)
        record = resolve_attempt_experiment(session, attempt)
        service.finalize(session, attempt.id, token, dict(attempt_id="synthetic-seed", experiment_id=record.wire_id,
            execution_status="completed", verdict="pass_within_scope", identity={**record.identity, "environment_hash": "b" * 64}, usage={"cycles": 1}))
        rounds.attach_planner(session, campaign.id, limits=dict(max_total_cycles=80, max_cycles_per_experiment=8,
            max_wall_seconds=60, max_execution_seconds=2, max_model_seconds=2), requested_model=MODEL, at=AT, strategy="meta")
        row = rounds.advance(session, campaign.id, at=AT)
        session.flush()
        intent = session.scalar(select(PlannerIntent.id).where(PlannerIntent.round_id == row.id))
        return campaign.id, row.id, intent


class FakeProvider(MetaProvider):
    def __init__(self, effect=None):
        super().__init__(MetaConfig(model=MODEL, api_key=SecretStr(SECRET)))
        self.effect, self.calls, self.threads, self.loops = effect, 0, [], []
    async def complete(self, context, **kwargs):
        self.calls += 1
        self.threads.append(threading.get_ident())
        self.loops.append(asyncio.get_running_loop())
        if self.effect: await self.effect()
        return ProviderCompletion(model=MODEL, usage={"input_tokens": 2, "output_tokens": 3}, finish_reason="stop",
            content=json.dumps({"proposal": {"kind": "stop", "reason": "finite fake test"}}))


def runtime(sessions, provider=None, **changes):
    values = dict(sessions=sessions, provider=provider or FakeProvider(), control=lambda: "RUNNING",
        now=lambda: AT + timedelta(milliseconds=200), poll_seconds=0.01, settlement_seconds=0.02)
    values.update(changes)
    return role.PlannerRuntime(**values)


def wait_for(predicate, timeout=2):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate(): return
        time.sleep(0.01)
    assert predicate()


def view(sessions, round_id):
    with sessions() as session: return cleanup.read_cleanup(session, round_id)


def test_disabled_and_paused_entrypoint_never_load_env_config_or_worker(monkeypatch):
    def forbidden(*args, **kwargs): pytest.fail("disabled/test path read live config")
    monkeypatch.setattr(MetaConfig, "from_env", forbidden)
    assert role.serve().reason == "disabled"
    assert role.serve(enabled=True, control=lambda: "PAUSED", provider_factory=forbidden, worker_factory=forbidden).reason == "paused"
    assert role.execute_intent("invalid")["reason"] == "runtime_unavailable"


def test_continuous_loop_survives_between_id_jobs_and_incarnation_is_immutable(db):
    first = queued(db, "first")
    provider, owner = FakeProvider(), None
    owner = runtime(db, provider)
    identity = owner.incarnation
    try:
        assert owner.start().accepting
        assert owner.execute(first[2])["accepted"] and provider.calls == 1
        assert view(db, first[1]).local_state == "settled"
        driver.release_settled_resource(db, now=lambda: AT + timedelta(milliseconds=200))
        second = queued(db, "second")
        time.sleep(0.02)  # Loop continues while there is no broker job.
        assert owner.execute(second[2])["accepted"] and provider.calls == 2
        assert provider.loops[0] is provider.loops[1]
        assert provider.threads[0] == provider.threads[1] != threading.get_ident()
        assert owner.incarnation == identity
        with pytest.raises(AttributeError): owner.incarnation = resources.new_incarnation()
        assert SECRET not in repr(owner.execute(second[2]))
    finally:
        assert not owner.shutdown(grace_seconds=1).cleanup_pending


def test_replacement_startup_is_blocked_without_takeover(db):
    _, round_id, _ = queued(db)
    with db.begin() as session:
        grant = resources.claim_provider_slot(session, round_id, incarnation=resources.new_incarnation(),
            role=resources.ROLE, at=AT, control="RUNNING")
        before = resources.safe_snapshot(session)
    owner = runtime(db)
    assert owner.start().reason == "resource_blocked"
    assert owner.shutdown().cleanup_pending
    with db() as session:
        assert resources.safe_snapshot(session) == before
        assert cleanup.read_cleanup(session, round_id).local_state == "pending"


@pytest.mark.parametrize("mutation", ["target", "queue", "extra", "kwargs", "id"])
def test_queue_allowlist_rejects_untrusted_shape(mutation):
    job = SimpleNamespace(func_name=role.TARGET, args=(resources.new_incarnation(),), kwargs={})
    queue = SimpleNamespace(name=role.QUEUE)
    assert role.allowed_job(job, queue)
    if mutation == "target": job.func_name = "arbitrary.function"
    elif mutation == "queue": queue.name = "veriforge"
    elif mutation == "extra": job.args += ("private-body",)
    elif mutation == "kwargs": job.kwargs = {"model_handler": "untrusted"}
    else: job.args = ("invalid",)
    assert not role.allowed_job(job, queue)


def test_configured_model_mismatch_never_claims(db):
    campaign, round_id, intent_id = queued(db)
    provider = FakeProvider()
    provider.model = "other-configured-model"
    owner = runtime(db, provider)
    try:
        assert owner.start().accepting
        assert owner.handle_model_intent(intent_id)["reason"] == "configured_model_mismatch"
        assert provider.calls == 0 and view(db, round_id) is None
        with db() as session: assert session.get(PlannerRound, round_id).status == "queued"
    finally: owner.shutdown()


def test_pause_cooperatively_cancels_and_preserves_uncertainty(db):
    campaign, round_id, intent_id = queued(db)
    entered, current = threading.Event(), ["RUNNING"]
    async def stall():
        entered.set()
        await asyncio.Event().wait()
    provider = FakeProvider(stall)
    owner = runtime(db, provider, control=lambda: current[0])
    try:
        assert owner.start().accepting
        with ThreadPoolExecutor(max_workers=1) as pool:
            result = pool.submit(owner.execute, intent_id)
            assert entered.wait(1)
            current[0] = "PAUSED"
            result.result(2)
        with db() as session:
            assert session.get(PlannerState, campaign).stop_reason == "paused"
            assert session.get(PlannerModelCall, round_id).outcome == "remote_outcome_unknown"
        assert view(db, round_id).local_state == "settled"
        assert not owner.execute(intent_id)["accepted"] and provider.calls == 1
    finally: assert not owner.shutdown().cleanup_pending


@pytest.mark.parametrize("quarantine_failure", [False, True])
def test_noncooperative_shutdown_is_bounded_and_late_owner_can_settle(db, monkeypatch, quarantine_failure):
    campaign, round_id, intent_id = queued(db)
    entered, release = threading.Event(), threading.Event()
    async def ignore_cancel():
        entered.set()
        while not release.is_set():
            try: await asyncio.sleep(0.005)
            except asyncio.CancelledError: continue
    provider = FakeProvider(ignore_cancel)
    owner = runtime(db, provider)
    failed = threading.Event()
    if quarantine_failure:
        def unavailable(*args, **kwargs):
            failed.set()
            raise RuntimeError("private DB diagnostic must not escape")
        monkeypatch.setattr(resources, "mark_owner_lost", unavailable)
    try:
        assert owner.start().accepting
        with ThreadPoolExecutor(max_workers=1) as pool:
            result = pool.submit(owner.execute, intent_id)
            assert entered.wait(1)
            before = time.monotonic()
            status = owner.shutdown(grace_seconds=0.1)
            assert status.cleanup_pending and status.running and time.monotonic() - before < 0.4
            result.result(2)
        def quarantined():
            with db() as session:
                return resources.safe_snapshot(session).state == "unknown"
        # The outer wait is bounded even when DB/scheduling consumes its grace;
        # quarantine may finish on the retained owner loop after that return.
        wait_for(failed.is_set if quarantine_failure else quarantined)
        with db() as session:
            assert resources.safe_snapshot(session).state == ("active" if quarantine_failure else "unknown")
            assert cleanup.read_cleanup(session, round_id).local_state == ("pending" if quarantine_failure else "unknown")
            saved_outcome = session.get(PlannerModelCall, round_id).outcome
        release.set()
        wait_for(lambda: view(db, round_id).local_state == "settled")
        with db() as session:
            assert resources.safe_snapshot(session).state == ("active" if quarantine_failure else "unknown")
            assert session.get(PlannerModelCall, round_id).outcome == saved_outcome
        assert provider.calls == 1
    finally:
        release.set()
        wait_for(lambda: view(db, round_id).local_state == "settled")
        assert not owner.shutdown(grace_seconds=1).running


def test_outer_timeout_requests_cancel_without_resend(db):
    _, round_id, intent_id = queued(db)
    async def stall(): await asyncio.Event().wait()
    provider = FakeProvider(stall)
    owner = runtime(db, provider, operation_seconds=0.1)
    try:
        assert owner.start().accepting
        result = owner.handle_model_intent(intent_id)
        assert result["reason"] == "runtime_timeout" and result["cleanup_pending"]
        wait_for(lambda: view(db, round_id) is not None and view(db, round_id).local_state == "settled")
        owner.execute(intent_id)
        assert provider.calls == 1
    finally: owner.shutdown()


def test_enabled_entrypoint_uses_only_injected_fake_factory_and_worker(db, monkeypatch):
    _, _, intent_id = queued(db)
    provider = FakeProvider()
    def forbidden(*args): pytest.fail("test loaded environment credentials")
    monkeypatch.setattr(MetaConfig, "from_env", forbidden)
    class FakeWorker:
        def __init__(self, owner): self.owner = owner
        def work(self):
            assert role.execute_intent(intent_id)["accepted"]
    status = role.serve(enabled=True, sessions=db, control=lambda: "RUNNING", provider_factory=lambda: provider,
        worker_factory=FakeWorker, now=lambda: AT + timedelta(milliseconds=200))
    assert not status.running and not status.cleanup_pending and provider.calls == 1
    assert role._SERVICE is None


def test_unexpected_provider_error_has_no_raw_diagnostic(db):
    _, _, intent_id = queued(db)
    async def fail(): raise RuntimeError("sensitive connection/header must not escape")
    owner = runtime(db, FakeProvider(fail))
    try:
        owner.start()
        result = owner.handle_model_intent(intent_id)
        assert "sensitive" not in repr(result) and SECRET not in repr(result)
    finally: owner.shutdown()


def test_fresh_sessions_close_before_provider_await_and_canary_never_persists(db):
    campaign, round_id, intent_id = queued(db)
    class Sessions:
        def __init__(self): self.active = 0; self.opened = []
        @contextmanager
        def begin(self):
            self.active += 1
            try:
                with db.begin() as session:
                    self.opened.append(session)
                    yield session
            finally: self.active -= 1
        @contextmanager
        def __call__(self):
            self.active += 1
            try:
                with db() as session:
                    self.opened.append(session)
                    yield session
            finally: self.active -= 1
    sessions = Sessions()
    async def inspect(): assert sessions.active == 0
    owner = runtime(sessions, FakeProvider(inspect))
    try:
        assert owner.start().accepting
        result = owner.execute(intent_id)
        assert result["accepted"] and SECRET not in repr(result)
        with db() as session:
            call = session.get(PlannerModelCall, round_id)
            assert SECRET.encode() not in call.request_body
            assert SECRET not in json.dumps(session.get(PlannerState, campaign).policy)
        assert len({id(item) for item in sessions.opened}) == len(sessions.opened)
    finally: owner.shutdown()


def test_registration_commit_failure_has_no_provider_and_tentative_grant_no_proof(db):
    _, round_id, intent_id = queued(db)
    class Sessions:
        @contextmanager
        def begin(self):
            with db.begin() as session:
                yield session
                if session.get(PlannerCleanup, round_id) is not None:
                    raise RuntimeError("synthetic registration commit loss")
        def __call__(self): return db()
    provider, owner = FakeProvider(), None
    owner = runtime(Sessions(), provider)
    try:
        assert owner.start().accepting
        assert owner.handle_model_intent(intent_id)["reason"] == "runtime_error"
        assert provider.calls == 0 and view(db, round_id) is None
        with db() as session:
            assert session.get(PlannerRound, round_id).status == "queued"
            assert resources.safe_snapshot(session).state == "idle"
    finally: assert not owner.shutdown().cleanup_pending


def test_repeated_signal_requests_do_not_replace_incarnation_or_revive_admission(db):
    _, _, intent_id = queued(db)
    owner = runtime(db)
    assert owner.start().accepting
    incarnation = owner.incarnation
    owner.request_stop()
    owner.request_stop()
    assert owner.incarnation == incarnation and not owner.execute(intent_id)["accepted"]
    assert not owner.shutdown().running
    assert not owner.shutdown().cleanup_pending


def test_blocked_enabled_role_never_constructs_provider_or_worker(db):
    _, round_id, _ = queued(db)
    with db.begin() as session:
        resources.claim_provider_slot(session, round_id, incarnation=resources.new_incarnation(), role=resources.ROLE, at=AT, control="RUNNING")
    def forbidden(*args): pytest.fail("blocked role constructed provider/worker")
    status = role.serve(enabled=True, sessions=db, control=lambda: "RUNNING", now=lambda: AT,
        provider_factory=forbidden, worker_factory=forbidden)
    assert status.reason == "resource_blocked" and status.cleanup_pending


def test_shutdown_during_slow_initialization_never_submits_to_missing_loop(db, monkeypatch):
    entered, release = threading.Event(), threading.Event()
    actual = role.asyncio.new_event_loop
    def slow():
        entered.set()
        assert release.wait(2)
        return actual()
    monkeypatch.setattr(role.asyncio, "new_event_loop", slow)
    owner = runtime(db)
    monkeypatch.setattr(owner._started, "wait", lambda timeout: entered.wait(1) and False)
    try:
        assert owner.start().reason == "loop_unavailable"
        assert owner._loop is None and owner._thread.is_alive()
        def forbidden(*args, **kwargs): pytest.fail("submitted coroutine while loop was unavailable")
        monkeypatch.setattr(role.asyncio, "run_coroutine_threadsafe", forbidden)
        before = time.monotonic()
        result = owner.shutdown(grace_seconds=0.05)
        assert result.cleanup_pending and not result.accepting and time.monotonic() - before < 0.3
        assert owner.start().cleanup_pending and not owner.start().accepting
    finally:
        release.set()
        owner._thread.join(2)
        assert not owner._thread.is_alive()
        assert owner.provider.calls == 0
        assert not owner.shutdown().cleanup_pending
