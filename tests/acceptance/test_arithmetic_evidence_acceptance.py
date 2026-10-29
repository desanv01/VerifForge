"""Independent family-evidence checks: synthetic records, no tool execution."""
import hashlib
import json
from pathlib import Path
from zipfile import ZipFile

import pytest

from veriforge.adapters import get_adapter
from veriforge.arithmetic import ArithmeticExperiment
from veriforge.contracts import ArtifactReference, AttemptResult, FailureSignature, ResourceUsage, ToolVersion, Verdict, canonical_hash
from veriforge.evidence import export_bundle, load_attempt, portable_artifact_bytes, reduce_failure, replay_attempt, validate_candidate


def plan():
    return ArithmeticExperiment(experiment_id="independent-synthetic", fixture_id="multiplier_data_bug", operations=(
        {"op": "reset"}, {"op": "multiply", "a": 7, "b": 9}, {"op": "idle", "cycles": 2}))


@pytest.fixture
def recorded(tmp_path):
    """7*9=63; deliberately synthetic observed 62 is not an HDL mutant claim."""
    experiment = plan()
    descriptor = get_adapter("rtl.arithmetic.v1")
    directory = tmp_path / "synthetic-original"
    directory.mkdir()
    expected = [
        dict(cycle=0, operation_index=0, valid=False, product=0, reset=True),
        dict(cycle=1, operation_index=1, valid=False, product=None, reset=False),
        dict(cycle=2, operation_index=2, valid=False, product=None, reset=False),
        dict(cycle=3, operation_index=2, valid=True, product=63, reset=False),
    ]
    observed = [dict(cycle=i, valid=i == 3, product=62 if i == 3 else 0) for i in range(4)]
    stimulus = [
        dict(operation_index=0, reset=True, valid=False, a=0, b=0),
        dict(operation_index=1, reset=False, valid=True, a=7, b=9),
        dict(operation_index=2, reset=False, valid=False, a=0, b=0),
        dict(operation_index=2, reset=False, valid=False, a=0, b=0),
    ]
    files = {
        "experiment.json": experiment.model_dump_json().encode(),
        "contract.json": descriptor.contract_for(experiment).model_dump_json().encode(),
        "stimulus.json": json.dumps(stimulus).encode(),
        "observed.json": json.dumps(observed).encode(),
        "arithmetic-trace.json": json.dumps(dict(schema_version="arithmetic.trace.v1", expected=expected, observed=observed)).encode(),
        "multiplier.sv": b"// synthetic source snapshot; not executed\n",
        "fifo.sv": b"// wrong-family file must never be exported\n",
        "job.json": b'{"private":"synthetic job only"}',
        "execution.log": b"synthetic private diagnostic; no simulator ran",
    }
    refs = []
    for index, (name, data) in enumerate(files.items()):
        (directory / name).write_bytes(data)
        refs.append(ArtifactReference(artifact_id=f"synthetic-{index}", path=name, sha256=hashlib.sha256(data).hexdigest(),
                                      size_bytes=len(data), media_type="text/plain"))
    result = AttemptResult(attempt_id="synthetic-attempt", experiment_id=experiment.experiment_id,
        execution_status="completed", verdict="design_mismatch", artifacts=tuple(refs),
        identity={**descriptor.expected_identity(experiment), "environment_hash": "f" * 64},
        tool_versions=(ToolVersion(name="synthetic-test-only", version="not-a-simulator"),),
        failure_signature=FailureSignature(requirement_id="ARITH_PRODUCT", failure_class="arithmetic.product",
            first_cycle=3, expected="63", observed="62", signature_hash="f" * 64), usage=ResourceUsage(cycles=4),
        uncertainties=("Synthetic trusted callback evidence only; no actual tool result",))
    (directory / "result.json").write_text(result.model_dump_json())
    return directory, experiment, result


def rehash_sidecar(directory, change):
    path = directory / "arithmetic-trace.json"
    data = json.loads(path.read_bytes())
    change(data)
    raw = json.dumps(data).encode()
    path.write_bytes(raw)
    result = AttemptResult.model_validate_json((directory / "result.json").read_bytes())
    refs = tuple(ref.model_copy(update={"sha256": hashlib.sha256(raw).hexdigest(), "size_bytes": len(raw)})
                 if ref.path == path.name else ref for ref in result.artifacts)
    (directory / "result.json").write_text(result.model_copy(update={"artifacts": refs}).model_dump_json())


@pytest.mark.parametrize("damage", ["reset_integer", "expected_float", "expected_null", "observed_float", "negative", "usage"])
def test_rehashed_sidecar_still_requires_strict_family_structure(recorded, damage):
    directory, _, result = recorded
    if damage == "usage":
        (directory / "result.json").write_text(result.model_copy(update={"usage": ResourceUsage(cycles=3)}).model_dump_json())
    else:
        def change(data):
            if damage == "reset_integer": data["expected"][0]["reset"] = 1
            if damage == "expected_float": data["expected"][3]["product"] = 63.0
            if damage == "expected_null": data["expected"][3]["product"] = None
            if damage == "observed_float": data["observed"][3]["product"] = 62.0
            if damage == "negative": data["observed"][0]["product"] = -1
        rehash_sidecar(directory, change)
    with pytest.raises(ValueError):
        load_attempt(directory)


