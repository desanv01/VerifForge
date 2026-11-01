import argparse
from pathlib import Path
from .contracts import RiscvExperiment
from .runner import run_experiment

parser = argparse.ArgumentParser(description='Curated RV32I RTL/reference comparison; actual toolchain required')
parser.add_argument('experiment', type=Path)
parser.add_argument('output', type=Path)
parser.add_argument('--saved-program', type=Path, help='Trusted completed control package; never an uploaded program')
args = parser.parse_args()
result = run_experiment(RiscvExperiment.model_validate_json(args.experiment.read_text()), args.output, saved_program=args.saved_program)
print(result.model_dump_json())
