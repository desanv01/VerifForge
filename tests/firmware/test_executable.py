"""Executable-fixture preparation checks only. No toolchain or core is invoked."""
import hashlib
import json
from pathlib import Path
import pytest
from pydantic import ValidationError
from veriforge.firmware import RegisterContract, extract_header, compare_register_facts
from veriforge.firmware.executable import (FIXTURE_ROOT, FirmwarePreparation, commands,
    extract_native_decode, prepare_executable_case)


@pytest.mark.parametrize("fixture,expected", [
    ("fw-regmap-control-v1", "facts_agree"), ("fw-regmap-offset-bug-v1", "mismatch"),
    ("fw-regmap-offset-candidate-v1", "facts_agree"),
])
def test_preparation_has_independent_source_facts_and_no_execution_claim(tmp_path, fixture, expected):
    plan = FirmwarePreparation(fixture_id=fixture)
    output = tmp_path / fixture
    manifest = prepare_executable_case(plan, output)
    assert manifest["execution_status"] == "not_run" and manifest["recipe_status"] == "prepared_not_executed"
    assert len(manifest["preparer_hash"]) == len(manifest["fact_extractor_hash"]) == len(manifest["address_checker_hash"]) == 64
    comparison = json.loads((output / "address-check.json").read_text())
    assert comparison["verdict"] == expected and comparison["execution_status"] == "not_run"
    if expected == "mismatch":
        issue, = comparison["issues"]
        assert (issue["register_name"], issue["artifact"], issue["expected_address"], issue["observed_address"]) == ("status", "header", 0x40000004, 0x40000008)
    for name, identity in manifest["source_files"].items():
        content = (output / name).read_bytes()
        assert hashlib.sha256(content).hexdigest() == identity["sha256"] and len(content) == identity["size_bytes"]
    assert (output / "original-bad-registers.h").read_bytes() == (FIXTURE_ROOT / "offset_bug/registers.h").read_bytes()
    before = (output / "preparation.json").read_bytes()
    with pytest.raises(ValueError, match="empty"): prepare_executable_case(plan, output)
    assert (output / "preparation.json").read_bytes() == before


def test_candidate_diff_changes_only_status_offset_and_keeps_bad_base(tmp_path):
    manifest = prepare_executable_case(FirmwarePreparation(fixture_id="fw-regmap-offset-candidate-v1"), tmp_path)
    diff = (tmp_path / "candidate.diff").read_text()
    changed = [line for line in diff.splitlines() if line.startswith(("+", "-")) and not line.startswith(("+++", "---"))]
    assert changed == ["-#define STATUS_ADDR 0x40000008u", "+#define STATUS_ADDR 0x40000004u"]
    assert manifest["original_bad_hash"] != manifest["selected_header_hash"]
    assert manifest["candidate_provenance"] == "curated_header_offset_restoration"
    assert (tmp_path / "registers.h").read_bytes() == (FIXTURE_ROOT / "control/registers.h").read_bytes()


def test_actual_proposed_native_decode_is_not_derived_from_bad_header():
    raw = (FIXTURE_ROOT / "native_registers.sv").read_bytes()
    facts = extract_native_decode(raw)
    assert [(row.name, row.value) for row in facts] == [("register_0", 0x40000000), ("register_1", 0x40000004), ("register_2", 0x40000008), ("register_3", 0x4000000C)]
    contract = RegisterContract.model_validate_json((FIXTURE_ROOT / "register-contract.json").read_text())
    good = extract_header((FIXTURE_ROOT / "control/registers.h").read_bytes(), "good.h", tuple(row.header_macro for row in contract.registers))
    wrong = raw.replace(b"32'h40000004", b"32'h40000014")
    result = compare_register_facts(contract, good, extract_native_decode(wrong))
    assert result.verdict == "mismatch" and result.issues[0].artifact == "rtl"
    assert facts[0].provenance.source_sha256 != good[0].provenance.source_sha256


