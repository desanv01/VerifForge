# Curated RISC-V four-case acceptance — 31 October 2026 MYT

[Run 41202693632, attempt 1](https://github.com/desanv01/VerifForge/actions/runs/41202693632), at [source c14525e3791569215ba385a1c5582d52d0b8858f](https://github.com/desanv01/VerifForge/commit/c14525e3791569215ba385a1c5582d52d0b8858f), passed independent retained-evidence review for this bounded native RISC-V configuration. This page publishes curated metadata only. It contains no copied raw bundle, logs, source snapshot or runtime image. Preparing it did not execute another simulator, compiler or model.

| Actual case | Spike / DUT retirements | Compared events | DUT cycles | Independent verdict |
|---|---:|---:|---:|---|
| Control | 17 / 17 | 17 | 68 | Pass within scope |
| Deliberately seeded mutant | 17 / 14 | 2 | 59 | Design mismatch |
| Control recompiled and rerun | 17 / 17 | 17 | 68 | Pass within scope; exact recompiled replay |
| Same-run control saved-program replay | 17 / 17 | 17 | 68 | Pass within scope; captured object/ELF/binary reused |

The mutant's first architectural divergence is retirement order 1, diagnostic DUT cycle 14: register value `0x8000fef0` expected versus `0x8000fef1` observed. This is upstream `PICORV32_TESTBUG_002`, an intentional fixture, not a newly discovered upstream bug. Its observed stop/completion-address write does not satisfy the passing completion value. Process completion and the independently checked design verdict are separate.

## What the two replays establish

The third case assembles and links again. Its five canonical identities, compiled program bytes and normalized observed/reference pair match the original control. The fourth case reuses this run's freshly captured control `smoke.o`, `smoke.elf` and `smoke.bin`; it does not reuse the historical October30 run. Original-result/run/attempt lineage and exact program bytes were independently checked.

Saved replay bypasses RISC-V assembly, linking and objcopy, and its environment records empty program-compilation commands with no GCC requirement/version query. It still executes Spike and rebuilds the Verilator HDL/C++ simulation. This is not an absence-of-all-compilers claim. Its environment identity intentionally differs to bind saved-program mode and origin proof; four design identities and the common runtime remain equal. The environment hash was recomputed rather than waived.

All four cases preserve identical program bytes:

| File | Bytes | SHA256 |
|---|---:|---|
| `smoke.o` | 884 | `77b316af350edc3805d465db21d88d9678aa7fb9925e5dc41db346c94c3018f3` |
| `smoke.elf` | 4708 | `d441470ea48605be9f29a95d503a4be492bcd8c5dcff0258b0ee571f33b22afc` |
| `smoke.bin` | 76 | `eb74f604790a38471fe7b65e2a2be2fc3e89f2aef0bf94f21fa5142213d1fcf3` |

## Supported configuration and attribution

The fixture is one-hart, little-endian RV32I with 64 KiB RAM at `0x80000000`, stack `0x8000fef0`, and a retired aligned full-word store of 1 to completion mailbox `0x8000ff00`. Limits remain 100000 DUT cycles, 256 retirements and 120 seconds per experiment. Actual results were rechecked from retained raw Spike commits and RVFI under unchanged checker and contract identities. DUT cycles are diagnostic and are not compared with Spike cycles. This small RAM smoke is not full ISA certification, MMIO/firmware acceptance or arbitrary-program support.

Upstream dependencies remain [PicoRV32 at ef203c2b0a3fb793280f5114941416c425c5b461](https://github.com/YosysHQ/picorv32/tree/ef203c2b0a3fb793280f5114941416c425c5b461), with its ISC source notice, and [Spike at 4c6a9bc79dab80616932949707e7c0471d8e9e54](https://github.com/riscv-software-src/riscv-isa-sim/tree/4c6a9bc79dab80616932949707e7c0471d8e9e54), BSD-3-Clause; the [retained Spike notice](https://github.com/desanv01/VerifForge/blob/c14525e3791569215ba385a1c5582d52d0b8858f/targets/riscv/upstream/SPIKE-LICENSE) is part of the pinned public source. VerifForge contributes integration, evidence handling and checking, not those upstream implementations.

Recorded tools include Verilator 5.042, cocotb 2.0.1 and Python 3.12.3 on linux/amd64. The executed image identity is `sha256:5f9a62c1043089d6eb4f7b2d966640ab25d2c53dc2934d11f37f141b1ddc6fff`. A future rebuild is not promised to reproduce that image digest.

## Evidence identity, access and reproduction

Independent audit checked the complete private inventory of 150 files / 1,622,915 bytes, all 149 entries in the receipt's closure excluding itself, 31 staged source identities, native results, program layouts, replay lineage and retained settlement evidence. The complete sorted filename/hash/size inventory vector SHA256 is `9e9de50a6682a9322534ed9e3a44219d3b8430c113ef5aa133ff6943aff81cb9`; raw staged source-manifest SHA256 is `2d8bfe6e1cb101fe9b5cb155783c9754b06b605a349253d54b8f37d784a7ed52`.

Artifact 13042803636 is a 227,602-byte ZIP. Its SHA256 `6bddbfad5da439597945b47729ea65ffb7f376366bcb9d54dd599aa0183fd22f` was independently recomputed during the accepted audit and matched the GitHub transport digest. Unlike the [historical October30 packet](../2026-10-30/README.md), this audit retained and rehashed the original ZIP. The extracted bytes also matched every archived file. Hash agreement supplies identity within the recorded provenance boundary, not independent remote attestation.

Retained artifact metadata gives creation `2026-10-31T12:58:37Z` and expiry `2026-11-01T12:58:36Z`; the workflow uses one-day retention. The public run page listed the artifact when checked for this summary, but download/log access may require sign-in and continued artifact availability is not promised. The privately preserved complete evidence is not included or made publicly accessible by this metadata page.

Reproduction uses the pinned [workflow](https://github.com/desanv01/VerifForge/blob/c14525e3791569215ba385a1c5582d52d0b8858f/.github/workflows/riscv-acceptance.yml), [controller](https://github.com/desanv01/VerifForge/blob/c14525e3791569215ba385a1c5582d52d0b8858f/scripts/ci_riscv_acceptance.py) and [toolchain instructions](https://github.com/desanv01/VerifForge/blob/c14525e3791569215ba385a1c5582d52d0b8858f/infra/riscv/README.md). Preserve their four-case inputs, checks, resource admission and deadlines; the old workflow step label mentioning three attempts is not the actual case count. No workflow was dispatched to prepare this page.

## Separate application and repair gates

This acceptance covers raw native execution and both replay semantics. A separately reviewed four-case recorded-import candidate has source acceptance for fresh typed preparation, but remains unadopted because compatibility with immutable legacy persisted validator identities and application routing needs separate integration. Do not treat raw acceptance as successful four-case database admission, API/export or browser/workbench acceptance. The independently accepted bounded read-only API matrix for previously imported evidence is a separate result; it does not admit this new four-case bundle.

No Meta call, model-discovered defect, generated repair or core-repair validation occurred. The firmware packet's curated header correction is a separate case, not a RISC-V core repair. The earlier failed ELF-replay event and October30 three-case acceptance remain historical and unchanged. See the current [support matrix](../../../project/SUPPORT_MATRIX.md) for Import / Execute / Repair gates.
