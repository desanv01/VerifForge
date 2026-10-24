# Arithmetic package: unsigned fixed-latency multiplier

Status: VF-050B real simulator acceptance passed on 24 October 2026: one WIDTH=8 reference and two deliberate mutants, each with 15 observed cycles. This demonstrates the curated package's positive/negative cases; application/API/planner integration and the full Phase 5 corpus remain separate work. Existing FIFO API schemas are unchanged.

## Contract and stimulus

`veriforge.arithmetic.ArithmeticExperiment` uses schema `arithmetic.v1`, adapter `rtl.arithmetic.v1`, target `arithmetic`, and contract `unsigned-multiplier-v1`. The contract's own schema is `arithmetic.contract.v1`. Existing shared `Experiment` and `BehavioralContract` remain FIFO v1.

Reviewed target template semantics:

- Unsigned operands, width 1–32; full 2*WIDTH-bit product; fixed latency two rising edges.
- Input valid on edge n, with reset low, produces output valid/product after edge n+2. The acceptance edge itself is offset zero. Every valid edge is accepted; there is no ready/backpressure port.
- Bubbles remain bubbles; consecutive inputs produce consecutive outputs in input order.
- Synchronous active-high reset takes priority over valid, clears output valid/data and cancels all in-flight transactions. An output otherwise due on the reset edge is cancelled.
- Invalid output data is unspecified except on reset, when it must be zero.
- First edge must reset. Plans must drain the final accepted transaction with at least two subsequent edges or cancel it with reset. No unobserved tail can receive a passing verdict.

Typed operations are `reset(cycles)`, `idle(cycles)`, `multiply(a,b)`, and `cycle(reset,valid,a,b)`. Strict unsigned integer inputs reject booleans and overflow; budgets constrain expanded cycle counts. An objective list names requirements, not achieved coverage.

The independent oracle schedules Python integer products by due-cycle index. It does not simulate HDL pipeline registers. Hand-derived vectors cover 0*x, 1*x, 128*2, (255)^2=65025, (65535)^2=4294836225 and (2^32-1)^2=18446744065119617025; tests also cover bubbles, consecutive inputs, reset cancellation and simultaneous reset/valid.

## Curated implementations and execution

`targets/arithmetic/multiplier.sv` is the only allowed source. Fixture selection maps to a fixed elaboration parameter: `multiplier_correct` (0), `multiplier_latency_bug` (1, one edge early), and `multiplier_data_bug` (2, truncates product to operand width). These are development fixtures; there are two faults, not the full phase-5 three-fault-per-family corpus. A truncation fault is equivalent at WIDTH=1; negative acceptance uses WIDTH=8.

`run_experiment(ArithmeticExperiment, Path, cancel_event=...) -> AttemptResult` executes only Linux Verilator >=5.036 with locked cocotb 2.0. It uses existing `rtl.runner._bounded` and `_environment` without changing those utilities. The model/controller does not supply commands, paths or testbench Python. The cocotb driver only drives stored stimulus and records settled output valid/product, after a timer and ReadOnly sampling phase. The checker runs in the parent process. Simulator environment variables use the existing credential-free allowlist.

`run_container_experiment` in the arithmetic package uses only existing `veriforge-rtl:dev`, overrides its entrypoint to `python -m veriforge.arithmetic`, mounts arithmetic sources read-only, disables networking and uses the existing resource limits. It never builds or installs anything. The serialized coordinator gate controls when a caller may run acceptance.

Results use common `AttemptResult`, `EvidenceIdentity`, `ArtifactReference`, `FailureSignature`, and resource/tool records. Exact experiment, stimulus, copied RTL/checker, canonical contract and tool/driver/job/runner identities are preserved. Hashes bind the source variant and parameters, checker, contract, experiment and environment. Arithmetic valid/product traces are stored in `arithmetic-trace.json` with schema `arithmetic.trace.v1`; the common FIFO-oriented trace stays empty. Requirement coverage stays empty until family-specific measured coverage is added; objectives are never reported as achieved coverage.