@pytest.mark.parametrize("mutation", ["comment", "conditional", "index", "duplicate"])
def test_native_decode_parser_never_guesses_ambiguous_sources(mutation):
    data = (FIXTURE_ROOT / "native_registers.sv").read_bytes()
    if mutation == "comment": data = b"/*\n" + data + b"\n*/"
    if mutation == "conditional": data = b"`ifdef CONFIG\n" + data + b"`endif\n"
    if mutation == "index": data = data.replace(b"mem_rdata = registers[1]", b"mem_rdata = registers[2]")
    if mutation == "duplicate": data = data.replace(b"32'h40000004", b"32'h40000000")
    with pytest.raises(ValueError): extract_native_decode(data)


@pytest.mark.parametrize("extra", [{"fixture_id": "../../untrusted"}, {"fixture_id": "fw-regmap-control-v1", "command": "anything"},
    {"fixture_id": "fw-regmap-control-v1", "wall_time_seconds": 0.0}, {"fixture_id": "fw-regmap-control-v1", "max_retired": True}])
def test_preparation_plan_has_only_curated_selection_and_bounded_resources(extra):
    with pytest.raises(ValidationError): FirmwarePreparation(**extra)


def test_recipe_has_frozen_flags_and_only_copied_sources_and_generated_outputs(tmp_path):
    recipe = commands(tmp_path)
    argv = recipe["compiler"]
    assert argv[0] == "riscv64-unknown-elf-gcc"
    for flag in ("-march=rv32i", "-mabi=ilp32", "-mno-relax", "-nostdlib", "-nostartfiles", "-msmall-data-limit=0", "-Wl,--no-relax"):
        assert flag in argv
    assert str(tmp_path / "start.o") in argv and str(tmp_path / "independent_probe.o") in argv and str(tmp_path / "main.o") in argv
    for name in ("start.S", "independent_probe.S", "main.c"):
        assert str(tmp_path / name) not in argv
    for key, source, obj in (("assemble_start", "start.S", "start.o"), ("assemble_probe", "independent_probe.S", "independent_probe.o"), ("compile_main", "main.c", "main.o")):
        step = recipe[key]
        assert "-c" in step and step[-2:] == ("-o", str(tmp_path / obj)) and str(tmp_path / source) in step
        assert not any(flag.startswith("-Wl,") for flag in step)
        for flag in ("-march=rv32i", "-mabi=ilp32", "-mno-relax", "-ffreestanding", "-fno-pic", "-fno-pie"):
            assert flag in step
    assert list(recipe)[:4] == ["assemble_start", "assemble_probe", "compile_main", "compiler"]
    assert recipe["binary"][:3] == ("riscv64-unknown-elf-objcopy", "-O", "binary")
    assert recipe["disassembly"][0] == "riscv64-unknown-elf-objdump"
    assert "spike" not in json.dumps(recipe)  # No invented MMIO architectural-reference capability.


def test_firmware_uses_independent_fixed_status_probe_and_exact_startup_addresses():
    probe = (FIXTURE_ROOT / "independent_probe.S").read_text()
    assert '#include' not in probe and "lui t0, 0x40000" in probe and "lw a0, 4(t0)" in probe
    code = (FIXTURE_ROOT / "main.c").read_text()
    assert "read_status_at_reviewed_address() != 0x55667788u" in code
    assert "0xAABB11DDu" in code and "DATA_ADDR + 1u" in code
    startup = (FIXTURE_ROOT / "start.S").read_text()
    assert 0x80010000 - 272 == 0x8000FEF0 and "addi sp, sp, -272" in startup
    assert 0x80010000 - 256 == 0x8000FF00 and "addi t0, t0, -256" in startup
    linker = (FIXTURE_ROOT / "link.ld").read_text()
    assert "0x80000000" in linker and "0x10000" in linker and "0x8000E000" in linker


def test_existing_core_ram_cannot_be_mistaken_for_mmio_integration():
    from veriforge.riscv.memory import Ram
    with pytest.raises(ValueError, match="MMIO unsupported"): Ram(b"").read_word(0x40000000)
