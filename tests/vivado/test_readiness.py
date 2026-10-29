"""Synthetic readiness inputs only; no vendor process or private source copying."""
from hashlib import sha256
from pathlib import Path

import pytest

from veriforge.vivado import BatchRecipe, inspect_exercise3, plan_batch, render_batch_recipe
from veriforge.vivado import adapter, readiness


@pytest.fixture
def synthetic_project(tmp_path, monkeypatch):
    root = tmp_path / "snapshot with spaces"
    root.mkdir()
    files = {"exercise3_core.v": b"// synthetic core\n", "exercise3_nexys_video_top.v": b"// synthetic top\n",
             "nexys_video_exercise3.xdc": b"# synthetic constraints\n"}
    for name, data in files.items():
        (root / name).write_bytes(data)
    monkeypatch.setattr(readiness, "EXERCISE3_HASHES", {name: sha256(data).hexdigest() for name, data in files.items()})
    return root


def test_inspection_preserves_bytes_and_does_not_upgrade_maturity(synthetic_project):
    before = {p.name: p.read_bytes() for p in synthetic_project.iterdir()}
    record = inspect_exercise3(synthetic_project)
    assert len(record.sources) == 3 and record.part == "xc7a200tsbg484-1"
    assert record.clock_period_ns == 10.0
    assert record.execute_status == record.repair_status == "not_validated"
    assert "no_io_delay" in record.timing_scope and "not_proven_safe" in record.cdc_scope
    assert before == {p.name: p.read_bytes() for p in synthetic_project.iterdir()}


@pytest.mark.parametrize("filename", ["exercise3_core.v", "exercise3_nexys_video_top.v", "nexys_video_exercise3.xdc"])
def test_any_reviewed_input_drift_rejected(synthetic_project, filename):
    (synthetic_project / filename).write_bytes(b"changed input; must re-review")
    with pytest.raises(ValueError, match="identity changed"):
        inspect_exercise3(synthetic_project)


def test_input_size_bound_before_identity(synthetic_project):
    (synthetic_project / "exercise3_core.v").write_bytes(b"x" * (readiness.MAX_SOURCE_BYTES + 1))
    with pytest.raises(ValueError, match="byte limit"):
        inspect_exercise3(synthetic_project)


@pytest.mark.parametrize("recipe", ["device_query_v1", "exec bad", None])
def test_recipe_not_arbitrary_tcl(recipe):
    with pytest.raises(ValueError, match="fixed BatchRecipe"):
        render_batch_recipe(recipe)


def test_device_query_fixed_parts_no_project_or_build_operations():
    script = render_batch_recipe(BatchRecipe.DEVICE_QUERY)
    assert "get_parts $part" in script
    for part in ("xc7a200tsbg484-1", "xc7a100tcsg324-1", "xc7a35tcpg236-1"):
        assert part in script
    assert all(token not in script for token in ("read_xdc", "read_verilog", "open_project", "create_project", "exec "))


def test_report_scope_and_constraints_preserved():
    script = render_batch_recipe(BatchRecipe.EXERCISE3_POST_SYNTH)
    assert script.index("read_xdc nexys_video_exercise3.xdc") < script.index("synth_design -top")
    assert script.index("check_timing -verbose") < script.index("report_cdc -details")
    assert "report_timing_summary -report_unconstrained" in script
    assert "report_methodology -file methodology.rpt" in script
    assert "VF_REPORT $stage unavailable" in script
    assert "functional_not_run timing_not_validated cdc_not_validated" in script
    assert all(token not in script for token in ("set_false_path", "set_clock_groups", "create_clock", "-quiet", "write_bitstream", "file delete", "program_hw_devices", "open_project"))


def test_plan_is_literal_argv_and_never_writes_inputs_or_script(synthetic_project, tmp_path):
    launcher = tmp_path / "vivado.bat"
    launcher.write_text("rem synthetic launcher; never executed")
    before = {p.name: p.read_bytes() for p in synthetic_project.iterdir()}
    plan = plan_batch(launcher, synthetic_project, BatchRecipe.EXERCISE3_POST_SYNTH)
    assert plan.argv[0] == str(launcher.resolve())
    assert plan.argv[1:5] == ("-mode", "batch", "-source", "recipe.tcl")
    assert plan.cwd == str(synthetic_project.resolve()) and plan.proposed_wall_seconds == 300
    assert plan.status == "source_only_not_executed" and len(plan.sources) == 3
    assert plan.script_sha256 == sha256(render_batch_recipe(BatchRecipe.EXERCISE3_POST_SYNTH).encode()).hexdigest()
    assert before == {p.name: p.read_bytes() for p in synthetic_project.iterdir()}


def test_readiness_query_plan_does_not_require_project_sources(tmp_path):
    launcher = tmp_path / "vivado.bat"
    launcher.touch()
    attempt = tmp_path / "fresh-attempt"
    attempt.mkdir()
    plan = plan_batch(launcher, attempt, BatchRecipe.DEVICE_QUERY)
    assert not plan.sources and plan.proposed_wall_seconds == 60
    assert not list(attempt.iterdir())


def test_existing_project_scripts_cannot_be_selected_as_attempt(synthetic_project, tmp_path):
    launcher = tmp_path / "vivado.bat"
    launcher.touch()
    (synthetic_project / "existing_user_flow.tcl").write_text("error never source this")
    with pytest.raises(ValueError, match="fresh isolated snapshot"):
        plan_batch(launcher, synthetic_project, BatchRecipe.EXERCISE3_POST_SYNTH)


def test_device_query_requires_empty_attempt_directory(tmp_path):
    launcher = tmp_path / "vivado.bat"
    launcher.touch()
    with pytest.raises(ValueError, match="fresh isolated snapshot"):
        plan_batch(launcher, tmp_path, BatchRecipe.DEVICE_QUERY)


def test_inventory_includes_current_default_root_without_claiming_execution(tmp_path, monkeypatch):
    launcher = tmp_path / "vivado.bat"
    launcher.touch()
    monkeypatch.setattr(adapter.shutil, "which", lambda name: None)
    monkeypatch.setattr(adapter.Path, "glob", lambda self, pattern: iter([launcher]) if self.as_posix() == "C:/AMDDesignTools" else iter(()))
    result = adapter.inventory()
    assert str(launcher.resolve()) in result.executable_candidates
    assert "C:/AMDDesignTools" in result.searched_locations
    assert result.support_level == "import-only" and result.tool_version is None
    assert result.device_support == "unverified"
