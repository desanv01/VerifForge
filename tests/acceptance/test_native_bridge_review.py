"""Independent bridge review: synthetic adversarial bytes and saved native evidence.

No native tools, Tcl, simulator, network, or implementation edits.
"""
import hashlib
import json
from pathlib import Path

import pytest
from pydantic import ValidationError

from veriforge.contracts import canonical_hash
from veriforge.contracts.engineering import EvidenceReference
from veriforge.system_investigation import (
    AddressBinding, RegisterIntent, RootArtifact, SourceArtifact,
    bundle_files, compare_candidate, compare_registers,
)
from veriforge.system_investigation.models import BridgeReport, ComparisonRow
from veriforge.system_investigation.xdc import extract_package_pins

ROOT = Path(__file__).resolve().parents[2]
SAVED = ROOT / "tests/system_investigation/.tmp-native-probe-a7b105d044e344158b5d6999b8c1e18e"
PRIVATE_NATIVE_BUNDLE = ROOT / "targets/kicad/corrections/U7-CONTROL-CORRECTION/bridge-facts.json"
PRIVATE_EVIDENCE_UNAVAILABLE = not (SAVED / "summary.json").is_file() or not PRIVATE_NATIVE_BUNDLE.is_file()
HEADER = b"#define FLAG_ADDR 0x40000000u\n"
RTL = b"case (addr)\n8'h00: flag <= write_data;\ndefault: ;\nendcase\n"


def digest(data):
    return hashlib.sha256(data).hexdigest()


def source(label, data, *, reviewed=True, parents=(), revision="review-v1", origin="synthetic_fixture"):
    return SourceArtifact(reference=EvidenceReference(artifact_id=label, sha256=digest(data), revision_id=revision),
        data=data, reviewed_by="independent-test" if reviewed else None, parents=parents, origin=origin)


def intent():
    return RegisterIntent(byte_base=0x40000000, reviewed_by="independent-test",
        bindings=(AddressBinding(binding_id="flag", header_macro="FLAG_ADDR", rtl_signal="flag", expected_address=0x40000000),))


def report(*, reviewed=True, header=HEADER, revision="review-v1", parents=()):
    return compare_registers(source("header", header, reviewed=reviewed, revision=revision, parents=parents),
        source("rtl", RTL, reviewed=reviewed, parents=parents), intent())


@pytest.mark.skipif(PRIVATE_EVIDENCE_UNAVAILABLE, reason="Private retained native evidence unavailable; no actual execution claim")
def test_saved_native_hashes_receipts_and_literal_eight_to_zero_are_observations_only():
    summary = json.loads((SAVED / "summary.json").read_bytes())
    native_path = ROOT / "targets/kicad/corrections/U7-CONTROL-CORRECTION/bridge-facts.json"
    assert digest(native_path.read_bytes()) == summary["native_bundle_hash"] == "976a65b0518d7b522716a66b04e202991c492d020d59c7898d4268cc9b97eac3"
    native = json.loads(native_path.read_bytes())
    xdc = ROOT / native["source_artifacts"]["official_xdc"]["local_path"]
    assert digest(xdc.read_bytes()) == summary["xdc_hash"] == "2debf0afb537c0a9f8f2c1569aa99e0366629a354f2fbdf369bd5142670086cc"
    assert native["source_artifacts"]["official_xdc"]["interpretation"] == "commented example constraints, not executed project pin assignments"
    assert native["source_artifacts"]["manual"]["sha256"] is None
    reports = []
    for case in summary["board_inputs"]:
        receipt_path = ROOT / case["receipt"]
        raw_receipt = receipt_path.read_bytes()
        assert digest(raw_receipt) == case["receipt_hash"]
        receipt = json.loads(raw_receipt)
        assert digest((receipt_path.parent / "reports/netlist.xml").read_bytes()) == case["netlist_hash"] == receipt["output_hashes"]["netlist.xml"]
        assert digest((receipt_path.parent / "project-copy/carrier.kicad_pcb").read_bytes()) == case["board_hash"] == receipt["original_before"]["carrier.kicad_pcb"] == receipt["original_after"]["carrier.kicad_pcb"]
        saved = BridgeReport.model_validate_json((SAVED / case["case"] / "report.json").read_bytes())
        assert saved.execution_status == "not_run"
        assert len(saved.comparisons) == 8
        assert all(row.verdict == "inconclusive" for row in saved.comparisons)
        assert all(rel.basis == "hypothesis" and rel.dependence == "same_source" for rel in saved.manifest.relations)
        assert all(row.fact.review_state == "unreviewed" for row in saved.native_records if row.fact.category == "observation")
        assert {row.reference.sha256 for row in saved.sources} == {case["netlist_hash"], case["board_hash"], summary["xdc_hash"]}
        assert all(binding.reference == "J1" for binding in saved.intent.bindings)
        assert all("mpn" not in type(binding).model_fields for binding in saved.intent.bindings)
        assert all(row.data.interpretation == "commented_board_reference" for row in saved.native_records if row.data.kind == "package_pin")
        reports.append(saved)
    candidate = compare_candidate(*reports)
    assert candidate.model_dump(mode="json") == summary["candidate_comparison"]
    assert (candidate.value_conflicts_before, candidate.value_conflicts_after) == (8, 0)
    assert candidate.outcome == "no_demonstrated_improvement"
    assert candidate.before_hash == canonical_hash(reports[0])
    assert candidate.after_hash == canonical_hash(reports[1])


