"""Run in the installed/uv-locked project environment; no experiment execution."""
from veriforge.evaluation.cli import collect_main

if __name__ == "__main__":
    raise SystemExit(collect_main())
