"""Family routing tests use static descriptors and labeled stub outcomes only."""
from dataclasses import replace
import json

import pytest
from alembic import command
from alembic.config import Config
from fastapi.testclient import TestClient
from sqlalchemy import text

from veriforge.adapters import get_adapter
from veriforge.api.app import create_app
from veriforge.arithmetic import ArithmeticExperiment
from veriforge.contracts import AttemptResult, Experiment
from veriforge.core import service, worker
from veriforge.core.database import Attempt, Campaign, make_engine, session_factory

ARITHMETIC = {"schema_version": "arithmetic.v1", "experiment_id": "arithmetic-api", "fixture_id": "multiplier_correct", "parameters": {"width": 8, "latency": 2}, "operations": [{"op": "reset"}, {"op": "multiply", "a": 255, "b": 255}, {"op": "idle", "cycles": 2}]}


@pytest.fixture
def client(db):
    with TestClient(create_app(db[0])) as client:
        yield client


def create(client, key="family", plan=None, adapter="rtl.arithmetic.v1"):
    return client.post("/api/campaigns", json={"adapter_id": adapter, "project_id": (plan or ARITHMETIC)["fixture_id"], "plan": plan or ARITHMETIC}, headers={"Idempotency-Key": key})


def test_arithmetic_typed_create_and_catalog(client):
    response = create(client)
    assert response.status_code == 201, response.text
    campaign = response.json()
    assert campaign["adapter_id"] == "rtl.arithmetic.v1"
    assert campaign["plan"]["schema_version"] == "arithmetic.v1"
    assert campaign["contract_snapshot"]["schema_version"] == "arithmetic.contract.v1"
    assert campaign["contract_snapshot"]["parameters"]["latency"] == 2
    assert campaign["trace_artifact"] == "arithmetic-trace.json" and campaign["trace_schema"] == "arithmetic.trace.v1"
    expected = get_adapter("rtl.arithmetic.v1").expected_identity(ArithmeticExperiment.model_validate(ARITHMETIC))
    assert campaign["identity"] == expected
    assert create(client).json() == campaign
    assert create(client, plan={**ARITHMETIC, "seed": 2}).status_code == 409
    assert client.get(f"/api/campaigns/{campaign['id']}").json() == campaign
    projects = client.get("/api/projects").json()["items"]
    assert len([p for p in projects if p["adapter_id"] == "rtl.arithmetic.v1"]) == 3
    assert client.get("/api/contracts/unsigned-multiplier-v1").json()["schema_version"] == "arithmetic.contract.v1"


@pytest.mark.parametrize("mutation", ["missing_adapter", "missing_schema", "unknown_adapter", "fifo_adapter", "fifo_schema", "fifo_operation", "undrained", "overflow", "fixture"])
def test_family_boundary_rejects_without_creating(client, mutation):
    payload = {"adapter_id": "rtl.arithmetic.v1", "project_id": "multiplier_correct", "plan": json.loads(json.dumps(ARITHMETIC))}
    if mutation == "missing_adapter": payload.pop("adapter_id")
    elif mutation == "missing_schema": payload["plan"].pop("schema_version")
    elif mutation == "unknown_adapter": payload["adapter_id"] = "python.arbitrary"
    elif mutation == "fifo_adapter": payload["adapter_id"] = "rtl.fifo.v1"
    elif mutation == "fifo_schema": payload["plan"]["schema_version"] = "1"
    elif mutation == "fifo_operation": payload["plan"]["operations"][1] = {"op": "write", "value": 17}
    elif mutation == "undrained": payload["plan"]["operations"].pop()
    elif mutation == "overflow": payload["plan"]["operations"][1]["a"] = 256
    else: payload["project_id"] = "fifo_correct"
    assert client.post("/api/campaigns", json=payload, headers={"Idempotency-Key": mutation}).status_code == 422
    assert client.get("/api/campaigns").json()["items"] == []


def test_arithmetic_result_remains_common_outcome_with_distinct_trace(client, db, tmp_path):
    campaign = create(client).json()
    queued = client.post(f"/api/campaigns/{campaign['id']}/start", headers={"Idempotency-Key": "start"}).json()
    attempt_id = queued["attempts"][0]["id"]
    def test_adapter(experiment, output, *, cancel_event):
        assert isinstance(experiment, ArithmeticExperiment)
        return AttemptResult(attempt_id="synthetic-family-result", experiment_id=experiment.experiment_id, execution_status="completed", verdict="design_mismatch", identity={**campaign["identity"], "environment_hash": "a" * 64}, failure_signature={"requirement_id": "ARITH_PRODUCT", "failure_class": "arithmetic.product", "first_cycle": 3, "expected": "65025", "observed": "1", "signature_hash": "c" * 64}, artifacts=({"artifact_id": "arithmetic-trace", "path": "arithmetic-trace.json", "sha256": "d" * 64, "size_bytes": 10, "media_type": "application/json"},), uncertainties=("Synthetic test result; no HDL executed",))
    assert worker.execute_attempt(attempt_id, sessions=db[1], runner=test_adapter, artifact_root=tmp_path)["accepted"]
    result = client.get(f"/api/campaigns/{campaign['id']}").json()
    assert result["execution_status"] == "completed" and result["verdict"] == "design_mismatch"
    assert result["attempts"][0]["result"]["trace"] == []
    assert result["findings"][0]["result"]["failure_signature"]["requirement_id"] == "ARITH_PRODUCT"
    for suffix in ("artifacts", "artifacts/arithmetic-trace/download"):
        response = client.get(f"/api/attempts/{attempt_id}/{suffix}")
        assert response.status_code == 409 and "unsupported" not in response.json()["detail"]
    assert client.post(f"/api/attempts/{attempt_id}/replay", headers={"Idempotency-Key": "replay"}).status_code == 409
    # Export admission is asynchronous; the missing synthetic files will fail in
    # its worker, rather than being blocked by a family-unsupported guard.
    assert client.post(f"/api/attempts/{attempt_id}/exports", headers={"Idempotency-Key": "exports"}).status_code == 202


