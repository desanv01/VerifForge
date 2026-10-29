# Experimental RV32I investigation baseline

Status on 29 October 2026: source implementation and corrections independently accepted. An authorized toolchain image build timed out at 1500.84 seconds while compiling Spike; its BuildKit record is Canceled and no finished image exists. In-image Python compatibility tests have not run. No actual target-program compiler, PicoRV32 simulator or Spike execution is claimed. Sequence 2 acceptance remains pending a completed tool image and actual control/mutant/replay evidence.

## Selection and provenance

The granted revised four-job build also failed to produce an image: its supervisor stopped the dedicated builder at224.375seconds after host available memory fell to532631552bytes (below512MiB). VM available memory remained9567956992bytes. The builder is independently confirmed exited/not running and the image absent. It stopped during package installation, before Spike compilation; the graceful-stop transport message is a consequence of intentional cancellation. Logs/cache are preserved; no further retry, compatibility test or core/reference job was started.

Read-only assessment located the user's SHAKTI source project under its relocated desktop project directory. Its E-class base simulation configuration selects RV64IMAC, BSV generation, AXI4-Lite, reset PC 4096 and several dependency repositories. The inspected base-simulation tree lacks generated Verilog/dependency outputs. C-class additionally selects floating point, privilege and MMU/cache behavior. These are valuable later targets but require a separate build and configuration effort before independent retirement comparison. Private source revision could not be established through the available git read. No private source was copied or modified.

