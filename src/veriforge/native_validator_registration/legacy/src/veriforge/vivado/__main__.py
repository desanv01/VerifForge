"""Local import/preparation commands; does not start Vivado."""
from argparse import ArgumentParser
from dataclasses import asdict
import json
from pathlib import Path

from .adapter import Recipe, import_log, inventory, prepare_repair_pair


def main() -> None:
    parser = ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("inventory")
    prepare = commands.add_parser("prepare")
    prepare.add_argument("recipe", choices=[Recipe.WRONG_TOP.value, Recipe.MISSING_SOURCE.value])
    prepare.add_argument("--output", type=Path, required=True)
    ingest = commands.add_parser("import-log")
    ingest.add_argument("log", type=Path)
    ingest.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if args.command == "inventory":
        result = asdict(inventory())
    elif args.command == "prepare":
        result = [asdict(item) for item in prepare_repair_pair(args.output, Recipe(args.recipe))]
    else:
        result = asdict(import_log(args.log, args.output))
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
