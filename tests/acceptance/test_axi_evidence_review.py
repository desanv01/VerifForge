"""Independent review of saved native AXI evidence; never runs HDL or a model.

Private accepted recordings are optional outside the implementation checkout.
Adversarial cases modify temporary copies only, not accepted originals.
"""
import hashlib
import json
import shutil
from pathlib import Path
from zipfile import ZipFile

import pytest

from veriforge.adapters import get_adapter
from veriforge.axi_lite.contracts import AxiLiteContract, AxiLiteExperiment
from veriforge.contracts import canonical_hash
from veriforge.evidence import export_bundle, load_attempt, reduce_failure
from veriforge.evidence import workflow


ROOT = Path(__file__).resolve().parents[2]
SAVED = ROOT / ".coordination/U3-AXI-LIVE-RESUME-live1"
CASES = [(ROOT / ".coordination/VF055E-live1/test_live_curated_registers_di0/evidence", "pass_within_scope")] + [
    (SAVED / f"test_live_curated_registers_{case}/evidence", verdict)
    for case, verdict in (("or0", "pass_within_scope"), ("st0", "pass_within_scope"),
                         ("re0", "pass_within_scope"), ("or1", "property_failure"),
                         ("st1", "design_mismatch"), ("re1", "property_failure"))
]
COMPANIONS = ("check.json", "checker.py", "checker-contracts.py", "contract.json",
              "environment.json", "registers.sv", "stimulus.json")


def sha(raw):
    return hashlib.sha256(raw).hexdigest()


def encode(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":")).encode()


def read_json(path):
    return json.loads(path.read_bytes())


def inventory(path):
    return {str(file.relative_to(path)): sha(file.read_bytes())
            for file in path.rglob("*") if file.is_file()}


def require_saved(path):
    if not path.is_dir():
        pytest.skip("accepted native AXI recording unavailable in this checkout")


@pytest.fixture
def copied_failure(tmp_path):
    source = CASES[4][0]
    require_saved(source)
    destination = tmp_path / "copied-failure"
    shutil.copytree(source, destination)
    return destination


def replace_artifact(path, name, raw):
    result = read_json(path / "result.json")
    (path / name).write_bytes(raw)
    reference = next(ref for ref in result["artifacts"] if ref["path"] == name)
    reference.update(sha256=sha(raw), size_bytes=len(raw))
    (path / "result.json").write_bytes(encode(result))


@pytest.mark.parametrize("directory,verdict", CASES, ids=["directed", "order-control", "strobe-control",
    "response-control", "order-mutant", "strobe-mutant", "response-mutant"])
def test_actual_recording_identity_prefix_and_native_export(directory, verdict, tmp_path):
    require_saved(directory)
    before = inventory(directory)
    experiment, result = load_attempt(directory)
    descriptor = get_adapter("rtl.axi_lite.v1")
    assert isinstance(experiment, AxiLiteExperiment)
    assert result.execution_status == "completed" and result.verdict == verdict and result.trace == ()
    rows = read_json(directory / "observed.json")["observations"]
    checked = read_json(directory / "check.json")
    assert len(rows) == experiment.cycle_count == result.usage.cycles
    assert [row["cycle"] for row in rows] == list(range(experiment.cycle_count))
    if checked["failure"]:
        failure, signature = checked["failure"], result.failure_signature
        assert 0 < checked["checked_cycles"] == failure["cycle"] + 1 < len(rows)
        assert (failure["requirement_id"], failure["failure_class"], failure["cycle"],
                failure["expected"], failure["observed"]) == (
            signature.requirement_id, signature.failure_class, signature.first_cycle,
            signature.expected, signature.observed)
        assert signature.signature_hash == sha(encode({"contract": result.identity.contract_hash,
            "checker": result.identity.checker_hash, "requirement": signature.requirement_id,
            "class": signature.failure_class}))
    else:
        assert checked["checked_cycles"] == len(rows) and result.failure_signature is None
    environment = read_json(directory / "environment.json")
    contract = AxiLiteContract.model_validate(read_json(directory / "contract.json"))
    assert result.identity.experiment_hash == canonical_hash(experiment)
    assert result.identity.checker_hash == sha((directory / "checker.py").read_bytes()) == contract.checker.sha256
    assert result.identity.contract_hash == canonical_hash(contract)
    assert result.identity.environment_hash == sha((directory / "environment.json").read_bytes())
    assert environment["contracts_hash"] == sha((directory / "checker-contracts.py").read_bytes())
    assert environment["tools"] == [tool.model_dump(mode="json") for tool in result.tool_versions]
    assert contract.parameters == experiment.parameters and contract.contract_id == experiment.contract_id
    assert result.identity.source_hash == sha(encode({"source": sha((directory / "registers.sv").read_bytes()),
        "variant": descriptor.fixture_source(experiment.fixture_id)["variant"],
        "parameters": experiment.parameters.model_dump()}))
    assert read_json(directory / "stimulus.json") == [cycle.model_dump(mode="json") for cycle in experiment.cycles]
    bundle = export_bundle(directory, tmp_path / "review.zip")
    with ZipFile(bundle) as archive:
        manifest = json.loads(archive.read("manifest.json"))
        selected = {ref.path: ref for ref in result.artifacts if ref.path in descriptor.portable_artifacts}
        assert set(archive.namelist()) == {"manifest.json", "experiment.json", "REPORT.txt"} | {
            "artifacts/" + name for name in selected}
        assert manifest["trace"] == [] and manifest["adapter_id"] == descriptor.adapter_id
        assert manifest["family_trace"] == {"artifact": "artifacts/observed.json", "schema_version": "axi_lite.trace.v1"}
        for name, ref in selected.items():
            raw = archive.read("artifacts/" + name)
            assert raw == (directory / name).read_bytes() and sha(raw) == ref.sha256 and len(raw) == ref.size_bytes
    assert inventory(directory) == before
    print(f"{directory.parent.name}: {verdict}, executed={len(rows)}, checked={checked['checked_cycles']}")


