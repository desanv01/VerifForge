"""Synthetic checked records test API authority; no simulator/model executes."""
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
import hashlib
from io import BytesIO
import json
from zipfile import ZipFile

from fastapi.testclient import TestClient
import pytest
from sqlalchemy import select

from veriforge.api.app import create_app
from veriforge.adapters import get_adapter
from veriforge.contracts import ArtifactReference, AttemptResult, Experiment
from veriforge.core import artifacts, service, worker
from veriforge.core.database import Attempt, Campaign, EvidenceExport, ReplayLink, now


PLAN = {"experiment_id": "evidence-test", "fixture_id": "fifo_correct", "objective_ids": ["FIFO_BEHAVIOR"], "operations": [{"op": "reset"}, {"op": "write", "value": 17}, {"op": "read"}]}


@pytest.fixture
def evidence_state(db, tmp_path):
    root = tmp_path / "evidence"
    experiment = Experiment.model_validate(PLAN)
    with db[1].begin() as session:
        campaign = service.create_campaign(session, experiment.fixture_id, experiment.model_dump(mode="json"), "original")
        service.start(session, campaign.id)
        session.flush()
        attempt = session.scalar(select(Attempt).where(Attempt.campaign_id == campaign.id))
        lease, _, _ = service.claim(session, attempt.id)
        directory = root / attempt.id
        directory.mkdir(parents=True)
        stimulus_bytes = json.dumps(get_adapter("rtl.fifo.v1").expand_stimulus(experiment)).encode()
        contents = {"experiment.json": experiment.model_dump_json(indent=2).encode(), "stimulus.json": stimulus_bytes, "execution.log": b"private local diagnostics"}
        refs = []
        for index, (name, data) in enumerate(contents.items()):
            (directory / name).write_bytes(data)
            refs.append(ArtifactReference(artifact_id="artifact-" + str(index), path=name, sha256=hashlib.sha256(data).hexdigest(), size_bytes=len(data), media_type="application/json" if name.endswith("json") else "text/plain"))
        result = AttemptResult(attempt_id="different-adapter-record-id", experiment_id=experiment.experiment_id, execution_status="completed", verdict="pass_within_scope", identity={**campaign.identity, "environment_hash": "b" * 64}, artifacts=tuple(refs), uncertainties=("Synthetic API test; no simulator executed",))
        (directory / "result.json").write_text(result.model_dump_json())
        assert service.finalize(session, attempt.id, lease, result.model_dump(mode="json"))
        campaign_id, attempt_id = campaign.id, attempt.id
    with TestClient(create_app(db[0], artifact_root=root)) as client:
        yield {"client": client, "root": root, "directory": directory, "attempt_id": attempt_id, "campaign_id": campaign_id, "result": result, "sessions": db[1], "stimulus_bytes": stimulus_bytes}


def request_replay(state, key="replay"):
    return state["client"].post(f"/api/attempts/{state['attempt_id']}/replay", headers={"Idempotency-Key": key})


def request_export(state, key="export"):
    return state["client"].post(f"/api/attempts/{state['attempt_id']}/exports", headers={"Idempotency-Key": key})


def test_authorized_artifact_mapping_and_portable_snapshot(evidence_state):
    state = evidence_state
    client, attempt_id = state["client"], state["attempt_id"]
    items = client.get(f"/api/attempts/{attempt_id}/artifacts").json()["items"]
    assert [item["downloadable"] for item in items] == [False, True, False]
    response = client.get(f"/api/attempts/{attempt_id}/artifacts/artifact-1/download")
    assert response.content == state["stimulus_bytes"]
    assert response.headers["x-content-type-options"] == "nosniff"
    assert "attachment" in response.headers["content-disposition"]
    for artifact_id in ("artifact-0", "artifact-2", "other-artifact"):
        assert client.get(f"/api/attempts/{attempt_id}/artifacts/{artifact_id}/download").status_code == 404
    assert client.get("/api/attempts/different-adapter-record-id/artifacts").status_code == 404
    assert client.get("/api/attempts/other-db-attempt/artifacts/artifact-1/download").status_code == 404