@pytest.mark.skipif(PRIVATE_EVIDENCE_UNAVAILABLE, reason="Private retained native evidence unavailable; no actual execution claim")
def test_saved_bundle_native_and_comparison_references_resolve_exact_bytes():
    for case in ("before", "after", "firmware"):
        saved = BridgeReport.model_validate_json((SAVED / case / "report.json").read_bytes())
        files = bundle_files(saved)
        for name, raw in files.items():
            assert (SAVED / case / name).read_bytes() == raw
        for row in saved.native_records:
            assert digest(files["native/" + row.fact.native_record.artifact_id + ".json"]) == row.fact.native_record_hash
        for rel in saved.manifest.relations:
            assert digest(files["comparisons/" + rel.evidence[0].artifact_id + ".json"]) == rel.evidence[0].sha256


@pytest.mark.parametrize("mode", ["unreviewed_leaf", "unreviewed_root", "shared_root", "model_root", "nested_root"])
def test_authority_does_not_promote_unreviewed_transitive_or_common_generation(mode):
    root = RootArtifact(reference=EvidenceReference(artifact_id="generator", sha256=digest(b"generator"), revision_id="r1"),
        data=b"generator", origin="model_proposal" if mode == "model_root" else "synthetic_fixture",
        reviewed_by=None if mode == "unreviewed_root" else "independent-test")
    left = source("header", HEADER, reviewed=mode != "unreviewed_leaf", parents=(root,) if mode != "unreviewed_leaf" else ())
    right = source("rtl", RTL, parents=(root,) if mode == "shared_root" else ())
    if mode == "nested_root":
        with pytest.raises(ValueError):
            compare_registers(left.model_copy(update={"parents": (left,)}), right, intent())
        return
    result = compare_registers(left, right, intent())
    assert result.comparisons[0].verdict == "inconclusive"
    assert all(rel.basis != "independent_check" for rel in result.manifest.relations)


@pytest.mark.parametrize("data", [b"#include <arbitrary.h>", b"#define FLAG_ADDR (unknown + 4)", b"\xff", b"#define OTHER 0x40000000"])
def test_malformed_or_missing_native_facts_do_not_become_checked(data):
    result = report(header=data)
    assert result.comparisons[0].verdict == "inconclusive"
    assert result.execution_status == "not_run"


def test_hash_drift_and_wrong_native_record_reference_rejected():
    with pytest.raises(ValidationError):
        compare_registers(source("header", HEADER).model_copy(update={"data": b"changed"}), source("rtl", RTL), intent())
    data = report().model_dump(mode="json")
    data["native_records"][0]["fact"]["native_record"]["sha256"] = "f" * 64
    with pytest.raises(ValidationError):
        BridgeReport.model_validate(data)


