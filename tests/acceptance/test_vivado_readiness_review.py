"""Independent pure path/recipe/accounting review; no vendor execution."""
from hashlib import sha256
from pathlib import Path
import subprocess

import pytest

from veriforge.vivado import readiness
from veriforge.vivado.readiness import BatchRecipe, inspect_exercise3, plan_batch, render_batch_recipe
from veriforge.vivado import storage


@pytest.fixture
def project(tmp_path, monkeypatch):
    root = tmp_path / "snapshot [untrusted text]"
    root.mkdir()
    data = {"exercise3_core.v": b"// synthetic core\n",
        "exercise3_nexys_video_top.v": b"// synthetic top\n",
        "nexys_video_exercise3.xdc": b"create_clock -period 10 [get_ports clk]\n"}
    for name, raw in data.items():
        (root / name).write_bytes(raw)
    monkeypatch.setattr(readiness, "EXERCISE3_HASHES", {name: sha256(raw).hexdigest() for name, raw in data.items()})
    return root


def test_literal_recipe_is_independent_of_hostile_path_text_and_never_executes(project, tmp_path, monkeypatch):
    launcher = tmp_path / "tool [do not evaluate]" / "vivado.bat"
    launcher.parent.mkdir()
    launcher.write_text("synthetic launcher marker; not an executable test")
    def forbidden(*args, **kwargs):
        pytest.fail("source-only readiness must not dispatch a process")
    monkeypatch.setattr(subprocess, "run", forbidden)
    monkeypatch.setattr(subprocess, "Popen", forbidden)
    before = {p.name: p.read_bytes() for p in project.iterdir()}
    plan = plan_batch(launcher, project, BatchRecipe.EXERCISE3_POST_SYNTH)
    assert plan.argv == (str(launcher.resolve()), "-mode", "batch", "-source", "recipe.tcl", "-log", "vivado.log", "-journal", "vivado.jou")
    script = render_batch_recipe(BatchRecipe.EXERCISE3_POST_SYNTH)
    assert "untrusted text" not in script and "do not evaluate" not in script
    assert plan.script_sha256 == sha256(script.encode()).hexdigest()
    assert plan.status == "source_only_not_executed" and plan.proposed_wall_seconds == 300
    assert before == {p.name: p.read_bytes() for p in project.iterdir()}


def test_constraint_drift_cannot_rebind_the_approved_clock(project):
    record = inspect_exercise3(project)
    assert record.clock_period_ns == 10.0
    assert record.execute_status == record.repair_status == "not_validated"
    (project / "nexys_video_exercise3.xdc").write_bytes(b"create_clock -period 20 [get_ports clk]\n")
    with pytest.raises(ValueError, match="identity changed"):
        inspect_exercise3(project)


def test_matching_hash_outside_source_root_still_rejected(project, tmp_path, monkeypatch):
    source = project / "exercise3_core.v"
    outside = tmp_path / "outside.v"
    outside.write_bytes(source.read_bytes())
    resolve = Path.resolve
    monkeypatch.setattr(Path, "resolve", lambda path, *a, **kw: resolve(outside, *a, **kw) if path == source else resolve(path, *a, **kw))
    with pytest.raises(ValueError, match="escapes"):
        inspect_exercise3(project)


def test_query_plan_refuses_hidden_init_hook(tmp_path):
    launcher = tmp_path / "vivado.exe"
    launcher.touch()
    root = tmp_path / "query"
    root.mkdir()
    (root / ".hidden-init.tcl").write_text("synthetic marker; never executed")
    with pytest.raises(ValueError, match="fresh isolated snapshot"):
        plan_batch(launcher, root, BatchRecipe.DEVICE_QUERY)


@pytest.mark.parametrize("recipe", ["exercise3_post_synth_v1; exec injected", "device_query_v1", None])
def test_untyped_recipe_is_rejected_without_interpretation(recipe):
    with pytest.raises(ValueError):
        render_batch_recipe(recipe)


def test_metadata_storage_scan_counts_exact_prefix_without_opening_contents(tmp_path, monkeypatch):
    for name, raw in {"2026.1/XilinxTclStore/a": b"12", "2026.1/XilinxTclStore_extra/a": b"345",
        ".Xil/elab.rtd": b"6789"}.items():
        path = tmp_path / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(raw)
    def forbidden(*args, **kwargs):
        pytest.fail("metadata scan must not open file contents")
    monkeypatch.setattr(Path, "open", forbidden)
    result = storage.measure_storage(tmp_path)
    assert (result.cache_bytes, result.result_bytes, result.files, result.total_bytes) == (2, 7, 3, 9)


def test_junction_is_rejected_before_descendant_scan(tmp_path, monkeypatch):
    link = tmp_path / "synthetic-junction"
    link.mkdir()
    original = Path.is_junction
    monkeypatch.setattr(Path, "is_junction", lambda path: path == link or original(path))
    with pytest.raises(ValueError, match="linked"):
        storage.measure_storage(tmp_path)


def test_regular_file_cannot_be_accepted_as_an_empty_attempt_directory(tmp_path):
    path = tmp_path / "not-a-directory"
    path.write_bytes(b"nonempty file")
    with pytest.raises(ValueError, match="directory|attempt"):
        storage.measure_storage(path)


def test_directory_only_tree_cannot_bypass_metadata_scan_bound(tmp_path, monkeypatch):
    directory = tmp_path / "empty-dir"
    directory.mkdir()
    def directories(root, pattern):
        for _ in range(storage.FILE_CAP + 2):
            yield directory
        pytest.fail("directory-only traversal exceeded the bounded metadata budget")
    monkeypatch.setattr(Path, "rglob", directories)
    try:
        result = storage.measure_storage(tmp_path)
    except ValueError:
        return  # Explicit scan-budget rejection is a valid fail-closed policy.
    assert result.limit_reason is not None