@pytest.mark.parametrize("tamper", ["artifact", "result", "experiment", "size"])
def test_tampered_evidence_never_downloads_or_replays(evidence_state, tamper):
    state = evidence_state
    if tamper == "artifact":
        (state["directory"] / "stimulus.json").write_bytes(b"changed")
    elif tamper == "result":
        payload = state["result"].model_dump(mode="json")
        payload["identity"]["source_hash"] = "0" * 64
        (state["directory"] / "result.json").write_text(json.dumps(payload))
    elif tamper == "experiment":
        (state["directory"] / "experiment.json").write_text(Experiment.model_validate({**PLAN, "seed": 99}).model_dump_json())
    else:
        (state["directory"] / "stimulus.json").write_bytes(b"x" * 100)
    assert state["client"].get(f"/api/attempts/{state['attempt_id']}/artifacts/artifact-1/download").status_code == 409
    assert request_replay(state).status_code == 409


def test_cross_owner_is_not_found(evidence_state):
    state = evidence_state
    with state["sessions"].begin() as session:
        session.get(Campaign, state["campaign_id"]).owner = "somebody-else"
    assert state["client"].get(f"/api/attempts/{state['attempt_id']}/artifacts").status_code == 404
    assert request_export(state).status_code == 404
    assert request_replay(state).status_code == 404


def test_replay_is_exact_queued_durable_idempotent_and_cancellable(evidence_state):
    state = evidence_state
    before = state["client"].get(f"/api/campaigns/{state['campaign_id']}").json()
    first = request_replay(state)
    assert first.status_code == 202, first.text
    replay = first.json()
    assert replay["source_attempt_id"] == state["attempt_id"]
    assert replay["id"] != before["id"] and replay["execution_status"] == "queued"
    assert replay["plan"] == before["plan"] and replay["identity"] == before["identity"]
    assert request_replay(state).json() == replay
    assert len(replay["attempts"]) == 1 and replay["attempts"][0]["result"] is None
    cancelled = state["client"].post(f"/api/campaigns/{replay['id']}/cancel", headers={"Idempotency-Key": "cancel"}).json()
    assert cancelled["execution_status"] == "cancelled"
    assert request_replay(state).json() == cancelled
    assert state["client"].get(f"/api/campaigns/{before['id']}").json() == before
    with state["sessions"]() as session:
        assert session.get(ReplayLink, replay["id"]).source_attempt_id == state["attempt_id"]


def test_concurrent_replay_returns_one_new_campaign(evidence_state):
    state = evidence_state
    with ThreadPoolExecutor(max_workers=3) as pool:
        results = list(pool.map(lambda _: request_replay(state, "concurrent"), range(3)))
    assert all(response.status_code == 202 for response in results), [response.text for response in results]
    assert len({response.json()["id"] for response in results}) == 1
    with state["sessions"]() as session:
        assert len(list(session.scalars(select(ReplayLink)))) == 1


def test_cross_attempt_key_reuse_and_artifact_lookup_are_rejected(evidence_state):
    state = evidence_state
    request_replay(state)
    request_export(state)
    with state["sessions"].begin() as session:
        row = service.create_campaign(session, "fifo_correct", Experiment.model_validate(PLAN).model_dump(mode="json"), "second-original")
        service.start(session, row.id)
        session.flush()
        other_attempt = session.scalar(select(Attempt).where(Attempt.campaign_id == row.id))
        other_id = other_attempt.id
    assert state["client"].post(f"/api/attempts/{other_id}/replay", headers={"Idempotency-Key": "replay"}).status_code == 409
    assert state["client"].post(f"/api/attempts/{other_id}/exports", headers={"Idempotency-Key": "export"}).status_code == 409
    # A valid artifact ID on one completed attempt does not authorize another one.
    assert state["client"].get(f"/api/attempts/{other_id}/artifacts/artifact-1/download").status_code == 409


