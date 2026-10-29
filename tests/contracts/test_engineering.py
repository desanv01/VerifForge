"""Engineering references only: no domain tools, acceptance claims or execution."""
import pytest
from pydantic import ValidationError

import veriforge.contracts as legacy
from veriforge.contracts import canonical_hash
from veriforge.contracts.engineering import (
    AdapterCapabilities, AdapterIdentity, CapabilitySupport, EvidenceReference, ExtractionIdentity,
    FactProvenance, FactRelation, NativeFactReference, PointerLocation, SourceProvenance, SystemManifest,
)

H = "a" * 64
J = "b" * 64
K = "c" * 64


def evidence(name="record", digest=H, **kwargs):
    return EvidenceReference(artifact_id=name, sha256=digest, revision_id="r1", **kwargs)


def fact(name, digest=H, **changes):
    values = dict(fact_id=name, domain="firmware", native_schema_id="firmware.address.v1", native_record_hash=K,
        native_record=evidence(name + "-native", K), category="observation", review_state="reviewed",
        provenance=FactProvenance(state="complete", sources=(SourceProvenance(artifact=evidence(name + "-source", digest),
            location={"kind": "lines", "start": 1, "end": 2}),), extraction=ExtractionIdentity(
                method="native_parser", extractor_id="address-parser", extractor_version="1", implementation_hash=K)))
    values.update(changes)
    return NativeFactReference(**values)


def relation(**changes):
    values = dict(relation_id="mapping", relation="same_address", fact_ids=("rtl", "firmware"),
        basis="independent_check", verdict="consistent", dependence="independent",
        checker={"checker_id": "address-checker", "version": "1", "sha256": K}, evidence=(evidence("check"),))
    values.update(changes)
    return FactRelation(**values)


def manifest(facts=None, relations=None, **changes):
    values = dict(manifest_id="system", revision_id="r1", review_state="reviewed", reviewed_by="reviewer",
                  facts=(fact("rtl", H), fact("firmware", J)) if facts is None else facts,
                  relations=(relation(),) if relations is None else relations)
    values.update(changes)
    return SystemManifest(**values)


def test_modes_are_separate_and_no_support_is_inferred_from_import():
    adapter = AdapterCapabilities(identity=AdapterIdentity(adapter_id="vivado.reports.v1", adapter_version="1",
        native_schema_id="vivado.report.v1", implementation_hash=H, configuration_hash=J), support=(
            CapabilitySupport(mode="import", maturity="demonstrated", scope="imported logs", evidence=(evidence(),)),
            CapabilitySupport(mode="execute", maturity="unsupported", scope="tool absent"),
            CapabilitySupport(mode="repair", maturity="planned", scope="not validated"),
        ))
    assert adapter.support[0].maturity == "demonstrated"
    assert adapter.support[1].maturity == "unsupported" and adapter.support[2].maturity == "planned"
    assert AdapterCapabilities.model_validate_json(adapter.model_dump_json()) == adapter
    assert canonical_hash(adapter.identity) != canonical_hash(adapter.identity.model_copy(update={"configuration_hash": K}))
    with pytest.raises(ValidationError):
        AdapterCapabilities(identity=adapter.identity, support=(adapter.support[0],) * 3)


@pytest.mark.parametrize("mode", ["import", "execute", "repair"])
def test_demonstrated_mode_requires_immutable_evidence(mode):
    with pytest.raises(ValidationError):
        CapabilitySupport(mode=mode, maturity="demonstrated", scope="example")


@pytest.mark.parametrize("mode", ["execute", "repair"])
def test_demonstrated_execution_or_repair_requires_attempt_reference(mode):
    with pytest.raises(ValidationError):
        CapabilitySupport(mode=mode, maturity="demonstrated", scope="example", evidence=(evidence(),))
    assert CapabilitySupport(mode=mode, maturity="demonstrated", scope="example", evidence=(evidence(attempt_id="a1"),))


