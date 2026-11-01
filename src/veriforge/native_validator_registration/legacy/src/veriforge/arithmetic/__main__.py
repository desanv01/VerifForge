import argparse
from pathlib import Path
from .contracts import ArithmeticExperiment
from .runner import run_experiment

parser = argparse.ArgumentParser(description="Run a curated arithmetic.v1 experiment without a model")
parser.add_argument("experiment", type=Path)
parser.add_argument("output", type=Path)
args = parser.parse_args()
print(run_experiment(ArithmeticExperiment.model_validate_json(args.experiment.read_text()), args.output).model_dump_json())
