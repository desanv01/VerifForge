"""Driver qualification: temporary SQLite, fake broker/provider/runner only."""
import asyncio
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
import json

from alembic import command
from alembic.config import Config
from pydantic import SecretStr
import pytest
from sqlalchemy import func, select

from veriforge.contracts import AttemptResult, Experiment
from veriforge.core import dispatch, planner_cleanup as cleanup, planner_driver as driver
from veriforge.core import planner_resources as resources, planner_rounds as rounds, planner_transport, service, worker
from veriforge.core.catalog import seed_catalog
from veriforge.core.database import (Attempt, Campaign, Finding, PlannerFeedback, PlannerIntent,
    PlannerModelCall, PlannerReservation, PlannerRound, PlannerState, make_engine, session_factory)
from veriforge.core.planner_experiments import resolve_attempt_experiment
from veriforge.planner.models import InvestigationLimits, ProviderCompletion
from veriforge.planner.provider import MetaConfig, MetaProvider, ProviderError

AT = datetime(2026, 10, 30, tzinfo=timezone.utc)
MODEL = "driver-explicit-fake-meta"


@pytest.fixture
def db(tmp_path):
    engine = make_engine("sqlite:///" + (tmp_path / "driver.db").as_posix())
    config = Config("alembic.ini")
    with engine.begin() as connection:
        config.attributes["connection"] = connection
        command.upgrade(config, "head")
    sessions = session_factory(engine)
    with sessions.begin() as session:
        seed_catalog(session)
    yield sessions
    engine.dispose()


def clock():
    return AT + timedelta(milliseconds=200)


def seed(sessions, key="seed", *, planner=True):
    exp = Experiment(experiment_id=key, fixture_id="fifo_correct", objective_ids=("FIFO_BEHAVIOR",),
        budget={"max_cycles": 8, "wall_time_seconds": 2},
        operations=({"op": "reset"}, {"op": "write", "value": 17}, {"op": "read"}))
    with sessions.begin() as session:
        parent = service.create_campaign(session, "fifo_correct", exp.model_dump(mode="json"), key,
            completion_policy="sequential" if planner else "single")
        service.start(session, parent.id)
        attempt = session.scalar(select(Attempt).where(Attempt.campaign_id == parent.id))
        if planner:
            token, _, _ = service.claim(session, attempt.id)
            record = resolve_attempt_experiment(session, attempt)
            service.finalize(session, attempt.id, token, fake_result(record).model_dump(mode="json"))
            rounds.attach_planner(session, parent.id, at=AT, requested_model=MODEL, strategy="meta",
                limits=InvestigationLimits(max_rounds=5, max_total_cycles=80, max_cycles_per_experiment=8,
                    max_wall_seconds=60, max_model_seconds=2, max_execution_seconds=2))
        return parent.id, attempt.id


def fake_result(record):
    return AttemptResult(attempt_id="labelled-synthetic-runner", experiment_id=record.wire_id,
        execution_status="completed", verdict="pass_within_scope",
        identity={**record.identity, "environment_hash": "b" * 64}, usage={"cycles": 3},
        uncertainties=("Synthetic fake runner; no simulator executed",))


def pending(sessions, campaign, kind):
    with sessions() as session:
        state = session.get(PlannerState, campaign)
        return session.scalar(select(PlannerIntent.id).where(PlannerIntent.campaign_id == campaign,
            PlannerIntent.generation == state.generation, PlannerIntent.kind == kind))


def current_round(sessions, campaign):
    with sessions() as session:
        return session.scalar(select(PlannerRound.id).where(PlannerRound.campaign_id == campaign)
            .order_by(PlannerRound.ordinal.desc()))


def child(sessions, campaign):
    with sessions() as session:
        return session.scalar(select(Attempt.id).where(Attempt.campaign_id == campaign, Attempt.execution_status == "queued"))


