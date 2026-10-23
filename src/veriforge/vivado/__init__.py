"""Bounded Vivado fixture preparation and report import (no live executor)."""

from .adapter import (
    Capability, Diagnostic, ImportedReport, PreparedAttempt, Recipe, StageObservation,
    import_log, inventory, prepare_attempt, prepare_repair_pair,
)

__all__ = [
    "Capability", "Diagnostic", "ImportedReport", "PreparedAttempt", "Recipe",
    "StageObservation", "import_log", "inventory", "prepare_attempt", "prepare_repair_pair",
]
