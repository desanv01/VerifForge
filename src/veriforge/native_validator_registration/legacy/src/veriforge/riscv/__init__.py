"""Experimental source baseline. Execution acceptance requires actual core/reference runs."""
from .contracts import RiscvExperiment, Retirement, RVFIEvent, MemoryEffect, Comparison, FirstDivergence
from .trace import normalize_rvfi, parse_spike_commits, TraceFormatError, UnsupportedTrace
from .checker import compare_retirements
from .profile import PLATFORM

__all__ = ["RiscvExperiment", "Retirement", "RVFIEvent", "MemoryEffect", "Comparison", "FirstDivergence",
           "normalize_rvfi", "parse_spike_commits", "TraceFormatError", "UnsupportedTrace", "compare_retirements", "PLATFORM"]
