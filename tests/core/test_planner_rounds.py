"""Durable-state tests use typed fake providers/results, isolated SQLite, no tools."""
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from datetime import datetime, timedelta, timezone
import hashlib
import json

from alembic import command
from alembic.config import Config
import pytest
from sqlalchemy import func, select, text
from sqlalchemy.exc import IntegrityError

from veriforge.contracts import AttemptResult, Experiment
from veriforge.core import planner_rounds as rounds, service
from veriforge.core.catalog import seed_catalog
from veriforge.core.database import (Attempt, AttemptExperiment, Campaign, ExperimentRecord, PlannerFeedback, PlannerIntent,
    Finding, PlannerModelCall, PlannerReservation, PlannerRound, PlannerState, make_engine, session_factory)
from veriforge.core.planner_experiments import resolve_attempt_experiment
from veriforge.planner.models import InvestigationLimits, Pricing, ProviderCompletion

AT = datetime(2026, 10, 29, 15, tzinfo=timezone.utc)


def proposal(value=42):
    return {"proposal": {"kind": "experiment", "objective_ids": ["FIFO_BEHAVIOR"], "seed": 1,
        "operations": [{"op": "reset"}, {"op": "write", "value": value}, {"op": "read"}]}}


def completion(value=42, **overrides):
    payload = dict(model="fake-model-1", content=json.dumps(proposal(value)), usage={"input_tokens": 10, "output_tokens": 10}, finish_reason="stop")
    payload.update(overrides)
    return ProviderCompletion(**payload)


def result(session, attempt_id, *, verdict="pass_within_scope", artifacts=()):
    record = resolve_attempt_experiment(session, session.get(Attempt, attempt_id))
    payload = dict(attempt_id="synthetic-checker", experiment_id=record.wire_id, execution_status="completed", verdict=verdict,
        identity={**record.identity, "environment_hash": "b" * 64}, artifacts=artifacts, usage={"cycles": 3}, uncertainties=("Synthetic typed result, no simulator executed",))
    if verdict == "design_mismatch":
        payload["failure_signature"] = {"requirement_id": "FIFO_BEHAVIOR", "failure_class": "fifo.data", "first_cycle": 2, "expected": "42", "observed": "0", "signature_hash": "c" * 64}
    return AttemptResult(**payload).model_dump(mode="json")


@pytest.fixture
def db(tmp_path):
    engine = make_engine("sqlite:///" + (tmp_path / "planner.db").as_posix())
    config = Config("alembic.ini")
    with engine.begin() as connection:
        config.attributes["connection"] = connection
        command.upgrade(config, "head")
    sessions = session_factory(engine)
    with sessions.begin() as session: seed_catalog(session)
    yield engine, sessions
    engine.dispose()


def seeded(db, **limit_overrides):
    sessions = db[1]
    limits = InvestigationLimits(max_rounds=5, max_total_cycles=80, max_cycles_per_experiment=8, max_wall_seconds=60.0,
        max_execution_seconds=2.0, max_model_seconds=2.0, **limit_overrides)
    experiment = Experiment(experiment_id="seed", fixture_id="fifo_correct", objective_ids=("FIFO_BEHAVIOR",), budget={"max_cycles": 8, "wall_time_seconds": 2.0},
        operations=({"op": "reset"}, {"op": "write", "value": 17}, {"op": "read"}))
    with sessions.begin() as session:
        parent = service.create_campaign(session, experiment.fixture_id, experiment.model_dump(mode="json"), "seed", completion_policy="sequential")
        service.start(session, parent.id)
        attempt_id = session.scalar(select(Attempt.id).where(Attempt.campaign_id == parent.id))
        lease, _, _ = service.claim(session, attempt_id)
        service.finalize(session, attempt_id, lease, result(session, attempt_id))
        parent_id = parent.id
    return parent_id, attempt_id, limits


