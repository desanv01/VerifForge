"""Role/config qualification with fake queues/runners and temporary SQLite only."""
from datetime import datetime, timedelta, timezone
import importlib.util
import json
import os
from pathlib import Path
import sys
from types import SimpleNamespace

from alembic import command
from alembic.config import Config
import pytest
from sqlalchemy import select

from veriforge.contracts import AttemptResult, Experiment
from veriforge.core import dispatch, planner_cleanup as cleanup, planner_driver as driver
from veriforge.core import planner_resources as resources, planner_rounds as rounds, planner_runtime as role, service, worker
from veriforge.core import runtime_configuration as config
from veriforge.core.catalog import seed_catalog
from veriforge.core.database import Attempt, PlannerState, make_engine, session_factory
from veriforge.core.planner_experiments import resolve_attempt_experiment
from veriforge.planner.models import InvestigationLimits, ProviderCompletion
from veriforge.planner.provider import MetaConfig

AT = datetime(2026, 10, 30, tzinfo=timezone.utc)


@pytest.fixture(autouse=True)
def disabled_environment_and_no_provider(monkeypatch):
    monkeypatch.delenv(config.ENABLED_VARIABLE, raising=False)
    monkeypatch.delenv(config.CONTROL_VARIABLE, raising=False)
    def forbidden(*args, **kwargs): pytest.fail("role wiring constructed a provider")
    monkeypatch.setattr(MetaConfig, "from_env", forbidden)


@pytest.fixture
def db(tmp_path):
    engine = make_engine("sqlite:///" + (tmp_path / "roles.db").as_posix())
    alembic = Config("alembic.ini")
    with engine.begin() as connection:
        alembic.attributes["connection"] = connection
        command.upgrade(alembic, "head")
    sessions = session_factory(engine)
    with sessions.begin() as session: seed_catalog(session)
    yield sessions
    engine.dispose()


def clock(): return AT + timedelta(milliseconds=300)


def recorded(record):
    return AttemptResult(attempt_id="synthetic-role-test", experiment_id=record.wire_id, execution_status="completed",
        verdict="pass_within_scope", identity={**record.identity, "environment_hash": "b" * 64}, usage={"cycles": 1},
        uncertainties=("Synthetic fixture; no simulator/model executed",))


def prepare(sessions, key="seed", *, planner=True, make_child=True):
    with sessions.begin() as session:
        exp = Experiment(experiment_id=key, fixture_id="fifo_correct", objective_ids=("FIFO_BEHAVIOR",),
            operations=({"op": "reset"},), budget={"max_cycles": 8, "wall_time_seconds": 2})
        parent = service.create_campaign(session, "fifo_correct", exp.model_dump(mode="json"), key,
            completion_policy="sequential" if planner else "single")
        service.start(session, parent.id)
        attempt = session.scalar(select(Attempt).where(Attempt.campaign_id == parent.id))
        if not planner: return parent.id, attempt.id
        token, _, _ = service.claim(session, attempt.id)
        service.finalize(session, attempt.id, token, recorded(resolve_attempt_experiment(session, attempt)).model_dump(mode="json"))
        rounds.attach_planner(session, parent.id, at=AT, requested_model="fake-role-model", strategy="meta",
            limits=InvestigationLimits(max_total_cycles=80, max_cycles_per_experiment=8, max_wall_seconds=60,
                max_execution_seconds=2, max_model_seconds=2))
        campaign_id = parent.id
    if not make_child: return campaign_id, None
    with sessions.begin() as session:
        round_id = rounds.advance(session, campaign_id, at=AT).id
    with sessions.begin() as session:
        grant = resources.claim_provider_slot(session, round_id, incarnation=resources.new_incarnation(),
            role=resources.ROLE, at=AT + timedelta(milliseconds=100), control="RUNNING")
    with sessions.begin() as session:
        assert resources.validate_provider_slot(session, grant, role=resources.ROLE, at=AT + timedelta(milliseconds=200), control="RUNNING", phase="completion")
        completion = ProviderCompletion(model="fake-role-model", usage={"input_tokens": 1, "output_tokens": 1}, finish_reason="stop",
            content=json.dumps({"proposal": {"kind": "experiment", "seed": 4, "objective_ids": ["FIFO_BEHAVIOR"],
                "operations": [{"op": "reset"}, {"op": "write", "value": 5}, {"op": "read"}]}}))
        assert rounds.finish_model_round(session, grant.lease, completion, at=AT + timedelta(milliseconds=200))
        attempt_id = session.scalar(select(Attempt.id).where(Attempt.campaign_id == campaign_id, Attempt.execution_status == "queued"))
    with sessions.begin() as session:
        # No local provider task was created by this explicit typed fixture.
        assert cleanup.acknowledge_settled(session, grant.cleanup_ticket, proof="cooperative_tasks_settled", at=clock())
    driver.release_settled_resource(sessions, now=clock)
    return campaign_id, attempt_id


