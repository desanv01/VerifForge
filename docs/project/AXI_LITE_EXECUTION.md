# Curated AXI-Lite execution — VF-055B

Status on 29 October 2026: the bounded seven-case local HDL gate is complete: four correct controls pass and three intended mutants fail with distinct independently checked signatures. One 149-cycle directed control was executed on October24 and audited/preserved on October29; the other six cases were actually executed on October29. Independent real-evidence review is handed to main. API/workbench/replay/export/repair integration is a separate gate. Contract/oracle semantics remain owned by VF-055A; see [AXI_LITE.md](AXI_LITE.md).

The original VF-055B source delivery on October24 used pure/mocked verification only. Its seven-case live follow-up was interrupted by the human pause after the first case passed; the second partial case remains preserved and is not counted as a completed attempt. No mock results were substituted for the completed real cases below.

## Completed real gate — U3-AXI-LIVE-RESUME

After direct human resume and a main-recorded sole simulator slot, the saved 149-cycle attempt's 16 artifact hashes, all five evidence identities, exact applied stimulus and independent checker result were verified against unchanged current source. The saved runner/driver/job/schema/SV/oracle hashes match current files, so that valid case was not rerun unnecessarily.

The remaining six-case command ran exactly once with stop-on-first-failure:

```powershell
$env:VF_AXI_LITE_SIM_GATE='granted'
.venv/Scripts/python.exe -m pytest tests/axi_lite/test_live.py -q -x -k 'not directed' --basetemp .coordination/U3-AXI-LIVE-RESUME-live1 --junitxml .coordination/deliveries/U3-AXI-LIVE-RESUME.junit.xml --tb=short
```

Result: **6 passed, 1 deselected in 163.25 seconds**, with no retry or source correction. Existing immutable image `sha256:b4675c1030a181154151561d95864e3eb6663c8c325ade500275c27afd33ee46` was inspected before execution and used for each case without pull/build/install. Actual tools: Verilator 5.042, cocotb 2.0.1, Python 3.12.3, GNU Make 4.3 and Ubuntu g++ 13.3.0. No model or API service was involved.

| Actual case | Executed cycles | Independently checked outcome | First failure sample |
| --- | ---: | --- | ---: |
| Correct directed control (October24, re-audited October29) | 149 | pass_within_scope | — |
| Correct separated AW/W witness | 11 | pass_within_scope | — |
| Correct byte-strobe witness | 9 | pass_within_scope | — |
| Correct response-stall witness | 7 | pass_within_scope | — |
| Separate-channel pairing mutant | 11 | property_failure / AXIL_PROGRESS / axil.response_bound | 7 |
| Ignored-strobe mutant | 9 | design_mismatch / AXIL_READ / axil.read_value | 7 |
| Stalled-response VALID mutant | 7 | property_failure / AXIL_HANDSHAKE / axil.stalled_response | 4 |

Post-run read-only audit rechecked all seven actual observation envelopes with the frozen independent oracle and verified all five identity components, code/source/schema snapshots, exact stimulus, checker output/signature, every artifact path/size/SHA-256 (**112 artifacts total**), image/tools, and retained resource budgets. All child wall times stayed below 120 seconds and each complete retained directory below 10MiB. These cases demonstrate the specified configurations only; live cancellation/resource-exhaustion behavior and repeat replay/repair are not established by this gate. Final named AXI container inventory was empty, and the slot was explicitly released.

Private reproducible records: `.coordination/deliveries/U3-AXI-LIVE-RESUME.md`, its `-preflight.json`, `-results.json`, `-audit.py`, and JUnit XML; actual output directories remain `.coordination/VF055E-live1/test_live_curated_registers_di0` and `.coordination/U3-AXI-LIVE-RESUME-live1`. These ignored local records contain full exact identities and artifact manifests. They are not public download/API integration.

## Trusted interface