class Broker:
    def __init__(self, fail=False):
        self.planner, self.attempts, self.fail, self.hook = [], [], fail, None
    def enqueue_planner(self, value):
        self.planner.append(value)
        if self.hook: self.hook()
        if self.fail: raise ConnectionError("synthetic secret broker URL must not persist")
    def enqueue(self, value): self.attempts.append(value)


class FakeProvider(MetaProvider):
    def __init__(self, *, stop=False, error=None):
        super().__init__(MetaConfig(model=MODEL, api_key=SecretStr("fake-not-a-real-key")))
        self.calls, self.contexts, self.stop, self.error = 0, [], stop, error
    async def complete(self, context, **kwargs):
        self.calls += 1
        self.contexts.append(context)
        if self.error: raise ProviderError(self.error, transient=True)
        proposal = {"kind": "stop", "reason": "finite synthetic stop"} if self.stop else {
            "kind": "experiment", "objective_ids": ["FIFO_BEHAVIOR"], "seed": 3,
            "operations": [{"op": "reset"}, {"op": "write", "value": 23}, {"op": "read"}]}
        return ProviderCompletion(model=MODEL, usage={"input_tokens": 2, "output_tokens": 3},
            finish_reason="stop", content=json.dumps({"proposal": proposal}))


def runtime(sessions, provider):
    """Explicit test bridge; production driver has no configured model fallback."""
    authority = planner_transport.ResourceAuthority(resources.new_incarnation())
    def handle(intent_id):
        with sessions() as session:
            round_id = session.get(PlannerIntent, intent_id).round_id
        return asyncio.run(planner_transport.run_model_intent(round_id, sessions=sessions, provider=provider,
            authority=authority, now=clock, control=lambda: "RUNNING", settlement_seconds=0.02))
    return handle


def advance(sessions, campaign):
    return driver.execute_intent(pending(sessions, campaign, "advance"), sessions=sessions, now=clock)


def proposed_child(sessions, campaign):
    assert advance(sessions, campaign)["accepted"]
    provider = FakeProvider()
    assert driver.execute_intent(pending(sessions, campaign, "model"), sessions=sessions, now=clock,
        model_handler=runtime(sessions, provider))["accepted"]
    return child(sessions, campaign), provider


def fake_runner(sessions, calls):
    def run(experiment, output, *, cancel_event):
        calls.append(experiment)
        assert not cancel_event.is_set()
        with sessions() as session:
            attempt = session.scalar(select(Attempt).where(Attempt.execution_status == "running"))
            return fake_result(resolve_attempt_experiment(session, attempt))
    return run


def test_finite_two_feedback_rounds_with_fresh_sessions_and_id_only_messages(db, tmp_path):
    campaign, _ = seed(db)
    broker = Broker()
    assert driver.publish_pending(db, broker, now=clock) == {"sent": 1, "failed": 0}
    first = broker.planner[0]
    assert isinstance(first, str) and driver.execute_intent(first, sessions=db, now=clock)["accepted"]
    assert not driver.execute_intent(first, sessions=db, now=clock)["accepted"]
    model_intent = pending(db, campaign, "model")
    assert driver.execute_intent(model_intent, sessions=db, now=clock)["reason"] == "runtime_unavailable"
    provider = FakeProvider()
    handle = runtime(db, provider)
    assert driver.execute_intent(model_intent, sessions=db, now=clock, model_handler=handle)["accepted"]
    assert not driver.execute_intent(model_intent, sessions=db, now=clock, model_handler=handle)["accepted"]
    assert provider.calls == 1 and len(provider.contexts[0].feedback) == 1
    attempt_id = child(db, campaign)
    assert dispatch.dispatch_pending(db, broker, clock=clock, control=lambda: "RUNNING") == {"sent": 0, "failed": 0}
    assert driver.release_settled_resource(db, now=clock)
    assert dispatch.dispatch_pending(db, broker, clock=clock, control=lambda: "RUNNING")["sent"] == 1
    assert broker.attempts == [attempt_id]
    calls = []
    assert worker.execute_attempt(attempt_id, sessions=db, runner=fake_runner(db, calls), artifact_root=tmp_path, now=clock, control=lambda: "RUNNING")["accepted"]
    assert not worker.execute_attempt(attempt_id, sessions=db, runner=fake_runner(db, calls), artifact_root=tmp_path, now=clock, control=lambda: "RUNNING")["accepted"]
    with db() as session:
        assert session.scalar(select(func.count()).select_from(PlannerFeedback)) == 2
        assert rounds.totals(session, campaign)["cycles"] == 16  # Full8+8, not measured3+3.
    assert len(calls) == 1 and advance(db, campaign)["accepted"]
    stop = FakeProvider(stop=True)
    driver.execute_intent(pending(db, campaign, "model"), sessions=db, now=clock, model_handler=runtime(db, stop))
    assert len(stop.contexts[0].feedback) == 2
    with db() as session:
        assert session.get(PlannerState, campaign).stop_reason == "model_stopped"
        assert session.scalar(select(func.count()).select_from(PlannerReservation)) == 4
        assert not rounds.pending_intents(session, at=clock())


