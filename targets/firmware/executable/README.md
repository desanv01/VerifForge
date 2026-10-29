# Curated executable firmware preparation

Status: trusted C/assembly/linker/peripheral source prepared on 29 October 2026. **Not compiled or run.** Current evidence is pure preparation and parser checks; real firmware/MMIO failure, correction and replay are pending an explicit shared tool slot and harness integration. These new files are separate from the earlier synthetic parser fixtures and are excluded from that foundation's acceptance claim.

## Fixed system and sources

Use main-approved PicoRV32 `ef203c2b0a3fb793280f5114941416c425c5b461`, RV32I/ILP32 little endian, zeroed 64 KiB RAM at 0x80000000. `start.S` sets SP to 0x8000fef0, clears word-aligned BSS, calls the C scenario, and writes its returned value to 0x8000ff00. Mailbox 1 means scenario self-check success; 2 means a self-check failure. Reset/ELF entry is 0x80000000. The linker keeps the image below 0x8000e000, reserving space below the fixed stack and mailbox. ELF loading must initialize .data at matching VMA/LMA; no runtime ROM copy assumption is hidden.

`main.c` writes/readbacks CONTROL, writes STATUS and checks it using `independent_probe.S`, then tests DATA byte lane 1 and SCRATCH. The hand-authored probe encodes 0x40000004 directly and includes no register header. This prevents a bad header and its own readback from agreeing with the same incorrect address. C's byte write replaces lane 1 of AABBCCDD with 11, requiring AABB11DD; byte-strobe behavior is a future live check, not established by source inspection.

Curated selections:

| ID | Header | Intended actual outcome after integration |
|---|---|---|
| fw-regmap-control-v1 | control/registers.h | Mailbox 1; address requirements and neighboring readbacks agree |
| fw-regmap-offset-bug-v1 | offset_bug/registers.h | STATUS aliases DATA at 0x40000008; third accepted MMIO transaction violates reviewed STATUS address 0x40000004; independent probe reads reset-zero STATUS; mailbox 2 |
| fw-regmap-offset-candidate-v1 | candidate/registers.h | One definition restored to 0x40000004; mailbox 1 and unchanged neighbor tests pass |

The candidate is a curated one-line header correction against the preserved offset_bug source. It is neither model-generated nor compiled/validated yet. `prepare_executable_case` retains the bad original in every snapshot, emits a candidate diff for the candidate variant, and never edits any source fixture.

`native_registers.sv` proposes the actual four-word RTL peripheral at 0x40000000/04/08/0c. Address decoding supplies one common register index to read and byte-lane write paths; reset clears all registers. The new bounded extractor reads these literal branches independently of the C headers and reviewed JSON relationships. It verifies only source address/index agreement, not live RTL semantics. No native-to-AXI bridge is needed by this proposed native peripheral; an existing AXI fixture is not silently treated as connected hardware.

## Preparation and fixed compiler recipe

Local API (source copying only):

```python
from pathlib import Path
from veriforge.firmware.executable import FirmwarePreparation, prepare_executable_case
record = prepare_executable_case(
    FirmwarePreparation(fixture_id="fw-regmap-offset-bug-v1"),
    Path("a-fresh-controller-owned-attempt-directory"),
)
```

The frozen plan permits only three curated IDs and bounded retirement/cycle/wall limits (defaults 4096 / 200000 / 120s). It accepts no source paths, extra flags, commands or scripts. Output includes exact source bytes, independent address-check report, contract/profile identities, preparer/extractor/checker hashes, bad-base/header hashes and fixed argv recipes. All records say `not_run` and `prepared_not_executed`. Output paths are chosen by the trusted controller; this is not an API runner or arbitrary process capability.

