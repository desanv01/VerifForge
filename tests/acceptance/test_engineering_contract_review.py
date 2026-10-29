"""Independent wire/lineage consistency tests; all evidence references are fabricated."""
import hashlib

import pytest
from pydantic import ValidationError

from veriforge.contracts import canonical_hash
from veriforge.contracts.engineering import (
    AdapterCapabilities, AdapterIdentity, CapabilitySupport, EvidenceReference,
    ExtractionIdentity, FactProvenance, FactRelation, NativeFactReference,
    SourceProvenance, SystemManifest,
)


def digest(label):
    return hashlib.sha256(label.encode()).hexdigest()


def evidence(label, *, source=None, attempt=None):
    return EvidenceReference(artifact_id=label, sha256=digest(source or label),
                             revision_id="review-revision", attempt_id=attempt)


def fact(label, *, parents=(), source=None, method=None, review="reviewed", category="observation", state="complete"):
    return NativeFactReference(fact_id=label, domain="system", native_schema_id="review.native.v1",
        native_record_hash=digest(label + "-native"), native_record=evidence(label + "-native"),
        category=category, review_state=review,
        provenance=FactProvenance(state=state,
            sources=(SourceProvenance(artifact=evidence(label + "-source", source=source),
                                     location={"kind": "json_pointer", "pointer": "/facts/0"}),),
            extraction=ExtractionIdentity(method=method or ("derived" if parents else "native_parser"),
                extractor_id="fabricated-parser", extractor_version="review-1", implementation_hash=digest("parser")),
            derived_from=parents, uncertainties=() if state == "complete" else ("fabricated ambiguity",)))


def relation(*endpoints, **changes):
    values = dict(relation_id="review-relation", relation="same_address", fact_ids=endpoints,
        basis="independent_check", verdict="consistent", dependence="independent",
        checker={"checker_id": "review-checker", "version": "1", "sha256": digest("checker")},
        evidence=(evidence("comparison-result", attempt="fake-attempt"),))
    values.update(changes)
    return FactRelation(**values)


def manifest(facts, relations=()):
    return SystemManifest(manifest_id="review-manifest", revision_id="review-revision",
                          review_state="reviewed", reviewed_by="fabricated-reviewer",
                          facts=facts, relations=relations)


@pytest.mark.parametrize("depth", [1, 3])
def test_reviewed_leaf_cannot_launder_explicitly_unreviewed_ancestor(depth):
    chain = [fact("ancestor", review="unreviewed")]
    for i in range(depth):
        chain.append(fact("leaf-" + str(i), parents=(chain[-1].fact_id,)))
    independent = fact("independent")
    with pytest.raises(ValidationError):
        manifest((*chain, independent), (relation(chain[-1].fact_id, independent.fact_id),))


def test_reviewed_leaf_with_reviewed_ancestry_and_disjoint_roots_is_valid():
    root, other = fact("root"), fact("other")
    leaf = fact("leaf", parents=(root.fact_id,))
    value = manifest((root, leaf, other), (relation("leaf", "other"),))
    assert canonical_hash(value) == canonical_hash(SystemManifest.model_validate_json(value.model_dump_json()))


@pytest.mark.parametrize("root_change", [
    {"method": "model_proposal"}, {"category": "hypothesis"},
    {"state": "ambiguous"}, {"state": "missing"},
])
def test_two_hop_reviewed_derivation_cannot_hide_nonobservational_or_incomplete_lineage(root_change):
    root = fact("root", **root_change)
    bridge = fact("bridge", parents=("root",))
    leaf = fact("leaf", parents=("bridge",))
    with pytest.raises(ValidationError):
        manifest((root, bridge, leaf, fact("other")), (relation("leaf", "other"),))


def test_partial_root_overlap_detected_across_diamond_and_multiple_endpoint_paths():
    shared, left_root, right_root = fact("shared"), fact("left-root"), fact("right-root")
    left = fact("left", parents=("shared", "left-root"))
    bridge = fact("bridge", parents=("shared",))
    right = fact("right", parents=("bridge", "right-root"))
    facts = (shared, left_root, right_root, left, bridge, right, fact("third"))
    with pytest.raises(ValidationError, match="shared derivation"):
        manifest(facts, (relation("left", "third", "right"),))
    retained = manifest(facts, (relation("left", "right", basis="reviewed", dependence="same_source",
        verdict="inconclusive", uncertainties=("Shared actual derivation input",)),))
    assert retained.relations[0].dependence == "same_source"