class Broker:
    def __init__(self): self.attempts, self.planner = [], []
    def enqueue(self, value): self.attempts.append(value)
    def enqueue_planner(self, value): self.planner.append(value)


def fake_runner(sessions, attempt_id, calls, effect=None):
    def run(experiment, output, *, cancel_event):
        calls.append(experiment.experiment_id)
        if effect: effect(cancel_event)
        with sessions() as session:
            return recorded(resolve_attempt_experiment(session, session.get(Attempt, attempt_id)))
    return run


def enabled_file(tmp_path, value="RUNNING"):
    path = tmp_path / "control.json"
    path.write_text(json.dumps({"control": value}))
    return path, config.RuntimeConfiguration(enabled=True, control_path=path)


def test_actual_dispatcher_uses_fixed_runtime_target_and_id_only_fake_rq(monkeypatch):
    import redis
    import rq
    queues = {}
    connection = object()
    monkeypatch.setattr(redis.Redis, "from_url", lambda value: connection)
    class Queue:
        def __init__(self, name, *, connection):
            self.name, self.connection, self.calls = name, connection, []
            queues[name] = self
        def enqueue(self, target, *args, **kwargs): self.calls.append((target, args, kwargs))
    monkeypatch.setattr(rq, "Queue", Queue)
    dispatcher = dispatch.RQDispatcher(redis_url="redis://explicit-fake")
    identifier = resources.new_incarnation()
    dispatcher.enqueue_planner(identifier)
    dispatcher.enqueue(identifier)
    dispatcher.enqueue_export(identifier)
    target, args, metadata = queues[role.QUEUE].calls[0]
    assert target == role.TARGET and args == (identifier,)
    assert metadata == {"job_timeout": 180, "result_ttl": 0}
    assert role.allowed_job(SimpleNamespace(func_name=target, args=args, kwargs={}), queues[role.QUEUE])
    assert queues["veriforge"].calls == [
        ("veriforge.core.worker.execute_attempt", (identifier,), {"job_timeout": 3720, "result_ttl": 0}),
        ("veriforge.core.artifacts.execute_export", (identifier,), {"job_timeout": 60, "result_ttl": 0})]


def test_config_reads_only_two_explicit_names_and_never_credentials(tmp_path):
    path, _ = enabled_file(tmp_path)
    names = []
    class Mapping:
        def get(self, name):
            names.append(name)
            assert name in {config.ENABLED_VARIABLE, config.CONTROL_VARIABLE}
            return {config.ENABLED_VARIABLE: "1", config.CONTROL_VARIABLE: str(path)}[name]
    settings = config.RuntimeConfiguration.from_mapping(Mapping())
    assert settings.enabled and settings.control() == "RUNNING"
    assert names == [config.ENABLED_VARIABLE, config.CONTROL_VARIABLE]


