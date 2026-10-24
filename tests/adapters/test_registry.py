from dataclasses import FrozenInstanceError, replace
from pathlib import Path
import threading

import pytest
from pydantic import ValidationError

from veriforge.adapters import get_adapter, registered_adapters, UnknownAdapterError, UnsupportedFixtureError
from veriforge.arithmetic import ArithmeticExperiment, Parameters, directed_experiment, reviewed_contract as arithmetic_contract
from veriforge.contracts import AttemptResult, Experiment, FifoParameters, canonical_hash
from veriforge.rtl import reviewed_contract as fifo_contract


def fifo_payload():
    return {"experiment_id": "registry-unit-fifo", "fixture_id": "fifo_correct",
            "objective_ids": ["FIFO_BEHAVIOR"], "operations": [{"op": "reset"}, {"op": "write", "value": 42}, {"op": "read"}]}


def arithmetic_payload():
    return directed_experiment().model_dump(mode="json")


def test_only_curated_identifiers_registered_and_descriptors_frozen():
    assert tuple(item.adapter_id for item in registered_adapters()) == ("rtl.fifo.v1", "rtl.arithmetic.v1")
    with pytest.raises(FrozenInstanceError):
        get_adapter("rtl.fifo.v1").target = "changed"


@pytest.mark.parametrize("identifier", ["fifo", "arithmetic.v1", "os.system", "../../rtl", "", [], None])
def test_unknown_identifiers_fail_closed(identifier):
    with pytest.raises(UnknownAdapterError):
        get_adapter(identifier)


@pytest.mark.parametrize("identifier,payload,model", [
    ("rtl.fifo.v1", fifo_payload, Experiment),
    ("rtl.arithmetic.v1", arithmetic_payload, ArithmeticExperiment),
])
def test_family_validation_and_fresh_json_schema(identifier, payload, model):
    adapter = get_adapter(identifier)
    experiment = adapter.validate_experiment(payload())
    assert type(experiment) is model
    schema = adapter.experiment_schema()
    assert schema["additionalProperties"] is False
    assert schema["properties"]["fixture_id"]["enum"] == list(adapter.fixture_ids)
    schema["properties"]["fixture_id"]["enum"].clear()
    assert adapter.experiment_schema()["properties"]["fixture_id"]["enum"]
    assert adapter.contract_schema()["additionalProperties"] is False


def test_fifo_schema_is_not_extended_or_mutated():
    fifo = get_adapter("rtl.fifo.v1")
    assert "adapter" not in fifo.experiment_schema()["properties"]
    assert "fifo_full_bug" in Experiment.model_json_schema()["properties"]["fixture_id"]["enum"]
    for fixture in ("fifo_full_bug", "fifo_order_bug"):
        payload = {**fifo_payload(), "fixture_id": fixture}
        assert Experiment.model_validate(payload).fixture_id == fixture
        with pytest.raises(UnsupportedFixtureError):
            fifo.validate_experiment(payload)


@pytest.mark.parametrize("identifier,payload", [("rtl.fifo.v1", arithmetic_payload), ("rtl.arithmetic.v1", fifo_payload)])
def test_no_cross_family_coercion(identifier, payload):
    with pytest.raises(ValidationError):
        get_adapter(identifier).validate_experiment(payload())


@pytest.mark.parametrize("field,value", [("command", "echo unsafe"), ("source", "../../arbitrary.sv"), ("python", "exec('x')"), ("adapter", "os.system")])
def test_fifo_rejects_executable_or_dynamic_fields(field, value):
    with pytest.raises(ValidationError):
        get_adapter("rtl.fifo.v1").validate_experiment({**fifo_payload(), field: value})


@pytest.mark.parametrize("identifier,payload", [("rtl.fifo.v1", fifo_payload), ("rtl.arithmetic.v1", arithmetic_payload)])
def test_preserves_existing_strict_width_and_budget_validation(identifier, payload):
    data = payload()
    data["budget"] = {"max_cycles": 1}
    with pytest.raises(ValidationError):
        get_adapter(identifier).validate_experiment(data)
    data = payload()
    data.setdefault("parameters", {})["width"] = True
    with pytest.raises(ValidationError):
        get_adapter(identifier).validate_experiment(data)


