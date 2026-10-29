"""Independent pure/fake review of the hosted gate; never dispatch containers/jobs."""
import copy
import importlib.util
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

ROOT = Path(__file__).resolve().parents[2]


@pytest.fixture
def ci(monkeypatch):
    # Import itself must not invoke an external process.
    import subprocess
    import platform
    # Windows platform.machine() can invoke cmd /c ver during library import.
    monkeypatch.setattr(platform, "machine", lambda: "AMD64")
    monkeypatch.setattr(subprocess, "Popen", lambda *a, **k: pytest.fail("external dispatch during review"))
    monkeypatch.setattr(subprocess, "run", lambda *a, **k: pytest.fail("external dispatch during review"))
    monkeypatch.setattr(subprocess, "check_output", lambda *a, **k: pytest.fail("external dispatch during review"))
    spec = importlib.util.spec_from_file_location("independent_axi_ci_review", ROOT / "scripts/ci_axi_application.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_exact_fixed_cases_and_no_ambient_services(ci):
    assert ci.CASES == ("fifo", "arithmetic", "control", "order", "strobe", "response", "replay")
    assert ci.MAX_ATTEMPTS == 7
    project = "vf-axi-ci-314-2"
    plan = ci.compose_plan(project, "sha256:" + "a" * 64)
    assert set(plan["services"]) == {"api", "worker", "reconciler", "postgres", "valkey"}
    assert all(s["labels"][ci.OWNER] == project for s in plan["services"].values())
    assert all(v["labels"][ci.OWNER] == project for v in plan["volumes"].values())
    assert set(plan["volumes"]) == {"database", "broker", "artifacts"}
    assert not any(v.get("external") for v in plan["volumes"].values())
    assert plan["services"]["api"]["ports"] == ["127.0.0.1::8000"]
    assert all("ports" not in value for role, value in plan["services"].items() if role != "api")
    for service in plan["services"].values():
        assert "env_file" not in service and "build" not in service
        assert service["restart"] == "no" and service["mem_limit"] and service["pids_limit"]
    assert all(volume.split(":")[0] in plan["volumes"] for service in plan["services"].values() for volume in service.get("volumes", []))


def test_migration_closure_rejects_disconnected_cycle(ci):
    files = {"migrations/versions/root.py": b'revision="root"\ndown_revision=None\n',
             "migrations/versions/x.py": b'revision="x"\ndown_revision="y"\n',
             "migrations/versions/y.py": b'revision="y"\ndown_revision="x"\n'}
    with pytest.raises(RuntimeError, match="disconnected"):
        ci.migration_head(files)


def test_public_source_closure_is_committed_bytes_without_private_helpers(ci, monkeypatch, tmp_path):
    monkeypatch.setattr(ci, "ROOT", tmp_path)
    monkeypatch.setattr(ci, "OUT", tmp_path / "evidence")
    files = {name: b"public committed bytes" for name in (
        "pyproject.toml", "uv.lock", "alembic.ini", "infra/rtl/Dockerfile",
        "infra/app/Dockerfile", "scripts/ci_axi_application.py", "src/veriforge/public.py",
        "targets/axi_lite/registers.sv", "tests/integration/__init__.py",
        ".github/workflows/axi-application-acceptance.yml", "tests/axi_lite/test_ci_application_plan.py")}
    files["migrations/versions/head.py"] = b'revision="head"\ndown_revision=None\n'
    files[".coordination/operator-helper.py"] = b"private helper must not be staged"
    calls = []
    def git(args, **kwargs):
        calls.append(args)
        if args[1] == "rev-parse":
            return "a" * 40
        if args[1:4] == ["ls-tree", "-r", "--name-only"]:
            return "\n".join(files)
        if args[1] == "ls-tree":
            return "100644 blob fake\t" + args[-1]
        assert args[1] == "show"
        return files[args[2].split(":", 1)[1]]
    monkeypatch.setattr(ci.subprocess, "check_output", git)
    work = tmp_path / "context"
    commit, head = ci.stage_sources(work)
    assert commit == "a" * 40 and head == "head"
    assert not (work / ".coordination").exists()
    assert (work / "src/veriforge/public.py").read_bytes() == files["src/veriforge/public.py"]
    manifest = json.loads((ci.OUT / "source-manifest.json").read_text())
    assert set(manifest["files"]) == set(files) - {".coordination/operator-helper.py"}
    assert all(args[0] == "git" for args in calls)


def test_typed_fixed_plans_do_not_connect_database_or_dispatch(ci, monkeypatch):
    from sqlalchemy.engine import Engine
    monkeypatch.setattr(Engine, "connect", lambda *a, **k: pytest.fail("database operation"))
    plans = ci.fixture_plans()
    assert set(plans) == set(ci.CASES) - {"replay"}
    for label, fixture, cycles in (("control", "axil_registers_correct", 149),
                                   ("order", "axil_aw_w_order_bug", 11),
                                   ("strobe", "axil_strobe_bug", 9),
                                   ("response", "axil_response_stability_bug", 7)):
        assert plans[label]["adapter_id"] == "rtl.axi_lite.v1"
        assert plans[label]["plan"]["fixture_id"] == fixture
        assert len(plans[label]["plan"]["cycles"]) == cycles
        assert plans[label]["plan"]["budget"]["wall_time_seconds"] == 120
        assert plans[label]["plan"]["budget"]["max_artifact_bytes"] == 4 * 1024 * 1024


@pytest.mark.parametrize("size", [2 * 1024 * 1024 + 1, 64 * 1024 * 1024 + 1])
def test_already_exited_command_cannot_bypass_output_cap(ci, monkeypatch, tmp_path, size):
    monkeypatch.setattr(ci, "OUT", tmp_path / "evidence")
    # Sparse fake output avoids a subprocess or allocating a 64 MiB payload.
    def fake_process(args, stdout, **kwargs):
        stdout.seek(size - 1)
        stdout.write(b"x")
        stdout.flush()
        return SimpleNamespace(poll=lambda: 0, returncode=0)
    monkeypatch.setattr(ci.subprocess, "Popen", fake_process)
    host = ci.Host("vf-axi-ci-314-2", tmp_path / "work")
    with pytest.raises(RuntimeError, match="cap"):
        host.run(["FAKE-NOT-EXECUTED"], "instant-output")


def test_owned_command_deadline_terminates_then_kills_if_needed(ci, monkeypatch, tmp_path):
    monkeypatch.setattr(ci, "OUT", tmp_path / "evidence")
    calls = []
    class Process:
        returncode = None
        def poll(self):
            return None
        def terminate(self):
            calls.append("terminate")
        def kill(self):
            calls.append("kill")
            self.returncode = -9
        def wait(self, timeout):
            calls.append(("wait", timeout))
            if self.returncode is None:
                raise ci.subprocess.TimeoutExpired("FAKE", timeout)
            return self.returncode
    monkeypatch.setattr(ci.subprocess, "Popen", lambda *a, **k: Process())
    clock = iter((0, 0, 0, 2))
    monkeypatch.setattr(ci.time, "monotonic", lambda: next(clock, 2))
    host = ci.Host("vf-axi-ci-314-2", tmp_path / "work")
    with pytest.raises(RuntimeError, match="deadline"):
        host.run(["FAKE-NOT-EXECUTED"], "timeout", timeout=1)
    assert calls == ["terminate", ("wait", 30), "kill", ("wait", 5)]


def test_cancel_failure_preserves_first_checker_failure(ci, monkeypatch, tmp_path):
    monkeypatch.setattr(ci.sys, "platform", "linux")
    monkeypatch.setenv("VERIFORGE_CI_EPHEMERAL_PROJECT", "vf-axi-ci-314-2")
    monkeypatch.setattr(ci, "fixture_plans", lambda: {"fifo": {}})
    monkeypatch.setattr(ci, "idle_snapshot", lambda: {"tables": {"campaigns": []}, "files": {}})
    def fake_api(method, path, *a, **k):
        if path == "/health":
            return {"evidence_adapters": ["rtl.fifo.v1", "rtl.arithmetic.v1", "rtl.axi_lite.v1"]}
        if path.endswith("/cancel"):
            raise RuntimeError("secondary cancel failure")
        return {"id": "owned-campaign", "execution_status": "completed"}
    monkeypatch.setattr(ci, "api_json", fake_api)
    monkeypatch.setattr(ci, "validate_case", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("primary checker failure")))
    with pytest.raises(RuntimeError, match="primary checker failure"):
        ci.exercise(tmp_path / "fake-only")


