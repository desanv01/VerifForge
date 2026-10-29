"""Isolated database foundation tests; synthetic outcomes never run a simulator."""
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
import json

from alembic import command
from alembic.config import Config
import pytest
from sqlalchemy import select, text
from sqlalchemy.exc import IntegrityError

from veriforge.adapters import get_adapter
from veriforge.contracts import AttemptResult, Experiment, canonical_hash
from veriforge.core import artifacts, service, worker
from veriforge.core.catalog import seed_catalog
from veriforge.core.database import Attempt, AttemptExperiment, Campaign, ExperimentRecord, Finding, make_engine, now, session_factory
from veriforge.core.planner_experiments import admit_experiment, queue_experiment, resolve_attempt_experiment, settle_sequence
import hashlib
from veriforge.contracts import ArtifactReference


def plan(name="first", value=17, fixture="fifo_correct"):
    return Experiment(experiment_id=name, fixture_id=fixture, objective_ids=("FIFO_BEHAVIOR",), operations=({"op": "reset"}, {"op": "write", "value": value}, {"op": "read"})).model_dump(mode="json")


def migrate(engine, revision="head"):
    config = Config("alembic.ini")
    with engine.begin() as connection:
        config.attributes["connection"] = connection
        command.upgrade(config, revision)


@pytest.fixture
def db(tmp_path):
    engine = make_engine("sqlite:///" + (tmp_path / "state.db").as_posix())
    migrate(engine)
    sessions = session_factory(engine)
    with sessions.begin() as session:
        seed_catalog(session)
    yield engine, sessions
    engine.dispose()


def create(sessions, *, policy="single", key="create", payload=None):
    with sessions.begin() as session:
        row = service.create_campaign(session, "fifo_correct", payload or plan(), key, completion_policy=policy)
        service.start(session, row.id)
        session.flush()
        attempt = session.scalar(select(Attempt).where(Attempt.campaign_id == row.id))
        return row.id, attempt.id


def finish(sessions, attempt_id, *, verdict="pass_within_scope"):
    with sessions.begin() as session:
        lease, _, _ = service.claim(session, attempt_id)
        record = resolve_attempt_experiment(session, session.get(Attempt, attempt_id))
        payload = dict(attempt_id="synthetic-result", experiment_id=record.plan["experiment_id"], execution_status="completed", verdict=verdict,
            identity={**record.identity, "environment_hash": "a" * 64}, uncertainties=("Synthetic foundation test, no HDL executed",))
        if verdict == "design_mismatch":
            payload["failure_signature"] = {"requirement_id": "FIFO_BEHAVIOR", "failure_class": "fifo.data", "first_cycle": 2, "expected": "17", "observed": "0", "signature_hash": "b" * 64}
        assert service.finalize(session, attempt_id, lease, AttemptResult(**payload).model_dump(mode="json"))


def test_initial_experiment_and_binding_are_immutable_in_orm_and_sql(db):
    campaign_id, attempt_id = create(db[1])
    with db[1]() as session:
        record = resolve_attempt_experiment(session, session.get(Attempt, attempt_id))
        record_id = record.id
        assert record.plan == plan() and record.plan_hash == service.digest(plan())
        assert record.identity["experiment_hash"] == canonical_hash(Experiment.model_validate(plan()))
    with pytest.raises(ValueError, match="immutable"):
        with db[1].begin() as session:
            session.get(ExperimentRecord, record_id).plan = plan("rewritten")
    with pytest.raises(IntegrityError):
        with db[0].begin() as connection:
            connection.execute(text("UPDATE experiments SET plan_hash = 'changed' WHERE id=:id"), {"id": record_id})
    with pytest.raises(IntegrityError):
        with db[0].begin() as connection:
            connection.execute(text("DELETE FROM attempt_experiments WHERE attempt_id=:id"), {"id": attempt_id})
    with db[1]() as session:
        assert resolve_attempt_experiment(session, session.get(Attempt, attempt_id)).id == record_id


def test_cross_parent_binding_fails_database_constraint(db):
    first_campaign, first_attempt = create(db[1])
    second_campaign, _ = create(db[1], key="second")
    with db[1]() as session:
        second_record = session.scalar(select(ExperimentRecord).where(ExperimentRecord.campaign_id == second_campaign))
        second_record_id = second_record.id
    with pytest.raises(IntegrityError):
        with db[1].begin() as session:
            extra = Attempt(campaign_id=first_campaign)
            session.add(extra)
            session.flush()
            session.add(AttemptExperiment(attempt_id=extra.id, campaign_id=first_campaign, experiment_id=second_record_id))
    with pytest.raises(IntegrityError):
        with db[1].begin() as session:
            session.add(AttemptExperiment(attempt_id=first_attempt, campaign_id=second_campaign, experiment_id=second_record_id))


