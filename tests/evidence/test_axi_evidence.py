"""AXI evidence: synthetic bus observations and read-only accepted artifacts, no HDL."""
import hashlib
import json
from pathlib import Path

import pytest

from veriforge.adapters import get_adapter
from veriforge.axi_lite import contracts, oracle
from veriforge.axi_lite.contracts import AxiLiteExperiment
from veriforge.contracts import (
    ArtifactReference, AttemptResult, EvidenceIdentity, FailureSignature, ResourceUsage,
    ToolVersion, TraceSample, canonical_hash,
)
from veriforge.evidence import (
    CandidateValidation, export_bundle, load_attempt, portable_artifact_bytes,
    reduce_failure, replay_attempt, validate_candidate,
)
from veriforge.evidence.families import validate_family_trace

ROOT = Path(__file__).resolve().parents[2]
LIVE = ROOT / ".coordination/U3-AXI-LIVE-RESUME-live1"
OLD = ROOT / ".coordination/VF055E-live1/test_live_curated_registers_di0/evidence"
CASES = [(OLD, "pass_within_scope")] + [
    (LIVE / f"test_live_curated_registers_{case}/evidence", verdict)
    for case, verdict in (("or0", "pass_within_scope"), ("st0", "pass_within_scope"),
        ("re0", "pass_within_scope"), ("or1", "property_failure"),
        ("st1", "design_mismatch"), ("re1", "property_failure"))]


def digest(raw):
    return hashlib.sha256(raw).hexdigest()


def encoded(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":")).encode()


def plan():
    return AxiLiteExperiment(experiment_id="scripted-axi-evidence", fixture_id="axil_aw_w_order_bug",
        cycles=({"reset_n": False}, {}, {}))


def scripted(experiment, output, *, fail=None):
    """Generated observations, not DUT execution; checker is fixed packaged code."""
    output = Path(output)
    output.mkdir(parents=True)
    if fail is None:
        fail = experiment.fixture_id != "axil_registers_correct"
    rows = [{"cycle": index, "aw_ready": cycle.reset_n, "w_ready": cycle.reset_n,
             "b_valid": False, "b_resp": 0, "ar_ready": cycle.reset_n,
             "r_valid": False, "r_data": 0, "r_resp": 0} for index, cycle in enumerate(experiment.cycles)]
    if fail:
        rows[2]["b_valid"] = True  # Deliberately synthetic unsolicited response.
    native = {"schema_version": "axi_lite.trace.v1", "observations": rows}
    checked = oracle.check(experiment, native)
    descriptor = get_adapter("rtl.axi_lite.v1")
    contract = descriptor.contract_for(experiment)
    versions = (ToolVersion(name="scripted-unit-no-hdl", version="1"),)
    environment = encoded({"contracts_hash": digest(Path(contracts.__file__).read_bytes()),
                           "tools": [tool.model_dump(mode="json") for tool in versions]})
    files = {"experiment.json": experiment.model_dump_json().encode(), "observed.json": encoded(native),
        "check.json": checked.model_dump_json().encode(), "contract.json": contract.model_dump_json().encode(),
        "checker.py": Path(oracle.__file__).read_bytes(), "checker-contracts.py": Path(contracts.__file__).read_bytes(),
        "registers.sv": (ROOT / "targets/axi_lite/registers.sv").read_bytes(),
        "environment.json": environment, "stimulus.json": encoded([cycle.model_dump(mode="json") for cycle in experiment.cycles])}
    refs = []
    for name, raw in files.items():
        (output / name).write_bytes(raw)
        refs.append(ArtifactReference(artifact_id="scripted-" + str(len(refs)), path=name, sha256=digest(raw),
                                      size_bytes=len(raw), media_type="text/plain"))
    identity = EvidenceIdentity(**descriptor.expected_identity(experiment), environment_hash=digest(environment))
    failure = checked.failure
    signature = FailureSignature(requirement_id=failure.requirement_id, failure_class=failure.failure_class,
        first_cycle=failure.cycle, expected=failure.expected, observed=failure.observed,
        signature_hash=digest(encoded({"contract": identity.contract_hash, "checker": identity.checker_hash,
            "requirement": failure.requirement_id, "class": failure.failure_class}))) if failure else None
    result = AttemptResult(attempt_id=output.name, experiment_id=experiment.experiment_id,
        execution_status="completed", verdict=checked.verdict, identity=identity,
        failure_signature=signature, artifacts=tuple(refs), tool_versions=versions,
        usage=ResourceUsage(cycles=len(rows)), uncertainties=("Synthetic observations; no simulator executed.",))
    (output / "result.json").write_text(result.model_dump_json())
    return result