def attached(db, *, pricing=None, limits=None):
    parent_id, seed_id, defaults = seeded(db)
    with db[1].begin() as session:
        rounds.attach_planner(session, parent_id, limits=limits or defaults, requested_model="fake-model-1", at=AT, pricing=pricing)
    return parent_id, seed_id


def admitted(db, parent_id):
    with db[1].begin() as session:
        row = rounds.advance(session, parent_id, at=AT + timedelta(milliseconds=100))
        round_id = row.id
    with db[1].begin() as session:
        return rounds.claim_model_round(session, round_id, at=AT + timedelta(milliseconds=200))


def child_attempt(session, parent_id):
    return session.scalar(select(Attempt.id).where(Attempt.campaign_id == parent_id, Attempt.execution_status == "queued"))


def test_typed_fake_sequence_restarts_at_each_boundary(db):
    parent_id, seed_id = attached(db)
    # Every block uses a new Session, emulating process loss between committed steps.
    with db[1]() as session:
        assert rounds.totals(session, parent_id)["cycles"] == 8
        intent = rounds.pending_intents(session, at=AT)[0]
        initial_generation = intent.generation
    with db[1].begin() as session:
        row = rounds.advance(session, parent_id, at=AT, expected_generation=initial_generation)
        round_id = row.id
    with db[1]() as session:
        call = session.get(PlannerModelCall, round_id)
        assert hashlib.sha256(call.request_body).hexdigest() == call.request_hash
        assert call.context["feedback"][0]["experiment_id"] == "seed"
        assert call.context["feedback"][0]["verdict"] == "pass_within_scope"
    with db[1].begin() as session: lease = rounds.claim_model_round(session, round_id, at=AT + timedelta(milliseconds=100))
    # This explicit fake provider is not Meta inference.
    class TypedFakeProvider:
        def complete(self, lease):
            assert lease.requested_model == "fake-model-1"
            return completion()
    received = TypedFakeProvider().complete(lease)
    with db[1].begin() as session:
        assert rounds.finish_model_round(session, lease, received, at=AT + timedelta(milliseconds=200))
    with db[1].begin() as session:
        attempt_id = child_attempt(session, parent_id)
        execution_lease, submitted, _ = service.claim(session, attempt_id)
        assert submitted["experiment_id"] == "round-" + round_id and submitted["operations"][1]["value"] == 42
    with db[1].begin() as session:
        assert rounds.finish_execution_step(session, attempt_id, execution_lease, result(session, attempt_id), at=AT + timedelta(milliseconds=300))
    with db[1].begin() as session:
        next_round = rounds.advance(session, parent_id, at=AT + timedelta(milliseconds=400))
        assert next_round.ordinal == 2
        assert len(session.get(PlannerModelCall, next_round.id).context["feedback"]) == 2
        second_id = next_round.id
    with db[1].begin() as session: second = rounds.claim_model_round(session, second_id, at=AT + timedelta(milliseconds=500))
    stop = completion(content=json.dumps({"proposal": {"kind": "stop", "reason": "Synthetic finite sequence complete"}}))
    with db[1].begin() as session: assert rounds.finish_model_round(session, second, stop, at=AT + timedelta(milliseconds=600))
    with db[1]() as session:
        state = session.get(PlannerState, parent_id)
        assert state.phase == "settled" and state.stop_reason == "model_stopped"
        assert session.get(Campaign, parent_id).verdict is None
        assert session.scalar(select(func.count()).select_from(PlannerFeedback)) == 2
        assert session.scalar(select(func.count()).select_from(PlannerReservation)) == 4


def test_crash_after_reservation_before_dispatch_does_not_double_charge(db):
    parent_id, _ = attached(db)
    with db[1].begin() as session:
        first = rounds.advance(session, parent_id, at=AT)
        first_id = first.id
        before = rounds.totals(session, parent_id)
    with db[1].begin() as session:
        assert rounds.advance(session, parent_id, at=AT + timedelta(milliseconds=100)).id == first_id
        intent = rounds.pending_intents(session, at=AT)[0]
        assert intent.kind == "model"
        rounds.acknowledge_intent(session, intent.id, at=AT)
    with db[1]() as session:
        assert not rounds.pending_intents(session, at=AT + timedelta(seconds=1))
        assert len(rounds.pending_intents(session, at=AT + timedelta(seconds=31))) == 1
        assert rounds.totals(session, parent_id) == before
    with db[1].begin() as session:
        assert rounds.claim_model_round(session, first_id, at=AT + timedelta(milliseconds=200)) is not None
    with db[1].begin() as session:
        assert rounds.claim_model_round(session, first_id, at=AT + timedelta(milliseconds=300)) is None