def test_missing_binding_fails_closed_without_execution(db, tmp_path):
    campaign_id, _ = create(db[1])
    with db[1].begin() as session:
        missing = Attempt(campaign_id=campaign_id)
        session.add(missing)
        session.flush()
        missing_id = missing.id
    called = []
    with pytest.raises(ValueError, match="binding"):
        worker.execute_attempt(missing_id, sessions=db[1], runner=lambda *args, **kwargs: called.append(True), artifact_root=tmp_path)
    assert called == []


def test_attempt_plan_identity_does_not_follow_mutable_parent(db, tmp_path):
    campaign_id, attempt_id = create(db[1])
    with db[1].begin() as session:
        campaign = session.get(Campaign, campaign_id)
        campaign.plan = plan("wrong-parent-copy", 22)
        campaign.identity = {**campaign.identity, "experiment_hash": "0" * 64}
    seen = []
    def stub(experiment, output, *, cancel_event):
        seen.append(experiment.experiment_id)
        return AttemptResult(attempt_id="stub", experiment_id=experiment.experiment_id, execution_status="completed", verdict="unsupported", uncertainties=("Stub, no simulator",))
    assert worker.execute_attempt(attempt_id, sessions=db[1], runner=stub, artifact_root=tmp_path)["accepted"]
    assert seen == ["first"]
    with db[1]() as session:
        view = service.campaign_view(session, session.get(Campaign, campaign_id))
        assert view["plan"]["experiment_id"] == "first"
        assert view["identity"]["experiment_hash"] == canonical_hash(Experiment.model_validate(plan()))


def test_sequential_child_results_never_finish_parent_or_replace_finding(db):
    campaign_id, initial_attempt = create(db[1], policy="sequential")
    finish(db[1], initial_attempt, verdict="design_mismatch")
    with db[1].begin() as session:
        parent = session.get(Campaign, campaign_id)
        assert parent.execution_status == "running" and parent.verdict is None
        record = admit_experiment(session, campaign_id, plan("second", 42), "round-2")
        next_attempt = session.scalar(select(AttemptExperiment.attempt_id).where(AttemptExperiment.experiment_id == record.id))
        next_record_id = record.id
    finish(db[1], next_attempt)
    with db[1].begin() as session:
        assert admit_experiment(session, campaign_id, plan("second", 42), "round-2").id == next_record_id
        parent = session.get(Campaign, campaign_id)
        assert parent.execution_status == "running" and parent.verdict is None
        findings = list(session.scalars(select(Finding).where(Finding.campaign_id == campaign_id)))
        assert len(findings) == 1 and findings[0].data["experiment_record_id"] != next_record_id
        assert findings[0].data["plan_hash"] == service.digest(plan())
        settled = settle_sequence(session, campaign_id, reason="budget_exhausted")
        assert settled.execution_status == "completed" and settled.verdict is None
    with db[1].begin() as session:
        with pytest.raises(service.Conflict): admit_experiment(session, campaign_id, plan("third"), "round-3")


def test_attempts_cannot_alias_other_experiment_identity(db):
    campaign_id, attempt_id = create(db[1], policy="sequential")
    finish(db[1], attempt_id)
    with db[1].begin() as session:
        record = admit_experiment(session, campaign_id, plan("next", 42), "next")
        next_attempt = session.scalar(select(AttemptExperiment.attempt_id).where(AttemptExperiment.experiment_id == record.id))
        lease, _, _ = service.claim(session, next_attempt)
        original_record = resolve_attempt_experiment(session, session.get(Attempt, attempt_id))
    with pytest.raises(ValueError, match="different experiment"):
        with db[1].begin() as session:
            result = AttemptResult(attempt_id="wrong", experiment_id="first", execution_status="completed", verdict="pass_within_scope", identity={**original_record.identity, "environment_hash": "a" * 64})
            service.finalize(session, next_attempt, lease, result.model_dump(mode="json"))


def test_admission_serializes_duplicate_delivery_and_rejects_binding_drift(db):
    campaign_id, attempt_id = create(db[1], policy="sequential")
    finish(db[1], attempt_id)
    def admit(_):
        with db[1].begin() as session: return admit_experiment(session, campaign_id, plan("next"), "next").id
    with ThreadPoolExecutor(max_workers=3) as pool:
        ids = list(pool.map(admit, range(3)))
    assert len(set(ids)) == 1
    with db[1].begin() as session:
        with pytest.raises(service.Conflict): admit_experiment(session, campaign_id, plan("wrong"), "next")
        with pytest.raises(service.Conflict): settle_sequence(session, campaign_id, reason="stopped")


def test_cancellation_revokes_sequential_child_without_erasing_history(db):
    campaign_id, attempt_id = create(db[1], policy="sequential")
    finish(db[1], attempt_id)
    with db[1].begin() as session:
        record = admit_experiment(session, campaign_id, plan("next"), "next")
        next_attempt = session.scalar(select(AttemptExperiment.attempt_id).where(AttemptExperiment.experiment_id == record.id))
        lease, _, _ = service.claim(session, next_attempt)
    with db[1].begin() as session: service.cancel(session, campaign_id)
    with db[1].begin() as session:
        assert not service.finalize(session, next_attempt, lease, {})
        assert session.get(Attempt, attempt_id).execution_status == "completed"
        assert session.get(Campaign, campaign_id).execution_status == "cancelled"


