import argparse
from pathlib import Path
from veriforge.contracts import Experiment
from .runner import replay, run_experiment

parser = argparse.ArgumentParser(description="Run/replay curated FIFO experiments without a model")
parser.add_argument("experiment", type=Path)
parser.add_argument("output", type=Path)
parser.add_argument("--replay", action="store_true")
args = parser.parse_args()
result = replay(args.experiment, args.output) if args.replay else run_experiment(Experiment.model_validate_json(args.experiment.read_text()), args.output)
print(result.model_dump_json())
