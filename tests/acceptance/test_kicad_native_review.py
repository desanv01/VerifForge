"""Independent synthetic native-format inputs; no KiCad, part or board execution."""
from hashlib import sha256
import json
from pathlib import Path
import subprocess

import pytest

from veriforge.kicad import (Check, MappingRequirement, check_mapping, command_argv,
    import_report, inventory, parse_board, parse_netlist, parse_report)
from veriforge.kicad import readiness
from veriforge.kicad.reports import ImportFailure, MAX_BYTES


XML = b'''<export version="E"><components><comp ref="J1">
<value>SYNTHETIC</value><footprint>Test:OnePad</footprint><datasheet>synthetic only</datasheet>
<libsource lib="Test" part="Connector"/><fields><field name="Manufacturer">Synthetic Maker</field>
<field name="MPN">SYNTHETIC-PART</field></fields></comp></components>
<nets><net code="1" name="SIGNAL"><node ref="J1" pin="1" pinfunction="P1" pintype="passive"/></net></nets></export>'''
BOARD = b'''(kicad_pcb (version 20241229) (net 0 "") (net 1 "SIGNAL")
(footprint "Test:OnePad" (property "Reference" "J1") (property "Value" "SYNTHETIC")
(pad "1" thru_hole circle (net 1 "SIGNAL"))))'''


def requirement():
    return MappingRequirement("J1", "1", "1", "SIGNAL", "Synthetic Maker", "SYNTHETIC-PART",
        "Test:OnePad", "a" * 64, "synthetic drawing page", "synthetic test reviewer", "b" * 64)


def report():
    return {"$schema": "https://schemas.kicad.org/drc.v1.json", "kicad_version": "10.0.6",
        "source": "inert/report/source", "date": "synthetic test", "coordinate_units": "mm",
        "included_severities": ["warning"], "ignored_checks": [{"key": "clearance", "description": "synthetic ignored rule"}],
        "violations": [], "unconnected_items": [], "schematic_parity": []}


def test_configured_zero_findings_is_never_execution_or_design_success():
    raw = json.dumps(report()).encode()
    parsed = parse_report(raw, "drc")
    assert parsed.raw_sha256 == sha256(raw).hexdigest()
    assert parsed.included_severities == ("warning",) and parsed.ignored_checks == ("clearance",)
    assert parsed.rule_status == "no_active_violations_reported"
    assert (parsed.execution_status, parsed.design_verdict, parsed.evidence_origin) == ("not_observed", "inconclusive", "imported_unverified")


def test_huge_json_integer_coordinate_has_typed_import_failure():
    obj = report()
    obj["violations"] = [{"type": "clearance", "description": "synthetic issue", "severity": "error",
        "items": [{"uuid": "00000000-0000-0000-0000-000000000001", "description": "pad",
                   "pos": {"x": 10 ** 400, "y": 0}}]}]
    with pytest.raises(ImportFailure):
        parse_report(json.dumps(obj).encode(), "drc")


@pytest.mark.parametrize("extra", [b"<footprint>Test:Other</footprint>",
    b"<datasheet>conflicting source</datasheet>", b'<libsource lib="Other" part="Conflicting"/>'])
def test_ambiguous_xml_component_singletons_cannot_be_silently_discarded(extra):
    raw = XML.replace(b"<fields>", extra + b"<fields>")
    with pytest.raises(ImportFailure):
        parse_netlist(raw)


@pytest.mark.parametrize("field,value", [("manufacturer", "Different Maker"), ("mpn", "DIFFERENT-PART")])
def test_explicit_board_part_contradiction_cannot_be_consistent(field, value):
    name = "Manufacturer" if field == "manufacturer" else "MPN"
    added = f'(property "{name}" "{value}") '.encode()
    board_raw = BOARD.replace(b'(property "Value"', added + b'(property "Value"')
    schematic, board = parse_netlist(XML), parse_board(board_raw)
    assert getattr(board.components[0], field) == value
    assert board.raw_sha256 == sha256(board_raw).hexdigest()
    assert check_mapping(schematic, board, requirement()).status != "consistent_with_reviewed_mapping"