def test_stale_child_is_interrupted_parent_requires_explicit_decision(db):
    campaign_id, attempt_id = create(db[1], policy="sequential")
    with db[1].begin() as session:
        service.claim(session, attempt_id)
        session.get(Attempt, attempt_id).heartbeat_at = now() - timedelta(seconds=1000)
    with db[1].begin() as session:
        assert service.recover_stale(session) == 1
        parent = session.get(Campaign, campaign_id)
        assert parent.execution_status == "running" and parent.verdict is None
        with pytest.raises(service.Conflict, match="blocks continuation"): admit_experiment(session, campaign_id, plan("next"), "next")
        settle_sequence(session, campaign_id, reason="execution_blocked")


def test_new_experiment_cannot_change_binding_or_alias_wire_id(db):
    campaign_id, attempt_id = create(db[1], policy="sequential")
    finish(db[1], attempt_id)
    with db[1].begin() as session:
        with pytest.raises(service.Conflict, match="already bound"):
            admit_experiment(session, campaign_id, plan(), "different-admission-same-wire-id")
        changed = plan("different-binding")
        changed["parameters"] = {"width": 8, "depth": 8}
        with pytest.raises(service.Conflict, match="frozen campaign binding"):
            admit_experiment(session, campaign_id, changed, "different-binding")


def test_second_child_worker_evidence_and_replay_use_bound_plan(db, tmp_path):
    campaign_id, first_attempt = create(db[1], policy="sequential")
    finish(db[1], first_attempt)
    with db[1].begin() as session:
        second = admit_experiment(session, campaign_id, plan("second", 42), "round-2")
        second_id = second.id
        second_attempt = session.scalar(select(AttemptExperiment.attempt_id).where(AttemptExperiment.experiment_id == second_id))
    root = tmp_path / "evidence"
    def stub(experiment, output, *, cancel_event):
        assert experiment.experiment_id == "second" and experiment.operations[1].value == 42
        output.mkdir(parents=True)
        data = {"experiment.json": experiment.model_dump_json().encode(), "stimulus.json": json.dumps(get_adapter("rtl.fifo.v1").expand_stimulus(experiment)).encode()}
        refs = []
        for index, (name, payload) in enumerate(data.items()):
            (output / name).write_bytes(payload)
            refs.append(ArtifactReference(artifact_id=f"file-{index}", path=name, sha256=hashlib.sha256(payload).hexdigest(), size_bytes=len(payload), media_type="application/json"))
        result = AttemptResult(attempt_id="synthetic-child", experiment_id=experiment.experiment_id, execution_status="completed", verdict="pass_within_scope", identity={**worker.installed_identity(experiment), "environment_hash": "a" * 64}, artifacts=tuple(refs), uncertainties=("Synthetic foundation evidence, no simulator",))
        (output / "result.json").write_text(result.model_dump_json())
        return result
    assert worker.execute_attempt(second_attempt, sessions=db[1], runner=stub, artifact_root=root)["accepted"]
    with db[1].begin() as session:
        _, experiment, _, parent = artifacts.attempt_snapshot(session, second_attempt, root=root, checked_required=True)
        assert experiment.experiment_id == "second" and parent.plan["experiment_id"] == "first"
        replay = artifacts.replay_campaign(session, second_attempt, "second-replay", root=root)
        assert replay.id != campaign_id and replay.plan == plan("second", 42)
        assert replay.identity == session.get(ExperimentRecord, second_id).identity
        replay_record = session.scalar(select(ExperimentRecord).where(ExperimentRecord.campaign_id == replay.id))
        assert replay_record.origin == "replay" and replay_record.wire_id == "second"
        assert parent.execution_status == "running" and parent.verdict is None


def test_multiple_attempts_can_bind_same_record_but_never_run_concurrently(db):
    campaign_id, attempt_id = create(db[1], policy="sequential")
    with db[1].begin() as session:
        record = resolve_attempt_experiment(session, session.get(Attempt, attempt_id))
        with pytest.raises(service.Conflict, match="active execution"):
            queue_experiment(session, session.get(Campaign, campaign_id), record)
    finish(db[1], attempt_id)
    with db[1].begin() as session:
        parent = service.lock(session, campaign_id)
        record = resolve_attempt_experiment(session, session.get(Attempt, attempt_id))
        retry = queue_experiment(session, parent, record)
        assert retry.id != attempt_id
        assert resolve_attempt_experiment(session, retry).id == record.id


def test_downgrade_refuses_to_discard_sequential_semantics(db):
    create(db[1], policy="sequential")
    config = Config("alembic.ini")
    with pytest.raises(ValueError, match="Cannot downgrade"):
        with db[0].begin() as connection:
            config.attributes["connection"] = connection
            command.downgrade(config, "7bf054adapter")