def test_remote_response_lost_before_commit_becomes_uncertain_no_retry_no_refund(db):
    parent_id, _ = attached(db)
    lease = admitted(db, parent_id)
    received = completion()
    with pytest.raises(RuntimeError):
        with db[1].begin() as session:
            rounds.finish_model_round(session, lease, received, at=AT + timedelta(milliseconds=300))
            raise RuntimeError("Emulated loss before transaction commit")
    with db[1]() as session: before = rounds.totals(session, parent_id)
    with db[1].begin() as session:
        assert rounds.recover_rounds(session, at=AT + timedelta(seconds=3)) == 1
    with db[1].begin() as session:
        assert rounds.advance(session, parent_id, at=AT + timedelta(seconds=4)) is None
        assert not rounds.finish_model_round(session, lease, received, at=AT + timedelta(seconds=4))
        state = session.get(PlannerState, parent_id)
        assert state.phase == "interrupted" and state.stop_reason == "model_call_uncertain"
        assert session.get(PlannerModelCall, lease.round_id).outcome == "remote_outcome_unknown"
        assert rounds.totals(session, parent_id) == before
        assert session.scalar(select(func.count()).select_from(ExperimentRecord)) == 1


def test_execution_commit_before_outbox_is_recovered_once(db):
    parent_id, _ = attached(db)
    lease = admitted(db, parent_id)
    with db[1].begin() as session:
        rounds.finish_model_round(session, lease, completion(), at=AT + timedelta(milliseconds=300))
        attempt_id = child_attempt(session, parent_id)
        execution_lease, _, _ = service.claim(session, attempt_id)
    # Emulate old worker finalization without the new atomic continuation wrapper.
    with db[1].begin() as session: service.finalize(session, attempt_id, execution_lease, result(session, attempt_id))
    with db[1].begin() as session: rounds.recover_rounds(session, at=AT + timedelta(milliseconds=400))
    with db[1]() as session:
        receipt = session.get(PlannerFeedback, attempt_id)
        assert receipt is not None
        intents = list(session.scalars(select(PlannerIntent).where(PlannerIntent.kind == "advance")))
        count = len(intents)
    with db[1].begin() as session: rounds.recover_rounds(session, at=AT + timedelta(milliseconds=500))
    with db[1]() as session:
        assert session.scalar(select(func.count()).select_from(PlannerFeedback)) == 2
        assert session.scalar(select(func.count()).select_from(PlannerIntent).where(PlannerIntent.kind == "advance")) == count


def test_cancel_and_forged_generation_cannot_admit_late_output(db):
    parent_id, _ = attached(db)
    lease = admitted(db, parent_id)
    with db[1].begin() as session:
        assert not rounds.finish_model_round(session, replace(lease, generation=lease.generation + 1), completion(), at=AT + timedelta(milliseconds=300))
        assert not rounds.finish_model_round(session, replace(lease, requested_model="changed-model"), completion(), at=AT + timedelta(milliseconds=300))
    with db[1].begin() as session: rounds.cancel_investigation(session, parent_id, at=AT + timedelta(milliseconds=400))
    with db[1].begin() as session:
        assert not rounds.finish_model_round(session, lease, completion(), at=AT + timedelta(milliseconds=500))
        assert rounds.advance(session, parent_id, at=AT + timedelta(milliseconds=600)) is None
        assert session.get(Campaign, parent_id).execution_status == "cancelled"
        assert session.scalar(select(func.count()).select_from(ExperimentRecord)) == 1


