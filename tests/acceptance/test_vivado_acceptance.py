"""Independent import-only checks; synthetic logs are not Vivado evidence."""
from hashlib import sha256
import json
from pathlib import Path

import pytest

from veriforge.vivado import Recipe, import_log, prepare_repair_pair
from veriforge.vivado.adapter import MAX_LOG_BYTES


def imported(tmp_path, raw):
    original = tmp_path / "input.log"
    original.write_bytes(raw)
    report = import_log(original, tmp_path / "imports")
    artifacts = {p.name: p.read_text(encoding="utf-8") for p in Path(report.directory).iterdir()}
    assert original.read_bytes() == raw
    assert report.original_sha256 == sha256(raw).hexdigest()
    return report, artifacts


def test_import_of_script_text_never_executes_or_claims_functional_validation(tmp_path):
    marker = tmp_path / "must-not-exist"
    text = f'exec touch {marker}\nVF_BUILD passed\n'
    report, artifacts = imported(tmp_path, text.encode())
    assert not marker.exists()
    assert report.build_status == "reported_passed"
    assert report.tool_execution_status == "unverified_import"
    assert report.provenance == "imported_unverified"
    assert report.functional_status == "not_run"
    assert json.loads(artifacts["observations.json"])["functional_status"] == "not_run"


def test_failed_stage_without_error_diagnostic_overrides_success(tmp_path):
    report, _ = imported(tmp_path, b"VF_BUILD passed\nVF_STAGE synthesis failed\n")
    assert report.build_status == "reported_failed"
    assert report.failure_signature
    assert report.functional_status == "not_run"


def test_input_size_bound_applies_to_bytes_before_decode_or_output(tmp_path):
    source = tmp_path / "input.log"
    source.write_bytes(b"\xff" * (MAX_LOG_BYTES + 1))
    with pytest.raises(ValueError, match="1 MiB"):
        import_log(source, tmp_path / "imports")
    assert not (tmp_path / "imports").exists()


def test_candidate_edit_cannot_change_baseline_bytes(tmp_path):
    baseline, candidate = prepare_repair_pair(tmp_path, Recipe.WRONG_TOP)
    original = Path(baseline.directory)
    snapshot = {p.name: p.read_bytes() for p in original.iterdir()}
    candidate_source = Path(candidate.directory) / "counter.v"
    candidate_source.write_text("// independent candidate modification\n")
    assert {p.name: p.read_bytes() for p in original.iterdir()} == snapshot
    assert candidate.parent_attempt_id == baseline.attempt_id
    assert baseline.build_status == candidate.build_status == "not_run"
    assert baseline.functional_status == candidate.functional_status == "not_run"


def test_json_encoded_credentials_are_removed_from_all_import_artifacts(tmp_path):
    secrets = {"api_key": "SYNTHETIC_PRIVATE_KEY", "token": "SYNTHETIC_PRIVATE_TOKEN",
               "LM_LICENSE_FILE": "2100@SYNTHETIC_PRIVATE_LICENSE_HOST"}
    _, artifacts = imported(tmp_path, ("ERROR: [Common 17-1] " + json.dumps(secrets)).encode())
    for artifact in artifacts.values():
        for secret in secrets.values():
            assert secret not in artifact


def test_windows_user_path_with_spaces_is_fully_redacted(tmp_path):
    _, artifacts = imported(tmp_path, b'ERROR: [Common 17-1] "C:\\Users\\Private GivenSurname\\project\\file.v"\n')
    for artifact in artifacts.values():
        assert "Private" not in artifact
        assert "GivenSurname" not in artifact