def test_cleanup_failure_preserves_first_host_failure(ci, monkeypatch, tmp_path):
    monkeypatch.setattr(ci.sys, "platform", "linux")
    monkeypatch.setenv("GITHUB_ACTIONS", "true")
    monkeypatch.setenv("GITHUB_RUN_ID", "314")
    monkeypatch.setenv("GITHUB_RUN_ATTEMPT", "2")
    monkeypatch.setattr(ci, "ROOT", tmp_path)
    monkeypatch.setattr(ci, "OUT", tmp_path / "evidence")
    def stage(work):
        ci.write(ci.OUT / "source-manifest.json", {"commit": "fake-not-built"})
        return "fake-not-built", "fake-head"
    monkeypatch.setattr(ci, "stage_sources", stage)
    monkeypatch.setattr(ci.Host, "run", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("primary host failure")))
    monkeypatch.setattr(ci, "cleanup", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("secondary cleanup failure")))
    with pytest.raises(RuntimeError, match="primary host failure"):
        ci.hosted_main()


@pytest.mark.parametrize("primary", [None, "first checker failure"])
def test_cleanup_failure_never_leaves_success_receipt(ci, monkeypatch, tmp_path, primary):
    monkeypatch.setattr(ci, "OUT", tmp_path / "evidence")
    first = None if primary is None else RuntimeError(primary)
    secondary = RuntimeError("secondary cleanup failure")
    monkeypatch.setattr(ci, "cleanup", lambda *a: (_ for _ in ()).throw(secondary))
    summary = {"status": "application_checks_passed_pending_cleanup", "local_adoption": False}
    failure = ci.finish_owned("vf-axi-ci-314-2", tmp_path / "work", summary, first)
    assert failure is (secondary if first is None else first)
    saved = json.loads((ci.OUT / "summary.json").read_text())
    assert saved["status"] == "failed_or_cancelled" and saved["owned_cleanup"]["status"] == "failed"
    assert saved["local_adoption"] is False


