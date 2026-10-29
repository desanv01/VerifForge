"""Durable glue evidence with isolated SQLite and explicit fake Meta; no network."""
import asyncio
from contextlib import contextmanager
from dataclasses import replace
from datetime import datetime, timedelta, timezone
import hashlib
import json
import time

from alembic import command
from alembic.config import Config
from pydantic import SecretStr
import pytest
from sqlalchemy import select

from veriforge.contracts import AttemptResult, Experiment
from veriforge.core import planner_rounds as rounds, planner_transport as glue, service
from veriforge.core import planner_cleanup as cleanup
from veriforge.core import planner_resources as resources
from veriforge.core.catalog import seed_catalog
from veriforge.core.database import (Attempt, PlannerModelCall, PlannerReservation, PlannerRound,
    PlannerCleanup, PlannerState, make_engine, session_factory)
from veriforge.core.planner_experiments import resolve_attempt_experiment
from veriforge.planner.durable_transport import TransportOutcome
from veriforge.planner.models import InvestigationLimits, ModelUsage, PlannerDecision, ProviderCompletion
from veriforge.planner.provider import MetaConfig, MetaProvider, ProviderError


AT = datetime(2026, 10, 29, 15, tzinfo=timezone.utc)
MODEL = "explicit-test-meta"
SECRET = "fake-test-value-not-a-credential"


def completion():
    return ProviderCompletion(model=MODEL, usage={"input_tokens": 2, "output_tokens": 3}, finish_reason="stop",
        content=json.dumps({"proposal": {"kind": "experiment", "objective_ids": ["FIFO_BEHAVIOR"], "seed": 3,
            "operations": [{"op": "reset"}, {"op": "write", "value": 23}, {"op": "read"}]}}))


class FakeMetaProvider(MetaProvider):
    """The only provider used here: explicit config, no env config or HTTP."""
    def __init__(self, effect=None):
        super().__init__(MetaConfig(model=MODEL, api_key=SecretStr(SECRET)))
        self.effect, self.calls = effect, 0

    async def complete(self, context, **kwargs):
        self.calls += 1
        return await self.effect(context, kwargs) if self.effect else completion()


class Sessions:
    def __init__(self, factory):
        self.factory, self.active, self.opened = factory, 0, []
        self.before_commit = self.after_commit = None

    @contextmanager
    def begin(self):
        self.active += 1
        try:
            with self.factory.begin() as session:
                self.opened.append(session)
                yield session
                if self.before_commit:
                    hook, self.before_commit = self.before_commit, None
                    hook()
            if self.after_commit:
                hook, self.after_commit = self.after_commit, None
                hook()
        finally:
            self.active -= 1


@pytest.fixture
def database(tmp_path):
    engine = make_engine("sqlite:///" + (tmp_path / "transport.db").as_posix())
    config = Config("alembic.ini")
    with engine.begin() as connection:
        config.attributes["connection"] = connection
        command.upgrade(config, "head")
    factory = session_factory(engine)
    with factory.begin() as session:
        seed_catalog(session)
    yield factory
    engine.dispose()


def real_authority():
    return glue.ResourceAuthority(resources.new_incarnation())


class RecordedAuthority:
    """Test instrumentation delegates every claim/validation to real resource API."""
    def __init__(self):
        self.real, self.grants, self.phases = real_authority(), [], []

    def claim(self, session, round_id, **kwargs):
        grant = self.real.claim(session, round_id, **kwargs)
        if grant is not None:
            self.grants.append(grant)
        return grant

    def validate(self, session, grant, **kwargs):
        assert grant is self.grants[0]
        self.phases.append(kwargs["phase"])
        return self.real.validate(session, grant, **kwargs)


def queued(factory, *, strategy="meta", key="seed"):
    experiment = Experiment(experiment_id=key, fixture_id="fifo_correct", objective_ids=("FIFO_BEHAVIOR",),
        operations=({"op": "reset"}, {"op": "write", "value": 17}, {"op": "read"}),
        budget={"max_cycles": 8, "wall_time_seconds": 2.0})
    with factory.begin() as session:
        campaign = service.create_campaign(session, experiment.fixture_id, experiment.model_dump(mode="json"),
            key, completion_policy="sequential")
        service.start(session, campaign.id)
        attempt = session.scalar(select(Attempt).where(Attempt.campaign_id == campaign.id))
        lease, _, _ = service.claim(session, attempt.id)
        record = resolve_attempt_experiment(session, attempt)
        result = AttemptResult(attempt_id="synthetic-seed", experiment_id=record.wire_id, execution_status="completed",
            verdict="pass_within_scope", identity={**record.identity, "environment_hash": "b" * 64},
            usage={"cycles": 3}, uncertainties=("Synthetic seed; no simulator executed",))
        service.finalize(session, attempt.id, lease, result.model_dump(mode="json"))
        limits = InvestigationLimits(max_rounds=4, max_total_cycles=80, max_cycles_per_experiment=8,
            max_wall_seconds=60, max_execution_seconds=2, max_model_seconds=2)
        rounds.attach_planner(session, campaign.id, limits=limits, requested_model=MODEL, at=AT, strategy=strategy)
        campaign_id = campaign.id
    with factory.begin() as session:
        round_id = rounds.advance(session, campaign_id, at=AT + timedelta(milliseconds=100)).id
    return campaign_id, round_id


