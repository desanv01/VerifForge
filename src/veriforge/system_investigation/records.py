"""Pure hash-bound admission, never persistence, dispatch or correctness authority.

Only trusted service code constructs the host authority. Public JSON cannot
construct authority or an admitted receipt. Authentication/transactional storage
remain the caller's responsibility; arbitrary Python is not a client capability.
"""
from dataclasses import dataclass, field
import hashlib
import json
from types import MappingProxyType
from typing import Annotated, Literal

from pydantic import Field, StrictBytes, StrictInt, ValidationError, model_validator

from veriforge.contracts import AttemptResult, Identifier, Sha256, WireModel, canonical_hash
from veriforge.contracts.engineering import Domain, EvidenceReference, SystemManifest
from veriforge.firmware.contracts import AddressFact
from .models import (BridgeReport, ComparisonRow, ConnectorObservation, IntentObservation,
                     PinObservation, RootObservation)

MAX_ARTIFACT_BYTES = 1_048_576
MAX_TOTAL_BYTES = 16 * MAX_ARTIFACT_BYTES
MAX_ARTIFACTS = 256
MAX_ENVELOPE_BYTES = 262_144
NativeData = AddressFact | PinObservation | ConnectorObservation | RootObservation | IntentObservation
Role = Literal["project_revision", "manifest", "bridge_report", "project_source", "native_fact",
               "provenance_source", "relation_evidence", "capability_evidence", "attempt_result", "attempt_artifact"]
_CODECS = MappingProxyType({
    ("firmware", "AddressFact.v1"): AddressFact, ("rtl", "AddressFact.v1"): AddressFact,
    ("vivado", "PinObservation.v1"): PinObservation,
    ("kicad", "ConnectorObservation.v1"): ConnectorObservation,
    ("system", "RootObservation.v1"): RootObservation,
    ("system", "IntentObservation.v1"): IntentObservation,
})
NATIVE_SCHEMA_ALLOWLIST = frozenset(_CODECS)


def _key(reference):
    return reference.artifact_id, reference.revision_id, reference.attempt_id


def _encode(model):
    return json.dumps(model.model_dump(mode="json"), sort_keys=True, separators=(",", ":"),
                      ensure_ascii=False, allow_nan=False).encode("utf-8")


def _decode(data, model, limit=MAX_ARTIFACT_BYTES):
    if type(data) is not bytes or len(data) > limit:
        raise ValueError("bounded exact JSON bytes required")
    def unique(pairs):
        values = {}
        for key, value in pairs:
            if key in values:
                raise ValueError("duplicate JSON key")
            values[key] = value
        return values
    def invalid_constant(_):
        raise ValueError("nonfinite JSON constant")
    try:
        parsed = json.loads(data, object_pairs_hook=unique, parse_constant=invalid_constant)
        return model.model_validate(parsed)
    except (UnicodeError, RecursionError, json.JSONDecodeError):
        raise ValueError("malformed bounded JSON record") from None
    except (ValidationError, AttributeError, IndexError, KeyError, TypeError):
        # Invalid private native fields must not become public error/log text.
        raise ValueError("typed record consistency validation failed") from None


def _reference(reference):
    if not isinstance(reference, EvidenceReference):
        raise ValueError("typed immutable evidence reference required")
    return _decode(_encode(reference), EvidenceReference)


class EngineeringProjectRevision(WireModel):
    """Associated source snapshot, not a claim these domains are the same DUT.

    configuration_hash identifies one retained source artifact's raw bytes. Its
    contents are never interpreted as runtime configuration or tool commands.
    """
    schema_version: Literal["engineering.project-revision.v1"] = "engineering.project-revision.v1"
    project_id: Identifier
    revision_id: Identifier
    source_refs: Annotated[tuple[EvidenceReference, ...], Field(min_length=1, max_length=64)]
    configuration_hash: Sha256

    @model_validator(mode="after")
    def unique_sources(self):
        if len({_key(ref) for ref in self.source_refs}) != len(self.source_refs):
            raise ValueError("duplicate project source identity")
        return self