def test_broker_failure_and_redelivery_never_double_reserve(db):
    campaign, _ = seed(db)
    broker = Broker(fail=True)
    assert driver.publish_pending(db, broker, now=clock) == {"sent": 0, "failed": 1}
    with db() as session: assert session.get(PlannerIntent, broker.planner[0]).sent_at is None
    broker.fail = False
    assert driver.publish_pending(db, broker, now=clock)["sent"] == 1
    assert broker.planner[0] == broker.planner[1]
    driver.execute_intent(broker.planner[0], sessions=db, now=clock)
    assert not driver.execute_intent(broker.planner[1], sessions=db, now=clock)["accepted"]
    with db() as session:
        assert session.scalar(select(func.count()).select_from(PlannerReservation)) == 2
    assert "secret" not in repr(driver.publish_pending(db, broker, now=clock))


def test_successful_enqueue_with_ack_rollback_is_redeliverable(db):
    campaign, _ = seed(db)
    intent_id = pending(db, campaign, "advance")
    class AckCrash:
        @contextmanager
        def begin(self):
            with db.begin() as session:
                yield session
                if session.get(PlannerIntent, intent_id).sent_at is not None:
                    raise RuntimeError("synthetic lost broker acknowledgment commit")
        def __call__(self): return db()
    broker = Broker()
    with pytest.raises(RuntimeError): driver.publish_pending(AckCrash(), broker, now=clock)
    with db() as session: assert session.get(PlannerIntent, intent_id).sent_at is None
    driver.publish_pending(db, broker, now=clock)
    assert broker.planner == [intent_id, intent_id]
    assert advance(db, campaign)["accepted"]
    assert not driver.execute_intent(intent_id, sessions=db, now=clock)["accepted"]


def test_redelivery_backoff_and_query_batch_are_bounded(db):
    for index in range(3): seed(db, str(index))
    broker = Broker()
    assert driver.publish_pending(db, broker, now=clock, limit=1)["sent"] == 1
    assert driver.publish_pending(db, broker, now=clock, limit=1)["sent"] == 1
    with db() as session: assert len(rounds.pending_intents(session, at=clock(), limit=1)) == 1
    driver.publish_pending(db, broker, now=clock)
    assert driver.publish_pending(db, broker, now=clock)["sent"] == 0
    assert driver.publish_pending(db, broker, now=lambda: AT + timedelta(seconds=31), limit=1)["sent"] == 1


def test_paused_recovery_is_bounded_and_never_publishes_or_invokes(db):
    for index in range(3): seed(db, str(index))
    broker = Broker()
    result = driver.reconcile_once(db, broker, now=clock, control=lambda: "PAUSED", limit=1)
    assert result["recovered"] == 1 and result["sent"] == 0 and not broker.planner
    with db() as session:
        assert session.scalar(select(func.count()).select_from(PlannerState).where(PlannerState.phase == "interrupted")) == 1
        assert len(rounds.pending_intents(session, at=clock(), limit=1)) == 1


