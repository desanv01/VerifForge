"""Independent glue boundaries: real resource grants/isolated SQLite, no HTTP."""
import ast
import asyncio
from contextlib import contextmanager
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace

import runpy
from alembic import command
from alembic.config import Config
import pytest
from veriforge.core import planner_resources as resources
from veriforge.core.catalog import seed_catalog
from veriforge.core.database import PlannerCleanup, make_engine, session_factory

from veriforge.core import planner_transport as glue
from veriforge.planner import durable_transport
from veriforge.core.planner_rounds import ModelLease
from veriforge.planner.durable_transport import TransportOutcome
from veriforge.planner.models import InvestigationLimits, ModelUsage, PlannerDecision
from veriforge.planner.provider import MetaProvider
from veriforge.planner.steps import build_context, prepare_model_call
from veriforge.rtl import reviewed_contract


class NoNetworkMeta(MetaProvider):
    def __init__(self):
        pass  # No config, key, environment, HTTP client, or provider construction.

    async def complete(self, *args, **kwargs):
        pytest.fail("review may not invoke a live provider")


class ReviewSessions:
    """Real transactions with explicit commit/authority fault injection only."""
    def __init__(self, factory, round_id):
        self.factory, self.round_id = factory, round_id
        self.active = self.commits = self.dispatches = 0
        self.claimed = self.claim_committed = self.settled = False
        self.cleanup_registered = self.cleanup_committed = False
        self._cleanup_state = None
        self.failures, self.finishes = [], []
        self.crash, self.heartbeat, self.accept_finish = None, True, True
        self.strategy = "meta"
        self.ack_attempts, self.ack_commit_failure = 0, False
        self.register_refused = False
        self.grant = None

    @property
    def cleanup_state(self):
        return self._cleanup_state

    @cleanup_state.setter
    def cleanup_state(self, value):
        # Existing test's explicit synthetic reconciler handoff now uses the
        # genuine resource API rather than changing an in-memory status label.
        if value == "unknown" and self.grant is not None:
            with self.factory.begin() as session:
                resources.mark_owner_lost(session, self.grant, role=resources.ROLE, at=STAMP)
        self._cleanup_state = value

    @contextmanager
    def begin(self):
        self.active += 1
        previous_cleanup = self._cleanup_state
        try:
            with self.factory.begin() as session:
                if self.strategy != "meta":
                    original_get = session.get
                    def get(cls, key, *args, **kwargs):
                        value = original_get(cls, key, *args, **kwargs)
                        return SimpleNamespace(strategy=self.strategy, policy=value.policy) if cls is glue.PlannerState else value
                    session.get = get
                yield session
                if self.commits == 0 and self.crash == "before":
                    self.claimed = self.cleanup_registered = False
                    raise RuntimeError("simulated claim commit failure")
                if self._cleanup_state == "settled" and self.ack_commit_failure:
                    self._cleanup_state = previous_cleanup
                    raise RuntimeError("simulated cleanup acknowledgment commit rollback")
            self.commits += 1
            self.claim_committed = self.claimed
            self.cleanup_committed = self.cleanup_registered
            if self.commits == 1 and self.crash == "after":
                raise RuntimeError("simulated loss after committed claim")
        finally:
            self.active -= 1


ROOT = Path(__file__).resolve().parents[2]
BUILDERS = runpy.run_path(str(ROOT / "tests/core/test_planner_transport.py"))
STAMP = BUILDERS["AT"] + timedelta(milliseconds=200)


