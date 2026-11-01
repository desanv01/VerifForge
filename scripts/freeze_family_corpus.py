"""Freeze private installed corpus inputs; no tools, providers or scoring."""
from veriforge.evaluation.cli import freeze_corpus_main

if __name__ == '__main__':
    raise SystemExit(freeze_corpus_main())