@pytest.mark.parametrize("stage", ["advance", "model", "execution"])
def test_cancelled_delivery_cannot_claim_or_publish_late_work(db, tmp_path, stage):
    campaign, _ = seed(db)
    if stage == "execution":
        attempt_id, provider = proposed_child(db, campaign)
        driver.release_settled_resource(db, now=clock)
        intent_id = None
    else:
        if stage == "model": advance(db, campaign)
        intent_id = pending(db, campaign, stage)
    with db.begin() as session: rounds.cancel_investigation(session, campaign, at=clock())
    calls = []
    if stage == "execution":
        assert not worker.execute_attempt(attempt_id, sessions=db, runner=fake_runner(db, calls), artifact_root=tmp_path, now=clock, control=lambda: "RUNNING")["accepted"]
    else:
        assert not driver.execute_intent(intent_id, sessions=db, now=clock, model_handler=lambda value: calls.append(value))["accepted"]
    assert not calls


@pytest.mark.parametrize("code,expected", [("model_http_429", "ready"), ("model_http_503", "interrupted")])
def test_known_retry_reserves_new_round_but_unknown_never_resends(db, code, expected):
    campaign, _ = seed(db)
    advance(db, campaign)
    intent_id = pending(db, campaign, "model")
    provider = FakeProvider(error=code)
    handler = runtime(db, provider)
    driver.execute_intent(intent_id, sessions=db, now=clock, model_handler=handler)
    with db() as session:
        before = rounds.totals(session, campaign)
        assert session.get(PlannerState, campaign).phase == expected
    assert not driver.execute_intent(intent_id, sessions=db, now=clock, model_handler=handler)["accepted"]
    assert provider.calls == 1
    driver.release_settled_resource(db, now=clock)
    broker = Broker()
    result = driver.publish_pending(db, broker, now=lambda: AT + timedelta(seconds=1))
    if code == "model_http_429":
        assert result["sent"] == 1
        driver.execute_intent(broker.planner[0], sessions=db, now=lambda: AT + timedelta(seconds=1))
        with db() as session:
            assert rounds.totals(session, campaign)["tokens"] > before["tokens"]
            assert session.scalar(select(func.count()).select_from(PlannerModelCall)) == 2
    else:
        assert result["sent"] == 0 and not broker.planner
        with db() as session: assert rounds.totals(session, campaign) == before


def test_unresolved_cleanup_blocks_planner_execution_but_not_manual(db, tmp_path):
    campaign, _ = seed(db)
    attempt_id, _ = proposed_child(db, campaign)
    driver.release_settled_resource(db, now=clock)
    other, _ = seed(db, "other")
    advance(db, other)
    with db.begin() as session:
        grant = resources.claim_provider_slot(session, current_round(db, other), incarnation=resources.new_incarnation(),
            role=resources.ROLE, at=clock(), control="RUNNING")
    broker, calls = Broker(), []
    assert dispatch.dispatch_pending(db, broker, clock=clock, control=lambda: "RUNNING")["sent"] == 0
    assert not worker.execute_attempt(attempt_id, sessions=db, runner=fake_runner(db, calls), artifact_root=tmp_path, now=clock, control=lambda: "RUNNING")["accepted"]
    manual, manual_attempt = seed(db, "manual", planner=False)
    assert worker.execute_attempt(manual_attempt, sessions=db, runner=fake_runner(db, calls), artifact_root=tmp_path, now=clock)["accepted"]
    with db.begin() as session: resources.mark_owner_lost(session, grant, role=resources.ROLE, at=clock())
    driver.reconcile_once(db, broker, now=lambda: AT + timedelta(seconds=3))
    with db() as session:
        assert resources.safe_snapshot(session).state == "unknown"
        assert cleanup.read_cleanup(session, grant.lease.round_id).local_state == "unknown"


