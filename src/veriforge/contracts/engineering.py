"""Additive engineering references. Native facts/results remain authoritative.

No command, import path, executable recipe or domain-value dictionary is accepted.
Validation establishes record consistency, never tool execution or authenticity.
"""
from typing import Annotated, Literal

from pydantic import Field, StrictInt, model_validator

from . import CheckerIdentity, Identifier, Sha256, WireModel

Text = Annotated[str, Field(min_length=1, max_length=1000)]
Mode = Literal["import", "execute", "repair"]
Domain = Literal["rtl", "riscv", "firmware", "vivado", "kicad", "system"]


def _unique(values, label):
    if len(values) != len(set(values)):
        raise ValueError("duplicate " + label)


class ToolIdentity(WireModel):
    name: Identifier
    version: Annotated[str, Field(min_length=1, max_length=256)]


class EvidenceReference(WireModel):
    artifact_id: Identifier
    sha256: Sha256
    revision_id: Identifier
    attempt_id: Identifier | None = None


class AdapterIdentity(WireModel):
    schema_version: Literal["engineering.adapter.v1"] = "engineering.adapter.v1"
    adapter_id: Identifier
    adapter_version: Identifier
    native_schema_id: Identifier
    implementation_hash: Sha256
    configuration_hash: Sha256


class CapabilitySupport(WireModel):
    mode: Mode
    maturity: Literal["planned", "unsupported", "experimental", "demonstrated"]
    scope: Text
    evidence: Annotated[tuple[EvidenceReference, ...], Field(max_length=64)] = ()

    @model_validator(mode="after")
    def demonstrated_evidence(self):
        if self.maturity == "demonstrated" and not self.evidence:
            raise ValueError("demonstrated support requires immutable evidence references")
        if self.maturity == "demonstrated" and self.mode != "import" and not any(ref.attempt_id for ref in self.evidence):
            raise ValueError("demonstrated execution/repair requires an attempt reference")
        _unique([(ref.artifact_id, ref.revision_id, ref.attempt_id) for ref in self.evidence], "capability evidence")
        return self


class AdapterCapabilities(WireModel):
    identity: AdapterIdentity
    support: Annotated[tuple[CapabilitySupport, ...], Field(min_length=3, max_length=3)]

    @model_validator(mode="after")
    def independent_modes(self):
        if {item.mode for item in self.support} != {"import", "execute", "repair"}:
            raise ValueError("import, execute and repair must each be explicit exactly once")
        return self


class LineLocation(WireModel):
    kind: Literal["lines"] = "lines"
    start: Annotated[StrictInt, Field(ge=1)]
    end: Annotated[StrictInt, Field(ge=1)]

    @model_validator(mode="after")
    def ordered(self):
        if self.end < self.start:
            raise ValueError("line end precedes start")
        return self


class PointerLocation(WireModel):
    kind: Literal["json_pointer"] = "json_pointer"
    pointer: Annotated[str, Field(max_length=512, pattern=r"^(?:/(?:[^~/]|~[01])*)*$")]


class NativeObjectLocation(WireModel):
    kind: Literal["native_object"] = "native_object"
    object_id: Identifier
    field: Identifier


ExtractionLocation = Annotated[LineLocation | PointerLocation | NativeObjectLocation, Field(discriminator="kind")]


class SourceProvenance(WireModel):
    artifact: EvidenceReference
    location: ExtractionLocation


class ExtractionIdentity(WireModel):
    method: Literal["native_parser", "tool_report", "manual_review", "model_proposal", "derived"]
    extractor_id: Identifier
    extractor_version: Identifier
    implementation_hash: Sha256
    tool: ToolIdentity | None = None

    @model_validator(mode="after")
    def observed_tool(self):
        if self.method == "tool_report" and self.tool is None:
            raise ValueError("tool-report extraction requires actual tool identity/version")
        return self


class FactProvenance(WireModel):
    state: Literal["complete", "ambiguous", "missing"]
    sources: Annotated[tuple[SourceProvenance, ...], Field(max_length=32)] = ()
    extraction: ExtractionIdentity | None = None
    derived_from: Annotated[tuple[Identifier, ...], Field(max_length=32)] = ()
    uncertainties: Annotated[tuple[Text, ...], Field(max_length=32)] = ()

    @model_validator(mode="after")
    def explicit_completeness(self):
        if self.state == "complete" and (not self.sources or self.extraction is None):
            raise ValueError("complete provenance requires source/location and extraction identity")
        if self.state != "complete" and not self.uncertainties:
            raise ValueError("ambiguous/missing provenance requires explicit uncertainty")
        if self.extraction and self.extraction.method == "derived" and not self.derived_from:
            raise ValueError("derived extraction requires input fact lineage")
        _unique(list(self.derived_from), "derivation input")
        return self


class NativeFactReference(WireModel):
    fact_id: Identifier
    domain: Domain
    native_schema_id: Identifier
    native_record_hash: Sha256
    native_record: EvidenceReference
    provenance: FactProvenance
    category: Literal["observation", "requirement", "hypothesis"]
    review_state: Literal["unreviewed", "reviewed"] = "unreviewed"
    conditions: Annotated[tuple[Text, ...], Field(max_length=32)] = ()

    @model_validator(mode="after")
    def record_identity(self):
        if self.native_record.sha256 != self.native_record_hash:
            raise ValueError("native record reference/hash disagree")
        return self


