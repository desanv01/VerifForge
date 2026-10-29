"""Pure parser/semantic tests. No KiCad, hardware, or real datasheet execution."""

from dataclasses import replace
from hashlib import sha256
import json
from pathlib import Path

import pytest

from veriforge.kicad import Check, MappingRequirement, check_mapping, command_argv
from veriforge.kicad import import_report, parse_board, parse_netlist, parse_report
from veriforge.kicad.reports import ImportFailure, MAX_BYTES

FIXTURES = Path(__file__).resolve().parents[2] / "targets/kicad/synthetic"


def report(kind="drc", violations=None):
    result = {"$schema": f"https://schemas.kicad.org/{kind}.v1.json",
              "source": "inert/private/path", "date": "2026-10-29T00:00:00Z",
              "kicad_version": "10.0.6", "coordinate_units": "mm",
              "included_severities": ["warning", "error", "exclusion"], "ignored_checks": []}
    if kind == "erc":
        result["sheets"] = [{"path": "/", "uuid_path": "/root/", "violations": violations or []}]
    else:
        result.update(violations=violations or [], unconnected_items=[], schematic_parity=[])
    return result


def violation(**overrides):
    return {"type": "clearance", "description": "Synthetic violation", "severity": "error",
            "items": [{"uuid": "00000000-0000-0000-0000-000000000001", "description": "pad", "pos": {"x": 1, "y": 2}}], **overrides}


def encode(obj):
    return json.dumps(obj).encode()


@pytest.mark.parametrize("kind", ["erc", "drc"])
def test_empty_report_is_never_electrical_acceptance(kind):
    result = parse_report(encode(report(kind)), kind)
    assert result.rule_status == "no_active_violations_reported"
    assert result.design_verdict == "inconclusive"
    assert result.execution_status == "not_observed"
    assert result.evidence_origin == "imported_unverified"


def test_all_drc_groups_and_exclusions_preserved():
    data = report(violations=[violation(excluded=True)])
    data["unconnected_items"] = [violation(type="unconnected_items")]
    data["schematic_parity"] = [violation(type="net_conflict")]
    data["ignored_checks"] = [{"key": "some_check", "description": "synthetic ignored check"}]
    result = parse_report(encode(data), "drc")
    assert len(result.findings) == 3
    assert result.findings[0].excluded
    assert result.findings[2].location == "/schematic_parity/0"
    assert result.ignored_checks == ("some_check",)
    assert result.rule_status == "reported_violations"


def test_excluded_finding_not_active():
    result = parse_report(encode(report(violations=[violation(excluded=True)])), "drc")
    assert result.rule_status == "no_active_violations_reported"
    assert len(result.findings) == 1


@pytest.mark.parametrize("change", [lambda x: x.update(excluded="false"),
    lambda x: x.update(severity="ignore"), lambda x: x["items"][0].update(uuid="bad"),
    lambda x: x["items"][0].update(pos={"x": True, "y": 2}),
    lambda x: x["items"][0].pop("pos")])
def test_malformed_finding_is_not_silently_dropped(change):
    item = violation()
    change(item)
    with pytest.raises(ImportFailure):
        parse_report(encode(report(violations=[item])), "drc")


def test_signature_stable_across_report_time():
    data = report(violations=[violation()])
    before = parse_report(encode(data), "drc")
    data["date"] = "2026-10-30T00:00:00Z"
    after = parse_report(encode(data), "drc")
    assert before.raw_sha256 != after.raw_sha256
    assert before.findings[0].signature == after.findings[0].signature


@pytest.mark.parametrize("key,value", [("violations", None), ("kicad_version", "11.0.0"),
    ("$schema", "https://example.com/fake.json"), ("coordinate_units", "cm"),
    ("included_severities", ["unknown"])])
def test_unsupported_report_not_clean(key, value):
    data = report()
    data[key] = value
    with pytest.raises(ImportFailure):
        parse_report(encode(data), "drc")


@pytest.mark.parametrize("raw", [b"{}", b"{", b'{"a":1,"a":2}', b'{"a":NaN}', b"", b"x" * (MAX_BYTES + 1)],
                         ids=["missing", "syntax", "duplicate", "nonfinite", "empty", "oversized"])
def test_bad_input_rejected(raw):
    with pytest.raises(ImportFailure):
        parse_report(raw, "drc")


def test_bundle_preserves_raw_and_malformed_input(tmp_path):
    raw = encode(report()) + b"\n"
    directory, result = import_report(raw, "drc", tmp_path)
    assert (directory / "original.json").read_bytes() == raw
    assert result.raw_sha256 == sha256(raw).hexdigest()
    with pytest.raises(ImportFailure):
        import_report(b"{", "drc", tmp_path)
    failures = list(tmp_path.glob("*/parse-error.json"))
    assert len(failures) == 1
    assert (failures[0].parent / "original.json").read_bytes() == b"{"


