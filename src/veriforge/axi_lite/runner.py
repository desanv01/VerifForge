"""Curated AXI-Lite execution; independent parent-only checking of exact cycles."""
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

from veriforge.contracts import (ArtifactReference, AttemptResult, EvidenceIdentity,
    FailureSignature, ResourceUsage, ToolVersion, canonical_hash)
from veriforge.rtl.runner import _bounded, _environment, digest, encoded
from . import contracts, oracle
from .contracts import AxiLiteExperiment

ROOT = Path(__file__).resolve().parents[3]
FIXTURES = {"axil_registers_correct": 0, "axil_aw_w_order_bug": 1,
            "axil_strobe_bug": 2, "axil_response_stability_bug": 3}


def finalize_result(result, output, byte_limit):
    """Bound retained evidence including final JSON; excess is never a finding.

    Process polling can detect an overshoot. Remove largest files until the
    retained attempt obeys its cap, explicitly reporting loss of replay evidence.
    Only regular generated files below this attempt directory are eligible.
    """
    dropped = False
    while True:
        files = sorted(p for p in output.rglob("*") if p.is_file() and p != output / "result.json")
        artifacts = tuple(ArtifactReference(artifact_id=f"artifact-{i}", path=p.relative_to(output).as_posix(),
            sha256=digest(p.read_bytes()), size_bytes=p.stat().st_size,
            media_type="application/json" if p.suffix == ".json" else "text/plain") for i, p in enumerate(files))
        values = {**result.model_dump(), "artifacts": artifacts,
                  "usage": ResourceUsage(cycles=result.usage.cycles, wall_time_seconds=result.usage.wall_time_seconds,
                                         artifact_bytes=sum(a.size_bytes for a in artifacts))}
        if dropped:
            values.update(verdict="resource_limit", failure_signature=None,
                uncertainties=("Evidence byte limit exceeded; largest artifacts omitted. Replay evidence incomplete.",))
        revised = AttemptResult.model_validate(values)
        payload = revised.model_dump_json(indent=2).encode("utf-8")
        if sum(a.size_bytes for a in artifacts) + len(payload) <= byte_limit:
            (output / "result.json").write_bytes(payload)
            return revised
        if not files:
            # A bounded minimal resource result fits the schema's 1024-byte minimum.
            revised = AttemptResult(attempt_id=result.attempt_id, experiment_id=result.experiment_id,
                execution_status=result.execution_status, verdict="resource_limit",
                uncertainties=("Evidence/report exceeded byte limit; no replay evidence retained.",))
            (output / "result.json").write_bytes(revised.model_dump_json().encode("utf-8"))
            return revised
        largest = max(files, key=lambda p: p.stat().st_size)
        if largest.is_symlink() or not largest.resolve().is_relative_to(output.resolve()):
            raise ValueError("unexpected artifact path outside attempt directory")
        largest.unlink()
        dropped = True


def witness_experiment(case="order", fixture_id="axil_registers_correct"):
    """Stored cycle witnesses for this fixed READY schedule; no adaptive driver."""
    if case == "order":
        rows = [{"aw_valid": True}, {"w_valid": True, "w_data": 0x11223344},
                {}, {}, {}, {}, {"ar_valid": True}, {}, {}]
    elif case == "strobe":
        rows = [{"aw_valid": True, "w_valid": True, "w_data": 0x11223344}, {},
                {"aw_valid": True, "w_valid": True, "w_data": 0xAABBCCDD, "w_strb": 5}, {},
                {"ar_valid": True}, {}, {}]
    elif case == "response":
        rows = [{"aw_valid": True, "w_valid": True, "w_data": 37},
                {"b_ready": False}, {"b_ready": False}, {}, {}]
    else:
        raise ValueError("unknown curated witness")
    return AxiLiteExperiment(experiment_id="axil-witness-" + case, fixture_id=fixture_id,
                             cycles=({"reset_n": False}, {}, *rows))


