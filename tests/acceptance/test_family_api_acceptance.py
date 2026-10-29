"""Independent family boundary checks; isolated SQLite, no HTTP/runner/tool jobs."""
import hashlib
import json
from pathlib import Path

import pytest
from sqlalchemy import select

from veriforge.adapters import get_adapter
from veriforge.api.app import CreateCampaign
from veriforge.core import artifacts, service, worker
from veriforge.core.catalog import seed_catalog
from veriforge.core.database import Attempt, Base, Campaign, Finding, make_engine, session_factory


@pytest.fixture
def store():
    engine = make_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    sessions = session_factory(engine)
    with sessions.begin() as session:
        seed_catalog(session)
    yield sessions
    engine.dispose()


def payload(adapter):
    if adapter == "rtl.fifo.v1":
        return dict(experiment_id="review-fifo", fixture_id="fifo_correct", contract_id="fifo-v1",
                    objective_ids=["FIFO_BEHAVIOR"], operations=[{"op": "reset"}, {"op": "write", "value": 7}])
    return dict(schema_version="arithmetic.v1", experiment_id="review-arithmetic",
                fixture_id="multiplier_correct", contract_id="unsigned-multiplier-v1",
                parameters={"width": 32, "latency": 2},
                operations=[{"op": "reset"}, {"op": "multiply", "a": 2**32 - 1, "b": 2**32 - 1},
                            {"op": "idle", "cycles": 2}])


def test_legacy_fifo_retry_preserves_original_plan_and_pre_adapter_request_hash(store):
    # Reconstruct a genuine pre-adapter FIFO request hash instead of using the new creator.
    request = CreateCampaign.model_validate({"project_id": "fifo_correct", "plan": payload("rtl.fifo.v1")})
    plan = request.plan.model_dump(mode="json")
    old_hash = hashlib.sha256(json.dumps({"project_id": "fifo_correct", "plan": plan},
                                        sort_keys=True, separators=(",", ":")).encode()).hexdigest()
    with store.begin() as session:
        original = Campaign(project_id="fifo_correct", contract_id="fifo-v1", create_key="old-key",
                            request_hash=old_hash, plan=plan,
                            identity=get_adapter("rtl.fifo.v1").expected_identity(plan))
        session.add(original)
        session.flush()
        original_id = original.id
        saved = json.dumps(original.plan, sort_keys=True)
    with store.begin() as session:
        repeated = service.create_campaign(session, "fifo_correct", plan, "old-key", adapter_id="rtl.fifo.v1")
        assert repeated.id == original_id and repeated.request_hash == old_hash
        assert json.dumps(repeated.plan, sort_keys=True) == saved
        assert repeated.adapter_id == "rtl.fifo.v1"


@pytest.mark.parametrize("adapter", ["rtl.fifo.v1", "rtl.arithmetic.v1"])
def test_changed_installed_source_stops_worker_before_any_adapter_call(store, tmp_path, monkeypatch, adapter):
    descriptor = get_adapter(adapter)
    experiment = descriptor.validate_experiment(payload(adapter))
    with store.begin() as session:
        campaign = service.create_campaign(session, experiment.fixture_id, experiment.model_dump(mode="json"),
                                           "source-drift", adapter_id=adapter)
        service.start(session, campaign.id)
        session.flush()
        attempt_id = session.scalar(select(Attempt.id).where(Attempt.campaign_id == campaign.id))
    source = Path(__file__).resolve().parents[2] / "targets" / (
        "fifo/fifo.sv" if adapter == "rtl.fifo.v1" else "arithmetic/multiplier.sv")
    original_read = Path.read_bytes

    def source_drift(path):
        data = original_read(path)
        return data + b"\n// simulated installed revision change\n" if path.resolve() == source.resolve() else data

    monkeypatch.setattr(Path, "read_bytes", source_drift)
    calls = []

    def forbidden_runner(*args, **kwargs):
        calls.append((args, kwargs))
        raise AssertionError("Source mismatch must be rejected before dispatch")

    outcome = worker.execute_attempt(attempt_id, sessions=store, runner=forbidden_runner,
                                     artifact_root=tmp_path / "must-not-exist")
    assert outcome == {"accepted": True, "infrastructure_error": True}
    assert calls == [] and not (tmp_path / "must-not-exist").exists()
    with store() as session:
        attempt = session.get(Attempt, attempt_id)
        assert attempt.execution_status == "infrastructure_error" and attempt.verdict is None
        assert list(session.scalars(select(Finding))) == []


def test_arithmetic_snapshot_uses_nondefault_parameters_and_rejects_unfinalized_evidence(store, monkeypatch):
    descriptor = get_adapter("rtl.arithmetic.v1")
    experiment = descriptor.validate_experiment(payload(descriptor.adapter_id))
    with store.begin() as session:
        campaign = service.create_campaign(session, experiment.fixture_id, experiment.model_dump(mode="json"),
                                           "wide-arithmetic", adapter_id=descriptor.adapter_id)
        assert campaign.identity == descriptor.expected_identity(experiment)
        view = service.campaign_view(session, campaign)
        assert view["contract_snapshot"]["parameters"] == {"width": 32, "latency": 2}
        assert view["trace_schema"] == "arithmetic.trace.v1" and view["trace_artifact"] == "arithmetic-trace.json"
        service.start(session, campaign.id)
        session.flush()
        attempt_id = session.scalar(select(Attempt.id).where(Attempt.campaign_id == campaign.id))

        def forbidden_loader(*args, **kwargs):
            raise AssertionError("Unfinalized evidence must be rejected before any family loader")

        monkeypatch.setattr(artifacts, "load_attempt", forbidden_loader)
        with pytest.raises(service.Conflict, match="Only finalized completed evidence"):
            artifacts.artifact_list(session, attempt_id)
        with pytest.raises(service.Conflict, match="Only finalized completed evidence"):
            artifacts.replay_campaign(session, attempt_id, "blocked-replay")
        with pytest.raises(service.Conflict, match="Export requires finalized checked evidence"):
            artifacts.request_export(session, attempt_id, "blocked-export")
