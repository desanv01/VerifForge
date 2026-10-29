"""Explicit registry of trusted target packages. Input never supplies imports."""
from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
from pathlib import Path
from types import MappingProxyType
from typing import Any, Callable, Literal, Mapping, Protocol

from pydantic import BaseModel

from veriforge.contracts import AttemptResult, BehavioralContract, Experiment, FifoParameters, canonical_hash
from veriforge.rtl import oracle as fifo_oracle
from veriforge.rtl import reviewed_contract as fifo_contract
from veriforge.rtl import run_container_experiment as fifo_container
from veriforge.rtl import run_experiment as fifo_native
from veriforge.arithmetic import ArithmeticContract, ArithmeticExperiment, Parameters
from veriforge.arithmetic import reviewed_contract as arithmetic_contract
from veriforge.arithmetic import run_experiment as arithmetic_native
from veriforge.arithmetic.container import run_container_experiment as arithmetic_container
from veriforge.arithmetic import oracle as arithmetic_oracle

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
class _CuratedSource:
    # Internal metadata: clients never supply an import, source path or variant.
    path: Path
    variants: tuple[tuple[str, int], ...]


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
    source_artifact: str | None = None
    portable_artifacts: tuple[str, ...] = ()
    _source: _CuratedSource | None = None
    _expand: Callable[[ValidatedExperiment], list[dict[str, Any]]] | None = None

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

    def fixture_source(self, fixture_id: str) -> dict[str, str | int]:
        """Fresh trusted catalog snapshot; no paths or executable fields escape."""
        if fixture_id not in self.fixture_ids:
            raise UnsupportedFixtureError("Fixture is not installed for this adapter")
        if self._source is None:
            raise NotImplementedError("Adapter has no curated source metadata")
        variants = dict(self._source.variants)
        if fixture_id not in variants:
            raise UnsupportedFixtureError("Fixture has no installed source variant")
        return {"source_file_hash": hashlib.sha256(self._source.path.read_bytes()).hexdigest(),
                "variant": variants[fixture_id]}

    def expected_identity(self, payload: ValidatedExperiment | Mapping[str, Any]) -> dict[str, str]:
        """Active installed identity for preflight; never fabricate tool/environment."""
        experiment = self.validate_experiment(payload)
        source = self.fixture_source(experiment.fixture_id)
        contract = self.reviewed_contract(experiment.parameters)
        revision = {"source": source["source_file_hash"], "variant": source["variant"],
                    "parameters": experiment.parameters.model_dump()}
        return {"source_hash": hashlib.sha256(json.dumps(revision, sort_keys=True, separators=(",", ":")).encode()).hexdigest(),
                "checker_hash": contract.checker.sha256,
                "contract_hash": canonical_hash(contract), "experiment_hash": canonical_hash(experiment)}

    def expand_stimulus(self, payload: ValidatedExperiment | Mapping[str, Any]) -> list[dict[str, Any]]:
        experiment = self.validate_experiment(payload)
        if self._expand is None:
            raise NotImplementedError("Adapter has no trusted stimulus expander")
        return self._expand(experiment)

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


_ROOT = Path(__file__).resolve().parents[3]
_COMMON_PORTABLE = ("stimulus.json", "contract.json", "checker.py", "observed.json")
_DESCRIPTORS = (
    AdapterDescriptor(adapter_id="rtl.fifo.v1", target="fifo", experiment_model=Experiment,
        parameter_model=FifoParameters, contract_model=BehavioralContract,
        fixture_ids=("fifo_correct", "fifo_reset_bug"), trace_schema=None, trace_artifact=None,
        _contract_factory=fifo_contract, _native_runner=fifo_native, _container_runner=fifo_container,
        source_artifact="fifo.sv", portable_artifacts=(*_COMMON_PORTABLE, "fifo.sv"),
        _source=_CuratedSource(_ROOT / "targets/fifo/fifo.sv", (("fifo_correct", 0), ("fifo_reset_bug", 1))),
        _expand=fifo_oracle.expand),
    AdapterDescriptor(adapter_id="rtl.arithmetic.v1", target="arithmetic", experiment_model=ArithmeticExperiment,
        parameter_model=Parameters, contract_model=ArithmeticContract,
        fixture_ids=("multiplier_correct", "multiplier_latency_bug", "multiplier_data_bug"),
        trace_schema="arithmetic.trace.v1", trace_artifact="arithmetic-trace.json", _contract_factory=arithmetic_contract,
        _native_runner=arithmetic_native, _container_runner=arithmetic_container,
        source_artifact="multiplier.sv", portable_artifacts=(*_COMMON_PORTABLE, "multiplier.sv", "arithmetic-trace.json"),
        _source=_CuratedSource(_ROOT / "targets/arithmetic/multiplier.sv", (
            ("multiplier_correct", 0), ("multiplier_latency_bug", 1), ("multiplier_data_bug", 2))),
        _expand=arithmetic_oracle.expand),
)
_REGISTRY = MappingProxyType({descriptor.adapter_id: descriptor for descriptor in _DESCRIPTORS})


def registered_adapters() -> tuple[AdapterDescriptor, ...]:
    return _DESCRIPTORS


def get_adapter(adapter_id: str) -> AdapterDescriptor:
    if not isinstance(adapter_id, str) or adapter_id not in _REGISTRY:
        raise UnknownAdapterError("Adapter is not installed in the curated registry")
    return _REGISTRY[adapter_id]
