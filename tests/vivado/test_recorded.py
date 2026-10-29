"""Recorded, redacted imports and adversarial synthetic mutations; no execution."""
from dataclasses import asdict
from hashlib import sha256
import json
from pathlib import Path
import shutil

import pytest

from veriforge.vivado import BatchRecipe, inspect_recorded_attempt
from veriforge.vivado import recorded

FIXTURES = Path(__file__).parent / "fixtures/recorded"


def test_actual_recorded_query_part_readiness_does_not_promote_design():
    r = inspect_recorded_attempt(FIXTURES / "query", BatchRecipe.DEVICE_QUERY)
    assert r.tool_version == "2026.1"
    assert r.selected_part == "xc7a200tsbg484-1"
    assert r.selected_part_readiness == "recorded_available"
    assert [p.count for p in r.parts] == [1, 1, 1]
    assert r.native_execution_status == "completed" and r.build_status == "not_run"
    assert r.project is None and not r.new_execution
    assert r.provenance == "recorded_import_not_new_execution"
    assert r.design_verdict == "not_evaluated" and r.functional_status == "not_run"


def test_actual_failed_synthesis_keeps_resource_build_and_verdict_separate():
    r = inspect_recorded_attempt(FIXTURES / "resource_limited_synthesis", BatchRecipe.EXERCISE3_POST_SYNTH)
    assert r.native_execution_status == "resource_limited"
    assert r.resource_limit == "result_directory_limit" and r.native_exit_code == 1
    assert r.storage_before_receipt.result_bytes == 6_418_119
    assert r.storage_budget.result_bytes == 4 * 1024 * 1024
    assert r.wall_seconds == pytest.approx(115.719)
    assert r.owned_processes_after_cleanup == 0
    assert r.build_status == "incomplete" and r.design_verdict == "not_evaluated"
    assert {p.availability for p in r.reports} == {"absent"}
    assert len(r.reports) == 6 and all(p.verdict == "not_evaluated" for p in r.reports)
    assert r.project.clock_period_ns == 10.0 and len(r.project.sources) == 3
    assert r.project.sources[-1].name == "nexys_video_exercise3.xdc"
    assert r.project.execute_status == r.repair_status == "not_validated"


@pytest.fixture
def bundle(tmp_path):
    destination = tmp_path / "private-directory"
    shutil.copytree(FIXTURES / "resource_limited_synthesis", destination)
    return destination


def receipt(bundle, **changes):
    path = bundle / "synthesis-receipt.json"
    value = json.loads(path.read_text())
    value.update(changes)
    path.write_text(json.dumps(value))


def log(bundle, extra):
    path = bundle / "stdout.log"
    raw = path.read_bytes() + extra.encode()
    path.write_bytes(raw)
    value = json.loads((bundle / "synthesis-receipt.json").read_text())
    value["artifacts"]["stdout.log"] = {"bytes": len(raw), "sha256": sha256(raw).hexdigest()}
    receipt(bundle, artifacts=value["artifacts"])


def inspect(bundle):
    return inspect_recorded_attempt(bundle, BatchRecipe.EXERCISE3_POST_SYNTH)


def test_serializable_projection_never_exports_private_paths_logs_or_unknown_keys(bundle):
    receipt(bundle, cwd="C:/Users/Private Person/Secret", argv=["secret-token"], password="never-export")
    result = json.dumps(asdict(inspect(bundle)))
    assert all(value not in result for value in ("Private Person", "secret-token", "never-export", "private-directory", "argv", "cwd"))


@pytest.mark.parametrize("field,value", [("limit_reason", "timeout"), ("active_owned_processes_after_cleanup", 2)])
def test_timeout_or_cleanup_failure_is_not_design_failure(bundle, field, value):
    receipt(bundle, **{field: value})
    r = inspect(bundle)
    assert r.native_execution_status == ("timed_out" if field == "limit_reason" else "cleanup_incomplete")
    assert r.design_verdict == "not_evaluated"


def test_forged_success_marker_does_not_override_resource_failure(bundle):
    markers = "VF_STAGE synthesis passed\n"
    markers += "".join(f"VF_REPORT {name} started\nVF_REPORT {name} unavailable\n" for name in recorded.REPORTS)
    log(bundle, markers + "VF_BUILD passed\n")
    r = inspect(bundle)
    assert r.native_execution_status == "resource_limited" and r.build_status == "incomplete"


@pytest.mark.parametrize("marker,expected", [("started", "incomplete"), ("unavailable", "unavailable"), ("captured", "missing")])
def test_report_absence_and_incomplete_are_explicit(bundle, marker, expected):
    log(bundle, "VF_REPORT cdc started\n" + (f"VF_REPORT cdc {marker}\n" if marker != "started" else ""))
    assert next(r for r in inspect(bundle).reports if r.report == "cdc").availability == expected


def test_captured_hash_bound_synthetic_report_is_still_not_a_verdict(bundle):
    raw = b"Synthetic report for import testing only; not a vendor result.\n"
    (bundle / "cdc.rpt").write_bytes(raw)
    value = json.loads((bundle / "synthesis-receipt.json").read_text())
    value["artifacts"]["cdc.rpt"] = {"bytes": len(raw), "sha256": sha256(raw).hexdigest()}
    receipt(bundle, artifacts=value["artifacts"])
    log(bundle, "VF_REPORT cdc started\nVF_REPORT cdc captured\n")
    r = next(r for r in inspect(bundle).reports if r.report == "cdc")
    assert r.availability == "captured" and r.verdict == "not_evaluated"
    (bundle / "cdc.rpt").write_text("tampered")
    assert next(r for r in inspect(bundle).reports if r.report == "cdc").availability == "unverified"


