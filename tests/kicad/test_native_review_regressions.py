"""Synthetic parser and exact-part contradiction regressions; no native tools."""
from hashlib import sha256
import json

import pytest

from veriforge.kicad import check_mapping, import_report, MappingRequirement, parse_board, parse_netlist, parse_report
from veriforge.kicad.reports import ImportFailure

XML = b'''<export version="E"><components><comp ref="J1"><value>SYNTHETIC</value>
<footprint>Test:OnePad</footprint><datasheet>synthetic drawing</datasheet>
<libsource lib="Test" part="Connector"/><fields><field name="Manufacturer">Synthetic Maker</field>
<field name="MPN">SYNTHETIC-PART</field></fields></comp></components>
<nets><net code="1" name="SIGNAL"><node ref="J1" pin="1"/></net></nets></export>'''
BOARD = b'''(kicad_pcb (version 20241229) (net 0 "") (net 1 "SIGNAL")
(footprint "Test:OnePad" (property "Reference" "J1") (property "Value" "SYNTHETIC")
(pad "1" thru_hole circle (net 1 "SIGNAL"))))'''


def requirement():
    return MappingRequirement('J1', '1', '1', 'SIGNAL', 'Synthetic Maker', 'SYNTHETIC-PART',
        'Test:OnePad', 'a' * 64, 'synthetic drawing page', 'synthetic reviewer', 'b' * 64)


def report(x=1, y=2):
    return {'$schema': 'https://schemas.kicad.org/drc.v1.json', 'kicad_version': '10.0.6',
        'source': 'inert/private/path', 'date': 'synthetic', 'coordinate_units': 'mm',
        'included_severities': ['warning'], 'ignored_checks': [{'key': 'clearance', 'description': 'synthetic configured check'}],
        'violations': [{'type': 'clearance', 'description': 'synthetic issue', 'severity': 'warning',
            'items': [{'uuid': '00000000-0000-0000-0000-000000000001', 'description': 'pad', 'pos': {'x': x, 'y': y}}]}],
        'unconnected_items': [], 'schematic_parity': []}


@pytest.mark.parametrize('axis', ['x', 'y'])
@pytest.mark.parametrize('sign', [-1, 1])
def test_numeric_overflow_has_typed_error_and_retained_error_receipt(tmp_path, axis, sign):
    obj = report()
    obj['violations'][0]['items'][0]['pos'][axis] = sign * 10 ** 400
    raw = json.dumps(obj).encode()
    with pytest.raises(ImportFailure): parse_report(raw, 'drc')
    with pytest.raises(ImportFailure): import_report(raw, 'drc', tmp_path)
    receipt = next(tmp_path.glob('*/parse-error.json'))
    assert (receipt.parent / 'original.json').read_bytes() == raw
    parsed = json.loads(receipt.read_text())
    assert parsed['raw_sha256'] == sha256(raw).hexdigest()
    assert parsed['execution_status'] == 'not_observed' and parsed['design_verdict'] == 'inconclusive'


@pytest.mark.parametrize('duplicate', [b'<footprint>Test:Other</footprint>', b'<datasheet>other drawing</datasheet>',
    b'<libsource lib="Other" part="Other"/>', b'<value>Other</value>', b'<fields><field name="Other">extra</field></fields>',
    b'<footprint>Test:OnePad</footprint>'])
def test_singleton_duplicates_rejected_instead_of_first_win(duplicate):
    with pytest.raises(ImportFailure, match='duplicate component'):
        parse_netlist(XML.replace(b'</comp>', duplicate + b'</comp>'))


def test_valid_optional_singletons_keep_exact_source_identity():
    parsed = parse_netlist(XML)
    part = parsed.components[0]
    assert (part.footprint, part.datasheet, part.symbol) == ('Test:OnePad', 'synthetic drawing', 'Test:Connector')
    assert part.source.source_sha256 == parsed.raw_sha256 == sha256(XML).hexdigest()


@pytest.mark.parametrize('field,value,reason', [('Manufacturer', 'Different Maker', 'board_manufacturer_mismatch'),
    ('MPN', 'DIFFERENT-PART', 'board_mpn_mismatch')])
def test_nonempty_board_exact_part_contradiction_is_mismatch(field, value, reason):
    raw = BOARD.replace(b'(property "Value"', f'(property "{field}" "{value}") (property "Value"'.encode())
    result = check_mapping(parse_netlist(XML), parse_board(raw), requirement())
    assert result.status == 'mismatch' and reason in result.reasons
    assert {source.source_sha256 for source in result.sources} == {sha256(XML).hexdigest(), sha256(raw).hexdigest()}


@pytest.mark.parametrize('properties', [b'', b'(property "Manufacturer" "") (property "MPN" "")',
    b'(property "Manufacturer" "  ") (property "MPN" "  ")'])
def test_missing_optional_board_part_is_reported_without_fabricated_match(properties):
    raw = BOARD.replace(b'(property "Value"', properties + b'(property "Value"')
    result = check_mapping(parse_netlist(XML), parse_board(raw), requirement())
    assert result.status == 'consistent_with_reviewed_mapping'
    assert result.reasons == ('board_manufacturer_not_reported', 'board_mpn_not_reported')


def test_explicit_board_part_agreement_has_no_missing_identity_reasons():
    raw = BOARD.replace(b'(property "Value"', b'(property "Manufacturer" "Synthetic Maker") (property "MPN" "SYNTHETIC-PART") (property "Value"')
    result = check_mapping(parse_netlist(XML), parse_board(raw), requirement())
    assert result.status == 'consistent_with_reviewed_mapping' and result.reasons == ()


def test_configured_report_semantics_do_not_change_with_valid_coordinates():
    raw = json.dumps(report(-12.5, 0)).encode()
    result = parse_report(raw, 'drc')
    assert result.raw_sha256 == sha256(raw).hexdigest()
    assert result.included_severities == ('warning',) and result.ignored_checks == ('clearance',)
    assert result.rule_status == 'reported_violations'
    assert result.design_verdict == 'inconclusive' and result.execution_status == 'not_observed'