@pytest.fixture
def harness(monkeypatch, tmp_path):
    engine = make_engine("sqlite:///" + (tmp_path / "review.db").as_posix())
    config = Config(str(ROOT / "alembic.ini"))
    with engine.begin() as connection:
        config.attributes["connection"] = connection
        command.upgrade(config, "head")
    factory = session_factory(engine)
    with factory.begin() as session:
        seed_catalog(session)
    _, round_id = BUILDERS["queued"](factory, key="prior-independent-review")
    sessions = ReviewSessions(factory, round_id)
    # This immutable value is ONLY a reconstruction-fault selector for prior
    # negative tests. It never grants a model lease/resource/cleanup authority.
    limits = InvestigationLimits(max_model_seconds=1)
    context = build_context(reviewed_contract(), limits, reserved_cycles=0, feedback=())
    admission = prepare_model_call(context, "review-model", limits,
        reserved_tokens=0, reserved_cost_usd=None, pricing=None)
    lease = ModelLease("fault-campaign", "fault-round", 7, "fault-lease", "review-model",
        admission.request_hash, admission.schema_hash, admission.input_allowance,
        admission.request_body, context.model_dump(mode="json"), 1.0, "meta")
    state = SimpleNamespace(lease=lease, sessions=sessions, invoke=None, round_id=round_id,
                           authority=glue.ResourceAuthority(resources.new_incarnation()))
    real_claim = resources.claim_provider_slot
    real_validate = resources.validate_provider_slot
    real_finish, real_fail = glue.rounds.finish_model_round, glue.rounds.fail_model_round
    real_register, real_ack = glue.cleanup.record_pending, glue.cleanup.acknowledge_settled
    real_reconstruct = glue.reconstruct_admitted_request

    def claim(session, *args, **kwargs):
        assert sessions.active == 1
        grant = real_claim(session, *args, **kwargs)
        if grant is not None:
            sessions.claimed, sessions.grant = True, grant
        return grant

    def validate(session, *args, **kwargs):
        assert sessions.active == 1
        return real_validate(session, *args, **kwargs) and sessions.heartbeat

    def finish(session, lease, completion, **kwargs):
        assert sessions.active == 1
        sessions.finishes.append((lease, completion, kwargs))
        sessions.settled = sessions.accept_finish and real_finish(session, lease, completion, **kwargs)
        return sessions.settled

    def fail(session, lease, **kwargs):
        sessions.failures.append(kwargs)
        sessions.settled = real_fail(session, lease, **kwargs)
        return sessions.settled

    def register(session, lease, **kwargs):
        assert sessions.active == 1 and not sessions.claim_committed
        if sessions.register_refused:
            return None
        ticket = real_register(session, lease, **kwargs)
        if ticket is not None:
            sessions.cleanup_registered, sessions._cleanup_state = True, "pending"
        return ticket

    def acknowledge(session, ticket, **kwargs):
        assert sessions.active == 1 and sessions.cleanup_committed
        assert ticket == sessions.grant.cleanup_ticket
        assert kwargs["proof"] == "cooperative_tasks_settled"
        sessions.ack_attempts += 1
        settled = real_ack(session, ticket, **kwargs)
        if settled:
            sessions._cleanup_state = "settled"
        return settled

    def reconstruct(*args, **kwargs):
        if state.lease != lease:
            raise ValueError("synthetic immutable admission reconstruction drift")
        return real_reconstruct(*args, **kwargs)

    async def invoke(request, **kwargs):
        assert sessions.active == 0 and sessions.claim_committed and sessions.cleanup_committed
        sessions.dispatches += 1
        return await state.invoke(request, **kwargs) if state.invoke else outcome(request)

    monkeypatch.setattr(resources, "claim_provider_slot", claim)
    monkeypatch.setattr(resources, "validate_provider_slot", validate)
    monkeypatch.setattr(glue.rounds, "finish_model_round", finish)
    monkeypatch.setattr(glue.rounds, "fail_model_round", fail)
    monkeypatch.setattr(glue, "invoke_admitted", invoke)
    monkeypatch.setattr(glue, "reconstruct_admitted_request", reconstruct)
    monkeypatch.setattr(glue.cleanup, "record_pending", register)
    monkeypatch.setattr(glue.cleanup, "acknowledge_settled", acknowledge)
    yield state
    engine.dispose()