def run(round_id, sessions, provider, **options):
    return asyncio.run(glue.run_model_intent(round_id, authority=options.pop("authority", real_authority()), sessions=sessions, provider=provider,
        now=options.pop("now", lambda: AT + timedelta(milliseconds=200)),
        control=options.pop("control", lambda: "RUNNING"), poll_seconds=0.01,
        settlement_seconds=0.02, **options))


def snapshot(factory, campaign_id, round_id):
    with factory() as session:
        state, row, call = (session.get(cls, key) for cls, key in (
            (PlannerState, campaign_id), (PlannerRound, round_id), (PlannerModelCall, round_id)))
        return state.phase, state.stop_reason, row.status, call.outcome, rounds.totals(session, campaign_id)


def test_commit_before_io_and_new_session_settlement(database):
    campaign_id, round_id = queued(database)
    sessions = Sessions(database)
    async def effect(context, kwargs):
        assert sessions.active == 0
        with database() as session:
            row, call = session.get(PlannerRound, round_id), session.get(PlannerModelCall, round_id)
            assert row.status == "running" and call.started_at is not None
            assert hashlib.sha256(call.request_body).hexdigest() == call.request_hash
            assert json.loads(call.request_body)["model"] == MODEL
            assert json.loads(json.loads(call.request_body)["messages"][1]["content"]) == context.model_dump(mode="json")
        return completion()
    provider = FakeMetaProvider(effect)
    result = run(round_id, sessions, provider)
    assert result.claimed and result.applied and result.reason == "accepted" and provider.calls == 1
    assert not result.cleanup_pending and not result.remote_outcome_unknown
    assert sessions.active == 0 and len(sessions.opened) >= 3
    assert len({id(session) for session in sessions.opened}) == len(sessions.opened)
    assert snapshot(database, campaign_id, round_id)[:4] == ("waiting_execution", None, "accepted", "accepted")
    assert SECRET not in repr(result)
    assert not run(round_id, Sessions(database), provider).claimed and provider.calls == 1


def test_mock_strategy_refuses_before_claim_and_provider_access(database):
    campaign_id, round_id = queued(database, strategy="mock_planner")
    class PoisonProvider:
        def __getattribute__(self, name):
            pytest.fail("mock strategy accessed provider")
    before = snapshot(database, campaign_id, round_id)
    result = run(round_id, Sessions(database), PoisonProvider())
    assert result.reason == "strategy_mismatch" and not result.claimed
    assert snapshot(database, campaign_id, round_id) == before


@pytest.mark.parametrize("boundary", ["before", "after"])
def test_crash_at_claim_commit_never_dispatches_or_reserves_twice(database, boundary):
    campaign_id, round_id = queued(database)
    before = snapshot(database, campaign_id, round_id)[4]
    sessions, provider = Sessions(database), FakeMetaProvider()
    class Crash(RuntimeError): pass
    def crash(): raise Crash("simulated worker loss")
    setattr(sessions, boundary + "_commit", crash)
    with pytest.raises(Crash):
        run(round_id, sessions, provider)
    assert provider.calls == 0
    saved = snapshot(database, campaign_id, round_id)
    assert saved[2] == ("queued" if boundary == "before" else "running") and saved[4] == before
    result = run(round_id, Sessions(database), provider)
    assert result.claimed == (boundary == "before")
    assert provider.calls == (1 if boundary == "before" else 0)
    if boundary == "after":
        with database.begin() as session:
            rounds.recover_rounds(session, at=AT + timedelta(seconds=200))
        saved = snapshot(database, campaign_id, round_id)
        assert saved[0] == "interrupted" and saved[3] == "remote_outcome_unknown" and saved[4] == before


