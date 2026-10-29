"""Independent hostile-record import tests; no vendor execution or app mutation."""
from dataclasses import asdict
import hashlib
import json
from pathlib import Path
import shutil

import pytest

from veriforge.vivado import BatchRecipe, inspect_recorded_attempt
from veriforge.vivado import recorded

ROOT = Path(__file__).resolve().parents[2]
FIXTURES = ROOT / "tests/vivado/fixtures/recorded"


@pytest.fixture
def bundle(tmp_path):
    dest = tmp_path / "synthetic-private-directory"
    shutil.copytree(FIXTURES / "resource_limited_synthesis", dest)
    return dest


def mutate_receipt(root, **changes):
    path = root / "synthesis-receipt.json"
    doc = json.loads(path.read_text())
    doc.update(changes)
    path.write_text(json.dumps(doc))


def replace_stdout(root, raw):
    (root / "stdout.log").write_bytes(raw)
    doc = json.loads((root / "synthesis-receipt.json").read_text())
    doc["artifacts"]["stdout.log"] = {"bytes": len(raw), "sha256": hashlib.sha256(raw).hexdigest()}
    mutate_receipt(root, artifacts=doc["artifacts"])


def inspect(root):
    return inspect_recorded_attempt(root, BatchRecipe.EXERCISE3_POST_SYNTH)


def test_projection_does_not_follow_receipt_paths_or_claim_current_source_proof(bundle):
    doc = json.loads((bundle / "synthesis-receipt.json").read_text())
    doc["artifacts"]["../../credential.txt"] = {"bytes": 999, "sha256": "0" * 64}
    mutate_receipt(bundle, artifacts=doc["artifacts"], argv=["private-token"], password="private-password")
    record = inspect(bundle)
    serialized = json.dumps(asdict(record))
    assert all(word not in serialized for word in ("credential", "private-token", "private-password", str(bundle)))
    assert record.project is not None
    assert not (bundle / record.project.sources[0].name).exists()
    assert record.identity_scope == "recorded_plan_receipt_agreement_not_current_source_bytes"
    assert record.project.execute_status == "not_validated"
    assert not record.new_execution and record.design_verdict == "not_evaluated"


@pytest.mark.parametrize("part", ["C:/Users/Private/Secret", "private-token-from-untrusted-log"])
def test_untrusted_synthesis_part_marker_cannot_escape_safe_projection(bundle, part):
    replace_stdout(bundle, (bundle / "stdout.log").read_bytes() + f"VF_PART {part} 1\n".encode())
    try:
        record = inspect(bundle)
    except ValueError:
        return
    assert part not in json.dumps(asdict(record)), "Unrestricted synthesis VF_PART marker leaks into safe output"


def test_dangling_report_link_rejected_before_missing_file_classification(bundle, monkeypatch):
    original = Path.is_symlink
    monkeypatch.setattr(Path, "is_symlink", lambda path: path == bundle / "cdc.rpt" or original(path))
    assert not (bundle / "cdc.rpt").exists()
    with pytest.raises(ValueError, match="linked"):
        inspect(bundle)


@pytest.mark.parametrize("field,value", [("active_owned_processes_after_cleanup", True),
    ("wall_seconds", float("inf")), ("exit_code", 1.0)])
def test_untrusted_counter_types_cannot_enter_projection(bundle, field, value):
    mutate_receipt(bundle, **{field: value})
    with pytest.raises(ValueError):
        inspect(bundle)


def test_impossible_stage_success_cannot_be_promoted_to_reported_passed(bundle):
    # Fixed recipe exits on read_sources failure and cannot synthesize first.
    raw = (b"VF_VERSION 2026.1\nVF_STAGE synthesis started\nVF_STAGE synthesis passed\n"
        b"VF_STAGE read_sources started\nVF_STAGE read_sources failed\nVF_BUILD passed\n")
    replace_stdout(bundle, raw)
    doc = json.loads((bundle / "synthesis-receipt.json").read_text())
    doc["storage_before_receipt"]["result_bytes"] = 100
    mutate_receipt(bundle, exit_code=0, limit_reason=None, storage_before_receipt=doc["storage_before_receipt"])
    try:
        record = inspect(bundle)
    except ValueError:
        return
    assert record.build_status != "reported_passed", "Conflicting stage order/failure cannot establish a successful fixed build"


@pytest.mark.parametrize("newline", [b"\n", b"\r\n"])
def test_native_newlines_keep_byte_identity_and_resource_not_design_verdict(bundle, newline):
    raw = (bundle / "stdout.log").read_bytes().replace(b"\r\n", b"\n").replace(b"\n", newline)
    replace_stdout(bundle, raw)
    record = inspect(bundle)
    assert record.stdout_sha256 == hashlib.sha256(raw).hexdigest()
    assert record.native_execution_status == "resource_limited"
    assert record.build_status == "incomplete" and record.design_verdict == "not_evaluated"


def test_report_marker_and_matching_bytes_never_mean_functional_success(bundle):
    raw = b"Synthetic non-vendor report: timing met\n"
    (bundle / "timing_summary.rpt").write_bytes(raw)
    doc = json.loads((bundle / "synthesis-receipt.json").read_text())
    doc["artifacts"]["timing_summary.rpt"] = {"bytes": len(raw), "sha256": hashlib.sha256(raw).hexdigest()}
    mutate_receipt(bundle, artifacts=doc["artifacts"])
    replace_stdout(bundle, (bundle / "stdout.log").read_bytes() + b"VF_REPORT timing_summary started\nVF_REPORT timing_summary captured\n")
    record = inspect(bundle)
    report = next(r for r in record.reports if r.report == "timing_summary")
    assert report.availability == "captured" and report.verdict == "not_evaluated"
    assert record.functional_status == "not_run" and record.repair_status == "not_validated"


def test_bounded_report_read_rejects_before_projection(bundle):
    (bundle / "cdc.rpt").write_bytes(b"x" * (recorded.MAX_RECORD_BYTES + 1))
    with pytest.raises(ValueError, match="exceeds byte limit"):
        inspect(bundle)
