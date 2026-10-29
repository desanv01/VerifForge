"""Family evidence tests: scripted runners and read-only saved HDL artifacts only."""
import hashlib
import json
from pathlib import Path
import shutil
from zipfile import ZipFile

import pytest

from veriforge.adapters import get_adapter
from veriforge.arithmetic import ArithmeticExperiment, directed_experiment
from veriforge.arithmetic import oracle
from veriforge.contracts import (
    ArtifactReference, AttemptResult, EvidenceIdentity, FailureSignature, ResourceUsage,
    ToolVersion, TraceSample, canonical_hash,
    Verdict,
)
from veriforge.evidence import (
    export_bundle, load_attempt, portable_artifact_bytes, reduce_failure, replay_attempt,
    validate_candidate,
)
from veriforge.evidence.workflow import CandidateValidation, ReductionRecord

ROOT = Path(__file__).resolve().parents[2]
SAVED = ROOT / "tests/arithmetic/.tmp-live-vf050b-retry-20261024"
H = "a" * 64


def arithmetic_plan():
    return directed_experiment("multiplier_data_bug")


def scripted_arithmetic(plan, output, *, fail=True, failure_class="arithmetic.product", change=None):
    """Synthetic observations, not a simulator; exercises evidence decision rules."""
    output = Path(output)
    output.mkdir(parents=True)
    expected = oracle.expected_trace(oracle.expand(plan))
    observed = [{"cycle": row["cycle"], "valid": row["valid"],
                 "product": row["product"] if row["product"] is not None else 0} for row in expected]
    failing_cycle = next((row["cycle"] for row in expected if row["valid"]), None)
    failed = fail and failing_cycle is not None
    if failed:
        observed[failing_cycle]["product"] ^= 1
    files = {
        "experiment.json": plan.model_dump_json().encode(),
        "arithmetic-trace.json": json.dumps({"schema_version": "arithmetic.trace.v1",
            "expected": expected, "observed": observed}).encode(),
        "stimulus.json": json.dumps(oracle.expand(plan)).encode(),
        "observed.json": json.dumps(observed).encode(),
        "multiplier.sv": b"// scripted fixture; not simulator evidence\n",
    }
    refs = []
    for name, data in files.items():
        (output / name).write_bytes(data)
        refs.append(ArtifactReference(artifact_id="file-" + str(len(refs)), path=name,
            sha256=hashlib.sha256(data).hexdigest(), size_bytes=len(data), media_type="text/plain"))
    identity = dict(source_hash=H if plan.fixture_id != "multiplier_correct" else "b" * 64,
        checker_hash=H, contract_hash=H, experiment_hash=canonical_hash(plan), environment_hash=H)
    identity.update(change or {})
    signature = FailureSignature(requirement_id="ARITH_PRODUCT", failure_class=failure_class,
        first_cycle=failing_cycle, expected="scripted", observed="scripted", signature_hash=H) if failed else None
    result = AttemptResult(attempt_id=output.name, experiment_id=plan.experiment_id,
        execution_status="completed", verdict="design_mismatch" if failed else "pass_within_scope",
        identity=EvidenceIdentity(**identity), failure_signature=signature, artifacts=tuple(refs),
        tool_versions=(ToolVersion(name="scripted-unit-runner", version="1"),),
        usage=ResourceUsage(cycles=plan.cycle_count))
    (output / "result.json").write_text(result.model_dump_json())
    return result


@pytest.fixture
def scripted_original(tmp_path):
    path = tmp_path / "original"
    result = scripted_arithmetic(arithmetic_plan(), path)
    return path, result


def replace_artifact(directory, name, data):
    result = AttemptResult.model_validate_json((directory / "result.json").read_bytes())
    (directory / name).write_bytes(data)
    refs = tuple(ref.model_copy(update={"sha256": hashlib.sha256(data).hexdigest(), "size_bytes": len(data)})
                 if ref.path == name else ref for ref in result.artifacts)
    (directory / "result.json").write_text(result.model_copy(update={"artifacts": refs}).model_dump_json())


def test_arithmetic_load_export_and_snapshot(scripted_original, tmp_path):
    path, original = scripted_original
    experiment, result = load_attempt(path)
    assert isinstance(experiment, ArithmeticExperiment)
    assert result == original and not result.trace
    archive = export_bundle(path, tmp_path / "arithmetic.zip")
    with ZipFile(archive) as zipped:
        manifest = json.loads(zipped.read("manifest.json"))
        assert manifest["adapter_id"] == "rtl.arithmetic.v1"
        assert manifest["trace"] == []
        assert manifest["family_trace"] == {"schema_version": "arithmetic.trace.v1",
                                           "artifact": "artifacts/arithmetic-trace.json"}
        assert "artifacts/multiplier.sv" in zipped.namelist()
        assert "artifacts/fifo.sv" not in zipped.namelist()
        assert zipped.read("artifacts/arithmetic-trace.json") == (path / "arithmetic-trace.json").read_bytes()
    ref = next(ref for ref in original.artifacts if ref.path == "arithmetic-trace.json")
    assert portable_artifact_bytes(path, ref.artifact_id) == (path / ref.path).read_bytes()


