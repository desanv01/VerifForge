"""Native AXI API evidence admission; synthetic records, no HDL or live API.

The package import must match this test checkout. Frozen-context runs set an
explicit PYTHONPATH so an editable installation cannot substitute other source.
"""
import hashlib
import json
from io import BytesIO
from pathlib import Path
from zipfile import ZipFile

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from veriforge.adapters import get_adapter
from veriforge.api.app import create_app
from veriforge.axi_lite import contracts, oracle
from veriforge.axi_lite.contracts import AxiLiteExperiment
from veriforge.contracts import ArtifactReference, AttemptResult, EvidenceIdentity, FailureSignature, ToolVersion
from veriforge.core import artifacts, service, worker
from veriforge.core.catalog import seed_catalog
from veriforge.core.database import Attempt, Base, make_engine, session_factory

CONTEXT = Path(__file__).resolve().parents[2]
ADAPTER = "rtl.axi_lite.v1"


def sha(raw):
    return hashlib.sha256(raw).hexdigest()


def encode(data):
    return json.dumps(data, sort_keys=True, separators=(",", ":")).encode()


def plan(failing=False):
    return AxiLiteExperiment(experiment_id="isolated-axi-api-review",
        fixture_id="axil_aw_w_order_bug" if failing else "axil_registers_correct",
        cycles=({"reset_n": False}, {}, {}))


def synthetic(experiment, output, *, cancel_event=None):
    """Handwritten bus rows checked by packaged oracle; no simulator observations."""
    assert isinstance(experiment, AxiLiteExperiment)
    output = Path(output)
    output.mkdir(parents=True)
    observed = [dict(cycle=i, aw_ready=i != 0, w_ready=i != 0, b_valid=False, b_resp=0,
                     ar_ready=i != 0, r_valid=False, r_data=0, r_resp=0) for i in range(3)]
    if experiment.fixture_id != "axil_registers_correct": observed[2]["b_valid"] = True
    native = {"schema_version": "axi_lite.trace.v1", "observations": observed}
    checked = oracle.check(experiment, native)
    descriptor = get_adapter(ADAPTER)
    versions = (ToolVersion(name="synthetic-api-review-no-hdl", version="1"),)
    environment = encode({"contracts_hash": sha(Path(contracts.__file__).read_bytes()),
                          "tools": [version.model_dump(mode="json") for version in versions]})
    source = Path(CONTEXT) / "targets/axi_lite/registers.sv"
    files = {"experiment.json": experiment.model_dump_json().encode(),
        "stimulus.json": encode(descriptor.expand_stimulus(experiment)), "observed.json": encode(native),
        "check.json": checked.model_dump_json().encode(), "contract.json": descriptor.contract_for(experiment).model_dump_json().encode(),
        "checker.py": Path(oracle.__file__).read_bytes(), "checker-contracts.py": Path(contracts.__file__).read_bytes(),
        "registers.sv": source.read_bytes(), "environment.json": environment,
        "fifo.sv": b"// Wrong-family decoy; must not be portable\n", "execution.log": b"private diagnostics"}
    refs = []
    for name, raw in files.items():
        (output / name).write_bytes(raw)
        refs.append(ArtifactReference(artifact_id="review-"+str(len(refs)), path=name,
                    sha256=sha(raw), size_bytes=len(raw), media_type="text/plain"))
    identity = EvidenceIdentity(**descriptor.expected_identity(experiment), environment_hash=sha(environment))
    failure = checked.failure
    signature = FailureSignature(requirement_id=failure.requirement_id, failure_class=failure.failure_class,
        first_cycle=failure.cycle, expected=failure.expected, observed=failure.observed,
        signature_hash=sha(encode({"contract": identity.contract_hash, "checker": identity.checker_hash,
            "requirement": failure.requirement_id, "class": failure.failure_class}))) if failure else None
    result = AttemptResult(attempt_id=output.name, experiment_id=experiment.experiment_id, execution_status="completed",
        verdict=checked.verdict, identity=identity, failure_signature=signature, artifacts=tuple(refs),
        tool_versions=versions, usage={"cycles": len(observed)}, uncertainties=("Synthetic API fixture, no HDL execution",))
    (output / "result.json").write_text(result.model_dump_json())
    return result


@pytest.fixture
def state(tmp_path):
    import veriforge.api.app as api_module
    import veriforge.evidence.families as family_module
    for module in (artifacts, service, worker, api_module, family_module, contracts, oracle):
        assert Path(module.__file__).resolve().is_relative_to(Path(CONTEXT).resolve() / "src"), module.__file__
    engine = make_engine("sqlite:///" + (tmp_path / "review.db").as_posix())
    Base.metadata.create_all(engine)
    sessions = session_factory(engine)
    with sessions.begin() as session: seed_catalog(session)
    root = tmp_path / "evidence"
    with TestClient(create_app(engine, artifact_root=root)) as client:
        yield dict(client=client, sessions=sessions, root=root)
    engine.dispose()