def test_active_and_comment_xdc_are_separate_literal_observations():
    raw = (b"# set_property PACKAGE_PIN AB22 [get_ports {ja[0]}]\n"
           b"set_property PACKAGE_PIN Y21 [get_ports {ja[0]}]\n")
    active = extract_package_pins(raw, ("ja[0]",))
    reference = extract_package_pins(raw, ("ja[0]",), board_reference=True)
    assert [(r.package_pin, r.interpretation, r.line) for r in active] == [("Y21", "active_literal", 2)]
    assert [(r.package_pin, r.interpretation, r.line) for r in reference] == [("AB22", "commented_board_reference", 1)]


@pytest.mark.parametrize("raw", [b"exec arbitrary", b"source other.xdc", b"set_property PACKAGE_PIN $pin [get_ports {ja[0]}]", b"set_property PACKAGE_PIN AB22 [get_ports {ja[0]}]; exec arbitrary"])
def test_xdc_substitution_commands_are_never_executed_or_admitted(raw, monkeypatch):
    import subprocess
    def forbidden(*args, **kwargs):
        pytest.fail("native/Tcl process execution forbidden in bridge review")
    monkeypatch.setattr(subprocess, "Popen", forbidden)
    with pytest.raises(ValueError):
        extract_package_pins(raw, ("ja[0]",))


@pytest.mark.parametrize("policy", ["intent", "checker"])
def test_candidate_keeps_checker_and_requirements(policy):
    before = report()
    data = before.model_dump(mode="json")
    data[policy + "_hash"] = "f" * 64
    with pytest.raises(ValueError):
        compare_candidate(before, BridgeReport.model_validate(data))


def test_missing_relationship_cannot_promote_unreviewed_candidate():
    before = report(reviewed=False, header=HEADER.replace(b"40000000", b"40000004"))
    after = report(reviewed=False, revision="review-v2")
    def forged(original, verdict):
        data = original.model_dump(mode="json")
        data["manifest"]["relations"] = []
        data["comparisons"][0]["verdict"] = verdict
        return BridgeReport.model_validate(data)
    with pytest.raises(ValueError):
        compare_candidate(forged(before, "conflict"), forged(after, "consistent"))


def test_source_receipt_hash_must_match_native_provenance():
    data = report().model_dump(mode="json")
    data["sources"][0]["reference"]["sha256"] = "f" * 64
    with pytest.raises(ValueError):
        BridgeReport.model_validate(data)


@pytest.mark.parametrize("mutation", ["ordered_roles", "literal_value", "hypothesis_promotion", "source_revision", "source_artifact", "unreviewed_ancestor"])
def test_reviewfix_boundary_rejects_rehashed_or_relabelled_authority(mutation):
    if mutation == "unreviewed_ancestor":
        parent = RootArtifact(reference=EvidenceReference(artifact_id="generator", sha256=digest(b"unreviewed-root"), revision_id="r1"),
            data=b"unreviewed-root", origin="synthetic_fixture")
        original = compare_registers(source("header", HEADER, parents=(parent,)), source("rtl", RTL), intent())
    else:
        original = report(reviewed=mutation != "hypothesis_promotion")
    data = original.model_dump(mode="json")
    row, relation = data["comparisons"][0], data["manifest"]["relations"][0]
    if mutation == "ordered_roles":
        row["fact_ids"].reverse()
        relation["fact_ids"] = row["fact_ids"][:]
    elif mutation == "literal_value":
        row["value_comparison"] = row["verdict"] = relation["verdict"] = "conflict"
    elif mutation in ("hypothesis_promotion", "unreviewed_ancestor"):
        row["verdict"] = relation["verdict"] = "consistent"
        if mutation == "unreviewed_ancestor":
            relation["basis"], relation["dependence"] = "independent_check", "independent"
            relation["checker"] = {"checker_id": "review-forgery", "version": "1", "sha256": data["checker_hash"]}
    elif mutation == "source_revision":
        data["sources"][0]["reference"]["revision_id"] = "substituted-revision"
    else:
        data["sources"][0]["reference"]["artifact_id"] = "substituted-artifact"
    row_hash = canonical_hash(ComparisonRow.model_validate(row))
    relation["evidence"][0]["sha256"] = row_hash
    relation["evidence"][0]["revision_id"] = row_hash
    with pytest.raises(ValueError):
        BridgeReport.model_validate(data)
