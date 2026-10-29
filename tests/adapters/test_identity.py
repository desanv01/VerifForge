"""Pure metadata/hash parity checks. No tool, runner, Docker or simulator starts."""
from dataclasses import FrozenInstanceError, replace
import hashlib
import json
from pathlib import Path, PurePosixPath

import pytest
from pydantic import ValidationError

from veriforge.adapters import get_adapter, UnsupportedFixtureError
from veriforge.arithmetic import directed_experiment
from veriforge.arithmetic import oracle as arithmetic_oracle
from veriforge.arithmetic.runner import FIXTURES as ARITHMETIC_FIXTURES
from veriforge.contracts import Experiment, canonical_hash
from veriforge.rtl import oracle as fifo_oracle
from veriforge.rtl.runner import FIXTURES as FIFO_FIXTURES

ROOT = Path(__file__).resolve().parents[2]


def fifo():
    return Experiment(experiment_id="identity-unit-fifo", fixture_id="fifo_correct",
        objective_ids=("FIFO_BEHAVIOR",), operations=({"op": "reset"}, {"op": "write", "value": 17}, {"op": "read"}))


@pytest.mark.parametrize("adapter_id,plan,source_path,variants", [
    ("rtl.fifo.v1", fifo, "targets/fifo/fifo.sv", FIFO_FIXTURES),
    ("rtl.arithmetic.v1", directed_experiment, "targets/arithmetic/multiplier.sv", ARITHMETIC_FIXTURES),
])
def test_fixture_snapshot_and_exact_runner_revision_formula(adapter_id, plan, source_path, variants):
    descriptor = get_adapter(adapter_id)
    file_hash = hashlib.sha256((ROOT / source_path).read_bytes()).hexdigest()
    for fixture in descriptor.fixture_ids:
        payload = plan().model_dump(mode="json")
        payload["fixture_id"] = fixture
        experiment = descriptor.validate_experiment(payload)
        snapshot = descriptor.fixture_source(fixture)
        assert snapshot == {"source_file_hash": file_hash, "variant": variants[fixture]}
        assert set(snapshot) == {"source_file_hash", "variant"}  # No path/command output.
        serialized_revision = json.dumps({"source": file_hash, "variant": variants[fixture],
            "parameters": experiment.parameters.model_dump()}, sort_keys=True, separators=(",", ":"))
        contract = descriptor.contract_for(experiment)
        expected = {"source_hash": hashlib.sha256(serialized_revision.encode()).hexdigest(),
                    "checker_hash": contract.checker.sha256, "contract_hash": canonical_hash(contract),
                    "experiment_hash": canonical_hash(experiment)}
        assert descriptor.expected_identity(experiment) == expected
        assert "environment_hash" not in expected
        snapshot["variant"] = 999
        assert descriptor.fixture_source(fixture)["variant"] == variants[fixture]


@pytest.mark.parametrize("adapter_id,plan", [("rtl.fifo.v1", fifo), ("rtl.arithmetic.v1", directed_experiment)])
def test_identity_is_fresh_and_binds_parameters_and_experiment(adapter_id, plan):
    descriptor = get_adapter(adapter_id)
    payload = plan().model_dump(mode="json")
    original = descriptor.expected_identity(payload)
    payload["seed"] += 1
    changed = descriptor.expected_identity(payload)
    assert changed["experiment_hash"] != original["experiment_hash"]
    assert {key: value for key, value in changed.items() if key != "experiment_hash"} == {
        key: value for key, value in original.items() if key != "experiment_hash"}
    payload["parameters"]["width"] = 16
    changed = descriptor.expected_identity(payload)
    assert changed["source_hash"] != original["source_hash"]
    assert changed["contract_hash"] != original["contract_hash"]
    assert changed["checker_hash"] == original["checker_hash"]
    original["checker_hash"] = "client mutation"
    assert descriptor.expected_identity(plan())["checker_hash"] != "client mutation"


def test_live_installed_source_is_rechecked_not_cached(tmp_path):
    descriptor = get_adapter("rtl.fifo.v1")
    source = tmp_path / "unit-only-source.sv"
    source.write_text("unit snapshot A")
    descriptor = replace(descriptor, _source=replace(descriptor._source, path=source))
    before = descriptor.expected_identity(fifo())
    source.write_text("unit snapshot B")
    after = descriptor.expected_identity(fifo())
    assert before["source_hash"] != after["source_hash"]
    assert before["contract_hash"] == after["contract_hash"]
    with pytest.raises(FrozenInstanceError):
        descriptor._source.path = source


def test_historical_parsing_metadata_does_not_require_active_source(tmp_path):
    descriptor = get_adapter("rtl.fifo.v1")
    descriptor = replace(descriptor, _source=replace(descriptor._source, path=tmp_path / "not-installed.sv"))
    assert descriptor.validate_experiment(fifo()) == fifo()
    assert "fifo.sv" in descriptor.portable_artifacts
    # Only an explicit active-run preflight requires the installed source bytes.
    with pytest.raises(FileNotFoundError):
        descriptor.expected_identity(fifo())


@pytest.mark.parametrize("fixture", ["fifo_full_bug", "fifo_order_bug", "../../other.sv", "module:function"])
def test_unknown_or_uninstalled_fixture_cannot_select_source(fixture):
    with pytest.raises(UnsupportedFixtureError):
        get_adapter("rtl.fifo.v1").fixture_source(fixture)


@pytest.mark.parametrize("adapter_id,plan,oracle", [
    ("rtl.fifo.v1", fifo, fifo_oracle),
    ("rtl.arithmetic.v1", directed_experiment, arithmetic_oracle),
])
def test_expansion_is_exact_fresh_and_type_checked(adapter_id, plan, oracle):
    descriptor = get_adapter(adapter_id)
    experiment = plan()
    assert descriptor.expand_stimulus(experiment) == oracle.expand(experiment)
    expanded = descriptor.expand_stimulus(experiment)
    assert len(expanded) == experiment.cycle_count
    expanded[0]["operation_index"] = 999
    assert descriptor.expand_stimulus(experiment)[0]["operation_index"] == 0
    invalid = experiment.model_copy(update={"seed": True})
    for helper in (descriptor.expected_identity, descriptor.expand_stimulus):
        with pytest.raises(ValidationError):
            helper(invalid)


def test_portable_allowlists_are_fixed_family_specific_names():
    common = {"stimulus.json", "contract.json", "checker.py", "observed.json"}
    fifo_descriptor = get_adapter("rtl.fifo.v1")
    arithmetic = get_adapter("rtl.arithmetic.v1")
    assert fifo_descriptor.source_artifact == "fifo.sv"
    assert arithmetic.source_artifact == "multiplier.sv"
    assert set(fifo_descriptor.portable_artifacts) == common | {"fifo.sv"}
    assert set(arithmetic.portable_artifacts) == common | {"multiplier.sv", "arithmetic-trace.json"}
    for descriptor in (fifo_descriptor, arithmetic):
        assert len(descriptor.portable_artifacts) == len(set(descriptor.portable_artifacts))
        for name in descriptor.portable_artifacts:
            assert PurePosixPath(name).name == name
        assert not set(descriptor.portable_artifacts) & {"job.json", "environment.json", "execution.log"}
        with pytest.raises(FrozenInstanceError):
            descriptor.portable_artifacts += ("execution.log",)