@pytest.mark.parametrize("field,sql", [
    ("model", "UPDATE planner_model_calls SET requested_model='changed'"),
    ("hash", "UPDATE planner_model_calls SET request_hash='changed'"),
    ("body", "UPDATE planner_model_calls SET request_body=x'00'"),
    ("policy", "UPDATE planner_states SET policy='{}'"),
    ("reservation", "UPDATE planner_reservations SET reserved_tokens=0"),
    ("reservation_delete", "DELETE FROM planner_reservations"),
    ("feedback", "UPDATE planner_feedback SET feedback='{}'"),
])
def test_admitted_identity_ledger_feedback_immutable_in_database(db, field, sql):
    parent_id, _ = attached(db)
    admitted(db, parent_id)
    with pytest.raises(IntegrityError):
        with db[0].begin() as connection: connection.execute(text(sql))


def test_known_transient_new_round_reserves_again_and_unknown_never_retries(db):
    parent_id, _ = attached(db)
    lease = admitted(db, parent_id)
    with db[1].begin() as session:
        before = rounds.totals(session, parent_id)
        assert rounds.fail_model_round(session, lease, code="model_http_503", transient=True, at=AT + timedelta(milliseconds=300))
    with db[1].begin() as session:
        assert rounds.advance(session, parent_id, at=AT + timedelta(milliseconds=400)) is None
        row = rounds.advance(session, parent_id, at=AT + timedelta(seconds=1))
        assert row.ordinal == 2 and row.id != lease.round_id
        assert rounds.totals(session, parent_id)["tokens"] == before["tokens"] * 2
        next_id = row.id
    with db[1].begin() as session: new = rounds.claim_model_round(session, next_id, at=AT + timedelta(seconds=1, milliseconds=100))
    with db[1].begin() as session:
        rounds.fail_model_round(session, new, code="model_transport_error", transient=True, remote_unknown=True, at=AT + timedelta(seconds=1, milliseconds=200))
    with db[1].begin() as session:
        assert rounds.advance(session, parent_id, at=AT + timedelta(seconds=2)) is None
        assert session.get(PlannerState, parent_id).stop_reason == "model_call_uncertain"


@pytest.mark.parametrize("mode,expected", [("usage", "model_usage_unknown"), ("model", "model_identity_mismatch"), ("invalid", "invalid_proposal_limit")])
def test_completion_validation_and_invalid_counter_survive_restart(db, mode, expected):
    limits = InvestigationLimits(max_invalid_proposals=1, max_total_cycles=80, max_wall_seconds=60.0, max_execution_seconds=2.0, max_model_seconds=2.0)
    parent_id, _ = attached(db, limits=limits)
    lease = admitted(db, parent_id)
    received = completion(usage=None) if mode == "usage" else completion(model="unexpected-model") if mode == "model" else completion(content="invalid JSON private text")
    with db[1].begin() as session: rounds.finish_model_round(session, lease, received, at=AT + timedelta(milliseconds=300))
    with db[1].begin() as session:
        rounds.advance(session, parent_id, at=AT + timedelta(milliseconds=400))
        assert session.get(PlannerState, parent_id).stop_reason == expected
        call = session.get(PlannerModelCall, lease.round_id)
        assert not hasattr(call, "raw_response") and call.returned_model != "unexpected-model"


def test_cycle_token_time_cost_budgets_and_unknown_price(db):
    limits = InvestigationLimits(max_total_cycles=8, max_wall_seconds=60.0)
    parent_id, _ = attached(db, limits=limits)
    with db[1].begin() as session:
        assert rounds.advance(session, parent_id, at=AT) is None
        assert session.get(PlannerState, parent_id).stop_reason == "cycle_limit"


