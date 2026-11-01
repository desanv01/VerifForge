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


def stimulus_identity(experiment) -> str:
    """Hash exact applied cycles, excluding arithmetic's presentation indices."""
    rows = [{k: v for k, v in row.items() if k != "operation_index"}
            for row in descriptor_for(experiment).expand_stimulus(experiment)]
    return hashlib.sha256(json.dumps(rows, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def stimulus_identity(experiment) -> str:
    """Hash exact applied cycles, excluding arithmetic's presentation indices."""
    rows = [{k: v for k, v in row.items() if k != "operation_index"}
            for row in descriptor_for(experiment).expand_stimulus(experiment)]
    return hashlib.sha256(json.dumps(rows, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


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


def checked_family_attempt(attempt_dir):
    """Recompute a retained family verdict with installed code, never copied code.

    This strengthens new repair workflows without relabeling historical imports.
    Observation provenance still depends on the supplied/native execution evidence.
    """
    from pathlib import Path
    from .workflow import load_attempt, _read, _artifact
    plan, result = load_attempt(attempt_dir)
    descriptor = descriptor_for(plan)
    if descriptor.adapter_id not in {"rtl.arithmetic.v1", "rtl.axi_lite.v1"}:
        raise ValueError("checked repair supports arithmetic and AXI only")
    expected_identity = descriptor.expected_identity(plan)
    if any(getattr(result.identity, key) != value for key, value in expected_identity.items()):
        raise ValueError("repair source/checker/contract/stimulus identity changed")
    if result.execution_status != "completed":
        raise ValueError("repair evidence must be completed")
    def read(name):
        ref = next((ref for ref in result.artifacts if ref.path == name), None)
        if ref is None:
            raise ValueError("checked repair requires complete recorded artifacts")
        raw = _read(_artifact(Path(attempt_dir).resolve(), ref.path), ref.size_bytes)
        if len(raw) != ref.size_bytes or _digest(raw) != ref.sha256:
            raise ValueError("repair artifact changed during checking")
        return raw
    if _strict_json(read("stimulus.json")) != descriptor.expand_stimulus(plan):
        raise ValueError("repair recorded stimulus differs from exact plan")
    if descriptor.adapter_id == "rtl.axi_lite.v1":
        from veriforge.axi_lite import contracts
        from veriforge.axi_lite.oracle import check
        if _digest(read("checker-contracts.py")) != _digest(Path(contracts.__file__).read_bytes()):
            raise ValueError("repair AXI checker schema changed")
        checked = check(plan, _strict_json(read("observed.json")))
        if checked.model_dump(mode="json") != _strict_json(read("check.json")):
            raise ValueError("repair AXI verdict differs from immutable checker")
    else:
        from veriforge.arithmetic import oracle
        trace = _strict_json(read("arithmetic-trace.json"))
        expected = oracle.expected_trace(descriptor.expand_stimulus(plan), plan.parameters.latency)
        if trace["expected"] != expected or trace["observed"] != _strict_json(read("observed.json")):
            raise ValueError("repair arithmetic trace differs from mathematical expectation")
        difference = oracle.compare(expected, trace["observed"])
        verdict = "design_mismatch" if difference else "pass_within_scope"
        signature = result.failure_signature
        if result.verdict != verdict or bool(difference) != bool(signature):
            raise ValueError("repair arithmetic verdict differs from immutable checker")
        if difference:
            failure_class = "arithmetic." + difference["signal"]
            payload = {"contract": result.identity.contract_hash, "checker": result.identity.checker_hash,
                       "requirement": difference["requirement"], "class": failure_class}
            values = (difference["requirement"], failure_class, difference["cycle"],
                      str(difference["expected"]), str(difference["observed"]),
                      _digest(json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()))
            if values != (signature.requirement_id, signature.failure_class, signature.first_cycle,
                          signature.expected, signature.observed, signature.signature_hash):
                raise ValueError("repair arithmetic failure signature changed")
        # Bind checker and contract snapshots as well as their installed identities.
        contract = descriptor.contract_model.model_validate(_strict_json(read("contract.json")))
        if _digest(read("checker.py")) != result.identity.checker_hash or canonical_hash(contract) != result.identity.contract_hash:
            raise ValueError("repair arithmetic checker/contract snapshots changed")
        environment = read("environment.json")
        if (_digest(environment) != result.identity.environment_hash or
                _strict_json(environment).get("tools") != [tool.model_dump(mode="json") for tool in result.tool_versions]):
            raise ValueError("repair arithmetic environment snapshot changed")
        revision = {"source": _digest(read(descriptor.source_artifact)),
                    "variant": descriptor.fixture_source(plan.fixture_id)["variant"],
                    "parameters": plan.parameters.model_dump()}
        if _digest(json.dumps(revision, sort_keys=True, separators=(",", ":")).encode()) != result.identity.source_hash:
            raise ValueError("repair arithmetic source snapshot changed")
    return plan, result


def _checked_runner(runner):
    def execute(plan, output):
        result = AttemptResult.model_validate(runner(plan, output).model_dump(mode="json"))
        if result.execution_status == "completed" and result.verdict in {
                "pass_within_scope", "design_mismatch", "property_failure"}:
            recorded_plan, recorded_result = checked_family_attempt(output)
            if recorded_plan != plan or recorded_result != result:
                raise ValueError("runner result differs from recorded repair evidence")
        return result
    return execute


def replay_family_attempt(attempt_dir, output_dir, *, runner=None):
    """Exact replay with independent retained-verdict checking and saved lineage."""
    from pathlib import Path
    from .workflow import replay_attempt, _save
    plan, _ = checked_family_attempt(attempt_dir)
    output = Path(output_dir).resolve()
    original = Path(attempt_dir).resolve()
    if output.is_relative_to(original) or original.is_relative_to(output):
        raise ValueError("repair output must be separate from original evidence")
    runner = runner or descriptor_for(plan).resolve_runner("native")
    record = replay_attempt(attempt_dir, output, runner=_checked_runner(runner))
    _save(output / "replay.json", record)
    return record


def _axi_reduction_cycles(plan):
    rows = plan.model_dump(mode="json")["cycles"]
    # Keep the initial reset, then remove chunks; the unchanged checker rejects
    # broken manager handshakes/harness outcomes rather than calling them repairs.
    size = max(1, (len(rows) - 1) // 2)
    while size:
        for start in range(1, len(rows), size):
            yield rows[:start] + rows[start + size:]
        size //= 2
    for index, row in enumerate(rows):
        for field in ("aw_addr", "aw_prot", "w_data", "w_strb", "ar_addr", "ar_prot"):
            if row[field]:
                yield rows[:index] + [{**row, field: 0}] + rows[index + 1:]


def reduce_axi_failure(attempt_dir, output_dir, *, runner, max_attempts=30):
    """Bounded cycle/payload deletion; no global minimality or protocol waiver."""
    from pathlib import Path
    from pydantic import ValidationError
    from .workflow import ReductionRecord, _identity_matches, _new_output, _save, same_failure
    if type(max_attempts) is not int or not 1 <= max_attempts <= 1000:
        raise ValueError("max_attempts must be between 1 and 1000")
    original_plan, original_result = checked_family_attempt(attempt_dir)
    if descriptor_for(original_plan).adapter_id != "rtl.axi_lite.v1" or not same_failure(original_result, original_result):
        raise ValueError("AXI reduction requires a checked original design/property failure")
    source, output = Path(attempt_dir).resolve(), Path(output_dir).resolve()
    if output.is_relative_to(source) or source.is_relative_to(output):
        raise ValueError("reduction output must be separate from original evidence")
    root = _new_output(output)
    best, best_result = original_plan, original_result
    results, submitted, seen = [], [], {canonical_hash(best)}
    execute = _checked_runner(runner)
    while len(results) < max_attempts:
        improved = False
        for rows in _axi_reduction_cycles(best):
            try:
                proposed = validate_experiment({**best.model_dump(mode="json"), "cycles": rows})
            except ValidationError:
                continue
            key = canonical_hash(proposed)
            if key in seen:
                continue
            seen.add(key)
            result = execute(proposed, root / f"attempt-{len(results):04d}")
            results.append(result)
            submitted.append(proposed)
            if _identity_matches(result, proposed, original_result) and same_failure(original_result, result):
                best, best_result, improved = proposed, result, True
                break
            if len(results) >= max_attempts:
                break
        if not improved:
            break
    record = ReductionRecord(original_experiment=original_plan, original_result=original_result,
        reduced_experiment=best, reduced_result=best_result, attempts=tuple(results),
        submitted_experiments=tuple(submitted), max_attempts=max_attempts,
        improved=canonical_hash(best) != canonical_hash(original_plan))
    _save(root / "reduction.json", record)
    return record
