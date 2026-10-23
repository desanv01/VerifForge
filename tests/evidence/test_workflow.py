"""Scripted callback tests: none of these tests claim simulator execution."""
import hashlib
import json
from pathlib import Path
from zipfile import ZipFile

import pytest

from veriforge.contracts import (
    ArtifactReference, AttemptResult, EvidenceIdentity, Experiment, FailureSignature,
    ToolVersion, canonical_hash,
)
from veriforge.evidence import (
    ExportLimits, export_bundle, load_attempt, portable_artifact_bytes, reduce_failure, replay_attempt,
    same_failure, validate_candidate,
)

H = "a" * 64


def plan():
    return Experiment.model_validate(dict(experiment_id="scripted-unit", fixture_id="fifo_reset_bug",
        objective_ids=["FIFO_BEHAVIOR"], operations=[
            {"op": "reset", "cycles": 3}, {"op": "write", "value": 17},
            {"op": "idle", "cycles": 4}, {"op": "reset"}, {"op": "read"}]))


def scripted(experiment, directory, *, verdict="design_mismatch", change=None):
    """A deterministic unit-test stub, never HDL evidence."""
    directory = Path(directory)
    directory.mkdir(parents=True)
    raw = experiment.model_dump_json().encode()
    (directory / "experiment.json").write_bytes(raw)
    identity = dict(source_hash=H if experiment.fixture_id == "fifo_reset_bug" else "b" * 64,
        checker_hash=H, contract_hash=H, experiment_hash=canonical_hash(experiment), environment_hash=H)
    if change:
        identity.update(change)
    signature = FailureSignature(requirement_id="FIFO_BEHAVIOR", failure_class="fifo.empty",
        first_cycle=experiment.cycle_count - 1, expected="True", observed="False", signature_hash=H)
    result = AttemptResult(attempt_id=directory.name, experiment_id=experiment.experiment_id,
        execution_status="completed", verdict=verdict, identity=EvidenceIdentity(**identity),
        failure_signature=signature if verdict in {"design_mismatch", "property_failure"} else None,
        tool_versions=(ToolVersion(name="scripted-test-stub", version="1"),),
        artifacts=(ArtifactReference(artifact_id="input", path="experiment.json",
            sha256=hashlib.sha256(raw).hexdigest(), size_bytes=len(raw), media_type="application/json"),))
    (directory / "result.json").write_text(result.model_dump_json())
    return result


@pytest.fixture
def original(tmp_path):
    directory = tmp_path / "original"
    result = scripted(plan(), directory)
    return directory, result


def update_result(directory, result):
    (directory / "result.json").write_text(result.model_dump_json())


def add_artifact(directory, relative, data):
    experiment, result = load_attempt(directory)
    target = directory / relative
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(data)
    ref = ArtifactReference(artifact_id="added-" + hashlib.sha256(relative.encode()).hexdigest()[:12], path=relative, sha256=hashlib.sha256(data).hexdigest(),
        size_bytes=len(data), media_type="text/plain")
    update_result(directory, result.model_copy(update={"artifacts": (*result.artifacts, ref)}))
    return ref.artifact_id


def test_load_exact_and_replay(original, tmp_path):
    directory, result = original
    experiment, loaded = load_attempt(directory)
    assert loaded == result and experiment == plan()
    replay = replay_attempt(directory, tmp_path / "replay", runner=scripted)
    assert replay.same_semantic_failure
    assert replay.replayed.identity.experiment_hash == canonical_hash(experiment)


@pytest.mark.parametrize("field", ["source_hash", "checker_hash", "contract_hash", "experiment_hash"])
def test_replay_rejects_changed_identity(original, tmp_path, field):
    directory, _ = original
    def changed(experiment, output):
        return scripted(experiment, output, change={field: "c" * 64})
    with pytest.raises(ValueError, match="identity changed"):
        replay_attempt(directory, tmp_path / "replay", runner=changed)


def test_tampered_input_rejected(original):
    directory, _ = original
    (directory / "experiment.json").write_text(plan().model_dump_json().replace("17", "18"))
    with pytest.raises(ValueError, match="identity"):
        load_attempt(directory)


def test_artifact_hash_and_size_checked(original):
    directory, _ = original
    add_artifact(directory, "stimulus.json", b"[]")
    (directory / "stimulus.json").write_bytes(b"{}")
    with pytest.raises(ValueError, match="hash or size"):
        load_attempt(directory)


def test_missing_and_duplicate_artifacts(original):
    directory, result = original
    update_result(directory, result.model_copy(update={"artifacts": result.artifacts * 2}))
    with pytest.raises(ValueError, match="duplicate"):
        load_attempt(directory)
    update_result(directory, result)
    (directory / "experiment.json").unlink()
    with pytest.raises(ValueError, match="missing"):
        load_attempt(directory)


