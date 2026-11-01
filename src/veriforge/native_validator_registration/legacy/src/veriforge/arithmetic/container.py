"""Host bridge using the existing prebuilt image, never building or installing."""
from pathlib import Path
import shutil
import subprocess
import tempfile
from uuid import uuid4
from veriforge.contracts import AttemptResult
from veriforge.rtl.runner import _bounded, _environment
from .contracts import ArithmeticExperiment
from .runner import ROOT


def run_container_experiment(experiment: ArithmeticExperiment, output_dir: Path, *, cancel_event=None) -> AttemptResult:
    experiment = ArithmeticExperiment.model_validate(experiment)
    output = Path(output_dir).resolve()
    output.mkdir(parents=True, exist_ok=True)
    if any(output.iterdir()):
        raise ValueError("attempt directory must be empty")
    name = "veriforge-arithmetic-" + uuid4().hex
    env = _environment()
    status, verdict, uncertainty = "infrastructure_error", "unsupported", "Prebuilt Docker project image unavailable"
    if shutil.which("docker") is not None:
        with tempfile.TemporaryDirectory(prefix="vf-arithmetic-control-", dir=output.parent) as temporary:
            control = Path(temporary)
            (control / "experiment.json").write_text(experiment.model_dump_json())
            command = ["docker", "run", "--pull", "never", "--rm", "--name", name, "--network", "none", "--read-only",
                "--cap-drop", "ALL", "--security-opt", "no-new-privileges", "--pids-limit", "128",
                "--memory", "2g", "--cpus", "2", "--tmpfs", "/tmp:rw,exec,nosuid,size=128m",
                "--mount", f"type=bind,source={ROOT / 'src'},target=/workspace/src,readonly",
                "--mount", f"type=bind,source={ROOT / 'targets/arithmetic'},target=/workspace/targets/arithmetic,readonly",
                "--mount", f"type=bind,source={control},target=/input,readonly",
                "--mount", f"type=bind,source={output},target=/attempt",
                "--entrypoint", "python", "veriforge-rtl:dev", "-m", "veriforge.arithmetic", "/input/experiment.json", "/attempt"]
            try:
                _, reason = _bounded(command, control, env, experiment.budget.wall_time_seconds + 15,
                                     cancel_event, experiment.budget.max_artifact_bytes)
                if (output / "result.json").exists() and not reason:
                    return AttemptResult.model_validate_json((output / "result.json").read_text())
                status = "cancelled" if reason == "cancelled" else "infrastructure_error"
                verdict = reason if reason in ("timeout", "resource_limit") else "inconclusive" if reason == "cancelled" else "unsupported"
                uncertainty = (control / "execution.log").read_text(errors="replace")[-1800:] or "container produced no valid result"
            except (OSError, subprocess.SubprocessError, ValueError) as exc:
                uncertainty = str(exc)[:1800]
            finally:
                subprocess.run(["docker", "rm", "-f", name], capture_output=True, timeout=10, env=env)
    result = AttemptResult(attempt_id="attempt-" + uuid4().hex, experiment_id=experiment.experiment_id,
        execution_status=status, verdict=verdict, uncertainties=(uncertainty,))
    (output / "experiment.json").write_text(experiment.model_dump_json(indent=2))
    (output / "result.json").write_text(result.model_dump_json(indent=2))
    return result
