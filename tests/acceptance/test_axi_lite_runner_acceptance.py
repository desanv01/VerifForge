"""Independent execution boundary checks; every child/tool response is fake."""
import hashlib
import importlib.util
import json
from pathlib import Path
import sys
from types import ModuleType, SimpleNamespace
import pytest
from veriforge.axi_lite import AxiLiteExperiment, reviewed_contract
from veriforge.axi_lite import AxiLiteCycle, AxiLiteObservation
from veriforge.axi_lite import contracts, oracle, runner, container
from veriforge.contracts import AttemptResult, ToolVersion, canonical_hash


def test_final_retained_cap_counts_actual_report_bytes_at_exact_boundary(tmp_path):
    # Native text writes can translate newlines; a byte cap must count disk bytes.
    (tmp_path / "evidence.bin").write_bytes(b"x" * 2048)
    initial = AttemptResult(attempt_id="mock-only", experiment_id="mock-only", execution_status="completed", verdict="unsupported")
    reported = runner.finalize_result(initial, tmp_path, 100000)
    boundary = sum(a.size_bytes for a in reported.artifacts) + len(reported.model_dump_json(indent=2).encode())
    assert boundary >= 1024
    limited = runner.finalize_result(reported, tmp_path, boundary)
    actual = sum(path.stat().st_size for path in tmp_path.rglob("*") if path.is_file())
    assert actual <= boundary, f"retained {actual} bytes exceeds {boundary}-byte cap"
    assert AttemptResult.model_validate_json((tmp_path / "result.json").read_text()) == limited


def sha(data):
    return hashlib.sha256(data).hexdigest()


def make_plan(*tail, fixture="axil_registers_correct"):
    return AxiLiteExperiment(experiment_id="vf055d-pure", fixture_id=fixture,
                             cycles=({"reset_n": False}, {}, *tail))


def samples(count, overrides=None):
    return {"schema_version": "axi_lite.trace.v1", "observations": [
        {"cycle": i, "aw_ready": False, "w_ready": False, "b_valid": False,
         "ar_ready": False, "r_valid": False, "b_resp": 0, "r_resp": 0, "r_data": 0,
         **(overrides or {}).get(i, {})} for i in range(count)]}


@pytest.fixture
def fake_native(monkeypatch, tmp_path):
    # Tools and child execution are deliberately fabricated, never simulator evidence.
    monkeypatch.setattr(runner, "_tools", lambda: [ToolVersion(name="verilator", version="mock-not-executed")])
    monkeypatch.setattr(runner.tempfile, "tempdir", str(tmp_path))


