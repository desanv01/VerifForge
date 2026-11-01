"""Evidence structure checks; execution and mathematical verdicts remain adapter-owned."""
from __future__ import annotations

import json
import hashlib
from typing import Any, Callable, Mapping

from pydantic import StrictBool, StrictInt

from veriforge.adapters import AdapterDescriptor, ValidatedExperiment, get_adapter, registered_adapters
from veriforge.contracts import AttemptResult, WireModel, canonical_hash


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


def _strict_json(raw: bytes):
    def unique(pairs):
        result = {}
        for key, value in pairs:
            if key in result:
                raise ValueError("duplicate native AXI evidence JSON key")
            result[key] = value
        return result
    return json.loads(raw, object_pairs_hook=unique)


def _digest(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def validate_axi_trace(raw: bytes, experiment: ValidatedExperiment, result: AttemptResult, *,
                       artifact_reader: Callable[[str], bytes] | None = None) -> None:
    """Validate native recorded evidence and identity bindings, not a new HDL verdict.

    The reader must return bounded bytes verified against the attempt's manifest.
    Never import/execute copied checker or schema files.
    """
    from veriforge.axi_lite.contracts import AxiLiteContract, AxiLiteExperiment, AxiLiteObservation, AxiLiteTrace, CheckOutcome
    if not isinstance(experiment, AxiLiteExperiment):
        raise ValueError("AXI native trace requires its exact typed family experiment")
    experiment = AxiLiteExperiment.model_validate(experiment.model_dump(mode="json"))
    if result.identity is None or artifact_reader is None:
        raise ValueError("AXI native trace requires identity and verified companion artifacts")
    if result.trace:
        raise ValueError("AXI native observations cannot be coerced into FIFO trace fields")
    data = _strict_json(raw)
    if (not isinstance(data, dict) or set(data) != {"schema_version", "observations"}
            or data.get("schema_version") != "axi_lite.trace.v1" or not isinstance(data["observations"], list)):
        raise ValueError("malformed native AXI trace envelope")
    if any(not isinstance(row, dict) or set(row) != set(AxiLiteObservation.model_fields) for row in data["observations"]):
        raise ValueError("native AXI observation must contain exactly all bus signal fields")
    trace = AxiLiteTrace.model_validate(data)
    if len(trace.observations) != experiment.cycle_count or any(row.cycle != i for i, row in enumerate(trace.observations)):
        raise ValueError("AXI observation cycles differ from exact experiment")
    check_data = _strict_json(artifact_reader("check.json"))
    if not isinstance(check_data, dict) or set(check_data) != set(CheckOutcome.model_fields):
        raise ValueError("malformed native AXI checker outcome envelope")
    checked = CheckOutcome.model_validate(check_data)
    if checked.verdict != result.verdict or checked.checked_cycles > experiment.cycle_count:
        raise ValueError("AXI checker outcome differs from stored result or cycle scope")
    if result.usage.cycles != (experiment.cycle_count if checked.checked_cycles else 0):
        raise ValueError("AXI executed cycles differ from observed/checker scope")
    if checked.verdict == "pass_within_scope" and checked.checked_cycles != experiment.cycle_count:
        raise ValueError("AXI pass requires a complete checked trace")
    failure, signature = checked.failure, result.failure_signature
    if failure is not None:
        if (signature is None or failure.cycle >= experiment.cycle_count or checked.checked_cycles != failure.cycle + 1
                or (failure.requirement_id, failure.failure_class, failure.cycle, failure.expected, failure.observed)
                != (signature.requirement_id, signature.failure_class, signature.first_cycle, signature.expected, signature.observed)):
            raise ValueError("AXI checked failure differs from immutable result signature")
        signature_payload = {"contract": result.identity.contract_hash, "checker": result.identity.checker_hash,
                             "requirement": failure.requirement_id, "class": failure.failure_class}
        if _digest(json.dumps(signature_payload, sort_keys=True, separators=(",", ":")).encode()) != signature.signature_hash:
            raise ValueError("AXI semantic failure signature hash mismatch")
    elif signature is not None:
        raise ValueError("AXI outcome without failure cannot carry a design signature")
    checker = artifact_reader("checker.py")
    schema = artifact_reader("checker-contracts.py")
    contract = AxiLiteContract.model_validate(_strict_json(artifact_reader("contract.json")))
    environment_raw = artifact_reader("environment.json")
    environment = _strict_json(environment_raw)
    if (not isinstance(environment, dict) or _digest(checker) != result.identity.checker_hash
            or _digest(schema) != environment.get("contracts_hash")
            or _digest(environment_raw) != result.identity.environment_hash
            or canonical_hash(contract) != result.identity.contract_hash
            or contract.checker.sha256 != result.identity.checker_hash
            or contract.parameters != experiment.parameters
            or contract.contract_id != experiment.contract_id
            or environment.get("tools") != [tool.model_dump(mode="json") for tool in result.tool_versions]):
        raise ValueError("AXI checker/schema/contract/environment identity mismatch")
    descriptor = descriptor_for(experiment)
    if descriptor.source_artifact != "registers.sv":
        raise ValueError("AXI descriptor source artifact is unsupported")
    source_identity = {"source": _digest(artifact_reader("registers.sv")),
                       "variant": descriptor.fixture_source(experiment.fixture_id)["variant"],
                       "parameters": experiment.parameters.model_dump()}
    if (result.identity.experiment_hash != canonical_hash(experiment)
            or _digest(json.dumps(source_identity, sort_keys=True, separators=(",", ":")).encode()) != result.identity.source_hash):
        raise ValueError("AXI exact experiment/source snapshot identity mismatch")
    stimulus = _strict_json(artifact_reader("stimulus.json"))
    if stimulus != [cycle.model_dump(mode="json") for cycle in experiment.cycles]:
        raise ValueError("AXI stored cycle stimulus differs from exact experiment")


def validate_family_trace(raw: bytes, experiment: ValidatedExperiment, result: AttemptResult, *,
                          artifact_reader: Callable[[str], bytes] | None = None) -> None:
    """Fixed trusted schema dispatch, preserving family-specific signal semantics."""
    schema = descriptor_for(experiment).trace_schema
    if schema == "arithmetic.trace.v1":
        validate_arithmetic_trace(raw, experiment, result)
    elif schema == "axi_lite.trace.v1":
        validate_axi_trace(raw, experiment, result, artifact_reader=artifact_reader)
    else:
        raise ValueError("family trace schema has no accepted evidence validator")
