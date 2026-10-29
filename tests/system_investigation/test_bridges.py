"""Synthetic byte inputs test extraction/lineage only. No domain tool executes."""
import hashlib
import json
from pathlib import Path

import pytest
from pydantic import ValidationError

from veriforge.contracts import canonical_hash
from veriforge.contracts.engineering import EvidenceReference, SystemManifest
from veriforge.system_investigation import (AddressBinding, BoardIntent, PinBinding, RegisterIntent,
    RootArtifact, SourceArtifact, bundle_files, compare_board, compare_candidate, compare_registers)
from veriforge.system_investigation.xdc import extract_package_pins
from veriforge.system_investigation.models import BridgeReport, ComparisonRow

ROOT = Path(__file__).resolve().parents[2]
HEADER = b"#define CONTROL_ADDR 0x40000000u\n"
RTL = b"case (addr)\n8'h00: control <= write_data;\ndefault: ;\nendcase\n"
XDC = b"set_property -dict { PACKAGE_PIN AB22 IOSTANDARD LVCMOS33 } [get_ports {ja[0]}];\n"
NETLIST = (ROOT / "targets/kicad/synthetic/connector.xml").read_bytes()
BOARD = (ROOT / "targets/kicad/synthetic/connector.pcb-fragment").read_bytes()


def source(label, data, *, reviewed=True, origin="synthetic_fixture", parents=(), revision="fixture-v1"):
    return SourceArtifact(reference=EvidenceReference(artifact_id=label,
        sha256=hashlib.sha256(data).hexdigest(), revision_id=revision), data=data, origin=origin,
        reviewed_by="test-reviewer" if reviewed else None, parents=parents)


def registers(reviewed=True):
    return RegisterIntent(bindings=(AddressBinding(binding_id="control", header_macro="CONTROL_ADDR",
        rtl_signal="control", expected_address=0x40000000),), byte_base=0x40000000,
        reviewed_by="test-reviewer" if reviewed else None)


def pins(reviewed=True):
    return BoardIntent(bindings=(PinBinding(binding_id="gpio", port="ja[0]", package_pin="AB22",
        reference="J1", symbol_pin="1", footprint_pad="1", schematic_net="GPIO0", board_net="GPIO0"),),
        reviewed_by="test-reviewer" if reviewed else None)


def board_report(*, xdc=XDC, net=NETLIST, pcb=BOARD, intent=None, **kwargs):
    return compare_board(source("xdc", xdc), source("netlist", net), source("board", pcb), intent or pins(), **kwargs)


def test_firmware_native_values_and_bundle_hashes_bound_to_actual_bytes():
    report = compare_registers(source("header", HEADER), source("rtl", RTL), registers())
    assert report.execution_status == "not_run" and report.comparisons[0].verdict == "consistent"
    assert report.manifest.relations[0].basis == "independent_check"
    assert len(report.native_records) == 3
    files = bundle_files(report)
    for record in report.native_records:
        raw = files["native/" + record.fact.native_record.artifact_id + ".json"]
        assert hashlib.sha256(raw).hexdigest() == record.fact.native_record.sha256
    relation = report.manifest.relations[0]
    assert hashlib.sha256(files["comparisons/" + relation.evidence[0].artifact_id + ".json"]).hexdigest() == relation.evidence[0].sha256
    assert SystemManifest.model_validate_json(files["manifest.json"]) == report.manifest


def test_actual_selected_file_bytes_distinguish_mismatch_and_separate_candidate():
    rtl = source("rtl", RTL)
    bad = source("header", HEADER.replace(b"40000000", b"40000100"))
    before = compare_registers(bad, rtl, registers())
    after = compare_registers(source("header", HEADER, revision="candidate-v1"), rtl, registers())
    assert before.comparisons[0].verdict == "conflict" and after.comparisons[0].verdict == "consistent"
    comparison = compare_candidate(before, after)
    assert comparison.outcome == "source_comparison_improved"
    assert comparison.changed_source_ids == ("header",) and comparison.execution_status == "not_run"
    assert comparison.before_hash == canonical_hash(before)
    assert bad.data != HEADER  # Source input not patched in place.


@pytest.mark.parametrize("change", ["intent", "checker", "source_id"])
def test_candidate_cannot_change_check_policy_or_drop_source_identity(change):
    before = compare_registers(source("header", HEADER), source("rtl", RTL), registers())
    changed = before.model_dump(mode="json")
    if change == "source_id":
        changed["sources"][0]["reference"]["artifact_id"] = "unrelated-file"
    else:
        changed[change + "_hash"] = "f" * 64
    with pytest.raises(ValueError):
        compare_candidate(before, type(before).model_validate(changed))


def test_declared_actual_generator_bytes_preserve_shared_roots():
    root = source("generator", b"shared generation source")
    parent = RootArtifact(reference=root.reference, data=root.data, origin=root.origin, reviewed_by=root.reviewed_by)
    report = compare_registers(source("header", HEADER, parents=(parent,)), source("rtl", RTL, parents=(parent,)), registers())
    row = report.comparisons[0]
    assert row.value_comparison == "consistent" and row.verdict == "inconclusive"
    assert "shared_derivation_root" in row.reasons
    assert report.manifest.relations[0].dependence == "same_source"