@pytest.mark.parametrize("settings,pricing,expected", [
    ({"max_model_tokens": 1}, None, "model_token_limit"),
    ({"max_wall_seconds": 2.0}, None, "wall_time_limit"),
    ({"max_cost_usd": "1"}, None, "price_unknown"),
    ({"max_cost_usd": "0.000001"}, {"input_usd_per_million": "1", "output_usd_per_million": "2"}, "cost_limit"),
])
def test_each_admission_budget_blocks_before_model_call(db, settings, pricing, expected):
    policy = {"max_total_cycles": 80, "max_wall_seconds": 60.0, **settings}
    parent_id, _ = attached(db, limits=InvestigationLimits(**policy), pricing=Pricing.model_validate(pricing) if pricing else None)
    with db[1].begin() as session:
        assert rounds.advance(session, parent_id, at=AT) is None
        assert session.get(PlannerState, parent_id).stop_reason == expected
        assert session.scalar(select(func.count()).select_from(PlannerModelCall)) == 0


def test_actual_usage_separate_from_never_refunded_time_token_cost_reservations(db):
    rates = Pricing(input_usd_per_million="1", output_usd_per_million="2")
    parent_id, _ = attached(db, pricing=rates)
    lease = admitted(db, parent_id)
    with db[1]() as session: before = rounds.totals(session, parent_id)
    with db[1].begin() as session:
        rounds.finish_model_round(session, lease, completion(content=json.dumps({"proposal": {"kind": "stop", "reason": "Synthetic stop"}})), at=AT + timedelta(milliseconds=300))
    with db[1]() as session:
        after = rounds.totals(session, parent_id)
        call = session.get(PlannerModelCall, lease.round_id)
        assert after == before and after["seconds"] == 4
        assert call.usage == {"input_tokens": 10, "output_tokens": 10}
        assert call.actual_cost_usd == "0.00003" and after["tokens"] > 20


def test_round_limit_duplicate_policy_and_heartbeat_do_not_reset_budget(db):
    limits = InvestigationLimits(max_rounds=1, max_total_cycles=80, max_wall_seconds=60.0, max_execution_seconds=2.0, max_model_seconds=2.0)
    parent_id, _ = attached(db, limits=limits)
    lease = admitted(db, parent_id)
    with db[1].begin() as session:
        assert rounds.heartbeat_model_round(session, lease, at=AT + timedelta(milliseconds=300))
        rounds.finish_model_round(session, lease, completion(content="invalid"), at=AT + timedelta(milliseconds=400))
    with db[1].begin() as session:
        assert rounds.advance(session, parent_id, at=AT + timedelta(milliseconds=500)) is None
        assert session.get(PlannerState, parent_id).stop_reason == "round_limit"
        assert session.scalar(select(func.count()).select_from(PlannerRound)) == 1


def test_cumulative_time_pool_never_uses_helper_minimum_to_overadmit(db):
    limits = InvestigationLimits(max_total_cycles=80, max_wall_seconds=4.0, max_execution_seconds=2.0, max_model_seconds=2.0)
    parent_id, _ = attached(db, limits=limits)
    lease = admitted(db, parent_id)
    with db[1].begin() as session:
        rounds.finish_model_round(session, lease, completion(), at=AT + timedelta(milliseconds=300))
        assert session.get(PlannerState, parent_id).stop_reason == "wall_time_limit"
        assert session.scalar(select(func.count()).select_from(ExperimentRecord)) == 1
        assert rounds.totals(session, parent_id)["seconds"] == 4


def test_deadline_expiry_and_external_terminal_parent_ignore_late_completion(db):
    parent_id, _ = attached(db)
    lease = admitted(db, parent_id)
    with db[1].begin() as session:
        parent = service.lock(session, parent_id)
        parent.execution_status = "completed"  # external safe stop, no active executor
    with db[1].begin() as session:
        assert not rounds.finish_model_round(session, lease, completion(), at=AT + timedelta(milliseconds=300))
        assert session.get(Campaign, parent_id).execution_status == "completed"
        assert session.get(PlannerState, parent_id).stop_reason == "parent_settled"