@pytest.mark.parametrize("boundary", ["before", "after"])
@pytest.mark.parametrize("kind", ["event", "task"])
def test_cancellation_during_claim_commit_never_starts_provider(database, boundary, kind):
    campaign_id, round_id = queued(database)
    sessions, provider = Sessions(database), FakeMetaProvider()
    async def scenario():
        event = asyncio.Event()
        task = asyncio.current_task()
        def cancel():
            event.set() if kind == "event" else task.cancel()
        setattr(sessions, boundary + "_commit", cancel)
        return await glue.run_model_intent(round_id, authority=real_authority(), sessions=sessions, provider=provider,
            now=lambda: AT + timedelta(milliseconds=200), control=lambda: "RUNNING", cancel_event=event)
    result = asyncio.run(scenario())
    assert result.claimed and result.reason == "cancelled" and provider.calls == 0
    assert not result.remote_outcome_unknown and not result.cleanup_pending
    assert snapshot(database, campaign_id, round_id)[2] == "failed"


@pytest.mark.parametrize("mode", ["pause", "db-cancel", "heartbeat-false", "caller-cancel"])
def test_inflight_revocation_discards_late_provider_result(database, monkeypatch, mode):
    campaign_id, round_id = queued(database)
    sessions, provider = Sessions(database), FakeMetaProvider()
    async def scenario():
        entered, release = asyncio.Event(), asyncio.Event()
        control_value = ["RUNNING"]
        async def late(context, kwargs):
            assert sessions.active == 0
            entered.set()
            try:
                await release.wait()
            except asyncio.CancelledError:
                await release.wait()
            return completion()
        provider.effect = late
        if mode == "heartbeat-false":
            original = rounds.heartbeat_model_round
            calls = []
            def heartbeat(*args, **kwargs):
                calls.append(1)
                return original(*args, **kwargs) if len(calls) == 1 else False
            monkeypatch.setattr(rounds, "heartbeat_model_round", heartbeat)
        task = asyncio.create_task(glue.run_model_intent(round_id, authority=real_authority(), sessions=sessions, provider=provider,
            now=lambda: AT + timedelta(milliseconds=200), control=lambda: control_value[0],
            poll_seconds=0.01, settlement_seconds=0.02))
        await entered.wait()
        before = snapshot(database, campaign_id, round_id)[4]
        if mode == "pause": control_value[0] = "PAUSED"
        elif mode == "db-cancel":
            with database.begin() as session:
                rounds.cancel_investigation(session, campaign_id, at=AT + timedelta(milliseconds=300))
        elif mode == "caller-cancel": task.cancel()
        result = await task
        saved = snapshot(database, campaign_id, round_id)
        assert result.cleanup_pending and result.remote_outcome_unknown and provider.calls == 1
        if mode == "heartbeat-false":
            # Synthetic refusal does not itself revoke DB authority. Core must
            # not bypass the resource refusal with a raw round finalizer.
            assert not result.applied and saved[0] == "model_running"
        else:
            assert saved[0] in {"interrupted", "cancelled"}
        assert saved[4] == before
        release.set()
        await asyncio.sleep(0.02)
        assert snapshot(database, campaign_id, round_id) == saved
        return result
    asyncio.run(scenario())


@pytest.mark.parametrize("field,value", [("call_id", "forged"), ("generation", 900),
    ("request_hash", "f" * 64), ("requested_model", "forged-model"), ("code", "model_unavailable"),
    ("cleanup_pending", True), ("elapsed_seconds", float("nan")),
    ("usage", ModelUsage(input_tokens=999999, output_tokens=3))])
def test_forged_success_is_never_applied(database, monkeypatch, field, value):
    campaign_id, round_id = queued(database)
    provider = FakeMetaProvider()
    async def forged(request, **kwargs):
        outcome = TransportOutcome(call_id=request.call_id, generation=request.generation,
            request_hash=request.admission.request_hash, requested_model=request.requested_model,
            state="success", code="accepted", invocation_started=True, remote_outcome_unknown=False,
            elapsed_seconds=0.01, usage={"input_tokens": 2, "output_tokens": 3},
            decision=PlannerDecision.model_validate_json(completion().content))
        return outcome.model_copy(update={field: value})
    monkeypatch.setattr(glue, "invoke_admitted", forged)
    result = run(round_id, Sessions(database), provider)
    assert result.claimed and result.applied and result.reason == "outcome_invalid"
    assert result.remote_outcome_unknown and provider.calls == 0
    if field == "cleanup_pending":
        assert result.cleanup_pending
    assert snapshot(database, campaign_id, round_id)[0] == "interrupted"