`veriforge.axi_lite.runner.run_experiment(AxiLiteExperiment, output_dir, cancel_event=None)` executes only the installed `targets/axi_lite/registers.sv` with a fixed integer `MUTANT` selected by a typed fixture ID. The file has four 32-bit registers, 8-bit addresses and one independent slot per write channel plus one read slot. No parameterized widths, supplied sources, shell commands, dynamic Python plugins or uploaded testbenches are accepted. `python -m veriforge.axi_lite experiment.json empty-output-directory` is the internal approved-Linux entrypoint. Host callers use `veriforge.axi_lite.container.run_container_experiment`; this is not wired into registry/API/evidence dispatch in this task.

The public contract/checker remains `axi_lite.v1`, `axi_lite.trace.v1`, `axil-registers-v1`. No shared FIFO schema or `__init__.py` change was made. Helpers `directed_experiment()` and `witness_experiment(case, fixture_id)` are imported explicitly from `.runner`.

## Cycle and fixture behavior

The observation-only cocotb driver applies every stored input while clock is low, waits 5 ns, enters `ReadOnly`, records READY/response values, advances 1 ns out of the read-only phase, raises the clock, and waits 5 ns. It never reads the oracle, generates expectations, adapts VALID, inserts hold cycles, or decides a verdict. Sampling follows the pinned [cocotb 2.0 timing model](https://docs.cocotb.org/en/v2.0.0/timing_model.html): ReadOnly provides settled values and requires advancing time before writes. There is no asynchronous reset timing or complete AXI conformance claim.

Submitted cycles are exact playback. Manager VALID/payload hold violations against actual READY are independently returned as `harness_error`. Plans prepared for this fixture's READY schedule may therefore be illegal against another implementation. The correct fixture accepts independent AW/W into separate slots, commits on the second accepted half, and asserts B on the next sample. It accepts AR independently and snapshots old register values before any same-edge write. It conservatively waits until the sample after response consumption before accepting another request; same-edge replacement is allowed by the oracle but is not required. Response VALID/payload remain stable under stalls. Reset gates response VALID low before the sampled edge and clears storage and all saved work. The fixture includes asynchronous reset sensitivity solely to implement the required settled reset sample, without claiming an analog/asynchronous timing check.

The directed source plan covers both AW/W orders, all 16 byte masks with readback, all four registers, ignored protection bits, read/write collision, simultaneous stalled B/R, invalid addresses, and reset cancellation of AW-only, W-only and paired/stalled work. It is a prepared test plan, not measured coverage. Initial sampled reset and one quiescent release sample precede traffic.

## Distinct intended mutants and hand-derived witnesses

All three witness files under `targets/axi_lite/witnesses` are exact schema-valid experiments, beginning with reset at cycle 0 and quiet release at cycle 1. Pure tests supply explicitly hand-derived observations. The completed real gate above independently confirms the actual HDL's corresponding outcomes; the pure observations alone were never treated as HDL evidence.

| Fixture / MUTANT | Actual intended source change | Hand-derived distinguishing observation |
| --- | --- | --- |
| `axil_registers_correct` / 0 | Reviewed implementation | Each witness's expected trace passes the independent oracle |
| `axil_aw_w_order_bug` / 1 | Saves each half but commits only if AW and W handshake together | AW accepted at 2, W at 3; no B by sample 7 exceeds the four-edge response bound: `AXIL_PROGRESS`, `axil.response_bound` |
| `axil_strobe_bug` / 2 | Replaces every byte regardless of WSTRB | Write `11223344`, then `AABBCCDD` with mask 5; read at 6 must return `11BB33DD` at 7. Full replacement returns `AABBCCDD`: `AXIL_READ`, `axil.read_value` |
| `axil_response_stability_bug` / 3 | Clears BVALID on the first pending-response edge even when BREADY is low | Paired write at 2, BVALID at stalled sample 3; dropped BVALID at 4 violates `AXIL_HANDSHAKE`, `axil.stalled_response` |

The order witness leaves sufficient idle samples to distinguish a DUT response bound from incomplete stimulus. The response witness uses only two stalled rows, below the environment limit. A control version of each hand-derived trace passes. The mutants have different source branches, stimuli and failure classes; none is a syntax/build mutant.

## Evidence and execution outcomes

The parent preserves the exact typed experiment, full cycle stimulus, reviewed contract, source snapshot, oracle snapshot and contract schema snapshot. Successful execution additionally preserves observed tool versions, platform and runner/driver/job/schema hashes, internal job description, typed observation envelope and independent `check.json`. `EvidenceIdentity` hashes source plus selected variant/parameters, checker, canonical contract, canonical experiment and environment. The host resolves the existing approved image tag `veriforge-rtl:dev` to its local immutable SHA-256 ID, runs that ID with `--pull never`, and records both tag and ID in `container-image.json`. It does not invent versions or image digests when unavailable.

Observations stay AXI-specific; common FIFO trace is empty. `usage.cycles` counts complete executed observation rows, while `check.json.checked_cycles` records the independently checked prefix. Design failures carry the checker's requirement/class/first sample/expected/observed and a signature bound to checker and contract. Failure signatures are absent for unsupported tooling, malformed observations, illegal manager behavior, timeout, cancellation, resource exhaustion and build/harness errors. Exceeding finite environment bounds or ending with outstanding work is inconclusive. A timeout is never automatically a DUT defect.

Output directories must be empty. All retained files have relative artifact paths, sizes and SHA-256; the outer host report rebases nested child evidence correctly. The final result is deliberately excluded from its own artifact manifest to avoid self-reference. An identical rerun uses the saved experiment and same fixture/source/checker/environment; there is no model call. A standalone portable export/replay integration is outside this slice.

## Resource boundary and limitations

The host uses the existing reviewed shared `rtl.runner._bounded` and `_environment` pattern, explicitly approved by main for this slice. That private helper coupling is retained rather than refactoring other owners' code. Its environment allowlist keeps controller/model credentials out. The container has no network, read-only root, dropped capabilities, no privilege escalation, 128 PID limit, 2 GiB RAM, 2 CPU limit, and a 128 MiB executable tmpfs for generated Verilator binaries. Source, fixture and input mounts are read-only; only the attempt mount is writable. No image pull, dependency install or image build occurs. Waves are disabled; generated C++ is in temporary scratch and removed afterward.

The child bounds build/simulation together by the experiment wall budget; version probes have bounded five-second calls, and the host grants 15 seconds of container overhead beyond the experiment budget. Host monitoring includes the entire nested evidence directory. Cancellation/limits terminate the process tree via shared helpers; named-container force-removal also runs in `finally`. Cleanup failure is reported when no valid result is returned, but Docker cleanup behavior remains untested live.

The shared poller checks disk size every 50 ms, so byte-limit detection is not a filesystem quota and transient disk overshoot is possible. Finalization includes result JSON in the retained byte cap and removes largest artifacts if necessary, returning `resource_limit` with incomplete replay evidence explicitly stated. No design verdict survives evidence removal. An extremely small budget can retain only a minimal resource-limit report. This is suitable only for the curated trusted fixture boundary; it is not an arbitrary-code sandbox.

The prebuilt image is reused unchanged with host source mounts. The completed real gate above demonstrates cocotb/Verilator scheduling, compiler compatibility, build tmpfs capacity, scoped response behavior and ordinary container cleanup for these cases. Actual cancellation/resource-exhaustion, repeated replay and repair acceptance remain unverified here. Independent evidence review and registry, campaign/API dispatch, evidence bundles, planner support, reducer and UI capability changes remain separately owned.

## Verification performed

Original VF-055B bounded source-verification command (no live gate enabled at that time):

```powershell
.venv/Scripts/python.exe -m pytest tests/axi_lite/test_runner.py tests/axi_lite/test_container.py tests/axi_lite/test_live.py tests/axi_lite/test_contracts_oracle.py tests/acceptance/test_axi_lite_contract_acceptance.py -q --basetemp .coordination/VF055B-test4 --tb=short
```

Result: **141 passed, 7 skipped in 3.85 seconds**. This includes 79 contract-owner cases, 39 independent reviewer cases, and 23 new runner/container/driver checks. New execution tests mock subprocesses/tools and label fabricated observations; the driver test uses a fake synchronous trigger scheduler and checks exact writes/sample-before-edge order. Seven opt-in actual HDL cases require `VF_AXI_LITE_SIM_GATE=granted` plus an explicit coordinator slot; they were skipped, not executed. Setting that variable alone is not authorization.
