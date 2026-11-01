"""Trusted executable preparation only. No compiler or core process is invoked."""
import difflib
import json
from pathlib import Path
import re
from typing import Annotated, Literal
from pydantic import Field, StrictInt
from veriforge.contracts import WireModel, canonical_hash
from .contracts import AddressFact, PlatformProfile, RegisterContract
from .extract import _without_comments, bounded, extract_header, provenance, sha
from .workflow import compare_register_facts

ROOT = Path(__file__).resolve().parents[3]
FIXTURE_ROOT = ROOT / "targets/firmware/executable"
FIXTURES = {"fw-regmap-control-v1": "control", "fw-regmap-offset-bug-v1": "offset_bug", "fw-regmap-offset-candidate-v1": "candidate"}
FLAGS = ("-march=rv32i", "-mabi=ilp32", "-mno-relax", "-nostdlib", "-nostartfiles", "-ffreestanding",
         "-fno-builtin", "-fno-pic", "-fno-pie", "-fno-stack-protector", "-msmall-data-limit=0",
         "-Os", "-Wall", "-Wextra", "-Werror", "-Wl,--no-relax", "-Wl,--build-id=none")


class FirmwarePreparation(WireModel):
    schema_version: Literal["firmware.executable-prep.v1"] = "firmware.executable-prep.v1"
    fixture_id: Literal["fw-regmap-control-v1", "fw-regmap-offset-bug-v1", "fw-regmap-offset-candidate-v1"]
    max_retired: Annotated[StrictInt, Field(ge=256, le=10000)] = 4096
    max_cycles: Annotated[StrictInt, Field(ge=10000, le=1000000)] = 200000
    wall_time_seconds: Annotated[float, Field(gt=0, le=300, strict=True)] = 120.0


def extract_native_decode(data: bytes, source="native_registers.sv"):
    """Extract the actual proposed native peripheral's literal address/index map."""
    text = _without_comments(bounded(data).decode())
    if "`" in text: raise ValueError("native RTL preprocessing unsupported")
    rows, active, seen = [], False, False
    for line, raw in enumerate(text.splitlines(), 1):
        raw = raw.strip()
        if raw == "case (mem_addr)":
            if seen: raise ValueError("multiple native decode cases")
            active = seen = True
            continue
        if not active: continue
        if raw == "endcase": active = False; continue
        if raw in ("", "default: begin end"): continue
        match = re.fullmatch(r"32'h([0-9a-fA-F]{8}): begin register_index = ([0-3]); mem_rdata = registers\[([0-3])\]; selected = 1; end", raw)
        if not match or match[2] != match[3]: raise ValueError("unsupported or inconsistent native decode branch")
        address, index = int(match[1], 16), int(match[2])
        if any(row.name == f"register_{index}" or row.value == address for row in rows): raise ValueError("ambiguous native register decode")
        proof = provenance(data, source, f"line:{line}", "imported_artifact").model_copy(update={"conditions": ("native literal decode subset; address/index extraction only, not behavioral verification",)})
        rows.append(AddressFact(name=f"register_{index}", value=address, provenance=proof))
    if active or len(rows) != 4: raise ValueError("exactly four complete native decode branches required")
    return tuple(rows)


def commands(output_dir: Path):
    """Fixed argv only, using an already-prepared controller-owned directory."""
    root = Path(output_dir).resolve()
    # Stable object names avoid GCC's random temporary object FILE symbols in ELF.
    # Actual repeatability still needs the separately authorized toolchain gate.
    compile_flags = tuple(flag for flag in FLAGS if not flag.startswith("-Wl,") and flag not in ("-nostdlib", "-nostartfiles"))
    return {
        "assemble_start": ("riscv64-unknown-elf-gcc", *compile_flags, "-c", str(root / "start.S"), "-o", str(root / "start.o")),
        "assemble_probe": ("riscv64-unknown-elf-gcc", *compile_flags, "-c", str(root / "independent_probe.S"), "-o", str(root / "independent_probe.o")),
        "compile_main": ("riscv64-unknown-elf-gcc", *compile_flags, "-I", str(root), "-c", str(root / "main.c"), "-o", str(root / "main.o")),
        "compiler": ("riscv64-unknown-elf-gcc", *FLAGS, "-I", str(root), "-T", str(root / "link.ld"),
                     str(root / "start.o"), str(root / "independent_probe.o"), str(root / "main.o"),
                     "-Wl,-Map=" + str(root / "firmware.map"), "-o", str(root / "firmware.elf")),
        "binary": ("riscv64-unknown-elf-objcopy", "-O", "binary", str(root / "firmware.elf"), str(root / "firmware.bin")),
        "disassembly": ("riscv64-unknown-elf-objdump", "-d", "-M", "no-aliases,numeric", str(root / "firmware.elf")),
        "elf_headers": ("riscv64-unknown-elf-readelf", "-h", "-l", "-A", str(root / "firmware.elf")),
    }