The fixed compiler is `riscv64-unknown-elf-gcc`. Run the returned recipe in order: `assemble_start`, `assemble_probe`, `compile_main`, then `compiler` (link), followed by the binutils steps. Compile the three inputs separately with `-c` to fixed `start.o`, `independent_probe.o`, and `main.o` basenames. Compile steps retain the RV32I/ILP32/no-relax/freestanding/optimization/warning flags below, omit link-only `-Wl,*` and `-nostdlib/-nostartfiles`, and add `-I <snapshot>` for main.c. The link step is:

```text
-march=rv32i -mabi=ilp32 -mno-relax -nostdlib -nostartfiles -ffreestanding
-fno-builtin -fno-pic -fno-pie -fno-stack-protector -msmall-data-limit=0
-Os -Wall -Wextra -Werror -Wl,--no-relax -Wl,--build-id=none
-I <snapshot> -T <snapshot>/link.ld
<snapshot>/start.o <snapshot>/independent_probe.o <snapshot>/main.o
-Wl,-Map=<snapshot>/firmware.map -o <snapshot>/firmware.elf
```

Then trusted fixed recipes use objcopy `-O binary`, objdump `-d -M no-aliases,numeric` and readelf `-h -l -A`. Their actual tool versions, image digest, exit codes, stderr and output bytes must be retained. These commands have not been executed here. There is no Spike command in this preparation: its current RAM-only environment does not model this MMIO peripheral.

The separate object steps avoid asking the GCC driver to give the linker random temporary object names, which can leak into ELF file symbols. This adopts the same source-level preparation as the updated RAM-only smoke runner without importing its smoke recipe or editing any RISC-V module. Fixed filenames alone do not prove deterministic ELF output. First demonstrate actual repeatability under pinned tools, then replay the original saved executable without recompiling; retain object/ELF/binary hashes separately.

