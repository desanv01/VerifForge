"""Independent source review: handwritten ELF/text bytes, no compiler/tool execution."""
import hashlib
import json
from pathlib import Path
import struct

import pytest

from veriforge.contracts import canonical_hash
from veriforge.firmware import (
    PlatformProfile, RegisterBinding, RegisterContract, check_elf_layout,
    compare_mmio_addresses, compare_register_facts, corroborate_instruction_bytes,
    extract_disassembly, extract_elf, extract_header, extract_mmio, extract_rtl_decode,
    prepare_seeded_decode_case,
)
from veriforge.firmware.contracts import FactComparison, LayoutCheck

RAM = 0x80000000
MMIO = 0x40000000


def digest(data):
    return hashlib.sha256(data).hexdigest()


def handwritten_elf(*segments):
    """Independent ELF32 constructor. Each tuple: virtual,physical,filebytes,memsize."""
    if not segments:
        segments = ((RAM, RAM, b"\x13\x00\x00\x00", 8),)
    data = bytearray(256 + 16 * len(segments))
    data[:16] = b"\x7fELF\x01\x01\x01" + bytes(9)
    struct.pack_into("<HHIIIIIHHHHHH", data, 16, 2, 243, 1, RAM, 52, 0, 0, 52, 32, len(segments), 0, 0, 0)
    for index, (virtual, physical, content, memory_size) in enumerate(segments):
        offset = 256 + 16 * index
        struct.pack_into("<IIIIIIII", data, 52 + 32 * index,
                         1, offset, virtual, physical, len(content), memory_size, 5, 1)
        data[offset:offset + len(content)] = content
    return bytes(data)


def one_register_contract():
    return RegisterContract(review_state="reviewed_fixture", registers=(RegisterBinding(
        logical_name="control", header_macro="CONTROL", rtl_signal="control", address=MMIO),))


HEADER = b"// independent handwritten parser input\n#define CONTROL 0x40000000u\n"
DECODE = b"case (addr)\n8'h00: control <= write_data;\nendcase\n"


@pytest.mark.parametrize("offset,value", [
    (4, b"\x02"), (5, b"\x02"), (28, struct.pack("<I", 0xfffffff0)),
    (42, struct.pack("<H", 31)), (44, struct.pack("<H", 129)),
    (52 + 4, struct.pack("<I", 0xffffffff)),
    (52 + 16, struct.pack("<I", 9)),
    (52 + 8, struct.pack("<I", 0xfffffffc)),
    (52 + 12, struct.pack("<I", 0xfffffffc)),
    (52 + 28, struct.pack("<I", 3)),
    (52 + 4, struct.pack("<I", 255)),
], ids=["elf64", "big-endian", "table-wrap", "wrong-table-stride", "oversize-table",
        "file-offset-overflow", "file-larger-than-memory", "virtual-wrap", "physical-wrap",
        "non-power-alignment", "alignment-congruence"])
def test_independent_elf_invalid_bounds_never_create_load_facts(offset, value):
    raw = bytearray(handwritten_elf())
    if offset == 56 and value == struct.pack("<I", 255):
        struct.pack_into("<I", raw, 80, 4)
    raw[offset:offset + len(value)] = value
    with pytest.raises(ValueError):
        extract_elf(bytes(raw), "independent-bad.elf", origin="synthetic_fixture")


def test_adjacent_ranges_and_exact_little_endian_file_bytes_are_consistent():
    raw = handwritten_elf((RAM, RAM, b"\x13\0\0\0", 4),
                          (RAM + 4, RAM + 4, b"\x93\0\0\0", 4))
    facts = extract_elf(raw, "handwritten.elf", origin="synthetic_fixture")
    rows = extract_disassembly(b"80000000: 00000013 nop\n80000004: 00000093 addi ra,zero,0\n",
                              "handwritten.disasm", origin="synthetic_fixture")
    assert corroborate_instruction_bytes(raw, facts, rows)
    assert facts.provenance.source_sha256 == digest(raw)
    assert [segment.file_sha256 for segment in facts.load_segments] == [digest(b"\x13\0\0\0"), digest(b"\x93\0\0\0")]
    assert check_elf_layout(facts, PlatformProfile()).verdict == "layout_agrees"
    assert all(row.provenance.origin == "synthetic_fixture" for row in rows)


def test_byte_corroboration_rejects_zero_fill_and_any_elf_identity_change():
    raw = handwritten_elf()
    facts = extract_elf(raw, "handwritten.elf", origin="synthetic_fixture")
    rows = extract_disassembly(b"80000004: 00000000 invented\n", "printed.disasm", origin="synthetic_fixture")
    with pytest.raises(ValueError, match="uniquely backed"):
        corroborate_instruction_bytes(raw, facts, rows)
    first = extract_disassembly(b"80000000: 00000013 nop\n", "printed.disasm", origin="synthetic_fixture")
    with pytest.raises(ValueError, match="identity"):
        corroborate_instruction_bytes(raw + b"padding", facts, first)
    wrong = extract_disassembly(b"80000000: 13000000 wrong_endian\n", "printed.disasm", origin="synthetic_fixture")
    with pytest.raises(ValueError, match="differs"):
        corroborate_instruction_bytes(raw, facts, wrong)


def test_overlap_is_detected_by_instruction_corroboration_even_before_layout_fix():
    raw = handwritten_elf((RAM, RAM, b"AAAA", 4), (RAM, RAM, b"BBBB", 4))
    facts = extract_elf(raw, "overlapping.elf", origin="synthetic_fixture")
    rows = extract_disassembly(b"80000000: 41414141 printed\n", "printed.disasm", origin="synthetic_fixture")
    with pytest.raises(ValueError, match="uniquely backed"):
        corroborate_instruction_bytes(raw, facts, rows)