@pytest.mark.parametrize("code", ["model_http_429", "model_http_503", "model_response_invalid", "model_unavailable"])
def test_safe_error_classification_never_resends_the_claim(database, code):
    campaign_id, round_id = queued(database)
    async def error(*args):
        raise ProviderError(code, transient=True)
    provider = FakeMetaProvider(error)
    result = run(round_id, Sessions(database), provider)
    assert result.reason == code and result.applied and provider.calls == 1
    phase = snapshot(database, campaign_id, round_id)[0]
    assert phase == ("ready" if code == "model_http_429" else
                     "interrupted" if code in {"model_http_503", "model_response_invalid"} else "settled")
    assert not run(round_id, Sessions(database), provider).claimed and provider.calls == 1


def test_reconstructed_body_must_equal_committed_bytes(database, monkeypatch):
    campaign_id, round_id = queued(database)
    original = glue.reconstruct_admitted_request
    def wrong(**kwargs):
        request = original(**kwargs)
        return request.model_copy(update={"admission": request.admission.model_copy(update={"request_body": b"{}"})})
    monkeypatch.setattr(glue, "reconstruct_admitted_request", wrong)
    provider = FakeMetaProvider()
    result = run(round_id, Sessions(database), provider)
    assert result.reason == "admission_invalid" and result.applied and provider.calls == 0
    assert not result.remote_outcome_unknown
    assert snapshot(database, campaign_id, round_id)[0] == "settled"


def test_cancel_event_set_with_completion_wins_before_final_commit(database):
    campaign_id, round_id = queued(database)
    provider = FakeMetaProvider()
    async def scenario():
        event = asyncio.Event()
        async def complete_and_cancel(*args):
            event.set()
            return completion()
        provider.effect = complete_and_cancel
        return await glue.run_model_intent(round_id, authority=real_authority(), sessions=Sessions(database), provider=provider,
            now=lambda: AT + timedelta(milliseconds=200), control=lambda: "RUNNING", cancel_event=event,
            poll_seconds=0.01, settlement_seconds=0.02)
    result = asyncio.run(scenario())
    assert result.reason == "cancelled" and result.remote_outcome_unknown and result.applied
    assert snapshot(database, campaign_id, round_id)[0] == "interrupted"


def test_preexisting_cancel_and_invalid_provider_leave_round_queued(database):
    campaign_id, round_id = queued(database)
    before = snapshot(database, campaign_id, round_id)
    event = asyncio.Event()
    event.set()
    provider = FakeMetaProvider()
    assert run(round_id, Sessions(database), provider, cancel_event=event).reason == "cancelled"
    assert run(round_id, Sessions(database), object()).reason == "provider_invalid"
    assert provider.calls == 0 and snapshot(database, campaign_id, round_id) == before


def test_wall_deadline_poll_revokes_inflight_provider(database):
    campaign_id, round_id = queued(database)
    provider = FakeMetaProvider()
    async def scenario():
        clock = [AT + timedelta(milliseconds=200)]
        async def stall(*args):
            clock[0] = AT + timedelta(seconds=61)
            await asyncio.Event().wait()
        provider.effect = stall
        return await glue.run_model_intent(round_id, authority=real_authority(), sessions=Sessions(database), provider=provider,
            now=lambda: clock[0], control=lambda: "RUNNING", poll_seconds=0.01, settlement_seconds=0.02)
    result = asyncio.run(scenario())
    assert provider.calls == 1 and result.remote_outcome_unknown and not result.cleanup_pending
    assert snapshot(database, campaign_id, round_id)[0:2] == ("interrupted", "wall_time_limit")


def test_final_lease_fence_rejects_success_after_external_cancellation(database):
    campaign_id, round_id = queued(database)
    async def race(*args):
        with database.begin() as session:
            rounds.cancel_investigation(session, campaign_id, at=AT + timedelta(milliseconds=300))
        return completion()
    provider = FakeMetaProvider(race)
    result = run(round_id, Sessions(database), provider)
    assert provider.calls == 1 and result.claimed and not result.applied
    assert snapshot(database, campaign_id, round_id)[0:2] == ("cancelled", "cancelled")


def test_response_lost_before_settlement_commit_is_not_resent(database):
    campaign_id, round_id = queued(database)
    before = snapshot(database, campaign_id, round_id)[4]
    sessions = Sessions(database)
    class Crash(RuntimeError): pass
    def lose_commit():
        # A polling heartbeat can commit between provider return and settlement.
        # Crash only the transaction that would admit the returned proposal.
        if sessions.opened[-1].get(PlannerRound, round_id).status == "accepted":
            raise Crash("simulated loss before settlement commit")
        sessions.before_commit = lose_commit
    async def response(*args):
        sessions.before_commit = lose_commit
        return completion()
    provider = FakeMetaProvider(response)
    with pytest.raises(Crash):
        run(round_id, sessions, provider)
    saved = snapshot(database, campaign_id, round_id)
    assert provider.calls == 1 and saved[2] == "running" and saved[4] == before
    assert not run(round_id, Sessions(database), provider).claimed and provider.calls == 1
    with database.begin() as session:
        rounds.recover_rounds(session, at=AT + timedelta(seconds=3))
    assert snapshot(database, campaign_id, round_id)[0:4] == (
        "interrupted", "model_call_uncertain", "uncertain", "remote_outcome_unknown")


