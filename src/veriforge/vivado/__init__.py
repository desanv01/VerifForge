"""Bounded report adapters and explicitly installed fixed candidate orchestration."""

from .adapter import (
    Capability, Diagnostic, ImportedReport, PreparedAttempt, Recipe, StageObservation,
    import_log, inventory, prepare_attempt, prepare_repair_pair,
)
from .readiness import BatchPlan, BatchRecipe, ProjectReadiness, inspect_exercise3, plan_batch, render_batch_recipe
from .recorded import PartObservation, RecordedAttempt, RecordedBudget, RecordedStorage, ReportAvailability, inspect_recorded_attempt
from .investigation import (
    VivadoProjectIdentity, VivadoExecution, VivadoReportFact, VivadoFinding,
    VivadoInvestigation, VivadoCandidateComparison, investigate_vivado_reports, compare_vivado_candidate, vivado_projection,
)
from .repair_execution import ControllerInstallation, RepairHooks, RepairPins, PrivateFixedBatchAdapter
from .repair_runtime import RepairTicket, VivadoRepairRuntime

__all__ = [
    "Capability", "Diagnostic", "ImportedReport", "PreparedAttempt", "Recipe",
    "StageObservation", "import_log", "inventory", "prepare_attempt", "prepare_repair_pair",
    "BatchPlan", "BatchRecipe", "ProjectReadiness", "inspect_exercise3", "plan_batch", "render_batch_recipe",
    "PartObservation", "RecordedAttempt", "ReportAvailability", "inspect_recorded_attempt",
    "RecordedBudget", "RecordedStorage",
    "VivadoProjectIdentity", "VivadoExecution", "VivadoReportFact", "VivadoFinding",
    "VivadoInvestigation", "VivadoCandidateComparison", "investigate_vivado_reports", "compare_vivado_candidate",
    "vivado_projection",
    "ControllerInstallation", "RepairHooks", "RepairPins", "PrivateFixedBatchAdapter",
    "RepairTicket", "VivadoRepairRuntime",
]