def directed_experiment(fixture_id="axil_registers_correct"):
    """Finite exact plan: both orders, all masks, collisions, errors and resets."""
    rows = [{"reset_n": False}, {}]
    def write(address, data, mask=15, order="together"):
        aw = {"aw_valid": True, "aw_addr": address, "aw_prot": 7}
        wd = {"w_valid": True, "w_data": data, "w_strb": mask}
        rows.extend([aw, {}, wd, {}] if order == "aw" else
                    [wd, {}, aw, {}] if order == "w" else [{**aw, **wd}, {}])
    def read(address):
        rows.extend([{"ar_valid": True, "ar_addr": address, "ar_prot": 5}, {}])
    write(0, 0x11223344, order="aw")
    write(4, 0x55667788, order="w")
    read(0); read(4)
    for mask in range(16):
        write(8, 0x11223344)
        write(8, 0xAABBCCDD, mask)
        read(8)
    rows.extend([{"aw_valid": True, "aw_addr": 12, "w_valid": True, "w_data": 37,
                  "ar_valid": True, "ar_addr": 12},
                 {"b_ready": False, "r_ready": False},
                 {"b_ready": False, "r_ready": False}, {}, {}])
    read(12)
    for address in (1, 16, 255):
        write(address, 0xFFFFFFFF)
        read(address)
    # Cancel partial AW, partial W, and both stalled responses separately.
    for request in ({"aw_valid": True}, {"w_valid": True, "w_data": 99},
                    {"aw_valid": True, "w_valid": True, "w_data": 99, "ar_valid": True}):
        rows.extend([request, {"b_ready": False, "r_ready": False}, {"reset_n": False}, {}])
    for address in (0, 4, 8, 12):
        read(address)
    return AxiLiteExperiment(experiment_id="axil-directed", fixture_id=fixture_id, cycles=rows)


def _tools():
    if platform.system() != "Linux" or shutil.which("verilator") is None:
        raise NotImplementedError("Approved Linux Verilator/cocotb environment required; no simulation performed")
    tools = []
    for executable in ("verilator", "g++", "make"):
        reported = subprocess.run([executable, "--version"], capture_output=True, text=True,
                                  env=_environment(), timeout=5, check=True).stdout.splitlines()[0]
        if executable == "verilator" and tuple(map(int, reported.split()[1].split(".")[:2])) < (5, 36):
            raise NotImplementedError("Verilator older than approved 5.036 baseline")
        tools.append(ToolVersion(name={"g++": "gcc"}.get(executable, executable), version=reported[:256]))
    version = importlib.metadata.version("cocotb")
    if not version.startswith("2.0."):
        raise NotImplementedError("Locked cocotb 2.0 required")
    return [*tools, ToolVersion(name="cocotb", version=version),
            ToolVersion(name="python", version=platform.python_version())]


