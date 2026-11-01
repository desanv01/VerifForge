"""Local, finite investigations and a cancellation-aware bridge to trusted RTL runners."""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from contextlib import suppress
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import platform
import threading
from typing import Protocol
from uuid import uuid4

from veriforge.contracts import (
    AttemptResult, BehavioralContract, Experiment, FifoParameters, ProjectRevision,
    SourceFile, canonical_hash,
)
from .controller import InvestigationController
from .models import InvestigationLimits, InvestigationReport, Pricing
from .provider import MetaProvider, PlannerProvider


class BlockingRunner(Protocol):
    def __call__(self, experiment: Experiment, output_dir: Path, *,
                 cancel_event: threading.Event) -> AttemptResult: ...


class RunnerExecutionError(RuntimeError):
    """Safe runner failure code; raw exceptions never enter planner records."""


def _write_json(path: Path, value: dict) -> None:
    """Atomic checkpoint replacement within this unique workspace, not DB durability."""
    temporary = path.with_name(f".{path.name}.{uuid4().hex}.tmp")
    with temporary.open("x", encoding="utf-8") as stream:
        json.dump(value, stream, indent=2, ensure_ascii=False, allow_nan=False)
        stream.flush()
        os.fsync(stream.fileno())
    os.replace(temporary, path)


def curated_fifo_inputs(fixture_id: str, parameters: FifoParameters | None = None
                        ) -> tuple[ProjectRevision, BehavioralContract]:
    """Bind installed curated source variant/parameters to the RTL owner's identity."""
    from veriforge.rtl import reviewed_contract
    from veriforge.rtl.runner import FIXTURES, ROOT, digest, encoded

    if fixture_id not in FIXTURES:
        raise ValueError("fixture is not installed")
    parameters = parameters or FifoParameters()
    source_file_hash = hashlib.sha256((ROOT / "targets/fifo/fifo.sv").read_bytes()).hexdigest()
    source_hash = digest(encoded({"source": source_file_hash, "variant": FIXTURES[fixture_id],
                                  "parameters": parameters.model_dump()}))
    revision = ProjectRevision(
        project_id="curated-fifo", revision_id=f"local-{source_hash[:32]}",
        source_hash=source_hash, fixture_id=fixture_id,
        sources=(SourceFile(path="targets/fifo/fifo.sv", sha256=source_file_hash),),
    )
    return revision, reviewed_contract(parameters)


def platform_runner() -> BlockingRunner:
    from veriforge.rtl import run_container_experiment, run_experiment

    system = platform.system()
    if system == "Windows":
        return run_container_experiment
    if system == "Linux":
        return run_experiment
    raise ValueError("local execution requires Windows/Docker or Linux/Verilator")


class LocalWorkspace:
    """Unique local evidence; checkpoints preserve completed attempts, not resumability."""

    def __init__(self, parent: Path, *, revision: ProjectRevision,
                 contract: BehavioralContract, limits: InvestigationLimits,
                 pricing: Pricing | None, model: str):
        parent = Path(parent).resolve()
        parent.mkdir(parents=True, exist_ok=True)
        self.path = parent / f"investigation-{uuid4().hex}"
        self.path.mkdir(exist_ok=False)
        (self.path / "rounds").mkdir()
        _write_json(self.path / "manifest.json", {
            "schema_version": "1", "strategy": "meta", "model": model,
            "created_at": datetime.now(timezone.utc).isoformat(),
            "revision": revision.model_dump(mode="json"),
            "contract": contract.model_dump(mode="json"),
            "limits": limits.model_dump(mode="json"),
            "pricing": pricing.model_dump(mode="json") if pricing else None,
            "contract_hash": canonical_hash(contract),
            "persistence": "local_checkpoints_no_resume_guarantee",
        })
        self.state("ready")

    def state(self, state: str, **fields) -> None:
        _write_json(self.path / "state.json", {"state": state, **fields})

    def finish(self, report: InvestigationReport) -> None:
        _write_json(self.path / "report.json", report.model_dump(mode="json"))
        self.state("settled", stop_reason=report.stop_reason,
                   completed_results=len(report.results), report="report.json")