@pytest.mark.parametrize("name", COMPANIONS)
def test_companions_are_rechecked_with_manifest_bounds(copied_failure, monkeypatch, name):
    result = read_json(copied_failure / "result.json")
    ref = next(ref for ref in result["artifacts"] if ref["path"] == name)
    original_read, calls = workflow._read, []

    def read_with_race(path, limit):
        raw = original_read(path, limit)
        if path.name == name:
            calls.append(limit)
            if len(calls) == 2:
                # Same-size data change after the initial manifest scan.
                return bytes([raw[0] ^ 1]) + raw[1:]
        return raw

    monkeypatch.setattr(workflow, "_read", read_with_race)
    with pytest.raises(ValueError, match="companion artifact changed"):
        load_attempt(copied_failure)
    assert calls == [ref["size_bytes"], ref["size_bytes"]]


def test_missing_manifest_companion_cannot_be_read_from_loose_file(copied_failure):
    result = read_json(copied_failure / "result.json")
    result["artifacts"] = [ref for ref in result["artifacts"] if ref["path"] != "checker-contracts.py"]
    (copied_failure / "result.json").write_bytes(encode(result))
    with pytest.raises(ValueError, match="companion artifact is missing"):
        load_attempt(copied_failure)


@pytest.mark.parametrize("mutation", ["truncate-execution", "checked-full-execution", "usage-prefix"])
def test_failure_prefix_cannot_replace_full_execution(copied_failure, mutation):
    checked = read_json(copied_failure / "check.json")
    observed = read_json(copied_failure / "observed.json")
    assert checked["checked_cycles"] < len(observed["observations"])
    if mutation == "truncate-execution":
        observed["observations"] = observed["observations"][:checked["checked_cycles"]]
        replace_artifact(copied_failure, "observed.json", encode(observed))
    elif mutation == "checked-full-execution":
        checked["checked_cycles"] = len(observed["observations"])
        replace_artifact(copied_failure, "check.json", encode(checked))
    else:
        result = read_json(copied_failure / "result.json")
        result["usage"]["cycles"] = checked["checked_cycles"]
        (copied_failure / "result.json").write_bytes(encode(result))
    with pytest.raises(ValueError, match="cycles differ|checked failure differs"):
        load_attempt(copied_failure)


def test_historical_python_snapshots_are_data_and_never_executed(copied_failure, tmp_path):
    for name in ("checker.py", "checker-contracts.py"):
        replace_artifact(copied_failure, name, (copied_failure / name).read_bytes() +
                         b'\nraise AssertionError("copied snapshot executed")\n')
    contract = read_json(copied_failure / "contract.json")
    contract["checker"]["sha256"] = sha((copied_failure / "checker.py").read_bytes())
    replace_artifact(copied_failure, "contract.json", encode(contract))
    environment = read_json(copied_failure / "environment.json")
    environment["contracts_hash"] = sha((copied_failure / "checker-contracts.py").read_bytes())
    replace_artifact(copied_failure, "environment.json", encode(environment))
    result = read_json(copied_failure / "result.json")
    identity, failure = result["identity"], result["failure_signature"]
    identity.update(checker_hash=contract["checker"]["sha256"],
                    contract_hash=canonical_hash(AxiLiteContract.model_validate(contract)),
                    environment_hash=sha((copied_failure / "environment.json").read_bytes()))
    failure["signature_hash"] = sha(encode({"contract": identity["contract_hash"],
        "checker": identity["checker_hash"], "requirement": failure["requirement_id"], "class": failure["failure_class"]}))
    (copied_failure / "result.json").write_bytes(encode(result))
    load_attempt(copied_failure)
    with ZipFile(export_bundle(copied_failure, tmp_path / "inert-snapshots.zip")) as archive:
        assert b"raise AssertionError" in archive.read("artifacts/checker.py")


def test_axi_reducer_rejects_before_output_or_runner(copied_failure, tmp_path):
    output = tmp_path / "must-not-exist"
    def forbidden(*args, **kwargs):
        pytest.fail("AXI reducer invoked a runner")
    with pytest.raises(NotImplementedError, match="AXI cycle reduction"):
        reduce_failure(copied_failure, output, runner=forbidden)
    assert not output.exists()
