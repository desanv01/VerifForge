"""Offline evidence: HTTP MockTransport only; never real model/tool execution."""

import asyncio
import hashlib
import json

import httpx
import pytest
from pydantic import SecretStr

from veriforge.contracts import BehavioralContract, CheckerIdentity, ProjectRevision, Requirement, SourceFile
from veriforge.planner.durable_transport import (
    AdmittedModelRequest, invoke_admitted, reconstruct_admitted_request,
)
from veriforge.planner.models import InvestigationLimits, ModelUsage, PlannerDecision, ProviderCompletion
from veriforge.planner.provider import ENDPOINT, INSTRUCTIONS, MetaConfig, MetaProvider, encode_payload, request_payload
from veriforge.planner.steps import (
    PlannerStepError, build_context, build_experiment, prepare_model_call,
    require_planner_capability, validate_completion,
)
from veriforge.planner.wire_schema import decision_wire_schema, decision_wire_schema_hash

MODEL = "explicit-fake-schema-model"
CONTRACT = BehavioralContract(review_status="reviewed",
    checker=CheckerIdentity(checker_id="fake-checker", version="1", sha256="a" * 64),
    requirements=(Requirement(requirement_id="ORDER", description="Synthetic FIFO order requirement"),))
REVISION = ProjectRevision(project_id="synthetic", revision_id="r1", source_hash="b" * 64,
    fixture_id="fifo_correct", sources=(SourceFile(path="fifo.sv", sha256="b" * 64),))
LIMITS = InvestigationLimits(max_total_cycles=32, max_cycles_per_experiment=32)
STOP = {"proposal": {"kind": "stop", "reason": "finite fake task complete"}}


def experiment(operation=None, **changes):
    proposal = dict(kind="experiment", objective_ids=["ORDER"], seed=1,
                    operations=[{"op": "reset"}, operation or {"op": "read"}])
    proposal.update(changes)
    return {"proposal": proposal}


def completion(value):
    return ProviderCompletion(content=json.dumps(value), model=MODEL, finish_reason="stop",
                              usage=ModelUsage(input_tokens=10, output_tokens=20))


def validate(value):
    return validate_completion(completion(value), requested_model=MODEL, input_allowance=10000, limits=LIMITS)


def build(value):
    decision = validate(value)
    assert decision.outcome == "accepted"
    return build_experiment(decision.decision.proposal, experiment_id="server-owned", revision=REVISION,
        contract=CONTRACT, limits=LIMITS, reserved_cycles=0, remaining_wall_seconds=10)


def fits_schema(value, schema):
    """Independent check of the small JSON Schema vocabulary used on this wire."""
    kind = schema["type"]
    assert {"object": lambda: isinstance(value, dict), "array": lambda: isinstance(value, list),
            "string": lambda: isinstance(value, str), "integer": lambda: type(value) is int,
            "boolean": lambda: type(value) is bool}[kind]()
    if "enum" in schema:
        assert value in schema["enum"]
    if kind == "object":
        assert set(schema["required"]) <= value.keys()
        assert schema["additionalProperties"] is False
        assert value.keys() <= schema["properties"].keys()
        for key, item in value.items():
            fits_schema(item, schema["properties"][key])
    if kind == "array":
        assert schema.get("minItems", 0) <= len(value) <= schema.get("maxItems", float("inf"))
        for item in value:
            fits_schema(item, schema["items"])
    if kind == "string":
        assert schema.get("minLength", 0) <= len(value) <= schema.get("maxLength", float("inf"))
    if kind == "integer":
        assert schema["minimum"] <= value <= schema["maximum"]


@pytest.mark.parametrize("operation", [
    {"op": "reset", "cycles": 2}, {"op": "write", "value": 255}, {"op": "read"},
    {"op": "idle", "cycles": 2}, {"op": "cycle", "reset": False, "write": True, "read": True, "value": 7},
])
def test_every_operation_fits_wire_and_unchanged_local_authority(operation):
    value = experiment(operation)
    fits_schema(value, decision_wire_schema())
    plan = build(value)
    assert plan.operations[1].op == operation["op"]
    assert plan.fixture_id == REVISION.fixture_id and plan.budget.max_cycles == 32
    assert plan.parameters == CONTRACT.parameters


def test_stop_is_still_supported():
    fits_schema(STOP, decision_wire_schema())
    assert validate(STOP).decision.proposal.kind == "stop"


@pytest.mark.parametrize("value", [
    {"proposal": {"kind": "stop"}},
    {"proposal": {"kind": "experiment"}},
    {"proposal": {**STOP["proposal"], "seed": 1}},
    experiment(reason="not an experiment field"),
    experiment({"op": "read", "value": 1}),
    experiment({"op": "write"}),
    experiment({"op": "reset", "write": True}),
    experiment({"op": "cycle", "cycles": 2}),
])
def test_deliberately_broader_wire_never_authorizes_impossible_branches(value):
    fits_schema(value, decision_wire_schema())
    assert validate(value).outcome == "invalid_proposal"


@pytest.mark.parametrize("value", [
    experiment(operations=[{"op": "write", "value": 1}]),
    experiment({"op": "write", "value": 256}),
    experiment({"op": "idle", "cycles": 32}),
    experiment(objective_ids=["UNKNOWN"]),
])
def test_context_cross_fields_still_fail_before_dispatch(value):
    fits_schema(value, decision_wire_schema())
    with pytest.raises(PlannerStepError, match="invalid_proposal"):
        build(value)