class FactRelation(WireModel):
    relation_id: Identifier
    relation: Literal["maps_to", "same_signal", "same_address", "same_clock", "connects_to", "satisfies"]
    fact_ids: Annotated[tuple[Identifier, ...], Field(min_length=2, max_length=16)]
    requirement_fact_ids: Annotated[tuple[Identifier, ...], Field(max_length=32)] = ()
    basis: Literal["hypothesis", "reviewed", "independent_check"]
    verdict: Literal["consistent", "conflict", "inconclusive"]
    dependence: Literal["independent", "same_source", "unknown"]
    checker: CheckerIdentity | None = None
    evidence: Annotated[tuple[EvidenceReference, ...], Field(max_length=64)] = ()
    uncertainties: Annotated[tuple[Text, ...], Field(max_length=32)] = ()

    @model_validator(mode="after")
    def checked_claim(self):
        _unique(list(self.fact_ids), "relation endpoint")
        _unique(list(self.requirement_fact_ids), "requirement reference")
        if self.basis == "independent_check" and (self.dependence != "independent" or self.checker is None or not self.evidence):
            raise ValueError("independent check requires independent provenance, checker and evidence")
        if self.verdict == "inconclusive" and not self.uncertainties:
            raise ValueError("inconclusive relation requires uncertainty")
        return self


class SystemManifest(WireModel):
    schema_version: Literal["engineering.manifest.v1"] = "engineering.manifest.v1"
    manifest_id: Identifier
    revision_id: Identifier
    review_state: Literal["draft", "reviewed"] = "draft"
    reviewed_by: Identifier | None = None
    adapters: Annotated[tuple[AdapterCapabilities, ...], Field(max_length=64)] = ()
    facts: Annotated[tuple[NativeFactReference, ...], Field(min_length=1, max_length=256)]
    relations: Annotated[tuple[FactRelation, ...], Field(max_length=256)] = ()

    @model_validator(mode="after")
    def checked_lineage(self):
        if self.review_state == "reviewed" and self.reviewed_by is None:
            raise ValueError("reviewed manifest requires reviewer identity")
        _unique([(item.identity.adapter_id, item.identity.adapter_version, item.identity.configuration_hash) for item in self.adapters], "adapter configuration")
        _unique([fact.fact_id for fact in self.facts], "fact")
        _unique([rel.relation_id for rel in self.relations], "relation")
        facts = {fact.fact_id: fact for fact in self.facts}
        references = [reference for adapter in self.adapters for mode in adapter.support for reference in mode.evidence]
        references.extend(reference for fact in self.facts for reference in
                          (fact.native_record, *(source.artifact for source in fact.provenance.sources)))
        references.extend(reference for relation in self.relations for reference in relation.evidence)
        identities = {}
        for reference in references:
            key = reference.artifact_id, reference.revision_id, reference.attempt_id
            if key in identities and identities[key] != reference.sha256:
                raise ValueError("conflicting hash for one immutable artifact identity")
            identities[key] = reference.sha256
        visiting, cache = set(), {}

        def roots(fact_id):
            if fact_id not in facts:
                raise ValueError("unresolved derivation fact")
            if fact_id in visiting:
                raise ValueError("cyclic fact derivation")
            if fact_id in cache:
                return cache[fact_id]
            visiting.add(fact_id)
            fact = facts[fact_id]
            provenance = fact.provenance
            complete = provenance.state == "complete" and fact.category != "hypothesis" and bool(
                provenance.extraction and provenance.extraction.method != "model_proposal")
            parents = [roots(parent) for parent in provenance.derived_from]
            # Source-content hashes, never labels/extractor IDs, establish root
            # dependence. Generated outputs inherit their inputs' roots.
            hashes = set().union(*(parent[0] for parent in parents)) if parents else {source.artifact.sha256 for source in provenance.sources}
            complete = complete and all(parent[1] for parent in parents)
            reviewed_ancestry = fact.review_state == "reviewed" and all(parent[2] for parent in parents)
            visiting.remove(fact_id)
            cache[fact_id] = hashes, complete, reviewed_ancestry
            return cache[fact_id]

        for fact_id in facts:
            roots(fact_id)
        for relation in self.relations:
            if any(fact_id not in facts for fact_id in relation.fact_ids):
                raise ValueError("unresolved relation endpoint")
            if any(key not in facts or facts[key].category != "requirement" for key in relation.requirement_fact_ids):
                raise ValueError("requirement context must resolve to native requirement facts")
            histories = [roots(fact_id) for fact_id in relation.fact_ids]
            complete = all(history[1] and history[0] for history in histories)
            overlap = any(histories[i][0] & histories[j][0] for i in range(len(histories)) for j in range(i))
            if relation.dependence == "independent" and (not complete or overlap):
                raise ValueError("independent claim has missing/ambiguous/shared derivation roots")
            if relation.dependence == "same_source" and not overlap:
                raise ValueError("same-source dependence requires a shared derivation root")
            if relation.basis == "independent_check":
                if not all(history[2] for history in histories):
                    raise ValueError("independently checked claim requires reviewed derivation ancestry")
                if self.review_state != "reviewed" or any(facts[key].review_state != "reviewed" or facts[key].category != "observation" for key in relation.fact_ids):
                    raise ValueError("independently checked claim requires reviewed independently extracted observations")
                if any(facts[key].review_state != "reviewed" for key in relation.requirement_fact_ids):
                    raise ValueError("checked requirement context must be reviewed")
        return self


__all__ = ["AdapterCapabilities", "AdapterIdentity", "CapabilitySupport", "EvidenceReference", "ExtractionIdentity",
           "FactProvenance", "FactRelation", "LineLocation", "NativeFactReference", "NativeObjectLocation",
           "PointerLocation", "SourceProvenance", "SystemManifest", "ToolIdentity"]