@pytest.mark.parametrize("values", [{}, {config.ENABLED_VARIABLE: "true"},
    {config.ENABLED_VARIABLE: "1"}, {config.ENABLED_VARIABLE: "1", config.CONTROL_VARIABLE: "relative.json"},
    {config.ENABLED_VARIABLE: "1", config.CONTROL_VARIABLE: 123}])
def test_invalid_or_incomplete_config_never_authorizes(values):
    assert config.RuntimeConfiguration.from_mapping(values).control() == "STOPPED"


def test_control_reader_is_uncached_and_disabled_config_does_not_read(tmp_path, monkeypatch):
    path, settings = enabled_file(tmp_path)
    assert settings.control() == "RUNNING"
    replacement = tmp_path / "replacement.json"
    replacement.write_text('{"control":"PAUSED"}')
    os.replace(replacement, path)
    assert settings.control() == "STOPPED"
    path.write_text('{"control":"RUNNING"}')
    assert settings.control() == "RUNNING"
    def forbidden(*args): pytest.fail("disabled config read a control file")
    monkeypatch.setattr(config.ControlReader, "__call__", forbidden)
    assert config.RuntimeConfiguration(enabled=False, control_path=path).control() == "STOPPED"


@pytest.mark.parametrize("mode", ["disabled", "missing-path", "missing-file", "unreadable", "paused", "malformed", "running"])
def test_planner_dispatch_and_execution_fail_closed_for_role_config(db, tmp_path, monkeypatch, mode):
    campaign, attempt_id = prepare(db)
    path, settings = enabled_file(tmp_path)
    if mode == "disabled": settings = config.RuntimeConfiguration(control_path=path)
    elif mode == "missing-path": settings = config.RuntimeConfiguration(enabled=True)
    elif mode == "missing-file": path.unlink()
    elif mode == "unreadable":
        actual = os.open
        def denied(target, *args, **kwargs):
            if Path(target) == path: raise PermissionError("synthetic control permission")
            return actual(target, *args, **kwargs)
        monkeypatch.setattr(os, "open", denied)
    elif mode == "paused": path.write_text('{"control":"PAUSED"}')
    elif mode == "malformed": path.write_text("{")
    broker, calls = Broker(), []
    dispatched = dispatch.dispatch_pending(db, broker, clock=clock, configuration=settings)
    result = worker.execute_attempt(attempt_id, sessions=db, runner=fake_runner(db, attempt_id, calls),
        artifact_root=tmp_path, now=clock, configuration=settings)
    assert dispatched["sent"] == int(mode == "running")
    assert result["accepted"] == (mode == "running")
    assert len(calls) == int(mode == "running")


def test_unconfigured_queued_planner_job_never_uses_canonical_cwd_fallback(db, tmp_path):
    _, attempt_id = prepare(db)
    calls, broker = [], Broker()
    assert dispatch.dispatch_pending(db, broker, clock=clock)["sent"] == 0
    assert not worker.execute_attempt(attempt_id, sessions=db, runner=fake_runner(db, attempt_id, calls),
        artifact_root=tmp_path, now=clock)["accepted"]
    assert not calls


@pytest.mark.parametrize("value", ["PAUSED", "malformed", None])
def test_production_reconciler_stops_planner_but_retains_manual_dispatch(db, tmp_path, value):
    campaign, _ = prepare(db, make_child=False)
    _, manual_id = prepare(db, "manual", planner=False)
    path, settings = enabled_file(tmp_path, value or "RUNNING")
    if value == "malformed": path.write_text("{")
    elif value is None: settings = config.RuntimeConfiguration()
    broker = Broker()
    result = worker.reconcile_once(sessions=db, dispatcher=broker, now=clock, configuration=settings)
    assert result["planner"]["sent"] == 0 and not broker.planner
    assert broker.attempts == [manual_id]
    with db() as session: assert session.get(PlannerState, campaign).stop_reason == "paused"


