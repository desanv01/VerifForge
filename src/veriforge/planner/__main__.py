"""python -m veriforge.planner: local curated Meta-to-RTL investigation."""

from __future__ import annotations

import argparse
import asyncio
from decimal import Decimal
import json
from pathlib import Path
import signal

from pydantic import ValidationError

from veriforge.contracts import FifoParameters
from .local import run_local_investigation
from .models import InvestigationLimits, Pricing


def parser() -> argparse.ArgumentParser:
    from veriforge.rtl.runner import FIXTURES, ROOT

    command = argparse.ArgumentParser(description=__doc__)
    command.add_argument("--output", type=Path, default=ROOT / "artifacts/planner")
    command.add_argument("--fixture", choices=sorted(FIXTURES), default="fifo_correct")
    command.add_argument("--width", type=int, default=8)
    command.add_argument("--depth", type=int, default=4)
    command.add_argument("--rounds", type=int, default=3)
    command.add_argument("--cycles", type=int, default=30_000, help="total reserved simulation cycles")
    command.add_argument("--cycles-per-round", type=int, default=10_000)
    command.add_argument("--seconds", type=float, default=300.0)
    command.add_argument("--execution-seconds", type=float, default=30.0)
    command.add_argument("--model-seconds", type=float, default=30.0)
    command.add_argument("--model-tokens", type=int, default=200_000)
    command.add_argument("--output-tokens", type=int, default=4096)
    command.add_argument("--max-cost-usd", type=Decimal)
    command.add_argument("--input-usd-per-million", type=Decimal)
    command.add_argument("--output-usd-per-million", type=Decimal)
    return command


def main(argv: list[str] | None = None) -> int:
    command = parser()
    arguments = command.parse_args(argv)
    try:
        parameters = FifoParameters(width=arguments.width, depth=arguments.depth)
        limits = InvestigationLimits(
            max_rounds=arguments.rounds, max_total_cycles=arguments.cycles,
            max_cycles_per_experiment=arguments.cycles_per_round,
            max_wall_seconds=arguments.seconds, max_execution_seconds=arguments.execution_seconds,
            max_model_seconds=arguments.model_seconds, max_model_tokens=arguments.model_tokens,
            max_output_tokens=arguments.output_tokens, max_cost_usd=arguments.max_cost_usd,
        )
        pricing = None
        if (arguments.input_usd_per_million is None) != (arguments.output_usd_per_million is None):
            command.error("both input and output prices are required together")
        if arguments.input_usd_per_million is not None:
            pricing = Pricing(input_usd_per_million=arguments.input_usd_per_million,
                              output_usd_per_million=arguments.output_usd_per_million)
    except ValidationError:
        command.error("invalid finite investigation limits or FIFO parameters")

    original = {}
    def install_cancellation(controller):
        def cancel(signum, frame):
            controller.cancel()
        for signum in (signal.SIGINT, signal.SIGTERM):
            original[signum] = signal.getsignal(signum)
            signal.signal(signum, cancel)

    try:
        workspace, report = asyncio.run(run_local_investigation(
            arguments.output, fixture_id=arguments.fixture, parameters=parameters,
            limits=limits, pricing=pricing, on_controller=install_cancellation,
        ))
    except (OSError, ValueError, RuntimeError):
        print(json.dumps({"state": "failed", "error_code": "local_investigation_error"}))
        return 1
    finally:
        for signum, previous in original.items():
            signal.signal(signum, previous)
    print(json.dumps({"workspace": str(workspace.path), "stop_reason": report.stop_reason,
                      "checked_results": len(report.results), "report": str(workspace.path / "report.json")}))
    if report.stop_reason == "cancelled":
        return 130
    return 0 if report.stop_reason in {"round_limit", "cycle_limit", "model_stopped", "finding_observed"} else 2


if __name__ == "__main__":
    raise SystemExit(main())
