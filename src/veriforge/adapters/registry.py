"""Explicit registry of trusted target packages. Input never supplies imports."""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from types import MappingProxyType
from typing import Any, Callable, Literal, Mapping, Protocol

from pydantic import BaseModel

from veriforge.contracts import AttemptResult, BehavioralContract, Experiment, FifoParameters
from veriforge.rtl import reviewed_contract as fifo_contract
from veriforge.rtl import run_container_experiment as fifo_container
from veriforge.rtl import run_experiment as fifo_native
from veriforge.arithmetic import ArithmeticContract, ArithmeticExperiment, Parameters
from veriforge.arithmetic import reviewed_contract as arithmetic_contract
from veriforge.arithmetic import run_experiment as arithmetic_native
from veriforge.arithmetic.container import run_container_experiment as arithmetic_container

ExecutionMode = Literal["native", "container"]
ValidatedExperiment = Experiment | ArithmeticExperiment
ReviewedContract = BehavioralContract | ArithmeticContract


class CancellationToken(Protocol):
    """Cooperative token shared by the existing bounded runners."""

    def is_set(self) -> bool: ...


class AdapterRunner(Protocol):
    def __call__(self, experiment: ValidatedExperiment | Mapping[str, Any], output_dir: Path,
                 *, cancel_event: CancellationToken | None = None) -> AttemptResult: ...


class UnknownAdapterError(ValueError):
    """Identifier is not in the curated registry."""


class UnsupportedFixtureError(ValueError):
    """A schema-known fixture has no installed implementation in this adapter."""


@dataclass(frozen=True)
class AdapterDescriptor:
    adapter_id: str
    target: str
    experiment_model: type[Experiment] | type[ArithmeticExperiment]
    parameter_model: type[FifoParameters] | type[Parameters]
    contract_model: type[BehavioralContract] | type[ArithmeticContract]
    fixture_ids: tuple[str, ...]
    trace_schema: str | None
    trace_artifact: str | None
    _contract_factory: Callable[..., ReviewedContract]
    _native_runner: Callable[..., AttemptResult]
    _container_runner: Callable[..., AttemptResult]

    def validate_experiment(self, payload: ValidatedExperiment | Mapping[str, Any]) -> ValidatedExperiment:
        # Existing family validators enforce cycle limits, reset and width semantics.
        if isinstance(payload, self.experiment_model):
            # Recheck model_copy/model_construct values at this execution boundary.
            payload = payload.model_dump(mode="python")
        experiment = self.experiment_model.model_validate(payload)
        if experiment.fixture_id not in self.fixture_ids:
            raise UnsupportedFixtureError("Fixture is not installed for this adapter")
        return experiment

    def experiment_schema(self) -> dict[str, Any]:
        """Fresh schema for registry-accepted plans; shared models stay unchanged."""
        schema = self.experiment_model.model_json_schema()
        schema["properties"]["fixture_id"]["enum"] = list(self.fixture_ids)
        return schema

    def contract_schema(self) -> dict[str, Any]:
        return self.contract_model.model_json_schema()

    def reviewed_contract(self, parameters: BaseModel | Mapping[str, Any] | None = None) -> ReviewedContract:
        if isinstance(parameters, self.parameter_model):
            parameters = parameters.model_dump(mode="python")
        validated = self.parameter_model.model_validate({} if parameters is None else parameters)
        return self._contract_factory(validated)

    def contract_for(self, payload: ValidatedExperiment | Mapping[str, Any]) -> ReviewedContract:
        experiment = self.validate_experiment(payload)
        return self.reviewed_contract(experiment.parameters)

    def resolve_runner(self, mode: ExecutionMode = "native") -> AdapterRunner:
        if mode not in ("native", "container"):
            raise ValueError("Execution mode must be native or container")
        return _BoundRunner(self, mode)


@dataclass(frozen=True)
class _BoundRunner:
    descriptor: AdapterDescriptor
    mode: ExecutionMode

    def __call__(self, experiment: ValidatedExperiment | Mapping[str, Any], output_dir: Path,
                 *, cancel_event: CancellationToken | None = None) -> AttemptResult:
        validated = self.descriptor.validate_experiment(experiment)
        if cancel_event is not None and not callable(getattr(cancel_event, "is_set", None)):
            raise TypeError("Cancellation token must provide is_set()")
        runner = self.descriptor._native_runner if self.mode == "native" else self.descriptor._container_runner
        return runner(validated, output_dir, cancel_event=cancel_event)


_DESCRIPTORS = (
    AdapterDescriptor(adapter_id="rtl.fifo.v1", target="fifo", experiment_model=Experiment,
        parameter_model=FifoParameters, contract_model=BehavioralContract,
        fixture_ids=("fifo_correct", "fifo_reset_bug"), trace_schema=None, trace_artifact=None,
        _contract_factory=fifo_contract, _native_runner=fifo_native, _container_runner=fifo_container),
    AdapterDescriptor(adapter_id="rtl.arithmetic.v1", target="arithmetic", experiment_model=ArithmeticExperiment,
        parameter_model=Parameters, contract_model=ArithmeticContract,
        fixture_ids=("multiplier_correct", "multiplier_latency_bug", "multiplier_data_bug"),
        trace_schema="arithmetic.trace.v1", trace_artifact="arithmetic-trace.json", _contract_factory=arithmetic_contract,
        _native_runner=arithmetic_native, _container_runner=arithmetic_container),
)
_REGISTRY = MappingProxyType({descriptor.adapter_id: descriptor for descriptor in _DESCRIPTORS})


def registered_adapters() -> tuple[AdapterDescriptor, ...]:
    return _DESCRIPTORS


def get_adapter(adapter_id: str) -> AdapterDescriptor:
    if not isinstance(adapter_id, str) or adapter_id not in _REGISTRY:
        raise UnknownAdapterError("Adapter is not installed in the curated registry")
    return _REGISTRY[adapter_id]