@pytest.mark.parametrize("fixture,variant", [
    ("axil_registers_correct", 0), ("axil_aw_w_order_bug", 1),
    ("axil_strobe_bug", 2), ("axil_response_stability_bug", 3),
])
def test_each_curated_variant_binds_all_five_identities_and_fixed_launch(tmp_path, monkeypatch, fake_native, fixture, variant):
    experiment = make_plan(fixture=fixture)
    for name in ("META_API_KEY", "DATABASE_URL", "REDIS_URL", "OPENAI_API_KEY"):
        monkeypatch.setenv(name, "test-only-not-forwarded")
    def child(command, output, env, seconds, token, limit):
        assert command == [sys.executable, "-m", "veriforge.axi_lite.sim_job", str(output / "job.json")]
        assert 0 < seconds <= experiment.budget.wall_time_seconds
        assert token is None and limit == experiment.budget.max_artifact_bytes
        assert not any(name in env for name in ("META_API_KEY", "DATABASE_URL", "REDIS_URL", "OPENAI_API_KEY"))
        assert env["PYTHONNOUSERSITE"] == "1" and env["PYTHONDONTWRITEBYTECODE"] == "1"
        job = json.loads((output / "job.json").read_text())
        assert job["parameters"] == {"MUTANT": variant}
        assert job["source"] == str(output / "registers.sv")
        assert job["stimulus"] == str(output / "stimulus.json")
        assert Path(job["build"]).is_dir()
        assert json.loads((output / "stimulus.json").read_text()) == [row.model_dump() for row in experiment.cycles]
        (output / "observed.json").write_text(json.dumps(samples(2)))
        return 0, None
    monkeypatch.setattr(runner, "_bounded", child)
    output = tmp_path / "attempt"
    result = runner.run_experiment(experiment, output)
    assert result.verdict == "pass_within_scope" and result.usage.cycles == 2
    assert result.trace == () and result.requirement_coverage == ()
    assert result.identity.experiment_hash == canonical_hash(experiment)
    assert result.identity.contract_hash == canonical_hash(reviewed_contract(experiment.parameters))
    assert result.identity.checker_hash == sha(Path(oracle.__file__).read_bytes()) == sha((output / "checker.py").read_bytes())
    source_record = {"source": sha((output / "registers.sv").read_bytes()), "variant": variant, "parameters": experiment.parameters.model_dump()}
    assert result.identity.source_hash == sha(json.dumps(source_record, sort_keys=True, separators=(",", ":")).encode())
    assert result.identity.environment_hash == sha((output / "environment.json").read_bytes())
    environment = json.loads((output / "environment.json").read_text())
    for name in ("runner", "sim_test", "sim_job", "contracts"):
        assert environment[name + "_hash"] == sha(Path(runner.__file__).with_name(name + ".py").read_bytes())
    assert (output / "checker-contracts.py").read_bytes() == Path(contracts.__file__).read_bytes()
    assert AttemptResult.model_validate_json((output / "result.json").read_text()) == result
    for artifact in result.artifacts:
        artifact_path = (output / artifact.path).resolve()
        assert artifact_path.is_relative_to(output.resolve())
        assert artifact.sha256 == sha(artifact_path.read_bytes()) and artifact.size_bytes == artifact_path.stat().st_size
    assert not list(tmp_path.glob("veriforge-axil-*"))  # Native build scratch cleaned even under fake execution.


@pytest.mark.parametrize("case,verdict,checked_cycles", [
    ("early_response", "property_failure", 3), ("wrong_data", "design_mismatch", 4),
    ("incomplete", "inconclusive", 3), ("illegal_manager", "harness_error", 4),
    ("late_malformed", "harness_error", 0),
])
def test_parent_maps_checker_semantics_without_fabricating_execution_prefix(tmp_path, monkeypatch, fake_native, case, verdict, checked_cycles):
    experiment = make_plan({"ar_valid": True}, {})
    raw = samples(4, {2: {"ar_ready": True}, 3: {"r_valid": True, "r_data": 9}})
    if case == "early_response": raw = samples(4, {2: {"ar_ready": True, "r_valid": True}})
    if case == "incomplete":
        experiment = make_plan({"ar_valid": True})
        raw = samples(3, {2: {"ar_ready": True}})
    if case == "illegal_manager":
        experiment = make_plan({"aw_valid": True}, {})
        raw = samples(4)
    if case == "late_malformed":
        raw = samples(4, {2: {"r_valid": True}, 3: {"r_data": "invalid"}})
    def child(command, output, *args):
        (output / "observed.json").write_text(json.dumps(raw))
        return 0, None
    monkeypatch.setattr(runner, "_bounded", child)
    output = tmp_path / "attempt"
    result = runner.run_experiment(experiment, output)
    checked = json.loads((output / "check.json").read_text())
    assert result.execution_status == "completed" and result.verdict == verdict
    assert checked["checked_cycles"] == checked_cycles
    assert result.usage.cycles == (experiment.cycle_count if checked_cycles else 0)
    assert bool(result.failure_signature) == (verdict in ("design_mismatch", "property_failure"))
    if result.failure_signature:
        failure = checked["failure"]
        for key, wire_key in (("requirement_id", "requirement_id"), ("failure_class", "failure_class"),
                              ("first_cycle", "cycle"), ("expected", "expected"), ("observed", "observed")):
            assert getattr(result.failure_signature, key) == failure[wire_key]
        record = {"contract": result.identity.contract_hash, "checker": result.identity.checker_hash,
                  "requirement": failure["requirement_id"], "class": failure["failure_class"]}
        assert result.failure_signature.signature_hash == sha(json.dumps(record, sort_keys=True, separators=(",", ":")).encode())


