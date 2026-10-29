# Firmware and register facts — U5 source groundwork

Status: source extraction and synthetic fixture checks only, 29 October 2026. Initial 41 pure tests passed; after independent review corrections, **51 pure tests passed in 1.09s**. No firmware was compiled, disassembled by a tool, booted, simulated or replayed. No Execute/Repair acceptance is claimed. The source-only candidate is a curated restoration, not a model-generated or live-validated repair.

## Approved platform and outstanding execution dependency

Main confirmed PicoRV32 revision `ef203c2b0a3fb793280f5114941416c425c5b461` and independent Spike revision `4c6a9bc79dab80616932949707e7c0471d8e9e54`. Baseline is RV32I, little endian, one hart, 32 zeroed integer registers; counters/IRQ/PCPI/M/C disabled. RAM is `[0x80000000,0x80010000)`, reset/ELF entry 0x80000000, completion mailbox 0x8000ff00 written with 1. Firmware initializes SP below the mailbox. Compile flags are `-march=rv32i -mabi=ilp32 -mno-relax -nostdlib -nostartfiles`, with linker relaxation disabled. These are approved assumptions recorded in local `PlatformProfile`, not observed execution or a shared system manifest.

The approved MMIO window is `[0x40000000,0x40000010)`: four aligned 32-bit RW registers at offsets 0/4/8/12, reset zero, per-byte write strobes, no assumed side effects. PicoRV32 uses its native valid/ready 32-bit bus with byte write strobes. A native-to-AXI bridge and actual attached peripheral are **not present by implication**. The first Spike core/reference gate excludes MMIO; do not call it peripheral validation. Any Spike MMIO comparison requires a real modeled device or a separately approved independent transaction checker.

Native read-only command discovery found no `readelf`, `objdump`, `llvm-readelf`, `llvm-objdump`, `riscv64-unknown-elf-gcc` or `riscv32-unknown-elf-gcc` in this chat's PATH. This is a narrow PATH inventory, not proof that tools are absent from Docker/WSL/other authorized environments. No tools installed or launched by this task.

## Local interface

`veriforge.firmware` exports `RegisterContract`, `PlatformProfile`, fact records, extractors, `compare_register_facts`, `compare_mmio_addresses`, `check_elf_layout`, `corroborate_instruction_bytes` and `prepare_seeded_decode_case`. These are additive domain-local schemas; shared contracts, project manifests, API, registry and dependency locks are unchanged.

Every extracted fact carries original bytes' SHA-256, source identifier, line/byte/JSON location, extractor version, origin (`synthetic_fixture` or `imported_artifact`), unreviewed extraction state and applicable conditions. Reviewed register identity explicitly relates logical register, header macro, RTL signal and intended byte address. Extracted facts do not silently become approved requirements. Source labels are metadata, never input file paths that the parser opens or commands to execute.

## Supported extraction and limits

- Header: strict numeric object-like defines, prior resolved macros, parentheses, addition/subtraction and integer C suffixes. Caller selects up to 64 address macros. Comments are removed while retaining line positions; inactive commented definitions cannot become facts. Conditional preprocessing, undef/include/other directives, continuations, duplicate definitions, calls, shifts, nonnumeric/unresolved macros and out-of-range arithmetic fail closed. This is not a C preprocessor; include guards/conditional headers require an explicit reviewed supported subset rather than silent branch selection.
- RTL: one `case (addr)` with literal 8/16/32-bit hex address branches assigning `register <= write_data;` on one line, plus `default: ;`. Caller explicitly supplies the byte base, which is retained as a condition. Comments are erased before parsing; backtick preprocessing/macros are unsupported. Unknown expressions, multiple cases, duplicate branches/signals, unterminated cases and truncating literals are rejected. This extracts only addresses; it does not verify actual write enable, clock, strobe, access, reset or side-effect behavior. Real AXI/SoC RTL must have a separately reviewed extractor for its actual syntax before a cross-domain claim.
- ELF: bounded ELF32 little-endian ET_EXEC for EM_RISCV=243, raw flags and loadable program-header ranges, permissions and file-content hashes. Bounds/alignment are checked. Section headers, symbols, relocation/attribute contents and every ELF feature are not supported or validated. `check_elf_layout` checks entry, raw baseline flags and RAM ranges against main's frozen profile, plus executable file-backed entry. Overlapping nonempty PT_LOAD memory ranges in either virtual or physical space fail this profile's layout check, including overlap into a zero-fill tail. No loader ordering or identical-byte exception is guessed; this is a conservative supported-profile restriction, not a statement that all overlapping ELF files violate the general format. It does not infer complete ISA support or boot success from e_flags=0.
- Disassembly: GNU-style address + 16/32-bit hex encoding + mnemonic/operands, up to 4096 rows. Facts preserve the printed text without trusting mnemonics as ISA validation. `corroborate_instruction_bytes` independently requires each printed encoding to match uniquely mapped ELF load bytes. It does not prove that an instruction retired, that every executable byte was disassembled, or that unsupported extensions are absent.
- MMIO: versioned JSON transaction import with strict contiguous sequence, read/write, absolute address, data, byte strobe and optional PC. Duplicate JSON keys and malformed records are rejected. An explicit reviewed ordered association maps transaction positions to logical registers; labels/PC equality are not guessed. Missing/extra transactions are inconclusive. This compares addresses only; data, strobes, access and temporal semantics require a real independent peripheral contract checker.

