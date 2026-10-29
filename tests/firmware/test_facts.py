"""Synthetic parser fixtures only. No compiler, ELF execution, simulator or tools."""
import hashlib
import json
from pathlib import Path
import struct
import pytest
from pydantic import ValidationError
from veriforge.firmware import (RegisterContract, extract_header, extract_rtl_decode, extract_elf,
    extract_disassembly, extract_mmio, corroborate_instruction_bytes, compare_register_facts,
    compare_mmio_addresses, prepare_seeded_decode_case)
from veriforge.firmware.extract import MAX_BYTES

FIXTURES = Path(__file__).resolve().parents[2] / "targets/firmware"


def contract():
    return RegisterContract.model_validate_json((FIXTURES / "register-map.json").read_text())


def header(data=None):
    return extract_header(data if data is not None else (FIXTURES / "registers.h").read_bytes(), "fixture.h",
        ("CONTROL_ADDR", "STATUS_ADDR", "DATA_ADDR", "SCRATCH_ADDR"), origin="synthetic_fixture")


def hardware(data=None):
    return extract_rtl_decode(data if data is not None else (FIXTURES / "registers.sv").read_bytes(), "fixture.sv", byte_base=0x40000000, origin="synthetic_fixture")


def elf_fixture():
    # Hand-written minimal ELF32: header52 + phdr32 + padding12; 12 raw code bytes.
    # Addresses/encodings match example.disasm; this has NEVER been linked or run.
    ident = b"\x7fELF\x01\x01\x01" + b"\0" * 9
    eh = struct.pack("<HHIIIIIHHHHHH", 2, 243, 1, 0, 52, 0, 0, 52, 32, 1, 0, 0, 0)
    ph = struct.pack("<IIIIIIII", 1, 96, 0, 0, 12, 16, 5, 4)
    code = bytes.fromhex("b70200409382420003a30200")
    return ident + eh + ph + bytes(12) + code


def test_independent_header_rtl_values_and_provenance():
    software, rtl = header(), hardware()
    assert [fact.value for fact in software] == [0x40000000, 0x40000004, 0x40000008, 0x4000000C]
    assert [fact.value for fact in rtl] == [0x40000000, 0x40000004, 0x40000008, 0x4000000C]
    assert software[0].provenance.source_sha256 != rtl[0].provenance.source_sha256
    assert software[0].provenance.location == "line:3"
    assert rtl[0].provenance.conditions
    result = compare_register_facts(contract(), software, rtl)
    assert result.verdict == "facts_agree" and result.execution_status == "not_run"
    assert software[0].provenance.review_state == "extracted_unreviewed"


def test_c_suffix_handling_does_not_rewrite_macro_identifiers():
    facts = extract_header(b"#define FULL 0x10ULL\n#define ADDRESS (FULL + 4u)", "suffix.h", ("ADDRESS",))
    assert facts[0].value == 20


@pytest.mark.parametrize("text", [
    b"#define REG 4\n#undef REG\n", b"/*\n#define REG 4\n*/\n",
    b"#define REG 4\n#include <other.h>\n", b"/* never closed\n#define REG 4\n",
])
def test_inactive_or_unreviewed_header_source_cannot_become_positive_facts(text):
    with pytest.raises(ValueError): extract_header(text, "inactive.h", ("REG",))


def test_legitimate_comments_preserve_macro_source_locations():
    row, = extract_header(b"/* ignored\n#define REG 99\n*/\n#define REG /* gap */ 4 // suffix\n", "commented.h", ("REG",))
    assert row.value == 4 and row.provenance.location == "line:4"


@pytest.mark.parametrize("prefix,suffix", [(b"/*\n", b"*/\n"), (b"`ifdef X\n", b"`endif\n")])
def test_inactive_rtl_decode_is_never_silently_selected(prefix, suffix):
    raw = b"case (addr)\n8'h00: reg0 <= write_data;\nendcase\n"
    with pytest.raises(ValueError): extract_rtl_decode(prefix + raw + suffix, "inactive.sv")


def test_independent_hardware_mismatch_is_not_hidden_by_matching_header_manifest():
    wrong_rtl = (FIXTURES / "registers.sv").read_bytes().replace(b"8'h04", b"8'h14")
    outcome = compare_register_facts(contract(), header(), hardware(wrong_rtl))
    assert outcome.verdict == "mismatch" and outcome.issues[0].artifact == "rtl"
    assert outcome.issues[0].observed_address == 0x40000014
    assert compare_register_facts(contract(), header()[:-1], hardware()).verdict == "inconclusive"
    with pytest.raises(ValueError): compare_register_facts(contract(), header() * 2, hardware())