@pytest.mark.parametrize("value", [
    {"proposal": {"kind": "stop", "reason": ""}},
    {"proposal": {"kind": "stop", "reason": "x" * 501}},
    experiment(seed=-1), experiment(seed=2**32), experiment(seed=True), experiment(seed="1"),
    experiment(operations=[]), experiment(operations=[{"op": "reset"}] * 1001),
    experiment({"op": "write", "value": -1}), experiment({"op": "write", "value": 2**64}),
    experiment({"op": "idle", "cycles": 0}), experiment({"op": "idle", "cycles": 1_000_001}),
    experiment({"op": "cycle", "reset": 1}), experiment(objective_ids=[]),
    experiment(objective_ids=["ORDER"] * 101), experiment(command="unsafe"),
])
def test_strict_local_types_and_bounds_remain_authority(value):
    assert validate(value).outcome == "invalid_proposal"


def test_schema_uses_only_simple_vocabulary_and_returns_fresh_data():
    allowed = {"type", "properties", "required", "additionalProperties", "enum", "description",
               "items", "minimum", "maximum", "minLength", "maxLength", "minItems", "maxItems"}
    def walk(node):
        assert set(node) <= allowed
        for child in node.get("properties", {}).values():
            walk(child)
        if "items" in node:
            walk(node["items"])
    walk(decision_wire_schema())
    first = decision_wire_schema()
    first["properties"].clear()
    assert decision_wire_schema()["properties"]


def admitted():
    context = build_context(CONTRACT, LIMITS, reserved_cycles=0, feedback=())
    admission = prepare_model_call(context, MODEL, LIMITS, reserved_tokens=0, reserved_cost_usd=None, pricing=None)
    return context, AdmittedModelRequest(call_id="fake-call", generation=1, requested_model=MODEL,
        admission=admission, limits=LIMITS, timeout_seconds=1)


def test_actual_wire_hash_admission_and_durable_reconstruction_agree():
    context, request = admitted()
    payload = json.loads(request.admission.request_body)
    schema = payload["response_format"]["json_schema"]["schema"]
    digest = hashlib.sha256(json.dumps(schema, sort_keys=True, separators=(",", ":"),
        ensure_ascii=False, allow_nan=False).encode()).hexdigest()
    capability = require_planner_capability("rtl.fifo.v1")
    assert request.admission.schema_hash == capability.decision_schema_hash == decision_wire_schema_hash() == digest
    logical = hashlib.sha256(json.dumps(PlannerDecision.model_json_schema(), sort_keys=True).encode()).hexdigest()
    assert capability.logical_decision_schema_hash == logical != digest
    restored = reconstruct_admitted_request(call_id="fake-call", generation=1, requested_model=MODEL,
        admission_metadata=request.admission.model_dump(), context=context, limits=LIMITS, timeout_seconds=1)
    assert restored == request
    metadata = request.admission.model_dump()
    metadata["schema_hash"] = logical
    with pytest.raises(PlannerStepError, match="admission_invalid"):
        reconstruct_admitted_request(call_id="fake-call", generation=1, requested_model=MODEL,
            admission_metadata=metadata, context=context, limits=LIMITS, timeout_seconds=1)


@pytest.mark.parametrize("value", [STOP, experiment({"op": "cycle", "write": True, "value": 9})])
def test_fake_http_sends_exact_admitted_payload_without_changing_messages_model_or_caps(value):
    context, request = admitted()
    seen = []
    def handler(http_request):
        seen.append(http_request.content)
        assert str(http_request.url) == ENDPOINT
        return httpx.Response(200, json={"model": MODEL, "choices": [{"message": {"content": json.dumps(value)},
            "finish_reason": "stop"}], "usage": {"prompt_tokens": 10, "completion_tokens": 20}})
    provider = MetaProvider(MetaConfig(model=MODEL, api_key=SecretStr("fake-key")), transport=httpx.MockTransport(handler))
    result = asyncio.run(invoke_admitted(request, provider=provider, remaining_seconds=2))
    assert result.state == "success" and result.code == "accepted"
    assert seen == [request.admission.request_body]
    payload = json.loads(seen[0])
    assert payload["model"] == MODEL and payload["max_completion_tokens"] == 4096 and payload["stream"] is False
    assert payload["messages"] == [{"role": "developer", "content": INSTRUCTIONS},
                                    {"role": "user", "content": context.model_dump_json()}]
    assert set(payload["response_format"]["json_schema"]) == {"name", "schema"}


def test_old_wire_and_self_consistent_drift_rejected_without_http():
    context, request = admitted()
    seen = []
    provider = MetaProvider(MetaConfig(model=MODEL, api_key=SecretStr("fake-key")),
        transport=httpx.MockTransport(lambda value: seen.append(value)))
    old = request_payload(context, MODEL, 4096)
    old["response_format"]["json_schema"]["schema"] = PlannerDecision.model_json_schema()
    for field, value in [("schema", old), ("model", {**request_payload(context, MODEL, 4096), "model": "drift"}),
                         ("cap", {**request_payload(context, MODEL, 4096), "max_completion_tokens": 8192})]:
        body = encode_payload(value)
        changed = request.admission.model_copy(update={"request_body": body, "request_hash": hashlib.sha256(body).hexdigest(),
            "input_allowance": len(body) + 1024, "reserved_tokens": len(body) + 1024 + LIMITS.max_output_tokens})
        outcome = asyncio.run(invoke_admitted(request.model_copy(update={"admission": changed}), provider=provider,
                                             remaining_seconds=2))
        assert outcome.code == "admission_invalid", field
    assert seen == []


def test_null_length_remains_accounted_but_rejected_with_accepted_pr77_model():
    completion = ProviderCompletion(content=None, model=MODEL, finish_reason="length",
        usage=ModelUsage(input_tokens=10, output_tokens=4096))
    outcome = validate_completion(completion, requested_model=MODEL, input_allowance=10000, limits=LIMITS)
    assert outcome.outcome == "invalid_proposal" and outcome.decision is None
    assert completion.usage.output_tokens == 4096