def test_reviewed_independent_observations_reference_native_records_without_flattening():
    value = manifest()
    assert SystemManifest.model_validate_json(value.model_dump_json()) == value
    assert "value" not in value.facts[0].model_dump()
    with pytest.raises(ValidationError):
        value.facts[0].native_record_hash = J
    assert canonical_hash(value) == canonical_hash(SystemManifest.model_validate_json(value.model_dump_json()))


def test_shared_requirements_are_context_not_fake_independent_observations():
    requirement = fact("requirement", K, category="requirement")
    value = manifest(facts=(fact("rtl", H), fact("firmware", J), requirement),
                     relations=(relation(requirement_fact_ids=("requirement",)),))
    assert value.relations[0].requirement_fact_ids == ("requirement",)
    with pytest.raises(ValidationError):
        manifest(facts=(fact("rtl", H, category="requirement"), fact("firmware", J)))


def test_equal_source_content_under_different_names_is_same_source():
    with pytest.raises(ValidationError, match="shared derivation"):
        manifest(facts=(fact("rtl", H), fact("firmware", H)))
    dependent = relation(basis="reviewed", dependence="same_source", verdict="inconclusive", uncertainties=("same source cannot independently cross-check itself",))
    assert manifest(facts=(fact("rtl", H), fact("firmware", H)), relations=(dependent,)).relations[0].verdict == "inconclusive"


def test_one_artifact_identity_cannot_carry_conflicting_hashes():
    rtl, firmware = fact("rtl", H), fact("firmware", J)
    source = firmware.provenance.sources[0]
    source = source.model_copy(update={"artifact": source.artifact.model_copy(update={"artifact_id": "rtl-source"})})
    firmware = firmware.model_copy(update={"provenance": firmware.provenance.model_copy(update={"sources": (source,)})})
    with pytest.raises(ValidationError, match="conflicting hash"):
        manifest(facts=(rtl, firmware))


def test_independent_check_may_be_inconclusive_with_complete_reviewed_provenance():
    value = manifest(relations=(relation(verdict="inconclusive", uncertainties=("address units unresolved by checker",)),))
    assert value.relations[0].verdict == "inconclusive"


def test_ambiguous_ancestor_cannot_be_hidden_by_complete_generated_leaf():
    root = fact("root", provenance=FactProvenance(state="ambiguous", uncertainties=("source lineage unknown",)))
    firmware = fact("firmware", J)
    firmware = firmware.model_copy(update={"provenance": firmware.provenance.model_copy(update={"derived_from": ("root",)})})
    with pytest.raises(ValidationError):
        manifest(facts=(fact("rtl", H), root, firmware))


def test_unreviewed_ancestry_can_remain_nonchecked_unknown_inconclusive():
    root = fact("root", K, review_state="unreviewed")
    firmware = fact("firmware", J)
    firmware = firmware.model_copy(update={"provenance": firmware.provenance.model_copy(update={"derived_from": ("root",)})})
    uncertain = relation(basis="reviewed", dependence="unknown", verdict="inconclusive", checker=None, evidence=(),
                         uncertainties=("Upstream derivation not reviewed",))
    value = manifest(facts=(root, fact("rtl", H), firmware), relations=(uncertain,))
    assert value.relations[0].basis == "reviewed" and value.relations[0].verdict == "inconclusive"
    with pytest.raises(ValidationError, match="reviewed derivation ancestry"):
        manifest(facts=value.facts)


def test_generated_outputs_inherit_root_dependence_even_with_distinct_output_hashes():
    root = fact("source-manifest", K, category="requirement")
    rtl = fact("rtl", H)
    firmware = fact("firmware", J)
    rtl = rtl.model_copy(update={"provenance": rtl.provenance.model_copy(update={"derived_from": (root.fact_id,)})})
    firmware = firmware.model_copy(update={"provenance": firmware.provenance.model_copy(update={"derived_from": (root.fact_id,)})})
    with pytest.raises(ValidationError, match="shared derivation"):
        manifest(facts=(root, rtl, firmware))
    dependent = relation(basis="reviewed", dependence="same_source", verdict="inconclusive", uncertainties=("both generated from one manifest",))
    assert manifest(facts=(root, rtl, firmware), relations=(dependent,))