def test_existing_replay_key_still_requires_source_ownership(evidence_state):
    state = evidence_state
    assert request_replay(state).status_code == 202
    with state["sessions"].begin() as session:
        session.get(Campaign, state["campaign_id"]).owner = "somebody-else"
    assert request_replay(state).status_code == 404


def test_internal_fresh_creation_never_reuses_public_campaign(evidence_state):
    state = evidence_state
    with state["sessions"].begin() as session:
        original = session.get(Campaign, state["campaign_id"])
        with pytest.raises(service.Conflict, match="cannot reuse"):
            service.create_campaign(session, original.project_id, original.plan, "original", require_new=True)


def test_replay_rejects_source_change_and_worker_preflight_stops_runner(evidence_state, monkeypatch):
    state = evidence_state
    changed = {**state["result"].identity.model_dump(), "source_hash": "0" * 64}
    changed.pop("environment_hash")
    monkeypatch.setattr(worker, "installed_identity", lambda experiment: changed)
    assert request_replay(state).status_code == 409
    with state["sessions"].begin() as session:
        campaign = service.create_campaign(session, "fifo_correct", Experiment.model_validate(PLAN).model_dump(mode="json"), "preflight")
        service.start(session, campaign.id)
        session.flush()
        attempt_id = session.scalar(select(Attempt.id).where(Attempt.campaign_id == campaign.id))
    def prohibited_runner(*args, **kwargs):
        pytest.fail("Provenance change must be rejected before execution")
    outcome = worker.execute_attempt(attempt_id, sessions=state["sessions"], runner=prohibited_runner, artifact_root=state["root"])
    assert outcome["infrastructure_error"]


def test_replay_worker_rejects_tampered_fresh_output(evidence_state):
    state = evidence_state
    replay = request_replay(state).json()
    attempt_id = replay["attempts"][0]["id"]
    def malformed_output(experiment, output, *, cancel_event):
        output.mkdir(parents=True)
        (output / "experiment.json").write_text(experiment.model_dump_json())
        (output / "result.json").write_text(state["result"].model_dump_json())
        (output / "stimulus.json").write_bytes(b"invalid fresh bytes")
        return state["result"]
    outcome = worker.execute_attempt(attempt_id, sessions=state["sessions"], runner=malformed_output, artifact_root=state["root"])
    assert outcome["infrastructure_error"]
    result = state["client"].get(f"/api/campaigns/{replay['id']}").json()
    assert result["execution_status"] == "infrastructure_error" and result["verdict"] is None


def test_artifact_link_policy_rejects_ancestor_without_host_symlink_privilege(evidence_state, monkeypatch):
    from pathlib import Path
    state = evidence_state
    original = Path.is_symlink
    monkeypatch.setattr(Path, "is_symlink", lambda path: True if path == state["directory"] else original(path))
    assert state["client"].get(f"/api/attempts/{state['attempt_id']}/artifacts").status_code == 409


def test_export_is_async_duplicate_safe_and_download_verified(evidence_state):
    state = evidence_state
    response = request_export(state)
    assert response.status_code == 202, response.text
    export = response.json()
    assert export["status"] == "queued" and export["sha256"] is None
    assert not (state["root"] / "exports").exists()
    assert request_export(state).json() == export
    assert state["client"].get(f"/api/exports/{export['id']}/download").status_code == 409
    assert artifacts.execute_export(export["id"], sessions=state["sessions"], root=state["root"])["accepted"]
    assert not artifacts.execute_export(export["id"], sessions=state["sessions"], root=state["root"])["accepted"]
    status = state["client"].get(f"/api/exports/{export['id']}").json()
    assert status["status"] == "completed", status
    download = state["client"].get(f"/api/exports/{export['id']}/download")
    assert download.status_code == 200
    assert hashlib.sha256(download.content).hexdigest() == status["sha256"]
    with ZipFile(BytesIO(download.content)) as archive:
        assert "artifacts/stimulus.json" in archive.namelist()
        assert "artifacts/execution.log" not in archive.namelist()
    with state["sessions"]() as session:
        artifacts.export_path(session.get(EvidenceExport, export["id"]), state["root"]).write_bytes(b"tampered")
    assert state["client"].get(f"/api/exports/{export['id']}/download").status_code == 409


