"""Mocked command capture only. Never invokes Docker, builds, or simulators."""
import json
from threading import Event
from types import SimpleNamespace

import pytest

from veriforge.axi_lite import container
from veriforge.axi_lite.runner import witness_experiment
from veriforge.contracts import AttemptResult


@pytest.mark.parametrize("reason,verdict,status", [
    (None, "unsupported", "infrastructure_error"),
    ("timeout", "timeout", "infrastructure_error"),
    ("resource_limit", "resource_limit", "infrastructure_error"),
    ("cancelled", "inconclusive", "cancelled"),
])
def test_mock_container_isolation_and_limit_classification(tmp_path, monkeypatch, reason, verdict, status):
    commands = []
    monkeypatch.setenv("VF_SECRET", "not-forwarded")
    monkeypatch.setattr(container.shutil, "which", lambda _: "/mock/docker")
    def capture(command, directory, env, seconds, event, limit):
        commands.append(command)
        assert directory == tmp_path.resolve()  # monitor all nested evidence too
        assert seconds == 45 and "VF_SECRET" not in env
        (directory / "execution.log").write_text("mock command capture; no execution")
        return 1, reason
    monkeypatch.setattr(container, "_bounded", capture)
    def subprocess_mock(command, **kwargs):
        commands.append(command)
        return SimpleNamespace(stdout="sha256:" + "a" * 64)
    monkeypatch.setattr(container.subprocess, "run", subprocess_mock)
    result = container.run_container_experiment(witness_experiment(), tmp_path)
    assert commands[0][:3] == ["docker", "image", "inspect"]
    command = commands[1]
    for option, value in (("--pull", "never"), ("--network", "none"), ("--cap-drop", "ALL"),
                          ("--security-opt", "no-new-privileges"), ("--pids-limit", "128"),
                          ("--memory", "2g"), ("--cpus", "2"), ("--tmpfs", "/tmp:rw,exec,nosuid,size=128m")):
        assert command[command.index(option) + 1] == value
    assert "--read-only" in command and "sha256:" + "a" * 64 in command
    assert "--env" not in command and "--privileged" not in command
    assert command[-4:] == ["-m", "veriforge.axi_lite", "/input/experiment.json", "/attempt/evidence"]
    mounts = [command[i + 1] for i, arg in enumerate(command) if arg == "--mount"]
    assert all("readonly" in mount for mount in mounts if "target=/attempt" not in mount)
    assert commands[2][:3] == ["docker", "rm", "-f"]
    assert (result.verdict, result.execution_status) == (verdict, status)
    assert result.failure_signature is None


def test_mock_container_result_artifacts_are_rebased(tmp_path, monkeypatch):
    monkeypatch.setattr(container.shutil, "which", lambda _: "/mock/docker")
    monkeypatch.setattr(container.subprocess, "run", lambda *args, **kwargs: SimpleNamespace(stdout="sha256:" + "a" * 64))
    def execute(command, directory, *args):
        evidence = directory / "evidence"
        evidence.mkdir()
        (evidence / "experiment.json").write_text("{}")
        child = AttemptResult(attempt_id="mock-attempt", experiment_id="axil-witness-order",
                              execution_status="completed", verdict="unsupported")
        (evidence / "result.json").write_text(child.model_dump_json())
        return 0, None
    monkeypatch.setattr(container, "_bounded", execute)
    result = container.run_container_experiment(witness_experiment(), tmp_path)
    assert {a.path for a in result.artifacts} == {"container-image.json", "evidence/experiment.json", "evidence/result.json"}
    assert json.loads((tmp_path / "result.json").read_text())["attempt_id"] == "mock-attempt"


def test_prelaunch_cancellation_never_launches_docker(tmp_path, monkeypatch):
    def forbidden(*args, **kwargs): raise AssertionError("unexpected process call")
    monkeypatch.setattr(container, "_bounded", forbidden)
    event = Event(); event.set()
    result = container.run_container_experiment(witness_experiment(), tmp_path, cancel_event=event)
    assert result.execution_status == "cancelled"
