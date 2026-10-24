"""Command construction only; no Docker or simulator process is launched."""
from veriforge.arithmetic import directed_experiment
from veriforge.arithmetic import container


def test_build_tmpfs_allows_execution_and_preserves_isolation(tmp_path, monkeypatch):
    commands = []
    monkeypatch.setattr(container.shutil, "which", lambda _: "/docker")
    def capture(command, directory, env, seconds, cancel_event, byte_limit):
        commands.append(command)
        (directory / "execution.log").write_text("mock command capture; no execution")
        return 1, None
    monkeypatch.setattr(container, "_bounded", capture)
    monkeypatch.setattr(container.subprocess, "run", lambda command, **kwargs: commands.append(command))
    result = container.run_container_experiment(directed_experiment(), tmp_path / "attempt")
    command = commands[0]
    assert command[command.index("--tmpfs") + 1] == "/tmp:rw,exec,nosuid,size=128m"
    assert command[command.index("--network") + 1] == "none"
    assert "--read-only" in command
    assert command[command.index("--cap-drop") + 1] == "ALL"
    assert command[command.index("--security-opt") + 1] == "no-new-privileges"
    assert command[command.index("--pids-limit") + 1] == "128"
    assert command[command.index("--memory") + 1] == "2g"
    assert command[command.index("--cpus") + 1] == "2"
    assert command[command.index("--pull") + 1] == "never"
    assert all("readonly" in command[index + 1] for index, arg in enumerate(command)
               if arg == "--mount" and any(path in command[index + 1] for path in ("target=/workspace/", "target=/input,")))
    assert commands[1][:3] == ["docker", "rm", "-f"]
    assert result.verdict == "unsupported"
