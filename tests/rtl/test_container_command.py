"""Command-construction regression; no Docker or simulator is invoked."""
import subprocess
from veriforge.contracts import Experiment
import veriforge.rtl.container as bridge


def test_trusted_simulator_tmpfs_is_executable_and_bounded(tmp_path, monkeypatch):
    commands = []
    cleanup = []
    def bounded(command, directory, *args):
        commands.append(command)
        (directory / "execution.log").write_text("Unit stub; no Docker/simulator execution")
        return 1, None
    def remove(command, **kwargs):
        cleanup.append(command)
        return subprocess.CompletedProcess(command, 0)
    monkeypatch.setattr(bridge.shutil, "which", lambda executable: "docker")
    monkeypatch.setattr(bridge, "_bounded", bounded)
    monkeypatch.setattr(bridge.subprocess, "run", remove)
    experiment = Experiment(experiment_id="bridge-command-unit", fixture_id="fifo_correct",
        objective_ids=("FIFO_BEHAVIOR",), operations=({"op": "reset"},))
    result = bridge.run_container_experiment(experiment, tmp_path / "attempt")
    assert result.verdict == "unsupported"
    command = commands[0]
    assert command[command.index("--tmpfs") + 1] == "/tmp:rw,exec,nosuid,size=128m"
    assert command[command.index("--network") + 1] == "none"
    assert command[command.index("--cap-drop") + 1] == "ALL"
    assert command[command.index("--pids-limit") + 1] == "128"
    assert "--read-only" in command and command[command.index("--pull") + 1] == "never"
    assert cleanup == [["docker", "rm", "-f", command[command.index("--name") + 1]]]
