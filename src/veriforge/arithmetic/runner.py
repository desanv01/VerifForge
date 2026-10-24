"""Bounded curated multiplier runner. Common outcomes; arithmetic-specific traces."""
from __future__ import annotations
import importlib.metadata
import json
from pathlib import Path
import platform
import shutil
import subprocess
import sys
import tempfile
import time
from uuid import uuid4
from veriforge.contracts import (AttemptResult, ArtifactReference, CheckerIdentity, EvidenceIdentity,
    FailureSignature, Requirement, ResourceUsage, ToolVersion, canonical_hash)
from veriforge.rtl.runner import _bounded, _environment, digest, encoded
from .contracts import ArithmeticContract, ArithmeticExperiment, Parameters
from . import oracle

ROOT = Path(__file__).resolve().parents[3]
FIXTURES = {"multiplier_correct": 0, "multiplier_latency_bug": 1, "multiplier_data_bug": 2}


def reviewed_contract(parameters=None):
    return ArithmeticContract(parameters=parameters or Parameters(),
        checker=CheckerIdentity(checker_id="scheduled-integer-multiplier", version="1", sha256=digest(Path(oracle.__file__).read_bytes())),
        requirements=(Requirement(requirement_id="ARITH_PRODUCT", description="Full unsigned 2*WIDTH-bit product, in accepted order."),
                      Requirement(requirement_id="ARITH_LATENCY", description="Valid exactly two edges after each accepted input; bubbles preserved."),
                      Requirement(requirement_id="ARITH_RESET", description="Reset clears valid/data and cancels every in-flight product.")))


def directed_experiment(fixture_id="multiplier_correct", width=8):
    maximum = 2**width - 1
    return ArithmeticExperiment(experiment_id="arithmetic-directed", fixture_id=fixture_id,
        parameters=Parameters(width=width), operations=(
            {"op": "reset"}, {"op": "multiply", "a": 0, "b": maximum},
            {"op": "multiply", "a": 1, "b": maximum}, {"op": "idle"},
            {"op": "multiply", "a": maximum, "b": maximum}, {"op": "idle", "cycles": 2},
            {"op": "multiply", "a": maximum, "b": 2 if width > 1 else 1},
            {"op": "cycle", "reset": True, "valid": True, "a": maximum, "b": maximum},
            {"op": "idle", "cycles": 3}, {"op": "multiply", "a": maximum, "b": 1},
            {"op": "idle", "cycles": 2}))


