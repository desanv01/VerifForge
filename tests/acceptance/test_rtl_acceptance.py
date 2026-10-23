"""Control-boundary tests only. No simulator or external process is launched."""
from veriforge.rtl import runner


def test_terminal_process_output_is_checked_against_artifact_budget(tmp_path, monkeypatch):
    class FinishedProcess:
        pid = 123456
        returncode = 0

        def poll(self):
            return 0

        def wait(self, timeout=None):
            return 0

    def simulated_launch(*args, **kwargs):
        # Represents bytes written before the first poll, not a simulator result.
        (tmp_path / "oversized.log").write_bytes(b"x" * 2048)
        return FinishedProcess()

    monkeypatch.setattr(runner.subprocess, "Popen", simulated_launch)
    monkeypatch.setattr(runner, "_kill_tree", lambda process: None)
    _, reason = runner._bounded(["never-executed"], tmp_path, {}, 10, None, 1024)
    assert reason == "resource_limit"