@pytest.mark.parametrize("text", [
    b"#define CONTROL_ADDR __import__('os').system('bad')", b"#define CONTROL_ADDR (1 << 30)",
    b"#define CONTROL_ADDR 0x100000000", b"#define CONTROL_ADDR True", b"#define CONTROL_ADDR -4",
    b"#define CONTROL_ADDR UNKNOWN", b"#define CONTROL_ADDR 4\n#define CONTROL_ADDR 8",
    b"#if X\n#define CONTROL_ADDR 4\n#endif", b"#define CONTROL_ADDR 8 / 2",
])
def test_unsafe_or_ambiguous_header_subsets_fail_closed(text):
    with pytest.raises(ValueError): extract_header(text, "bad.h", ("CONTROL_ADDR",))


@pytest.mark.parametrize("source", [
    b"case (addr)\n8'h100: control <= write_data;\nendcase",
    b"case (addr)\n8'h00: control <= write_data;\n8'h00: data <= write_data;\nendcase",
    b"case (addr)\nOFFSET: control <= write_data;\nendcase",
    b"case (addr)\n8'h00: control <= write_data;",
])
def test_rtl_literal_subset_rejects_truncation_unknown_and_ambiguous_decode(source):
    with pytest.raises(ValueError): extract_rtl_decode(source, "bad.sv")


def test_elf_hand_derived_load_facts_and_disassembly_byte_corroboration():
    binary = elf_fixture()
    facts = extract_elf(binary, "synthetic.elf", origin="synthetic_fixture")
    assert facts.entry == 0 and facts.flags == 0 and facts.machine == 243
    segment, = facts.load_segments
    assert (segment.offset, segment.virtual_address, segment.file_size, segment.memory_size, segment.flags) == (96, 0, 12, 16, 5)
    assert segment.file_sha256 == hashlib.sha256(binary[96:108]).hexdigest()
    rows = extract_disassembly((FIXTURES / "example.disasm").read_bytes(), "synthetic.disasm", origin="synthetic_fixture")
    assert [row.address for row in rows] == [0, 4, 8]
    assert [row.mnemonic for row in rows] == ["lui", "addi", "lw"]
    assert corroborate_instruction_bytes(binary, facts, rows)
    wrong = rows[0].model_copy(update={"encoding": rows[0].encoding ^ 1})
    with pytest.raises(ValueError, match="differs"): corroborate_instruction_bytes(binary, facts, (wrong,))
    with pytest.raises(ValueError, match="identity"): corroborate_instruction_bytes(binary + b"x", facts, rows)


@pytest.mark.parametrize("offset,packed", [
    (4, b"\x02"), (5, b"\x02"), (18, struct.pack("<H", 62)),
    (40, struct.pack("<H", 51)), (42, struct.pack("<H", 31)),
    (44, struct.pack("<H", 129)), (56, struct.pack("<I", 1024)),
    (68, struct.pack("<I", 17)), (80, struct.pack("<I", 3)),
])
def test_elf_unsupported_and_malformed_offsets_are_bounded(offset, packed):
    binary = bytearray(elf_fixture())
    binary[offset:offset+len(packed)] = packed
    with pytest.raises(ValueError): extract_elf(bytes(binary), "bad.elf")


@pytest.mark.parametrize("data", [b"", b"x" * (MAX_BYTES + 1)], ids=("empty", "oversized"))
def test_empty_or_oversized_artifacts_do_not_become_facts(data):
    with pytest.raises(ValueError): extract_elf(data, "bad.elf")


def test_disassembly_is_explicitly_text_fact_not_complete_isa_validation():
    rows = extract_disassembly(b"0: 0001 c.nop\n2: 02000033 mul zero,zero,zero", "fixture.txt", origin="synthetic_fixture")
    assert [(row.width_bits, row.mnemonic) for row in rows] == [(16, "c.nop"), (32, "mul")]
    with pytest.raises(ValueError): extract_disassembly(b"0: 00000013 nop\n0: 00000013 nop", "duplicate.txt")
    with pytest.raises(ValueError): extract_disassembly(b"0: 13 00 00 00 nop", "unsupported-layout.txt")


def test_mmio_requires_explicit_bindings_and_never_claims_execution():
    data = (FIXTURES / "mmio.json").read_bytes()
    facts = extract_mmio(data, "synthetic-mmio.json", origin="synthetic_fixture")
    result = compare_mmio_addresses(contract(), facts, ("control", "status"))
    assert result.verdict == "facts_agree" and result.execution_status == "not_run"
    altered = json.loads(data)
    altered["transactions"][1]["address"] = 0x4000000C
    result = compare_mmio_addresses(contract(), extract_mmio(json.dumps(altered).encode(), "mismatch.json"), ("control", "status"))
    assert result.verdict == "mismatch" and result.issues[0].artifact == "mmio"
    assert compare_mmio_addresses(contract(), facts, ("control",)).verdict == "inconclusive"
    with pytest.raises(ValueError): compare_mmio_addresses(contract(), facts, ("unknown",))