@pytest.fixture
def facts():
    return (parse_netlist((FIXTURES / "connector.xml").read_bytes()),
            parse_board((FIXTURES / "connector.pcb-fragment").read_bytes()))


@pytest.fixture
def requirement():
    # Fictional test provenance; never accepted into real project configuration.
    return MappingRequirement("J1", "1", "1", "GPIO0", "Example Devices", "SYNTHETIC-2P",
                              "Test:TwoPads", "a" * 64, "synthetic page 1", "test only", "b" * 64)


def test_source_facts_preserve_identity_and_lineage(facts):
    sch, board = facts
    assert sch.components[0].mpn == "SYNTHETIC-2P"
    assert sch.components[0].review_status == "unreviewed_source_claim"
    assert sch.endpoints[0].function == "Pin_1"
    assert sch.endpoints[0].source.source_sha256 == sch.raw_sha256
    assert board.endpoints[0].source.source_sha256 == board.raw_sha256
    assert board.kind == "board_pad_assignments"


def test_selected_mapping_control_and_seeded_swap(facts, requirement):
    sch, board = facts
    control = check_mapping(sch, board, requirement)
    assert control.status == "consistent_with_reviewed_mapping"
    changed = replace(board, endpoints=(replace(board.endpoints[0], net="GND"), *board.endpoints[1:]))
    failure = check_mapping(sch, changed, requirement)
    assert failure.status == "mismatch"
    assert failure.reasons == ("footprint_pad_net_mismatch",)
    assert failure.requirement_sha256 == control.requirement_sha256


@pytest.mark.parametrize("field", ["mpn", "manufacturer", "reviewed_by", "datasheet_location", "intent_sha256"])
def test_missing_reviewed_identity_inconclusive(facts, requirement, field):
    assert check_mapping(*facts, replace(requirement, **{field: ""})).status == "inconclusive"


def test_missing_component_mpn_inconclusive(facts, requirement):
    sch, board = facts
    sch = replace(sch, components=(replace(sch.components[0], mpn=None),))
    assert check_mapping(sch, board, requirement).reasons == ("missing_exact_part_identity",)


def test_conflicting_duplicate_pad_is_ambiguous(facts, requirement):
    sch, board = facts
    board = replace(board, endpoints=(*board.endpoints, replace(board.endpoints[0], net="GND")))
    assert check_mapping(sch, board, requirement).reasons == ("duplicate_pad_conflicting_nets",)


def test_does_not_infer_symbol_pin_equals_pad(facts, requirement):
    assert check_mapping(*facts, replace(requirement, footprint_pad="2")).status == "mismatch"


@pytest.mark.parametrize("raw", [b'<!DOCTYPE x [<!ENTITY e "x">]><export version="E"/>',
    b"<export/>", b"<export version='E'><components/><nets/></export>",
    "<!DOCTYPE x><export/>".encode("utf-16")])
def test_unsafe_or_empty_xml_rejected(raw):
    with pytest.raises(ImportFailure):
        parse_netlist(raw)


def test_multiple_net_assignment_rejected():
    raw = (FIXTURES / "connector.xml").read_bytes().replace(b'pin="2"', b'pin="1"')
    with pytest.raises(ImportFailure):
        parse_netlist(raw)


@pytest.mark.parametrize("mutation", [lambda x: x[:-2], lambda x: x.replace(b"20241229", b"20299999"),
    lambda x: x.replace(b'(net 1 "GPIO0"))', b'(net 1 "WRONG"))'),
    lambda x: x + b" junk", lambda x: b"(" * 70 + x + b")" * 70])
def test_unsupported_board_fails_closed(mutation):
    with pytest.raises(ImportFailure):
        parse_board(mutation((FIXTURES / "connector.pcb-fragment").read_bytes()))


def test_fixed_command_does_not_save_original(tmp_path):
    exe, board = tmp_path / "kicad-cli.exe", tmp_path / "board with spaces.kicad_pcb"
    exe.write_bytes(b"test placeholder, never executed")
    board.write_bytes(b"test placeholder")
    args = command_argv(Check.DRC, exe, board, tmp_path / "report.json")
    assert "--save-board" not in args
    assert "--schematic-parity" in args
    assert "--severity-all" in args
    assert args[-1] == str(board.resolve())
    with pytest.raises(ValueError):
        command_argv("drc --evil", exe, board, tmp_path / "report.json")
    with pytest.raises(ValueError):
        command_argv(Check.DRC, exe, board, board)