@pytest.mark.parametrize("mode", ["unreviewed_source", "unreviewed_parent", "unreviewed_intent", "model_source", "model_parent"])
def test_model_claims_and_unreviewed_lineage_never_become_checked_observations(mode):
    parent = source("root", b"independent root", reviewed=mode != "unreviewed_parent",
                    origin="model_proposal" if mode == "model_parent" else "synthetic_fixture")
    parents = (RootArtifact(reference=parent.reference, data=parent.data, origin=parent.origin,
                            reviewed_by=parent.reviewed_by),) if mode in ("unreviewed_parent", "model_parent") else ()
    header = source("header", HEADER, reviewed=mode != "unreviewed_source",
                    origin="model_proposal" if mode == "model_source" else "synthetic_fixture", parents=parents)
    report = compare_registers(header, source("rtl", RTL), registers(mode != "unreviewed_intent"))
    assert report.comparisons[0].verdict == "inconclusive"
    assert all(r.basis != "independent_check" for r in report.manifest.relations)
    if mode in ("model_source", "model_parent"):
        assert report.comparisons[0].value_comparison == "inconclusive"
        assert report.comparisons[0].fact_ids == ()


def test_hash_tampering_and_invalid_bound_input_rejected_before_parse():
    with pytest.raises(ValidationError, match="SHA-256"):
        SourceArtifact(reference=source("header", HEADER).reference, data=b"different", origin="synthetic_fixture")
    forged = source("header", HEADER).model_copy(update={"data": b"changed-after-validation"})
    with pytest.raises(ValidationError):
        compare_registers(forged, source("rtl", RTL), registers())
    with pytest.raises(ValidationError):
        source("huge", b"x" * (1048576 + 1))


def test_nested_root_cannot_hide_unreviewed_parent_by_flattening():
    unreviewed = source("unreviewed", b"unreviewed", reviewed=False)
    nested = source("generator", b"generated", parents=(RootArtifact(reference=unreviewed.reference,
        data=unreviewed.data, origin=unreviewed.origin),))
    with pytest.raises(ValueError, match="nested ancestry"):
        source("header", HEADER, parents=(nested,))


def test_report_does_not_accept_changed_native_data_or_missing_manifest_record():
    report = compare_registers(source("header", HEADER), source("rtl", RTL), registers())
    payload = report.model_dump(mode="json")
    payload["native_records"][0]["data"]["value"] += 4
    with pytest.raises(ValueError, match="bytes/hash"):
        type(report).model_validate(payload)
    payload = report.model_dump(mode="json")
    payload["native_records"].pop()
    with pytest.raises(ValueError, match="manifest facts"):
        type(report).model_validate(payload)


def test_candidate_changed_bytes_need_new_revision_identity():
    before = compare_registers(source("header", HEADER.replace(b"40000000", b"40000004")), source("rtl", RTL), registers())
    after = compare_registers(source("header", HEADER), source("rtl", RTL), registers())
    with pytest.raises(ValueError, match="immutable revision"):
        compare_candidate(before, after)


@pytest.mark.parametrize("header,rtl", [(b"#include <unknown.h>", RTL), (HEADER, b"case (addr)\nparameterized_decode\nendcase"),
    (HEADER, RTL.replace(b"control", b"other"))])
def test_unsupported_or_missing_native_facts_remain_inconclusive(header, rtl):
    report = compare_registers(source("header", header), source("rtl", rtl), registers())
    assert report.comparisons[0].verdict == "inconclusive" and report.execution_status == "not_run"


def test_board_three_native_inputs_are_separately_parsed_without_inferred_pin_pad_aliases():
    report = board_report()
    assert report.comparisons[0].verdict == "consistent"
    assert len(report.comparisons[0].fact_ids) == 3 and report.execution_status == "not_run"
    mismatch = board_report(pcb=BOARD.replace(b'net 1 "GPIO0"))', b'net 2 "GND"))'))
    assert mismatch.comparisons[0].value_comparison == "conflict"
    changed = pins().model_dump(mode="json")
    changed["bindings"][0]["footprint_pad"] = "2"
    assert board_report(intent=BoardIntent.model_validate(changed)).comparisons[0].verdict == "conflict"


def test_identical_wrong_net_in_schematic_and_pcb_still_conflicts_with_external_intent():
    report = board_report(net=NETLIST.replace(b"GPIO0", b"WRONG_NET"), pcb=BOARD.replace(b"GPIO0", b"WRONG_NET"))
    assert report.comparisons[0].verdict == "conflict"


def test_net_names_are_exact_and_never_silently_strip_hierarchy():
    report = board_report(net=NETLIST.replace(b'"GPIO0"', b'"/GPIO0"'))
    assert report.comparisons[0].verdict == "conflict"


