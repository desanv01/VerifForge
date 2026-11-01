"""Host bridge to the existing approved image; never pull, build, or install."""
from pathlib import Path
import json
import re
import shutil
import subprocess
import tempfile
from uuid import uuid4

from veriforge.contracts import AttemptResult
from veriforge.rtl.runner import _bounded, _environment
from .contracts import AxiLiteExperiment
from .runner import ROOT, finalize_result


def run_container_experiment(experiment: AxiLiteExperiment, output_dir: Path, *, cancel_event=None) -> AttemptResult:
    experiment = AxiLiteExperiment.model_validate(experiment)
    output = Path(output_dir).resolve()
    output.mkdir(parents=True, exist_ok=True)
    if any(output.iterdir()):
        raise ValueError("attempt directory must be empty")
    name = "veriforge-axil-" + uuid4().hex
    env = _environment()
    status, verdict, uncertainty = "infrastructure_error", "unsupported", "Approved prebuilt Docker image unavailable"
    if cancel_event is not None and cancel_event.is_set():
        status, verdict, uncertainty = "cancelled", "inconclusive", "Cancelled before launch"
    elif shutil.which("docker") is not None:
        with tempfile.TemporaryDirectory(prefix="vf-axil-control-", dir=output.parent) as temporary:
            control = Path(temporary)
            (control / "experiment.json").write_text(experiment.model_dump_json())
            command = ["docker", "run", "--pull", "never", "--rm", "--name", name, "--network", "none", "--read-only",
                "--cap-drop", "ALL", "--security-opt", "no-new-privileges", "--pids-limit", "128",
                "--memory", "2g", "--cpus", "2", "--tmpfs", "/tmp:rw,exec,nosuid,size=128m",
                "--mount", f"type=bind,source={ROOT / 'src'},target=/workspace/src,readonly",
                "--mount", f"type=bind,source={ROOT / 'targets/axi_lite'},target=/workspace/targets/axi_lite,readonly",
                "--mount", f"type=bind,source={control},target=/input,readonly",
                "--mount", f"type=bind,source={output},target=/attempt",
                "--entrypoint", "python", "veriforge-rtl:dev", "-m", "veriforge.axi_lite", "/input/experiment.json", "/attempt/evidence"]
            try:
                inspected = subprocess.run(["docker", "image", "inspect", "--format", "{{.Id}}", "veriforge-rtl:dev"],
                                           capture_output=True, text=True, timeout=5, check=True, env=env)
                image_id = inspected.stdout.strip()
                if re.fullmatch(r"sha256:[0-9a-f]{64}", image_id) is None:
                    raise ValueError("approved local image returned no immutable SHA-256 identity")
                command[command.index("veriforge-rtl:dev")] = image_id
                (output / "container-image.json").write_text(json.dumps({"approved_tag": "veriforge-rtl:dev",
                                                                          "image_id": image_id}))
                code, reason = _bounded(command, output, env, experiment.budget.wall_time_seconds + 15,
                                        cancel_event, experiment.budget.max_artifact_bytes)
                path = output / "evidence/result.json"
                if code == 0 and path.exists() and not reason:
                    child = AttemptResult.model_validate_json(path.read_text())
                    return finalize_result(child, output, experiment.budget.max_artifact_bytes)
                status = "cancelled" if reason == "cancelled" else "infrastructure_error"
                verdict = reason if reason in ("timeout", "resource_limit") else "inconclusive" if reason == "cancelled" else "unsupported"
                log = output / "execution.log"
                uncertainty = log.read_text(errors="replace")[-1800:] if log.exists() else "container produced no valid result"
                uncertainty = uncertainty or "container produced no valid result"
            except (OSError, subprocess.SubprocessError, ValueError) as exc:
                uncertainty = str(exc)[:1800]
            finally:
                try:
                    subprocess.run(["docker", "rm", "-f", name], capture_output=True, timeout=10, env=env)
                except (OSError, subprocess.SubprocessError) as exc:
                    uncertainty += "; container cleanup failed: " + str(exc)[:100]
    result = AttemptResult(attempt_id="attempt-" + uuid4().hex, experiment_id=experiment.experiment_id,
        execution_status=status, verdict=verdict, uncertainties=(uncertainty,))
    (output / "experiment.json").write_text(experiment.model_dump_json(indent=2))
    return finalize_result(result, output, experiment.budget.max_artifact_bytes)
