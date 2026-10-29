"""Hash-bound native byte extraction and narrow comparisons; no tool dispatch."""
import hashlib
import json
from pathlib import Path

from veriforge.contracts import canonical_hash
from veriforge.contracts.engineering import (EvidenceReference, ExtractionIdentity, FactProvenance,
    FactRelation, NativeFactReference, SourceProvenance, SystemManifest)
from veriforge.firmware import extract as firmware
from veriforge.kicad import facts as kicad
from . import models, xdc
from .models import (BoardIntent, BridgeReport, CandidateComparison, ComparisonRow, ConnectorObservation,
    IntentObservation, NativeRecord, RegisterIntent, RootArtifact, RootObservation, SourceArtifact, SourceReceipt)

VERSION = "system.native-bridge.v1"


def _bytes(model):
    return json.dumps(model.model_dump(mode="json"), sort_keys=True, separators=(",", ":"),
                      ensure_ascii=False, allow_nan=False).encode("utf-8")


def implementation_hash():
    # Fixed reviewed dependencies, never caller-specified import names or plugins.
    paths = (Path(__file__), Path(models.__file__), Path(xdc.__file__),
             Path(firmware.__file__), Path(kicad.__file__),
             Path(__file__).parents[1] / "contracts/engineering.py")
    return hashlib.sha256(json.dumps({p.name + ":" + p.parent.name: hashlib.sha256(p.read_bytes()).hexdigest()
                                     for p in paths}, sort_keys=True).encode()).hexdigest()


def _source(source):
    # Validate even a model_copy/model_construct instance across this boundary.
    if not isinstance(source, SourceArtifact):
        raise ValueError("typed hash-bound source required")
    if any(type(parent) is not RootArtifact for parent in source.parents):
        raise ValueError("nested root ancestry unsupported, never truncated")
    return SourceArtifact(reference=EvidenceReference.model_validate_json(source.reference.model_dump_json()), data=source.data, origin=source.origin,
        reviewed_by=source.reviewed_by, parents=tuple(RootArtifact(reference=EvidenceReference.model_validate_json(parent.reference.model_dump_json()), data=parent.data,
            origin=parent.origin, reviewed_by=parent.reviewed_by) for parent in source.parents))


def _root_hashes(source):
    return {p.reference.sha256 for p in source.parents} if source.parents else {source.reference.sha256}


def _reviewed(source):
    return source.reviewed_by is not None and source.origin != "model_proposal" and all(
        p.reviewed_by is not None and p.origin != "model_proposal" for p in source.parents)


def _model_input(sources):
    return any(source.origin == "model_proposal" or any(p.origin == "model_proposal" for p in source.parents)
               for source in sources)