def test_duplicate_feedback_and_untrusted_requirement_never_reaches_provider(db):
    parent_id, seed = attached(db)
    with db[1].begin() as session:
        payload = session.get(Attempt, seed).result
        session.get(Attempt, seed).result = {**payload, "requirement_coverage": ["UNREVIEWED"]}
    with db[1].begin() as session:
        assert rounds.advance(session, parent_id, at=AT) is None
        assert session.get(PlannerState, parent_id).stop_reason == "executor_result_invalid"
        assert session.scalar(select(func.count()).select_from(PlannerModelCall)) == 0


def test_feedback_result_hash_change_and_stale_outbox_generation_reject(db):
    parent_id, seed = attached(db)
    lease = admitted(db, parent_id)
    with db[1].begin() as session:
        assert rounds.advance(session, parent_id, at=AT + timedelta(milliseconds=300), expected_generation=0) is None
        payload = session.get(Attempt, seed).result
        session.get(Attempt, seed).result = {**payload, "uncertainties": ["Changed record"]}
    with db[1].begin() as session:
        rounds.advance(session, parent_id, at=AT + timedelta(milliseconds=400))
        assert session.get(PlannerState, parent_id).stop_reason == "finalized_result_changed"


def test_transaction_rollback_after_execution_finalization_leaves_no_partial_feedback(db):
    parent_id, _ = attached(db)
    lease = admitted(db, parent_id)
    with db[1].begin() as session:
        rounds.finish_model_round(session, lease, completion(), at=AT + timedelta(milliseconds=300))
        attempt_id = child_attempt(session, parent_id)
        execution_lease, _, _ = service.claim(session, attempt_id)
    with pytest.raises(RuntimeError):
        with db[1].begin() as session:
            rounds.finish_execution_step(session, attempt_id, execution_lease, result(session, attempt_id), at=AT + timedelta(milliseconds=400))
            raise RuntimeError("Emulated crash before atomic commit")
    with db[1]() as session:
        assert session.get(Attempt, attempt_id).execution_status == "running"
        assert session.get(PlannerFeedback, attempt_id) is None
        assert session.get(PlannerState, parent_id).phase == "waiting_execution"


def test_policy_adoption_is_idempotent_not_a_budget_reset(db):
    parent_id, seed_id, limits = seeded(db)
    with db[1].begin() as session:
        rounds.attach_planner(session, parent_id, limits=limits, requested_model="fake-model-1", at=AT)
    with db[1].begin() as session:
        same = rounds.attach_planner(session, parent_id, limits=limits, requested_model="fake-model-1", at=AT + timedelta(seconds=10))
        assert rounds._utc(same.started_at) == AT
        assert rounds.totals(session, parent_id)["cycles"] == 8
        assert session.scalar(select(func.count()).select_from(PlannerReservation)) == 1
        with pytest.raises(service.Conflict, match="frozen"):
            rounds.attach_planner(session, parent_id, limits=limits, requested_model="different-model", at=AT)


def test_feedback_artifacts_are_db_scoped_and_bounded_in_request(db):
    parent_id, seed_id, limits = seeded(db)
    with db[1].begin() as session:
        payload = session.get(Attempt, seed_id).result
        session.get(Attempt, seed_id).result = {**payload, "artifacts": [{"artifact_id": "artifact-0", "path": "private-log.json", "sha256": "d" * 64, "size_bytes": 0, "media_type": "application/json"}]}
        rounds.attach_planner(session, parent_id, limits=limits, requested_model="fake-model-1", at=AT)
    lease = admitted(db, parent_id)
    with db[1]() as session:
        receipt = session.get(PlannerFeedback, seed_id)
        scoped = receipt.feedback["artifact_ids"]
        assert len(scoped) == 1 and scoped[0].startswith("artifact:") and scoped[0] != "artifact-0"
        request = lease.request_body.decode()
        assert "private-log.json" not in request and "artifact-0" not in request and scoped[0] in request


