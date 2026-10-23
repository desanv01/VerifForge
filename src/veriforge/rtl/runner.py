"""Bounded, credential-free execution of installed curated FIFO fixtures."""
from __future__ import annotations
import hashlib
import importlib.metadata
import json
import os
from pathlib import Path
import platform
import shutil
import signal
import subprocess
import sys
import tempfile
import time
from uuid import uuid4

from veriforge.contracts import (ArtifactReference, AttemptResult, BehavioralContract, CheckerIdentity, EvidenceIdentity, Experiment,
    FifoParameters, Requirement,
    FailureSignature, ResourceUsage, ToolVersion, TraceSample, canonical_hash)
from . import oracle

ROOT = Path(__file__).resolve().parents[3]
FIXTURES = {"fifo_correct": 0, "fifo_reset_bug": 1}


def digest(data):
    return hashlib.sha256(data).hexdigest()


def encoded(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":")).encode()


def reviewed_contract(parameters: FifoParameters | None = None) -> BehavioralContract:
    return BehavioralContract(review_status="reviewed", parameters=parameters or FifoParameters(),
        checker=CheckerIdentity(checker_id="fifo-deque-oracle", version="1", sha256=digest(Path(oracle.__file__).read_bytes())),
        requirements=(Requirement(requirement_id="FIFO_BEHAVIOR", description="; ".join(oracle.CONTRACT.values())),),
        assumptions=("Synchronous active-high reset clears queue and registered read output to zero.",
                     "Unsigned inputs fit width; reset precedes traffic.",
                     "Rejected reads retain read output; flags describe post-edge occupancy.",
                     "Full rejects write even with read; empty rejects read even with write."))


def _environment():
    # Explicit allowlist: controller/model credentials never enter simulator jobs.
    env = {key: os.environ[key] for key in ("PATH", "SystemRoot", "WINDIR", "TEMP", "TMP", "HOME") if key in os.environ}
    env.update(PYTHONPATH=str(ROOT / "src"), PYTHONNOUSERSITE="1", LANG="C.UTF-8")
    return env


