"""Typed schema and isolated SQLite API handlers; no jobs or HTTP server."""
import pytest
from pydantic import ValidationError

from veriforge.adapters import get_adapter
from veriforge.api.app import CreateCampaign, create_app
from veriforge.axi_lite import AxiLiteExperiment
from veriforge.axi_lite.runner import directed_experiment
from veriforge.arithmetic import directed_experiment as arithmetic_experiment
from veriforge.contracts import Experiment


def payload():
    plan = directed_experiment().model_dump(mode="json")
    return {"adapter_id": "rtl.axi_lite.v1", "project_id": plan["fixture_id"], "plan": plan}


def handler(app, path, method="GET"):
    return next(route.endpoint for route in app.routes if getattr(route, "path", None) == path and method in route.methods)


def test_axi_schema_and_catalog_create_through_api_handlers(db):
    app = create_app(db[0])
    request = CreateCampaign.model_validate(payload())
    assert isinstance(request.plan, AxiLiteExperiment)
    create = handler(app, "/api/campaigns", "POST")
    campaign = create(request, "axi-static-create")
    assert campaign["execution_status"] == "draft"
    assert campaign["attempts"] == []
    assert campaign["adapter_id"] == "rtl.axi_lite.v1"
    assert campaign["identity"] == get_adapter("rtl.axi_lite.v1").expected_identity(request.plan)
    assert campaign["contract_snapshot"]["schema_version"] == "axi_lite.contract.v1"
    assert campaign["trace_artifact"] == "observed.json"
    assert campaign["trace_schema"] == "axi_lite.trace.v1"
    assert create(request, "axi-static-create") == campaign
    projects = handler(app, "/api/projects")()["items"]
    assert len([p for p in projects if p["adapter_id"] == "rtl.axi_lite.v1"]) == 4
    assert len([p for p in projects if p["adapter_id"] == "rtl.fifo.v1"]) == 2
    assert len([p for p in projects if p["adapter_id"] == "rtl.arithmetic.v1"]) == 3
    assert handler(app, "/api/contracts/{contract_id}")("axil-registers-v1")["schema_version"] == "axi_lite.contract.v1"
    assert "rtl.axi_lite.v1" in handler(app, "/api/health")()["evidence_adapters"]


@pytest.mark.parametrize("mutation", ["missing_adapter", "fifo_adapter", "arithmetic_adapter", "missing_schema", "wrong_schema", "unknown_fixture", "dynamic"])
def test_axi_typed_admission_fails_closed(mutation):
    data = payload()
    if mutation == "missing_adapter": data.pop("adapter_id")
    elif mutation == "fifo_adapter": data["adapter_id"] = "rtl.fifo.v1"
    elif mutation == "arithmetic_adapter": data["adapter_id"] = "rtl.arithmetic.v1"
    elif mutation == "missing_schema": data["plan"].pop("schema_version")
    elif mutation == "wrong_schema": data["plan"]["schema_version"] = "1"
    elif mutation == "unknown_fixture": data["plan"]["fixture_id"] = "untrusted"
    else: data["plan"]["source"] = "untrusted.sv"
    with pytest.raises(ValidationError):
        CreateCampaign.model_validate(data)


def test_api_discriminator_includes_all_three_families():
    mapping = CreateCampaign.model_json_schema()["properties"]["plan"]["discriminator"]["mapping"]
    assert set(mapping) == {"1", "arithmetic.v1", "axi_lite.v1"}


def test_legacy_fifo_and_arithmetic_admission_preserved():
    fifo = {"experiment_id": "legacy", "fixture_id": "fifo_correct", "objective_ids": ["FIFO_BEHAVIOR"],
            "operations": [{"op": "reset"}]}
    legacy = CreateCampaign.model_validate({"project_id": "fifo_correct", "plan": fifo})
    assert legacy.adapter_id == "rtl.fifo.v1" and isinstance(legacy.plan, Experiment)
    arithmetic = arithmetic_experiment()
    admitted = CreateCampaign.model_validate({"adapter_id": "rtl.arithmetic.v1",
        "project_id": arithmetic.fixture_id, "plan": arithmetic.model_dump(mode="json")})
    assert admitted.plan == arithmetic