def run_experiment(experiment: ArithmeticExperiment, output_dir: Path, *, cancel_event=None) -> AttemptResult:
    experiment = ArithmeticExperiment.model_validate(experiment)
    output = Path(output_dir).resolve()
    output.mkdir(parents=True, exist_ok=True)
    if any(output.iterdir()):
        raise ValueError("attempt directory must be empty; evidence is never overwritten")
    started = time.monotonic()
    contract = reviewed_contract(experiment.parameters)
    stimulus = oracle.expand(experiment)
    for name, model in (("experiment", experiment), ("contract", contract)):
        (output / (name + ".json")).write_text(model.model_dump_json(indent=2))
    (output / "stimulus.json").write_bytes(encoded(stimulus))
    source = ROOT / "targets/arithmetic/multiplier.sv"
    shutil.copyfile(source, output / "multiplier.sv")
    shutil.copyfile(Path(oracle.__file__), output / "checker.py")
    tools, uncertainties, signature = [], [], None
    status, verdict, cycles, identity = "completed", "unsupported", 0, None
    scratch = None
    try:
        if platform.system() != "Linux" or shutil.which("verilator") is None:
            raise NotImplementedError("Linux Verilator >=5.036 with locked cocotb 2.0 required; no simulation performed")
        version = subprocess.run(["verilator", "--version"], capture_output=True, text=True,
                                 env=_environment(), timeout=5, check=True).stdout.strip()
        if tuple(map(int, version.split()[1].split(".")[:2])) < (5, 36):
            raise NotImplementedError("Verilator older than supported 5.036 baseline")
        cocotb_version = importlib.metadata.version("cocotb")
        if not cocotb_version.startswith("2.0."):
            raise NotImplementedError("locked cocotb 2.0 required")
        tools = [ToolVersion(name="verilator", version=version), ToolVersion(name="cocotb", version=cocotb_version),
                 ToolVersion(name="python", version=platform.python_version())]
        for executable in ("g++", "make"):
            reported = subprocess.run([executable, "--version"], capture_output=True, text=True,
                                      env=_environment(), timeout=5, check=True).stdout.splitlines()[0]
            tools.append(ToolVersion(name="gcc" if executable == "g++" else "make", version=reported[:256]))
        environment = {"tools": [tool.model_dump() for tool in tools], "platform": platform.platform(),
                       "driver_hash": digest(Path(__file__).with_name("sim_test.py").read_bytes()),
                       "runner_hash": digest(Path(__file__).read_bytes()),
                       "job_hash": digest(Path(__file__).with_name("sim_job.py").read_bytes())}
        (output / "environment.json").write_bytes(encoded(environment))
        identity = EvidenceIdentity(source_hash=digest(encoded({"source": digest(source.read_bytes()),
            "variant": FIXTURES[experiment.fixture_id], "parameters": experiment.parameters.model_dump()})),
            checker_hash=digest(Path(oracle.__file__).read_bytes()), contract_hash=canonical_hash(contract),
            experiment_hash=canonical_hash(experiment), environment_hash=digest(encoded(environment)))
        scratch = tempfile.TemporaryDirectory(prefix="veriforge-arithmetic-")
        job = {"source": str(output / "multiplier.sv"), "build": scratch.name, "output": str(output),
               "stimulus": str(output / "stimulus.json"), "observed": str(output / "observed.json"),
               "parameters": {"WIDTH": experiment.parameters.width, "LATENCY": experiment.parameters.latency,
                              "MUTANT": FIXTURES[experiment.fixture_id]}}
        (output / "job.json").write_bytes(encoded(job))
        env = _environment()
        env.update(HOME=scratch.name, TMPDIR=scratch.name, CCACHE_DISABLE="1", PYTHONDONTWRITEBYTECODE="1")
        code, reason = _bounded([sys.executable, "-m", "veriforge.arithmetic.sim_job", str(output / "job.json")],
            output, env, max(0.001, experiment.budget.wall_time_seconds - (time.monotonic()-started)),
            cancel_event, experiment.budget.max_artifact_bytes)
        if reason:
            status = "cancelled" if reason == "cancelled" else "completed"
            verdict = "inconclusive" if reason == "cancelled" else reason
        elif code or not (output / "observed.json").exists():
            verdict = "harness_error" if (output / "built.marker").exists() else "build_error"
        else:
            observed = json.loads((output / "observed.json").read_text())
            expected = oracle.expected_trace(stimulus, experiment.parameters.latency)
            difference = oracle.compare(expected, observed)
            cycles = len(observed)
            (output / "arithmetic-trace.json").write_bytes(encoded({"schema_version": "arithmetic.trace.v1", "expected": expected, "observed": observed}))
            verdict = "design_mismatch" if difference else "pass_within_scope"
            if difference:
                failure_class = "arithmetic." + difference["signal"]
                signature = FailureSignature(requirement_id=difference["requirement"], failure_class=failure_class,
                    first_cycle=difference["cycle"], expected=str(difference["expected"]), observed=str(difference["observed"]),
                    signature_hash=digest(encoded({"contract": identity.contract_hash, "checker": identity.checker_hash,
                        "requirement": difference["requirement"], "class": failure_class})))
    except (NotImplementedError, importlib.metadata.PackageNotFoundError) as exc:
        uncertainties.append(str(exc))
    except (OSError, subprocess.SubprocessError, ValueError, TypeError, KeyError, IndexError) as exc:
        status, verdict, signature = "infrastructure_error", "harness_error", None
        uncertainties.append(str(exc)[:2000])
    finally:
        if scratch is not None:
            scratch.cleanup()
    artifacts = tuple(ArtifactReference(artifact_id=f"artifact-{index}", path=path.relative_to(output).as_posix(),
        sha256=digest(path.read_bytes()), size_bytes=path.stat().st_size,
        media_type="application/json" if path.suffix == ".json" else "text/plain")
        for index, path in enumerate(sorted(p for p in output.rglob("*") if p.is_file())))
    # Common trace has FIFO-specific flags; preserve arithmetic valid/data only in its typed artifact.
    result = AttemptResult(attempt_id="attempt-" + uuid4().hex, experiment_id=experiment.experiment_id,
        execution_status=status, verdict=verdict, identity=identity, tool_versions=tuple(tools), artifacts=artifacts,
        failure_signature=signature, usage=ResourceUsage(cycles=cycles, wall_time_seconds=float(time.monotonic()-started),
            artifact_bytes=sum(a.size_bytes for a in artifacts)), uncertainties=tuple(uncertainties))
    (output / "result.json").write_text(result.model_dump_json(indent=2))
    # Parent-generated traces and the report can exceed the cap after child polling ends.
    if result.execution_status == "completed" and sum(path.stat().st_size for path in output.rglob("*") if path.is_file()) > experiment.budget.max_artifact_bytes:
        result = AttemptResult.model_validate({**result.model_dump(), "verdict": "resource_limit", "failure_signature": None,
            "uncertainties": (*result.uncertainties, "Final report/evidence exceeded the artifact byte budget; retained for diagnosis.")})
        (output / "result.json").write_text(result.model_dump_json(indent=2))
    return result