def test_symlink_escape_rejected(original, tmp_path):
    directory, result = original
    outside = tmp_path / "outside.json"
    outside.write_bytes(b"[]")
    try:
        (directory / "stimulus.json").symlink_to(outside)
    except OSError:
        pytest.skip("host does not permit symlink creation")
    ref = ArtifactReference(artifact_id="escape", path="stimulus.json", sha256=hashlib.sha256(b"[]").hexdigest(),
                            size_bytes=2, media_type="application/json")
    update_result(directory, result.model_copy(update={"artifacts": (*result.artifacts, ref)}))
    with pytest.raises(ValueError, match="escapes"):
        load_attempt(directory)


def test_export_omits_unsafe_diagnostics_and_keeps_original(original, tmp_path):
    directory, result = original
    add_artifact(directory, "job.json", b'{"secret":"sk-example-secret-key", "path":"C:/Users/Dv"}')
    add_artifact(directory, "stimulus.json", b"[]")
    before = {p.name: p.read_bytes() for p in directory.iterdir()}
    destination = export_bundle(directory, tmp_path / "portable.zip")
    with ZipFile(destination) as archive:
        assert "artifacts/stimulus.json" in archive.namelist()
        assert "artifacts/job.json" not in archive.namelist()
        assert all(not name.startswith("/") and ".." not in name for name in archive.namelist())
        manifest = json.loads(archive.read("manifest.json"))
        assert manifest["identity"] == result.identity.model_dump()
        assert manifest["experiment"] == plan().model_dump(mode="json")
        assert "expected" not in manifest["failure"]
    assert before == {p.name: p.read_bytes() for p in directory.iterdir()}
    with pytest.raises(FileExistsError):
        export_bundle(directory, destination)


@pytest.mark.parametrize("content", [b"C:/Users/private/source.sv", b"/home/private/build", b"api_key='secret'", b"sk-abcdefghijk123"])
def test_export_rejects_sensitive_selected_content(original, tmp_path, content):
    directory, _ = original
    add_artifact(directory, "fifo.sv", content)
    with pytest.raises(ValueError, match="possible secret or local"):
        export_bundle(directory, tmp_path / "unsafe.zip")
    assert not (tmp_path / "unsafe.zip").exists()


def test_export_bounds_and_original_destination(original, tmp_path):
    directory, _ = original
    with pytest.raises(ValueError, match="original"):
        export_bundle(directory, directory / "bundle.zip")
    with pytest.raises(ValueError, match="file or byte"):
        export_bundle(directory, tmp_path / "small.zip", limits=ExportLimits(max_files=2))
    assert not (tmp_path / "small.zip").exists()


def test_evidence_total_byte_bound(original):
    directory, _ = original
    add_artifact(directory, "observed.json", b" " * 2048)
    with pytest.raises(ValueError, match="byte limit"):
        load_attempt(directory, limits=ExportLimits(max_bytes=1024))


@pytest.mark.parametrize("name", ["stimulus.json", "contract.json", "fifo.sv", "checker.py", "observed.json"])
def test_portable_download_returns_verified_snapshot(original, name):
    directory, _ = original
    artifact_id = add_artifact(directory, name, b"bounded portable text")
    data = portable_artifact_bytes(directory, artifact_id)
    (directory / name).write_bytes(b"changed later")
    assert data == b"bounded portable text"


@pytest.mark.parametrize("name", ["job.json", "execution.log", "environment.json", "experiment.json"])
def test_portable_download_rejects_nonportable_names(original, name):
    directory, _ = original
    if name == "experiment.json":
        artifact_id = "input"
    else:
        artifact_id = add_artifact(directory, name, b"otherwise innocuous")
    with pytest.raises(ValueError, match="not permitted"):
        portable_artifact_bytes(directory, artifact_id)


@pytest.mark.parametrize("data", [b"C:/Users/private", b"/home/private/log", b"api_key='test'", b"\xff"])
def test_portable_download_rejects_sensitive_or_binary_text(original, data):
    directory, _ = original
    artifact_id = add_artifact(directory, "stimulus.json", data)
    with pytest.raises(ValueError, match="possible secret|UTF-8"):
        portable_artifact_bytes(directory, artifact_id)


def test_portable_download_rejects_unknown_id_and_bounds(original):
    directory, _ = original
    with pytest.raises(ValueError, match="not present"):
        portable_artifact_bytes(directory, "missing")
    artifact_id = add_artifact(directory, "stimulus.json", b"x" * 2048)
    with pytest.raises(ValueError, match="byte limit"):
        portable_artifact_bytes(directory, artifact_id, limits=ExportLimits(max_bytes=1024))


def test_portable_download_detects_change_after_initial_verification(original, monkeypatch):
    from veriforge.evidence import workflow
    directory, _ = original
    artifact_id = add_artifact(directory, "stimulus.json", b"[]")
    read = workflow._read
    selected_reads = 0
    def raced(path, limit):
        nonlocal selected_reads
        if path.name == "stimulus.json":
            selected_reads += 1
            if selected_reads == 2:
                path.write_bytes(b"{}")
        return read(path, limit)
    monkeypatch.setattr(workflow, "_read", raced)
    with pytest.raises(ValueError, match="changed during portable"):
        portable_artifact_bytes(directory, artifact_id)