def run_experiment(experiment: AxiLiteExperiment, output_dir: Path, *, cancel_event=None) -> AttemptResult:
    experiment = AxiLiteExperiment.model_validate(experiment)
    output = Path(output_dir).resolve()
    output.mkdir(parents=True, exist_ok=True)
    if any(output.iterdir()):
        raise ValueError("attempt directory must be empty; evidence is never overwritten")
    started = time.monotonic()
    contract = oracle.reviewed_contract(experiment.parameters)
    source = ROOT / "targets/axi_lite/registers.sv"
    for name, model in (("experiment", experiment), ("contract", contract)):
        (output / (name + ".json")).write_text(model.model_dump_json(indent=2))
    (output / "stimulus.json").write_bytes(encoded([row.model_dump() for row in experiment.cycles]))
    for path, name in ((source, "registers.sv"), (Path(oracle.__file__), "checker.py"),
                       (Path(contracts.__file__), "checker-contracts.py")):
        shutil.copyfile(path, output / name)
    tools, uncertainties, signature = [], [], None
    status, verdict, cycles, identity = "completed", "unsupported", 0, None
    scratch = None
    try:
        if cancel_event is not None and cancel_event.is_set():
            status, verdict = "cancelled", "inconclusive"
        elif sum(p.stat().st_size for p in output.iterdir()) > experiment.budget.max_artifact_bytes:
            verdict = "resource_limit"
        else:
            tools = _tools()
            environment = {"tools": [tool.model_dump() for tool in tools], "platform": platform.platform(),
                           **{name + "_hash": digest(Path(__file__).with_name(name + ".py").read_bytes())
                              for name in ("runner", "sim_test", "sim_job", "contracts")}}
            (output / "environment.json").write_bytes(encoded(environment))
            identity = EvidenceIdentity(source_hash=digest(encoded({"source": digest((output / "registers.sv").read_bytes()),
                "variant": FIXTURES[experiment.fixture_id], "parameters": experiment.parameters.model_dump()})),
                checker_hash=digest((output / "checker.py").read_bytes()), contract_hash=canonical_hash(contract),
                experiment_hash=canonical_hash(experiment), environment_hash=digest(encoded(environment)))
            scratch = tempfile.TemporaryDirectory(prefix="veriforge-axil-")
            job = {"source": str(output / "registers.sv"), "build": scratch.name, "output": str(output),
                   "stimulus": str(output / "stimulus.json"), "observed": str(output / "observed.json"),
                   "parameters": {"MUTANT": FIXTURES[experiment.fixture_id]}}
            (output / "job.json").write_bytes(encoded(job))
            env = _environment()
            env.update(HOME=scratch.name, TMPDIR=scratch.name, CCACHE_DISABLE="1", PYTHONDONTWRITEBYTECODE="1")
            code, reason = _bounded([sys.executable, "-m", "veriforge.axi_lite.sim_job", str(output / "job.json")],
                output, env, max(0.001, experiment.budget.wall_time_seconds - (time.monotonic() - started)),
                cancel_event, experiment.budget.max_artifact_bytes)
            if reason:
                status = "cancelled" if reason == "cancelled" else "completed"
                verdict = "inconclusive" if reason == "cancelled" else reason
            elif code or not (output / "observed.json").exists():
                verdict = "harness_error" if (output / "built.marker").exists() else "build_error"
            else:
                try:
                    observed = json.loads((output / "observed.json").read_text())
                except (ValueError, UnicodeError):
                    observed = None
                checked = oracle.check(experiment, observed)
                verdict = checked.verdict
                # Executed rows and independently checked prefix are distinct.
                cycles = experiment.cycle_count if checked.checked_cycles else 0
                uncertainties.extend(checked.uncertainties)
                (output / "check.json").write_text(checked.model_dump_json(indent=2))
                if checked.failure is not None:
                    failure = checked.failure
                    signature = FailureSignature(requirement_id=failure.requirement_id, failure_class=failure.failure_class,
                        first_cycle=failure.cycle, expected=failure.expected, observed=failure.observed,
                        signature_hash=digest(encoded({"contract": identity.contract_hash, "checker": identity.checker_hash,
                            "requirement": failure.requirement_id, "class": failure.failure_class})))
    except (NotImplementedError, importlib.metadata.PackageNotFoundError) as exc:
        uncertainties.append(str(exc))
    except (OSError, subprocess.SubprocessError, ValueError, TypeError, KeyError, IndexError) as exc:
        status, verdict, signature = "infrastructure_error", "harness_error", None
        uncertainties.append(str(exc)[:2000])
    finally:
        if scratch is not None:
            scratch.cleanup()
    result = AttemptResult(attempt_id="attempt-" + uuid4().hex, experiment_id=experiment.experiment_id,
        execution_status=status, verdict=verdict, identity=identity, tool_versions=tuple(tools),
        failure_signature=signature, usage=ResourceUsage(cycles=cycles, wall_time_seconds=float(time.monotonic()-started),
            artifact_bytes=0), uncertainties=tuple(uncertainties))
    return finalize_result(result, output, experiment.budget.max_artifact_bytes)