def test_exact_native_endpoint_identity_and_multiple_physical_pads_keep_provenance():
    board_raw = BOARD.replace(b'(pad "1" thru_hole circle (net 1 "SIGNAL"))',
        b'(pad "1" thru_hole circle (net 1 "SIGNAL")) (pad "1" thru_hole circle (net 1 "SIGNAL"))')
    schematic, board = parse_netlist(XML), parse_board(board_raw)
    outcome = check_mapping(schematic, board, requirement())
    assert outcome.status == "consistent_with_reviewed_mapping"
    assert {s.source_sha256 for s in outcome.sources} == {sha256(XML).hexdigest(), sha256(board_raw).hexdigest()}
    assert len({p.source.location for p in board.endpoints}) == 2
    alias = parse_netlist(XML.replace(b'pin="1"', b'pin="01"'))
    assert check_mapping(alias, board, requirement()).status == "inconclusive"


@pytest.mark.parametrize("raw", [b'<!DOCTYPE export SYSTEM "file:///synthetic-never-open"><export version="E"/>',
    b"<export version='E'>" + b"<x>" * 2, XML.replace(b'code="1"', b'code="1"').replace(b"</nets>", b'<net code="2" name="OTHER"><node ref="J1" pin="1"/></net></nets>')])
def test_unsafe_malformed_and_multiply_assigned_xml_rejects(raw):
    with pytest.raises(ImportFailure):
        parse_netlist(raw)


@pytest.mark.parametrize("parser,kind", [(parse_board, None), (parse_netlist, None), (parse_report, "drc")])
def test_byte_bound_is_applied_before_native_parsing(parser, kind):
    raw = b"x" * (MAX_BYTES + 1)
    with pytest.raises(ImportFailure):
        parser(raw, kind) if kind else parser(raw)


def test_board_depth_and_duplicate_declared_net_reject():
    with pytest.raises(ImportFailure):
        parse_board(b"(" * 65 + b"x" + b")" * 65)
    with pytest.raises(ImportFailure):
        parse_board(BOARD.replace(b'(net 1 "SIGNAL")', b'(net 1 "SIGNAL") (net 1 "OTHER")', 1))


def test_import_bundles_keep_originals_and_error_receipt_without_reading_report_paths(tmp_path):
    obj = report()
    obj["source"] = str(tmp_path / "source-that-does-not-exist.kicad_pcb")
    raw = json.dumps(obj).encode()
    first, _ = import_report(raw, "drc", tmp_path)
    second, _ = import_report(raw, "drc", tmp_path)
    assert first != second and (first / "original.json").read_bytes() == raw
    assert not Path(obj["source"]).exists()
    with pytest.raises(ImportFailure):
        import_report(b'{"same":1,"same":2}', "drc", tmp_path)
    assert len(list(tmp_path.glob("*/parse-error.json"))) == 1


def test_readiness_inventory_and_host_paths_never_execute_or_validate_tool(tmp_path, monkeypatch):
    executable = tmp_path / "synthetic-tool.exe"
    source = tmp_path / "board [literal].kicad_pcb"
    executable.write_bytes(b"synthetic placeholder; never executed")
    source.write_bytes(BOARD)
    monkeypatch.setattr(readiness.shutil, "which", lambda name: str(executable))
    monkeypatch.setattr(Path, "glob", lambda *args: iter(()))
    def forbidden(*args, **kwargs):
        pytest.fail("readiness must not launch a native process")
    monkeypatch.setattr(subprocess, "run", forbidden)
    monkeypatch.setattr(subprocess, "Popen", forbidden)
    capability = inventory()
    assert capability.live_execution == "not_implemented" and capability.electrical_acceptance == "not_established"
    argv = command_argv(Check.DRC, executable, source, tmp_path / "output.json")
    assert argv[-1] == str(source.resolve()) and "--save-board" not in argv
    with pytest.raises(ValueError):
        command_argv(Check.DRC, executable, source, source)