@pytest.fixture
def original(tmp_path):
    directory = tmp_path / "scripted-original"
    result = scripted(plan(), directory)
    return directory, result


def replace_artifact(directory, name, raw):
    result = AttemptResult.model_validate_json((directory / "result.json").read_bytes())
    (directory / name).write_bytes(raw)
    refs = tuple(ref.model_copy(update={"sha256": digest(raw), "size_bytes": len(raw)}) if ref.path == name else ref
                 for ref in result.artifacts)
    (directory / "result.json").write_text(result.model_copy(update={"artifacts": refs}).model_dump_json())


def test_native_axi_load_export_and_verified_portable_snapshots(original, tmp_path):
    from zipfile import ZipFile
    directory, result = original
    experiment, loaded = load_attempt(directory)
    assert isinstance(experiment, AxiLiteExperiment) and loaded == result and not loaded.trace
    exported = export_bundle(directory, tmp_path / "axi.zip")
    with ZipFile(exported) as archive:
        metadata = json.loads(archive.read("manifest.json"))
        assert metadata["adapter_id"] == "rtl.axi_lite.v1" and metadata["trace"] == []
        assert metadata["family_trace"] == {"schema_version": "axi_lite.trace.v1", "artifact": "artifacts/observed.json"}
        assert "artifacts/registers.sv" in archive.namelist()
        assert "artifacts/checker-contracts.py" in archive.namelist()
        assert "artifacts/check.json" in archive.namelist()
        assert "artifacts/fifo.sv" not in archive.namelist() and "artifacts/environment.json" not in archive.namelist()
    for name in ("observed.json", "check.json", "registers.sv", "checker-contracts.py"):
        ref = next(ref for ref in result.artifacts if ref.path == name)
        assert portable_artifact_bytes(directory, ref.artifact_id) == (directory / name).read_bytes()


@pytest.mark.parametrize("mutation", ["schema", "fifo", "bool", "response", "data", "missing-field", "cycle", "missing-row", "duplicate-key"])
def test_rehashed_observations_do_not_bypass_native_shape_and_cycle_checks(original, mutation):
    directory, _ = original
    data = json.loads((directory / "observed.json").read_bytes())
    if mutation == "schema": data["schema_version"] = "arithmetic.trace.v1"
    elif mutation == "fifo": data["observations"][0]["empty"] = True
    elif mutation == "bool": data["observations"][0]["b_valid"] = 0
    elif mutation == "response": data["observations"][0]["r_resp"] = 4
    elif mutation == "data": data["observations"][0]["r_data"] = 2**32
    elif mutation == "missing-field": data["observations"][0].pop("b_resp")
    elif mutation == "cycle": data["observations"][1]["cycle"] = 0
    elif mutation == "missing-row": data["observations"].pop()
    raw = encoded(data)
    if mutation == "duplicate-key":
        raw = raw.replace(b'"schema_version":"axi_lite.trace.v1"',
                          b'"schema_version":"axi_lite.trace.v1","schema_version":"axi_lite.trace.v1"')
    replace_artifact(directory, "observed.json", raw)
    with pytest.raises(ValueError): load_attempt(directory)


@pytest.mark.parametrize("mutation", ["verdict", "checked-prefix", "requirement", "cycle", "expected", "register-width", "extra"])
def test_rehashed_check_outcomes_remain_bound_to_result_signature(original, mutation):
    directory, _ = original
    data = json.loads((directory / "check.json").read_bytes())
    if mutation == "verdict": data["verdict"] = "design_mismatch"
    elif mutation == "checked-prefix": data["checked_cycles"] = 1
    elif mutation == "requirement": data["failure"]["requirement_id"] = "AXIL_RESET"
    elif mutation == "cycle": data["failure"]["cycle"] = 4095
    elif mutation == "expected": data["failure"]["expected"] = "changed"
    elif mutation == "register-width": data["registers"][0] = 2**32
    elif mutation == "extra": data["extra"] = "ignored?"
    replace_artifact(directory, "check.json", encoded(data))
    with pytest.raises(ValueError): load_attempt(directory)