def outcome(request, **changes):
    values = dict(call_id=request.call_id, generation=request.generation,
        request_hash=request.admission.request_hash, requested_model=request.requested_model,
        state="success", code="accepted", invocation_started=True, remote_outcome_unknown=False,
        elapsed_seconds=0.001, usage=ModelUsage(input_tokens=1, output_tokens=1),
        decision=PlannerDecision(proposal={"kind": "stop", "reason": "synthetic review only"}))
    values.update(changes)
    return TransportOutcome(**values)


async def run(state, **kwargs):
    return await glue.run_model_intent(state.round_id, sessions=state.sessions, authority=state.authority,
        now=lambda: STAMP,
        control=kwargs.pop("control", lambda: "RUNNING"), provider=kwargs.pop("provider", NoNetworkMeta()),
        poll_seconds=0.001, settlement_seconds=0.005, **kwargs)


def test_source_has_no_transaction_await_or_retry_loop():
    tree = ast.parse(Path(glue.__file__).read_text())
    transactions = [node for node in ast.walk(tree) if isinstance(node, ast.With)]
    assert len(transactions) >= 4
    assert all(not any(isinstance(child, ast.Await) for child in ast.walk(node)) for node in transactions)
    calls = [node for node in ast.walk(tree) if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id == "invoke_admitted"]
    assert len(calls) == 1


def test_commit_then_dispatch_and_fenced_finish_are_separate(harness):
    result = asyncio.run(run(harness))
    assert result.applied and result.reason == "accepted"
    assert harness.sessions.dispatches == 1 and harness.sessions.commits >= 3
    assert len(harness.sessions.finishes) == 1 and not harness.sessions.failures
    duplicate = asyncio.run(run(harness))
    assert not duplicate.claimed and harness.sessions.dispatches == 1


@pytest.mark.parametrize("boundary", ["before", "after"])
def test_claim_commit_crash_never_dispatches_and_unknown_claim_is_not_resent(harness, boundary):
    harness.sessions.crash = boundary
    with pytest.raises(RuntimeError, match="simulated"):
        asyncio.run(run(harness))
    assert harness.sessions.dispatches == 0
    if boundary == "after":
        harness.sessions.crash = None
        assert not asyncio.run(run(harness)).claimed
        assert harness.sessions.dispatches == 0


@pytest.mark.parametrize("field", ["request_body", "request_hash", "schema_hash", "requested_model"])
def test_stored_admission_drift_never_reaches_dispatch(harness, field):
    value = b"different bytes" if field == "request_body" else "f" * 64 if field.endswith("hash") else "other-model"
    harness.lease = replace(harness.lease, **{field: value})
    result = asyncio.run(run(harness))
    assert result.claimed and result.reason == "admission_invalid"
    assert harness.sessions.dispatches == 0 and not harness.sessions.finishes
    assert not harness.sessions.failures[-1]["transient"]


@pytest.mark.parametrize("field,value", [("generation", 8), ("call_id", "other-round"), ("request_hash", "f" * 64), ("requested_model", "other-model"), ("elapsed_seconds", float("inf"))])
def test_constructed_completion_correlation_cannot_settle_success(harness, field, value):
    async def forged(request, **kwargs):
        return outcome(request).model_copy(update={field: value})
    harness.invoke = forged
    result = asyncio.run(run(harness))
    assert result.reason == "outcome_invalid" and result.remote_outcome_unknown
    assert not harness.sessions.finishes
    assert harness.sessions.failures[-1]["remote_unknown"] and not harness.sessions.failures[-1]["transient"]


