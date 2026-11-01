"""Internal fixed firmware Verilator job. Launch only with main's resource grant.

Controller first compiles the prepared sources with executable.commands, preserving
stdout/stderr/tool versions. This module has no shell or selectable build flags.
The parent owns the wall-time/process-tree limit and runs check_observation.
"""
import json
from pathlib import Path
import sys
from .contracts import PlatformProfile
from .executable import FirmwarePreparation, ROOT
from .extract import extract_elf, sha
from .workflow import check_elf_layout


def validate_snapshot(root):
    root = Path(root).resolve()
    manifest = json.loads((root / "preparation.json").read_text())
    plan = FirmwarePreparation.model_validate(manifest["preparation"])
    expected_sources = {"start.S", "independent_probe.S", "main.c", "link.ld", "native_registers.sv", "firmware_top.sv",
                        "picorv32.v", "picorv32_top.sv", "registers.h", "original-bad-registers.h", "register-contract.json", "address-check.json"}
    if plan.fixture_id == "fw-regmap-offset-candidate-v1":
        expected_sources.add("candidate.diff")
    if set(manifest["source_files"]) != expected_sources:
        raise ValueError("snapshot manifest must identify the exact curated source set")
    for field, name in (("preparer_hash", "executable.py"), ("fact_extractor_hash", "extract.py"),
                        ("address_checker_hash", "workflow.py")):
        if manifest[field] != sha(Path(__file__).with_name(name).read_bytes()):
            raise ValueError("validator identity changed: " + field)
    for name, identity in manifest["source_files"].items():
        if Path(name).name != name:
            raise ValueError("snapshot path must be a basename")
        data = (root / name).read_bytes()
        if sha(data) != identity["sha256"] or len(data) != identity["size_bytes"]:
            raise ValueError("snapshot source identity changed: " + name)
    for name in ("harness.py", "sim_test.py", "sim_job.py"):
        if sha(Path(__file__).with_name(name).read_bytes()) != manifest["runtime_files"][name]:
            raise ValueError("runtime/checker identity changed: " + name)
    for name in ("memory.py", "profile.py", "contracts.py"):
        if sha((ROOT / "src/veriforge/riscv" / name).read_bytes()) != manifest["shared_runtime_files"][name]:
            raise ValueError("read-only shared runtime identity changed: " + name)
    elf_data = (root / "firmware.elf").read_bytes()
    binary = (root / "firmware.bin").read_bytes()
    elf = extract_elf(elf_data, "firmware.elf")
    if check_elf_layout(elf, PlatformProfile()).verdict != "layout_agrees":
        raise ValueError("ELF layout outside frozen platform")
    if not binary or len(binary) > 0xE000:
        raise ValueError("binary violates linker image bounds")
    expected = bytearray(len(binary))
    for segment in elf.load_segments:
        if segment.virtual_address != segment.physical_address or segment.physical_address + segment.memory_size > 0x8000E000:
            raise ValueError("ELF aliases or reserved memory unsupported")
        offset = segment.physical_address - 0x80000000
        if segment.file_size:
            if offset + segment.file_size > len(binary):
                raise ValueError("binary omits ELF bytes")
            expected[offset:offset+segment.file_size] = elf_data[segment.offset:segment.offset+segment.file_size]
    if bytes(expected) != binary:
        raise ValueError("binary differs from ELF load bytes/zero gaps")
    return plan, {"elf_sha256": sha(elf_data), "binary_sha256": sha(binary), "preparation_sha256": sha((root / "preparation.json").read_bytes())}


def build_recipe(root):
    root = Path(root).resolve()
    return dict(sources=[root / name for name in ("picorv32.v", "picorv32_top.sv", "native_registers.sv", "firmware_top.sv")],
                defines={"RISCV_FORMAL": 1}, hdl_toplevel="firmware_top", build_dir=root / "build",
                build_args=["-Wno-fatal"])


def main():
    # Only the controller-owned preparation directory is accepted, never job flags.
    root = Path(sys.argv[1]).resolve()
    plan, identities = validate_snapshot(root)
    from cocotb_tools.runner import get_runner
    runner = get_runner("verilator")
    runner.build(**build_recipe(root))
    job = {"preparation": plan.model_dump(mode="json"), "binary": str(root / "firmware.bin"), "trace": str(root / "observation.json")}
    (root / "simulation-job.json").write_text(json.dumps(job, indent=2))
    (root / "execution-identities.json").write_text(json.dumps(identities, indent=2))
    runner.test(hdl_toplevel="firmware_top", test_module="veriforge.firmware.sim_test", build_dir=root / "build",
                test_dir=root, extra_env={"VF_FIRMWARE_JOB": str(root / "simulation-job.json")})


if __name__ == "__main__":
    main()