Official [GCC RISC-V options](https://gcc.gnu.org/onlinedocs/gcc/RISC-V-Options.html) and [GNU ld output-section syntax](https://sourceware.org/binutils/docs/ld/Output-Section-Description.html) were consulted. Actual selected toolchain acceptance remains required; source preparation does not prove these files link or fit the chosen bounds.

## Initial integration requirements (now implemented in source)

The separate firmware harness below implements these requirements in source. The accepted RAM-only RISC-V harness remains intact:

1. Instantiates this actual peripheral beside the pinned core and routes only selected aligned non-instruction MMIO requests to it. Mux RAM vs MMIO READY/RDATA; unmapped accesses fail visibly or time out within bounds. Ensure memory writes occur once per accepted valid/ready edge and reset cancels requests.
2. Preserves raw RVFI retirement and RAM completion metadata. The existing Ram helper rejects this MMIO window; merely loading this binary into it is not integration.
3. Logs each accepted native MMIO event with aligned bus address, read/write, WDATA/RDATA, WSTRB and cycle/PC correlation. Byte address 0x40000009 is normalized as aligned bus address 0x40000008 with lane-1 strobe; retain the original RVFI effective address separately. Do not generate a trace from the C header or expected scenario.
4. Independently checks real peripheral read/write/reset/strobe behavior and the exact external transaction sequence. Existing firmware MMIO address importer is not a complete peripheral scoreboard. Use the reviewed addresses/readback constants as requirements; do not reuse the DUT register implementation to compute its own expected behavior.
5. Captures ELF/program/header/native RTL/config/checker/contract identities; selects only these trusted compiled programs. No planner-uploaded source or extra shell command.

This was requested through main, not implemented by changing another owner's RISC-V/core files. No cross-path change or real execution slot is assumed.

## Finite actual acceptance recipe after a grant

- First verify the toolchain image and fixed source snapshot. Compile three fresh variants with the argv above, keeping original/candidate snapshots distinct. Validate ELF entry/ranges/flags and independently corroborate actual disassembly encodings against ELF bytes. Compiler ISA flags and e_flags alone do not establish complete instruction support.
- Run the control on actual PicoRV32 plus actual peripheral under bounds; require mailbox 1, no trap and complete accepted MMIO/peripheral checks, including the byte-lane/neighbor checks. Retain all raw and normalized traces.
- Run the offset defect without changing the checker: require actual STATUS write address 0x40000008 against 0x40000004, independent status probe value zero versus 0x55667788, and mailbox 2. A timeout/build/reference failure is not a register mismatch.
- Validate the candidate against the exact bad base hash and unchanged contract/checker; require mailbox 1 and control/byte-strobe/neighbor behavior. Preserve the original bad ELF/header and first differing access/retirement.
- Replay the bad and corrected saved binaries/configuration without compiling or inference, checking their original hashes and matching semantic results. A repeat compilation alone is not exact executable replay.
- Spike device/reference coverage must either use an actual reviewed device model or remain explicitly unsupported for MMIO. The core-only Spike gate does not complete this firmware gate.

Pure tests validate only preparation, one-line correction, source hashes, independently extracted decode, schema/argv bounds and absence of implicit RAM MMIO support. They do not count as any of the actual steps above.

## Firmware harness source and exact simulator recipe

`firmware_top.sv` instantiates the read-only pinned `picorv32_top` and actual `firmware_native_registers`. It reserves the entire MMIO window, selects only the reviewed aligned registers, refuses instruction MMIO, and permits host RAM READY only for aligned RAM addresses. Invalid requests cannot fall through to RAM. Peripheral writes occur synchronously at valid/ready acceptance.

`veriforge.firmware.sim_test.collect` zeroes RAM through the existing read-only helper, holds reset for five cycles, drives only RAM responses, samples settled valid/ready before each rising edge, and records each accepted transfer once. RTL supplies peripheral responses. Raw WDATA/RDATA/WSTRB, instruction/data classification, cycle and sequence are retained independently of raw RVFI fields. RVFI retains instruction/rs1 values needed to reconstruct effective byte addresses, while native addresses remain aligned. Collection stops on retired mailbox writes (including failure value 2), trap, protocol error, or cycle/retirement/transfer bounds. It does not generate expected transactions.

`veriforge.firmware.harness.check_observation` checks ordering, reset/PC/RAM/MMIO bounds, bus-to-retirement correspondence, write lanes, full-word mailbox 1/2 and completion. Its immutable Python literal requirements and independent four-register scoreboard use neither the selected C header nor RTL decode. A completed incorrect sequence/failure mailbox is a design mismatch; missing, inconsistent or bounded incomplete evidence cannot pass or establish a defect. Passing is limited to this curated MMIO firmware contract; it does not establish full CPU ISA correctness or Spike MMIO agreement.

Preparation now snapshots core, core wrapper and firmware wrapper, checks the pinned upstream Git blob identity, and records hashes of firmware driver/checker/job plus read-only shared runtime dependencies. After main grants a resource slot, the controller must first run the fixed compiler/binutils recipes, retain exact output and tool versions, and validate disassembly bytes/ISA. The simulator entry point is fixed:

```text
python -m veriforge.firmware.sim_job <prepared-controller-directory>
```

The job validates all snapshot/runtime hashes, ELF entry/flags/segments/reserved memory and exact flat binary load bytes before importing the simulator runner. `build_recipe` fixes `get_runner('verilator')`, source order `picorv32.v`, `picorv32_top.sv`, `native_registers.sv`, `firmware_top.sv`, define `RISCV_FORMAL=1`, top `firmware_top`, build directory `<snapshot>/build`, build args `['-Wno-fatal']`; `runner.test` fixes test module `veriforge.firmware.sim_test`, top/build/test directories and internal job environment. No planner-supplied flags or commands exist.

The trusted parent must enforce the plan's wall timeout and process-tree settlement, capture Verilator/compiler/cocotb/Python versions and image digest, preserve stdout/stderr/exit codes and raw observation hash, and call `check_observation` outside the simulator. The internal job is not a public runner/API or complete evidence exporter. Actual compilation, Verilator acceptance, simulation and saved-binary replay remain unperformed and gated. The pure suite uses handwritten synthetic ELF/evidence and a fake scheduler; none are actual execution evidence.
