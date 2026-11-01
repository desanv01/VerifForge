"""Reviewed, hash-bound source comparisons and correction rerun obligations.

Host authentication of review/origin assertions is required. This module neither
executes tools nor establishes physical correctness from matching CAD facts.
"""
from hashlib import sha256
from pathlib import Path
import json
from typing import Annotated, Literal

from pydantic import Field, StrictInt, StrictStr, model_validator

from veriforge.contracts import Identifier, WireModel, canonical_hash
from veriforge.contracts.engineering import SystemManifest


class ManifestCheck(WireModel):
    check_id: Identifier
    quantity: Literal["register_address", "package_pin", "net", "clock_period_ps"]
    fact_ids: Annotated[tuple[Identifier, ...], Field(min_length=2, max_length=16)]
    requirement_fact_ids: Annotated[tuple[Identifier, ...], Field(min_length=1, max_length=16)]
    field: Annotated[str, Field(pattern=r"^[A-Za-z_][A-Za-z0-9_]{0,95}$")]
    expected: StrictInt | StrictStr
    reviewed_by: Identifier | None = None
    # Explicit downstream checks that must be repeated if this check is affected.
    rerun_check_ids: Annotated[tuple[Identifier, ...], Field(max_length=32)] = ()

    @model_validator(mode="after")
    def bounded_value(self):
        if len(set(self.fact_ids)) != len(self.fact_ids) or len(set(self.requirement_fact_ids)) != len(self.requirement_fact_ids):
            raise ValueError("duplicate check endpoints")
        if self.quantity in ("register_address", "clock_period_ps"):
            if type(self.expected) is not int or not 0 <= self.expected < 2**64 or (self.quantity == "clock_period_ps" and self.expected == 0):
                raise ValueError("canonical nonnegative integer units required")
        elif type(self.expected) is not str or not 1 <= len(self.expected) <= 160:
            raise ValueError("bounded literal value required")
        return self


def implementation_hash():
    return sha256(Path(__file__).read_bytes()).hexdigest()


def record_hash(record):
    return sha256(json.dumps(record, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False).encode()).hexdigest()


def _inputs(manifest, checks):
    manifest = SystemManifest.model_validate_json(manifest.model_dump_json())
    checks = tuple(ManifestCheck.model_validate_json(c.model_dump_json()) for c in checks)
    if not 1 <= len(checks) <= 128 or len({c.check_id for c in checks}) != len(checks):
        raise ValueError("unique bounded check set required")
    facts = {f.fact_id: f for f in manifest.facts}
    ids = {c.check_id for c in checks}
    for c in checks:
        if not set(c.rerun_check_ids) <= ids:
            raise ValueError("unresolved downstream check")
        if any(key not in facts or facts[key].category != "observation" for key in c.fact_ids):
            raise ValueError("check endpoints must resolve to observations")
        if any(key not in facts or facts[key].category != "requirement" for key in c.requirement_fact_ids):
            raise ValueError("check requires native requirement context")
    return manifest, checks, facts


def check_manifest(manifest, records: dict[str, dict], checks: tuple[ManifestCheck, ...]):
    """Compare explicit units/literals. Malformed bytes are errors, not defects.

    records are canonical native record objects, never caller-asserted scalars.
    Checks/requirements must be reviewed by the trusted host before admission.
    """
    manifest, checks, facts = _inputs(manifest, checks)
    needed = {key for c in checks for key in (*c.fact_ids, *c.requirement_fact_ids)}
    if not needed <= records.keys() or not records.keys() <= facts.keys():
        raise ValueError("missing or orphan native record")
    for key, record in records.items():
        if type(record) is not dict or record_hash(record) != facts[key].native_record_hash:
            raise ValueError("native record identity differs")
    cache = {}
    def history(key):
        if key in cache:
            return cache[key]
        f = facts[key]
        parents = [history(p) for p in f.provenance.derived_from]
        roots = set().union(*(p[0] for p in parents)) if parents else {s.artifact.sha256 for s in f.provenance.sources}
        reviewed = f.review_state == "reviewed" and f.provenance.state == "complete" and f.category != "hypothesis" and bool(
            f.provenance.extraction and f.provenance.extraction.method != "model_proposal") and all(p[1] for p in parents)
        cache[key] = roots, reviewed
        return cache[key]
    rows = []
    for c in checks:
        values = [records[key].get(c.field) for key in c.fact_ids]
        valid = all(type(v) is type(c.expected) and (type(v) is int and 0 <= v < 2**64 or type(v) is str and 1 <= len(v) <= 160) for v in values)
        if c.quantity == "clock_period_ps":
            valid = valid and all(v > 0 for v in values if type(v) is int)
        observed = "inconclusive" if not valid else "consistent" if all(v == c.expected for v in values) else "conflict"
        histories = [history(key) for key in (*c.fact_ids, *c.requirement_fact_ids)]
        overlap = any(histories[i][0] & histories[j][0] for i in range(len(histories)) for j in range(i))
        reasons = []
        if c.reviewed_by is None: reasons.append("unreviewed_check_binding")
        if manifest.review_state != "reviewed" or not all(h[1] and h[0] for h in histories): reasons.append("unreviewed_or_incomplete_ancestry")
        if overlap: reasons.append("common_source_ancestry")
        if not valid: reasons.append("missing_or_unsupported_value")
        rows.append(dict(check_id=c.check_id, quantity=c.quantity, fact_ids=c.fact_ids, expected=c.expected,
                         observed=values, value_comparison=observed, verdict="inconclusive" if reasons else observed, reasons=reasons))
    return dict(execution_status="completed", scope="source_comparison_only", physical_verdict="inconclusive",
                manifest_hash=canonical_hash(manifest), checks_hash=record_hash({"checks": [c.model_dump(mode="json") for c in checks]}),
                checker_hash=implementation_hash(), comparisons=rows)


def affected_checks(before, after, checks: tuple[ManifestCheck, ...]):
    """Changed facts and all descendants invalidate consuming/downstream checks.

    Even a corrected consistent value does not discharge these obligations.
    """
    before, checks, old = _inputs(before, checks)
    after = SystemManifest.model_validate_json(after.model_dump_json())
    new = {f.fact_id: f for f in after.facts}
    changed = {key for key in old.keys() | new.keys() if key not in old or key not in new or canonical_hash(old[key]) != canonical_hash(new[key])}
    impacted = set(changed)
    while True:
        more = {key for graph in (old, new) for key, fact in graph.items() if set(fact.provenance.derived_from) & impacted}
        if more <= impacted: break
        impacted |= more
    required = {c.check_id for c in checks if set((*c.fact_ids, *c.requirement_fact_ids)) & impacted}
    if (before.review_state, before.reviewed_by) != (after.review_state, after.reviewed_by):
        required = {c.check_id for c in checks}
    while True:
        more = {key for c in checks if c.check_id in required for key in c.rerun_check_ids}
        if more <= required: break
        required |= more
    return dict(before_hash=canonical_hash(before), after_hash=canonical_hash(after), checker_hash=implementation_hash(),
                checks_hash=record_hash({"checks": [c.model_dump(mode="json") for c in checks]}),
                changed_fact_ids=sorted(changed), affected_fact_ids=sorted(impacted), required_check_ids=sorted(required),
                status="pending_reruns" if required else "no_affected_checks", acceptance="not_established")
