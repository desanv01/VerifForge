"""Required transport -> strict logical authority; pure offline evidence only."""
import copy
import hashlib
import json

import pytest
from pydantic import ValidationError

from veriforge.contracts import ProjectRevision, SourceFile
from veriforge.planner.models import InvestigationLimits, PlannerDecision, ProviderCompletion, ModelUsage
from veriforge.planner.provider import encode_payload, request_payload
from veriforge.planner.steps import (PlannerStepError, build_context, build_experiment,
    prepare_model_call, require_planner_capability, validate_completion)
from veriforge.planner.durable_transport import reconstruct_admitted_request
from veriforge.planner.wire_schema import (WIRE_VERSION, decision_wire_schema,
    decision_wire_schema_hash, decode_decision_json, decode_wire_decision)
from veriforge.rtl import reviewed_contract

MODEL = "explicit-fake-wire-v2"
LIMITS = InvestigationLimits(max_total_cycles=32, max_cycles_per_experiment=32)
CONTRACT = reviewed_contract()
REVISION = ProjectRevision(project_id="synthetic", revision_id="r1", source_hash="a" * 64,
    fixture_id="fifo_correct", sources=(SourceFile(path="fifo.sv", sha256="a" * 64),))


def cycle(**changes):
    return {"op": "cycle", "reset": False, "write": False, "read": False, "value": 0, **changes}


def experiment():
    return {"wire_version": WIRE_VERSION, "proposal": {"kind": "experiment", "reason": "",
        "objective_ids": ["FIFO_BEHAVIOR"], "seed": 1,
        "operations": [cycle(reset=True), cycle(write=True, value=37), cycle(read=True), cycle()]}}


def stop():
    return {"wire_version": WIRE_VERSION, "proposal": {"kind": "stop", "reason": "done",
        "objective_ids": [], "seed": 0, "operations": []}}


def validate(value, **kwargs):
    completion = ProviderCompletion(content=json.dumps(value), model=MODEL,
        finish_reason=kwargs.get("finish", "stop"), usage=ModelUsage(input_tokens=10, output_tokens=20))
    return validate_completion(completion, requested_model=MODEL, input_allowance=10000, limits=LIMITS)


def build(value):
    decision = decode_wire_decision(value)
    return build_experiment(decision.proposal, experiment_id="trusted-id", revision=REVISION,
        contract=CONTRACT, limits=LIMITS, reserved_cycles=0, remaining_wall_seconds=10)


def test_explicit_cycle_operations_are_preserved_and_reset_first_is_legal():
    value = experiment()
    decision = decode_wire_decision(value)
    assert decision.proposal.model_dump(mode="json")["operations"] == value["proposal"]["operations"]
    plan = build(value)
    assert plan.cycle_count == 4 and plan.operations[0].reset is True
    assert plan.operations[1].value == 37 and plan.parameters == CONTRACT.parameters
    assert validate(value).outcome == "accepted"


@pytest.mark.parametrize("operation", [cycle(reset=True), cycle(write=True, value=255),
    cycle(read=True), cycle(), cycle(write=True, read=True, value=7), cycle(reset=True, value=23)])
def test_every_explicit_cycle_kind_stays_an_exact_existing_cycle(operation):
    value = experiment(); value["proposal"]["operations"] = [cycle(reset=True), operation]
    plan = build(value)
    assert plan.operations[1].model_dump(mode="json") == operation


def test_stop_sentinels_are_validated_before_projection():
    logical = decode_wire_decision(stop())
    assert logical.model_dump(mode="json") == {"proposal": {"kind": "stop", "reason": "done"}}
    assert validate(stop()).outcome == "accepted"


@pytest.mark.parametrize("field", ["kind", "reason", "objective_ids", "seed", "operations"])
def test_every_proposal_field_is_required_even_when_neutral(field):
    for value in (experiment(), stop()):
        del value["proposal"][field]
        assert validate(value).outcome == "invalid_proposal"


@pytest.mark.parametrize("field", ["op", "reset", "write", "read", "value"])
def test_every_cycle_field_is_required_without_default_injection(field):
    value = experiment(); del value["proposal"]["operations"][0][field]
    assert validate(value).outcome == "invalid_proposal"


@pytest.mark.parametrize("changes", [{"seed": 1}, {"objective_ids": ["FIFO_BEHAVIOR"]},
    {"operations": [cycle()]}, {"reason": ""}, {"reason": "x" * 501}])
def test_stop_non_neutral_or_invalid_fields_are_not_silently_discarded(changes):
    value = stop(); value["proposal"].update(changes)
    assert validate(value).outcome == "invalid_proposal"


@pytest.mark.parametrize("changes", [{"reason": "contradictory"}, {"objective_ids": []},
    {"operations": []}, {"seed": True}, {"seed": -1}, {"seed": 2**32}])
def test_experiment_branch_remains_strict(changes):
    value = experiment(); value["proposal"].update(changes)
    assert validate(value).outcome == "invalid_proposal"


@pytest.mark.parametrize("changes", [{"op": "reset"}, {"reset": 1}, {"write": "false"},
    {"read": None}, {"value": True}, {"value": -1}, {"value": 2**64}, {"cycles": 2}])
