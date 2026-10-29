"""Bounded native bridge inputs; host review is an explicit trusted assertion."""
import hashlib
from typing import Annotated, Literal

from pydantic import Field, StrictBytes, StrictInt, model_validator

from veriforge.contracts import Identifier, Sha256, WireModel, canonical_hash
from veriforge.contracts.engineering import EvidenceReference, NativeFactReference, SystemManifest
from veriforge.firmware.contracts import AddressFact

MAX_BYTES = 1_048_576
Name = Annotated[str, Field(min_length=1, max_length=96)]
BindingId = Annotated[Identifier, Field(max_length=48)]


class RootArtifact(WireModel):
    reference: EvidenceReference
    data: Annotated[StrictBytes, Field(min_length=1, max_length=MAX_BYTES, repr=False, exclude=True)]
    origin: Literal["imported_artifact", "synthetic_fixture", "model_proposal"]
    reviewed_by: Identifier | None = None

    @model_validator(mode="after")
    def actual_bytes(self):
        if hashlib.sha256(self.data).hexdigest() != self.reference.sha256:
            raise ValueError("source bytes differ from admitted SHA-256")
        return self


class SourceArtifact(RootArtifact):
    # Real generator inputs, supplied with their bytes; not a label-based alias.
    parents: Annotated[tuple[RootArtifact, ...], Field(max_length=8)] = ()

    @model_validator(mode="after")
    def flat_roots(self):
        if any(type(parent) is not RootArtifact for parent in self.parents):
            raise ValueError("only explicit flat root artifacts accepted; nested ancestry cannot be truncated")
        return self


class AddressBinding(WireModel):
    binding_id: BindingId
    header_macro: Annotated[str, Field(pattern=r"^[A-Za-z_][A-Za-z0-9_]{0,95}$")]
    rtl_signal: Annotated[str, Field(pattern=r"^[A-Za-z_][A-Za-z0-9_]{0,95}$")]
    expected_address: Annotated[StrictInt, Field(ge=0, lt=2**32)]


class RegisterIntent(WireModel):
    schema_version: Literal["system.register-intent.v1"] = "system.register-intent.v1"
    bindings: Annotated[tuple[AddressBinding, ...], Field(min_length=1, max_length=16)]
    byte_base: Annotated[StrictInt, Field(ge=0, lt=2**32)] = 0
    selector: Annotated[str, Field(pattern=r"^[A-Za-z_][A-Za-z0-9_]{0,95}$")] = "addr"
    reviewed_by: Identifier | None = None
    review_evidence: EvidenceReference | None = None

    @model_validator(mode="after")
    def unique(self):
        for key in ("binding_id", "header_macro", "rtl_signal", "expected_address"):
            values = [getattr(row, key) for row in self.bindings]
            if len(set(values)) != len(values):
                raise ValueError("ambiguous duplicate register intent")
        return self


class PinBinding(WireModel):
    binding_id: BindingId
    port: Annotated[str, Field(max_length=96, pattern=r"^[A-Za-z_][A-Za-z0-9_]*(?:\[[0-9]{1,3}\])?$")]
    package_pin: Annotated[str, Field(pattern=r"^[A-Z]{1,3}[1-9][0-9]{0,2}$")]
    reference: Name
    symbol_pin: Name
    footprint_pad: Name
    schematic_net: Name
    board_net: Name


class BoardIntent(WireModel):
    schema_version: Literal["system.board-intent.v1"] = "system.board-intent.v1"
    bindings: Annotated[tuple[PinBinding, ...], Field(min_length=1, max_length=16)]
    reviewed_by: Identifier | None = None
    review_evidence: EvidenceReference | None = None

    @model_validator(mode="after")
    def unique(self):
        for keys in (("binding_id",), ("port",), ("reference", "symbol_pin"), ("reference", "footprint_pad")):
            values = [tuple(getattr(row, key) for key in keys) for row in self.bindings]
            if len(values) != len(set(values)):
                raise ValueError("ambiguous duplicate connector intent")
        return self


class PinObservation(WireModel):
    kind: Literal["package_pin"] = "package_pin"
    port: Name
    package_pin: Name
    line: Annotated[StrictInt, Field(ge=1)]
    interpretation: Literal["active_literal", "commented_board_reference"]


class ConnectorObservation(WireModel):
    kind: Literal["connector"] = "connector"
    reference: Name
    number: Name
    net: Name | None
    native_location: Annotated[str, Field(min_length=1, max_length=256)]
    interpretation: Literal["schematic_pin", "board_pad_assignment"]


class RootObservation(WireModel):
    kind: Literal["source_root"] = "source_root"
    source: EvidenceReference
    origin: Literal["imported_artifact", "synthetic_fixture", "model_proposal"]


class IntentObservation(WireModel):
    kind: Literal["intent"] = "intent"
    register_binding: AddressBinding | None = None
    pin_binding: PinBinding | None = None

    @model_validator(mode="after")
    def one(self):
        if (self.register_binding is None) == (self.pin_binding is None):
            raise ValueError("one typed intent required")
        return self