def test_internal_sim_job_uses_only_fixed_top_driver_and_observation_paths(tmp_path, monkeypatch):
    calls = []
    fake = SimpleNamespace(build=lambda **kwargs: calls.append(("build", kwargs)), test=lambda **kwargs: calls.append(("test", kwargs)))
    library = ModuleType("cocotb_tools.runner")
    library.get_runner = lambda simulator: fake if simulator == "verilator" else pytest.fail("non-curated simulator")
    monkeypatch.setitem(sys.modules, "cocotb_tools.runner", library)
    path = Path(runner.__file__).with_name("sim_job.py")
    spec = importlib.util.spec_from_file_location("vf055d_sim_job", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    job = {"source": "fixed-source", "build": "fixed-build", "output": str(tmp_path), "parameters": {"MUTANT": 3},
           "stimulus": "exact-input.json", "observed": "actual-output.json"}
    job_path = tmp_path / "job.json"
    job_path.write_text(json.dumps(job))
    monkeypatch.setattr(sys, "argv", ["trusted-module", str(job_path)])
    module.main()
    build, test = calls
    assert build[1]["sources"] == ["fixed-source"] and build[1]["parameters"] == {"MUTANT": 3}
    assert build[1]["hdl_toplevel"] == test[1]["hdl_toplevel"] == "axil_registers"
    assert build[1]["waves"] is test[1]["waves"] is False
    assert test[1]["test_module"] == "veriforge.axi_lite.sim_test"
    assert test[1]["extra_env"] == {"VF_STIMULUS": "exact-input.json", "VF_OBSERVED": "actual-output.json"}
    assert (tmp_path / "built.marker").exists()


def test_approved_tool_probes_are_bounded_fixed_argv_and_credential_free(monkeypatch):
    calls = []
    monkeypatch.setenv("META_API_KEY", "not-forwarded")
    monkeypatch.setattr(runner.platform, "system", lambda: "Linux")
    monkeypatch.setattr(runner.shutil, "which", lambda _: "/mock-not-real")
    monkeypatch.setattr(runner.importlib.metadata, "version", lambda _: "2.0.1")
    def probe(command, **kwargs):
        calls.append(command)
        assert kwargs["timeout"] == 5 and kwargs["check"] is True
        assert "META_API_KEY" not in kwargs["env"] and not kwargs.get("shell", False)
        return SimpleNamespace(stdout="Verilator 5.042" if command[0] == "verilator" else "mock-tool-version")
    monkeypatch.setattr(runner.subprocess, "run", probe)
    tools = runner._tools()
    assert calls == [["verilator", "--version"], ["g++", "--version"], ["make", "--version"]]
    assert {tool.name for tool in tools} == {"verilator", "gcc", "make", "cocotb", "python"}


@pytest.mark.parametrize("reported_id", ["", "veriforge-rtl:dev", "sha256:" + "z" * 64])
def test_host_never_launches_unresolved_or_non_sha_image(tmp_path, monkeypatch, reported_id):
    calls = []
    monkeypatch.setattr(container.shutil, "which", lambda _: "/mock-docker")
    def subprocess_fake(command, **kwargs):
        calls.append(command)
        return SimpleNamespace(stdout=reported_id)
    monkeypatch.setattr(container.subprocess, "run", subprocess_fake)
    monkeypatch.setattr(container, "_bounded", lambda *args: pytest.fail("unresolved image must not execute"))
    result = container.run_container_experiment(make_plan(), tmp_path / "attempt")
    assert result.execution_status == "infrastructure_error" and result.verdict == "unsupported"
    assert result.identity is None and result.failure_signature is None
    assert calls[0] == ["docker", "image", "inspect", "--format", "{{.Id}}", "veriforge-rtl:dev"]
    assert calls[1][:3] == ["docker", "rm", "-f"]


def test_outer_manifest_preserves_child_identity_and_rebases_real_nested_files(tmp_path, monkeypatch, fake_native):
    experiment = make_plan()
    captured = []
    monkeypatch.setattr(container.shutil, "which", lambda _: "/mock-docker")
    def probe(command, **kwargs):
        captured.append(command)
        assert not kwargs.get("shell", False)
        return SimpleNamespace(stdout="sha256:" + "b" * 64)
    monkeypatch.setattr(container.subprocess, "run", probe)
    def child(command, output, *args):
        (output / "observed.json").write_text(json.dumps(samples(2)))
        return 0, None
    monkeypatch.setattr(runner, "_bounded", child)
    child_results = []
    def host(command, output, env, seconds, token, byte_limit):
        captured.append(command)
        assert seconds == experiment.budget.wall_time_seconds + 15
        child_results.append(runner.run_experiment(experiment, output / "evidence"))
        return 0, None
    monkeypatch.setattr(container, "_bounded", host)
    output = tmp_path / "attempt"
    result = container.run_container_experiment(experiment, output)
    assert result.identity == child_results[0].identity
    assert result.tool_versions == child_results[0].tool_versions
    assert result.experiment_id == experiment.experiment_id and result.verdict == "pass_within_scope"
    names = {artifact.path for artifact in result.artifacts}
    assert {"evidence/result.json", "evidence/registers.sv", "evidence/checker.py", "container-image.json"} <= names
    for artifact in result.artifacts:
        path = output / artifact.path
        assert path.is_file() and path.resolve().is_relative_to(output.resolve())
        assert artifact.sha256 == sha(path.read_bytes()) and artifact.size_bytes == path.stat().st_size
    assert AttemptResult.model_validate_json((output / "result.json").read_text()) == result
    command = captured[1]
    assert command[-5] == "sha256:" + "b" * 64
    assert captured[2] == ["docker", "rm", "-f", command[command.index("--name") + 1]]


def test_native_exception_cleans_build_scratch_and_never_signs_design_failure(tmp_path, monkeypatch, fake_native):
    def failed_child(*args):
        raise OSError("mock launch failure; no process existed")
    monkeypatch.setattr(runner, "_bounded", failed_child)
    result = runner.run_experiment(make_plan(), tmp_path / "attempt")
    assert result.execution_status == "infrastructure_error" and result.verdict == "harness_error"
    assert result.failure_signature is None and not list(tmp_path.glob("veriforge-axil-*"))
    assert "mock launch failure" in result.uncertainties[0]


def test_driver_signal_sets_match_exact_schema_without_checker_or_verdict_imports():
    import ast
    source = Path(runner.__file__).with_name("sim_test.py").read_text()
    tree = ast.parse(source)
    constants = {node.targets[0].id: ast.literal_eval(node.value) for node in tree.body
                 if isinstance(node, ast.Assign) and isinstance(node.targets[0], ast.Name)}
    assert set(constants["INPUTS"]) == set(AxiLiteCycle.model_fields)
    assert set(constants["BOOL_OUTPUTS"]) | set(constants["INT_OUTPUTS"]) == set(AxiLiteObservation.model_fields) - {"cycle"}
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom):
            assert node.module in {"pathlib", "cocotb.triggers"}
        if isinstance(node, ast.Import):
            assert all(alias.name in {"json", "os", "cocotb"} for alias in node.names)
        assert not isinstance(node, ast.Assert)  # Simulator only observes; parent evaluates correctness.