class _Builder:
    def __init__(self, intent, sources):
        self.intent, self.sources = intent, sources
        self.code_hash = implementation_hash()
        self.records, self.rows, self.relations = {}, [], []
        for source in sources:
            for parent in source.parents:
                payload = RootObservation(source=parent.reference, origin=parent.origin)
                self.add(payload, "root-" + parent.reference.sha256, "system", parent, (),
                         {"kind": "json_pointer", "pointer": ""}, "declared-input-root-v1", "1")

    def add(self, payload, fact_id, domain, source, parents, location, extractor, version, *, category="observation", conditions=()):
        raw_hash = canonical_hash(payload)
        reference = EvidenceReference(artifact_id="native-" + fact_id, sha256=raw_hash,
                                     revision_id=raw_hash)
        model_origin = source.origin == "model_proposal"
        fact = NativeFactReference(fact_id=fact_id, domain=domain, native_schema_id=type(payload).__name__ + ".v1",
            native_record_hash=raw_hash, native_record=reference, category="hypothesis" if model_origin else category,
            review_state="reviewed" if source.reviewed_by and not model_origin else "unreviewed",
            provenance=FactProvenance(state="complete", sources=(SourceProvenance(artifact=source.reference, location=location),),
                extraction=ExtractionIdentity(method="model_proposal" if model_origin else "native_parser",
                    extractor_id=extractor, extractor_version=version, implementation_hash=self.code_hash),
                derived_from=parents), conditions=("Parsed source assertions only; native tool execution not observed by this bridge.", *conditions))
        record = NativeRecord(fact=fact, data=payload)
        if fact_id in self.records and self.records[fact_id] != record:
            raise ValueError("inconsistent metadata for one derivation root")
        self.records[fact_id] = record
        return fact_id

    def observation(self, payload, binding_id, role, domain, source, location, extractor, version, conditions=()):
        return self.add(payload, role + "-" + binding_id, domain, source,
            tuple("root-" + p.reference.sha256 for p in source.parents), location, extractor, version, conditions=conditions)

    def compare(self, binding, ids, verdict, reasons):
        # Requirements describe selection/intent; never become observation roots.
        intent_data = IntentObservation(register_binding=binding) if isinstance(self.intent, RegisterIntent) else IntentObservation(pin_binding=binding)
        intent_ref = EvidenceReference(artifact_id="reviewed-intent", sha256=canonical_hash(self.intent), revision_id=canonical_hash(self.intent))
        intent_source = RootArtifact(reference=intent_ref, data=_bytes(self.intent), origin="imported_artifact",
                                     reviewed_by=self.intent.reviewed_by)
        requirement_id = self.add(intent_data, "intent-" + binding.binding_id, "system", intent_source, (),
            {"kind": "json_pointer", "pointer": "/bindings"}, "explicit-intent-v1", "1", category="requirement")
        if self.intent.review_evidence is not None:
            current = self.records[requirement_id]
            proof = current.fact.provenance.model_copy(update={"sources": (*current.fact.provenance.sources,
                SourceProvenance(artifact=self.intent.review_evidence, location={"kind": "json_pointer", "pointer": ""}))})
            self.records[requirement_id] = current.model_copy(update={"fact": current.fact.model_copy(update={"provenance": proof})})
        source_roots = [_root_hashes(s) for s in self.sources]
        shared = any(source_roots[i] & source_roots[j] for i in range(len(source_roots)) for j in range(i))
        reviewed = self.intent.reviewed_by is not None and all(_reviewed(s) for s in self.sources)
        checked = reviewed and not shared and verdict != "inconclusive" and len(ids) == len(self.sources)
        extra = list(reasons)
        if shared:
            extra.append("shared_derivation_root")
        if not reviewed:
            extra.append("unreviewed_or_model_source_or_intent")
        final_verdict = verdict if checked else "inconclusive"
        if not checked and not extra:
            extra.append("independence_or_complete_observations_not_established")
        row = ComparisonRow(binding_id=binding.binding_id, fact_ids=ids,
                            value_comparison=verdict, verdict=final_verdict, reasons=tuple(extra))
        self.rows.append(row)
        if len(ids) >= 2:
            self.relations.append(FactRelation(relation_id="relation-" + binding.binding_id,
                relation="same_address" if isinstance(self.intent, RegisterIntent) else "maps_to",
                fact_ids=ids, requirement_fact_ids=(requirement_id,), basis="independent_check" if checked else "hypothesis",
                verdict=final_verdict, dependence="independent" if checked else "same_source" if shared else "unknown",
                checker={"checker_id": VERSION, "version": "1", "sha256": self.code_hash} if checked else None,
                evidence=(EvidenceReference(artifact_id="comparison-" + binding.binding_id,
                    sha256=canonical_hash(row), revision_id=canonical_hash(row)),),
                uncertainties=tuple(extra) if final_verdict == "inconclusive" else ()))

    def finish(self, family):
        for source in self.sources:
            if not any(proof.artifact == source.reference for row in self.records.values() for proof in row.fact.provenance.sources):
                identity = canonical_hash(source.reference)
                self.add(RootObservation(source=source.reference, origin=source.origin), "input-" + identity,
                    "system", source, tuple("root-" + p.reference.sha256 for p in source.parents),
                    {"kind": "json_pointer", "pointer": ""}, "source-identity-v1", "1")
        revision = hashlib.sha256(json.dumps({"intent": canonical_hash(self.intent), "implementation": self.code_hash,
            "facts": [canonical_hash(row.fact) for row in self.records.values()]}, sort_keys=True).encode()).hexdigest()
        manifest = SystemManifest(manifest_id="native-bridge", revision_id=revision,
            review_state="reviewed" if self.intent.reviewed_by else "draft", reviewed_by=self.intent.reviewed_by,
            facts=tuple(row.fact for row in self.records.values()), relations=tuple(self.relations))
        return BridgeReport(family=family, intent=self.intent, intent_hash=canonical_hash(self.intent), checker_hash=self.code_hash,
            manifest=manifest, native_records=tuple(self.records.values()), comparisons=tuple(self.rows),
            sources=tuple(SourceReceipt(reference=source.reference, origin=source.origin, size_bytes=len(source.data))
                          for source in self.sources), limitations=(
                "Byte extraction and source comparison only; no firmware/core/Vivado/KiCad execution is performed.",
                "Review IDs, declared generator roots and independent artifact origins require host authentication; hashes alone do not prove authenticity.",
                "No access-policy, reset/strobe, routing, electrical, connector-part, cable-orientation or physical-board acceptance.",))