class NativeAttachment(WireModel):
    fact_id: Identifier
    domain: Domain
    native_schema_id: Identifier
    reference: EvidenceReference


class CampaignAttemptEvidence(WireModel):
    """Host-pinned context, with DB and external native namespaces kept separate.

    Persistence must independently resolve DB campaign/project ownership, the DB
    attempt and immutable experiment record, wire plan ID, stored result value/
    identity and original native-byte provenance before claiming local origin.
    This pure record cannot establish those database/execution facts.
    """
    campaign_id: Identifier
    db_attempt_id: Identifier
    experiment_record_id: Identifier
    native_attempt_id: Identifier
    wire_experiment_id: Identifier
    result_ref: EvidenceReference

    @model_validator(mode="after")
    def scoped_result(self):
        if self.result_ref.attempt_id != self.native_attempt_id:
            raise ValueError("result evidence must be scoped to its actual attempt")
        return self


class EngineeringInvestigation(WireModel):
    schema_version: Literal["engineering.investigation.v1"] = "engineering.investigation.v1"
    investigation_id: Identifier
    project_id: Identifier
    revision_id: Identifier
    project_revision_ref: EvidenceReference
    manifest_ref: EvidenceReference
    bridge_report_ref: EvidenceReference
    native_records: Annotated[tuple[NativeAttachment, ...], Field(min_length=1, max_length=128)]
    attempts: Annotated[tuple[CampaignAttemptEvidence, ...], Field(max_length=32)] = ()

    @model_validator(mode="after")
    def unique_native_and_attempts(self):
        if len({row.fact_id for row in self.native_records}) != len(self.native_records):
            raise ValueError("duplicate native fact attachment")
        if len({row.db_attempt_id for row in self.attempts}) != len(self.attempts):
            raise ValueError("duplicate attempt binding")
        return self


class ArtifactBytes(WireModel):
    reference: EvidenceReference
    data: Annotated[StrictBytes, Field(max_length=MAX_ARTIFACT_BYTES, repr=False, exclude=True)]

    @model_validator(mode="after")
    def exact_hash(self):
        if hashlib.sha256(self.data).hexdigest() != self.reference.sha256:
            raise ValueError("artifact bytes differ from immutable SHA-256")
        return self


class VerifiedArtifact(WireModel):
    reference: EvidenceReference
    size_bytes: Annotated[StrictInt, Field(ge=0, le=MAX_ARTIFACT_BYTES)]
    roles: Annotated[tuple[Role, ...], Field(min_length=1, max_length=10)]
    canonical_record_hash: Sha256 | None = None


class AdmissionEnvelope(WireModel):
    schema_version: Literal["engineering.admission.v1"] = "engineering.admission.v1"
    operation: Literal["admission_only"] = "admission_only"
    new_execution: Literal["not_run"] = "not_run"
    claim_scope: Literal["resolved_bytes_and_typed_consistency_only"] = "resolved_bytes_and_typed_consistency_only"
    attempt_context_scope: Literal["host_pinned_association_not_execution_origin"] = "host_pinned_association_not_execution_origin"
    record: EngineeringInvestigation
    record_hash: Sha256
    artifacts: Annotated[tuple[VerifiedArtifact, ...], Field(max_length=MAX_ARTIFACTS)]
    total_artifact_bytes: Annotated[StrictInt, Field(ge=0, le=MAX_TOTAL_BYTES)]


_AUTHORITY_SEAL, _ADMISSION_SEAL = object(), object()


@dataclass(frozen=True, init=False)
class TrustedAdmissionAuthority:
    project_id: str
    revision_id: str
    project_revision_ref: EvidenceReference
    manifest_ref: EvidenceReference
    bridge_report_ref: EvidenceReference
    attempts: tuple[CampaignAttemptEvidence, ...]
    _seal: object = field(repr=False)

    def __new__(cls, *args, **kwargs):
        raise TypeError("authority requires trusted service factory; no client deserialization")