@pytest.mark.parametrize("physical_only", [False, True], ids=["virtual-and-physical", "physical-only"])
def test_conflicting_load_bytes_must_not_claim_layout_agreement(physical_only):
    raw = handwritten_elf((RAM, RAM, b"AAAA", 4),
        (RAM + 4 if physical_only else RAM, RAM, b"BBBB", 4))
    try:
        facts = extract_elf(raw, "conflicting.elf", origin="synthetic_fixture")
    except ValueError:
        return  # Explicitly unsupported is also a valid fail-closed decision.
    assert check_elf_layout(facts, PlatformProfile()).verdict != "layout_agrees"


@pytest.mark.parametrize("data", [b"#define CONTROL 0x40000000u\n#undef CONTROL\n",
    b"/*\n#define CONTROL 0x40000000u\n*/\n"], ids=["undefined-macro", "comment-only-macro"])
def test_inactive_header_definitions_must_not_create_address_facts(data):
    with pytest.raises(ValueError):
        extract_header(data, "inactive.h", ("CONTROL",), origin="synthetic_fixture")


@pytest.mark.parametrize("data", [b"/*\n" + DECODE + b"*/\n",
    b"`ifdef UNKNOWN_CONFIGURATION\n" + DECODE + b"`endif\n"], ids=["comment-only-decode", "unreviewed-conditional"])
def test_inactive_or_unreviewed_rtl_decode_must_not_be_selected(data):
    with pytest.raises(ValueError):
        extract_rtl_decode(data, "inactive.sv", byte_base=MMIO, origin="synthetic_fixture")


def test_independently_written_header_and_rtl_disagreement_retains_exact_provenance():
    contract = one_register_contract()
    header_raw = HEADER.replace(b"0x40000000", b"0x40000004")
    header = extract_header(header_raw, "software.h", ("CONTROL",), origin="synthetic_fixture")
    rtl = extract_rtl_decode(DECODE, "hardware.sv", byte_base=MMIO, origin="synthetic_fixture")
    result = compare_register_facts(contract, header, rtl)
    assert result.verdict == "mismatch" and result.execution_status == "not_run"
    issue, = result.issues
    assert (issue.artifact, issue.expected_address, issue.observed_address) == ("header", MMIO, MMIO + 4)
    assert issue.provenance.source_sha256 == digest(header_raw)
    assert header[0].provenance.source_sha256 != rtl[0].provenance.source_sha256
    assert result.contract_hash == canonical_hash(contract)
    assert compare_register_facts(contract, (), rtl).verdict == "inconclusive"
    with pytest.raises(ValueError, match="ambiguous"):
        compare_register_facts(contract, header + header, rtl)


def test_imported_mmio_comparison_is_address_fact_only_never_observed_execution():
    raw = b'{"schema_version":"firmware.mmio.v1","transactions":[{"sequence":0,"operation":"write","address":1073741828,"data":99}]}'
    facts = extract_mmio(raw, "handwritten.json", origin="synthetic_fixture")
    compared = compare_mmio_addresses(one_register_contract(), facts, ("control",))
    assert compared.verdict == "mismatch" and compared.execution_status == "not_run"
    assert facts.provenance.source_sha256 == digest(raw) and facts.provenance.origin == "synthetic_fixture"
    assert compare_mmio_addresses(one_register_contract(), facts, ()).verdict == "inconclusive"


def test_source_candidate_preserves_all_exact_bytes_and_does_not_claim_execution(tmp_path):
    contract = one_register_contract()
    output = tmp_path / "reviewed-source-only"
    manifest = prepare_seeded_decode_case(contract, HEADER, DECODE, output, seed=11, byte_base=MMIO)
    assert manifest["origin"] == "synthetic_fixture" and manifest["execution_status"] == "not_run"
    assert (output / "candidate.h").read_bytes() == (output / "original.h").read_bytes() == HEADER
    assert manifest["original_hash"] == manifest["candidate_hash"] == digest(HEADER)
    assert manifest["candidate_base_hash"] == digest((output / "seeded.h").read_bytes()) != digest(HEADER)
    assert (output / "registers.sv").read_bytes() == DECODE
    assert manifest["contract_hash"] == canonical_hash(contract)
    for name, identity in manifest["files"].items():
        content = (output / name).read_bytes()
        assert digest(content) == identity["sha256"] and len(content) == identity["size_bytes"]
    for name, verdict in (("before.json", "mismatch"), ("after.json", "facts_agree")):
        report = FactComparison.model_validate_json((output / name).read_bytes())
        assert report.verdict == verdict and report.execution_status == "not_run"
    before = {file.name: digest(file.read_bytes()) for file in output.iterdir()}
    with pytest.raises(ValueError, match="empty"):
        prepare_seeded_decode_case(contract, HEADER, DECODE, output, seed=12, byte_base=MMIO)
    assert before == {file.name: digest(file.read_bytes()) for file in output.iterdir()}


@pytest.mark.parametrize("model,data", [
    (FactComparison, {"verdict": "facts_agree", "contract_hash": "a" * 64, "execution_status": "completed"}),
    (LayoutCheck, {"verdict": "layout_agrees", "profile_hash": "a" * 64,
                   "elf_hash": "a" * 64, "execution_status": "running"}),
])
def test_source_results_cannot_be_deserialized_as_tool_execution(model, data):
    with pytest.raises(ValueError):
        model.model_validate(data)