def test_disconnected_cycle_or_missing_parent_cannot_hide_outside_checked_endpoints():
    for dangling in ((fact("unused", parents=("missing",)),),
                     (fact("unused-a", parents=("unused-b",)), fact("unused-b", parents=("unused-a",)))):
        with pytest.raises(ValidationError):
            manifest((fact("left"), fact("right"), *dangling), (relation("left", "right"),))


def test_conflicting_identity_detected_between_fact_native_record_and_relation_evidence():
    left = fact("left")
    conflict = EvidenceReference(artifact_id=left.native_record.artifact_id,
        sha256=digest("different-bytes"), revision_id=left.native_record.revision_id)
    with pytest.raises(ValidationError, match="conflicting hash"):
        manifest((left, fact("right")), (relation("left", "right", evidence=(conflict,)),))


def test_mode_maturity_is_explicit_and_independent_in_both_directions():
    identity = AdapterIdentity(adapter_id="review.adapter", adapter_version="1", native_schema_id="review.v1",
                               implementation_hash=digest("implementation"), configuration_hash=digest("config"))
    declared = AdapterCapabilities(identity=identity, support=(
        CapabilitySupport(mode="import", maturity="unsupported", scope="No import claim"),
        CapabilitySupport(mode="execute", maturity="demonstrated", scope="Supplied attempt reference only",
                          evidence=(evidence("execution", attempt="fabricated"),)),
        CapabilitySupport(mode="repair", maturity="planned", scope="Not executed")))
    assert {x.mode: x.maturity for x in declared.support} == {
        "import": "unsupported", "execute": "demonstrated", "repair": "planned"}
    # An accepted assertion is not verification of the supplied artifact's contents.
    assert declared.support[1].evidence[0].attempt_id == "fabricated"


@pytest.mark.parametrize("field,value", [
    ("facts", 257), ("relations", 257),
])
def test_manifest_limits_checked_before_unbounded_graph_processing(field, value):
    payload = manifest((fact("left"), fact("right"))).model_dump(mode="json")
    payload[field] = [fact("f-" + str(i)).model_dump(mode="json") for i in range(value)] if field == "facts" else [
        relation("left", "right", relation_id="r-" + str(i)).model_dump(mode="json") for i in range(value)]
    with pytest.raises(ValidationError, match="at most 256"):
        SystemManifest.model_validate(payload)


def test_maximum_depth_bounded_graph_roundtrips_without_recursion_failure():
    facts = [fact("f-0")]
    for i in range(1, 256):
        facts.append(fact("f-" + str(i), parents=("f-" + str(i - 1),)))
    value = manifest(tuple(facts))
    assert len(value.facts) == 256
    assert SystemManifest.model_validate_json(value.model_dump_json()) == value


def test_parent_and_endpoint_fanout_bounds_are_not_inferred_from_labels():
    payload = fact("leaf").model_dump(mode="json")
    payload["provenance"]["derived_from"] = ["parent-" + str(i) for i in range(33)]
    with pytest.raises(ValidationError, match="at most 32"):
        NativeFactReference.model_validate(payload)
    with pytest.raises(ValidationError, match="at most 16"):
        relation(*(str(i) for i in range(17)))


@pytest.mark.parametrize("path", [(), ("provenance",), ("provenance", "extraction"),
    ("provenance", "sources", 0, "location"), ("native_record",)])
def test_unknown_execution_payload_rejected_at_each_nested_wire_boundary(path):
    payload = fact("fact").model_dump(mode="json")
    nested = payload
    for key in path:
        nested = nested[key]
    nested["command"] = "never executable"
    with pytest.raises(ValidationError, match="Extra inputs"):
        NativeFactReference.model_validate(payload)


def test_explicit_unknown_lineage_remains_uncertain_and_cannot_be_promoted_by_manifest_review():
    left = fact("left", state="ambiguous")
    value = manifest((left, fact("right")), (relation("left", "right", basis="hypothesis", dependence="unknown",
        verdict="inconclusive", uncertainties=("Ancestry is not established",), checker=None, evidence=()),))
    assert value.review_state == "reviewed" and value.facts[0].provenance.state == "ambiguous"
    payload = value.model_dump(mode="json")
    payload["relations"] = [relation("left", "right").model_dump(mode="json")]
    with pytest.raises(ValidationError):
        SystemManifest.model_validate(payload)