def create_admission_authority(*, project_id, revision_id, project_revision_ref, manifest_ref,
                               bridge_report_ref, attempts=()):
    """Trusted service code only: pin already-authorized stored references.

    Never call this factory with public request hashes/claims as authority. No
    wire deserializer, registry or proof of review is supplied by this function.
    """
    probe = EngineeringProjectRevision(project_id=project_id, revision_id=revision_id,
        source_refs=(_reference(project_revision_ref),), configuration_hash="0" * 64)
    if type(attempts) is not tuple or len(attempts) > 32:
        raise ValueError("bounded trusted attempt tuple required")
    if any(type(row) is not CampaignAttemptEvidence for row in attempts):
        raise ValueError("typed trusted attempt bindings required")
    checked_attempts = tuple(_decode(_encode(row), CampaignAttemptEvidence) for row in attempts)
    if len({row.db_attempt_id for row in checked_attempts}) != len(checked_attempts):
        raise ValueError("duplicate trusted attempt binding")
    value = object.__new__(TrustedAdmissionAuthority)
    for name, content in dict(project_id=probe.project_id, revision_id=probe.revision_id,
        project_revision_ref=_reference(project_revision_ref), manifest_ref=_reference(manifest_ref),
        bridge_report_ref=_reference(bridge_report_ref), attempts=checked_attempts, _seal=_AUTHORITY_SEAL).items():
        object.__setattr__(value, name, content)
    return value


@dataclass(frozen=True, init=False)
class AdmittedInvestigation:
    envelope: AdmissionEnvelope
    project_revision: EngineeringProjectRevision
    manifest: SystemManifest
    bridge_report: BridgeReport = field(repr=False)
    native_records: tuple[NativeData, ...] = field(repr=False)
    attempt_results: tuple[AttemptResult, ...] = field(repr=False)
    artifacts: tuple[ArtifactBytes, ...] = field(repr=False)
    _seal: object = field(repr=False)

    def __new__(cls, *args, **kwargs):
        raise TypeError("admitted records require complete admission; no public constructor")