def completed(state, failing=False):
    client, experiment = state["client"], plan(failing)
    response = client.post("/api/campaigns", headers={"Idempotency-Key": "create-"+str(failing)},
        json={"adapter_id": ADAPTER, "project_id": experiment.fixture_id, "plan": experiment.model_dump(mode="json")})
    assert response.status_code == 201, response.text
    campaign = response.json()
    response = client.post(f"/api/campaigns/{campaign['id']}/start", headers={"Idempotency-Key": "start"})
    assert response.status_code == 200, response.text
    attempt_id = response.json()["attempts"][0]["id"]
    assert worker.execute_attempt(attempt_id, sessions=state["sessions"], artifact_root=state["root"], runner=synthetic) == {"accepted": True}
    directory = state["root"] / attempt_id
    return campaign, attempt_id, directory


def rewrite(state, attempt_id, directory, name, raw):
    payload = json.loads((directory / "result.json").read_bytes())
    if name:
        (directory / name).write_bytes(raw)
        for ref in payload["artifacts"]:
            if ref["path"] == name: ref.update(sha256=sha(raw), size_bytes=len(raw))
    else:
        payload.update(raw)
    (directory / "result.json").write_bytes(encode(payload))
    with state["sessions"].begin() as session: session.get(Attempt, attempt_id).result = payload


def request_export(state, attempt_id, key="export"):
    response = state["client"].post(f"/api/attempts/{attempt_id}/exports", headers={"Idempotency-Key": key})
    assert response.status_code == 202, response.text
    return response.json()


def test_single_missing_admission_gate_and_health_are_coherent(state, monkeypatch):
    _, attempt_id, _ = completed(state)
    base = f"/api/attempts/{attempt_id}"
    proposed = artifacts.EVIDENCE_ADAPTERS
    assert proposed == {"rtl.fifo.v1", "rtl.arithmetic.v1", ADAPTER}
    monkeypatch.setattr(artifacts, "EVIDENCE_ADAPTERS", proposed - {ADAPTER})
    assert ADAPTER not in state["client"].get("/api/health").json()["evidence_adapters"]
    assert state["client"].get(base+"/artifacts").status_code == 409
    for action in ("replay", "exports"):
        assert state["client"].post(base+"/"+action, headers={"Idempotency-Key": action}).status_code == 409
    monkeypatch.setattr(artifacts, "EVIDENCE_ADAPTERS", proposed)
    assert ADAPTER in state["client"].get("/api/health").json()["evidence_adapters"]
    assert state["client"].get(base+"/artifacts").status_code == 200


@pytest.mark.parametrize("failing", [False, True])
def test_native_list_download_and_export_preserve_axi_and_privacy(state, failing):
    _, attempt_id, directory = completed(state, failing)
    client, base = state["client"], f"/api/attempts/{attempt_id}"
    response = client.get(base+"/artifacts")
    assert response.status_code == 200, response.text
    items = response.json()["items"]
    expected = set(get_adapter(ADAPTER).portable_artifacts) & {path.name for path in directory.iterdir()}
    assert {item["path"] for item in items if item["downloadable"]} == expected
    for item in items:
        response = client.get(base+f"/artifacts/{item['artifact_id']}/download")
        if item["downloadable"]:
            assert response.status_code == 200 and response.content == (directory / item["path"]).read_bytes()
            assert sha(response.content) == item["sha256"] and response.headers["cache-control"] == "no-store"
        else: assert response.status_code == 404
    job = request_export(state, attempt_id)
    assert request_export(state, attempt_id) == job
    assert artifacts.execute_export(job["id"], sessions=state["sessions"], root=state["root"]) == {"accepted": True}
    response = client.get(f"/api/exports/{job['id']}/download")
    assert response.status_code == 200, response.text
    with ZipFile(BytesIO(response.content)) as bundle:
        manifest = json.loads(bundle.read("manifest.json"))
        assert manifest["adapter_id"] == ADAPTER and manifest["trace"] == []
        assert manifest["family_trace"] == {"schema_version": "axi_lite.trace.v1", "artifact": "artifacts/observed.json"}
        assert set(bundle.namelist()) == {"manifest.json", "experiment.json", "REPORT.txt"} | {"artifacts/"+name for name in expected}
        assert not {"artifacts/fifo.sv", "artifacts/environment.json", "artifacts/execution.log"} & set(bundle.namelist())
        for name in expected: assert bundle.read("artifacts/"+name) == (directory / name).read_bytes()


