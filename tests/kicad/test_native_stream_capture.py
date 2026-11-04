"""Portable stream regressions using fake OS/process services only."""
import io

import pytest

from veriforge.kicad import native_backend as backend
from veriforge.kicad.owned_process import OwnedProcess
from veriforge.kicad.repair_execution import commands


class Clock:
    def __init__(self):
        self.value = 0.0

    def __call__(self):
        return self.value

    def sleep(self, seconds):
        self.value += seconds


class OwnershipApi:
    """No native constructor, pipe creation or subprocess is used."""
    def __init__(self):
        self.mode = None
        self.terminated = False

    def create_job(self): return 10
    def create_suspended(self, argv, env, cwd):
        self.mode = "merged"
        return 11, 12, 9001, io.BytesIO(b"combined")
    def create_suspended_split(self, argv, env, cwd):
        self.mode = "separate"
        return 11, 12, 9001, io.BytesIO(b"stdout"), io.BytesIO(b"stderr")
    def identity(self, process): return 9001, 99
    def assign(self, job, process): pass
    def in_job(self, job, process): return True
    def resume(self, thread): pass
    def close(self, handle): pass
    def poll(self, process): return 0 if self.terminated else None
    def members(self, job): return () if self.terminated else (9001,)
    def terminate_job(self, job): self.terminated = True


@pytest.mark.parametrize("split", [False, True])
def test_owned_process_stream_selection_is_opt_in(tmp_path, split):
    api = OwnershipApi()
    options = {"separate_stderr": True} if split else {}
    process = OwnedProcess([str(tmp_path / "synthetic-tool.exe")], env={},
                           cwd=str(tmp_path), _api=api, **options)
    try:
        assert api.mode == ("separate" if split else "merged")
        assert process.stdout.read() == (b"stdout" if split else b"combined")
        if split:
            assert process.stderr is not process.stdout
            assert process.stderr.read() == b"stderr"
        else:
            assert process.stderr is None
        assert process.close_tree(grace=0) == []
        assert process.cleanup_receipt["final_member_pids"] == []
    finally:
        process.close_tree(grace=0)
        process.stdout.close()
        if process.stderr is not None:
            process.stderr.close()


class Process:
    def __init__(self, stdout, stderr, uncertain=False):
        self.stdout = io.BytesIO(stdout)
        self.stderr = None if stderr is None else io.BytesIO(stderr)
        self.uncertain = uncertain
        self.cleanup_receipt = None

    def poll(self): return 0

    def close_tree(self, *, grace):
        self.cleanup_receipt = {
            "errors": ["synthetic_unsettled"] if self.uncertain else [],
            "final_member_pids": [9001] if self.uncertain else [],
            "root_exit_status": None if self.uncertain else 0,
            "unexpected_members": [], "job_assignment_completed": True,
            "job_close_fallback_requested": self.uncertain,
        }


class Services:
    def __init__(self, clock, stdout=b"10.0.6\n", stderr=b"", *,
                 stderr_available_at=0.0, uncertain=False):
        self.clock = clock
        self.process = Process(stdout, stderr, uncertain)
        self.stderr_available_at = stderr_available_at

    def verify_installation(self): pass
    def memory(self): return 3 * 1024 ** 3
    def windows_directory(self): return "synthetic-windows"
    def launch(self, argv, env, cwd, guard):
        guard()
        return self.process
    def peek(self, stream):
        if stream is self.process.stderr and self.clock() < self.stderr_available_at:
            return 0, False
        remaining = len(stream.getvalue()) - stream.tell()
        return remaining, remaining == 0


def instance(tmp_path, *, split=True, **service_options):
    for name in ("reports", "project-copy", "config", "temp"):
        (tmp_path / name).mkdir()
    clock = Clock()
    services = Services(clock, **service_options)
    options = {"separate_stderr": True} if split else {}
    native = backend.NativeBackend(backend.Installation("a" * 64),
        _test_services=services, _clock=clock, _sleep=clock.sleep, **options)
    return native, services, clock


def run_stage(native, tmp_path, deadline=5.0):
    return native.run(commands(tmp_path)[0], cwd=tmp_path / "project-copy",
                      environment={}, deadline=deadline, checkpoint=lambda: None)


def test_split_capture_waits_for_both_eof_and_preserves_diagnostics(tmp_path):
    diagnostic = b"synthetic startup diagnostic\n"
    native, services, clock = instance(tmp_path, stderr=diagnostic,
                                       stderr_available_at=0.2)
    try:
        result = run_stage(native, tmp_path)
        assert clock() >= 0.2  # Exited root/stdout EOF cannot discard late stderr.
        assert result["stdout"] == b"10.0.6\n"
        assert result["stderr"] == diagnostic
        assert result["log_format"] == "separate_stdout_stderr"
        assert result["stderr_status"] == "present"
        assert result["diagnostics_present"] is True
        assert result["provenance"] == "fake-native-boundary"
        assert (tmp_path / "reports/version.native-stdout.log").read_bytes() == result["stdout"]
        assert (tmp_path / "reports/version.native-stderr.log").read_bytes() == diagnostic
    finally:
        cleanup = native.cleanup(seconds=5)
    assert cleanup["settled"] is True
    assert cleanup["log_eof_observed"] is True
    assert services.process.stdout.closed and services.process.stderr.closed


@pytest.mark.parametrize("stdout,stderr", [(b"", b"x" * 17), (b"x" * 8, b"y" * 9)])
def test_split_capture_caps_the_aggregate_of_both_streams(tmp_path, monkeypatch, stdout, stderr):
    native, _, _ = instance(tmp_path, stdout=stdout, stderr=stderr)
    monkeypatch.setattr(backend, "LIMITS", dict(backend.LIMITS, log_bytes=16))
    try:
        with pytest.raises(RuntimeError, match="aggregate log cap"):
            run_stage(native, tmp_path)
        assert native.log_eof is False
    finally:
        cleanup = native.cleanup(seconds=5)
    assert cleanup["settled"] is True
    assert cleanup["log_eof_observed"] is False


def test_unfinished_stderr_and_uncertain_cleanup_never_look_complete(tmp_path):
    native, services, _ = instance(tmp_path, stderr_available_at=float("inf"), uncertain=True)
    try:
        with pytest.raises(TimeoutError, match="native stage deadline"):
            run_stage(native, tmp_path, deadline=0.15)
        assert native.log_eof is False
    finally:
        cleanup = native.cleanup(seconds=5)
    assert cleanup["settled"] is False
    assert cleanup["log_eof_observed"] is False
    assert cleanup["owned_receipt"]["final_member_pids"] == [9001]
    assert services.process.stdout.closed and services.process.stderr.closed


def test_default_capture_keeps_merged_compatibility(tmp_path):
    combined = b"synthetic diagnostic\n10.0.6\n"
    native, _, _ = instance(tmp_path, split=False, stdout=combined, stderr=None)
    try:
        result = run_stage(native, tmp_path)
        assert result["stdout"] == combined
        assert result["stderr"] == b""
        assert result["log_format"] == "combined_stdout_stderr"
        assert (tmp_path / "reports/version.native-combined.log").read_bytes() == combined
        assert not (tmp_path / "reports/version.native-stderr.log").exists()
    finally:
        cleanup = native.cleanup(seconds=5)
    assert cleanup["settled"] is True