def test_repeated_caller_cancellation_has_finite_cleanup(database):
    campaign_id, round_id = queued(database)
    provider = FakeMetaProvider()
    async def scenario():
        entered, release = asyncio.Event(), asyncio.Event()
        async def ignore_cancellation(*args):
            entered.set()
            while not release.is_set():
                try:
                    await release.wait()
                except asyncio.CancelledError:
                    continue
            return completion()
        provider.effect = ignore_cancellation
        task = asyncio.create_task(glue.run_model_intent(round_id, authority=real_authority(), sessions=Sessions(database), provider=provider,
            now=lambda: AT + timedelta(milliseconds=200), control=lambda: "RUNNING",
            poll_seconds=0.01, settlement_seconds=0.02))
        await entered.wait()
        started = time.monotonic()
        for _ in range(4):
            task.cancel()
            await asyncio.sleep(0.005)
        done, _ = await asyncio.wait({task}, timeout=0.4)
        release.set()
        assert task in done and time.monotonic() - started < 0.5
        result = task.result()
        assert result.cleanup_pending and result.remote_outcome_unknown
        await asyncio.sleep(0.02)
    asyncio.run(scenario())
    assert provider.calls == 1 and snapshot(database, campaign_id, round_id)[0] == "interrupted"


def cleanup_view(factory, round_id):
    with factory() as session:
        return cleanup.read_cleanup(session, round_id)


def test_cleanup_registration_is_committed_before_provider_io(database):
    campaign_id, round_id = queued(database)
    sessions = Sessions(database)
    async def inspect(*args):
        assert sessions.active == 0
        assert cleanup_view(database, round_id).local_state == "pending"
        return completion()
    result = run(round_id, sessions, FakeMetaProvider(inspect))
    assert result.applied and not result.cleanup_pending
    assert cleanup_view(database, round_id).local_state == "settled"


def test_duplicate_cleanup_refusal_never_invokes(database, monkeypatch):
    campaign_id, round_id = queued(database)
    monkeypatch.setattr(cleanup, "record_pending", lambda *args, **kwargs: None)
    provider = FakeMetaProvider()
    result = run(round_id, Sessions(database), provider)
    assert not result.claimed and result.reason == "not_claimed" and provider.calls == 0
    # The resource API rolls the partial round claim back atomically on refusal.
    assert snapshot(database, campaign_id, round_id)[2] == "queued"
    assert not run(round_id, Sessions(database), provider).claimed


@pytest.mark.parametrize("boundary", ["before", "after"])
def test_registration_commit_loss_preserves_no_io_and_recovery_inventory(database, boundary):
    campaign_id, round_id = queued(database)
    sessions, provider = Sessions(database), FakeMetaProvider()
    class Crash(RuntimeError): pass
    def crash(): raise Crash("registration transaction loss")
    setattr(sessions, boundary + "_commit", crash)
    with pytest.raises(Crash):
        run(round_id, sessions, provider)
    assert provider.calls == 0
    view = cleanup_view(database, round_id)
    assert (view is None) == (boundary == "before")
    if boundary == "after":
        with database.begin() as session:
            assert cleanup.unresolved_cleanup(session) == (view,)
            cleanup.mark_owner_lost(session, round_id, at=AT + timedelta(seconds=1))
        assert cleanup_view(database, round_id).local_state == "unknown"
        assert not run(round_id, Sessions(database), provider).claimed and provider.calls == 0


@pytest.mark.parametrize("fail_on", [1, 2, 3])
def test_task_observation_failure_retains_durable_pending_without_io(database, monkeypatch, fail_on):
    campaign_id, round_id = queued(database)
    original, seen = glue._LocalCleanupOwner.track, []
    def faulty(self, task):
        seen.append(task)
        original(self, task)
        if len(seen) == fail_on:
            raise RuntimeError("trusted bookkeeping failure")
    monkeypatch.setattr(glue._LocalCleanupOwner, "track", faulty)
    provider = FakeMetaProvider()
    async def scenario():
        errors = []
        asyncio.get_running_loop().set_exception_handler(lambda loop, context: errors.append(context))
        result = await glue.run_model_intent(round_id, authority=real_authority(), sessions=Sessions(database), provider=provider,
            now=lambda: AT + timedelta(milliseconds=200), control=lambda: "RUNNING", settlement_seconds=0.02)
        await asyncio.sleep(0)
        assert not errors and all(task.done() for task in seen)
        return result
    result = asyncio.run(scenario())
    assert provider.calls == 0 and result.cleanup_pending
    assert cleanup_view(database, round_id).local_state == "pending"


