"""Evidence structure checks; execution and mathematical verdicts remain adapter-owned."""
from __future__ import annotations

import json
from typing import Any, Mapping

from pydantic import StrictBool, StrictInt

from veriforge.adapters import AdapterDescriptor, ValidatedExperiment, get_adapter, registered_adapters
from veriforge.contracts import AttemptResult, WireModel


def descriptor_for(payload: ValidatedExperiment | Mapping[str, Any]) -> AdapterDescriptor:
    data = payload.model_dump(mode="python") if isinstance(payload, WireModel) else payload
    if not isinstance(data, Mapping):
        raise ValueError("experiment must be a typed curated plan")
    adapter_id = data.get("adapter")
    if adapter_id is None:
        matches = [d for d in registered_adapters() if d.target == data.get("target", "fifo")]
        if len(matches) != 1:
            raise ValueError("experiment target is not registered")
        return matches[0]
    return get_adapter(adapter_id)


def validate_experiment(payload: ValidatedExperiment | Mapping[str, Any]) -> ValidatedExperiment:
    return descriptor_for(payload).validate_experiment(payload)


class ExpectedArithmeticRow(WireModel):
    cycle: StrictInt
    operation_index: StrictInt
    valid: StrictBool
    product: StrictInt | None
    reset: StrictBool


class ObservedArithmeticRow(WireModel):
    cycle: StrictInt
    valid: StrictBool
    product: StrictInt


def validate_arithmetic_trace(raw: bytes, experiment: ValidatedExperiment, result: AttemptResult) -> None:
    """Check the stored sidecar envelope, never translate it to FIFO flags/data."""
    data = json.loads(raw)
    if not isinstance(data, dict) or set(data) != {"schema_version", "expected", "observed"}:
        raise ValueError("malformed arithmetic trace envelope")
    if data["schema_version"] != descriptor_for(experiment).trace_schema:
        raise ValueError("arithmetic trace schema mismatch")
    expected, observed = data["expected"], data["observed"]
    if not isinstance(expected, list) or not isinstance(observed, list):
        raise ValueError("arithmetic trace rows must be arrays")
    if len(expected) != len(observed) or len(expected) != experiment.cycle_count:
        raise ValueError("arithmetic trace length differs from exact stimulus")
    if result.usage.cycles != len(observed):
        raise ValueError("arithmetic trace differs from observed cycle count")
    indices = (index for index, op in enumerate(experiment.operations) for _ in range(getattr(op, "cycles", 1)))
    maximum = 2 ** (2 * experiment.parameters.width)
    for cycle, (want, got, index) in enumerate(zip(expected, observed, indices)):
        want = ExpectedArithmeticRow.model_validate(want)
        got = ObservedArithmeticRow.model_validate(got)
        if want.cycle != cycle or got.cycle != cycle or want.operation_index != index:
            raise ValueError("arithmetic trace cycles or operation indices changed")
        if not 0 <= got.product < maximum or want.product is not None and not 0 <= want.product < maximum:
            raise ValueError("arithmetic trace product exceeds unsigned width")
        if (want.valid or want.reset) and want.product is None:
            raise ValueError("defined arithmetic output requires a product")
