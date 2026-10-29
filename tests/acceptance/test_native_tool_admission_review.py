"""Independent negative admission checks, pure bytes and synthetic seed closure.

The trusted owner's synthetic fixture arranger supplies complete initial closure
only. Rehashed metadata attacks and checker fault injections below are independent
of that arranger. No optional real bundles/tests, copied source, tool or DB run.
"""
from copy import deepcopy
from dataclasses import FrozenInstanceError
from hashlib import sha256
import importlib.util
import json
from pathlib import Path

import pytest

from veriforge.contracts import AttemptResult, canonical_hash
from veriforge.contracts.engineering import EvidenceReference, SystemManifest
from veriforge.system_investigation import records as dispatch
from veriforge.system_investigation import native_tool_records as native


def encode(value):
    if hasattr(value, "model_dump"):
        value = value.model_dump(mode="json")
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False).encode()


def key(ref):
    return ref.artifact_id, ref.revision_id, ref.attempt_id


@pytest.fixture(scope="module")
def arranger():
    # Fixed trusted repository test helper, never an artifact-selected module.
    # Import defines its test functions but does not run optional evidence tests.
    path = Path(__file__).resolve().parents[1] / "system_investigation/test_native_tool_records.py"
    spec = importlib.util.spec_from_file_location("trusted_native_review_fixture", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture(params=["riscv", "firmware"])
def seed(request, arranger, tmp_path):
    kind = request.param
    raw = arranger.synthetic_riscv() if kind == "riscv" else arranger.synthetic_firmware(tmp_path)
    return arranger.bundle(raw, kind)


def admit(seed):
    record, artifacts, authority = seed
    return dispatch.admit_investigation(record, artifacts, authority=authority)


def pins(record, authority, **changes):
    values = authority.pins.model_dump(mode="python")
    values.update(project_id=record.project_id, revision_id=record.revision_id,
        project_revision_ref=record.project_revision_ref, manifest_ref=record.manifest_ref,
        report_kind=record.report_kind, report_ref=record.report_ref)
    values.update(changes)
    return native.create_external_admission_authority(**values)


def rehash_metadata(seed, mutate):
    """Forge adapter observations without changing a single producer byte.

    Update every case-wrapper hash, manifest, report selection and host pin so
    rejection must come from raw evidence revalidation, not stale metadata SHA.
    """
    record, artifacts, authority = seed
    blobs = {key(a.reference): a for a in artifacts}
    report = json.loads(blobs[key(record.report_ref)].data)
    manifest = json.loads(blobs[key(record.manifest_ref)].data)
    mutate(report)

    def replace(old_ref, raw):
        ref = EvidenceReference(**{**old_ref.model_dump(mode="json"), "sha256": sha256(raw).hexdigest()})
        blobs[key(old_ref)] = dispatch.ArtifactBytes(reference=ref, data=raw)
        return ref

    attachments = []
    for attachment, fact, case in zip(record.native_records, manifest["facts"], report["cases"], strict=True):
        ref = replace(attachment.reference, encode(case))
        attachments.append(attachment.model_copy(update={"reference": ref}))
        fact.update(native_record=ref.model_dump(mode="json"), native_record_hash=ref.sha256)
    manifest_raw = encode(SystemManifest.model_validate(manifest))
    manifest_ref = replace(record.manifest_ref, manifest_raw)
    report["manifest_ref"] = manifest_ref.model_dump(mode="json")
    for binding in report["selection"]["selected"]:
        if binding["ref"]["artifact_id"] == record.manifest_ref.artifact_id:
            binding.update(ref=manifest_ref.model_dump(mode="json"), size_bytes=len(manifest_raw))
    report_model = native._REPORT.validate_python(report)
    report_ref = replace(record.report_ref, encode(report_model))
    updated = record.model_copy(update={"manifest_ref": manifest_ref, "report_ref": report_ref,
                                       "native_records": tuple(attachments)})
    return updated, tuple(blobs.values()), pins(updated, authority)


def test_complete_readmission_preserves_exact_bytes_and_external_scope(seed):
    record, artifacts, authority = seed
    admitted = admit(seed)
    wire = dispatch.serialize_investigation(admitted)
    assert dispatch.read_investigation(wire, artifacts, authority=authority).envelope == admitted.envelope
    assert type(dispatch.decode_admission_envelope(wire)) is native.AdmissionEnvelopeV2
    assert admitted.envelope.new_execution == "not_run"
    assert admitted.envelope.validation.implementation_hash == authority.pins.validator_implementation_hash
    assert not hasattr(admitted, "attempt_results") and "attempts" not in record.model_dump()
    assert len(admitted.artifacts) <= 256 and len(wire) <= 262144
    assert {(key(x.reference), x.data) for x in admitted.artifacts} == {(key(x.reference), x.data) for x in artifacts}
    if record.report_kind.startswith("riscv"):
        result_blob = next(a for a in artifacts if a.reference == admitted.native_records[0].locator.raw_result_ref)
        assert b"\n  " in result_blob.data
        assert result_blob.reference.sha256 != canonical_hash(AttemptResult.model_validate_json(result_blob.data))
        assert admitted.native_report.recompiled_control.kind == "recompiled_control"
    else:
        assert all(c.locator.execution_ref.attempt_id is None for c in admitted.native_records)
        assert {r.kind for r in admitted.native_report.replays} == {"saved_program_replay"}


def test_fully_rehashed_declared_counts_cannot_replace_native_recomputation(seed):
    forged = rehash_metadata(seed, lambda report: report["cases"][0].update(cycles=report["cases"][0]["cycles"] + 1))
    with pytest.raises(ValueError, match="registered checker revalidation"):
        admit(forged)


def test_registered_independent_checker_is_called_and_its_changed_result_rejects(seed, monkeypatch):
    calls = []
    if seed[0].report_kind.startswith("riscv"):
        from veriforge.riscv import checker
        original = checker.compare_retirements

        def fault(*args, **kwargs):
            calls.append(True)
            return original(*args, **kwargs).model_copy(update={"reason": "INDEPENDENT_FAULT_INJECTION"})

        monkeypatch.setattr(checker, "compare_retirements", fault)
    else:
        from veriforge.firmware import harness
        original = harness.check_observation

        def fault(*args, **kwargs):
            calls.append(True)
            return original(*args, **kwargs).model_copy(update={"reason": "INDEPENDENT_FAULT_INJECTION"})

        monkeypatch.setattr(harness, "check_observation", fault)
    with pytest.raises(ValueError, match="registered checker revalidation"):
        admit(seed)
    assert calls


@pytest.mark.parametrize("change", ["project_id", "revision_id", "wrong_authority_hash", "foreign_authority", "no_seal"])
def test_host_authority_and_validator_pin_cannot_be_replaced(seed, change):
    record, artifacts, authority = seed
    if change in {"project_id", "revision_id"}:
        record = record.model_copy(update={change: "different-host-binding"})
    elif change == "wrong_authority_hash":
        authority = pins(record, authority, validator_implementation_hash="f" * 64)
    elif change == "foreign_authority":
        authority = authority.pins.model_dump(mode="json")
    else:
        authority = object.__new__(native.TrustedExternalAdmissionAuthority)
    with pytest.raises(ValueError):
        dispatch.admit_investigation(record, artifacts, authority=authority)


@pytest.mark.parametrize("damage", ["missing", "duplicate", "wrong_bytes", "list_not_tuple"])
def test_complete_typed_blob_closure_rejects_missing_duplicate_or_changed_bytes(seed, damage):
    record, artifacts, authority = seed
    if damage == "missing":
        artifacts = artifacts[:-1]
    elif damage == "duplicate":
        artifacts = artifacts + (artifacts[0],)
    elif damage == "list_not_tuple":
        artifacts = list(artifacts)
    else:
        forged = artifacts[0].model_copy(update={"data": b"DIFFERENT_BYTES"})
        artifacts = (forged,) + artifacts[1:]
    with pytest.raises(ValueError):
        dispatch.admit_investigation(record, artifacts, authority=authority)


def test_duplicate_report_keys_and_unknown_envelope_version_fail_closed(seed):
    record, artifacts, authority = seed
    blob = next(a for a in artifacts if a.reference == record.report_ref)
    raw = b'{"schema_version":' + json.dumps(record.report_kind).encode() + b',' + blob.data[1:]
    ref = blob.reference.model_copy(update={"sha256": sha256(raw).hexdigest()})
    changed = record.model_copy(update={"report_ref": ref})
    artifacts = tuple(dispatch.ArtifactBytes(reference=ref, data=raw) if a is blob else a for a in artifacts)
    with pytest.raises(ValueError):
        dispatch.admit_investigation(changed, artifacts, authority=pins(changed, authority))
    with pytest.raises(ValueError):
        dispatch.decode_admission_envelope(b'{"schema_version":"engineering.admission.v2","schema_version":"engineering.admission.v1"}')
    with pytest.raises(ValueError):
        dispatch.decode_admission_envelope(b'{"schema_version":"engineering.admission.v99"}')


def test_sealed_constructors_serializer_and_envelope_replay_hash_cannot_be_forged(seed):
    record, artifacts, authority = seed
    for cls in (native.TrustedExternalAdmissionAuthority, native.AdmittedNativeInvestigation):
        with pytest.raises(TypeError):
            cls()
    admitted = admit(seed)
    with pytest.raises((FrozenInstanceError, AttributeError)):
        admitted.envelope = None
    with pytest.raises(ValueError):
        dispatch.serialize_investigation(object.__new__(native.AdmittedNativeInvestigation))
    parsed = json.loads(dispatch.serialize_investigation(admitted))
    parsed["validation"]["implementation_hash"] = "f" * 64
    with pytest.raises(ValueError):
        dispatch.read_investigation(encode(parsed), artifacts, authority=authority)


def test_no_local_execution_fields_can_be_inserted_into_v2_record(seed):
    data = seed[0].model_dump(mode="json")
    for field in ("attempts", "campaign_id", "db_attempt_id", "experiment_record_id"):
        changed = {**data, field: [] if field == "attempts" else "local-id"}
        with pytest.raises(ValueError):
            native.EngineeringInvestigationV2.model_validate(changed)


def test_registered_source_version_pin_rejects_weaker_copied_source():
    names = getattr(native, "_REGISTERED_SOURCES", {})
    assert names, "fixed native source pins required"
    name = next(iter(names))

    def path(path, *args, **kwargs):
        return encode({name: "0" * 64}) if path == "source-manifest.json" else b"weaker copied source, never executed"

    with pytest.raises(ValueError, match="unsupported native source/configuration"):
        native._source_closure(path)
