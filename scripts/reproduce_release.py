"""Validate retained release inputs; this command does not run engineering tools."""
from veriforge.evaluation.cli import reproduce_main

if __name__ == "__main__":
    raise SystemExit(reproduce_main())
