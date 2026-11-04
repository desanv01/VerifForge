"""Simple generation schema; PlannerDecision remains the acceptance authority.

This deliberately permits a superset of valid decisions to avoid schema unions
and references on the provider wire. Missing branch fields, mixed branches and
operation-specific fields are rejected by the unchanged local validator. No
response rewriting, coercion or default injection happens at this boundary.
"""

from __future__ import annotations

import hashlib
import json


def decision_wire_schema() -> dict:
    """Return a fresh schema supporting both proposals and every FIFO operation."""
    operation = {
        "type": "object",
        "description": (
            "reset/idle: op and optional cycles; write: op and required value; "
            "read: op only; cycle: op and optional reset/write/read/value. "
            "Use only fields belonging to the selected op."
        ),
        "properties": {
            "op": {"type": "string", "enum": ["reset", "write", "read", "idle", "cycle"]},
            "cycles": {"type": "integer", "minimum": 1, "maximum": 1_000_000},
            "value": {"type": "integer", "minimum": 0, "maximum": 2**64 - 1},
            "reset": {"type": "boolean"},
            "write": {"type": "boolean"},
            "read": {"type": "boolean"},
        },
        "required": ["op"],
        "additionalProperties": False,
    }
    return {
        "type": "object",
        "properties": {
            "proposal": {
                "type": "object",
                "description": (
                    "stop requires kind and reason only. experiment requires kind, "
                    "objective_ids, seed and operations only."
                ),
                "properties": {
                    "kind": {"type": "string", "enum": ["experiment", "stop"]},
                    "reason": {"type": "string", "minLength": 1, "maxLength": 500},
                    "objective_ids": {"type": "array", "items": {"type": "string"},
                                      "minItems": 1, "maxItems": 100},
                    "seed": {"type": "integer", "minimum": 0, "maximum": 2**32 - 1},
                    "operations": {"type": "array", "items": operation,
                                   "minItems": 1, "maxItems": 1000},
                },
                "required": ["kind"],
                "additionalProperties": False,
            },
        },
        "required": ["proposal"],
        "additionalProperties": False,
    }


def decision_wire_schema_hash() -> str:
    """SHA256 of canonical UTF-8 JSON (sorted keys, compact, no ASCII escaping)."""
    body = json.dumps(decision_wire_schema(), sort_keys=True, separators=(",", ":"),
                      ensure_ascii=False, allow_nan=False).encode("utf-8")
    return hashlib.sha256(body).hexdigest()