def prepare_executable_case(preparation: FirmwarePreparation, output_dir: Path):
    preparation = FirmwarePreparation.model_validate(preparation)
    contract_bytes = (FIXTURE_ROOT / "register-contract.json").read_bytes()
    contract = RegisterContract.model_validate_json(contract_bytes)
    header = (FIXTURE_ROOT / FIXTURES[preparation.fixture_id] / "registers.h").read_bytes()
    bad_base = (FIXTURE_ROOT / "offset_bug/registers.h").read_bytes()
    rtl = (FIXTURE_ROOT / "native_registers.sv").read_bytes()
    software = extract_header(header, "registers.h", tuple(row.header_macro for row in contract.registers))
    comparison = compare_register_facts(contract, software, extract_native_decode(rtl))
    expected = "mismatch" if preparation.fixture_id == "fw-regmap-offset-bug-v1" else "facts_agree"
    if comparison.verdict != expected: raise ValueError("curated preparation contradicts reviewed address requirements")
    if preparation.fixture_id.endswith("candidate-v1") and len(comparison.issues): raise ValueError("candidate address validation failed")
    root = Path(output_dir).resolve()
    root.mkdir(parents=True, exist_ok=True)
    if any(root.iterdir()): raise ValueError("preparation directory must be empty")
    files = {name: bounded((FIXTURE_ROOT / name).read_bytes()) for name in ("start.S", "independent_probe.S", "main.c", "link.ld", "native_registers.sv", "firmware_top.sv")}
    from veriforge.riscv.profile import CORE_REVISION, CORE_BLOB
    import hashlib
    core = bounded((ROOT / "targets/riscv/upstream/picorv32.v").read_bytes())
    if hashlib.sha1(b"blob " + str(len(core)).encode() + b"\0" + core).hexdigest() != CORE_BLOB:
        raise ValueError("pinned core source identity differs")
    files["picorv32.v"] = core
    files["picorv32_top.sv"] = bounded((ROOT / "targets/riscv/picorv32_top.sv").read_bytes())
    files.update({"registers.h": header, "original-bad-registers.h": bad_base, "register-contract.json": contract_bytes,
                  "address-check.json": comparison.model_dump_json(indent=2).encode()})
    if preparation.fixture_id.endswith("candidate-v1"):
        files["candidate.diff"] = "".join(difflib.unified_diff(bad_base.decode().splitlines(keepends=True), header.decode().splitlines(keepends=True), fromfile="offset_bug/registers.h", tofile="candidate/registers.h")).encode()
    metadata = {"schema_version": "firmware.executable-preparation.v1", "execution_status": "not_run",
        "preparation": preparation.model_dump(mode="json"), "profile": PlatformProfile().model_dump(mode="json"),
        "profile_hash": canonical_hash(PlatformProfile()), "contract_hash": canonical_hash(contract),
        "candidate_provenance": "curated_header_offset_restoration" if preparation.fixture_id.endswith("candidate-v1") else None,
        "original_bad_hash": sha(bad_base), "selected_header_hash": sha(header),
        "preparer_hash": sha(Path(__file__).read_bytes()),
        "fact_extractor_hash": sha(Path(__file__).with_name("extract.py").read_bytes()),
        "address_checker_hash": sha(Path(__file__).with_name("workflow.py").read_bytes()),
        "core_revision": CORE_REVISION,
        "runtime_files": {name: sha(Path(__file__).with_name(name).read_bytes()) for name in ("harness.py", "sim_test.py", "sim_job.py")},
        "shared_runtime_files": {name: sha((ROOT / "src/veriforge/riscv" / name).read_bytes()) for name in ("memory.py", "profile.py", "contracts.py")},
        "source_files": {name: {"sha256": sha(value), "size_bytes": len(value)} for name, value in files.items()},
        "recipes": commands(root), "recipe_status": "prepared_not_executed",
        "expected_observation": "First conflicting accepted MMIO transaction is STATUS write: address0x40000008 versus independent requirement0x40000004. No actual transaction observed yet.",
        "limitations": ["No compilation/core/MMIO run, no portable replay or accepted repair yet.", "RAM-only Spike baseline is not a reference for this peripheral."]}
    for name, value in files.items(): (root / name).write_bytes(value)
    (root / "preparation.json").write_bytes(json.dumps(metadata, indent=2).encode())
    return metadata