Timeout/resource exhaustion/cancellation do not produce a design failure signature. Incomplete/malformed observations are harness failures. A build marker is not a functional verdict. Native Windows execution returns unsupported without invoking a simulator.

## Validation commands

Pure tests, including explicitly mocked subprocess outcomes:

```powershell
.venv/Scripts/python.exe -m pytest tests/arithmetic -q --basetemp=tests/arithmetic/.tmp-vf050a-verified
```

Only after an explicit coordinator simulator-gate grant, run the serial positive/negative suite against the prebuilt image:

```powershell
$env:VF_ARITHMETIC_SIM_GATE = 'granted'
.venv/Scripts/python.exe -m pytest tests/arithmetic/test_live.py -q --basetemp=tests/arithmetic/.tmp-live-vf050a
Remove-Item Env:VF_ARITHMETIC_SIM_GATE
```

The live tests skip during ordinary pure testing. They explicitly use a 120-second per-case wall budget: the first real run with the ordinary 30-second experiment default timed out while compiling or initializing. No timeout was treated as a design failure. The corrected acceptance run used a fresh directory and retained the initial timeout evidence.

Actual VF-050B command (with the granted environment variable) used `--basetemp=tests/arithmetic/.tmp-live-vf050b-retry-20261024 --junitxml=.coordination/deliveries/VF-050B-retry.junit.xml`: **3 passed in 120.55s**. Reference passed within scope; early-output mutant violated ARITH_LATENCY at cycle 2 (expected valid false, observed true); truncation mutant violated ARITH_PRODUCT at cycle 6 (expected 65025, observed 1). Tool records: Verilator 5.042, cocotb 2.0.1, runner Python 3.12.3, GCC 13.3.0, GNU Make 4.3. Existing image ID was `sha256:b4675c1030a181154151561d95864e3eb6663c8c325ade500275c27afd33ee46` before and after, with no image rebuild.

All 39 manifest artifacts across three attempts were checked for path containment, byte length and SHA-256 identity. Saved source/variant/parameter, canonical contract, experiment, checker and environment hashes were recomputed. Stored stimulus and complete expected/observed traces were rechecked without executing a simulator. The receipt is `.coordination/deliveries/VF-050B-evidence.json`, generated by `VF-050B-validate.py`; details and preserved first-run limits are in `VF-050B.md`. No arithmetic containers remained active at slot release. Wider widths have pure hand-derived checks, not live acceptance in this task.

## Proposed shared adapter registry integration

Main should register one descriptor keyed by `rtl.arithmetic.v1`, providing `experiment_model=ArithmeticExperiment`, `validate=ArithmeticExperiment.model_validate`, `contract=reviewed_contract`, `expand=oracle.expand`, `run=run_experiment` (or host container runner) and `trace_schema=arithmetic.trace.v1`. The API first reads an explicit adapter discriminator, looks up a trusted descriptor and validates with that descriptor's schema; it must not coerce arithmetic into FIFO v1. Unknown identifiers fail closed. The controller sees a common callable returning `AttemptResult`, keeping target conditionals in the registry boundary.

This is an integration proposal for the shared owners; no API/core/planner/contract changes were made. The current planner, evidence replay/reduction and repair APIs accept FIFO experiments. They do not yet support arithmetic. A family-neutral trace envelope and a validated experiment union need an explicitly versioned shared change. Arithmetic replay/reducer integration, a third distinct fault, planner proposal integration, and measured coverage are subsequent finite scopes.

Official references consulted during implementation/review: [cocotb 2.0.1 timing model](https://docs.cocotb.org/en/v2.0.1/timing_model.html) and [2.0.1 library reference](https://docs.cocotb.org/en/v2.0.1/library_reference.html). The actual curated compatibility gate is now backed by VF-050B's stored simulator evidence.