Each artifact is at most 1 MiB, register relationships at most 64, program headers at most 128 and instruction/MMIO lists at most 4096. Unsupported/malformed artifact input raises a validation error; it is never automatically a DUT defect. Source comparisons return `facts_agree`, `mismatch` or `inconclusive` with `execution_status=not_run`. They cannot fabricate common AttemptResult execution/tool provenance.

## Seeded mismatch and candidate preservation

`prepare_seeded_decode_case(contract, header_bytes, rtl_bytes, empty_output_dir, seed=..., byte_base=...)` first checks independently parsed header and RTL addresses against reviewed intent. A bounded seeded selection changes exactly one header definition to a conflicting address, then creates a candidate restoring the preserved original. Outputs include original, bad base, candidate, unchanged RTL, contract, exact diff, before/after source reports and hash manifest. All writes use fixed filenames and exact bytes in a fresh empty directory; no source path is modified and no compiler/job is invoked.

The manifest binds seed, selected register, exact original/base/candidate/file identities, canonical contract, checker and extractor source identities. Tests verify deterministic selection, one mismatch, candidate address agreement and every saved file hash. This demonstrates a parser-level workflow on explicitly synthetic fixtures, not the sequence-3 executable integration gate. Matching generated header/map copies alone is insufficient; the separately parsed RTL artifact is retained, and a deliberately wrong RTL branch is detected even when header and reviewed intent agree.

## Actual verification

Initial source command (before independent review corrections):

```powershell
.venv/Scripts/python.exe -m pytest tests/firmware -q --basetemp=.coordination/U5-firmware-tests4 --tb=short --junitxml=.coordination/deliveries/U5-FIRMWARE-FACTS.junit.xml
```

Initial result: 41 passed, exit 0, no warnings. Independent review then found inactive/unsupported source selection and ambiguous overlapping ELF load mappings. The owner corrected those two issues and added focused regressions:

```powershell
.venv/Scripts/python.exe -m pytest tests/firmware -q --basetemp=.coordination/U5-firmware-review-fixes --tb=short --junitxml=.coordination/deliveries/U5-FIRMWARE-FACTS-fix.junit.xml
```

Corrected owner result: **51 passed in 1.09s**, no warnings/skips. Main separately received an independent **25-case** review and **six exact correction checks**; those are separate receipts, not a summed project-wide count. Includes handwritten ELF layout/byte constants, malformed/unsupported bounds, independent source mismatch, all saved evidence hashes, comment/preprocessing/injection rejection, overlapping VA/PA/zero-fill memory ranges, disassembly-to-ELF contradiction, exact MMIO association and preserved seeded base/candidate. All tests are source/fixture tests; no actual tool or core output is used.

## Exact prerequisites for actual firmware/core replay

1. U4 supplies accepted core/reference source, configurations and a proven base execution/retirement comparison with true tool versions and immutable pins. Main grants one serial build/simulator slot.
2. Verify an available cross-compiler/binutils in that authorized environment; freeze actual versions and fixed commands. Build actual startup code/linker script at the approved RAM/reset map with flags above and `--no-relax`; initialize SP, .data/.bss and mailbox protocol explicitly. Preserve ELF, source, linker map, actual disassembly, compiler argv and hashes.
3. Supply a reviewed four-register DUT attachment with native valid/ready response semantics and byte strobes, or separately implement/review a bridge. Capture actual accepted bus transactions and architectural memory effects; do not infer them from this synthetic JSON or printed assembly.
4. Independently extract the actual compiled header, actual hardware decode and ELF layout. Implement unsupported real RTL/header syntax deliberately; do not silently broaden this parser or feed generated facts from one manifest back as independent evidence.
5. Reproduce one wrong-offset or decode mismatch on the actual core/peripheral, bind the earliest architectural/bus discrepancy to the conflicting sources, preserve the original bad revision and exact inputs.
6. Apply a narrowly reviewed candidate against that exact base; repeat executable firmware and affected peripheral/neighbor regressions with unchanged checker and contract. Record actual completion/timeout/harness/design outcomes separately. MMIO must be checked by a modeled device or independent register checker; Spike's RAM-only baseline does not establish device behavior.
7. Replay the saved ELF/initial RAM/config/stimulus without inference and validate immutable source/checker/contract/environment identities before claiming repair/replay support. Export/API/UI/system-manifest adoption are separately owned.

Primary format references checked: [ELF header](https://gabi.xinuos.com/elf/02-eheader.html) for class, machine, entry and table layout; [RISC-V ELF psABI](https://riscv-non-isa.github.io/riscv-elf-psabi-doc/) for RISC-V machine-specific flags/attributes context. The current psABI page identifies a pre-release development document; these parsers extract a narrow declared subset and do not claim complete ABI conformance.