@pytest.mark.parametrize("mutation", ["stdout", "source", "clock", "recipe", "version", "duplicate_marker", "oversize", "nan", "bool"])
def test_malformed_or_inconsistent_evidence_fails_closed(bundle, mutation, monkeypatch):
    plan_path = bundle / "plan.json"
    plan = json.loads(plan_path.read_text())
    if mutation == "stdout":
        (bundle / "stdout.log").write_text("changed")
    elif mutation == "source":
        plan["sources"][0]["sha256"] = "0" * 64
    elif mutation == "clock":
        plan["source_manifest"]["clock_period_ns"] = 100.0
    elif mutation == "recipe":
        plan["script_sha256"] = "0" * 64
    elif mutation == "version":
        receipt(bundle, version=["2020.1"])
    elif mutation == "duplicate_marker":
        log(bundle, "VF_STAGE synthesis started\n")
    elif mutation == "oversize":
        monkeypatch.setattr(recorded, "MAX_RECORD_BYTES", 20)
    elif mutation == "nan":
        receipt(bundle, wall_seconds=float("nan"))
    else:
        receipt(bundle, exit_code=True)
    plan_path.write_text(json.dumps(plan))
    with pytest.raises(ValueError):
        inspect(bundle)


def test_part_query_count_drift_rejected(tmp_path):
    bundle = tmp_path / "query"
    shutil.copytree(FIXTURES / "query", bundle)
    path = bundle / "query-receipt.json"
    value = json.loads(path.read_text())
    value["parts"][0]["count"] = 0
    path.write_text(json.dumps(value))
    with pytest.raises(ValueError, match="part query mismatch"):
        inspect_recorded_attempt(bundle, BatchRecipe.DEVICE_QUERY)


def test_linked_report_rejected_without_reading_target(bundle, monkeypatch):
    (bundle / "cdc.rpt").write_text("synthetic")
    original = Path.is_symlink
    monkeypatch.setattr(Path, "is_symlink", lambda p: p.name == "cdc.rpt" or original(p))
    with pytest.raises(ValueError, match="linked recorded artifact"):
        inspect(bundle)


def test_recorded_storage_excess_cannot_be_reported_completed(bundle):
    receipt(bundle, limit_reason=None, exit_code=0)
    with pytest.raises(ValueError, match="storage excess"):
        inspect(bundle)


@pytest.mark.parametrize("folder,recipe,receiptname", [
    ("query", BatchRecipe.DEVICE_QUERY, "query-receipt.json"),
    ("resource_limited_synthesis", BatchRecipe.EXERCISE3_POST_SYNTH, "synthesis-receipt.json"),
])
def test_windows_vendor_line_endings_preserve_raw_identity(tmp_path, folder, recipe, receiptname):
    root = tmp_path / "crlf"
    shutil.copytree(FIXTURES / folder, root)
    raw = (root / "stdout.log").read_bytes().replace(b"\n", b"\r\n")
    (root / "stdout.log").write_bytes(raw)
    path = root / receiptname
    value = json.loads(path.read_text())
    value["artifacts"]["stdout.log"] = {"bytes": len(raw), "sha256": sha256(raw).hexdigest()}
    path.write_text(json.dumps(value))
    result = inspect_recorded_attempt(root, recipe)
    assert result.stdout_sha256 == sha256(raw).hexdigest() and result.tool_version == "2026.1"


@pytest.mark.parametrize("part", ["C:/Users/Private/Secret", "private-token", "xc7a200tsbg484-1"])
def test_synthesis_never_projects_part_query_payload(bundle, part):
    log(bundle, f"VF_PART {part} 1\n")
    with pytest.raises(ValueError, match="part query marker"):
        inspect(bundle)


def test_dangling_report_link_rejected_before_exists(bundle, monkeypatch):
    original = Path.is_symlink
    monkeypatch.setattr(Path, "is_symlink", lambda p: p.name == "cdc.rpt" or original(p))
    assert not (bundle / "cdc.rpt").exists()
    with pytest.raises(ValueError, match="linked"):
        inspect(bundle)


@pytest.mark.parametrize("markers", [
    "VF_STAGE synthesis started\nVF_STAGE synthesis passed\nVF_STAGE read_sources started\nVF_STAGE read_sources failed\nVF_BUILD passed\n",
    "VF_STAGE read_sources started\nVF_STAGE read_sources failed\nVF_STAGE synthesis started\n",
    "VF_STAGE read_sources started\nVF_STAGE read_sources passed\nVF_STAGE synthesis started\nVF_STAGE synthesis passed\nVF_BUILD passed\n",
])
def test_impossible_or_incomplete_success_lifecycle_rejected(bundle, markers):
    (bundle / "stdout.log").write_bytes(b"VF_VERSION 2026.1\n")
    log(bundle, markers)
    with pytest.raises(ValueError, match="lifecycle"):
        inspect(bundle)


def test_full_fixed_lifecycle_can_report_build_without_design_verdict(bundle):
    markers = "VF_STAGE synthesis passed\n"
    markers += "".join(f"VF_REPORT {name} started\nVF_REPORT {name} unavailable\n" for name in recorded.REPORTS)
    log(bundle, markers + "VF_BUILD passed\n")
    value = json.loads((bundle / "synthesis-receipt.json").read_text())
    value["storage_before_receipt"]["result_bytes"] = 100
    receipt(bundle, exit_code=0, limit_reason=None, storage_before_receipt=value["storage_before_receipt"])
    result = inspect(bundle)
    assert result.build_status == "reported_passed" and result.design_verdict == "not_evaluated"
