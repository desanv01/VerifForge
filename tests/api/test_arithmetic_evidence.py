"""DB-authorized arithmetic evidence tests. All runner records are synthetic."""
import hashlib
import json
from io import BytesIO
from zipfile import ZipFile

from fastapi.testclient import TestClient
import pytest
from sqlalchemy import select

from veriforge.adapters import get_adapter
from veriforge.api.app import create_app
from veriforge.arithmetic import ArithmeticExperiment
from veriforge.contracts import ArtifactReference, AttemptResult
from veriforge.core import artifacts, service, worker
from veriforge.core.database import Attempt, Campaign


PLAN = {"schema_version": "arithmetic.v1", "experiment_id": "arithmetic-evidence-api", "fixture_id": "multiplier_correct", "operations": [{"op": "reset"}, {"op": "multiply", "a": 255, "b": 255}, {"op": "idle", "cycles": 2}]}


def write_synthetic(experiment, output, identity):
    """Hand-derived four-cycle trace, never a simulator or mathematical oracle call."""
    assert isinstance(experiment, ArithmeticExperiment)
    output.mkdir(parents=True)
    trace = {"schema_version": "arithmetic.trace.v1", "expected": [
        {"cycle": 0, "operation_index": 0, "reset": True, "valid": False, "product": 0},
        {"cycle": 1, "operation_index": 1, "reset": False, "valid": False, "product": None},
        {"cycle": 2, "operation_index": 2, "reset": False, "valid": False, "product": None},
        {"cycle": 3, "operation_index": 2, "reset": False, "valid": True, "product": 65025}],
        "observed": [{"cycle": n, "valid": n == 3, "product": 65025 if n == 3 else 0} for n in range(4)]}
    descriptor = get_adapter("rtl.arithmetic.v1")
    files = {"experiment.json": experiment.model_dump_json().encode(), "stimulus.json": json.dumps(descriptor.expand_stimulus(experiment)).encode(),
             "arithmetic-trace.json": json.dumps(trace).encode(), "multiplier.sv": b"// Synthetic API fixture, no HDL execution\n",
             "fifo.sv": b"// Wrong family artifact deliberately excluded\n", "execution.log": b"Synthetic private diagnostics"}
    refs = []
    for index, (name, data) in enumerate(files.items()):
        (output / name).write_bytes(data)
        refs.append(ArtifactReference(artifact_id=f"ref-{index}", path=name, sha256=hashlib.sha256(data).hexdigest(), size_bytes=len(data), media_type="text/plain"))
    result = AttemptResult(attempt_id="synthetic-adapter-id", experiment_id=experiment.experiment_id, execution_status="completed", verdict="pass_within_scope",
        identity={**identity, "environment_hash": "a" * 64}, artifacts=tuple(refs), usage={"cycles": 4}, uncertainties=("Synthetic API records; no simulator executed",))
    (output / "result.json").write_text(result.model_dump_json())
    return result


@pytest.fixture
def state(db, tmp_path):
    root = tmp_path / "evidence"
    experiment = ArithmeticExperiment.model_validate(PLAN)
    with db[1].begin() as session:
        campaign = service.create_campaign(session, experiment.fixture_id, experiment.model_dump(mode="json"), "arithmetic-original", adapter_id="rtl.arithmetic.v1")
        service.start(session, campaign.id)
        session.flush()
        attempt = session.scalar(select(Attempt).where(Attempt.campaign_id == campaign.id))
        lease, _, _ = service.claim(session, attempt.id)
        path = root / attempt.id
        result = write_synthetic(experiment, path, campaign.identity)
        assert service.finalize(session, attempt.id, lease, result.model_dump(mode="json"))
        campaign_id, attempt_id, identity = campaign.id, attempt.id, campaign.identity
    with TestClient(create_app(db[0], artifact_root=root)) as client:
        yield dict(client=client, sessions=db[1], path=path, root=root, attempt_id=attempt_id, campaign_id=campaign_id, result=result, identity=identity)