def test_late_child_settlement_acknowledges_unknown_after_parent_cancel(database):
    campaign_id, round_id = queued(database)
    sessions, provider, authority = Sessions(database), FakeMetaProvider(), RecordedAuthority()
    async def scenario():
        entered, release, cancel = asyncio.Event(), asyncio.Event(), asyncio.Event()
        clock = [AT + timedelta(milliseconds=200)]
        async def uncooperative(*args):
            entered.set()
            while not release.is_set():
                try:
                    await release.wait()
                except asyncio.CancelledError:
                    continue
            return completion()
        provider.effect = uncooperative
        wrapper = asyncio.create_task(glue.run_model_intent(round_id, authority=authority, sessions=sessions, provider=provider,
            now=lambda: clock[0], control=lambda: "RUNNING", cancel_event=cancel,
            poll_seconds=0.01, settlement_seconds=0.02))
        await entered.wait()
        cancel.set()
        result = await wrapper
        # A completed wrapper is explicitly insufficient: the inner task lives.
        assert wrapper.done() and result.cleanup_pending and result.remote_outcome_unknown
        assert cleanup_view(database, round_id).local_state == "pending"
        with database.begin() as session:
            rounds.cancel_investigation(session, campaign_id, at=AT + timedelta(milliseconds=300))
            assert resources.mark_owner_lost(session, authority.grants[0], role=resources.ROLE,
                at=AT + timedelta(milliseconds=300))
        assert cleanup_view(database, round_id).local_state == "unknown"
        saved = snapshot(database, campaign_id, round_id)
        clock[0] = AT + timedelta(milliseconds=400)
        release.set()
        await asyncio.sleep(0.03)
        assert cleanup_view(database, round_id).local_state == "settled"
        assert snapshot(database, campaign_id, round_id) == saved
        with database() as session:
            assert resources.safe_snapshot(session).state == "unknown"  # Callback never releases.
        assert result.cleanup_pending  # Frozen historical result is not rewritten.
        assert provider.calls == 1
    asyncio.run(scenario())


def test_acknowledgment_failure_has_no_unhandled_callback_or_false_settlement(database, monkeypatch):
    campaign_id, round_id = queued(database)
    def unavailable(*args, **kwargs): raise RuntimeError("synthetic DB failure")
    monkeypatch.setattr(cleanup, "acknowledge_settled", unavailable)
    async def scenario():
        errors = []
        asyncio.get_running_loop().set_exception_handler(lambda loop, context: errors.append(context))
        result = await glue.run_model_intent(round_id, authority=real_authority(), sessions=Sessions(database), provider=FakeMetaProvider(),
            now=lambda: AT + timedelta(milliseconds=200), control=lambda: "RUNNING")
        await asyncio.sleep(0)
        assert not errors
        return result
    result = asyncio.run(scenario())
    assert result.applied and result.cleanup_pending and not result.remote_outcome_unknown
    assert cleanup_view(database, round_id).local_state == "pending"


def test_parent_cancellation_after_registration_prevents_io_and_settles_zero_tasks(database):
    campaign_id, round_id = queued(database)
    sessions, provider = Sessions(database), FakeMetaProvider()
    def cancel_after_commit():
        assert cleanup_view(database, round_id).local_state == "pending"
        with database.begin() as session:
            rounds.cancel_investigation(session, campaign_id, at=AT + timedelta(milliseconds=200))
    sessions.after_commit = cancel_after_commit
    result = run(round_id, sessions, provider)
    assert provider.calls == 0 and not result.applied and result.reason == "lease_revoked"
    assert cleanup_view(database, round_id).local_state == "settled"
    assert snapshot(database, campaign_id, round_id)[0] == "cancelled"


def test_cleanup_acknowledgment_commit_rollback_remains_pending(database):
    campaign_id, round_id = queued(database)
    sessions = Sessions(database)
    def reject_ack_commit():
        sessions.before_commit = reject_ack_commit
        row = sessions.opened[-1].get(PlannerCleanup, round_id)
        if row is not None and row.local_state == "settled":
            raise RuntimeError("synthetic acknowledgment commit loss")
    sessions.before_commit = reject_ack_commit
    result = run(round_id, sessions, FakeMetaProvider())
    assert result.applied and result.cleanup_pending
    assert cleanup_view(database, round_id).local_state == "pending"
    assert snapshot(database, campaign_id, round_id)[0] == "waiting_execution"


