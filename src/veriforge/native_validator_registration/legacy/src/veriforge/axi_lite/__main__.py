"""Local trusted execution entrypoint; requires the approved Linux environment."""
import argparse
from pathlib import Path

from .contracts import AxiLiteExperiment
from .runner import run_experiment


def main():
    parser = argparse.ArgumentParser(description="Run exact curated axi_lite.v1 cycles")
    parser.add_argument("experiment", type=Path)
    parser.add_argument("output", type=Path)
    args = parser.parse_args()
    print(run_experiment(AxiLiteExperiment.model_validate_json(args.experiment.read_text()),
                         args.output).model_dump_json())


if __name__ == "__main__":
    main()