def compare_registers(header: SourceArtifact, rtl: SourceArtifact, intent: RegisterIntent):
    header, rtl = _source(header), _source(rtl)
    intent = RegisterIntent.model_validate_json(intent.model_dump_json())
    builder = _Builder(intent, (header, rtl))
    if _model_input(builder.sources):
        for binding in intent.bindings:
            builder.compare(binding, (), "inconclusive", ("model_proposal_is_not_native_observation",))
        return builder.finish("firmware_decode")
    try:
        hfacts = firmware.extract_header(header.data, header.reference.artifact_id,
            tuple(binding.header_macro for binding in intent.bindings), origin="synthetic_fixture" if header.origin == "synthetic_fixture" else "imported_artifact")
        rfacts = firmware.extract_rtl_decode(rtl.data, rtl.reference.artifact_id, selector=intent.selector,
            byte_base=intent.byte_base, origin="synthetic_fixture" if rtl.origin == "synthetic_fixture" else "imported_artifact")
    except (ValueError, UnicodeError, SyntaxError):
        for binding in intent.bindings:
            builder.compare(binding, (), "inconclusive", ("unsupported_or_ambiguous_native_decode",))
        return builder.finish("firmware_decode")
    for binding in intent.bindings:
        ids, values = [], []
        for role, facts, name, source in (("header", hfacts, binding.header_macro, header), ("rtl", rfacts, binding.rtl_signal, rtl)):
            selected = [row for row in facts if row.name == name]
            if len(selected) == 1:
                row = selected[0]
                line = int(row.provenance.location.removeprefix("line:"))
                ids.append(builder.observation(row, binding.binding_id, role, "firmware" if role == "header" else "rtl",
                    source, {"kind": "lines", "start": line, "end": line}, "firmware.facts.v1", "1"))
                values.append(row.value)
        verdict = "inconclusive" if len(values) != 2 else "consistent" if values == [binding.expected_address] * 2 else "conflict"
        builder.compare(binding, tuple(ids), verdict, ("literal_address_discrepancy",) if verdict == "conflict" else
                        ("selected_native_fact_missing",) if verdict == "inconclusive" else ())
    return builder.finish("firmware_decode")