def test_unobserved_inner_pending_report_cannot_acknowledge_wrapper_only(database, monkeypatch):
    campaign_id, round_id = queued(database)
    async def unobserved(request, **kwargs):
        return TransportOutcome(call_id=request.call_id, generation=request.generation,
            request_hash=request.admission.request_hash, requested_model=request.requested_model,
            state="uncertain", code="model_timeout", invocation_started=True,
            remote_outcome_unknown=True, cleanup_pending=True, elapsed_seconds=0.01)
    monkeypatch.setattr(glue, "invoke_admitted", unobserved)
    result = run(round_id, Sessions(database), FakeMetaProvider())
    assert result.cleanup_pending and result.remote_outcome_unknown
    assert cleanup_view(database, round_id).local_state == "pending"


def test_authority_is_mandatory_without_legacy_fallback(database):
    _, round_id = queued(database)
    provider = FakeMetaProvider()
    with pytest.raises(TypeError, match="authority"):
        glue.run_model_intent(round_id, sessions=Sessions(database), provider=provider,
            now=lambda: AT, control=lambda: "RUNNING")
    assert provider.calls == 0 and cleanup_view(database, round_id) is None


def test_atomic_resource_grant_is_not_claimed_or_registered_twice(database, monkeypatch):
    _, round_id = queued(database)
    authority, calls = RecordedAuthority(), []
    claim, register = rounds.claim_model_round, cleanup.record_pending
    def counted_claim(*args, **kwargs):
        calls.append("claim")
        return claim(*args, **kwargs)
    def counted_registration(*args, **kwargs):
        calls.append("registration")
        return register(*args, **kwargs)
    monkeypatch.setattr(rounds, "claim_model_round", counted_claim)
    monkeypatch.setattr(cleanup, "record_pending", counted_registration)
    async def delayed(*args):
        await asyncio.sleep(0.03)
        return completion()
    result = run(round_id, Sessions(database), FakeMetaProvider(delayed), authority=authority)
    assert result.applied and calls == ["claim", "registration"]
    assert authority.phases[0] == "pre_io" and "running" in authority.phases and authority.phases[-1] == "completion"
    assert cleanup_view(database, round_id).local_state == "settled"
    with database() as session:
        slot = resources.safe_snapshot(session)
        assert slot.state == "active" and slot.active_round_id == round_id  # No callback release.


@pytest.mark.parametrize("mutation", ["fence-bool", "generation-bool", "ticket", "round"])
def test_malformed_grant_rolls_back_before_io(database, mutation):
    campaign_id, round_id = queued(database)
    class CorruptGrant(RecordedAuthority):
        def claim(self, session, round_id, **kwargs):
            grant = super().claim(session, round_id, **kwargs)
            if mutation == "fence-bool": return replace(grant, fence=True)
            if mutation == "generation-bool": return replace(grant, lease=replace(grant.lease, generation=True))
            if mutation == "ticket": return replace(grant, cleanup_ticket=replace(grant.cleanup_ticket, request_hash="f" * 64))
            return replace(grant, lease=replace(grant.lease, round_id="other"))
    provider = FakeMetaProvider()
    with pytest.raises(ValueError, match="grant"):
        run(round_id, Sessions(database), provider, authority=CorruptGrant())
    assert provider.calls == 0 and cleanup_view(database, round_id) is None
    assert snapshot(database, campaign_id, round_id)[2] == "queued"
    with database() as session:
        assert resources.safe_snapshot(session).state == "idle"


def test_fence_revoked_after_commit_prevents_io_and_never_releases_slot(database):
    campaign_id, round_id = queued(database)
    sessions, authority, provider = Sessions(database), RecordedAuthority(), FakeMetaProvider()
    def revoke():
        with database.begin() as session:
            assert resources.mark_owner_lost(session, authority.grants[0], role=resources.ROLE,
                at=AT + timedelta(milliseconds=200))
    sessions.after_commit = revoke
    result = run(round_id, sessions, provider, authority=authority)
    assert provider.calls == 0 and not result.applied and result.reason == "lease_revoked"
    assert cleanup_view(database, round_id).local_state == "settled"  # Trusted zero-task proof.
    assert snapshot(database, campaign_id, round_id)[0] == "interrupted"
    with database() as session:
        assert resources.safe_snapshot(session).state == "unknown"


