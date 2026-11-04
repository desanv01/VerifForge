"""Required v2 transport fields; unchanged PlannerDecision is logical authority.

The wire admits only explicit single-cycle FIFO operations. Decoding removes
only validated branch-neutral sentinels and the version marker, never stimulus.
Unmarked historical logical decisions retain their original strict validation.
"""
from __future__ import annotations

import hashlib
import json
from typing import Annotated, Literal

from pydantic import Field, StrictBool, StrictInt, StrictStr

from veriforge.contracts import WireModel
from .models import PlannerDecision

WIRE_VERSION = "veriforge.planner-wire.v2"


class WireCycle(WireModel):
    op: Literal["cycle"]
    reset: StrictBool
    write: StrictBool
    read: StrictBool
    value: Annotated[StrictInt, Field(ge=0, lt=2**64)]


class WireProposal(WireModel):
    kind: Literal["experiment", "stop"]
    reason: Annotated[StrictStr, Field(max_length=500)]
    objective_ids: Annotated[tuple[StrictStr, ...], Field(max_length=100)]
    seed: Annotated[StrictInt, Field(ge=0, lt=2**32)]
    operations: Annotated[tuple[WireCycle, ...], Field(max_length=1000)]


class WireDecision(WireModel):
    wire_version: Literal[WIRE_VERSION]
    proposal: WireProposal


def decision_wire_schema() -> dict:
    """Fresh reference/union-free schema; every object field is required."""
    operation = {
        "type": "object",
        "properties": {
            "op": {"type": "string", "enum": ["cycle"]},
            "reset": {"type": "boolean"},
            "write": {"type": "boolean"},
            "read": {"type": "boolean"},
            "value": {"type": "integer", "minimum": 0, "maximum": 2**64 - 1},
        },
        "required": ["op", "reset", "write", "read", "value"],
        "additionalProperties": False,
    }
    return {
        "type": "object",
        "properties": {
            "wire_version": {"type": "string", "enum": [WIRE_VERSION]},
            "proposal": {
                "type": "object",
                "properties": {
                    "kind": {"type": "string", "enum": ["experiment", "stop"]},
                    "reason": {"type": "string", "maxLength": 500},
                    "objective_ids": {"type": "array", "items": {"type": "string"}, "maxItems": 100},
                    "seed": {"type": "integer", "minimum": 0, "maximum": 2**32 - 1},
                    "operations": {"type": "array", "items": operation, "maxItems": 1000},
                },
                "required": ["kind", "reason", "objective_ids", "seed", "operations"],
                "additionalProperties": False,
            },
        },
        "required": ["wire_version", "proposal"],
        "additionalProperties": False,
    }


def decision_wire_schema_hash() -> str:
    body = json.dumps(decision_wire_schema(), sort_keys=True, separators=(",", ":"),
                      ensure_ascii=False, allow_nan=False).encode("utf-8")
    return hashlib.sha256(body).hexdigest()


def decode_wire_decision(value: object) -> PlannerDecision:
    """Validate every field before explicitly projecting neutral branch fields."""
    wire = WireDecision.model_validate(value)
    proposal = wire.proposal
    if proposal.kind == "stop":
        if proposal.objective_ids or proposal.seed != 0 or proposal.operations:
            raise ValueError("stop_fields_not_neutral")
        logical = {"kind": "stop", "reason": proposal.reason}
    else:
        if proposal.reason != "":
            raise ValueError("experiment_reason_not_neutral")
        logical = {"kind": "experiment", "objective_ids": proposal.objective_ids,
                   "seed": proposal.seed,
                   "operations": tuple(op.model_dump(mode="json") for op in proposal.operations)}
    # Existing logical types enforce nonempty stop reason/experiment arrays and
    # all operation types/bounds. Context/reset/width/cycles remain build_experiment.
    return PlannerDecision.model_validate({"proposal": logical})


def decode_decision_json(content: str) -> PlannerDecision:
    """Unambiguous v2 decoding, or unchanged strict legacy logical acceptance."""
    if not isinstance(content, str):
        raise ValueError("decision_content_required")
    def pairs(items):
        result = {}
        for key, value in items:
            if key in result:
                raise ValueError("duplicate_decision_key")
            result[key] = value
        return result
    value = json.loads(content, object_pairs_hook=pairs,
                       parse_constant=lambda _: (_ for _ in ()).throw(ValueError("nonfinite_decision")))
    if isinstance(value, dict) and "wire_version" in value:
        return decode_wire_decision(value)
    return PlannerDecision.model_validate_json(content)
