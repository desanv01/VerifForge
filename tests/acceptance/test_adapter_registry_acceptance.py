"""Independent nested-model boundary checks; no execution backend is started."""
from dataclasses import replace

import pytest
from pydantic import ValidationError

from veriforge.adapters import get_adapter
from veriforge.arithmetic import directed_experiment


def family_plan(adapter_id):
    adapter = get_adapter(adapter_id)
    if adapter.target == "arithmetic":
        return directed_experiment()
    return adapter.validate_experiment({"experiment_id": "registry-independent",
        "fixture_id": "fifo_correct", "objective_ids": ["FIFO_BEHAVIOR"],
        "operations": [{"op": "reset"}, {"op": "write", "value": 13}]})


@pytest.mark.parametrize("adapter_id", ["rtl.fifo.v1", "rtl.arithmetic.v1"])
@pytest.mark.parametrize("nested", ["parameters", "budget"])
def test_nested_copied_models_cannot_bypass_registry_before_backend(tmp_path, adapter_id, nested):
    calls = []
    def forbidden(*args, **kwargs):
        calls.append(True)
        pytest.fail("invalid nested model reached a backend")
    descriptor = replace(get_adapter(adapter_id), _native_runner=forbidden, _container_runner=forbidden)
    original = family_plan(adapter_id)
    value = getattr(original, nested)
    updates = {"width": True} if nested == "parameters" else {"max_cycles": True}
    invalid = original.model_copy(update={nested: value.model_copy(update=updates)})
    with pytest.raises(ValidationError):
        descriptor.resolve_runner("native")(invalid, tmp_path / "must-not-exist")
    assert not calls
    assert not list(tmp_path.iterdir())


@pytest.mark.parametrize("adapter_id,other_id", [
    ("rtl.fifo.v1", "rtl.arithmetic.v1"), ("rtl.arithmetic.v1", "rtl.fifo.v1"),
])
def test_family_models_are_rejected_even_with_overlapping_reset_semantics(tmp_path, adapter_id, other_id):
    def forbidden(*args, **kwargs):
        pytest.fail("cross-family model reached a backend")
    descriptor = replace(get_adapter(adapter_id), _native_runner=forbidden)
    with pytest.raises(ValidationError):
        descriptor.resolve_runner()(family_plan(other_id), tmp_path / "cross-family")
    assert not list(tmp_path.iterdir())