def test_finding_stop_consumes_one_receipt_and_never_new_round(db):
    parent_id, _ = attached(db)
    lease = admitted(db, parent_id)
    with db[1].begin() as session:
        rounds.finish_model_round(session, lease, completion(), at=AT + timedelta(milliseconds=300))
        attempt_id = child_attempt(session, parent_id)
        execution_lease, _, _ = service.claim(session, attempt_id)
    with db[1].begin() as session:
        rounds.finish_execution_step(session, attempt_id, execution_lease, result(session, attempt_id, verdict="design_mismatch"), at=AT + timedelta(milliseconds=400))
    with db[1].begin() as session:
        assert rounds.advance(session, parent_id, at=AT + timedelta(milliseconds=500)) is None
        assert session.get(PlannerState, parent_id).stop_reason == "finding_observed"
        assert session.scalar(select(func.count()).select_from(PlannerFeedback)) == 2
        assert session.get(Campaign, parent_id).verdict is None


def test_duplicate_proposal_fingerprint_survives_restart(db):
    limits = InvestigationLimits(max_duplicate_proposals=1, max_total_cycles=80, max_cycles_per_experiment=8, max_wall_seconds=60.0, max_execution_seconds=2.0, max_model_seconds=2.0)
    parent_id, _ = attached(db, limits=limits)
    lease = admitted(db, parent_id)
    with db[1].begin() as session:
        rounds.finish_model_round(session, lease, completion(), at=AT + timedelta(milliseconds=300))
        attempt_id = child_attempt(session, parent_id)
        execution_lease, _, _ = service.claim(session, attempt_id)
    with db[1].begin() as session:
        rounds.finish_execution_step(session, attempt_id, execution_lease, result(session, attempt_id), at=AT + timedelta(milliseconds=400))
    with db[1].begin() as session:
        row = rounds.advance(session, parent_id, at=AT + timedelta(milliseconds=500))
        second_id = row.id
    with db[1].begin() as session: second = rounds.claim_model_round(session, second_id, at=AT + timedelta(milliseconds=600))
    with db[1].begin() as session: rounds.finish_model_round(session, second, completion(), at=AT + timedelta(milliseconds=700))
    with db[1].begin() as session:
        assert rounds.advance(session, parent_id, at=AT + timedelta(milliseconds=800)) is None
        assert session.get(PlannerState, parent_id).stop_reason == "no_progress"
        assert session.scalar(select(func.count()).select_from(ExperimentRecord)) == 2
        assert session.scalar(select(func.count()).select_from(PlannerReservation)) == 4


def test_downgrade_cannot_delete_retained_planner_reservations(db):
    parent_id, _ = attached(db)
    config = Config("alembic.ini")
    with pytest.raises(ValueError, match="Cannot downgrade"):
        with db[0].begin() as connection:
            config.attributes["connection"] = connection
            command.downgrade(config, "8u2experiments")
    with db[1]() as session:
        assert session.get(PlannerState, parent_id) is not None
        assert rounds.totals(session, parent_id)["cycles"] == 8


@pytest.mark.parametrize("amount", ["-1", "NaN", "Infinity", "not-a-number"])
def test_corrupt_reservation_text_cannot_reduce_or_bypass_admission(db, amount):
    parent_id, _ = attached(db)
    with db[0].begin() as connection:
        connection.execute(text("INSERT INTO planner_reservations(id,campaign_id,admission_key,reserved_tokens,reserved_cycles,reserved_seconds) VALUES ('bad',:campaign,'bad',0,0,:amount)"), {"campaign": parent_id, "amount": amount})
    with db[1].begin() as session:
        assert rounds.advance(session, parent_id, at=AT) is None
        assert session.get(PlannerState, parent_id).stop_reason == "reservation_invalid"
    with db[1]() as session:
        assert session.scalar(select(func.count()).select_from(PlannerModelCall)) == 0


