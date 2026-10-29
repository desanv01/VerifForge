"""Static AXI admission and dispatch only; no simulator evidence is produced."""
from dataclasses import replace
import hashlib
import json
from pathlib import Path
from threading import Event

import pytest
from pydantic import ValidationError

from veriforge.adapters import get_adapter, registered_adapters
from veriforge.axi_lite import AxiLiteExperiment, reviewed_contract
from veriforge.axi_lite.runner import FIXTURES, directed_experiment
from veriforge.contracts import canonical_hash


def test_axi_static_metadata_matches_installed_runner():
    descriptor = get_adapter("rtl.axi_lite.v1")
    assert tuple(registered_adapters())[-1] is descriptor
    assert descriptor.fixture_ids == tuple(FIXTURES)
    assert descriptor.trace_schema == "axi_lite.trace.v1"
    assert descriptor.trace_artifact == "observed.json"
    assert descriptor.source_artifact == "registers.sv"
    assert descriptor.portable_artifacts == (
        "stimulus.json", "contract.json", "checker.py", "observed.json", "registers.sv", "checker-contracts.py", "check.json")


@pytest.mark.parametrize("fixture", tuple(FIXTURES))
def test_axi_identity_and_exact_stimulus(fixture):
    descriptor = get_adapter("rtl.axi_lite.v1")
    plan = directed_experiment(fixture)
    source = Path(__file__).resolve().parents[2] / "targets/axi_lite/registers.sv"
    file_hash = hashlib.sha256(source.read_bytes()).hexdigest()
    assert descriptor.fixture_source(fixture) == {"source_file_hash": file_hash, "variant": FIXTURES[fixture]}
    revision = {"source": file_hash, "variant": FIXTURES[fixture], "parameters": plan.parameters.model_dump()}
    contract = reviewed_contract(plan.parameters)
    assert descriptor.expected_identity(plan) == {
        "source_hash": hashlib.sha256(json.dumps(revision, sort_keys=True, separators=(",", ":")).encode()).hexdigest(),
        "checker_hash": contract.checker.sha256, "contract_hash": canonical_hash(contract),
        "experiment_hash": canonical_hash(plan)}
    stimulus = descriptor.expand_stimulus(plan)
    assert stimulus == [row.model_dump() for row in plan.cycles]
    stimulus[0]["reset_n"] = 1
    assert descriptor.expand_stimulus(plan)[0]["reset_n"] == 0
    assert descriptor.contract_for(plan) == contract


@pytest.mark.parametrize("mode", ["native", "container"])
def test_axi_dispatch_is_fixed_and_forwards_token(mode, tmp_path):
    descriptor = get_adapter("rtl.axi_lite.v1")
    plan, token, calls = directed_experiment(), Event(), []
    sentinel = object()
    def backend(experiment, output, *, cancel_event):
        calls.append((experiment, output, cancel_event))
        return sentinel
    def forbidden(*args, **kwargs):
        pytest.fail("wrong backend invoked")
    bound = replace(descriptor, _native_runner=backend if mode == "native" else forbidden,
                    _container_runner=backend if mode == "container" else forbidden)
    assert bound.resolve_runner(mode)(plan.model_dump(), tmp_path, cancel_event=token) is sentinel
    assert calls == [(plan, tmp_path, token)]
    assert isinstance(calls[0][0], AxiLiteExperiment)


@pytest.mark.parametrize("mutation", ["fixture", "command", "cycle", "constructed"])
def test_axi_validation_cannot_bypass_installed_schema(mutation):
    descriptor, plan = get_adapter("rtl.axi_lite.v1"), directed_experiment()
    payload = plan.model_dump(mode="json")
    if mutation == "fixture": payload["fixture_id"] = "../../custom.sv"
    elif mutation == "command": payload["command"] = "echo arbitrary"
    elif mutation == "cycle": payload["cycles"][0]["aw_valid"] = True
    else: payload = plan.model_copy(update={"fixture_id": "uninstalled"})
    with pytest.raises(ValidationError):
        descriptor.validate_experiment(payload)


def test_axi_schema_is_fresh_and_rejects_other_families():
    descriptor = get_adapter("rtl.axi_lite.v1")
    schema = descriptor.experiment_schema()
    assert schema["properties"]["schema_version"]["const"] == "axi_lite.v1"
    schema["properties"]["fixture_id"]["enum"].clear()
    assert descriptor.experiment_schema()["properties"]["fixture_id"]["enum"] == list(FIXTURES)
    with pytest.raises(ValidationError):
        get_adapter("rtl.fifo.v1").validate_experiment(directed_experiment())