def admit_investigation(record, artifacts, *, authority):
    """Resolve every declared dependency and preserve exact admitted bytes.

    No filesystem, DB, process, network or execution action is performed. All
    hashes are consistency identities; authenticity comes from host authority.
    """
    if type(authority) is not TrustedAdmissionAuthority or getattr(authority, "_seal", None) is not _AUTHORITY_SEAL:
        raise ValueError("trusted service admission authority required")
    if not isinstance(record, EngineeringInvestigation):
        raise ValueError("typed investigation record required")
    record = _decode(_encode(record), EngineeringInvestigation, MAX_ENVELOPE_BYTES)
    if (record.project_id, record.revision_id, record.project_revision_ref, record.manifest_ref, record.bridge_report_ref) != (
        authority.project_id, authority.revision_id, authority.project_revision_ref, authority.manifest_ref, authority.bridge_report_ref):
        raise ValueError("investigation differs from host-pinned project or evidence authority")
    if type(artifacts) is not tuple or not 1 <= len(artifacts) <= MAX_ARTIFACTS:
        raise ValueError("bounded exact artifact tuple required")
    if any(not isinstance(row, ArtifactBytes) or type(row.data) is not bytes for row in artifacts):
        raise ValueError("typed exact artifact bytes required")
    if sum(len(row.data) for row in artifacts) > MAX_TOTAL_BYTES:
        raise ValueError("total artifact byte limit exceeded")
    supplied = {}
    for row in artifacts:
        checked = ArtifactBytes(reference=_reference(row.reference), data=row.data)
        key = _key(checked.reference)
        if key in supplied:
            raise ValueError("duplicate or conflicting immutable artifact identity")
        supplied[key] = checked
    roles, canonical = {}, {}

    def resolve(reference, role, model=None, *, native_hash=None):
        reference = _reference(reference)
        key = _key(reference)
        if key not in supplied or supplied[key].reference != reference:
            raise ValueError("missing or conflicting evidence reference")
        roles.setdefault(key, set()).add(role)
        item = supplied[key]
        if model is None:
            return item.data
        decoded = _decode(item.data, model)
        digest = canonical_hash(decoded)
        if key in canonical and canonical[key] != digest:
            raise ValueError("conflicting typed interpretations of one artifact")
        canonical[key] = digest
        if native_hash is not None and (digest != native_hash or item.data != _encode(decoded)):
            raise ValueError("native canonical hash/bytes conflict with raw artifact identity")
        return decoded

    revision = resolve(record.project_revision_ref, "project_revision", EngineeringProjectRevision)
    if (revision.project_id, revision.revision_id) != (record.project_id, record.revision_id):
        raise ValueError("project revision association differs")
    project_sources = {_key(ref): ref for ref in revision.source_refs}
    if revision.configuration_hash not in {ref.sha256 for ref in revision.source_refs}:
        raise ValueError("project configuration hash has no retained source artifact")
    for ref in revision.source_refs:
        resolve(ref, "project_source")
    manifest = resolve(record.manifest_ref, "manifest", SystemManifest)
    bridge = resolve(record.bridge_report_ref, "bridge_report", BridgeReport)
    if bridge.manifest != manifest:
        raise ValueError("approved manifest differs from authoritative native bridge graph")
    for source in bridge.sources:
        raw = resolve(source.reference, "provenance_source")
        if len(raw) != source.size_bytes:
            raise ValueError("bridge source receipt size differs from retained exact bytes")
    if tuple(row.fact_id for row in record.native_records) != tuple(fact.fact_id for fact in manifest.facts):
        raise ValueError("complete ordered native fact attachment coverage required")
    bridge_records = {row.fact.fact_id: row for row in bridge.native_records}
    decoded_native = []
    for attachment, fact in zip(record.native_records, manifest.facts):
        if (attachment.domain, attachment.native_schema_id, attachment.reference) != (fact.domain, fact.native_schema_id, fact.native_record):
            raise ValueError("native attachment domain/schema/reference differs from manifest")
        codec = _CODECS.get((attachment.domain, attachment.native_schema_id))
        if codec is None:
            raise ValueError("native domain/schema is not explicitly registered")
        native = resolve(attachment.reference, "native_fact", codec, native_hash=fact.native_record_hash)
        if native != bridge_records[fact.fact_id].data:
            raise ValueError("native authoritative payload differs from bound bridge record")
        decoded_native.append(native)
        for proof in fact.provenance.sources:
            ref = proof.artifact
            if project_sources.get(_key(ref)) != ref:
                raise ValueError("native provenance is outside associated project revision sources")
            resolve(ref, "provenance_source")
        if isinstance(native, IntentObservation):
            # Requirement context must retain the exact whole intent bytes too.
            proof = fact.provenance.sources[0].artifact
            if resolve(proof, "provenance_source") != _encode(bridge.intent):
                raise ValueError("native requirement provenance differs from typed intent bytes")
    comparisons = {"relation-" + row.binding_id: row for row in bridge.comparisons}
    for relation in manifest.relations:
        comparison = comparisons[relation.relation_id]
        for ref in relation.evidence:
            observed = resolve(ref, "relation_evidence", ComparisonRow, native_hash=canonical_hash(comparison))
            if observed != comparison:
                raise ValueError("relationship evidence differs from native comparison")
    for adapter in manifest.adapters:
        for support in adapter.support:
            for ref in support.evidence:
                resolve(ref, "capability_evidence")
    allowed = {row.db_attempt_id: row for row in authority.attempts}
    attempt_results = []
    for link in record.attempts:
        if allowed.get(link.db_attempt_id) != link:
            raise ValueError("campaign/attempt evidence binding is not host authorized")
        result = resolve(link.result_ref, "attempt_result", AttemptResult)
        if (result.attempt_id, result.experiment_id) != (link.native_attempt_id, link.wire_experiment_id):
            raise ValueError("stored native attempt/experiment identity differs")
        if len({artifact.artifact_id for artifact in result.artifacts}) != len(result.artifacts):
            raise ValueError("duplicate stored attempt artifact identity")
        for artifact in result.artifacts:
            ref = EvidenceReference(artifact_id=artifact.artifact_id, sha256=artifact.sha256,
                revision_id=link.result_ref.revision_id, attempt_id=link.native_attempt_id)
            raw = resolve(ref, "attempt_artifact")
            if len(raw) != artifact.size_bytes:
                raise ValueError("stored attempt artifact size differs")
        attempt_results.append(result)
    # A foreign attempt cannot be smuggled in through a provenance/capability ref.
    admitted_attempts = {row.native_attempt_id for row in record.attempts}
    for key in roles:
        if key[2] is not None and key[2] not in admitted_attempts:
            raise ValueError("attempt-scoped artifact has no authorized associated attempt")
    if set(roles) != set(supplied):
        raise ValueError("unreferenced/orphan artifact bytes in admission bundle")
    ordered = sorted(supplied, key=lambda key: (key[0], key[1], key[2] or ""))
    verified = tuple(VerifiedArtifact(reference=supplied[key].reference, size_bytes=len(supplied[key].data),
        roles=tuple(sorted(roles[key])), canonical_record_hash=canonical.get(key)) for key in ordered)
    envelope = AdmissionEnvelope(record=record, record_hash=canonical_hash(record), artifacts=verified,
                                  total_artifact_bytes=sum(row.size_bytes for row in verified))
    if len(_encode(envelope)) > MAX_ENVELOPE_BYTES:
        raise ValueError("safe admission envelope byte limit exceeded")
    value = object.__new__(AdmittedInvestigation)
    for name, content in dict(envelope=envelope, project_revision=revision, manifest=manifest, bridge_report=bridge,
        native_records=tuple(decoded_native), attempt_results=tuple(attempt_results),
        artifacts=tuple(supplied[key] for key in ordered), _seal=_ADMISSION_SEAL).items():
        object.__setattr__(value, name, content)
    return value