def test_contract_is_family_specific_and_parameter_bound():
    fifo = get_adapter("rtl.fifo.v1")
    params = FifoParameters(width=16, depth=8)
    assert canonical_hash(fifo.reviewed_contract(params)) == canonical_hash(fifo_contract(params))
    payload = {**fifo_payload(), "parameters": params.model_dump()}
    assert fifo.contract_for(payload).parameters == params
    arithmetic = get_adapter("rtl.arithmetic.v1")
    params = Parameters(width=16)
    assert canonical_hash(arithmetic.reviewed_contract({"width": 16})) == canonical_hash(arithmetic_contract(params))
    assert arithmetic.contract_for(arithmetic_payload()).parameters == Parameters()
    assert arithmetic.trace_artifact == "arithmetic-trace.json"
    assert arithmetic.trace_schema == "arithmetic.trace.v1"
    assert fifo.trace_artifact is None and fifo.trace_schema is None
    with pytest.raises(ValidationError):
        fifo.reviewed_contract(Parameters())


def test_existing_model_instances_are_revalidated_at_registry_boundary():
    descriptor = get_adapter("rtl.fifo.v1")
    experiment = Experiment.model_validate(fifo_payload()).model_copy(update={"seed": True})
    with pytest.raises(ValidationError):
        descriptor.validate_experiment(experiment)
    invalid_parameters = FifoParameters().model_copy(update={"width": True})
    with pytest.raises(ValidationError):
        descriptor.reviewed_contract(invalid_parameters)


@pytest.mark.parametrize("identifier,payload", [("rtl.fifo.v1", fifo_payload), ("rtl.arithmetic.v1", arithmetic_payload)])
@pytest.mark.parametrize("mode", ["native", "container"])
def test_explicit_runner_resolution_validates_and_forwards_cancel_token(tmp_path, identifier, payload, mode):
    calls = []
    def stub(experiment, output_dir, *, cancel_event=None):
        calls.append((experiment, output_dir, cancel_event))
        return AttemptResult(attempt_id="registry-unit-stub", experiment_id=experiment.experiment_id,
                             execution_status="completed", verdict="unsupported",
                             uncertainties=("Unit stub only; no simulator executed.",))
    def wrong_backend(*args, **kwargs):
        pytest.fail("Execution mode selected the wrong backend")
    descriptor = get_adapter(identifier)
    descriptor = replace(descriptor, _native_runner=stub if mode == "native" else wrong_backend,
                         _container_runner=stub if mode == "container" else wrong_backend)
    token = threading.Event()
    token.set()
    output = tmp_path / "attempt"
    result = descriptor.resolve_runner(mode)(payload(), output, cancel_event=token)
    assert result.verdict == "unsupported"
    assert len(calls) == 1 and type(calls[0][0]) is descriptor.experiment_model
    assert calls[0][1] == output and calls[0][2] is token and token.is_set()
    assert not output.exists()  # Descriptor resolution itself creates no run artifacts.


def test_invalid_execution_never_reaches_backend(tmp_path):
    def forbidden(*args, **kwargs):
        pytest.fail("Invalid input reached an execution backend")
    descriptor = replace(get_adapter("rtl.fifo.v1"), _native_runner=forbidden, _container_runner=forbidden)
    with pytest.raises(ValueError, match="mode"):
        descriptor.resolve_runner("os.system")
    runner = descriptor.resolve_runner()
    with pytest.raises(TypeError, match="is_set"):
        runner(fifo_payload(), tmp_path / "invalid-token", cancel_event=object())
    with pytest.raises(ValidationError):
        runner({**fifo_payload(), "command": "unsafe"}, tmp_path / "invalid-plan")
    assert not tuple(tmp_path.iterdir())