@pytest.mark.parametrize("name", ["checker.py", "checker-contracts.py", "contract.json", "environment.json", "registers.sv", "stimulus.json"])
def test_rehashed_companions_are_bound_to_oracle_schema_environment_source_and_plan(original, name):
    directory, _ = original
    raw = (directory / name).read_bytes()
    if name == "stimulus.json": raw = b"[]"
    elif name == "contract.json":
        data = json.loads(raw); data["parameters"]["response_bound"] = 5; raw = encoded(data)
    else: raw += b" "
    replace_artifact(directory, name, raw)
    with pytest.raises(ValueError): load_attempt(directory)


def test_common_fifo_trace_is_rejected_and_axi_reduction_is_explicitly_unsupported(original, tmp_path):
    directory, result = original
    def must_not_run(*args): pytest.fail("unaccepted AXI reducer must not execute")
    with pytest.raises(NotImplementedError, match="AXI cycle reduction"):
        reduce_failure(directory, tmp_path / "must-not-create", runner=must_not_run)
    assert not (tmp_path / "must-not-create").exists()
    (directory / "result.json").write_text(result.model_copy(update={"trace": (TraceSample(cycle=0, operation_index=0),)}).model_dump_json())
    with pytest.raises(ValueError, match="FIFO trace"):
        load_attempt(directory)


def test_typed_native_replay_dispatch_uses_stub_not_simulator(original, tmp_path, monkeypatch):
    calls = []
    def resolver(descriptor, mode):
        assert descriptor.adapter_id == "rtl.axi_lite.v1" and mode == "native"
        def stub(experiment, output):
            calls.append(experiment)
            return scripted(experiment, output)
        return stub
    monkeypatch.setattr(type(get_adapter("rtl.axi_lite.v1")), "resolve_runner", resolver)
    assert replay_attempt(original[0], tmp_path / "stubbed-replay").same_semantic_failure
    assert len(calls) == 1 and isinstance(calls[0], AxiLiteExperiment)


def test_axi_curated_candidate_records_keep_native_cycles_and_source_provenance(original, tmp_path):
    neighbor = plan().model_copy(update={"experiment_id": "scripted-axi-neighbor", "cycles": (*plan().cycles, plan().cycles[-1])})
    regression = AxiLiteExperiment(experiment_id="scripted-axi-regression", fixture_id=plan().fixture_id,
        cycles=({"reset_n": False}, {}, {}, {"reset_n": False}, {}))
    record = validate_candidate(original[0], tmp_path / "scripted-validation", candidate_fixture_id="axil_registers_correct",
        original_runner=scripted, candidate_runner=lambda exp, out: scripted(exp, out, fail=False),
        neighboring_plans=(neighbor,), regression_plans=(regression,))
    assert record.status == "validated_under_checks" and record.source_review_status == "available"
    assert record.source_review.adapter_id == "rtl.axi_lite.v1" and not record.source_review.source_bytes_changed
    assert all(isinstance(exp, AxiLiteExperiment) for exp in CandidateValidation.model_validate_json(record.model_dump_json()).candidate_experiments)
    assert all(not result.trace for result in (record.witness, *record.neighbors, *record.regressions))


@pytest.mark.parametrize("directory,verdict", CASES, ids=["old-directed", "order-control", "strobe-control", "response-control", "order-mutant", "strobe-mutant", "response-mutant"])
def test_saved_actual_axi_load_export_preserves_all_original_artifacts(directory, verdict, tmp_path):
    if not directory.exists(): pytest.skip("saved accepted native AXI artifacts unavailable")
    before = {file.name: digest(file.read_bytes()) for file in directory.iterdir() if file.is_file()}
    experiment, result = load_attempt(directory)
    assert isinstance(experiment, AxiLiteExperiment) and result.verdict == verdict and not result.trace
    export_bundle(directory, tmp_path / "accepted-axi.zip")
    assert before == {file.name: digest(file.read_bytes()) for file in directory.iterdir() if file.is_file()}
    if result.failure_signature:
        checked = json.loads((directory / "check.json").read_bytes())
        assert checked["checked_cycles"] == result.failure_signature.first_cycle + 1 <= result.usage.cycles


def test_unknown_family_trace_schema_has_no_dynamic_fallback(original, monkeypatch):
    descriptor = get_adapter("rtl.axi_lite.v1")
    from dataclasses import replace
    from veriforge.evidence import families
    monkeypatch.setattr(families, "descriptor_for", lambda exp: replace(descriptor, trace_schema="unreviewed.trace.v1"))
    with pytest.raises(ValueError, match="no accepted evidence validator"):
        validate_family_trace(b"{}", plan(), original[1])