def test_family_allowlist_preserves_sidecar_and_excludes_fifo_and_private_files(recorded, tmp_path):
    directory, experiment, result = recorded
    original = {p.name: hashlib.sha256(p.read_bytes()).hexdigest() for p in directory.iterdir()}
    archive = export_bundle(directory, tmp_path / "family.zip")
    with ZipFile(archive) as bundle:
        manifest = json.loads(bundle.read("manifest.json"))
        assert manifest["adapter_id"] == "rtl.arithmetic.v1" and manifest["trace"] == []
        assert manifest["family_trace"]["schema_version"] == "arithmetic.trace.v1"
        assert bundle.read("artifacts/arithmetic-trace.json") == (directory / "arithmetic-trace.json").read_bytes()
        assert "artifacts/multiplier.sv" in bundle.namelist()
        for name in ("fifo.sv", "job.json", "execution.log"):
            assert "artifacts/" + name not in bundle.namelist()
            ref = next(ref for ref in result.artifacts if ref.path == name)
            with pytest.raises(ValueError, match="not permitted"):
                portable_artifact_bytes(directory, ref.artifact_id)
    assert {p.name: hashlib.sha256(p.read_bytes()).hexdigest() for p in directory.iterdir()} == original
    assert isinstance(load_attempt(directory)[0], ArithmeticExperiment)


def test_historical_export_survives_installed_drift_but_native_replay_never_resolves_runner(recorded, tmp_path, monkeypatch):
    directory, experiment, result = recorded
    descriptor = get_adapter("rtl.arithmetic.v1")
    calls = []

    def changed_installed(self, experiment):
        calls.append(experiment)
        return {key: "0" * 64 for key in ("source_hash", "checker_hash", "contract_hash", "experiment_hash")}

    def prohibited_resolution(*args):
        pytest.fail("Changed installed provenance must stop native runner resolution")

    monkeypatch.setattr(type(descriptor), "expected_identity", changed_installed)
    monkeypatch.setattr(type(descriptor), "resolve_runner", prohibited_resolution)
    assert load_attempt(directory)[1] == result
    export_bundle(directory, tmp_path / "historical.zip")
    assert calls == []
    with pytest.raises(ValueError, match="identity changed"):
        replay_attempt(directory, tmp_path / "not-executed")
    assert calls == [experiment] and not (tmp_path / "not-executed").exists()


@pytest.mark.parametrize("field", ["source_hash", "checker_hash", "contract_hash", "experiment_hash"])
def test_supplied_runner_cannot_return_different_replay_identity(recorded, tmp_path, field):
    directory, _, result = recorded

    def scripted_callback(experiment, output):
        return result.model_copy(update={"identity": result.identity.model_copy(update={field: "0" * 64})})

    with pytest.raises(ValueError, match="identity changed"):
        replay_attempt(directory, tmp_path / "scripted-only", runner=scripted_callback)


@pytest.mark.parametrize("alteration", ["verdict_category", "requirement"])
def test_reducer_does_not_accept_semantically_different_arithmetic_failure(recorded, tmp_path, alteration):
    directory, _, original = recorded

    def scripted_callback(experiment, output):
        updates = {"identity": original.identity.model_copy(update={"experiment_hash": canonical_hash(experiment)})}
        if alteration == "verdict_category":
            updates["verdict"] = Verdict.PROPERTY_FAILURE
        else:
            updates["failure_signature"] = original.failure_signature.model_copy(update={"requirement_id": "ARITH_LATENCY"})
        return original.model_copy(update=updates)

    reduced = reduce_failure(directory, tmp_path / "scripted-reduction", runner=scripted_callback, max_attempts=2)
    assert len(reduced.attempts) <= 2 and not reduced.improved
    assert reduced.reduced_experiment == plan()


def test_candidate_original_identity_failure_prevents_candidate_calls(recorded, tmp_path):
    directory, experiment, original = recorded
    calls = []

    def original_callback(experiment, output):
        return original.model_copy(update={"identity": original.identity.model_copy(update={"contract_hash": "0" * 64})})

    def forbidden_candidate(*args):
        calls.append(args)
        pytest.fail("Candidate must not run when original reproduction identity changed")

    validation = validate_candidate(directory, tmp_path / "candidate-never-run", candidate_fixture_id="multiplier_correct",
        original_runner=original_callback, candidate_runner=forbidden_candidate,
        neighboring_plans=(experiment,), regression_plans=(experiment,))
    assert validation.status == "original_not_reproduced" and calls == []


def test_candidate_source_must_be_distinct_and_consistent_across_matrix(recorded, tmp_path):
    directory, experiment, original = recorded
    calls = []

    def original_callback(experiment, output):
        return original

    def candidate_callback(experiment, output):
        calls.append(experiment)
        identity = original.identity.model_copy(update={"experiment_hash": canonical_hash(experiment),
            "source_hash": "b" * 64 if len(calls) != 2 else "c" * 64})
        return original.model_copy(update={"identity": identity, "verdict": Verdict.PASS_WITHIN_SCOPE, "failure_signature": None})

    validation = validate_candidate(directory, tmp_path / "candidate-mixed-source", candidate_fixture_id="multiplier_correct",
        original_runner=original_callback, candidate_runner=candidate_callback,
        neighboring_plans=(experiment,), regression_plans=(experiment,))
    assert len(calls) == 3 and validation.status == "failed_under_checks"