The bounded baseline instead uses public [PicoRV32](https://github.com/YosysHQ/picorv32/tree/ef203c2b0a3fb793280f5114941416c425c5b461), ISC, as actual DUT RTL and independent [Spike](https://github.com/riscv-software-src/riscv-isa-sim/tree/4c6a9bc79dab80616932949707e7c0471d8e9e54), BSD-3-Clause, as architectural reference. PicoRV32 upstream is archived; the selected file is pinned and vendored byte-for-byte with its license header. `targets/riscv/upstream.json` records repositories, revisions, git blobs and SHA256 values. Spike source is fetched at its selected revision during the proposed image build; its license is preserved locally. No Python ISA emulator supplies expected outcomes.

## Frozen profile

| Property | Contract |
| --- | --- |
| Profile | `picorv32-rv32i-v1`; one hart, RV32I, little endian |
| Reset/entry | Synchronous active-low reset; ELF/PC `0x80000000` |
| Memory | Zeroed 64 KiB RAM, `[0x80000000,0x80010000)` |
| Registers | 32 initially zero GPRs; startup explicitly initializes SP `0x8000fef0` |
| Completion | Aligned 32-bit store of 1 to `0x8000ff00`, inside RAM |
| Bus | Pico native valid/ready, 32-bit words with four byte strobes |
| Compiler | `-march=rv32i -mabi=ilp32 -mno-relax -nostdlib -nostartfiles`, freestanding and linker relaxation disabled |
| Bounds | Default 256 retirements, 100000 DUT cycles, 120 seconds total job wall time |
| Exclusions | M/C, counters, IRQ, PCPI, CSR/privilege instructions, interrupts, MMIO and misaligned accesses |

The first mailbox-address store ends the compared prefix, including a wrong value/mask so a concrete mismatch can be checked. The hand-written smoke program exercises arithmetic, loads/stores, a branch and a signed-byte case, then writes completion; its fail path writes 2. The linker reserves scratch at `0x80000100` and asserts program sections do not overlap it. This is an intentionally narrow smoke workload, not full RV32I certification.

Future firmware peripheral agreement reserves `[0x40000000,0x40000010)` for four aligned 32-bit RW registers, zero reset and byte strobes. That peripheral is not implemented in the current Spike/RAM platform. Native-to-AXI integration and firmware acceptance require a separately assigned gate.

## Independent comparison and evidence

The model-facing plan selects only curated fixture/program/profile IDs and strict budgets. It cannot provide source paths, shell commands or scripts. The runner invokes fixed compiler, reference and cocotb job recipes. Control uses upstream RTL; the mutant enables upstream `PICORV32_TESTBUG_002`, which corrupts actual register-file writes. No mutation of expected traces or checker behavior is used.

The cocotb driver supplies only memory responses and records observed RVFI retirements. Spike executes the identical ELF in the fixed RAM map. The pinned Spike [`--pcs` option](https://github.com/riscv-software-src/riscv-isa-sim/blob/4c6a9bc79dab80616932949707e7c0471d8e9e54/spike_main/spike.cc) bypasses its boot ROM and starts the hart at the entry; `--instructions` bounds reference execution. Its [commit format](https://github.com/riscv-software-src/riscv-isa-sim/blob/4c6a9bc79dab80616932949707e7c0471d8e9e54/riscv/execute.cc) records register and memory effects. Load data is recovered from the destination register at architectural width; load-to-x0 is explicitly unsupported because this log omits read data.

The independent checker aligns contiguous retirement order and compares PC, instruction, register destination/value, trap/interrupt and architectural memory effects. It does not compare cycles. RVFI aligned full-word bus observations are normalized to effective architectural byte address/width; store masks are preserved so an extra lane cannot disappear. DUT timing is diagnostic metadata and is retained as `first_cycle` only when a mismatch is reported. Raw traces, normalized traces and first retirement divergence remain separate artifacts.

A completed matching mailbox prefix yields `pass_within_scope`. A concrete first architectural difference yields `design_mismatch`. Empty/truncated reference or DUT prefixes and absent completion cannot pass. Unsupported formats/ISA cases remain unsupported; build, harness, timeout, resource and cancellation outcomes are distinct from a design mismatch. Missing tools never produce fabricated traces.

Each successful comparison records exact stimulus (ELF/binary and experiment), source/checker manifests, profile, five evidence identity hashes, tool versions, Spike binary/revision/submodules, installed package hash, raw reference/RVFI traces, normalized pair, comparison, phase logs and shared typed result. The image inspect receipt must accompany actual acceptance. The Dockerfile alone does not freeze apt package resolution; retain the actual built image.

The image is a separate tool environment: cocotb 2.0.1, Pydantic 2.12.3 and pytest 8.4.2. Current application `uv.lock` and worker `.venv` instead resolve Pydantic 2.14.1 and pytest 9.1.1 (same cocotb 2.0.1). The pure-test result below used that worker environment; image compatibility still requires the focused suite inside the actual image. Shared dependency locks remain unchanged. Build context is restricted to `infra/riscv`, since no project files are copied into the image.

The simulator output envelope is strictly typed and bounded before comparison. Termination must agree with the observed final event and configured cycle/retirement limits; event cycles must increase and remain below the actual total. Unknown reasons, invalid/truncated envelopes, events after a stop and inconsistent completion/trap metadata produce infrastructure/harness failure. No phase is dispatched after the total wall deadline. RAM semantics are bound as `environment.json`'s `memory_hash`. The live recipe mounts only `src`, curated `targets/riscv` and the owned attempt output, excluding root environment files, private coordination state and backups.

## Validation and next gate

The focused pure suite covers hand-authored synthetic commit/RVFI vectors, first differences, malformed/order/truncated input, byte-load normalization, masks, RAM strobes, strict plan limits, fixed commands, source identity and unsupported-host behavior. Synthetic event agreement is checker unit-test evidence only. Native Windows executable discovery found none of Verilator, Spike, the cross compiler or BSC; this does not establish WSL or image availability.

The proposed authorized Linux Docker recipe and resource limits are in `infra/riscv/README.md`. Acceptance needs three actual serialized jobs: control passes, functional mutant produces a first architectural mismatch, and unmodified control replays with matching architectural trace. Main must record the build owner before this worker starts. Root configuration, dependency locks, global registries, API/UI integration and release acceptance remain coordinator-owned.