def test_stale_execution_generation_cannot_fall_back_to_generic_claim(db, tmp_path, monkeypatch):
    campaign, _ = seed(db)
    attempt_id, _ = proposed_child(db, campaign)
    driver.release_settled_resource(db, now=clock)
    with db.begin() as session: session.get(PlannerState, campaign).generation += 1
    def forbidden(*args, **kwargs): pytest.fail("stale planner child used generic claim")
    monkeypatch.setattr(service, "claim", forbidden)
    assert not worker.execute_attempt(attempt_id, sessions=db, runner=forbidden, artifact_root=tmp_path, now=clock, control=lambda: "RUNNING")["accepted"]


@pytest.mark.parametrize("infrastructure", [False, True])
def test_planner_finalizer_refusal_never_falls_back_to_generic(db, tmp_path, monkeypatch, infrastructure):
    campaign, _ = seed(db)
    attempt_id, _ = proposed_child(db, campaign)
    driver.release_settled_resource(db, now=clock)
    calls = []
    def refusal(*args, **kwargs):
        calls.append(kwargs.get("infrastructure_error", False))
        return False
    def forbidden(*args, **kwargs): pytest.fail("generic finalizer fallback")
    monkeypatch.setattr(rounds, "finish_execution_step", refusal)
    monkeypatch.setattr(service, "finalize", forbidden)
    def bad(*args, **kwargs): raise RuntimeError("synthetic runner failure")
    result = worker.execute_attempt(attempt_id, sessions=db, runner=bad if infrastructure else fake_runner(db, []),
        artifact_root=tmp_path, now=clock, control=lambda: "RUNNING")
    assert not result["accepted"] and calls == [infrastructure]


def test_invalid_checked_result_does_not_publish_finding_or_feedback(db, tmp_path):
    campaign, _ = seed(db)
    attempt_id, _ = proposed_child(db, campaign)
    driver.release_settled_resource(db, now=clock)
    def unbound(experiment, output, **kwargs):
        with db() as session:
            record = resolve_attempt_experiment(session, session.get(Attempt, attempt_id))
            identity = {**record.identity, "environment_hash": "b" * 64}
        return AttemptResult(attempt_id="synthetic-out-of-budget", experiment_id=experiment.experiment_id,
            execution_status="completed", verdict="pass_within_scope", identity=identity, usage={"cycles": 9})
    assert not worker.execute_attempt(attempt_id, sessions=db, runner=unbound, artifact_root=tmp_path, now=clock, control=lambda: "RUNNING")["accepted"]
    with db() as session:
        assert session.scalar(select(func.count()).select_from(Finding)) == 0
        assert session.scalar(select(func.count()).select_from(PlannerFeedback)) == 1


def test_generic_reconciler_only_publishes_never_runs_model_inline(db, monkeypatch):
    seed(db)
    def forbidden(*args, **kwargs): pytest.fail("reconciler executed a model intent")
    monkeypatch.setattr(driver, "execute_intent", forbidden)
    broker = Broker()
    result = worker.reconcile_once(sessions=db, dispatcher=broker, now=clock, control=lambda: "RUNNING")
    assert result["planner"]["sent"] == 1 and len(broker.planner) == 1


def test_legacy_committed_execution_is_consumed_once_without_tool_replay(db):
    campaign, _ = seed(db)
    attempt_id, provider = proposed_child(db, campaign)
    driver.release_settled_resource(db, now=clock)
    # Simulate an older worker's committed result before continuation recovery.
    with db.begin() as session:
        token, _, _ = service.claim(session, attempt_id)
        record = resolve_attempt_experiment(session, session.get(Attempt, attempt_id))
        service.finalize(session, attempt_id, token, fake_result(record).model_dump(mode="json"))
    broker = Broker()
    assert driver.reconcile_once(db, broker, now=clock)["recovered"] == 1
    assert driver.reconcile_once(db, broker, now=clock)["recovered"] == 0
    with db() as session:
        assert session.scalar(select(func.count()).select_from(PlannerFeedback)) == 2
        assert rounds.totals(session, campaign)["cycles"] == 16
    assert len(broker.planner) == 1 and provider.calls == 1


