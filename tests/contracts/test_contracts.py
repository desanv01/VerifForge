import json

import pytest
from pydantic import ValidationError

from veriforge.contracts import (
    ArtifactReference, AttemptResult, BehavioralContract, CheckerIdentity,
    EvidenceIdentity, Experiment, FailureSignature, Finding, ProjectRevision,
    Requirement, SourceFile, canonical_hash,
)


HASH = "a" * 64


def experiment(**changes):
    values = dict(experiment_id="exp-1", fixture_id="fifo_correct", objective_ids=["FIFO_ORDER"],
                  operations=[{"op": "reset"}, {"op": "write", "value": 42}, {"op": "read"}])
    values.update(changes)
    return Experiment(**values)


def identity():
    return EvidenceIdentity(source_hash=HASH, checker_hash=HASH, contract_hash=HASH,
                            experiment_hash=HASH, environment_hash=HASH)


def signature():
    return FailureSignature(requirement_id="FIFO_ORDER", failure_class="data_mismatch", first_cycle=2,
                            expected="42", observed="0", signature_hash=HASH)


def test_exact_stimulus_json_roundtrip_and_canonical_identity():
    plan = experiment()
    wire = plan.model_dump_json()
    assert Experiment.model_validate_json(wire) == plan
    assert plan.cycle_count == 3
    assert canonical_hash(plan) == canonical_hash(Experiment.model_validate(json.loads(wire)))
    assert canonical_hash(plan) != canonical_hash(experiment(seed=1))
    assert Experiment.model_json_schema()["properties"]["operations"]["items"]["discriminator"]["propertyName"] == "op"
    with pytest.raises(ValidationError):
        plan.seed = 2


@pytest.mark.parametrize("changes", [
    {"shell": "echo unsafe"}, {"fixture_id": "../../untrusted.sv"},
    {"seed": True}, {"seed": "12"}, {"seed": -1}, {"schema_version": "2"},
    {"operations": []}, {"operations": [{"op": "execute", "command": "anything"}]},
    {"operations": [{"op": "reset", "cycles": 0}]},
    {"operations": [{"op": "reset", "unknown": 1}]},
    {"operations": [{"op": "write", "value": 1}]},
    {"operations": [{"op": "idle"}]},
    {"operations": [{"op": "reset"}, {"op": "write", "value": 256}]},
    {"operations": [{"op": "reset"}, {"op": "write", "value": True}]},
    {"operations": [{"op": "reset"}, {"op": "cycle", "write": 1}]},
    {"operations": [{"op": "reset"}, {"op": "idle", "cycles": 11}], "budget": {"max_cycles": 10}},
    {"parameters": {"width": 0}}, {"parameters": {"depth": 1}},
    {"budget": {"wall_time_seconds": float("nan")}},
    {"budget": {"wall_time_seconds": float("inf")}},
    {"budget": {"wall_time_seconds": "30"}},
])
def test_malformed_experiments_rejected(changes):
    with pytest.raises(ValidationError):
        experiment(**changes)


def test_cycle_and_multi_cycle_stimulus_count():
    plan = experiment(operations=[{"op": "cycle", "reset": True, "write": True},
                                  {"op": "idle", "cycles": 4}, {"op": "reset", "cycles": 2}])
    assert plan.cycle_count == 7


@pytest.mark.parametrize("path", ["/absolute", "../outside", "a/../outside", "C:/secret", "a\\b", "a//b", "./a", "a/"])
def test_snapshot_and_artifact_paths_cannot_escape(path):
    with pytest.raises(ValidationError):
        SourceFile(path=path, sha256=HASH)
    with pytest.raises(ValidationError):
        ArtifactReference(artifact_id="a1", path=path, sha256=HASH, size_bytes=1, media_type="text/plain")


def test_execution_success_is_separate_from_design_verdict():
    timeout = AttemptResult(attempt_id="a1", experiment_id="e1", execution_status="completed", verdict="timeout")
    build = AttemptResult(attempt_id="a2", experiment_id="e1", execution_status="completed", verdict="build_error")
    assert timeout.verdict != "design_mismatch"
    assert build.verdict != "pass_within_scope"
    assert AttemptResult.model_validate_json(timeout.model_dump_json()) == timeout
    for status in ("queued", "preparing", "running", "finalizing"):
        assert AttemptResult(attempt_id="a1", experiment_id="e1", execution_status=status).verdict is None


@pytest.mark.parametrize("changes", [
    {"execution_status": "completed"},
    {"execution_status": "running", "verdict": "timeout"},
    {"execution_status": "cancelled", "verdict": "pass_within_scope", "identity": identity()},
    {"execution_status": "infrastructure_error", "verdict": "design_mismatch", "identity": identity(), "failure_signature": signature()},
    {"execution_status": "completed", "verdict": "pass_within_scope"},
    {"execution_status": "completed", "verdict": "design_mismatch", "identity": identity()},
    {"execution_status": "completed", "verdict": "timeout", "failure_signature": signature()},
])
def test_inconsistent_verdicts_rejected(changes):
    with pytest.raises(ValidationError):
        AttemptResult(attempt_id="a1", experiment_id="e1", **changes)


def test_design_failure_finding_and_revision_roundtrip():
    result = AttemptResult(attempt_id="a1", experiment_id="e1", execution_status="completed",
                           verdict="design_mismatch", identity=identity(), failure_signature=signature())
    assert AttemptResult.model_validate_json(result.model_dump_json()) == result
    finding = Finding(finding_id="f1", project_id="p1", revision_id="r1", contract_id="fifo-v1",
                      signature=signature(), identity=identity(), attempt_ids=["a1"], summary="FIFO order differs")
    assert Finding.model_validate_json(finding.model_dump_json()) == finding
    source = SourceFile(path="targets/fifo/fifo.sv", sha256=HASH)
    revision = ProjectRevision(project_id="p1", revision_id="r1", source_hash=HASH, fixture_id="fifo_correct", sources=[source])
    assert ProjectRevision.model_validate_json(revision.model_dump_json()) == revision
    with pytest.raises(ValidationError):
        ProjectRevision(project_id="p1", revision_id="r1", source_hash=HASH, fixture_id="fifo_correct", sources=[source, source])


def test_reviewed_contract_and_duplicate_requirements():
    checker = CheckerIdentity(checker_id="fifo-checker", version="1", sha256=HASH)
    requirement = Requirement(requirement_id="FIFO_ORDER", description="Reads retain FIFO ordering")
    contract = BehavioralContract(checker=checker, requirements=[requirement], review_status="reviewed")
    assert BehavioralContract.model_validate_json(contract.model_dump_json()) == contract
    with pytest.raises(ValidationError):
        BehavioralContract(checker=checker, requirements=[requirement, requirement])


def test_fastapi_request_boundary_uses_shared_discriminated_schema():
    import asyncio

    import httpx
    from fastapi import FastAPI

    app = FastAPI()

    @app.post("/experiments", response_model=Experiment)
    async def accept(plan: Experiment):
        return plan

    async def check_requests():
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
            payload = experiment().model_dump(mode="json")
            assert (await client.post("/experiments", json=payload)).json() == payload
            payload["operations"].append({"op": "shell", "command": "unsafe"})
            assert (await client.post("/experiments", json=payload)).status_code == 422

    asyncio.run(check_requests())
    assert app.openapi()["components"]["schemas"]["Experiment"]["additionalProperties"] is False