class LocalRunnerExecutor:
    """One dedicated runner thread at a time, with explicit token and shielded join.

    Cancelling this coroutine always signals cancel_event and waits for the blocking
    runner to return after its process/container cleanup. A late successful result is
    preserved on disk but discarded, never returned to the investigation controller.
    The trusted runner must honor its token and bound its own cleanup; this bridge
    deliberately does not pretend Python can forcibly stop an uncooperative thread.
    """

    def __init__(self, workspace: LocalWorkspace, runner: BlockingRunner | None = None, *,
                 before_run: Callable[[Experiment], None] | None = None):
        self._workspace = workspace
        self._runner = runner or platform_runner()
        self._before_run = before_run
        self._sequence = 0
        self._active = False

    async def __call__(self, experiment: Experiment) -> AttemptResult:
        if self._active:
            raise RunnerExecutionError("executor_already_active")
        self._sequence += 1
        directory = self._workspace.path / "rounds" / f"round-{self._sequence:04d}"
        directory.mkdir(exist_ok=False)
        output = directory / "attempt"
        output.mkdir()
        self._active = True
        token = threading.Event()
        loop = asyncio.get_running_loop()
        settled = loop.create_future()
        cancelled = False
        worker = None
        try:
            _write_json(directory / "experiment.json", experiment.model_dump(mode="json"))
            _write_json(directory / "settlement.json", {"state": "preparing", "delivered": False})
            if self._before_run:
                self._before_run(experiment)

            def invoke() -> None:
                outcome = None
                failed = False
                try:
                    outcome = self._runner(experiment, output, cancel_event=token)
                except BaseException:
                    # No raw exception string/body is retained across this boundary.
                    failed = True
                finally:
                    loop.call_soon_threadsafe(settled.set_result, (outcome, failed))

            worker = threading.Thread(target=invoke, name=f"vf-planner-{self._sequence}", daemon=False)
            _write_json(directory / "settlement.json", {"state": "running", "delivered": False})
            worker.start()
            while True:
                try:
                    outcome, failed = await asyncio.shield(settled)
                    break
                except asyncio.CancelledError:
                    cancelled = True
                    token.set()
                    # A failing checkpoint must not bypass child-process cleanup.
                    with suppress(OSError):
                        _write_json(directory / "settlement.json", {
                            "state": "cancelling_waiting_for_runner", "delivered": False,
                        })
                    # Repeated task.cancel() calls cannot bypass runner settlement.
            worker.join()
            if cancelled:
                _write_json(directory / "settlement.json", {
                    "state": "discarded_after_cancel", "delivered": False,
                    "runner_settled": True, "cancel_requested": True,
                })
                raise asyncio.CancelledError
            if failed or not isinstance(outcome, AttemptResult):
                raise RunnerExecutionError("runner_error")
            _write_json(directory / "runner-result.json", outcome.model_dump(mode="json"))
            _write_json(directory / "settlement.json", {
                "state": "delivered_to_controller", "delivered": True,
                "runner_settled": True, "identity_validation": "controller_required",
            })
            return outcome
        except asyncio.CancelledError:
            raise
        except BaseException:
            _write_json(directory / "settlement.json", {
                "state": "executor_error", "delivered": False,
                "runner_settled": worker is None or not worker.is_alive(),
            })
            raise
        finally:
            self._active = False


def coordination_control() -> str:
    from veriforge.rtl.runner import ROOT

    path = ROOT / ".coordination/current-state.json"
    if not path.exists():
        return "RUNNING"
    try:
        return json.loads(path.read_text(encoding="utf-8")).get("control", "PAUSED")
    except (OSError, ValueError):
        return "PAUSED"


async def run_local_investigation(
    output_parent: Path, *, fixture_id: str = "fifo_correct",
    parameters: FifoParameters | None = None, limits: InvestigationLimits | None = None,
    pricing: Pricing | None = None, provider: PlannerProvider | None = None,
    runner: BlockingRunner | None = None, control: Callable[[], str] = coordination_control,
    on_controller: Callable[[InvestigationController], None] | None = None,
) -> tuple[LocalWorkspace, InvestigationReport]:
    """Run a finite local campaign. Provider/runner injection is for trusted unit tests."""
    revision, contract = curated_fifo_inputs(fixture_id, parameters)
    limits = limits or InvestigationLimits()
    provider = provider or MetaProvider()
    workspace = LocalWorkspace(output_parent, revision=revision, contract=contract,
                               limits=limits, pricing=pricing, model=provider.model)

    def check_inputs(experiment: Experiment) -> None:
        current_revision, current_contract = curated_fifo_inputs(fixture_id, contract.parameters)
        if (current_revision != revision or current_contract != contract or
            experiment.fixture_id != revision.fixture_id or experiment.parameters != contract.parameters):
            raise RunnerExecutionError("installed_identity_changed")
        if control() != "RUNNING":
            raise RunnerExecutionError("execution_paused")

    executor = LocalRunnerExecutor(workspace, runner, before_run=check_inputs)
    controller = InvestigationController(provider, executor, revision=revision, contract=contract,
                                         limits=limits, pricing=pricing, control=control)
    if on_controller:
        on_controller(controller)
    workspace.state("running")
    try:
        report = await controller.run()
        workspace.finish(report)
        return workspace, report
    except asyncio.CancelledError:
        workspace.state("interrupted", cleanup="runner_settled_before_propagation",
                        report_available=False)
        raise
    except BaseException:
        workspace.state("failed", error_code="local_investigation_error", report_available=False)
        raise