@pytest.mark.parametrize("state", ["missing", "ambiguous"])
def test_incomplete_provenance_can_be_explicit_but_never_independently_checked(state):
    incomplete = fact("firmware", J, provenance=FactProvenance(state=state, uncertainties=("source extraction unresolved",)))
    with pytest.raises(ValidationError):
        manifest(facts=(fact("rtl", H), incomplete))
    unresolved = relation(basis="hypothesis", dependence="unknown", verdict="inconclusive", uncertainties=("native evidence missing",), checker=None, evidence=())
    assert manifest(facts=(fact("rtl", H), incomplete), relations=(unresolved,))


@pytest.mark.parametrize("mode", ["missing_parent", "cycle", "missing_endpoint"])
def test_all_lineage_and_relation_references_must_resolve_acyclically(mode):
    rtl, firmware = fact("rtl", H), fact("firmware", J)
    if mode == "missing_parent":
        rtl = rtl.model_copy(update={"provenance": rtl.provenance.model_copy(update={"derived_from": ("absent",)})})
    elif mode == "cycle":
        rtl = rtl.model_copy(update={"provenance": rtl.provenance.model_copy(update={"derived_from": ("firmware",)})})
        firmware = firmware.model_copy(update={"provenance": firmware.provenance.model_copy(update={"derived_from": ("rtl",)})})
    relations = (relation(fact_ids=("rtl", "absent")),) if mode == "missing_endpoint" else ()
    with pytest.raises(ValidationError):
        manifest(facts=(rtl, firmware), relations=relations)


@pytest.mark.parametrize("changes", [
    {"basis": "independent_check", "evidence": ()}, {"checker": None}, {"dependence": "unknown"},
    {"verdict": "inconclusive", "uncertainties": ()}, {"fact_ids": ("rtl", "rtl")},
])
def test_checked_relation_rejects_missing_evidence_or_ambiguity(changes):
    with pytest.raises(ValidationError):
        relation(**changes)


@pytest.mark.parametrize("changes", [{"review_state": "draft"}, {"reviewed_by": None}])
def test_checked_claim_requires_reviewed_manifest(changes):
    with pytest.raises(ValidationError):
        manifest(**changes)


def test_model_proposal_and_unreviewed_observations_are_not_checked_source_facts():
    for firmware in (fact("firmware", J, category="hypothesis"), fact("firmware", J, review_state="unreviewed")):
        with pytest.raises(ValidationError):
            manifest(facts=(fact("rtl", H), firmware))
    firmware = fact("firmware", J)
    extraction = firmware.provenance.extraction.model_copy(update={"method": "model_proposal"})
    firmware = firmware.model_copy(update={"provenance": firmware.provenance.model_copy(update={"extraction": extraction})})
    with pytest.raises(ValidationError):
        manifest(facts=(fact("rtl", H), firmware))


def test_tool_report_needs_observed_version_and_locations_are_typed():
    with pytest.raises(ValidationError):
        ExtractionIdentity(method="tool_report", extractor_id="parser", extractor_version="1", implementation_hash=H)
    assert SourceProvenance(artifact=evidence(), location={"kind": "native_object", "object_id": "pad-J1.2", "field": "net"})
    assert PointerLocation(pointer="/registers/0/address")
    with pytest.raises(ValidationError): PointerLocation(pointer="not-a-pointer")
    with pytest.raises(ValidationError): SourceProvenance(artifact=evidence(), location={"kind": "lines", "start": True, "end": 2})


def test_unknown_command_payloads_and_hash_mismatch_rejected():
    with pytest.raises(ValidationError):
        EvidenceReference(artifact_id="x", sha256=H, revision_id="1", command="arbitrary")
    with pytest.raises(ValidationError):
        fact("rtl", native_record_hash=J)


def test_legacy_contracts_still_import_and_keep_fifo_wire_format():
    experiment = legacy.Experiment(experiment_id="legacy", fixture_id="fifo_correct", objective_ids=("RESET",), operations=({"op": "reset"},))
    assert experiment.schema_version == "1" and experiment.target == "fifo"
    assert legacy.Experiment.model_validate_json(experiment.model_dump_json()) == experiment
    assert not hasattr(legacy, "SystemManifest")
