# Curated firmware/MMIO CI evidence — 30 October 2026 MYT

**The bounded five-case firmware/MMIO gate is independently accepted.** Main confirmed a separate saved-evidence audit that reconstructed native observations, checks, identities and program bytes against the pinned source. This is a scoped result supported by the raw records below. Preparing this directory started no compiler, simulator, checker job or replay.

[Run 40929665294, attempt 1](https://github.com/desanv01/VerifForge/actions/runs/40929665294), at [source 5565985bbe2bb2a0be869d366baa48c715057657](https://github.com/desanv01/VerifForge/commit/5565985bbe2bb2a0be869d366baa48c715057657), reports `accepted: true`, builder settled and 598.746973173 seconds in its unchanged [summary](summary.json). This is a curated register-offset defect and hand-authored restoration, not a naturally discovered upstream defect or Meta-generated repair.

## Recorded outcomes

| Case | Raw observation | Separate checker verdict, independently audited |
|---|---|---|
| [Control](control.check.json) | 218 DUT cycles, 55 retired events, 72 accepted bus transfers; retired mailbox value 1 | Pass within scope |
| [Bad header](bad.check.json) | 166 cycles, 39 events, 54 transfers; retired mailbox 2. First STATUS conflict at cycle 95, bus sequence 31, MMIO sequence 2 (third MMIO transfer): address `0x40000008` observed versus `0x40000004` required; independent probe reads 0 versus `0x55667788` | Design mismatch, `FIRMWARE_STATUS_ADDRESS` / `firmware.status_offset` |
| [Restored candidate](candidate.check.json) | 218 cycles, 55 events, 72 transfers; retired mailbox 1 | Pass within scope |
| [Bad saved-program replay](bad-replay.check.json) | Same raw observation bytes and failure signature as bad | Design mismatch reproduced; retained execution commands contain simulator only |
| [Candidate saved-program replay](candidate-replay.check.json) | Same raw observation bytes as candidate | Pass within scope reproduced; retained execution commands contain simulator only |

Bus-transfer counts include instruction fetches and RAM traffic; they are not MMIO counts. Final mailbox retirements occur at cycle 217 for passing cases and 165 for bad cases; the producer's total cycle counts are 218/166. A completed simulator process does not make the bad design pass.

The [candidate diff](candidate/candidate.diff) changes only `STATUS_ADDR` from `0x40000008` back to `0x40000004`. Both [original bad header](candidate/original-bad-registers.h) and [selected restored header](candidate/registers.h) remain intact. The [literal assembly probe](control/independent_probe.S) reads the required STATUS address without the C header; the [peripheral source](control/native_registers.sv) is retained for context. The curated independent checker and register contract remain separate from firmware self-checking. Spike is **not** an MMIO reference for this peripheral.

## Files and exact identity

[identity-manifest.json](identity-manifest.json) maps every copied file to its original artifact-relative path, receipt hash, byte count and run. All 72 copied files are unchanged actual bytes, checked before copying against the original downloaded receipt. The authored README and selection manifest are new explanations, not producer output.

Each of five case directories retains raw `observation.json`, recorded `execution.json` commands/status/file identities, `execution-identities.json`, `preparation.json`, `address-check.json`, `register-contract.json`, selected/original headers, disassembly/ELF headers and exact `firmware.elf`/`firmware.bin`. Top-level `*.check.json` records preserve typed source/checker/contract/experiment/environment identities, failure signatures and replay lineage. Both candidate directories retain their original diff. These are inert evidence files here; no artifact code or executable was run during curation.

`preparation.json` intentionally retains its earlier `not_run`/`prepared_not_executed` status and source-era limitations. It describes the frozen preparation, not the later execution result. Read it together with `execution.json`, observations and checker records; its bytes were not rewritten to conceal chronology. Original preparation/execution manifests also name omitted files, so this subset is not their complete runtime/file closure.

| Saved program | ELF SHA256 | Flat binary SHA256 |
|---|---|---|
| Control, candidate and candidate replay | `199eb65c18cd2798b197bc34289a4208c5da3be99a6cd988d24af328251ea9d5` | `46eb7d20bf166bae4acd64938989d09cb5ec33ffab92e1ba2e2b91c7bb259666` |
| Bad and bad replay | `9e969a9e5480e20135930f30b2fac8dbd348907c1282a33e71f51ffb7f19bbf0` | `26108391b15f93e12691da1c6116bb81ae0ebd6e96d9d1064fc67722a50c1da4` |

Replay execution records link the original execution hash. The replay commands omit assembly, compilation, linking and all binutils phases; saved program bytes and raw observations match their respective originals. The unchanged HDL model is rebuilt by the simulator job, which is distinct from recompiling firmware. This independently accepted firmware saved-program operation differs from the separate RISC-V RAM smoke's recompiled-control experiment.

## Tools, configuration and reproduction

The reviewed [tool metadata](tool-metadata.json) records container tool/package versions and executable hashes, not arbitrary environment variables or host configuration. Image ID: `sha256:41215acab7a23238d62b5bfc9a6e09187afffbc689c0ad2689a9778133a85f85`. Recorded versions include Verilator 5.042, RISC-V GCC 13.2.0 (`13.2.0-11ubuntu1+12`), binutils 2.42, cocotb 2.0.1, Python 3.12.3 and Pydantic 2.12.3. Package resolution is captured, not a promise that a future rebuild yields the same image.

The pinned profile is RV32I/ILP32 little-endian PicoRV32, 64 KiB RAM at `0x80000000`, reset/entry there, stack `0x8000fef0`, completion mailbox `0x8000ff00`. The native valid/ready 32-bit bus has byte write strobes. Four literal register addresses are `0x40000000`, `0x40000004`, `0x40000008`, `0x4000000c`; the peripheral is project-authored. Preparation budgets are 200000 cycles, 4096 retirements and 120 seconds. Recorded compile/simulation paths under `/tmp/firmware-snapshot` and interpreter `/opt/venv/bin/python` are fixed container paths, not private host paths.

Reproduce only in a separately authorized Linux/Docker environment using the pinned [workflow](https://github.com/desanv01/VerifForge/blob/5565985bbe2bb2a0be869d366baa48c715057657/.github/workflows/firmware-acceptance.yml), [controller](https://github.com/desanv01/VerifForge/blob/5565985bbe2bb2a0be869d366baa48c715057657/scripts/ci_firmware_acceptance.py) and [fixture instructions](https://github.com/desanv01/VerifForge/blob/5565985bbe2bb2a0be869d366baa48c715057657/targets/firmware/executable/README.md). The captured source instructions predate execution; their source-only status text is historical. Follow the fixed workflow's controls, source manifest, unchanged checks and runtime dependencies; this subset alone is not a runnable appliance or image. No workflow dispatch was performed here.

[source-manifest.json](source-manifest.json) identifies the captured public source closure. Upstream PicoRV32 revision is `ef203c2b0a3fb793280f5114941416c425c5b461`, source hash `0836050971b3c6cdd28ac3b1e5719a67fb645161912bef1e472e63995ceb0622`; its ISC notice remains in the [pinned public RTL](https://github.com/desanv01/VerifForge/blob/5565985bbe2bb2a0be869d366baa48c715057657/targets/riscv/upstream/picorv32.v). VerifForge supplies firmware/peripheral/checking integration; GCC/binutils, Verilator and cocotb are upstream tools. No project license is selected by this packet. A Spike revision in preparation/tool metadata is provenance context, not evidence of firmware/MMIO comparison against Spike.

## Original artifact and omissions

Original artifact ID: 12922737365. Its transport ZIP SHA256 is `60b05ba172dfec2a081b1ecc2530ef50c0da1c106fc371d10fc33327b6adcefd`, **GitHub-reported, not locally recomputed by this curation**. Original extracted receipt SHA256: `324291029545e9783a040452e7698067e09c29717d21c5c52a4ec842dda3ee13`. Hashes establish selected-byte consistency within that provenance boundary, not authentication against replacing all records.

The complete extracted public artifact has 285 files/2426556 bytes and remains locally retained; the hosted artifact is temporary. This packet copies 72 files and explicitly lists 213 omissions in its manifest: build/process logs, host/image inventories, host-mount commands, simulation-job metadata, duplicate sources, maps/objects and full original receipt. Source code needed for a new run remains at the pinned public commit. Included record references to omitted files are not claims that those files are present here. No unrelated private evidence, database, credentials, private coordination or full image inspection is included.

Independent acceptance covers the curated offset-fault detection, prepared restoration and saved-program replays. Broader firmware correctness, arbitrary programs/devices, ISA certification, Spike MMIO validation, model discovery/generated repair, physical hardware, application/browser adoption and release acceptance remain unclaimed. The separate audit was one saved-artifact audit, not additional native executions; its acceptance does not enlarge the fixture's scope.