class NativeRecord(WireModel):
    fact: NativeFactReference
    data: AddressFact | PinObservation | ConnectorObservation | RootObservation | IntentObservation


class ComparisonRow(WireModel):
    binding_id: Identifier
    fact_ids: Annotated[tuple[Identifier, ...], Field(max_length=3)]
    value_comparison: Literal["consistent", "conflict", "inconclusive"]
    verdict: Literal["consistent", "conflict", "inconclusive"]
    reasons: Annotated[tuple[Annotated[str, Field(max_length=256)], ...], Field(max_length=16)]


class SourceReceipt(WireModel):
    reference: EvidenceReference
    origin: Literal["imported_artifact", "synthetic_fixture", "model_proposal"]
    size_bytes: Annotated[StrictInt, Field(ge=1, le=MAX_BYTES)]


class BridgeReport(WireModel):
    schema_version: Literal["system.bridge.v1"] = "system.bridge.v1"
    execution_status: Literal["not_run"] = "not_run"
    family: Literal["firmware_decode", "board_connector"]
    intent: RegisterIntent | BoardIntent
    intent_hash: Sha256
    checker_hash: Sha256
    manifest: SystemManifest
    native_records: Annotated[tuple[NativeRecord, ...], Field(max_length=128)]
    comparisons: Annotated[tuple[ComparisonRow, ...], Field(min_length=1, max_length=16)]
    sources: Annotated[tuple[SourceReceipt, ...], Field(max_length=32)]
    limitations: tuple[str, ...]

    @model_validator(mode="after")
    def consistent_envelope(self):
        register_family = self.family == "firmware_decode"
        if register_family != isinstance(self.intent, RegisterIntent):
            raise ValueError("bridge family and typed intent disagree")
        roles = ("header", "rtl") if register_family else ("xdc", "schematic", "board")
        if len(self.sources) != len(roles):
            raise ValueError("exact ordered native source receipts required")
        source_keys = [(s.reference.artifact_id, s.reference.revision_id, s.reference.attempt_id) for s in self.sources]
        if len(set(source_keys)) != len(source_keys):
            raise ValueError("duplicate native source receipt identity")
        if self.intent_hash != canonical_hash(self.intent):
            raise ValueError("bridge intent bytes/hash disagree")
        if self.manifest.facts != tuple(row.fact for row in self.native_records):
            raise ValueError("native records disagree with manifest facts")
        for row in self.native_records:
            if row.fact.native_record_hash != canonical_hash(row.data):
                raise ValueError("native data bytes/hash disagree")
            if row.fact.provenance.extraction.implementation_hash != self.checker_hash:
                raise ValueError("bridge implementation identities disagree")
        comparisons = {row.binding_id: row for row in self.comparisons}
        if len(comparisons) != len(self.comparisons) or set(comparisons) != {row.binding_id for row in self.intent.bindings}:
            raise ValueError("comparison intent identity set differs")
        relations = {relation.relation_id: relation for relation in self.manifest.relations}
        expected_relations = {"relation-" + row.binding_id for row in self.comparisons if len(row.fact_ids) >= 2}
        if set(relations) != expected_relations:
            raise ValueError("complete exact comparison relationship coverage required")
        records = {row.fact.fact_id: row for row in self.native_records}
        if self.manifest.reviewed_by != self.intent.reviewed_by or self.manifest.review_state != (
                "reviewed" if self.intent.reviewed_by else "draft"):
            raise ValueError("manifest cannot promote unreviewed intent")
        used_observations = set()
        for binding in self.intent.bindings:
            row = comparisons[binding.binding_id]
            ordered = tuple(role + "-" + binding.binding_id for role in roles if role + "-" + binding.binding_id in records)
            if row.fact_ids != ordered:
                raise ValueError("comparison has orphaned, reordered or misbound native facts")
            values = []
            for index, role in enumerate(roles):
                key = role + "-" + binding.binding_id
                if key not in records:
                    continue
                native = records[key]
                proof = native.fact.provenance
                receipt = self.sources[index]
                domain = {"header": "firmware", "rtl": "rtl", "xdc": "vivado", "schematic": "kicad", "board": "kicad"}[role]
                if native.fact.category != "observation" or native.fact.domain != domain:
                    raise ValueError("native observation role/domain differs from binding")
                if not proof.sources or proof.sources[0].artifact != receipt.reference:
                    raise ValueError("source receipt differs from exact native provenance identity")
                if receipt.origin == "model_proposal":
                    raise ValueError("model source cannot supply native observations")
                data = native.data
                if register_family:
                    name = binding.header_macro if role == "header" else binding.rtl_signal
                    if not isinstance(data, AddressFact) or data.name != name:
                        raise ValueError("native address fact differs from selected binding")
                    if (data.provenance.source != receipt.reference.artifact_id or
                        data.provenance.source_sha256 != receipt.reference.sha256 or
                        data.provenance.origin != receipt.origin):
                        raise ValueError("firmware native provenance differs from source receipt")
                    values.append(data.value == binding.expected_address)
                elif role == "xdc":
                    if not isinstance(data, PinObservation) or data.port != binding.port:
                        raise ValueError("native pin fact differs from selected binding")
                    values.append(data.package_pin == binding.package_pin)
                else:
                    number = binding.symbol_pin if role == "schematic" else binding.footprint_pad
                    interpretation = "schematic_pin" if role == "schematic" else "board_pad_assignment"
                    if (not isinstance(data, ConnectorObservation) or data.reference != binding.reference or
                        data.number != number or data.interpretation != interpretation):
                        raise ValueError("native connector fact differs from selected binding")
                    values.append(data.net == (binding.schematic_net if role == "schematic" else binding.board_net))
                used_observations.add(key)
            value_comparison = "inconclusive" if len(values) != len(roles) else "consistent" if all(values) else "conflict"
            if row.value_comparison != value_comparison:
                raise ValueError("literal comparison differs from bound native values")
            requirement_id = "intent-" + binding.binding_id
            requirement = records.get(requirement_id)
            payload = IntentObservation(register_binding=binding) if register_family else IntentObservation(pin_binding=binding)
            if requirement is None or requirement.data != payload or requirement.fact.category != "requirement":
                raise ValueError("comparison requires exact typed requirement context")
            relation = relations.get("relation-" + binding.binding_id)
            if row.verdict != "inconclusive":
                # SystemManifest already validates review of every ancestor and
                # disjoint complete roots for this exact independent relation.
                if (row.verdict != value_comparison or len(values) != len(roles) or self.intent.reviewed_by is None or
                    relation is None or relation.basis != "independent_check" or relation.dependence != "independent"):
                    raise ValueError("checked verdict lacks independent reviewed lineage authority")
            if relation is None:
                continue
            if relation.requirement_fact_ids != (requirement_id,) or relation.relation != ("same_address" if register_family else "maps_to"):
                raise ValueError("relationship requirement or comparison family differs")
            if relation.fact_ids != row.fact_ids or relation.verdict != row.verdict:
                raise ValueError("comparison and relationship disagree")
            if relation.basis == "independent_check" and (relation.checker is None or relation.checker.sha256 != self.checker_hash):
                raise ValueError("independent relation checker identity differs")
            if len(relation.evidence) != 1 or relation.evidence[0].sha256 != canonical_hash(row):
                raise ValueError("comparison evidence hash disagrees")
        native_observations = {row.fact.fact_id for row in self.native_records if not isinstance(row.data, (RootObservation, IntentObservation))}
        if native_observations != used_observations:
            raise ValueError("unbound native observations in bridge report")
        # Unsupported/missing native extraction still needs an input-identity
        # record, so no receipt can float free of the manifest's actual sources.
        for receipt in self.sources:
            matches = [source.artifact for row in self.native_records for source in row.fact.provenance.sources
                       if source.artifact == receipt.reference]
            if not matches:
                raise ValueError("source receipt has no matching native provenance identity")
        for row in self.native_records:
            if isinstance(row.data, RootObservation):
                if not row.fact.provenance.sources or row.fact.provenance.sources[0].artifact != row.data.source:
                    raise ValueError("root input identity differs from native provenance")
                if row.fact.fact_id not in ("root-" + row.data.source.sha256, "input-" + canonical_hash(row.data.source)):
                    raise ValueError("root fact does not bind its input identity")
        bound = used_observations | {"intent-" + binding.binding_id for binding in self.intent.bindings}
        bound.update(row.fact.fact_id for row in self.native_records if isinstance(row.data, RootObservation) and
                     any(row.data.source == receipt.reference for receipt in self.sources))
        pending = list(bound)
        while pending:
            key = pending.pop()
            for parent in records[key].fact.provenance.derived_from:
                if parent not in bound:
                    bound.add(parent)
                    pending.append(parent)
        if bound != set(records):
            raise ValueError("orphan native fact outside bound comparison/input ancestry")
        return self


class CandidateComparison(WireModel):
    schema_version: Literal["system.candidate-comparison.v1"] = "system.candidate-comparison.v1"
    execution_status: Literal["not_run"] = "not_run"
    before_hash: Sha256
    after_hash: Sha256
    intent_hash: Sha256
    checker_hash: Sha256
    changed_source_ids: tuple[Identifier, ...]
    value_conflicts_before: Annotated[StrictInt, Field(ge=0, le=16)]
    value_conflicts_after: Annotated[StrictInt, Field(ge=0, le=16)]
    outcome: Literal["source_comparison_improved", "no_demonstrated_improvement"]
    limitation: Literal["Source comparison only; no tool rerun, functional repair or physical acceptance."] = "Source comparison only; no tool rerun, functional repair or physical acceptance."