def test_registry_dispatch_preserves_token_and_never_invokes_fifo(client, db, monkeypatch):
    campaign = create(client).json()
    queued = client.post(f"/api/campaigns/{campaign['id']}/start", headers={"Idempotency-Key": "start"}).json()
    observed = []
    descriptor = get_adapter("rtl.arithmetic.v1")
    def stub(experiment, output, *, cancel_event):
        observed.append((experiment, cancel_event))
        return AttemptResult(attempt_id="stub", experiment_id=experiment.experiment_id, execution_status="completed", verdict="unsupported", uncertainties=("Stub dispatch test only",))
    fake = replace(descriptor, _native_runner=stub, _container_runner=stub)
    original_lookup = worker.get_adapter
    monkeypatch.setattr(worker, "get_adapter", lambda key: fake if key == fake.adapter_id else original_lookup(key))
    assert worker.execute_attempt(queued["attempts"][0]["id"], sessions=db[1])["accepted"]
    assert len(observed) == 1 and isinstance(observed[0][0], ArithmeticExperiment)
    assert callable(observed[0][1].is_set)


def test_arithmetic_finalization_rejects_fifo_trace_and_wrong_identity(client, db):
    campaign = create(client).json()
    queued = client.post(f"/api/campaigns/{campaign['id']}/start", headers={"Idempotency-Key": "start"}).json()
    attempt_id = queued["attempts"][0]["id"]
    with db[1].begin() as session:
        lease, _, _ = service.claim(session, attempt_id)
    result = AttemptResult(attempt_id="stub", experiment_id="arithmetic-api", execution_status="completed", verdict="pass_within_scope", identity={**campaign["identity"], "environment_hash": "a" * 64}, trace=({"cycle": 0, "operation_index": 0},)).model_dump(mode="json")
    with pytest.raises(ValueError, match="coerced"):
        with db[1].begin() as session: service.finalize(session, attempt_id, lease, result)
    result["trace"] = []
    result["identity"]["checker_hash"] = "0" * 64
    with pytest.raises(ValueError, match="identity differs"):
        with db[1].begin() as session: service.finalize(session, attempt_id, lease, result)


def test_additive_migration_preserves_legacy_fifo_snapshot(tmp_path):
    engine = make_engine("sqlite:///" + (tmp_path / "legacy.db").as_posix())
    config = Config("alembic.ini")
    old_plan = Experiment(experiment_id="old-fifo", fixture_id="fifo_correct", objective_ids=("FIFO_BEHAVIOR",), operations=({"op": "reset"},)).model_dump(mode="json")
    old_identity = get_adapter("rtl.fifo.v1").expected_identity(Experiment.model_validate(old_plan))
    with engine.begin() as connection:
        config.attributes["connection"] = connection
        command.upgrade(config, "6bf022evidence")
        connection.execute(text("INSERT INTO contracts(id,data,identity) VALUES (:id,:data,:identity)"), {"id": "fifo-v1", "data": get_adapter("rtl.fifo.v1").reviewed_contract().model_dump_json(), "identity": json.dumps(old_identity)})
        connection.execute(text("INSERT INTO projects(id,owner,data,contract_id) VALUES ('fifo_correct','local',:data,'fifo-v1')"), {"data": json.dumps({"id": "fifo_correct"})})
        connection.execute(text("INSERT INTO campaigns(id,owner,project_id,contract_id,create_key,request_hash,plan,identity,execution_status,version,event_sequence,created_at,updated_at) VALUES ('legacy','local','fifo_correct','fifo-v1','legacy','hash',:plan,:identity,'draft',0,0,CURRENT_TIMESTAMP,CURRENT_TIMESTAMP)"), {"plan": json.dumps(old_plan), "identity": json.dumps(old_identity)})
        command.upgrade(config, "head")
    with session_factory(engine)() as session:
        row = session.get(Campaign, "legacy")
        assert row.adapter_id == "rtl.fifo.v1" and row.plan == old_plan and row.identity == old_identity
        assert service.campaign_view(session, row)["contract_snapshot"]["target"] == "fifo"
    engine.dispose()