def test_owner_loss_before_completion_cannot_use_round_only_finalizers(database, monkeypatch):
    campaign_id, round_id = queued(database)
    authority = RecordedAuthority()
    async def lose_owner(*args):
        with database.begin() as session:
            assert resources.mark_owner_lost(session, authority.grants[0], role=resources.ROLE,
                at=AT + timedelta(milliseconds=200))
        def forbidden(*args, **kwargs): pytest.fail("resource refusal bypassed with raw round mutation")
        monkeypatch.setattr(rounds, "finish_model_round", forbidden)
        monkeypatch.setattr(rounds, "fail_model_round", forbidden)
        return completion()
    result = run(round_id, Sessions(database), FakeMetaProvider(lose_owner), authority=authority)
    assert result.claimed and not result.applied
    assert snapshot(database, campaign_id, round_id)[3] == "remote_outcome_unknown"
    assert cleanup_view(database, round_id).local_state == "settled"


@pytest.mark.parametrize("path", ["reconstruction", "observation", "pre-task-cancel"])
def test_early_fail_paths_obey_completion_authority_refusal(database, monkeypatch, path):
    _, round_id = queued(database)
    class RefuseCompletion(RecordedAuthority):
        def validate(self, session, grant, **kwargs):
            valid = super().validate(session, grant, **kwargs)
            return valid and kwargs["phase"] != "completion"
    authority, sessions, event = RefuseCompletion(), Sessions(database), asyncio.Event()
    if path == "reconstruction":
        monkeypatch.setattr(glue, "reconstruct_admitted_request", lambda **kwargs: (_ for _ in ()).throw(ValueError()))
    elif path == "observation":
        monkeypatch.setattr(glue._LocalCleanupOwner, "track", lambda *args: (_ for _ in ()).throw(RuntimeError()))
    else:
        sessions.after_commit = event.set
    def forbidden(*args, **kwargs): pytest.fail("early failure bypassed resource completion fence")
    monkeypatch.setattr(rounds, "fail_model_round", forbidden)
    provider = FakeMetaProvider()
    result = run(round_id, sessions, provider, authority=authority, cancel_event=event)
    assert result.claimed and not result.applied and provider.calls == 0
    assert authority.phases[-1] == "completion"


def test_two_campaign_consumers_share_real_global_provider_slot(database):
    _, first = queued(database, key="first")
    _, second = queued(database, key="second")
    async def scenario():
        entered, release = asyncio.Event(), asyncio.Event()
        async def wait(*args):
            entered.set()
            await release.wait()
            return completion()
        provider1, provider2 = FakeMetaProvider(wait), FakeMetaProvider()
        task = asyncio.create_task(glue.run_model_intent(first, authority=real_authority(), sessions=Sessions(database),
            provider=provider1, now=lambda: AT + timedelta(milliseconds=200), control=lambda: "RUNNING"))
        await entered.wait()
        refused = await glue.run_model_intent(second, authority=real_authority(), sessions=Sessions(database),
            provider=provider2, now=lambda: AT + timedelta(milliseconds=200), control=lambda: "RUNNING")
        assert not refused.claimed and provider2.calls == 0
        release.set()
        result = await task
        assert result.applied and provider1.calls == 1
    asyncio.run(scenario())


def test_already_settled_cleanup_cannot_authorize_fresh_io(database):
    _, round_id = queued(database)
    sessions, authority = Sessions(database), RecordedAuthority()
    def settle_zero_created_tasks():
        with database.begin() as session:
            assert cleanup.acknowledge_settled(session, authority.grants[0].cleanup_ticket,
                proof="cooperative_tasks_settled", at=AT + timedelta(milliseconds=200))
    sessions.after_commit = settle_zero_created_tasks
    provider = FakeMetaProvider()
    result = run(round_id, sessions, provider, authority=authority)
    assert provider.calls == 0 and result.reason == "lease_revoked"
    assert authority.phases == ["pre_io", "completion"]
    assert cleanup_view(database, round_id).local_state == "settled"


def test_full_observed_settlement_before_finalization_allows_completion_phase(database, monkeypatch):
    campaign_id, round_id = queued(database)
    observed = []
    original_init = glue._LocalCleanupOwner.__init__
    def remember(owner, *args):
        original_init(owner, *args)
        observed.append(owner)
    monkeypatch.setattr(glue._LocalCleanupOwner, "__init__", remember)
    class SettleBeforeCompletion(RecordedAuthority):
        def validate(self, session, grant, **kwargs):
            if kwargs["phase"] == "completion":
                owner = observed[0]
                assert all(task.done() for task in owner.tasks)
                owner.seal()  # Original owner proves all real tracked tasks done.
                assert owner.acknowledged and cleanup_view(database, round_id).local_state == "settled"
            return super().validate(session, grant, **kwargs)
    result = run(round_id, Sessions(database), FakeMetaProvider(), authority=SettleBeforeCompletion())
    assert result.applied and not result.cleanup_pending
    assert snapshot(database, campaign_id, round_id)[0] == "waiting_execution"
