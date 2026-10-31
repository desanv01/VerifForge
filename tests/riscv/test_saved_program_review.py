"""Independent source/pure review. Fake bytes are never native execution evidence."""
import ast
import hashlib
import importlib.util
import json
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
CI_SHA = "f88b1e79aef7554c0c4287f6d97a3979e5f63514aed542c30684ddf5be679ec8"
CLI_SHA = "439e8c8bfdb54fca6ba271d6c57e685b94b3bf530277b0f69c372af6747c851d"


@pytest.fixture
def controller(monkeypatch):
    import subprocess
    def forbidden(*args, **kwargs):
        pytest.fail("Review attempted a native/process command")
    monkeypatch.setattr(subprocess, "run", forbidden)
    monkeypatch.setattr(subprocess, "Popen", forbidden)
    path = ROOT / "scripts/ci_riscv_acceptance.py"
    assert hashlib.sha256(path.read_bytes()).hexdigest() == CI_SHA
    spec = importlib.util.spec_from_file_location("independent_saved_controller", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_fourth_case_keeps_gate_budget_and_admission_boundary(controller):
    assert controller.CAP == 32 * 1024 * 1024
    assert [label for label, _ in controller.CASE_PLANS] == ["control", "mutant", "control-replay", "control-saved-replay"]
    for remaining in (0, 149, 149.999):
        with pytest.raises(controller.GateError):
            controller.admit_case(1000, 1000 - remaining)
    controller.admit_case(1000, 850)
    controller.CANCELLED = True
    with pytest.raises(controller.GateError):
        controller.admit_case(1000, 850)
    source = (ROOT / "scripts/ci_riscv_acceptance.py").read_text()
    normalized = "".join(ast.unparse(ast.parse(source)).split())
    assert "execution_end=min(time.monotonic()+8*60,work_deadline)" in normalized
    assert "28*1024**2" in normalized


def test_export_keeps_previous_failure_bytes_and_saved_attribution(controller, tmp_path):
    work, source, output = tmp_path / "work", tmp_path / "source", tmp_path / "export"
    work.mkdir()
    source.mkdir()
    retained = {}
    for label, _ in controller.CASE_PLANS:
        directory = work / label
        directory.mkdir()
        retained[label + "/result.json"] = b'{"synthetic":"prior failure, no tool ran"}'
        (directory / "result.json").write_bytes(retained[label + "/result.json"])
    retained["control-saved-replay/saved-program.json"] = b'{"synthetic":"saved proof"}'
    (work / "control-saved-replay/saved-program.json").write_bytes(retained["control-saved-replay/saved-program.json"])
    retained["control/saved-origin.json"] = b'{"synthetic":"original run locator"}'
    (work / "control/saved-origin.json").write_bytes(retained["control/saved-origin.json"])
    controller.export_evidence(work, source, output)
    receipt = json.loads((output / "receipt-sha256.json").read_bytes())
    for relative, raw in retained.items():
        assert (work / relative).read_bytes() == (output / relative).read_bytes() == raw
        assert receipt[relative] == hashlib.sha256(raw).hexdigest()


def test_cli_additive_saved_argument_is_forwarded_without_a_new_execution_api():
    path = ROOT / "src/veriforge/riscv/__main__.py"
    raw = path.read_bytes()
    assert hashlib.sha256(raw).hexdigest() == CLI_SHA
    tree = ast.parse(raw)
    calls = [node for node in ast.walk(tree) if isinstance(node, ast.Call)]
    assert any(isinstance(node.func, ast.Attribute) and node.func.attr == "add_argument" and node.args
        and isinstance(node.args[0], ast.Constant) and node.args[0].value == "--saved-program" for node in calls)
    runner = next(node for node in calls if isinstance(node.func, ast.Name) and node.func.id == "run_experiment")
    assert [(keyword.arg, ast.unparse(keyword.value)) for keyword in runner.keywords] == [("saved_program", "args.saved_program")]


def test_native_recorded_import_scope_stays_three_cases_and_old_runner_pin_is_not_silently_accepted():
    from veriforge.system_investigation import native_tool_import as importer
    assert len(importer._RV_CASES) == 3
    assert set(importer._CASES) == {"riscv-40920450439", "firmware-40929665294"}
    # This is a compatibility witness: changing the producer runner invalidates
    # the preparer's exact installed-source check for the old accepted bundle.
    # It is not proof that a new four-case hosted run is importable.
    with pytest.raises(importer.NativeImportError, match="installed_domain_version_differs"):
        importer._installed({"src/veriforge/riscv/runner.py": "c392ae910b354b55319036266a45621a930a69eb045ae57774a0c798026499cf"})


def test_frozen_runner_saved_unavailable_tools_never_probe_riscv_compiler(tmp_path, monkeypatch):
    from veriforge.riscv import runner
    from veriforge.riscv.contracts import RiscvExperiment
    assert hashlib.sha256(Path(runner.__file__).read_bytes()).hexdigest() == "80524f263ebf569f0d5f2fc989f82dbc3993e8e7b2e24490c96563435e0045e5"
    saved = tmp_path / "saved"
    saved.mkdir()
    lookups = []
    monkeypatch.setattr(runner, "validate_saved_program", lambda *args: ({}, {}, {}))
    monkeypatch.setattr(runner.platform, "system", lambda: "Linux")
    def which(name):
        lookups.append(name)
        assert name in ("verilator", "spike")
        return "synthetic-verilator" if name == "verilator" else None
    monkeypatch.setattr(runner.shutil, "which", which)
    monkeypatch.setattr(runner.subprocess, "run", lambda *a, **k: pytest.fail("Version or native process unexpectedly started"))
    result = runner.run_experiment(RiscvExperiment(experiment_id="review-control"), tmp_path / "output", saved_program=saved)
    assert lookups == ["verilator", "spike"]
    assert result.verdict == "unsupported" and result.identity is None


def test_frozen_bounded_reader_rejects_oversize_before_open_and_reads_only_cap_plus_one(tmp_path, monkeypatch):
    from veriforge.riscv import runner
    path = tmp_path / "bounded"
    path.write_bytes(b"abcd")
    original_open = Path.open
    def forbidden(*args, **kwargs):
        pytest.fail("Oversized file was opened")
    monkeypatch.setattr(Path, "open", forbidden)
    with pytest.raises(ValueError):
        runner._read_bounded(path, 3)
    monkeypatch.setattr(Path, "open", original_open)
    assert runner._read_bounded(path, 4) == b"abcd"
