"""Prepare one reviewed recorded case in memory; no database, execution or writes.

Run with --help for the operator-supplied local paths. Paths select byte storage,
not authority: the reviewed case pins every external input and report hash.
The returned sealed admission is consistency evidence, never a new tool result.
"""
from __future__ import annotations

import argparse
from dataclasses import dataclass
import hashlib
import json
from pathlib import Path
import re
import stat

from pydantic import ValidationError

from veriforge.contracts.engineering import EvidenceReference, SystemManifest
from .models import BridgeReport, RootObservation
from . import records


def _sha(raw):
    return hashlib.sha256(raw).hexdigest()


def _encode(value):
    if hasattr(value, "model_dump"):
        value = value.model_dump(mode="json")
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False,
                      allow_nan=False).encode()


@dataclass(frozen=True)
class _Case:
    case_id: str
    report_hash: str
    manifest_hash: str
    intent_hash: str
    checker_hash: str
    # Fixed operator roles, native artifact IDs, and raw source hashes.
    sources: tuple[tuple[str, str, str], ...]
    receipt_hash: str


_CORRECTED = _Case(
    "u7-corrected-v1",
    "fc7ca59e27f6be449c7f8c4b062e73db27323d8a8c04bac524f08177d34826ba",
    "949a9fcf12d7b133ebbf25c5a500ffc57c9fd6353f80f5a60847f72a0b0f0368",
    "8897eec0f1cb0e99ca70aa5bec4326a216fba43677b150d96509aa2d10f9de10",
    "989a83f4e09880cfd2183029d7c7288bc5f63b49e565e36e4efb4d8264f7051e",
    (("xdc", "official-master-xdc", "2debf0afb537c0a9f8f2c1569aa99e0366629a354f2fbdf369bd5142670086cc"),
     ("netlist", "carrier-netlist", "0f96ffcdbb8ace1c7c17be454f01f9438934b8147a54a63d5b6e1905a3d225ea"),
     ("board", "carrier-board", "6491491d6030c7a7319775fac4ba7b9216aa5a7f6665a855bb3004ecb6b6667c"),
     ("generator", "carrier-generator", "ec6e8758a7b8e1b489142d71a62d38a6b7892fa631fb4648e0859a4360d2b065"),
     ("logical_mapping", "u7-logical-mapping", "976a65b0518d7b522716a66b04e202991c492d020d59c7898d4268cc9b97eac3")),
    "ee7cc692428a475f96bc4ae31f5026aa4d7c3ba267548b0558146a421dd5ecda",
)
PROJECT_ID = "engineering-u7-carrier"
REVISION_ID = "u7-corrected-recorded-v1"
INVESTIGATION_ID = "u7-corrected-board-recorded-v1"


class PreparationError(ValueError):
    """Bounded diagnostic using logical input labels, never raw source content."""

    def __init__(self, stage, item, reason):
        self.diagnostic = {"stage": stage, "item": item, "reason": reason}
        super().__init__(f"{stage}: {item}: {reason}")


def _require(condition, item, reason):
    if not condition:
        raise PreparationError("case_contract", item, reason)


def _read(path, item, expected_hash):
    """Bounded regular-file reads; disallow links/reparse points at every level.

    Pre/post identity checks catch replacement; fixed hashes remain the authority
    even on platforms where a file can be renamed while a reader holds it open.
    """
    path = Path(path).absolute()
    try:
        for part in (*reversed(path.parents), path):
            info = part.lstat()
            if stat.S_ISLNK(info.st_mode) or getattr(info, "st_file_attributes", 0) & 0x400:
                raise PreparationError("input", item, "link_or_reparse_point")
        before = path.stat()
        if not stat.S_ISREG(before.st_mode):
            raise PreparationError("input", item, "regular_file_required")
        if before.st_size > records.MAX_ARTIFACT_BYTES:
            raise PreparationError("input", item, "artifact_size_limit")
        with path.open("rb") as stream:
            raw = stream.read(records.MAX_ARTIFACT_BYTES + 1)
        after = path.stat()
        if (before.st_dev, before.st_ino, before.st_size, before.st_mtime_ns) != (
                after.st_dev, after.st_ino, after.st_size, after.st_mtime_ns):
            raise PreparationError("input", item, "file_changed_during_read")
    except OSError:
        raise PreparationError("input", item, "missing_or_unreadable_file") from None
    if len(raw) > records.MAX_ARTIFACT_BYTES:
        raise PreparationError("input", item, "artifact_size_limit")
    if _sha(raw) != expected_hash:
        raise PreparationError("input", item, "raw_sha256_mismatch")
    return raw


