"""Curated adapter descriptors; API integration is a separate versioned change."""
from .registry import (
    AdapterDescriptor, AdapterRunner, CancellationToken, ExecutionMode,
    ReviewedContract, UnknownAdapterError, UnsupportedFixtureError,
    ValidatedExperiment, get_adapter, registered_adapters,
)

__all__ = [
    "AdapterDescriptor", "AdapterRunner", "CancellationToken", "ExecutionMode",
    "ReviewedContract", "UnknownAdapterError", "UnsupportedFixtureError",
    "ValidatedExperiment", "get_adapter", "registered_adapters",
]
