from dataclasses import asdict
from hashlib import sha256
import json
from pathlib import Path

import pytest

from veriforge.vivado import Recipe, import_log, inventory, prepare_attempt, prepare_repair_pair
from veriforge.vivado import adapter


def write_log(tmp_path, text):
    path = tmp_path / "original.log"
    path.write_text(text, encoding="utf-8")
    return path


@pytest.mark.parametrize("recipe,broken_token", [
    (Recipe.WRONG_TOP, "-top deliberately_wrong_top"),
    (Recipe.MISSING_SOURCE, "read_verilog missing.v"),
])
def test_repair_preserves_original_and_source_identity(tmp_path, recipe, broken_token):
    before, candidate = prepare_repair_pair(tmp_path, recipe)
    original = Path(before.directory)
    repaired = Path(candidate.directory)
    snapshot = {p.name: p.read_bytes() for p in original.iterdir()}
    assert broken_token in (original / "recipe.tcl").read_text()
    assert "-top vf_counter" in (repaired / "recipe.tcl").read_text()
    assert "read_verilog counter.v" in (repaired / "recipe.tcl").read_text()
    assert before.source_sha256 == candidate.source_sha256
    assert before.recipe_sha256 != candidate.recipe_sha256
    assert candidate.parent_attempt_id == before.attempt_id
    assert original != repaired
    assert before.execution_status == "prepared"
    assert candidate.functional_status == candidate.build_status == "not_run"
    assert snapshot == {p.name: p.read_bytes() for p in original.iterdir()}
    for attempt in (before, candidate):
        directory = Path(attempt.directory)
        assert sha256((directory / "manifest.json").read_bytes()).hexdigest() == attempt.manifest_sha256
        assert sha256((directory / "recipe.tcl").read_bytes()).hexdigest() == attempt.recipe_sha256
        assert sha256((directory / "counter.v").read_bytes()).hexdigest() == attempt.source_sha256


def test_source_fixture_matches_trusted_adapter():
    fixture = Path(__file__).resolve().parents[2] / "targets/vivado/counter.v"
    assert fixture.read_text() == adapter.SOURCE


def test_unique_attempts(tmp_path):
    first = prepare_attempt(tmp_path, Recipe.WRONG_TOP)
    second = prepare_attempt(tmp_path, Recipe.WRONG_TOP)
    assert first.attempt_id != second.attempt_id


@pytest.mark.parametrize("recipe", ["wrong_top", "../escape", "x; exec bad", None])
def test_no_arbitrary_recipe_or_tcl(tmp_path, recipe):
    with pytest.raises(ValueError):
        prepare_attempt(tmp_path, recipe)
    assert list(tmp_path.iterdir()) == []


def test_repaired_is_not_a_failure_recipe(tmp_path):
    with pytest.raises(ValueError):
        prepare_repair_pair(tmp_path, Recipe.REPAIRED)


def test_import_records_stage_ids_identity_and_provenance(tmp_path):
    path = write_log(tmp_path, "Vivado v2026.1 (64-bit)\nVF_STAGE synthesis started\n"
                     "ERROR: [Synth 8-439] module not found\nVF_STAGE synthesis failed\n")
    before = path.read_bytes()
    report = import_log(path, tmp_path / "imports")
    assert report.original_sha256 == sha256(before).hexdigest()
    assert report.build_status == "reported_failed"
    assert report.tool_execution_status == "unverified_import"
    assert report.tool_version == "2026.1"
    assert report.functional_status == "not_run"
    assert report.diagnostics[0].message_id == "Synth 8-439"
    assert report.diagnostics[0].stage == "synthesis"
    assert report.stages[-1].status == "failed"
    assert report.failure_signature
    assert path.read_bytes() == before
    assert json.loads((Path(report.directory) / "observations.json").read_text()) == json.loads(json.dumps(asdict(report)))


def test_imported_success_does_not_claim_design_pass(tmp_path):
    path = write_log(tmp_path, "VF_VERSION 2026.1\nVF_STAGE synthesis passed\nVF_BUILD passed\n")
    report = import_log(path, tmp_path / "out")
    assert report.build_status == "reported_passed"
    assert report.functional_status == "not_run"
    assert report.provenance == "imported_unverified"


@pytest.mark.parametrize("text", ["", "INFO: [Common 17-1] hello", "synthesis completed"])
def test_ambiguous_logs_are_unknown(tmp_path, text):
    report = import_log(write_log(tmp_path, text), tmp_path / "out")
    assert report.build_status == "unknown"
    assert report.failure_signature is None


