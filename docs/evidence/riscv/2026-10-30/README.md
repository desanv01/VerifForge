# Curated RISC-V CI evidence — 30 October 2026 MYT

This is a byte-preserved subset of already public GitHub CI results, prepared for permanent source review because the hosted artifact has one-day retention and expires on 30 October. It is not a new execution or a complete runtime image. Publication is subject to the repository's review process.

[Run 40920450439, attempt 1](https://github.com/desanv01/VerifForge/actions/runs/40920450439) used [source 44b8b5e40ce7841f076e4178df13f38ecdbd05fd](https://github.com/desanv01/VerifForge/commit/44b8b5e40ce7841f076e4178df13f38ecdbd05fd). The [producer summary](summary.json) records `accepted: true`, builder settled and 610.326892502 seconds. Independent saved-evidence audit accepted the curated recompiled-control gate after 14 checks; that audit did not start another simulator/compiler job.

| Attempt | Recorded observation | Independent design verdict |
|---|---|---|
| [Control](control/result.json) | 17 retirements, 68 DUT cycles; all 17 compared architectural events agree with Spike through completion | Pass within the curated scope |
| [Seeded mutant](mutant/result.json) | 14 raw retirements, 59 cycles; first discrepancy after two comparisons, order 1, PC `0x80000004`, diagnostic DUT cycle 14: register value `0x8000fef0` expected, `0x8000fef1` observed | Design mismatch from upstream `PICORV32_TESTBUG_002`, a deliberately seeded register-file corruption |
| [Control recompiled and rerun](control-replay/result.json) | 17/68 again; exact object, ELF, flat binary, raw DUT/Spike logs, normalized pair and comparison bytes match control | Pass within scope; this operation assembles and links again |

The seed is a demonstration fixture, not a newly discovered upstream defect. Process/test success and the architectural design verdict are separate. The mutant's full 14-event raw trace is retained even though the comparator stops at the first divergence.

## Inspect the retained results

Each of `control`, `mutant` and `control-replay` retains `dut/rvfi.json` (raw DUT), `reference/commit.log` (raw Spike), `retirement.json` (normalized observed/reference pair), `comparison.json`, `result.json`, reviewed tool/command `environment.json`, experiment/profile/source/checker manifests and `smoke.o`, `smoke.elf`, `smoke.bin`. These compiled files are inert evidence here; no copied artifact code was executed during preparation.

Start with [identity-manifest.json](identity-manifest.json) for every selected file's byte count, SHA256, originating public run and relative source path. [source-manifest.json](source-manifest.json) identifies the exact 30 captured source files; those sources remain available at the pinned public commit rather than being duplicated here. [upstream.json](upstream.json) preserves the original upstream revision/license metadata.

The two controls and mutant use identical compiled program bytes:

| File | SHA256 |
|---|---|
| `smoke.o` | `77b316af350edc3805d465db21d88d9678aa7fb9925e5dc41db346c94c3018f3` |
| `smoke.elf` | `d441470ea48605be9f29a95d503a4be492bcd8c5dcff0258b0ee571f33b22afc` |
| `smoke.bin` | `eb74f604790a38471fe7b65e2a2be2fc3e89f2aef0bf94f21fa5142213d1fcf3` |

## Configuration and upstream attribution

The profile is one-hart, little-endian RV32I, 64 KiB RAM at `0x80000000`, entry `0x80000000`, stack top `0x8000fef0`, and retired aligned full-word store of 1 to completion mailbox `0x8000ff00`. Limits are 100000 DUT cycles, 256 retirements and 120 seconds shared across the inner compile/reference/simulation phases. Raw Spike logs include the bounded stop-loop tail; normalization stops at the completion store. DUT cycle is diagnostic timing, not a Spike cycle comparison.

PicoRV32 is upstream [YosysHQ/picorv32 at ef203c2](https://github.com/YosysHQ/picorv32/tree/ef203c2b0a3fb793280f5114941416c425c5b461), ISC notice retained in its public source header. Spike is upstream [riscv-isa-sim at 4c6a9bc](https://github.com/riscv-software-src/riscv-isa-sim/tree/4c6a9bc79dab80616932949707e7c0471d8e9e54), BSD-3-Clause; its [retained notice](https://github.com/desanv01/VerifForge/blob/44b8b5e40ce7841f076e4178df13f38ecdbd05fd/targets/riscv/upstream/SPIKE-LICENSE) remains part of the pinned source. VerifForge contributes integration and checking, not these upstream tools or core. No project license is chosen by this evidence packet.

Recorded image ID is `sha256:77fb34efe7f2cda0daba0cb249358cbce8020158a023f2030e2c6726099dbcf5`; tools are Verilator 5.042, RISC-V GCC 13.2.0 (`13.2.0-11ubuntu1+12`), cocotb 2.0.1, Python 3.12.3 and Pydantic 2.12.3. Spike executable hash and its softfloat revision are bound in each environment. `environment.json` is a reviewed identity/tool/command record with fixed container paths, not an environment-variable dump.

## Reproduction and earlier failure

Use the pinned public [workflow](https://github.com/desanv01/VerifForge/blob/44b8b5e40ce7841f076e4178df13f38ecdbd05fd/.github/workflows/riscv-acceptance.yml), [controller](https://github.com/desanv01/VerifForge/blob/44b8b5e40ce7841f076e4178df13f38ecdbd05fd/scripts/ci_riscv_acceptance.py) and [toolchain instructions](https://github.com/desanv01/VerifForge/blob/44b8b5e40ce7841f076e4178df13f38ecdbd05fd/infra/riscv/README.md) in a separately authorized, adequately provisioned Linux/Docker environment. The manual workflow fixes all three experiments; preserve its resource/deadline and independent postprocessing checks. It assembles to fixed-name `smoke.o`, then links that object. Inspect the retained experiment, profile and environment records before comparing a new result. No workflow was dispatched to prepare this packet. Ubuntu packages resolve during build; identical image IDs or bytes from a future rebuild are not promised. The image itself is not distributed here.

[Earlier run 40912735570](https://github.com/desanv01/VerifForge/actions/runs/40912735570), at source `f6a85ea8c91620b934ff95b43b692f61b5f72df4`, remains a failed exact-replay event: architectural traces and flat binaries agreed, but full ELF differed. Its unchanged [summary](earlier-failure/summary.json) records `accepted: false` and `exact control replay differs`. Independent diagnosis found random temporary object-name `STT_FILE` metadata from direct assembly/link. The later fixed-object recipe preserves full ELF equality checks; it does not strip or waive the difference. The new acceptance does not rewrite that failure.

## Subset and acceptance limits

The complete extracted current-run artifact has 125 files/1508745 bytes including its receipt, retained locally. This public subset copies 42 current-run files plus the earlier run's summary. The manifest explicitly lists 83 omitted current-run files: build/execution/validation logs, host/image inventories, host-mount commands, temporary job paths, duplicated source and the complete receipt. Original `result.json` artifact references also name omitted files; their presence in a record is not a claim that this subset contains them. Retained raw Spike commit logs are intentional evidence, distinct from omitted process logs. Only the summary from the earlier failed run is republished here; its full artifact remains separately retained.

The manifest records artifact ID 12919420477 and transport ZIP SHA256 `035f1cfce1a8010250db0498a89db387539b7122cb6adee173d2b1105f3dfd68` as **GitHub-reported**, not independently recomputed locally: the extracted download is retained, not the ZIP. Selected extracted bytes were independently rehashed against the downloaded receipt. Hash consistency supplies identity within that provenance boundary, not authentication against replacement of all records.

Acceptance covers this curated RAM smoke, seeded divergence and byte-identical recompiled control. It does not establish executing the original saved ELF without recompilation, all RV32I instructions, MMIO/firmware, arbitrary programs, model discovery, candidate repair, local application adoption or browser acceptance. See the [support matrix](../../../project/SUPPORT_MATRIX.md) for broader product gates.