@pytest.mark.parametrize("amount", ["NaN", "-0.1"])
@pytest.mark.parametrize("boundary", ["claim", "execution_admission"])
def test_corrupt_ledger_revokes_committed_external_admissions(db, amount, boundary):
    parent_id, _ = attached(db)
    with db[1].begin() as session:
        round_id = rounds.advance(session, parent_id, at=AT).id
        lease = rounds.claim_model_round(session, round_id, at=AT) if boundary == "execution_admission" else None
    with db[0].begin() as connection:
        connection.execute(text("INSERT INTO planner_reservations(id,campaign_id,admission_key,reserved_tokens,reserved_cycles,reserved_seconds) VALUES ('bad',:campaign,'bad',0,0,:amount)"), {"campaign": parent_id, "amount": amount})
    with db[1].begin() as session:
        if lease is None:
            assert rounds.claim_model_round(session, round_id, at=AT) is None
            assert session.get(PlannerModelCall, round_id).started_at is None
        else:
            assert rounds.finish_model_round(session, lease, completion(), at=AT) is False
        assert session.get(PlannerState, parent_id).stop_reason == "reservation_invalid"
        assert session.get(PlannerState, parent_id).phase == "interrupted"
        assert session.get(PlannerReservation, "bad").reserved_seconds == amount
        assert session.scalar(select(func.count()).select_from(ExperimentRecord)) == 1


@pytest.mark.parametrize("invalid", ["requirement", "cycles", "artifacts"])
def test_invalid_execution_is_rejected_before_functional_publication(db, invalid):
    parent_id, _ = attached(db)
    model_lease = admitted(db, parent_id)
    with db[1].begin() as session:
        assert rounds.finish_model_round(session, model_lease, completion(), at=AT + timedelta(milliseconds=300))
        attempt_id = child_attempt(session, parent_id)
        token, _, _ = service.claim(session, attempt_id)
    with db[1].begin() as session:
        payload = result(session, attempt_id, verdict="design_mismatch")
        if invalid == "requirement":
            payload["failure_signature"]["requirement_id"] = "UNREVIEWED"
        elif invalid == "cycles":
            payload["usage"]["cycles"] = 9
        else:
            payload["artifacts"] = [{"artifact_id": "same", "path": "one.log", "sha256": "a" * 64, "size_bytes": 1, "media_type": "text/plain"}] * 2
        AttemptResult.model_validate(payload)  # Invalid binding/policy, but valid wire schema.
        assert rounds.finish_execution_step(session, attempt_id, token, payload, at=AT + timedelta(milliseconds=400)) is False
    with db[1]() as session:
        assert session.get(PlannerState, parent_id).stop_reason == "executor_result_invalid"
        assert session.get(Attempt, attempt_id).verdict is None
        assert session.scalar(select(func.count()).select_from(Finding)) == 0


def test_deadline_or_pause_revokes_round_before_dispatch(db):
    parent_id, _ = attached(db)
    with db[1].begin() as session: row = rounds.advance(session, parent_id, at=AT); round_id = row.id
    with db[1].begin() as session:
        assert rounds.claim_model_round(session, round_id, at=AT + timedelta(milliseconds=100), control="PAUSED") is None
        assert session.get(PlannerState, parent_id).stop_reason == "paused"
        assert session.get(PlannerModelCall, round_id).started_at is None


def test_concurrent_advance_and_completion_idempotence(db):
    parent_id, _ = attached(db)
    def advance(_):
        with db[1].begin() as session: return rounds.advance(session, parent_id, at=AT).id
    with ThreadPoolExecutor(max_workers=3) as pool: ids = list(pool.map(advance, range(3)))
    assert len(set(ids)) == 1
    with db[1].begin() as session: lease = rounds.claim_model_round(session, ids[0], at=AT + timedelta(milliseconds=100))
    def finish(_):
        with db[1].begin() as session: return rounds.finish_model_round(session, lease, completion(), at=AT + timedelta(milliseconds=200))
    with ThreadPoolExecutor(max_workers=3) as pool: accepted = list(pool.map(finish, range(3)))
    assert accepted.count(True) == 1
    with db[1]() as session:
        assert session.scalar(select(func.count()).select_from(ExperimentRecord)) == 2
        assert session.scalar(select(func.count()).select_from(PlannerReservation)) == 3