@pytest.mark.parametrize("data", [
    b'{"schema_version":"firmware.mmio.v1","transactions":[],"transactions":[]}',
    b'{"schema_version":"wrong","transactions":[]}',
    b'{"schema_version":"firmware.mmio.v1","transactions":[{"sequence":1,"operation":"read","address":0,"data":0}]}',
    b'{"schema_version":"firmware.mmio.v1","transactions":[{"sequence":0,"operation":"read","address":true,"data":0}]}',
])
def test_mmio_malformed_or_ambiguous_is_not_a_design_finding(data):
    with pytest.raises(ValueError): extract_mmio(data, "bad.json")


def test_seeded_case_preserves_control_bad_base_and_candidate_with_unchanged_contract(tmp_path):
    original = (FIXTURES / "registers.h").read_bytes()
    rtl = (FIXTURES / "registers.sv").read_bytes()
    first = prepare_seeded_decode_case(contract(), original, rtl, tmp_path / "first", seed=17, byte_base=0x40000000)
    second = prepare_seeded_decode_case(contract(), original, rtl, tmp_path / "second", seed=17, byte_base=0x40000000)
    assert first == second and first["execution_status"] == "not_run"
    assert first["original_hash"] == first["candidate_hash"] != first["candidate_base_hash"]
    assert (tmp_path / "first/original.h").read_bytes() == original == (tmp_path / "first/candidate.h").read_bytes()
    assert json.loads((tmp_path / "first/before.json").read_text())["verdict"] == "mismatch"
    assert json.loads((tmp_path / "first/after.json").read_text())["verdict"] == "facts_agree"
    for name, identity in first["files"].items():
        data = (tmp_path / "first" / name).read_bytes()
        assert hashlib.sha256(data).hexdigest() == identity["sha256"] and len(data) == identity["size_bytes"]
    assert b"--- seeded.h" in (tmp_path / "first/candidate.diff").read_bytes()
    with pytest.raises(ValueError, match="empty"): prepare_seeded_decode_case(contract(), original, rtl, tmp_path / "first", seed=17, byte_base=0x40000000)


def test_schema_rejects_duplicate_relationships_without_touching_shared_contracts():
    raw = contract().model_dump()
    raw["registers"] = (raw["registers"][0], raw["registers"][0])
    with pytest.raises(ValidationError): RegisterContract.model_validate(raw)


@pytest.mark.parametrize("fault", [None, "entry", "ram", "flags", "non_executable"])
def test_main_approved_platform_layout_is_checked_without_claiming_isa_or_boot(fault):
    from veriforge.firmware import PlatformProfile, check_elf_layout
    binary = bytearray(elf_fixture())
    struct.pack_into("<I", binary, 24, 0x80000000)
    struct.pack_into("<I", binary, 60, 0x80000000)
    struct.pack_into("<I", binary, 64, 0x80000000)
    if fault == "entry": struct.pack_into("<I", binary, 24, 0x80000004)
    if fault == "ram": struct.pack_into("<I", binary, 64, 0x80010000)
    if fault == "flags": struct.pack_into("<I", binary, 36, 1)
    if fault == "non_executable": struct.pack_into("<I", binary, 76, 6)
    result = check_elf_layout(extract_elf(bytes(binary), "synthetic-profile.elf", origin="synthetic_fixture"), PlatformProfile())
    assert result.verdict == ("mismatch" if fault else "layout_agrees")
    assert result.execution_status == "not_run" and result.limitations


@pytest.mark.parametrize("physical_only,bss_tail", [(False, False), (True, False), (False, True)])
def test_overlapping_load_or_bss_ranges_fail_platform_layout(physical_only, bss_tail):
    from veriforge.firmware import PlatformProfile, check_elf_layout
    # Handwritten two-PT_LOAD artifact. Conflicting initialized bytes or BSS
    # overlap are ambiguous even if virtual instruction byte lookup is unique.
    ident = b"\x7fELF\x01\x01\x01" + bytes(9)
    eh = struct.pack("<HHIIIIIHHHHHH", 2, 243, 1, 0x80000000, 52, 0, 0, 52, 32, 2, 0, 0, 0)
    first = struct.pack("<IIIIIIII", 1, 116, 0x80000000, 0x80000000, 4, 8 if bss_tail else 4, 5, 1)
    second = struct.pack("<IIIIIIII", 1, 120, 0x80000004 if physical_only or bss_tail else 0x80000000,
                         0x80000000 if physical_only else 0x80000004 if bss_tail else 0x80000000, 4, 4, 5, 1)
    facts = extract_elf(ident + eh + first + second + b"AAAABBBB", "overlap.elf", origin="synthetic_fixture")
    result = check_elf_layout(facts, PlatformProfile())
    assert result.verdict == "mismatch" and any("Overlapping" in issue for issue in result.issues)
