"""Independent historical import review; inert bytes only, no database/native run."""
import hashlib
import json
import os
from pathlib import Path
import socket
import sqlite3
import subprocess

import pytest

from veriforge.contracts.native_tools import HostedOrigin, RiscvRecordedReport
from veriforge.system_investigation import native_tool_import as importer, records

ROOT = Path(__file__).resolve().parents[2]
RV_MANIFEST = "474e91456137cc12aecae1b28bd64fae43c8a701381041460bb52282685e3f23"
FW_MANIFEST = "fc727ccb8d52b1fb1b76e1dddf95b647954a7f0779b95f31254addc36b131bb3"
PRODUCERS = ("src/veriforge/riscv/runner.py", "src/veriforge/riscv/__main__.py")
FREEZE = {
    "src/veriforge/system_investigation/native_tool_import.py": "6059a25c014ad164c7c128f877862f039fec7864b504ed06ae1dca7bc30a4166",
    "tests/system_investigation/test_native_import_compatibility.py": "b35b98dfbd0f67d7f47e35573e9fba2a7e664bc5496728924d931a8506c0b0ae",
    PRODUCERS[0]: "80524f263ebf569f0d5f2fc989f82dbc3993e8e7b2e24490c96563435e0045e5",
    PRODUCERS[1]: "439e8c8bfdb54fca6ba271d6c57e685b94b3bf530277b0f69c372af6747c851d",
    "src/veriforge/system_investigation/native_tool_records.py": "083e9202c0f601ff1b80de7b6010e2aca250c1e5d33bf4de6f92c15a4996dea8",
    "src/veriforge/contracts/native_tools.py": "7060474806480b7a3cd92a63ce0bc4d99b46c4731c258a42190605d8832e9471",
}


def sha(raw):
    return hashlib.sha256(raw).hexdigest()


@pytest.fixture(autouse=True)
def no_execution_or_database(monkeypatch):
    def forbidden(*args, **kwargs):
        pytest.fail("Compatibility review attempted process/network/database execution")
    monkeypatch.setattr(subprocess, "run", forbidden)
    monkeypatch.setattr(subprocess, "Popen", forbidden)
    monkeypatch.setattr(socket, "create_connection", forbidden)
    monkeypatch.setattr(sqlite3, "connect", forbidden)


@pytest.fixture(scope="module")
def retained():
    result = {}
    for domain, variable in (("riscv", "VERIFORGE_TEST_RISCV_BUNDLE"), ("firmware", "VERIFORGE_TEST_FIRMWARE_BUNDLE")):
        directory = os.environ.get(variable)
        if not directory:
            pytest.skip("Explicit inert retained-bundle paths required for independent byte review")
        result[domain] = (Path(directory), importer._snapshot(directory))
    return result


def test_exact_review_boundary_and_raw_lf_pins():
    for relative, expected in FREEZE.items():
        raw = (ROOT / relative).read_bytes()
        assert sha(raw) == sha(raw.replace(b"\r\n", b"\n")) == expected


@pytest.mark.parametrize("domain,count", [("riscv", 102), ("firmware", 199)])
def test_accepted_original_bundles_prepare_without_rewriting_evidence(retained, domain, count):
    directory, before = retained[domain]
    family = "riscv-40920450439" if domain == "riscv" else "firmware-40929665294"
    prepared = importer.prepare_native_bundle(family=family, bundle_root=directory,
        project_id="review-recorded-project", revision_id="review-recorded-revision")
    admitted = prepared.admitted
    assert len(admitted.artifacts) == count
    assert importer.preparation_summary(prepared)["installed_source_policy"] == "native-import.installed-source.v1"
    blobs = {records._key(item.reference): item.data for item in admitted.artifacts}
    for binding in admitted.native_report.selection.selected:
        if binding.provenance == "producer":
            assert blobs[records._key(binding.ref)] == before[binding.original_relative_path]
            assert binding.ref.sha256 == sha(before[binding.original_relative_path])
            assert binding.ref.revision_id == admitted.native_report.selection.origin.source_commit
    assert set(admitted.native_report.selection.omitted_paths) == set(before) - {
        item.original_relative_path for item in admitted.native_report.selection.selected if item.provenance == "producer"}
    if domain == "riscv":
        assert [case.locator.native_attempt_id for case in admitted.native_records] == [
            "attempt-ba01be2c331649828d66ffcca63365ba", "attempt-3507c622a1f143d894f819a0958c0f6a", "attempt-e845135395204d61b526d707bae1ef2f"]
        assert [(case.retired_count, case.cycles) for case in admitted.native_records] == [(17, 68), (14, 59), (17, 68)]
        for case in admitted.native_records:
            result = json.loads(before[case.locator.case_label + "/result.json"])
            assert case.identity.model_dump(mode="json") == result["identity"]
            assert case.locator.raw_result_ref.sha256 == sha(before[case.locator.case_label + "/result.json"])
    else:
        assert all(case.locator.execution_ref.attempt_id is None for case in admitted.native_records)
        assert [case.mailbox.value for case in admitted.native_records] == [1, 2, 1, 2, 1]
    wire = records.serialize_investigation(admitted)
    assert records.read_investigation(wire, admitted.artifacts, authority=prepared.authority).envelope == admitted.envelope
    assert importer._snapshot(directory) == before