def test_enabled_environment_profile_is_resolved_only_when_service_called(db, tmp_path, monkeypatch):
    _, _ = prepare(db, make_child=False)
    path, _ = enabled_file(tmp_path)
    monkeypatch.setenv(config.ENABLED_VARIABLE, "1")
    monkeypatch.setenv(config.CONTROL_VARIABLE, str(path))
    broker = Broker()
    assert worker.reconcile_once(sessions=db, dispatcher=broker, now=clock)["planner"]["sent"] == 1


def test_manual_default_ignores_planner_pause_while_explicit_manual_control_applies(db, tmp_path):
    _, manual_id = prepare(db, planner=False)
    _, settings = enabled_file(tmp_path, "PAUSED")
    broker, calls = Broker(), []
    assert dispatch.dispatch_pending(db, broker, clock=clock, configuration=settings)["sent"] == 1
    assert worker.execute_attempt(manual_id, sessions=db, runner=fake_runner(db, manual_id, calls), artifact_root=tmp_path,
        now=clock, configuration=settings)["accepted"]
    _, other_id = prepare(db, "explicit", planner=False)
    assert dispatch.dispatch_pending(db, Broker(), clock=clock, control=lambda: "PAUSED")["sent"] == 0
    assert not worker.execute_attempt(other_id, sessions=db, runner=fake_runner(db, other_id, calls), artifact_root=tmp_path,
        now=clock, control=lambda: "PAUSED")["accepted"]
    assert len(calls) == 1


def test_file_changes_after_enqueue_are_rechecked_at_actual_claim(db, tmp_path):
    _, attempt_id = prepare(db)
    path, settings = enabled_file(tmp_path)
    assert dispatch.dispatch_pending(db, Broker(), clock=clock, configuration=settings)["sent"] == 1
    path.write_text('{"control":"PAUSED"}')
    calls = []
    assert not worker.execute_attempt(attempt_id, sessions=db, runner=fake_runner(db, attempt_id, calls), artifact_root=tmp_path,
        now=clock, configuration=settings)["accepted"]
    assert not calls


@pytest.mark.parametrize("planner", [False, True])
def test_observed_stop_is_latched_and_late_running_cannot_publish(db, tmp_path, planner):
    campaign, attempt_id = prepare(db, planner=planner)
    path, settings = enabled_file(tmp_path)
    def toggle(token):
        path.write_text('{"control":"PAUSED"}')
        assert token.is_set()
        path.write_text('{"control":"RUNNING"}')
        assert token.is_set()
    calls = []
    kwargs = {"configuration": settings} if planner else {"control": settings.control}
    result = worker.execute_attempt(attempt_id, sessions=db, runner=fake_runner(db, attempt_id, calls, toggle),
        artifact_root=tmp_path, now=clock, **kwargs)
    assert not result["accepted"] and len(calls) == 1
    with db() as session:
        assert session.get(Attempt, attempt_id).result is None


def test_explicit_injected_control_is_deterministic_and_does_not_require_env(db, tmp_path):
    _, attempt_id = prepare(db)
    calls = []
    assert worker.execute_attempt(attempt_id, sessions=db, runner=fake_runner(db, attempt_id, calls), artifact_root=tmp_path,
        now=clock, control=lambda: "RUNNING")["accepted"]
    assert len(calls) == 1


def test_configuration_import_has_no_environment_reads():
    alias = "veriforge.core._configuration_import_probe"
    spec = importlib.util.spec_from_file_location(alias, config.__file__)
    module = importlib.util.module_from_spec(spec)
    sys.modules[alias] = module
    actual = os.environ
    class ForbiddenEnvironment:
        def __getattribute__(self, name): pytest.fail("configuration import read process environment")
    try:
        os.environ = ForbiddenEnvironment()
        spec.loader.exec_module(module)
    finally:
        os.environ = actual
        sys.modules.pop(alias, None)
    assert module.RuntimeConfiguration().control() == "STOPPED"