def test_actual_planner_infrastructure_finalization_is_not_design_failure(db, tmp_path):
    campaign, _ = seed(db)
    attempt_id, _ = proposed_child(db, campaign)
    driver.release_settled_resource(db, now=clock)
    def fail(*args, **kwargs): raise RuntimeError("synthetic controlled runner error")
    assert worker.execute_attempt(attempt_id, sessions=db, runner=fail, artifact_root=tmp_path, now=clock, control=lambda: "RUNNING")["accepted"]
    with db() as session:
        attempt, state = session.get(Attempt, attempt_id), session.get(PlannerState, campaign)
        assert attempt.execution_status == "infrastructure_error" and attempt.verdict is None
        assert state.stop_reason == "execution_unavailable"
        assert session.scalar(select(func.count()).select_from(Finding)) == 0
        assert not rounds.pending_intents(session, at=clock())


def test_cancel_during_runner_discards_late_result_and_continuation(db, tmp_path):
    campaign, _ = seed(db)
    attempt_id, _ = proposed_child(db, campaign)
    driver.release_settled_resource(db, now=clock)
    def cancel(experiment, output, *, cancel_event):
        with db.begin() as session:
            record = resolve_attempt_experiment(session, session.get(Attempt, attempt_id))
            result = fake_result(record)
            rounds.cancel_investigation(session, campaign, at=clock())
        assert cancel_event.is_set()
        return result
    assert not worker.execute_attempt(attempt_id, sessions=db, runner=cancel, artifact_root=tmp_path, now=clock, control=lambda: "RUNNING")["accepted"]
    with db() as session:
        assert session.get(Attempt, attempt_id).result is None
        assert session.scalar(select(func.count()).select_from(PlannerFeedback)) == 1
        assert not rounds.pending_intents(session, at=clock())


def test_lost_claimed_call_recovers_unknown_without_handler_resend_or_refund(db):
    campaign, _ = seed(db)
    advance(db, campaign)
    intent_id = pending(db, campaign, "model")
    round_id = current_round(db, campaign)
    with db.begin() as session:
        grant = resources.claim_provider_slot(session, round_id, incarnation=resources.new_incarnation(),
            role=resources.ROLE, at=clock(), control="RUNNING")
        before = rounds.totals(session, campaign)
    calls, broker = [], Broker()
    assert not driver.execute_intent(intent_id, sessions=db, now=clock, model_handler=calls.append)["accepted"]
    result = driver.reconcile_once(db, broker, now=lambda: AT + timedelta(seconds=3))
    assert result["recovered"] == 1 and not result["released"] and not broker.planner
    with db() as session:
        assert session.get(PlannerModelCall, round_id).outcome == "remote_outcome_unknown"
        assert cleanup.read_cleanup(session, round_id).local_state == "pending"
        assert rounds.totals(session, campaign) == before
    assert not driver.execute_intent(intent_id, sessions=db, now=lambda: AT + timedelta(seconds=4), model_handler=calls.append)["accepted"]
    assert not calls


def test_id_only_consumer_rejects_forged_payload_and_stale_intent(db):
    campaign, _ = seed(db)
    intent_id = pending(db, campaign, "advance")
    assert driver.execute_intent({"intent_id": intent_id, "kind": "model"}, sessions=db, now=clock)["reason"] == "invalid_intent"
    with pytest.raises(TypeError): driver.execute_intent(intent_id, sessions=db, now=clock, kind="model")
    assert advance(db, campaign)["accepted"]
    assert not driver.execute_intent(intent_id, sessions=db, now=clock, model_handler=lambda value: pytest.fail("stale intent invoked"))["accepted"]


@pytest.mark.parametrize("limit", [0, 101, True])
def test_driver_and_public_round_batches_reject_invalid_limits(db, limit):
    with pytest.raises(ValueError): driver.publish_pending(db, Broker(), now=clock, limit=limit)
    with db() as session:
        with pytest.raises(ValueError): rounds.pending_intents(session, at=clock(), limit=limit)
        with pytest.raises(ValueError): rounds.recover_rounds(session, at=clock(), limit=limit)