@pytest.mark.parametrize("mutation", ["schema", "boolean", "product", "cycle", "index", "length", "extra", "fifo"])
def test_arithmetic_trace_rejects_wrong_envelope_even_with_updated_file_hash(scripted_original, mutation):
    path, result = scripted_original
    data = json.loads((path / "arithmetic-trace.json").read_bytes())
    if mutation == "schema":
        data["schema_version"] = "fifo.trace.v1"
    elif mutation == "boolean":
        data["observed"][0]["valid"] = 0
    elif mutation == "product":
        data["observed"][0]["product"] = 2 ** 16
    elif mutation == "cycle":
        data["observed"][1]["cycle"] = 0
    elif mutation == "index":
        data["expected"][0]["operation_index"] = 1
    elif mutation == "length":
        data["observed"].pop()
    elif mutation == "extra":
        data["extra"] = "unrecognized"
    elif mutation == "fifo":
        data["observed"][0]["empty"] = True
    replace_artifact(path, "arithmetic-trace.json", json.dumps(data).encode())
    with pytest.raises(ValueError):
        load_attempt(path)


def test_arithmetic_requires_sidecar_and_rejects_common_fifo_trace(scripted_original):
    path, result = scripted_original
    invalid = result.model_copy(update={"trace": (TraceSample(cycle=0, operation_index=0),)})
    (path / "result.json").write_text(invalid.model_dump_json())
    with pytest.raises(ValueError, match="FIFO trace"):
        load_attempt(path)
    invalid = result.model_copy(update={"artifacts": tuple(ref for ref in result.artifacts if ref.path != "arithmetic-trace.json")})
    (path / "result.json").write_text(invalid.model_dump_json())
    with pytest.raises(ValueError, match="requires its family trace"):
        load_attempt(path)


def test_arithmetic_exact_scripted_replay_and_changed_identity(scripted_original, tmp_path):
    path, _ = scripted_original
    record = replay_attempt(path, tmp_path / "replay", runner=scripted_arithmetic)
    assert record.same_semantic_failure
    def changed(plan, output):
        return scripted_arithmetic(plan, output, change={"checker_hash": "c" * 64})
    with pytest.raises(ValueError, match="identity changed"):
        replay_attempt(path, tmp_path / "changed", runner=changed)


def test_arithmetic_reduction_revalidates_reset_width_and_drain(scripted_original, tmp_path):
    path, _ = scripted_original
    def checked_runner(plan, output):
        assert isinstance(plan, ArithmeticExperiment)
        assert ArithmeticExperiment.model_validate_json(plan.model_dump_json()) == plan
        return scripted_arithmetic(plan, output)
    record = reduce_failure(path, tmp_path / "reduced", runner=checked_runner, max_attempts=8)
    assert len(record.attempts) <= 8
    assert record.reduced_result.failure_signature.failure_class == "arithmetic.product"
    assert record.improved
    assert isinstance(ReductionRecord.model_validate_json(record.model_dump_json()).reduced_experiment, ArithmeticExperiment)


@pytest.mark.parametrize("change", ["class", "contract", "harness"])
def test_arithmetic_reducer_rejects_other_failures(scripted_original, tmp_path, change):
    path, _ = scripted_original
    def rejected(plan, output):
        result = scripted_arithmetic(plan, output,
            failure_class="arithmetic.valid" if change == "class" else "arithmetic.product",
            change={"contract_hash": "c" * 64} if change == "contract" else None)
        if change == "harness":
            return result.model_copy(update={"verdict": Verdict.HARNESS_ERROR, "failure_signature": None})
        return result
    record = reduce_failure(path, tmp_path / "rejected", runner=rejected, max_attempts=2)
    assert not record.improved