def compare_board(xdc_source: SourceArtifact, netlist: SourceArtifact, board: SourceArtifact,
                  intent: BoardIntent, *, board_reference=False):
    xdc_source, netlist, board = _source(xdc_source), _source(netlist), _source(board)
    intent = BoardIntent.model_validate_json(intent.model_dump_json())
    builder = _Builder(intent, (xdc_source, netlist, board))
    if _model_input(builder.sources):
        for binding in intent.bindings:
            builder.compare(binding, (), "inconclusive", ("model_proposal_is_not_native_observation",))
        return builder.finish("board_connector")
    try:
        pins = xdc.extract_package_pins(xdc_source.data, tuple(b.port for b in intent.bindings), board_reference=board_reference)
        schematic, pcb = kicad.parse_netlist(netlist.data), kicad.parse_board(board.data)
    except (ValueError, UnicodeError):
        for binding in intent.bindings:
            builder.compare(binding, (), "inconclusive", ("unsupported_or_ambiguous_native_input",))
        return builder.finish("board_connector")
    for binding in intent.bindings:
        ids, values, reasons = [], [], []
        selected = [pin for pin in pins if pin.port == binding.port]
        if len(selected) == 1:
            pin = selected[0]
            ids.append(builder.observation(pin, binding.binding_id, "xdc", "vivado", xdc_source,
                {"kind": "lines", "start": pin.line, "end": pin.line}, xdc.VERSION, "1",
                conditions=(pin.interpretation,)))
            values.append(pin.package_pin == binding.package_pin)
        for role, native, source, number, wanted in (("schematic", schematic, netlist, binding.symbol_pin, binding.schematic_net),
                                                    ("board", pcb, board, binding.footprint_pad, binding.board_net)):
            endpoints = [p for p in native.endpoints if (p.reference, p.number) == (binding.reference, number)]
            # Even equal duplicate pads are outside this bridge's unambiguous subset.
            if len(endpoints) != 1:
                reasons.append("missing_or_ambiguous_" + role + "_endpoint")
                continue
            row = endpoints[0]
            payload = ConnectorObservation(reference=row.reference, number=row.number, net=row.net,
                native_location=row.source.location,
                interpretation="schematic_pin" if role == "schematic" else "board_pad_assignment")
            ids.append(builder.observation(payload, binding.binding_id, role, "kicad", source,
                {"kind": "native_object", "object_id": binding.reference + ":" + number, "field": "net"},
                native.parser_version, "1"))
            values.append(row.net == wanted)
        verdict = "inconclusive" if len(values) != 3 else "consistent" if all(values) else "conflict"
        if verdict == "conflict":
            reasons.append("package_pin_or_explicit_connector_net_discrepancy")
        if len(selected) != 1:
            reasons.append("missing_or_ambiguous_literal_package_pin")
        builder.compare(binding, tuple(ids), verdict, tuple(reasons))
    return builder.finish("board_connector")


def compare_candidate(before: BridgeReport, after: BridgeReport):
    # Authenticated reports must come from the byte bridges above, not raw clients.
    before = BridgeReport.model_validate_json(before.model_dump_json())
    after = BridgeReport.model_validate_json(after.model_dump_json())
    if (before.family, before.intent_hash, before.checker_hash) != (after.family, after.intent_hash, after.checker_hash):
        raise ValueError("candidate changed checker or reviewed intent")
    old = {s.reference.artifact_id: s for s in before.sources}
    new = {s.reference.artifact_id: s for s in after.sources}
    if len(old) != len(before.sources) or len(new) != len(after.sources) or old.keys() != new.keys():
        raise ValueError("candidate source identity set changed")
    changes = tuple(key for key in old if old[key].reference.sha256 != new[key].reference.sha256)
    for key in changes:
        first, second = old[key].reference, new[key].reference
        if (first.revision_id, first.attempt_id) == (second.revision_id, second.attempt_id):
            raise ValueError("changed source bytes require a new immutable revision/attempt identity")
    improved = bool(changes) and any(r.verdict == "conflict" for r in before.comparisons) and all(
        r.verdict == "consistent" for r in after.comparisons)
    return CandidateComparison(before_hash=canonical_hash(before), after_hash=canonical_hash(after),
        intent_hash=before.intent_hash, checker_hash=before.checker_hash, changed_source_ids=changes,
        value_conflicts_before=sum(r.value_comparison == "conflict" for r in before.comparisons),
        value_conflicts_after=sum(r.value_comparison == "conflict" for r in after.comparisons),
        outcome="source_comparison_improved" if improved else "no_demonstrated_improvement")


def bundle_files(report: BridgeReport):
    """Return bounded bytes for trusted storage; never writes/uploads private inputs."""
    report = BridgeReport.model_validate_json(report.model_dump_json())
    files = {"manifest.json": _bytes(report.manifest), "report.json": _bytes(report), "intent.json": _bytes(report.intent)}
    for row in report.native_records:
        raw = _bytes(row.data)
        if hashlib.sha256(raw).hexdigest() != row.fact.native_record_hash:
            raise ValueError("native record bytes differ from evidence reference")
        files["native/" + row.fact.native_record.artifact_id + ".json"] = raw
    for row in report.comparisons:
        files["comparisons/comparison-" + row.binding_id + ".json"] = _bytes(row)
    if sum(len(value) for value in files.values()) > 4 * 1_048_576:
        raise ValueError("bridge bundle exceeds 4MiB")
    return files