def test_reducer_deletes_and_simplifies_without_changing_identity(original, tmp_path):
    directory, result = original
    original_bytes = (directory / "experiment.json").read_bytes()
    record = reduce_failure(directory, tmp_path / "reduction", runner=scripted, max_attempts=20)
    assert len(record.reduced_experiment.operations) == 1
    assert record.reduced_experiment.operations[0].cycles == 1
    assert same_failure(result, record.reduced_result)
    assert record.original_experiment == plan()
    assert len(record.attempts) == len(record.submitted_experiments) <= 20
    assert (directory / "experiment.json").read_bytes() == original_bytes
    assert record.improved
    assert record.claim == "bounded_search_no_minimality_guarantee"


@pytest.mark.parametrize("verdict", ["harness_error", "build_error", "timeout", "pass_within_scope", "property_failure"])
def test_reduction_rejects_other_outcomes(original, tmp_path, verdict):
    directory, result = original
    def rejected(experiment, output):
        return scripted(experiment, output, verdict=verdict)
    record = reduce_failure(directory, tmp_path / "reduction", runner=rejected, max_attempts=2)
    assert len(record.attempts) == 2
    assert record.reduced_experiment == plan()
    assert record.reduced_result == result
    assert not record.improved


@pytest.mark.parametrize("field", ["failure_class", "requirement_id", "checker_hash", "contract_hash", "experiment_hash"])
def test_reducer_rejects_changed_semantic_identity(original, tmp_path, field):
    directory, _ = original
    def changed(experiment, output):
        result = scripted(experiment, output)
        if field in {"failure_class", "requirement_id"}:
            signature = result.failure_signature.model_copy(update={field: "other.failure"})
            return result.model_copy(update={"failure_signature": signature})
        identity = result.identity.model_copy(update={field: "c" * 64})
        return result.model_copy(update={"identity": identity})
    record = reduce_failure(directory, tmp_path / "reduction", runner=changed, max_attempts=2)
    assert record.reduced_experiment == plan()


@pytest.mark.parametrize("budget", [0, 1001, True, 1.5])
def test_reducer_rejects_bad_budget(original, tmp_path, budget):
    with pytest.raises(ValueError, match="max_attempts"):
        reduce_failure(original[0], tmp_path / "reduction", runner=scripted, max_attempts=budget)


def candidate_pass(experiment, output):
    return scripted(experiment, output, verdict="pass_within_scope")


def validate(directory, output, **kwargs):
    options = dict(candidate_fixture_id="fifo_correct", candidate_runner=candidate_pass,
        original_runner=scripted, neighboring_plans=(plan(),), regression_plans=(plan(),))
    options.update(kwargs)
    return validate_candidate(directory, output, **options)


def test_candidate_requires_reproduction_and_all_checks(original, tmp_path):
    record = validate(original[0], tmp_path / "validation")
    assert record.status == "validated_under_checks"
    assert len(record.neighbors) == len(record.regressions) == 1
    assert len(record.candidate_experiments) == 3
    assert record.execution_basis == "supplied_runner_evidence"
    assert record.original_experiment.fixture_id == "fifo_reset_bug"


def test_candidate_stops_if_original_no_longer_reproduces(original, tmp_path):
    def never(*args):
        pytest.fail("candidate must not execute without reproduction")
    record = validate(original[0], tmp_path / "validation", original_runner=candidate_pass, candidate_runner=never)
    assert record.status == "original_not_reproduced"
    assert record.witness is None


@pytest.mark.parametrize("changed", ["checker_hash", "contract_hash", "experiment_hash", "source_hash"])
def test_candidate_rejects_changed_or_same_identity(original, tmp_path, changed):
    def invalid(experiment, output):
        return scripted(experiment, output, verdict="pass_within_scope", change={changed: H if changed == "source_hash" else "c" * 64})
    assert validate(original[0], tmp_path / "validation", candidate_runner=invalid).status == "failed_under_checks"


def test_candidate_regression_failure_is_not_validated(original, tmp_path):
    count = 0
    def overfit(experiment, output):
        nonlocal count
        count += 1
        return scripted(experiment, output, verdict="pass_within_scope" if count < 3 else "harness_error")
    record = validate(original[0], tmp_path / "validation", candidate_runner=overfit)
    assert record.witness.verdict == "pass_within_scope"
    assert record.status == "failed_under_checks"


def test_candidate_requires_separate_revision_nonempty_suites(original, tmp_path):
    for options in ({"candidate_fixture_id": "fifo_reset_bug"}, {"neighboring_plans": ()}, {"regression_plans": ()}):
        with pytest.raises(ValueError):
            validate(original[0], tmp_path / "validation", **options)


def test_candidate_rejects_plan_contract_parameter_change(original, tmp_path):
    data = plan().model_dump(mode="json")
    data["parameters"]["depth"] = 8
    with pytest.raises(ValueError, match="contract/target/parameters/revision"):
        validate(original[0], tmp_path / "validation", neighboring_plans=(Experiment.model_validate(data),))
