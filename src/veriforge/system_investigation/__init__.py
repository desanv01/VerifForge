"""Narrow source-only native provenance bridges; no tool execution capability."""
from .models import (AddressBinding, BoardIntent, BridgeReport, CandidateComparison,
                     PinBinding, RegisterIntent, RootArtifact, SourceArtifact)
from .bridge import bundle_files, compare_board, compare_candidate, compare_registers

__all__ = ["AddressBinding", "BoardIntent", "BridgeReport", "CandidateComparison", "PinBinding",
           "RegisterIntent", "RootArtifact", "SourceArtifact", "bundle_files", "compare_board",
           "compare_candidate", "compare_registers"]