def validation(path, output, **kwargs):
    original = arithmetic_plan()
    neighbor_data = original.model_dump(mode="json")
    neighbor_data["operations"][1]["a"] = 1
    neighbor_data["experiment_id"] = "arithmetic-neighbor"
    neighbor = ArithmeticExperiment.model_validate(neighbor_data)
    regression = ArithmeticExperiment(experiment_id="arithmetic-regression", fixture_id=original.fixture_id,
        operations=({"op": "reset"}, {"op": "multiply", "a": 3, "b": 5}, {"op": "idle", "cycles": 2}))
    options = dict(candidate_fixture_id="multiplier_correct", original_runner=scripted_arithmetic,
        candidate_runner=lambda plan, out: scripted_arithmetic(plan, out, fail=False),
        neighboring_plans=(neighbor,), regression_plans=(regression,))
    options.update(kwargs)
    return validate_candidate(path, output, **options)


def test_arithmetic_candidate_exact_distinct_suites_and_frozen_checker(scripted_original, tmp_path):
    record = validation(scripted_original[0], tmp_path / "candidate")
    assert record.status == "validated_under_checks"
    assert len({json.dumps(plan.model_dump(mode="json")["operations"], sort_keys=True)
                for plan in record.candidate_experiments}) == 3
    roundtrip = CandidateValidation.model_validate_json(record.model_dump_json())
    assert all(isinstance(plan, ArithmeticExperiment) for plan in roundtrip.candidate_experiments)
    def bad_checker(plan, output):
        return scripted_arithmetic(plan, output, fail=False, change={"checker_hash": "c" * 64})
    assert validation(scripted_original[0], tmp_path / "bad-checker", candidate_runner=bad_checker).status == "failed_under_checks"


def test_candidate_cannot_cross_adapter_or_select_uninstalled_fixture(scripted_original, tmp_path):
    for fixture in ("fifo_correct", "uninstalled_multiplier"):
        with pytest.raises(ValueError):
            validation(scripted_original[0], tmp_path / "invalid", candidate_fixture_id=fixture)


@pytest.mark.skipif(not SAVED.exists(), reason="saved accepted arithmetic HDL artifacts unavailable")
@pytest.mark.parametrize("case", [0, 1, 2])
def test_saved_actual_arithmetic_load_and_export_read_only(case, tmp_path):
    path = SAVED / f"test_live_curated_multiplier_m{case}"
    before = {file.name: hashlib.sha256(file.read_bytes()).hexdigest() for file in path.iterdir() if file.is_file()}
    plan, result = load_attempt(path)
    assert isinstance(plan, ArithmeticExperiment)
    assert result.verdict == ("pass_within_scope" if case == 0 else "design_mismatch")
    export_bundle(path, tmp_path / f"accepted-{case}.zip")
    assert before == {file.name: hashlib.sha256(file.read_bytes()).hexdigest() for file in path.iterdir() if file.is_file()}


@pytest.mark.skipif(not SAVED.exists(), reason="saved accepted arithmetic HDL artifacts unavailable")
def test_native_replay_preflight_dispatch_with_stub_never_executes_simulator(tmp_path, monkeypatch):
    path = tmp_path / "snapshot"
    shutil.copytree(SAVED / "test_live_curated_multiplier_m2", path)
    descriptor = get_adapter("rtl.arithmetic.v1")
    plan, original = load_attempt(path)
    calls = []
    def stub_resolver(self, mode):
        assert self.adapter_id == "rtl.arithmetic.v1" and mode == "native"
        def scripted_callback(experiment, output):
            calls.append(experiment)
            return original.model_copy(update={"attempt_id": "scripted-preflight-only"})
        return scripted_callback
    monkeypatch.setattr(type(descriptor), "resolve_runner", stub_resolver)
    assert replay_attempt(path, tmp_path / "stub-output").same_semantic_failure
    assert calls == [plan]
    changed = original.model_copy(update={"identity": original.identity.model_copy(update={"source_hash": H})})
    (path / "result.json").write_text(changed.model_dump_json())
    with pytest.raises(ValueError, match="identity changed"):
        replay_attempt(path, tmp_path / "not-executed")
    assert len(calls) == 1


@pytest.mark.skipif(not SAVED.exists(), reason="saved accepted arithmetic HDL artifacts unavailable")
def test_native_replay_rejects_rehashed_stimulus_before_runner(tmp_path, monkeypatch):
    path = tmp_path / "changed-stimulus"
    shutil.copytree(SAVED / "test_live_curated_multiplier_m2", path)
    replace_artifact(path, "stimulus.json", b"[]")
    def must_not_resolve(*args):
        pytest.fail("native runner must not be resolved for changed exact stimulus")
    monkeypatch.setattr(type(get_adapter("rtl.arithmetic.v1")), "resolve_runner", must_not_resolve)
    with pytest.raises(ValueError, match="stored stimulus differs"):
        replay_attempt(path, tmp_path / "not-executed")