@pytest.mark.parametrize("failing", [False, True])
def test_exact_api_replay_dispatch_keeps_native_plan_and_saved_stimulus(state, monkeypatch, failing):
    _, attempt_id, directory = completed(state, failing)
    client = state["client"]
    before = {path.name: sha(path.read_bytes()) for path in directory.iterdir()}
    response = client.post(f"/api/attempts/{attempt_id}/replay", headers={"Idempotency-Key": "replay"})
    assert response.status_code == 202, response.text
    queued = response.json()
    assert queued["adapter_id"] == ADAPTER and queued["plan"] == plan(failing).model_dump(mode="json")
    assert queued["trace_schema"] == "axi_lite.trace.v1" and queued["trace_artifact"] == "observed.json"
    calls = []
    def resolver(descriptor, mode):
        assert descriptor.adapter_id == ADAPTER and mode in ("native", "container")
        def stub(experiment, output, *, cancel_event):
            calls.append(experiment)
            assert experiment == plan(failing)
            return synthetic(experiment, output)
        return stub
    monkeypatch.setattr(type(get_adapter(ADAPTER)), "resolve_runner", resolver)
    replay_id = queued["attempts"][0]["id"]
    assert worker.execute_attempt(replay_id, sessions=state["sessions"], artifact_root=state["root"]) == {"accepted": True}
    finished = client.get(f"/api/campaigns/{queued['id']}").json()
    assert len(calls) == 1 and finished["execution_status"] == "completed"
    result = finished["attempts"][0]["result"]
    assert result["trace"] == [] and result["verdict"] == ("property_failure" if failing else "pass_within_scope")
    assert before == {path.name: sha(path.read_bytes()) for path in directory.iterdir()}
    assert (state["root"] / replay_id / "stimulus.json").read_bytes() == (directory / "stimulus.json").read_bytes()


@pytest.mark.parametrize("mutation", ["trace_schema", "fifo_trace", "boolean", "missing_companion", "changed_checker", "stimulus", "checked_scope"])
def test_rehashed_corrupt_native_evidence_never_downloads_replays_or_exports(state, mutation):
    _, attempt_id, directory = completed(state)
    if mutation == "fifo_trace":
        rewrite(state, attempt_id, directory, None, {"trace": [{"cycle": 0, "operation_index": 0}]})
    elif mutation == "missing_companion":
        result = json.loads((directory / "result.json").read_bytes())
        rewrite(state, attempt_id, directory, None, {"artifacts": [ref for ref in result["artifacts"] if ref["path"] != "checker-contracts.py"]})
    elif mutation == "changed_checker": rewrite(state, attempt_id, directory, "checker.py", b"raise AssertionError('must never execute')")
    elif mutation == "stimulus": rewrite(state, attempt_id, directory, "stimulus.json", b"[]")
    elif mutation == "checked_scope":
        checked = json.loads((directory / "check.json").read_bytes()); checked["checked_cycles"] = 1
        rewrite(state, attempt_id, directory, "check.json", encode(checked))
    else:
        native = json.loads((directory / "observed.json").read_bytes())
        if mutation == "trace_schema": native["schema_version"] = "arithmetic.trace.v1"
        else: native["observations"][0]["b_valid"] = 0
        rewrite(state, attempt_id, directory, "observed.json", encode(native))
    client, base = state["client"], f"/api/attempts/{attempt_id}"
    assert client.get(base+"/artifacts").status_code == 409
    assert client.get(base+"/artifacts/review-2/download").status_code == 409
    assert client.post(base+"/replay", headers={"Idempotency-Key": "bad"}).status_code == 409
    job = request_export(state, attempt_id)
    assert artifacts.execute_export(job["id"], sessions=state["sessions"], root=state["root"])["failed"]
    assert client.get(f"/api/exports/{job['id']}/download").status_code == 409


def test_unchecked_completed_records_are_explicitly_not_portable(state):
    _, attempt_id, directory = completed(state)
    rewrite(state, attempt_id, directory, None, {"identity": None, "verdict": "inconclusive"})
    client, base = state["client"], f"/api/attempts/{attempt_id}"
    response = client.get(base+"/artifacts")
    assert response.status_code == 200 and not any(item["downloadable"] for item in response.json()["items"])
    assert client.get(base+"/artifacts/review-2/download").status_code == 409
    for action in ("replay", "exports"):
        assert client.post(base+"/"+action, headers={"Idempotency-Key": action}).status_code == 409


def test_installed_identity_change_blocks_exact_replay(state, monkeypatch):
    _, attempt_id, _ = completed(state)
    monkeypatch.setattr(worker, "installed_identity", lambda experiment: {})
    response = state["client"].post(f"/api/attempts/{attempt_id}/replay", headers={"Idempotency-Key": "changed-installation"})
    assert response.status_code == 409