def _kill_tree(process):
    if os.name == "nt":
        subprocess.run(["taskkill", "/PID", str(process.pid), "/T", "/F"], capture_output=True, timeout=5)
    else:
        try:
            os.killpg(process.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
    process.wait(timeout=5)


def _bounded(command, directory, env, seconds, cancel_event, byte_limit):
    log = directory / "execution.log"
    started = time.monotonic()
    with log.open("wb") as stream:
        process = subprocess.Popen(command, cwd=directory, env=env, stdout=stream, stderr=subprocess.STDOUT,
                                   start_new_session=os.name != "nt")
        reason = None
        try:
            while process.poll() is None:
                if cancel_event is not None and cancel_event.is_set():
                    reason = "cancelled"
                elif time.monotonic() - started >= seconds:
                    reason = "timeout"
                elif sum(p.stat().st_size for p in directory.rglob("*") if p.is_file() and "build" not in p.relative_to(directory).parts) > byte_limit:
                    reason = "resource_limit"
                if reason:
                    _kill_tree(process)
                    break
                time.sleep(0.05)
        finally:
            # A compiler/driver must not leave descendants after its parent exits.
            if process.poll() is None or os.name != "nt":
                _kill_tree(process)
    # A fast child can exit between polls; final evidence still obeys the cap.
    if reason is None and sum(p.stat().st_size for p in directory.rglob("*") if p.is_file() and "build" not in p.relative_to(directory).parts) > byte_limit:
        reason = "resource_limit"
    return process.returncode, reason


def run_experiment(experiment: Experiment, output_dir: Path, *, cancel_event=None) -> AttemptResult:
    experiment = Experiment.model_validate(experiment)
    output = Path(output_dir).resolve()
    output.mkdir(parents=True, exist_ok=True)
    if any(output.iterdir()):
        raise ValueError("attempt directory must be empty; evidence is never overwritten")
    started = time.monotonic()
    (output / "experiment.json").write_text(experiment.model_dump_json(indent=2))
    stimulus = oracle.expand(experiment)
    (output / "stimulus.json").write_bytes(encoded(stimulus))
    tools, identity, trace, signature, uncertainties = [], None, [], None, []
    status, verdict, cycles = "completed", "unsupported", 0
    scratch = None
    try:
        if experiment.fixture_id not in FIXTURES:
            raise NotImplementedError("fixture is typed but not installed in this milestone")
        if platform.system() != "Linux" or shutil.which("verilator") is None:
            raise NotImplementedError("Linux Verilator >=5.036 and cocotb 2.0 are required")
        version = subprocess.run(["verilator", "--version"], capture_output=True, text=True, timeout=5, env=_environment(), check=True).stdout.strip()
        tools = [ToolVersion(name="verilator", version=version), ToolVersion(name="cocotb", version=importlib.metadata.version("cocotb")),
                 ToolVersion(name="python", version=platform.python_version())]
        for executable in ("g++", "make"):
            reported = subprocess.run([executable, "--version"], capture_output=True, text=True, timeout=5, env=_environment(), check=True).stdout.splitlines()[0]
            tools.append(ToolVersion(name="gcc" if executable == "g++" else "make", version=reported[:256]))
        parts = version.split()[1].split(".")
        if tuple(map(int, parts[:2])) < (5, 36):
            raise NotImplementedError("installed Verilator is older than the supported 5.036 baseline")
        source = ROOT / "targets/fifo/fifo.sv"
        shutil.copyfile(source, output / "fifo.sv")
        contract = reviewed_contract(experiment.parameters)
        (output / "contract.json").write_text(contract.model_dump_json(indent=2))
        shutil.copyfile(Path(oracle.__file__), output / "checker.py")
        environment = {"tools": [t.model_dump() for t in tools], "platform": platform.platform(),
                       "driver_hash": digest((Path(__file__).parent / "sim_test.py").read_bytes()),
                       "runner_hash": digest((Path(__file__).parent / "sim_job.py").read_bytes())}
        (output / "environment.json").write_bytes(encoded(environment))
        identity = EvidenceIdentity(source_hash=digest(encoded({"source": digest(source.read_bytes()), "variant": FIXTURES[experiment.fixture_id], "parameters": experiment.parameters.model_dump()})),
            checker_hash=digest((output / "checker.py").read_bytes()), contract_hash=canonical_hash(contract),
            experiment_hash=canonical_hash(experiment), environment_hash=digest(encoded(environment)))
        scratch = tempfile.TemporaryDirectory(prefix="veriforge-rtl-build-")
        job = {"source": str(output / "fifo.sv"), "build": scratch.name, "output": str(output),
               "stimulus": str(output / "stimulus.json"), "observed": str(output / "observed.json"),
               "parameters": {"WIDTH": experiment.parameters.width, "DEPTH": experiment.parameters.depth,
                              "RESET_BUG": FIXTURES[experiment.fixture_id]}}
        (output / "job.json").write_bytes(encoded(job))
        job_env = _environment()
        job_env.update(HOME=scratch.name, TMPDIR=scratch.name,
                       CCACHE_DIR=str(Path(tempfile.gettempdir()) / "veriforge-rtl-ccache"),
                       CCACHE_MAXSIZE="32M", PYTHONDONTWRITEBYTECODE="1")
        code, reason = _bounded([sys.executable, "-m", "veriforge.rtl.sim_job", str(output / "job.json")], output,
                                job_env, max(0.001, experiment.budget.wall_time_seconds - (time.monotonic()-started)),
                                cancel_event, experiment.budget.max_artifact_bytes)
        if reason:
            status = "cancelled" if reason == "cancelled" else "completed"
            verdict = "inconclusive" if reason == "cancelled" else reason
        elif code != 0 or not (output / "observed.json").exists():
            verdict = "harness_error" if (output / "built.marker").exists() else "build_error"
        else:
            observed = json.loads((output / "observed.json").read_text())
            expected = oracle.expected_trace(stimulus, experiment.parameters.depth)
            difference = oracle.compare(expected, observed)
            cycles = len(observed)
            for want, got in zip(expected, observed):
                trace.append(TraceSample(cycle=want["cycle"], operation_index=want["operation_index"],
                    write_accepted=want["accepted_write"], read_accepted=want["accepted_read"],
                    expected_data=want["data"], observed_data=got["data"], expected_full=want["full"],
                    observed_full=got["full"], expected_empty=want["empty"], observed_empty=got["empty"]))
            verdict = "design_mismatch" if difference else "pass_within_scope"
            if difference:
                signature = FailureSignature(requirement_id="FIFO_BEHAVIOR", failure_class="fifo."+difference["signal"],
                    first_cycle=difference["cycle"], expected=str(difference["expected"]), observed=str(difference["observed"]),
                    signature_hash=digest(encoded({"contract": identity.contract_hash, "checker": identity.checker_hash,
                        "requirement": "FIFO_BEHAVIOR", "class": "fifo."+difference["signal"]})))
    except (NotImplementedError, importlib.metadata.PackageNotFoundError) as exc:
        uncertainties.append(str(exc))
    except (OSError, subprocess.SubprocessError, ValueError) as exc:
        status, verdict = "infrastructure_error", "harness_error"
        signature = None
        uncertainties.append(str(exc)[:2000])
    finally:
        if scratch is not None:
            scratch.cleanup()
    artifacts = []
    for path in sorted(output.rglob("*")):
        if path.is_file() and "build" not in path.relative_to(output).parts:
            relative = path.relative_to(output).as_posix()
            artifacts.append(ArtifactReference(artifact_id="artifact-"+str(len(artifacts)), path=relative,
                sha256=digest(path.read_bytes()), size_bytes=path.stat().st_size,
                media_type="application/json" if path.suffix == ".json" else "text/plain"))
    result = AttemptResult(attempt_id="attempt-"+uuid4().hex, experiment_id=experiment.experiment_id,
        execution_status=status, verdict=verdict, identity=identity, tool_versions=tuple(tools), trace=tuple(trace),
        artifacts=tuple(artifacts), failure_signature=signature, requirement_coverage=("FIFO_BEHAVIOR",) if trace else (),
        usage=ResourceUsage(cycles=cycles, wall_time_seconds=float(time.monotonic()-started), artifact_bytes=sum(a.size_bytes for a in artifacts)),
        uncertainties=tuple(uncertainties))
    (output / "result.json").write_text(result.model_dump_json(indent=2))
    if result.execution_status == "completed" and sum(p.stat().st_size for p in output.rglob("*") if p.is_file() and "build" not in p.relative_to(output).parts) > experiment.budget.max_artifact_bytes:
        result = AttemptResult.model_validate({**result.model_dump(), "verdict": "resource_limit", "failure_signature": None,
            "uncertainties": (*result.uncertainties, "Final report/evidence exceeded the artifact byte budget; retained for diagnosis.")})
        (output / "result.json").write_text(result.model_dump_json(indent=2))
    return result


def replay(attempt_dir: Path, output_dir: Path, *, cancel_event=None):
    previous = Path(attempt_dir).resolve()
    result = AttemptResult.model_validate_json((previous / "result.json").read_text())
    for artifact in result.artifacts:
        path = (previous / artifact.path).resolve()
        if not path.is_relative_to(previous) or digest(path.read_bytes()) != artifact.sha256:
            raise ValueError("replay artifact identity mismatch")
    experiment = Experiment.model_validate_json((previous / "experiment.json").read_text())
    if json.loads((previous / "stimulus.json").read_text()) != oracle.expand(experiment):
        raise ValueError("stored stimulus differs from typed experiment")
    if result.identity and (result.identity.experiment_hash != canonical_hash(experiment) or result.identity.checker_hash != digest(Path(oracle.__file__).read_bytes())):
        raise ValueError("replay experiment/checker identity changed")
    active_source = digest(encoded({"source": digest((ROOT / "targets/fifo/fifo.sv").read_bytes()),
        "variant": FIXTURES.get(experiment.fixture_id), "parameters": experiment.parameters.model_dump()}))
    if result.identity and (result.identity.source_hash != active_source or result.identity.contract_hash != canonical_hash(reviewed_contract(experiment.parameters))):
        raise ValueError("replay source/contract identity changed")
    replayed = run_experiment(experiment, output_dir, cancel_event=cancel_event)
    if result.identity and replayed.identity and (result.identity.source_hash != replayed.identity.source_hash or result.identity.contract_hash != replayed.identity.contract_hash):
        raise ValueError("replay source/contract identity changed")
    return replayed