@pytest.mark.parametrize("manifest", [None, "0" * 64, FW_MANIFEST])
def test_wrong_or_absent_manifest_cannot_waive_old_producer_drift(retained, manifest):
    source = json.loads(retained["riscv"][1]["source-manifest.json"])
    with pytest.raises(importer.NativeImportError, match="installed_domain_version_differs"):
        importer._installed(source, source_manifest_hash=manifest)


@pytest.mark.parametrize("relative", PRODUCERS)
def test_unreviewed_installed_producer_and_wrong_historical_digest_are_rejected(retained, monkeypatch, relative):
    source = json.loads(retained["riscv"][1]["source-manifest.json"])
    read = importer._read_regular
    target = (ROOT / relative).absolute()
    monkeypatch.setattr(importer, "_read_regular", lambda path:
        read(path) + b"\nUNREVIEWED_PRODUCER" if Path(path).absolute() == target else read(path))
    with pytest.raises(importer.NativeImportError, match="installed_domain_version_differs"):
        importer._installed(source, source_manifest_hash=RV_MANIFEST)
    monkeypatch.setattr(importer, "_read_regular", read)
    source[relative] = "f" * 64
    with pytest.raises(importer.NativeImportError, match="installed_domain_version_differs"):
        importer._installed(source, source_manifest_hash=RV_MANIFEST)


def test_historical_installed_bytes_still_work_without_compatibility_override(retained, monkeypatch):
    raw = retained["riscv"][1]
    source = json.loads(raw["source-manifest.json"])
    read = importer._read_regular
    originals = {(ROOT / name).absolute(): raw["source/" + name] for name in PRODUCERS}
    monkeypatch.setattr(importer, "_read_regular", lambda path: originals.get(Path(path).absolute(), None) or read(path))
    assert importer._installed(source) == importer._VALIDATOR_HASH


@pytest.mark.parametrize("relative", ["src/veriforge/riscv/" + name for name in
    ("checker.py", "trace.py", "contracts.py", "profile.py", "memory.py", "sim_test.py", "sim_job.py")])
def test_producer_policy_does_not_waive_any_immutable_runtime_dependency(retained, monkeypatch, relative):
    source = json.loads(retained["riscv"][1]["source-manifest.json"])
    assert relative in source
    read = importer._read_regular
    target = (ROOT / relative).absolute()
    monkeypatch.setattr(importer, "_read_regular", lambda path:
        b"MUTATED_IMMUTABLE_DEPENDENCY" if Path(path).absolute() == target else read(path))
    with pytest.raises(importer.NativeImportError, match="installed_domain_version_differs"):
        importer._installed(source, source_manifest_hash=RV_MANIFEST)


@pytest.mark.parametrize("damage", ["raw_producer", "rehashed_source", "added_fourth_case", "rehashed_fourth_case"])
def test_reviewed_inventory_does_not_accept_rehashed_or_extra_historical_inputs(retained, damage):
    raw = dict(retained["riscv"][1])
    if damage in ("raw_producer", "rehashed_source"):
        path = "source/src/veriforge/riscv/runner.py"
        raw[path] += b"\nFORGED_HISTORICAL_PRODUCER"
        if damage == "rehashed_source":
            source = json.loads(raw["source-manifest.json"])
            source[PRODUCERS[0]] = sha(raw[path])
            raw["source-manifest.json"] = json.dumps(source, sort_keys=True).encode()
    else:
        raw["control-saved-replay/saved-program.json"] = b'{"new":"unsupported fourth case"}'
    if damage in ("rehashed_source", "rehashed_fourth_case"):
        raw["receipt-sha256.json"] = json.dumps({name: sha(data) for name, data in raw.items()
            if name != "receipt-sha256.json"}, sort_keys=True).encode()
    with pytest.raises(importer.NativeImportError):
        importer._inventory(importer._CASES["riscv-40920450439"], raw)


def test_new_run_and_four_case_report_remain_explicitly_unsupported(retained):
    assert set(importer._CASES) == {"riscv-40920450439", "firmware-40929665294"}
    assert [item[0] for item in importer._RV_CASES] == ["control", "mutant", "control-replay"]
    metadata = RiscvRecordedReport.model_fields["cases"].metadata
    assert any(getattr(item, "max_length", None) == 3 for item in metadata)
    receipt = importer._CASES["riscv-40920450439"]
    ref = next(iter(importer._Assembler(receipt, retained["riscv"][1], "project", "revision").references.values()))
    ref = ref.model_copy(update={"attempt_id": None})
    with pytest.raises(ValueError):
        HostedOrigin(source_commit=receipt.source_commit, run_id=40999999999, artifact_id=receipt.artifact_id,
            extracted_receipt_ref=ref)
