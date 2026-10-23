import json
from pathlib import Path
import platform
import shutil
import subprocess
import threading
import time
import sys
import tempfile
import pytest
from veriforge.contracts import Experiment
from veriforge.rtl import run_experiment, replay, reviewed_contract
from veriforge.rtl.runner import ROOT, _bounded, _environment


def test_job_environment_excludes_credentials(monkeypatch):
    monkeypatch.setenv("META_API_KEY", "test-secret")
    monkeypatch.setenv("OPENAI_API_KEY", "test-secret")
    assert "META_API_KEY" not in _environment() and "OPENAI_API_KEY" not in _environment()


def test_reviewed_contract_tracks_checker_and_parameters():
    from veriforge.contracts import FifoParameters, canonical_hash
    from veriforge.rtl import oracle
    from veriforge.rtl.runner import digest
    contract = reviewed_contract()
    assert contract.review_status == "reviewed"
    assert contract.checker.sha256 == digest(Path(oracle.__file__).read_bytes())
    assert canonical_hash(contract) != canonical_hash(reviewed_contract(FifoParameters(depth=8)))


def experiment(fixture="fifo_correct"):
    data = json.loads((ROOT / "targets/fifo/directed.json").read_text())
    data["fixture_id"] = fixture
    return Experiment.model_validate(data)


def test_unsupported_fixture_has_no_design_verdict(tmp_path):
    result = run_experiment(experiment("fifo_order_bug"), tmp_path)
    assert result.verdict == "unsupported"
    assert result.failure_signature is None


@pytest.mark.skipif(platform.system() != "Linux", reason="process-group test requires Linux")
def test_timeout_and_cancel_kill_descendants(tmp_path):
    # PID marker creation must not depend on host bind-mount latency.
    with tempfile.TemporaryDirectory(prefix="vf-tree-test-") as directory:
        _check_process_cleanup(Path(directory))


def _check_process_cleanup(tmp_path):
    for cancellation in (False, True):
        output = tmp_path / str(cancellation)
        output.mkdir()
        token = threading.Event()
        if cancellation:
            def cancel_after_spawn():
                deadline = time.monotonic() + 10
                while not (output / "pid").exists() and time.monotonic() < deadline:
                    time.sleep(.01)
                token.set()
            threading.Thread(target=cancel_after_spawn).start()
        command = [sys.executable, "-c", "import subprocess,time,pathlib; p=subprocess.Popen(['sleep','30']); pathlib.Path('pid').write_text(str(p.pid)); time.sleep(30)"]
        env = _environment()
        env.pop("PYTHONPATH", None)
        _, reason = _bounded(command, output, env, 5.0 if not cancellation else 12.0, token, 100000)
        assert reason == ("cancelled" if cancellation else "timeout")
        pid = int((output / "pid").read_text())
        state = Path(f"/proc/{pid}/stat")
        assert not state.exists() or state.read_text().split()[2] == "Z"


@pytest.mark.skipif(platform.system() != "Linux" or shutil.which("verilator") is None, reason="real Linux simulator required")
def test_real_correct_mutant_and_replay(tmp_path):
    correct = run_experiment(experiment(), tmp_path / "correct")
    assert correct.verdict == "pass_within_scope", correct.model_dump_json()
    faulty = run_experiment(experiment("fifo_reset_bug"), tmp_path / "faulty")
    assert faulty.verdict == "design_mismatch", faulty.model_dump_json()
    assert faulty.failure_signature.first_cycle == 14
    repeated = replay(tmp_path / "faulty", tmp_path / "replay")
    assert repeated.failure_signature == faulty.failure_signature
    assert repeated.trace == faulty.trace
    for number in (2, 3):
        again = replay(tmp_path / "faulty", tmp_path / f"replay-{number}")
        assert again.failure_signature == faulty.failure_signature
        assert again.trace == faulty.trace
    shutil.copytree(tmp_path / "faulty", tmp_path / "tampered")
    (tmp_path / "tampered/stimulus.json").write_text("[]")
    with pytest.raises(ValueError, match="identity mismatch"):
        replay(tmp_path / "tampered", tmp_path / "tampered-replay")


def test_tamper_detection_leaves_original_evidence_intact(tmp_path):
    original = tmp_path / "original"
    run_experiment(experiment("fifo_order_bug"), original)
    shutil.copytree(original, tmp_path / "tampered")
    (tmp_path / "tampered/stimulus.json").write_text("[]")
    with pytest.raises(ValueError, match="identity mismatch"):
        replay(tmp_path / "tampered", tmp_path / "invalid-replay")
    assert json.loads((original / "stimulus.json").read_text())


def test_final_report_cannot_bypass_byte_cap(tmp_path):
    data = experiment("fifo_order_bug").model_dump(mode="json")
    data["budget"]["max_artifact_bytes"] = 1024
    result = run_experiment(Experiment.model_validate(data), tmp_path)
    assert result.verdict == "resource_limit"
    assert result.failure_signature is None


@pytest.mark.skipif(platform.system() != "Linux" or shutil.which("verilator") is None, reason="real Linux simulator required")
def test_real_build_failure_is_separate(tmp_path, monkeypatch):
    original = shutil.copyfile
    def corrupt_source(source, destination):
        if Path(source).name == "fifo.sv":
            Path(destination).write_text("this is invalid HDL;")
        else:
            original(source, destination)
    monkeypatch.setattr(shutil, "copyfile", corrupt_source)
    result = run_experiment(experiment(), tmp_path / "invalid-build")
    assert result.verdict == "build_error"
    assert result.failure_signature is None


def test_reject_existing_attempt(tmp_path):
    (tmp_path / "keep").write_text("immutable")
    with pytest.raises(ValueError, match="empty"):
        run_experiment(experiment(), tmp_path)