@pytest.mark.parametrize("code,state,unknown,transient,expected", [
    ("model_http_429", "rejected", False, True, True),
    ("model_http_503", "uncertain", True, True, False),
    ("model_transport_error", "uncertain", True, True, False),
    ("model_http_400", "rejected", False, True, False),
    ("model_provider_error", "rejected", False, True, False),
])
def test_only_known_rejected_policy_transients_request_future_round(harness, code, state, unknown, transient, expected):
    async def failed(request, **kwargs):
        return outcome(request, state=state, code=code, remote_outcome_unknown=unknown,
            transient_error=transient, decision=None, usage=None)
    harness.invoke = failed
    result = asyncio.run(run(harness))
    assert result.reason == code and harness.sessions.dispatches == 1
    assert harness.sessions.failures[-1]["transient"] is expected
    assert harness.sessions.failures[-1]["code"] == ("model_response_invalid" if code == "model_provider_error" else code)


def test_mock_strategy_refuses_claim_and_provider_access(harness):
    harness.sessions.strategy = "mock_planner"
    result = asyncio.run(run(harness))
    assert result.reason == "strategy_mismatch" and not result.claimed
    assert not harness.sessions.claimed and harness.sessions.dispatches == 0


def test_success_is_not_authority_when_final_generation_fence_refuses(harness):
    harness.sessions.accept_finish = False
    result = asyncio.run(run(harness))
    assert result.reason == "accepted" and not result.applied
    assert not harness.sessions.settled


def test_poll_revocation_is_finite_and_late_success_has_no_authority(harness):
    async def scenario():
        release, entered = asyncio.Event(), asyncio.Event()
        async def stubborn(request, **kwargs):
            entered.set()
            while not release.is_set():
                try:
                    await release.wait()
                except asyncio.CancelledError:
                    continue
            return outcome(request)
        harness.invoke = stubborn
        task = asyncio.create_task(run(harness))
        await entered.wait()
        harness.sessions.heartbeat = False
        try:
            done, _ = await asyncio.wait({task}, timeout=0.3)
            assert task in done
            result = task.result()
            assert result.cleanup_pending and result.remote_outcome_unknown
            assert not harness.sessions.finishes and not harness.sessions.failures
            assert not result.applied  # Resource refusal forbids raw finalization.
        finally:
            release.set()
            await asyncio.sleep(0.01)
        assert not harness.sessions.finishes and harness.sessions.dispatches == 1
    asyncio.run(scenario())


def test_repeated_caller_cancel_cannot_extend_cleanup_or_apply_late_result(harness):
    async def scenario():
        entered, release = asyncio.Event(), asyncio.Event()
        async def stubborn(request, **kwargs):
            entered.set()
            while not release.is_set():
                try:
                    await release.wait()
                except asyncio.CancelledError:
                    continue
            return outcome(request)
        harness.invoke = stubborn
        task = asyncio.create_task(run(harness))
        await entered.wait()
        try:
            for _ in range(3):
                task.cancel()
                await asyncio.sleep(0.001)
            done, _ = await asyncio.wait({task}, timeout=0.3)
            assert task in done
            result = task.result()
            assert result.cleanup_pending and result.remote_outcome_unknown
            assert not harness.sessions.finishes
            assert harness.sessions.failures[-1]["remote_unknown"]
            assert not harness.sessions.failures[-1]["transient"]
        finally:
            release.set()
            await asyncio.sleep(0.01)
        assert not harness.sessions.finishes
    asyncio.run(scenario())


def test_rejected_forged_cleanup_claim_is_not_silently_lost(harness):
    async def forged(request, **kwargs):
        return outcome(request).model_copy(update={"cleanup_pending": True})
    harness.invoke = forged
    result = asyncio.run(run(harness))
    assert result.reason == "outcome_invalid" and result.cleanup_pending
    assert result.remote_outcome_unknown and result.outcome is None
    assert not harness.sessions.finishes


def test_cleanup_refusal_never_constructs_transport_or_grants_io(harness):
    harness.sessions.register_refused = True
    result = asyncio.run(run(harness))
    assert not result.claimed and result.reason == "not_claimed"
    assert not result.cleanup_pending and harness.sessions.dispatches == 0
    with harness.sessions.factory() as session:
        assert session.get(PlannerCleanup, harness.round_id) is None
        assert session.get(glue.PlannerRound, harness.round_id).status == "queued"
        assert resources.safe_snapshot(session).state == "idle"
    assert not harness.sessions.finishes and not harness.sessions.ack_attempts