def test_cycles_reject_other_ops_types_extra_fields_and_bounds(changes):
    value = experiment(); value["proposal"]["operations"][0].update(changes)
    assert validate(value).outcome == "invalid_proposal"


@pytest.mark.parametrize("location", ["root", "proposal", "operation"])
def test_unknown_fields_are_refused(location):
    value = experiment()
    target = value if location == "root" else value["proposal"] if location == "proposal" else value["proposal"]["operations"][0]
    target["unknown"] = "never drop this"
    assert validate(value).outcome == "invalid_proposal"


def test_wire_version_is_required_and_unknown_versions_do_not_fall_back():
    value = experiment(); del value["wire_version"]
    assert validate(value).outcome == "invalid_proposal"
    value["wire_version"] = "unreviewed"
    assert validate(value).outcome == "invalid_proposal"


@pytest.mark.parametrize("change", ["reset", "width", "cycles", "objective"])
def test_unchanged_semantic_builder_still_checks_context(change):
    value = experiment()
    if change == "reset": value["proposal"]["operations"] = [cycle(write=True, value=1)]
    if change == "width": value["proposal"]["operations"][1]["value"] = 256
    if change == "cycles": value["proposal"]["operations"] = [cycle(reset=True)] + [cycle()] * 32
    if change == "objective": value["proposal"]["objective_ids"] = ["UNREVIEWED"]
    assert validate(value).outcome == "accepted"  # Decoding is not dispatch authority.
    with pytest.raises(PlannerStepError, match="invalid_proposal"):
        build(value)


def test_retained_legacy_decisions_remain_logically_strict_and_unchanged():
    values = [{"proposal": {"kind": "stop", "reason": "legacy"}},
        {"proposal": {"kind": "experiment", "objective_ids": ["FIFO_BEHAVIOR"], "seed": 1,
                      "operations": [{"op": "reset"}, {"op": "write", "value": 2}, {"op": "read"}]}}]
    for value in values:
        assert decode_decision_json(json.dumps(value)) == PlannerDecision.model_validate(value)
        assert validate(value).outcome == "accepted"
    assert validate({"proposal": {"kind": "experiment", "objective_ids": ["FIFO_BEHAVIOR"], "seed": 1}}).outcome == "invalid_proposal"
    assert validate({"proposal": {"kind": "stop", "reason": "legacy", "operations": []}}).outcome == "invalid_proposal"


def test_generation_schema_requires_all_object_fields_without_references_or_unions():
    def walk(schema):
        assert not set(schema) & {"$ref", "$defs", "oneOf", "anyOf", "discriminator", "default"}
        if schema["type"] == "object":
            assert set(schema["required"]) == set(schema["properties"])
            assert schema["additionalProperties"] is False
            for value in schema["properties"].values(): walk(value)
        if schema["type"] == "array": walk(schema["items"])
    walk(decision_wire_schema())
    value = decision_wire_schema(); value["properties"].clear()
    assert decision_wire_schema()["properties"]


def test_wire_admission_durable_identity_and_old_schema_refusal():
    context = build_context(CONTRACT, LIMITS, reserved_cycles=0, feedback=())
    admission = prepare_model_call(context, MODEL, LIMITS, reserved_tokens=0, reserved_cost_usd=None, pricing=None)
    body = json.loads(admission.request_body)
    digest = hashlib.sha256(json.dumps(body["response_format"]["json_schema"]["schema"],
        sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()).hexdigest()
    capability = require_planner_capability("rtl.fifo.v1")
    assert digest == admission.schema_hash == capability.decision_schema_hash == decision_wire_schema_hash()
    assert capability.logical_decision_schema_hash == "e99e4334c40f10c11a88b477bbf42bace7666d245d2f8db87d93f22ac0abaef0"
    restored = reconstruct_admitted_request(call_id="fake-call", generation=1, requested_model=MODEL,
        admission_metadata=admission.model_dump(), context=context, limits=LIMITS, timeout_seconds=1)
    assert restored.admission.request_body == admission.request_body == encode_payload(request_payload(context, MODEL, 4096))
    metadata = admission.model_dump(); metadata["schema_hash"] = "77125ac32564ab26cb6056d40503116fd888126573e7bff2f938926e26c3577d"
    with pytest.raises(PlannerStepError, match="admission_invalid"):
        reconstruct_admitted_request(call_id="fake-call", generation=1, requested_model=MODEL,
            admission_metadata=metadata, context=context, limits=LIMITS, timeout_seconds=1)


def test_duplicate_version_and_nonfinite_json_are_refused():
    with pytest.raises(ValueError):
        decode_decision_json('{"wire_version":"veriforge.planner-wire.v2","wire_version":"old","proposal":{}}')
    with pytest.raises(ValueError): decode_decision_json('{"proposal":NaN}')


def test_length_and_usage_model_rules_still_precede_decoding():
    assert validate(experiment(), finish="length").outcome == "invalid_proposal"
    value = ProviderCompletion(content=None, model=MODEL, finish_reason="length", usage=ModelUsage(input_tokens=10, output_tokens=4096))
    result = validate_completion(value, requested_model=MODEL, input_allowance=10000, limits=LIMITS)
    assert result.outcome == "invalid_proposal" and value.usage.output_tokens == 4096