def serialize_investigation(admitted):
    """Safe metadata only. Transactional persistence stores artifacts separately."""
    if type(admitted) is not AdmittedInvestigation or getattr(admitted, "_seal", None) is not _ADMISSION_SEAL:
        raise ValueError("unsealed/unadmitted investigation")
    encoded = _encode(admitted.envelope)
    if len(encoded) > MAX_ENVELOPE_BYTES:
        raise ValueError("safe admission envelope byte limit exceeded")
    return encoded


def read_investigation(envelope_bytes, artifacts, *, authority):
    """Always re-admit exact dependencies, never trust a persisted receipt alone."""
    envelope = _decode(envelope_bytes, AdmissionEnvelope, MAX_ENVELOPE_BYTES)
    admitted = admit_investigation(envelope.record, artifacts, authority=authority)
    if admitted.envelope != envelope:
        raise ValueError("serialized admission metadata differs from fully resolved bytes")
    return admitted


__all__ = ["EngineeringProjectRevision", "NativeAttachment", "CampaignAttemptEvidence", "EngineeringInvestigation",
           "ArtifactBytes", "VerifiedArtifact", "AdmissionEnvelope", "TrustedAdmissionAuthority", "AdmittedInvestigation",
           "NATIVE_SCHEMA_ALLOWLIST", "create_admission_authority", "admit_investigation", "serialize_investigation",
           "read_investigation"]
