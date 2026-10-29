"""Pure admission tests. References/results are synthetic, never tool evidence."""
from dataclasses import FrozenInstanceError
import hashlib
import json

import pytest
from pydantic import ValidationError

from veriforge.contracts import ArtifactReference, AttemptResult, canonical_hash
from veriforge.contracts.engineering import EvidenceReference
from veriforge.system_investigation import AddressBinding, RegisterIntent, SourceArtifact, bundle_files, compare_registers
from veriforge.system_investigation.records import (AdmittedInvestigation, ArtifactBytes, CampaignAttemptEvidence,
    EngineeringInvestigation, EngineeringProjectRevision, NativeAttachment, NATIVE_SCHEMA_ALLOWLIST,
    TrustedAdmissionAuthority, admit_investigation, create_admission_authority,
    read_investigation, serialize_investigation)

SECRET_BYTES = b"PRIVATE_SOURCE_MARKER_NOT_METADATA"


def encode(model):
    return json.dumps(model.model_dump(mode="json"), sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()


def ref(name, data, *, revision="review-v1", attempt=None):
    return EvidenceReference(artifact_id=name, sha256=hashlib.sha256(data).hexdigest(),
                             revision_id=revision, attempt_id=attempt)


def source(name, data):
    return SourceArtifact(reference=ref(name, data), data=data, origin="synthetic_fixture")


def fixture(*, include_attempt=False):
    header = source("header", b"// " + SECRET_BYTES + b"\n#define FLAG_ADDR 0x40000000u\n")
    rtl = source("rtl", b"case (addr)\n8'h00: flag <= write_data;\ndefault: ;\nendcase\n")
    intent = RegisterIntent(byte_base=0x40000000, bindings=(AddressBinding(binding_id="flag", header_macro="FLAG_ADDR",
        rtl_signal="flag", expected_address=0x40000000),))
    report = compare_registers(header, rtl, intent)
    bundle = bundle_files(report)
    blobs = []
    def add(reference, data):
        if not any(row.reference == reference for row in blobs):
            blobs.append(ArtifactBytes(reference=reference, data=data))
        return reference
    proof_refs = {}
    for row in report.native_records:
        for proof in row.fact.provenance.sources:
            proof_refs[proof.artifact.artifact_id] = proof.artifact
    add(header.reference, header.data)
    add(rtl.reference, rtl.data)
    add(proof_refs["reviewed-intent"], encode(intent))
    config = b'{"scope":"associated engineering files, not board equals FIFO"}'
    config_ref = add(ref("configuration", config), config)
    revision = EngineeringProjectRevision(project_id="existing-rtl-project", revision_id="engineering-1",
        source_refs=tuple(proof_refs.values()) + (config_ref,), configuration_hash=config_ref.sha256)
    revision_raw = revision.model_dump_json(indent=2).encode()
    revision_ref = add(ref("project-revision", revision_raw), revision_raw)
    manifest_raw = report.manifest.model_dump_json(indent=2).encode()
    manifest_ref = add(ref("graph", manifest_raw), manifest_raw)
    report_raw = report.model_dump_json(indent=2).encode()
    report_ref = add(ref("bridge", report_raw), report_raw)
    attachments = []
    for row in report.native_records:
        add(row.fact.native_record, bundle["native/" + row.fact.native_record.artifact_id + ".json"])
        attachments.append(NativeAttachment(fact_id=row.fact.fact_id, domain=row.fact.domain,
            native_schema_id=row.fact.native_schema_id, reference=row.fact.native_record))
    for relation in report.manifest.relations:
        for reference in relation.evidence:
            add(reference, bundle["comparisons/" + reference.artifact_id + ".json"])
    links = ()
    if include_attempt:
        log = b"fake execution diagnostic only"
        attempt = AttemptResult(attempt_id="fake-attempt", experiment_id="fixed-experiment",
            execution_status="completed", verdict="timeout", artifacts=(ArtifactReference(
                artifact_id="diagnostic", path="log.txt", sha256=hashlib.sha256(log).hexdigest(),
                size_bytes=len(log), media_type="text/plain"),))
        raw = attempt.model_dump_json(indent=2).encode()
        reference = add(ref("attempt-result", raw, revision="attempt-version", attempt="fake-attempt"), raw)
        add(ref("diagnostic", log, revision="attempt-version", attempt="fake-attempt"), log)
        links = (CampaignAttemptEvidence(campaign_id="stored-campaign", db_attempt_id="database-attempt-17",
            experiment_record_id="database-experiment-42", native_attempt_id="fake-attempt",
            wire_experiment_id="fixed-experiment", result_ref=reference),)
    record = EngineeringInvestigation(investigation_id="associated-investigation", project_id=revision.project_id,
        revision_id=revision.revision_id, project_revision_ref=revision_ref, manifest_ref=manifest_ref,
        bridge_report_ref=report_ref, native_records=tuple(attachments), attempts=links)
    authority = authority_for(record)
    return record, tuple(blobs), authority


def authority_for(record):
    return create_admission_authority(project_id=record.project_id, revision_id=record.revision_id,
        project_revision_ref=record.project_revision_ref, manifest_ref=record.manifest_ref,
        bridge_report_ref=record.bridge_report_ref, attempts=record.attempts)


def replace_blob(record, blobs, field, data):
    old = getattr(record, field)
    new = ref(old.artifact_id, data, revision=old.revision_id, attempt=old.attempt_id)
    record = record.model_copy(update={field: new})
    return record, tuple(ArtifactBytes(reference=new, data=data) if row.reference == old else row for row in blobs)


def test_exact_bytes_preserved_safe_envelope_excludes_private_source_and_does_not_execute():
    record, blobs, authority = fixture(include_attempt=True)
    admitted = admit_investigation(record, blobs, authority=authority)
    assert admitted.envelope.operation == "admission_only" and admitted.envelope.new_execution == "not_run"
    assert admitted.attempt_results[0].verdict == "timeout"
    assert admitted.attempt_results[0].attempt_id == "fake-attempt" != record.attempts[0].db_attempt_id
    assert admitted.attempt_results[0].experiment_id == "fixed-experiment" != record.attempts[0].experiment_record_id
    assert admitted.envelope.attempt_context_scope == "host_pinned_association_not_execution_origin"
    assert admitted.bridge_report.comparisons[0].verdict == "inconclusive"
    assert SECRET_BYTES not in serialize_investigation(admitted)
    assert SECRET_BYTES.decode() not in repr(admitted) and SECRET_BYTES.decode() not in repr(blobs)
    assert {row.reference.sha256: row.data for row in admitted.artifacts} == {row.reference.sha256: row.data for row in blobs}
    assert any(SECRET_BYTES in row.data for row in admitted.artifacts)
    decoded = read_investigation(serialize_investigation(admitted), tuple(reversed(blobs)), authority=authority)
    assert decoded.envelope == admitted.envelope
    assert admitted.envelope.record_hash == canonical_hash(record)
    # Noncanonical revision/manifest/report/result raw JSON is retained as-is.
    meta = next(row for row in admitted.envelope.artifacts if "project_revision" in row.roles)
    assert meta.reference.sha256 != meta.canonical_record_hash
    with pytest.raises(FrozenInstanceError):
        admitted.envelope = decoded.envelope


def test_service_factory_guards_do_not_accept_client_objects_or_public_constructors():
    record, blobs, authority = fixture()
    with pytest.raises(TypeError):
        TrustedAdmissionAuthority()
    with pytest.raises(TypeError):
        AdmittedInvestigation()
    with pytest.raises(ValueError, match="authority"):
        admit_investigation(record, blobs, authority={"project_id": record.project_id})
    forged_authority = object.__new__(TrustedAdmissionAuthority)
    with pytest.raises(ValueError, match="authority"):
        admit_investigation(record, blobs, authority=forged_authority)
    forged = object.__new__(AdmittedInvestigation)
    assert isinstance(forged, AdmittedInvestigation)
    with pytest.raises(ValueError, match="unsealed"):
        serialize_investigation(forged)


@pytest.mark.parametrize("field", ["project_id", "revision_id"])
def test_host_project_authority_cannot_be_selected_by_client(field):
    record, blobs, authority = fixture()
    with pytest.raises(ValueError, match="host-pinned"):
        admit_investigation(record.model_copy(update={field: "different"}), blobs, authority=authority)


def test_each_reference_resolves_and_extra_or_duplicate_bytes_reject():
    record, blobs, authority = fixture()
    with pytest.raises(ValueError, match="missing"):
        admit_investigation(record, blobs[1:], authority=authority)
    with pytest.raises(ValueError, match="duplicate"):
        admit_investigation(record, (*blobs, blobs[0]), authority=authority)
    extra = ArtifactBytes(reference=ref("orphan", b"unreferenced"), data=b"unreferenced")
    with pytest.raises(ValueError, match="orphan"):
        admit_investigation(record, (*blobs, extra), authority=authority)
    conflicting = ArtifactBytes(reference=ref(blobs[0].reference.artifact_id, b"different"), data=b"different")
    with pytest.raises(ValueError, match="duplicate or conflicting"):
        admit_investigation(record, (*blobs, conflicting), authority=authority)


def test_model_copy_cannot_bypass_raw_byte_hash_validation():
    record, blobs, authority = fixture()
    forged = blobs[0].model_copy(update={"data": b"changed after original validation"})
    with pytest.raises(ValueError, match="SHA-256"):
        admit_investigation(record, (forged, *blobs[1:]), authority=authority)


@pytest.mark.parametrize("field", ["domain", "native_schema_id", "fact_id"])
def test_native_attachment_domains_schemas_and_order_are_explicit(field):
    record, blobs, authority = fixture()
    value = {"domain": "riscv", "native_schema_id": "dynamic.python", "fact_id": "orphan"}[field]
    changed = record.native_records[0].model_copy(update={field: value})
    with pytest.raises(ValueError):
        admit_investigation(record.model_copy(update={"native_records": (changed, *record.native_records[1:])}), blobs, authority=authority)
    with pytest.raises(ValueError, match="ordered"):
        admit_investigation(record.model_copy(update={"native_records": tuple(reversed(record.native_records))}), blobs, authority=authority)
    assert ("riscv", "riscv.retirement.v1") not in NATIVE_SCHEMA_ALLOWLIST


def test_native_canonical_hash_cannot_be_reinterpreted_as_pretty_raw_hash():
    record, blobs, authority = fixture()
    native_ref = record.native_records[0].reference
    original = next(row for row in blobs if row.reference == native_ref)
    pretty = json.dumps(json.loads(original.data), indent=2).encode()
    assert hashlib.sha256(pretty).hexdigest() != native_ref.sha256
    with pytest.raises(ValidationError, match="SHA-256"):
        ArtifactBytes(reference=native_ref, data=pretty)


def test_host_pinned_invalid_bridge_still_revalidates_relationship_coverage():
    record, blobs, _ = fixture()
    graph = json.loads(next(row.data for row in blobs if row.reference == record.manifest_ref))
    report = json.loads(next(row.data for row in blobs if row.reference == record.bridge_report_ref))
    graph["relations"] = []
    report["manifest"] = graph
    record, blobs = replace_blob(record, blobs, "manifest_ref", json.dumps(graph).encode())
    record, blobs = replace_blob(record, blobs, "bridge_report_ref", json.dumps(report).encode())
    with pytest.raises(ValueError, match="typed record consistency"):
        admit_investigation(record, blobs, authority=authority_for(record))


def test_project_snapshot_must_include_actual_native_provenance_sources():
    record, blobs, _ = fixture()
    raw = json.loads(next(row.data for row in blobs if row.reference == record.project_revision_ref))
    raw["source_refs"] = raw["source_refs"][1:]
    record, blobs = replace_blob(record, blobs, "project_revision_ref", json.dumps(raw).encode())
    with pytest.raises(ValueError, match="outside associated"):
        admit_investigation(record, blobs, authority=authority_for(record))


@pytest.mark.parametrize("field", ["campaign_id", "db_attempt_id", "experiment_record_id", "native_attempt_id", "wire_experiment_id"])
def test_existing_attempt_binding_is_host_pinned(field):
    record, blobs, authority = fixture(include_attempt=True)
    changed = record.attempts[0].model_copy(update={field: "unrelated"})
    with pytest.raises(ValueError):
        admit_investigation(record.model_copy(update={"attempts": (changed,)}), blobs, authority=authority)


def test_pinned_attempt_result_is_parsed_and_bound_not_accepted_by_reference_alone():
    record, blobs, _ = fixture(include_attempt=True)
    link = record.attempts[0]
    body = json.loads(next(row.data for row in blobs if row.reference == link.result_ref))
    body["experiment_id"] = "different-experiment"
    raw = json.dumps(body).encode()
    new_ref = ref(link.result_ref.artifact_id, raw, revision=link.result_ref.revision_id, attempt=link.native_attempt_id)
    updated = link.model_copy(update={"result_ref": new_ref})
    record = record.model_copy(update={"attempts": (updated,)})
    blobs = tuple(ArtifactBytes(reference=new_ref, data=raw) if row.reference == link.result_ref else row for row in blobs)
    with pytest.raises(ValueError, match="attempt/experiment"):
        admit_investigation(record, blobs, authority=authority_for(record))


def test_artifact_byte_count_and_total_bounds_fail_before_domain_decode():
    record, blobs, authority = fixture()
    with pytest.raises(ValueError, match="bounded exact artifact tuple"):
        admit_investigation(record, (blobs[0],) * 257, authority=authority)
    with pytest.raises(ValidationError):
        ArtifactBytes(reference=ref("huge", b"x" * 1048577), data=b"x" * 1048577)
    raw = b"x" * 1048576
    large = tuple(ArtifactBytes(reference=ref("large-" + str(i), raw), data=raw) for i in range(17))
    with pytest.raises(ValueError, match="total artifact byte limit"):
        admit_investigation(record, large, authority=authority)


def test_duplicate_json_keys_are_rejected_even_when_exact_bytes_host_pinned():
    record, blobs, _ = fixture()
    original = next(row.data for row in blobs if row.reference == record.project_revision_ref)
    raw = original.replace(b'{', b'{"project_id":"shadowed",', 1)
    record, blobs = replace_blob(record, blobs, "project_revision_ref", raw)
    with pytest.raises(ValueError, match="duplicate JSON key"):
        admit_investigation(record, blobs, authority=authority_for(record))


def test_read_revalidates_raw_dependencies_and_safe_metadata_not_receipt_type():
    record, blobs, authority = fixture()
    admitted = admit_investigation(record, blobs, authority=authority)
    wire = serialize_investigation(admitted)
    altered = json.loads(wire)
    altered["record_hash"] = "f" * 64
    with pytest.raises(ValueError, match="serialized admission metadata"):
        read_investigation(json.dumps(altered).encode(), blobs, authority=authority)
    altered = json.loads(wire)
    altered["overall_design_verdict"] = "pass"
    with pytest.raises(ValueError):
        read_investigation(json.dumps(altered).encode(), blobs, authority=authority)
    with pytest.raises(ValueError, match="missing"):
        read_investigation(wire, blobs[1:], authority=authority)


def test_zero_byte_attempt_log_is_preserved_not_invented():
    record, blobs, _ = fixture(include_attempt=True)
    link = record.attempts[0]
    body = json.loads(next(row.data for row in blobs if row.reference == link.result_ref))
    old_log = next(row for row in blobs if row.reference.artifact_id == "diagnostic")
    body["artifacts"][0]["sha256"] = hashlib.sha256(b"").hexdigest()
    body["artifacts"][0]["size_bytes"] = 0
    raw = json.dumps(body).encode()
    result_ref = ref(link.result_ref.artifact_id, raw, revision=link.result_ref.revision_id, attempt=link.native_attempt_id)
    record = record.model_copy(update={"attempts": (link.model_copy(update={"result_ref": result_ref}),)})
    replacement_log = ArtifactBytes(reference=ref("diagnostic", b"", revision=link.result_ref.revision_id,
                                                attempt=link.native_attempt_id), data=b"")
    blobs = tuple(ArtifactBytes(reference=result_ref, data=raw) if row.reference == link.result_ref else
                  replacement_log if row.reference == old_log.reference else row for row in blobs)
    admitted = admit_investigation(record, blobs, authority=authority_for(record))
    assert admitted.attempt_results[0].artifacts[0].size_bytes == 0
    assert any(row.data == b"" for row in admitted.artifacts)


def test_configuration_hash_requires_actual_retained_source_bytes():
    record, blobs, _ = fixture()
    data = json.loads(next(row.data for row in blobs if row.reference == record.project_revision_ref))
    data["configuration_hash"] = "f" * 64
    record, blobs = replace_blob(record, blobs, "project_revision_ref", json.dumps(data).encode())
    with pytest.raises(ValueError, match="configuration hash"):
        admit_investigation(record, blobs, authority=authority_for(record))


def test_invalid_native_private_data_not_reflected_in_error_message():
    record, blobs, _ = fixture()
    data = json.loads(next(row.data for row in blobs if row.reference == record.bridge_report_ref))
    data["schema_version"] = SECRET_BYTES.decode()
    record, blobs = replace_blob(record, blobs, "bridge_report_ref", json.dumps(data).encode())
    with pytest.raises(ValueError) as failed:
        admit_investigation(record, blobs, authority=authority_for(record))
    assert SECRET_BYTES.decode() not in str(failed.value)
    assert str(failed.value) == "typed record consistency validation failed"


def test_duplicate_native_attempt_artifacts_do_not_become_two_identities():
    record, blobs, _ = fixture(include_attempt=True)
    link = record.attempts[0]
    data = json.loads(next(row.data for row in blobs if row.reference == link.result_ref))
    data["artifacts"].append(data["artifacts"][0])
    raw = json.dumps(data).encode()
    result_ref = ref(link.result_ref.artifact_id, raw, revision=link.result_ref.revision_id, attempt=link.native_attempt_id)
    record = record.model_copy(update={"attempts": (link.model_copy(update={"result_ref": result_ref}),)})
    blobs = tuple(ArtifactBytes(reference=result_ref, data=raw) if row.reference == link.result_ref else row for row in blobs)
    with pytest.raises(ValueError, match="duplicate stored attempt artifact"):
        admit_investigation(record, blobs, authority=authority_for(record))


@pytest.mark.parametrize("index,delta", [(0, 1), (1, -1)])
def test_each_rehashed_source_receipt_size_is_bound_to_actual_retained_buffer(index, delta):
    record, blobs, _ = fixture()
    data = json.loads(next(row.data for row in blobs if row.reference == record.bridge_report_ref))
    data["sources"][index]["size_bytes"] += delta
    record, blobs = replace_blob(record, blobs, "bridge_report_ref", json.dumps(data).encode())
    with pytest.raises(ValueError, match="source receipt size"):
        admit_investigation(record, blobs, authority=authority_for(record))