def test_board_reference_templates_require_explicit_mode_and_retain_reference_scope():
    raw = b"#" + XDC
    assert board_report(xdc=raw).comparisons[0].verdict == "inconclusive"
    report = board_report(xdc=raw, board_reference=True)
    assert report.comparisons[0].verdict == "consistent"
    assert any("commented_board_reference" in row.fact.conditions for row in report.native_records)
    rows = extract_package_pins(raw + b"set_property CFGBVS VCCO [current_design]\n", ("ja[0]",), board_reference=True)
    assert len(rows) == 1 and rows[0].interpretation == "commented_board_reference"


@pytest.mark.parametrize("xdc", [XDC + XDC, XDC + b"source uploaded.tcl\n", XDC + b"set_property PACKAGE_PIN $pin [get_ports {ja[0]}]\n"])
def test_duplicate_or_executable_xdc_never_guess_last_declaration(xdc):
    assert board_report(xdc=xdc).comparisons[0].verdict == "inconclusive"


def test_xdc_unselected_valid_literal_is_ignored_but_invalid_selection_rejected():
    rows = extract_package_pins(XDC + XDC.replace(b"ja[0]", b"ja[1]").replace(b"AB22", b"AB21"), ("ja[0]",))
    assert len(rows) == 1 and rows[0].package_pin == "AB22"
    with pytest.raises(ValueError):
        extract_package_pins(XDC, ("[exec anything]",))


def test_unknown_source_fields_and_ambiguous_intent_rejected():
    raw = registers().model_dump(mode="json")
    raw["bindings"].append(raw["bindings"][0])
    with pytest.raises(ValidationError):
        RegisterIntent.model_validate(raw)
    with pytest.raises(ValidationError):
        SourceArtifact(reference=source("header", HEADER).reference, data=HEADER,
                       origin="synthetic_fixture", command="never")


def test_each_complete_comparison_requires_its_relation_even_when_inconclusive():
    report = compare_registers(source("header", HEADER, reviewed=False), source("rtl", RTL), registers())
    data = report.model_dump(mode="json")
    data["manifest"]["relations"] = []
    with pytest.raises(ValueError, match="coverage"):
        BridgeReport.model_validate(data)


def test_comparison_fact_order_is_bound_to_native_roles_even_if_relation_is_rehashed():
    report = compare_registers(source("header", HEADER), source("rtl", RTL), registers())
    data = report.model_dump(mode="json")
    row = data["comparisons"][0]
    row["fact_ids"].reverse()
    data["manifest"]["relations"][0]["fact_ids"] = row["fact_ids"]
    data["manifest"]["relations"][0]["evidence"][0]["sha256"] = canonical_hash(ComparisonRow.model_validate(row))
    with pytest.raises(ValueError, match="reordered"):
        BridgeReport.model_validate(data)


def test_hypothesis_relation_cannot_authorize_checked_verdict_even_with_matching_hashes():
    report = compare_registers(source("header", HEADER, reviewed=False), source("rtl", RTL), registers())
    data = report.model_dump(mode="json")
    data["comparisons"][0]["verdict"] = "consistent"
    relation = data["manifest"]["relations"][0]
    relation["verdict"] = "consistent"
    relation["evidence"][0]["sha256"] = canonical_hash(ComparisonRow.model_validate(data["comparisons"][0]))
    with pytest.raises(ValueError, match="lineage authority"):
        BridgeReport.model_validate(data)


@pytest.mark.parametrize("field,value", [("sha256", "f" * 64), ("revision_id", "different-revision"), ("artifact_id", "different-source")])
def test_source_receipt_full_reference_must_equal_native_provenance(field, value):
    report = compare_registers(source("header", HEADER), source("rtl", RTL), registers())
    data = report.model_dump(mode="json")
    data["sources"][0]["reference"][field] = value
    with pytest.raises(ValueError, match="source receipt|Source receipt"):
        BridgeReport.model_validate(data)


def test_unsupported_input_still_retains_exact_identity_provenance():
    report = compare_registers(source("header", b"#include <unsupported.h>"), source("rtl", RTL), registers())
    assert report.comparisons[0].fact_ids == ()
    for receipt in report.sources:
        assert any(proof.artifact == receipt.reference for record in report.native_records for proof in record.fact.provenance.sources)
    data = report.model_dump(mode="json")
    data["sources"][0]["reference"]["sha256"] = "f" * 64
    with pytest.raises(ValueError, match="native provenance"):
        BridgeReport.model_validate(data)


def test_extra_unbound_requirement_fact_is_not_accepted_as_report_evidence():
    report = compare_registers(source("header", HEADER), source("rtl", RTL), registers())
    data = report.model_dump(mode="json")
    extra = json.loads(json.dumps(data["native_records"][-1]))
    extra["fact"]["fact_id"] = "intent-orphan"
    extra["fact"]["native_record"]["artifact_id"] = "native-intent-orphan"
    data["native_records"].append(extra)
    data["manifest"]["facts"].append(extra["fact"])
    with pytest.raises(ValueError, match="orphan native fact"):
        BridgeReport.model_validate(data)