def test_arithmetic_artifact_download_and_async_bundle(state):
    client = state["client"]
    base = f"/api/attempts/{state['attempt_id']}"
    listing = client.get(base + "/artifacts")
    assert listing.status_code == 200, listing.text
    eligible = {item["path"] for item in listing.json()["items"] if item["downloadable"]}
    assert eligible == {"stimulus.json", "arithmetic-trace.json", "multiplier.sv"}
    raw = client.get(base + "/artifacts/ref-2/download")
    assert raw.status_code == 200 and raw.content == (state["path"] / "arithmetic-trace.json").read_bytes()
    assert client.get(base + "/artifacts/ref-4/download").status_code == 404
    requested = client.post(base + "/exports", headers={"Idempotency-Key": "arith-export"})
    assert requested.status_code == 202
    job = requested.json()
    assert artifacts.execute_export(job["id"], sessions=state["sessions"], root=state["root"])["accepted"]
    response = client.get(f"/api/exports/{job['id']}/download")
    assert response.status_code == 200, response.text
    with ZipFile(BytesIO(response.content)) as bundle:
        manifest = json.loads(bundle.read("manifest.json"))
        assert manifest["adapter_id"] == "rtl.arithmetic.v1" and manifest["trace"] == []
        assert manifest["family_trace"] == {"schema_version": "arithmetic.trace.v1", "artifact": "artifacts/arithmetic-trace.json"}
        assert "artifacts/multiplier.sv" in bundle.namelist() and "artifacts/fifo.sv" not in bundle.namelist()


def test_arithmetic_replay_routes_exact_plan_and_fresh_output(state):
    client = state["client"]
    before = client.get(f"/api/campaigns/{state['campaign_id']}").json()
    replay = client.post(f"/api/attempts/{state['attempt_id']}/replay", headers={"Idempotency-Key": "arithmetic-replay"})
    assert replay.status_code == 202, replay.text
    queued = replay.json()
    assert queued["adapter_id"] == "rtl.arithmetic.v1" and queued["plan"] == before["plan"] and queued["identity"] == before["identity"]
    def synthetic_runner(experiment, output, *, cancel_event):
        return write_synthetic(experiment, output, state["identity"])
    assert worker.execute_attempt(queued["attempts"][0]["id"], sessions=state["sessions"], runner=synthetic_runner, artifact_root=state["root"])["accepted"]
    finished = client.get(f"/api/campaigns/{queued['id']}").json()
    assert finished["execution_status"] == "completed" and finished["verdict"] == "pass_within_scope"
    assert finished["attempts"][0]["result"]["trace"] == []
    assert client.get(f"/api/campaigns/{before['id']}").json() == before


@pytest.mark.parametrize("mutation", ["empty", "boolean_as_integer"])
def test_rehashed_arithmetic_stimulus_cannot_change_replay(state, mutation):
    stimulus = json.loads((state["path"] / "stimulus.json").read_bytes())
    if mutation == "empty":
        stimulus = []
    else:
        stimulus[0]["reset"] = 1
    data = json.dumps(stimulus).encode()
    payload = state["result"].model_dump(mode="json")
    for ref in payload["artifacts"]:
        if ref["path"] == "stimulus.json":
            ref.update(sha256=hashlib.sha256(data).hexdigest(), size_bytes=len(data))
    (state["path"] / "stimulus.json").write_bytes(data)
    (state["path"] / "result.json").write_text(json.dumps(payload))
    with state["sessions"].begin() as session:
        session.get(Attempt, state["attempt_id"]).result = payload
    response = state["client"].post(f"/api/attempts/{state['attempt_id']}/replay", headers={"Idempotency-Key": "changed-stimulus"})
    assert response.status_code == 409


def test_rehashed_arithmetic_sidecar_structure_rejects_before_export(state):
    raw = json.loads((state["path"] / "arithmetic-trace.json").read_bytes())
    raw["expected"][0]["reset"] = 1
    data = json.dumps(raw).encode()
    payload = state["result"].model_dump(mode="json")
    for ref in payload["artifacts"]:
        if ref["path"] == "arithmetic-trace.json": ref.update(sha256=hashlib.sha256(data).hexdigest(), size_bytes=len(data))
    (state["path"] / "arithmetic-trace.json").write_bytes(data)
    (state["path"] / "result.json").write_text(json.dumps(payload))
    with state["sessions"].begin() as session: session.get(Attempt, state["attempt_id"]).result = payload
    assert state["client"].get(f"/api/attempts/{state['attempt_id']}/artifacts").status_code == 409
