"""Private evaluation records and bounded FIFO protocol source.

The harness has injected execution interfaces, not an operational provider/tool
service. Real transport, cancellation and ambiguous-effect recovery are separate.
"""
from .records import EvaluationLog, EvaluationLogError, EvaluationSnapshot

__all__ = ["EvaluationLog", "EvaluationLogError", "EvaluationSnapshot"]