def test_export_tampering_and_broker_failure_remain_durable(evidence_state):
    state = evidence_state
    export = request_export(state).json()
    class Broker:
        def enqueue_export(self, export_id):
            raise ConnectionError("private connection string")
    assert artifacts.dispatch_exports(state["sessions"], Broker()) == {"sent": 0, "failed": 1}
    assert state["client"].get(f"/api/exports/{export['id']}").json()["error_type"] == "ConnectionError"
    (state["directory"] / "stimulus.json").write_bytes(b"tampered")
    assert artifacts.execute_export(export["id"], sessions=state["sessions"], root=state["root"])["failed"]
    status = state["client"].get(f"/api/exports/{export['id']}").json()
    assert status["status"] == "failed" and status["sha256"] is None
    assert state["client"].get(f"/api/exports/{export['id']}/download").status_code == 409


def test_cancelled_export_cannot_publish_late_result(evidence_state, monkeypatch):
    state = evidence_state
    export = request_export(state).json()
    original = artifacts.export_bundle
    def cancel_during_export(*args, **kwargs):
        with state["sessions"].begin() as session:
            artifacts.cancel_export(session, export["id"])
        return original(*args, **kwargs)
    monkeypatch.setattr(artifacts, "export_bundle", cancel_during_export)
    assert not artifacts.execute_export(export["id"], sessions=state["sessions"], root=state["root"])["accepted"]
    assert state["client"].get(f"/api/exports/{export['id']}").json()["status"] == "cancelled"
    assert state["client"].get(f"/api/exports/{export['id']}/download").status_code == 409


def test_export_stale_recovery_revokes_publication(evidence_state):
    state = evidence_state
    export = request_export(state).json()
    with state["sessions"].begin() as session:
        row = session.get(EvidenceExport, export["id"])
        row.status, row.started_at = "running", now() - timedelta(seconds=500)
        old_lease = row.lease
    with state["sessions"].begin() as session:
        assert artifacts.recover_exports(session) == 1
    with state["sessions"]() as session:
        row = session.get(EvidenceExport, export["id"])
        assert row.status == "failed" and row.lease != old_lease


def test_export_uses_db_verified_snapshot_during_evidence_swap(evidence_state, monkeypatch):
    state = evidence_state
    export = request_export(state).json()
    original = artifacts.export_bundle
    def swap_original(directory, destination, **kwargs):
        (state["directory"] / "stimulus.json").write_bytes(b"malicious replacement")
        return original(directory, destination, **kwargs)
    monkeypatch.setattr(artifacts, "export_bundle", swap_original)
    artifacts.execute_export(export["id"], sessions=state["sessions"], root=state["root"])
    response = state["client"].get(f"/api/exports/{export['id']}/download")
    assert response.status_code == 200
    with ZipFile(BytesIO(response.content)) as archive:
        assert archive.read("artifacts/stimulus.json") == state["stimulus_bytes"]


def test_symlink_path_is_rejected_when_host_supports_it(evidence_state, tmp_path):
    state = evidence_state
    target = state["directory"] / "stimulus.json"
    alternate = state["directory"] / "same-bytes.json"
    alternate.write_bytes(target.read_bytes())
    target.unlink()
    try:
        target.symlink_to(alternate)
    except OSError:
        pytest.skip("Windows host lacks symlink creation privilege")
    assert state["client"].get(f"/api/attempts/{state['attempt_id']}/artifacts").status_code == 409