def _parse(raw, model, item):
    try:
        return model.model_validate_json(raw)
    except ValidationError as exc:
        # Retain exact contract field locations/types, exclude private values.
        failures = [{"field": ".".join(map(str, e["loc"])), "type": e["type"],
                     "message": e["msg"]} for e in exc.errors(include_input=False, include_url=False)]
        raise PreparationError("typed_contract", item, failures) from None


def _bundle_path(root, section, artifact_id):
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,127}", artifact_id):
        raise PreparationError("case_contract", section, "unsafe_artifact_filename")
    return root / section / (artifact_id + ".json")


def prepare_corrected_case(*, bundle_root: Path, xdc: Path, netlist: Path, board: Path,
                           generator: Path, logical_mapping: Path, native_receipt: Path):
    """Trusted fixed-case boundary. No caller-selected authority or hash pins.

    Paths may refer to preserved local files or exact copies. No path is placed
    in generated configuration, envelope, diagnostics or metadata output.
    """
    return _prepare(_CORRECTED, Path(bundle_root), {
        "xdc": xdc, "netlist": netlist, "board": board, "generator": generator,
        "logical_mapping": logical_mapping, "native_receipt": native_receipt,
    })


def _prepare(case, bundle_root, paths):
    # Private factoring permits authored synthetic CI cases; public callers and
    # CLI cannot deserialize a case catalog or construct authority from JSON.
    report_raw = _read(bundle_root / "report.json", "report.json", case.report_hash)
    report = _parse(report_raw, BridgeReport, "report.json")
    manifest_raw = _read(bundle_root / "manifest.json", "manifest.json", case.manifest_hash)
    manifest = _parse(manifest_raw, SystemManifest, "manifest.json")
    _require(report.manifest == manifest, "manifest", "report_manifest_disagreement")
    _require(report.family == "board_connector" and report.checker_hash == case.checker_hash,
             "report", "board_family_or_recorded_checker_disagreement")
    _require(len(manifest.facts) == 33 and len(manifest.relations) == 8,
             "manifest", "expected_33_facts_and_8_relations")
    _require(len(report.comparisons) == 8 and all(
        row.verdict == "inconclusive" and row.value_comparison == "consistent"
        and "shared_derivation_root" in row.reasons
        and "unreviewed_or_model_source_or_intent" in row.reasons for row in report.comparisons),
        "comparisons", "expected_consistent_but_inconclusive_shared_unreviewed_ancestry")
    _require(all(row.verdict == "inconclusive" and row.dependence == "same_source"
                 and row.basis == "hypothesis" for row in manifest.relations),
             "relations", "expected_hypothesis_same_source_inconclusive")
    roots = [r for r in report.native_records if isinstance(r.data, RootObservation)]
    _require(len(roots) == 1 and roots[0].fact.review_state == "unreviewed",
             "roots", "expected_one_unreviewed_generator_root")
    _require(all(r.fact.review_state == "unreviewed" for r in report.native_records
                 if r.fact.category == "observation"), "observations", "expected_unreviewed_sources")

    blobs = {}

    def add(ref, raw):
        key = ref.artifact_id, ref.revision_id, ref.attempt_id
        if key in blobs:
            _require(blobs[key].reference == ref and blobs[key].data == raw,
                     ref.artifact_id, "duplicate_identity_disagreement")
            return ref
        blobs[key] = records.ArtifactBytes(reference=ref, data=raw)
        _require(len(blobs) <= records.MAX_ARTIFACTS and
                 sum(len(b.data) for b in blobs.values()) <= records.MAX_TOTAL_BYTES,
                 "closure", "aggregate_byte_or_count_limit")
        return ref

    def associated(name, raw):
        return add(EvidenceReference(artifact_id=name, revision_id=REVISION_ID, sha256=_sha(raw)), raw)

    proofs = {}
    for row in report.native_records:
        for proof in row.fact.provenance.sources:
            ref = proof.artifact
            if ref.artifact_id in proofs:
                _require(proofs[ref.artifact_id] == ref, ref.artifact_id, "ambiguous_provenance_identity")
            proofs[ref.artifact_id] = ref
    expected = {artifact_id for _, artifact_id, _ in case.sources} | {"reviewed-intent"}
    _require(set(proofs) == expected, "provenance", "exact_six_reviewed_source_ids_required")
    intent_raw = _read(bundle_root / "intent.json", "intent.json", case.intent_hash)
    add(proofs["reviewed-intent"], intent_raw)
    for role, artifact_id, digest in case.sources:
        _require(proofs[artifact_id].sha256 == digest, artifact_id, "source_pin_disagrees_with_provenance")
        add(proofs[artifact_id], _read(paths[role], role, digest))
    receipt_raw = _read(paths["native_receipt"], "native_receipt", case.receipt_hash)
    receipt_ref = associated("historical-native-receipt", receipt_raw)
    native = []
    for fact in manifest.facts:
        ref = fact.native_record
        raw = _read(_bundle_path(bundle_root, "native", ref.artifact_id),
                    "native/" + ref.artifact_id, ref.sha256)
        add(ref, raw)
        native.append(records.NativeAttachment(fact_id=fact.fact_id, domain=fact.domain,
            native_schema_id=fact.native_schema_id, reference=ref))
    for relation in manifest.relations:
        for ref in relation.evidence:
            add(ref, _read(_bundle_path(bundle_root, "comparisons", ref.artifact_id),
                          "comparisons/" + ref.artifact_id, ref.sha256))
    _require(not any(s.evidence for a in manifest.adapters for s in a.support),
             "capabilities", "additional_capability_evidence_not_in_reviewed_case")
    config = _encode({"schema_version": "engineering.recorded-case.v1", "case_id": case.case_id,
        "scope": "recorded_import_only", "board_reference": True, "attempts": [],
        "review_scope": "logical_cable_intent_only", "report_sha256": case.report_hash,
        "manifest_sha256": case.manifest_hash, "intent_sha256": case.intent_hash,
        "checker_sha256": case.checker_hash, "native_receipt_sha256": case.receipt_hash,
        "sources": {artifact_id: digest for _, artifact_id, digest in case.sources}})
    config_ref = associated("recorded-case-configuration", config)
    revision = records.EngineeringProjectRevision(project_id=PROJECT_ID, revision_id=REVISION_ID,
        source_refs=tuple(proofs[key] for key in sorted(proofs)) + (receipt_ref, config_ref),
        configuration_hash=config_ref.sha256)
    project_ref = associated("engineering-project-revision", _encode(revision))
    manifest_ref = associated("recorded-manifest", manifest_raw)
    report_ref = associated("recorded-bridge-report", report_raw)
    record = records.EngineeringInvestigation(investigation_id=INVESTIGATION_ID, project_id=PROJECT_ID,
        revision_id=REVISION_ID, project_revision_ref=project_ref, manifest_ref=manifest_ref,
        bridge_report_ref=report_ref, native_records=tuple(native), attempts=())
    _require(len(blobs) == 52, "closure", "expected_exactly_52_unique_blobs")
    authority = records.create_admission_authority(project_id=PROJECT_ID, revision_id=REVISION_ID,
        project_revision_ref=project_ref, manifest_ref=manifest_ref, bridge_report_ref=report_ref, attempts=())
    try:
        admitted = records.admit_investigation(record, tuple(blobs.values()), authority=authority)
        return records.read_investigation(records.serialize_investigation(admitted),
                                         admitted.artifacts, authority=authority)
    except ValueError as exc:
        raise PreparationError("admission", "complete_closure", str(exc)) from None


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--case", choices=[_CORRECTED.case_id], required=True)
    parser.add_argument("--bundle-root", type=Path, required=True)
    for role in ("xdc", "netlist", "board", "generator", "logical-mapping", "native-receipt"):
        parser.add_argument("--" + role, type=Path, required=True)
    args = vars(parser.parse_args(argv))
    args.pop("case")
    try:
        admitted = prepare_corrected_case(**args)
    except PreparationError as exc:
        print(json.dumps({"status": "rejected", **exc.diagnostic}, sort_keys=True))
        return 2
    print(json.dumps({"status": "admitted_in_memory", "operation": "admission_only",
        "new_execution": "not_run", "database": "not_written", "case": _CORRECTED.case_id,
        "project_id": PROJECT_ID, "revision_id": REVISION_ID,
        "native_revision_id": admitted.manifest.revision_id,
        "artifacts": len(admitted.artifacts), "total_bytes": admitted.envelope.total_artifact_bytes,
        "envelope_sha256": _sha(records.serialize_investigation(admitted)),
        "facts": len(admitted.manifest.facts), "relations": len(admitted.manifest.relations),
        "graph_verdicts": sorted({r.verdict for r in admitted.manifest.relations})}, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