def test_already_exited_command_checks_combined_evidence_cap(ci, monkeypatch, tmp_path):
    out = tmp_path / "evidence"
    out.mkdir()
    with (out / "other-fake-artifact.bin").open("wb") as stream:
        stream.seek(ci.CAP)
        stream.write(b"x")
    monkeypatch.setattr(ci, "OUT", out)
    monkeypatch.setattr(ci.subprocess, "Popen", lambda *a, **k: SimpleNamespace(poll=lambda: 0, returncode=0))
    host = ci.Host("vf-axi-ci-314-2", tmp_path / "work")
    with pytest.raises(RuntimeError, match="cap"):
        host.run(["FAKE-NOT-EXECUTED"], "empty-instant-output")


@pytest.mark.parametrize("terminal", ["late", "cancelled"])
def test_already_exited_command_rechecks_deadline_and_cancellation(ci, monkeypatch, tmp_path, terminal):
    monkeypatch.setattr(ci, "OUT", tmp_path / "evidence")
    clock = iter((0, 0, 0, 2))
    monkeypatch.setattr(ci.time, "monotonic", lambda: next(clock, 2))
    def done(*a, **k):
        if terminal == "cancelled":
            ci.STOP = True
        return SimpleNamespace(poll=lambda: 0, returncode=0)
    monkeypatch.setattr(ci.subprocess, "Popen", done)
    host = ci.Host("vf-axi-ci-314-2", tmp_path / "work")
    with pytest.raises(RuntimeError, match="deadline|cancel"):
        host.run(["FAKE-NOT-EXECUTED"], "instant-terminal", timeout=1)


def test_final_summary_cannot_pass_with_evidence_over_budget(ci, monkeypatch, tmp_path):
    out = tmp_path / "evidence"
    out.mkdir()
    (out / "pre-existing-evidence.txt").write_bytes(b"12")
    monkeypatch.setattr(ci, "OUT", out)
    monkeypatch.setattr(ci, "CAP", 1)
    monkeypatch.setattr(ci, "cleanup", lambda *a: None)
    summary = {"status": "application_checks_passed_pending_cleanup", "local_adoption": False}
    failure = ci.finish_owned("vf-axi-ci-314-2", tmp_path / "work", summary)
    assert failure is not None and summary["status"] != "actual_hosted_gate_passed"


def test_preservation_rejects_old_row_and_artifact_changes(ci):
    before = {"tables": {"attempts": [{"id": "immutable", "result": {"ok": True}}]},
              "files": {"observed.json": {"sha256": "a", "size_bytes": 1}}}
    after = copy.deepcopy(before)
    after["tables"]["attempts"][0]["result"]["ok"] = 1
    with pytest.raises(RuntimeError, match="old rows"):
        ci.unchanged(before, after)
    after = copy.deepcopy(before)
    after["files"]["observed.json"]["sha256"] = "b"
    with pytest.raises(RuntimeError, match="artifact bytes"):
        ci.unchanged(before, after)


def test_workflow_cannot_automatically_claim_local_runtime_adoption():
    workflow = (ROOT / ".github/workflows/axi-application-acceptance.yml").read_text()
    assert "workflow_dispatch:" in workflow and "pull_request:" not in workflow and "push:" not in workflow
    assert "contents: read" in workflow and "persist-credentials: false" in workflow
    assert "--cleanup" in workflow and "if: always()" in workflow
    assert workflow.index("--cleanup") < workflow.index("uses: actions/upload-artifact")
    source = (ROOT / "scripts/ci_axi_application.py").read_text()
    assert "'local_adoption': False" in source
    assert "no local adoption/browser/model claim" in source
    assert ".coordination" not in source and ".tmp-" not in source
