# Curated RISC-V toolchain and acceptance

This experimental image builds the complete, unmodified pinned Spike reference and supplies the tools for a bounded RV32I RTL comparison. A successful image build does not establish architectural correctness. Actual control, functional mutant and replay evidence are required separately.

The Docker build context contains only this README and the Dockerfile. There is no COPY/ADD, source upload, registry login or image push. SPIKE_BUILD_JOBS accepts 2 or 4 (default 4). The upstream configure command and instruction implementation set are unchanged.

| Component | Pin |
| --- | --- |
| Verilator base | v5.042@sha256:63e38d3b18ca8799d2821c2c254b36c449eb80ea7f38769e919849996f0b7cd7 |
| Spike, BSD-3-Clause | 4c6a9bc79dab80616932949707e7c0471d8e9e54 |
| PicoRV32 DUT, ISC | ef203c2b0a3fb793280f5114941416c425c5b461 |
| Image Python packages | cocotb 2.0.1, Pydantic 2.12.3, pytest 8.4.2 |

Ubuntu dependencies resolve during the build. Preserve actual /opt/veriforge/PACKAGE_VERSIONS, Spike revision/submodule receipts, Python distributions, tool versions and immutable image ID. This image environment is separate from the application lock. The recipe alone does not guarantee bit-identical future rebuilds. Public source/license identity is recorded in targets/riscv/upstream.json.

The manual-only hosted workflow builds the toolchain, checks pure compatibility, then runs three fixed actual attempts in one ephemeral job. It exports only allowlisted evidence up to 32 MiB with 1 day retention; no image archive, GHCR publication or service deployment. The workflow uses standard public ubuntu-24.04, a 45 minute job cap, a 35 minute build cap, four compile jobs and at most 8 minutes for metadata/pure checks/actual execution. Exhausted deadlines, missing tools, malformed output and incomplete evidence fail the gate. There is no skip-to-success path or automatic retry.

The controller's explicit public file manifest includes the independent shared contracts/process-helper import closure, licensed target sources and pure baseline tests. Containers see only curated source/targets read-only and a fresh writable attempt directory. Each actual job runs as the host's nonroot UID with no network/capabilities, a read-only root, two CPUs/2 GiB/128 PIDs and 128 MiB temporary storage. The fixed experiment permits 120 seconds total inner wall time, 256 retirements and 100000 DUT cycles. Outer attempts allow at most 180 seconds, additionally limited by the shared 8 minute execution deadline. Main monitors and cancels the owned CI run when work must pause.

The platform is one-hart RV32I, little endian, entry 0x80000000, zeroed 64 KiB RAM and completion store 1 at 0x8000ff00. No MMIO, M/C, privilege/CSR or interrupt acceptance is claimed. The mutant enables upstream PICORV32_TESTBUG_002, corrupting physical register writes while retaining the independent reference and checker. The anticipated first RVFI difference is the second instruction's stack-pointer writeback (0x8000fef0 expected, 0x8000fef1 observed), derived from the unchanged source and subject to actual execution. Both control runs must agree through completion and have identical five identity hashes, executable bytes and normalized retirement traces. UUIDs, wall time and diagnostic logs are not replay equality inputs.

The CLI's process exit alone is insufficient: a read-only validation container uses the same pinned image to validate native schemas, reparse Spike commits, normalize raw RVFI events and compare those reconstructed records exactly against the saved retirement pair. It recomputes the comparison count, first divergence and signature hash in addition to checking status, source/environment identities and artifact checksums. These pure postprocessing containers run no compiler, RTL or reference workload. Metadata permits 20 seconds, pure compatibility 30 seconds and each of three validators 10 seconds, all within the same 8 minute execution deadline. Host Python requires no new dependencies. Until reviewed real receipts exist, this remains an experimental implementation.