def test_ack_commit_rollback_cannot_clear_result_cleanup_pending(harness):
    harness.sessions.ack_commit_failure = True
    result = asyncio.run(run(harness))
    assert result.applied and result.reason == "accepted"
    assert harness.sessions.ack_attempts > 0
    assert harness.sessions.cleanup_state == "pending"
    assert result.cleanup_pending and not result.remote_outcome_unknown


def test_wrapper_done_with_observed_inner_pending_ack_only_after_last_child(harness):
    async def scenario():
        release, cancel = asyncio.Event(), asyncio.Event()
        children = []
        async def wrapper(request, **kwargs):
            child = asyncio.create_task(release.wait())
            children.append(child)
            kwargs["task_observer"](child)
            cancel.set()
            return outcome(request, state="uncertain", code="model_timeout", decision=None,
                usage=None, remote_outcome_unknown=True, cleanup_pending=True)
        harness.invoke = wrapper
        try:
            result = await run(harness, cancel_event=cancel)
            assert result.cleanup_pending and result.remote_outcome_unknown
            assert result.reason == "cancelled" and not harness.sessions.finishes
            assert not children[0].done() and harness.sessions.cleanup_state == "pending"
            assert harness.sessions.ack_attempts == 0
            harness.sessions.cleanup_state = "unknown"  # Synthetic reconciler owner loss.
            harness.sessions.heartbeat = False
            harness.sessions.accept_finish = False
            before = (len(harness.sessions.finishes), len(harness.sessions.failures), harness.sessions.dispatches)
            remote_unknown = harness.sessions.failures[-1]["remote_unknown"]
            release.set()
            await children[0]
            await asyncio.sleep(0)
            assert harness.sessions.cleanup_state == "settled"
            assert (len(harness.sessions.finishes), len(harness.sessions.failures), harness.sessions.dispatches) == before
            assert remote_unknown and harness.sessions.failures[-1]["remote_unknown"]
            assert result.cleanup_pending  # Historical frozen caller result stays unchanged.
        finally:
            release.set()
            await asyncio.gather(*children)
    asyncio.run(scenario())


def test_parent_revoked_after_registration_never_dispatches_but_settles_zero_tasks(harness):
    harness.sessions.heartbeat = False
    result = asyncio.run(run(harness))
    assert result.reason == "lease_revoked" and harness.sessions.dispatches == 0
    assert harness.sessions.cleanup_state == "settled" and not result.cleanup_pending
    assert not harness.sessions.finishes


@pytest.mark.parametrize("failure_index", [1, 2, 3])
def test_observer_creation_failure_before_await_prevents_provider_io(harness, monkeypatch, failure_index):
    class LiteralProvider(NoNetworkMeta):
        calls = 0
        @property
        def available(self):
            return True
        @property
        def model(self):
            return BUILDERS["MODEL"]
        async def complete(self, *args, **kwargs):
            self.calls += 1
            pytest.fail("observation failure allowed provider invocation")
    provider = LiteralProvider()
    tracked = []
    original_track = glue._LocalCleanupOwner.track
    def fail_track(owner, task):
        original_track(owner, task)
        tracked.append(task)
        if len(tracked) == failure_index:
            raise RuntimeError("synthetic observer failure before first await")
    monkeypatch.setattr(glue._LocalCleanupOwner, "track", fail_track)
    monkeypatch.setattr(glue, "invoke_admitted", durable_transport.invoke_admitted)
    async def scenario():
        result = await run(harness, provider=provider)
        await asyncio.sleep(0)
        assert provider.calls == 0
        assert result.cleanup_pending and harness.sessions.cleanup_state == "pending"
        assert harness.sessions.ack_attempts == 0
        assert all(task.done() for task in tracked)
    asyncio.run(scenario())
