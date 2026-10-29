"""Source-only register decode investigation and preserved seeded candidate copy."""
import difflib
import json
from pathlib import Path
import random
import re
from veriforge.contracts import canonical_hash
from .contracts import FactComparison, FactIssue, RegisterContract, PlatformProfile, LayoutCheck
from .extract import extract_header, extract_rtl_decode, sha


def compare_register_facts(contract: RegisterContract, header_facts, rtl_facts):
    contract = RegisterContract.model_validate(contract)
    issues, missing = [], False
    for facts in (header_facts, rtl_facts):
        names = [fact.name for fact in facts]
        if len(names) != len(set(names)): raise ValueError("ambiguous duplicate artifact facts")
    header, rtl = {fact.name: fact for fact in header_facts}, {fact.name: fact for fact in rtl_facts}
    for binding in contract.registers:
        for kind, fact in (("header", header.get(binding.header_macro)), ("rtl", rtl.get(binding.rtl_signal))):
            if fact is None or fact.value != binding.address:
                missing |= fact is None
                issues.append(FactIssue(register_name=binding.logical_name, artifact=kind, expected_address=binding.address,
                    observed_address=fact.value if fact else None, provenance=fact.provenance if fact else None))
    return FactComparison(verdict="inconclusive" if missing else "mismatch" if issues else "facts_agree",
                          contract_hash=canonical_hash(contract), issues=tuple(issues))


def compare_mmio_addresses(contract: RegisterContract, facts, expected_registers: tuple[str, ...]):
    """Explicit reviewed ordered transaction/register association, never name inference."""
    contract = RegisterContract.model_validate(contract)
    lookup = {row.logical_name: row for row in contract.registers}
    if any(name not in lookup for name in expected_registers) or len(expected_registers) > 4096:
        raise ValueError("unknown or oversized explicit MMIO binding")
    if len(facts.transactions) != len(expected_registers):
        return FactComparison(verdict="inconclusive", contract_hash=canonical_hash(contract), limitations=("Incomplete or extra imported MMIO transactions; no exact scenario association established.",))
    issues = tuple(FactIssue(register_name=name, artifact="mmio", expected_address=lookup[name].address,
                            observed_address=row.address, provenance=facts.provenance)
                   for row, name in zip(facts.transactions, expected_registers) if row.address != lookup[name].address)
    return FactComparison(verdict="mismatch" if issues else "facts_agree", contract_hash=canonical_hash(contract), issues=issues)


def check_elf_layout(elf, profile: PlatformProfile):
    profile = PlatformProfile.model_validate(profile)
    issues = []
    if elf.entry != profile.reset_vector: issues.append("ELF entry differs from approved reset vector")
    if elf.flags != 0: issues.append("Nonzero ELF flags require unsupported ABI/extension review for this frozen RV32I baseline")
    for index, first in enumerate(elf.load_segments):
        for second in elf.load_segments[index+1:]:
            for field in ("virtual_address", "physical_address"):
                start, other = getattr(first, field), getattr(second, field)
                if first.memory_size and second.memory_size and max(start, other) < min(start + first.memory_size, other + second.memory_size):
                    issues.append("Overlapping ELF load memory ranges are unsupported by the frozen platform: " + field)
    for segment in elf.load_segments:
        for address in (segment.virtual_address, segment.physical_address):
            if not profile.ram_start <= address <= address + segment.memory_size <= profile.ram_end_exclusive:
                issues.append("ELF load segment is outside approved RAM")
    if not any(segment.flags & 1 and segment.virtual_address <= elf.entry < segment.virtual_address + segment.file_size for segment in elf.load_segments):
        issues.append("Entry is not backed by an executable load segment's file bytes")
    return LayoutCheck(verdict="mismatch" if issues else "layout_agrees" if elf.load_segments else "inconclusive",
                       profile_hash=canonical_hash(profile), elf_hash=elf.provenance.source_sha256, issues=tuple(issues))


def prepare_seeded_decode_case(contract: RegisterContract, header: bytes, rtl: bytes, output_dir: Path, *, seed: int, byte_base: int):
    """A labelled synthetic source discrepancy, never firmware/core execution evidence.

    Correct original -> seeded bad base -> reviewed-intent candidate. The candidate
    restores only one literal header definition; original and bad base stay intact.
    """
    contract = RegisterContract.model_validate(contract)
    if type(seed) is not int or not 0 <= seed < 2**32: raise ValueError("seed must be uint32")
    names = tuple(row.header_macro for row in contract.registers)
    hardware = extract_rtl_decode(rtl, "registers.sv", byte_base=byte_base, origin="synthetic_fixture")
    original = extract_header(header, "original.h", names, origin="synthetic_fixture")
    baseline = compare_register_facts(contract, original, hardware)
    if baseline.verdict != "facts_agree": raise ValueError("seed control must independently agree with reviewed map")
    choice = random.Random(seed).choice(contract.registers)
    # Fixed bounded seed mutation, deliberately outside every reviewed register.
    wrong = choice.address ^ 0x100
    if any(row.address == wrong for row in contract.registers): raise ValueError("seed mutation collides with reviewed map")
    text = header.decode("utf-8")
    pattern = r"(?m)^(\s*#define\s+" + re.escape(choice.header_macro) + r"\s+)([^\r\n]+)"
    mutant, count = re.subn(pattern, lambda match: match[1] + f"0x{wrong:08x}u", text)
    if count != 1: raise ValueError("candidate edit must touch exactly one definition")
    bad = mutant.encode("utf-8")
    candidate = header
    observed = compare_register_facts(contract, extract_header(bad, "seeded.h", names, origin="synthetic_fixture"), hardware)
    validated = compare_register_facts(contract, extract_header(candidate, "candidate.h", names, origin="synthetic_fixture"), hardware)
    if observed.verdict != "mismatch" or validated.verdict != "facts_agree": raise ValueError("seed/candidate source checks did not distinguish")
    output = Path(output_dir).resolve()
    output.mkdir(parents=True, exist_ok=True)
    if any(output.iterdir()): raise ValueError("evidence directory must be empty")
    files = {"original.h": header, "seeded.h": bad, "candidate.h": candidate, "registers.sv": rtl,
             "contract.json": contract.model_dump_json(indent=2).encode(),
             "candidate.diff": "".join(difflib.unified_diff(mutant.splitlines(keepends=True), text.splitlines(keepends=True), fromfile="seeded.h", tofile="candidate.h")).encode(),
             "before.json": observed.model_dump_json(indent=2).encode(), "after.json": validated.model_dump_json(indent=2).encode()}
    manifest = {"schema_version": "firmware.seeded-case.v1", "origin": "synthetic_fixture", "execution_status": "not_run",
        "seed": seed, "selected_register": choice.logical_name, "contract_hash": canonical_hash(contract),
        "candidate_base_hash": sha(bad), "candidate_hash": sha(candidate), "original_hash": sha(header),
        "checker_hash": sha(Path(__file__).read_bytes()), "extractor_hash": sha(Path(__file__).with_name("extract.py").read_bytes()),
        "files": {name: {"sha256": sha(data), "size_bytes": len(data)} for name, data in files.items()},
        "limitations": ["Parser-level address checks only; candidate is a curated restoration, no compiler/core/tool execution.", "Actual ISA/memory/core assumptions must be confirmed by the RTL owner before executable firmware selection."]}
    for name, data in files.items(): (output / name).write_bytes(data)
    (output / "manifest.json").write_bytes(json.dumps(manifest, indent=2).encode())
    return manifest