def test_error_overrides_success_marker(tmp_path):
    report = import_log(write_log(tmp_path, "VF_BUILD passed\nERROR: [Common 17-1] failed"), tmp_path / "out")
    assert report.build_status == "reported_failed"


def test_timeout_is_not_a_design_failure(tmp_path):
    report = import_log(write_log(tmp_path, "process timed out"), tmp_path / "out")
    assert report.build_status == "unknown"
    assert report.functional_status == "not_run"
    assert report.failure_signature is None


def test_logs_are_bounded_before_output(tmp_path):
    path = tmp_path / "large.log"
    path.write_bytes(b"a" * (adapter.MAX_LOG_BYTES + 1))
    with pytest.raises(ValueError, match="1 MiB"):
        import_log(path, tmp_path / "out")
    assert not (tmp_path / "out").exists()


def test_secret_redaction_and_original_retained(tmp_path):
    path = write_log(tmp_path, "api_key=private1 TOKEN=private2 password='private three'\n"
                     "XILINXD_LICENSE_FILE=2100@private4 LM_LICENSE_FILE=private5\n"
                     "Bearer private6 https://user:private7@example.com\n"
                     "ERROR: [Common 17-1] C:/Users/Alice/project/file.v\n")
    report = import_log(path, tmp_path / "out")
    content = (Path(report.directory) / "vivado.sanitized.log").read_text()
    serialized = (Path(report.directory) / "observations.json").read_text()
    for secret in ["private1", "private2", "private three", "private4", "private5", "private6", "private7", "Alice"]:
        assert secret not in content
        assert secret not in serialized
    assert "private1" in path.read_text()
    assert report.original_sha256 != report.sanitized_sha256


def test_diagnostic_cap_does_not_hide_later_failure(tmp_path):
    text = "INFO: [Common 1-1] info\n" * 250 + "ERROR: [Synth 8-439] missing\n"
    text += "VF_STAGE synthesis started\n" * 250 + "VF_STAGE synthesis failed\nVF_BUILD passed\n"
    report = import_log(write_log(tmp_path, text), tmp_path / "out")
    assert len(report.diagnostics) == len(report.stages) == adapter.MAX_DIAGNOSTICS
    assert report.diagnostics_truncated
    assert report.build_status == "reported_failed"
    assert report.failure_signature


def test_quoted_json_credentials_are_redacted_in_memory_and_on_disk(tmp_path):
    secrets = {"api_key": 'private key with "quoted" text', "token": "private token",
               "LM_LICENSE_FILE": "2100@private-license-host"}
    text = "ERROR: [Common 17-1] " + json.dumps(secrets)
    report = import_log(write_log(tmp_path, text), tmp_path / "out")
    outputs = [report.diagnostics[0].text]
    outputs.extend(p.read_text() for p in Path(report.directory).iterdir())
    for output in outputs:
        assert "private" not in output
    assert "best-effort" in report.uncertainties[-1]


@pytest.mark.parametrize("separator", ["/", "\\"])
def test_user_directory_with_spaces_keeps_project_context_and_line_boundary(tmp_path, separator):
    user_path = separator.join(["C:", "Users", "Private GivenSurname", "project", "file.v"])
    text = f'ERROR: [Common 17-1] "{user_path}"\nINFO: [Common 1-1] retained\n'
    report = import_log(write_log(tmp_path, text), tmp_path / "out")
    outputs = [report.diagnostics[0].text]
    outputs.extend(p.read_text() for p in Path(report.directory).iterdir())
    for output in outputs:
        assert "Private" not in output
        assert "GivenSurname" not in output
        assert "project" in output
        assert "file.v" in output
    assert report.diagnostics[1].text == "retained"


def test_failure_signature_ignores_message_paths(tmp_path):
    path = write_log(tmp_path, "ERROR: [Synth 8-439] missing C:/a.v")
    first = import_log(path, tmp_path / "out")
    path.write_text("ERROR: [Synth 8-439] missing C:/different.v")
    second = import_log(path, tmp_path / "out")
    assert first.failure_signature == second.failure_signature
    assert first.original_sha256 != second.original_sha256
    assert first.directory != second.directory


def test_inventory_never_promotes_unverified_install(monkeypatch):
    monkeypatch.setattr(adapter.shutil, "which", lambda name: "C:/Xilinx/Vivado/2026.1/bin/vivado.bat")
    monkeypatch.setattr(adapter.Path, "glob", lambda self, pattern: iter(()))
    capability = inventory()
    assert capability.support_level == "import-only"
    assert capability.executable_candidates
    assert capability.tool_version is None
    assert capability.device_support == "unverified"
