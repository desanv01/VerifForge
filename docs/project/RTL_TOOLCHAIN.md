# FIFO toolchain and deterministic execution

VF-011 owns this first FIFO milestone. Installed fixture IDs are `fifo_correct` and
`fifo_reset_bug`; full/order variants remain explicitly unsupported. Arithmetic and
AXI-Lite are outside this milestone. No model is needed to run or replay.

## Contract

`fifo-v1` uses unsigned data, synchronous active-high reset with priority over
read/write, registered read output initially cleared to zero, and occupancy flags
after each edge. Reads pop the oldest entry only if nonempty before that edge.
Writes append only if nonfull before that edge. Thus simultaneous read/write on
empty accepts only the write; on full it accepts only the read. Rejected reads
retain the last read output; reset discards all queued data. Depth need not be a
power of two. `targets/fifo/directed.json` exercises empty/full, simultaneous
operations, ordering, wraparound, rejected reads/writes, and reset with pending data.

`oracle.py` uses a Python deque and does not import HDL or simulator code. The
cocotb test only drives typed cycles and records observations. The parent checker
compares every cycle, including flags and retained output, then sets the verdict.
The deliberate mutant fails to clear occupancy on reset; labels never determine
the checker verdict.

## Setup and invocation

Build from the repository root in PowerShell:

```powershell
docker build -t veriforge-rtl:dev -f infra/rtl/Dockerfile .
docker run --rm --network none --cap-drop ALL --security-opt no-new-privileges --pids-limit 128 --memory 2g --cpus 2 -v "${PWD}:/workspace:ro" -v "${PWD}/infra/rtl/evidence:/evidence" veriforge-rtl:dev targets/fifo/directed.json /evidence/correct
docker run --rm --network none --cap-drop ALL --security-opt no-new-privileges --pids-limit 128 --memory 2g --cpus 2 -v "${PWD}:/workspace:ro" -v "${PWD}/infra/rtl/evidence:/evidence" veriforge-rtl:dev /evidence/correct /evidence/replay --replay
```

Create the evidence directory first and use a fresh output directory for every
attempt. Existing attempt contents are rejected. The pinned official Verilator
image is extended only with Python dependencies. Compiler and simulator jobs have
an explicit environment allowlist; API/model credentials are not forwarded.

Native entrypoint: `from veriforge.rtl import run_experiment, replay`.
`run_experiment(experiment, output_dir, *, cancel_event=None)` accepts a token
with `is_set()`, checks it every 50 ms, and kills the process group on cancellation,
timeout, resource overflow, or abnormal parent exit. Native Windows execution is
unsupported; run the worker in the Linux image. Missing/older tools produce an
unsupported result rather than a simulated pass. `build_error`, `harness_error`,
`timeout`, and `cancelled` remain separate from design mismatch.

Artifacts include exact experiment JSON, expanded stimulus JSON, source snapshot,
checker snapshot, contract, observed trace, environment identity, execution log,
VCD, and typed result. Build intermediates remain local and are not published
artifact references. Runtime byte monitoring excludes build intermediates and is
polled rather than a filesystem quota; scratch disk has no hard per-attempt quota.
Docker memory/CPU/PID limits are applied by
the launcher. The default 30-second budget includes compilation. Use the supplied
120-second directed budget for the first uncalibrated build on slow hosts.

Local default-budget checks in `infra/rtl/evidence/linux-tests-1` and
`linux-tests-2` timed out during compilation, including after moving scratch to
Linux storage. They establish timeout handling, not functional verification.
Do not claim the 30-second default is sufficient on this host. The 120-second
check in `linux-tests-3` compiled HDL but found a missing shared Python library;
the Dockerfile now explicitly installs `libpython3.12t64`.
Compiler scratch is ephemeral Linux storage. A bounded 32 MiB ccache in Linux
temporary storage may reuse compiler outputs across attempts in one worker;
replay still creates a fresh build directory and runs the actual simulator.

Replay validates artifact hashes and current source/checker/contract identity,
re-expands the saved typed experiment, and rejects altered stimulus. It recompiles
in a clean directory without a model call. Tool environment hashes are recorded
for comparison; running with another tool environment is a new attempt.

## Documentation and inventory

Official references checked October 23, 2026:
[cocotb simulator support](https://docs.cocotb.org/en/stable/simulator_support.html),
[runner API](https://docs.cocotb.org/en/stable/library_reference.html), and
[Verilator container installation](https://verilator.org/guide/latest/install.html).
Current cocotb documentation requires Verilator 5.036 or newer. The selected image
pins Verilator 5.042 and cocotb 2.0.1; actual tested versions and outcomes belong in
the VF-011 delivery record and generated environment artifacts.

Read-only inventory found Docker Desktop Linux engine 29.8.1 and Ubuntu 24.04 WSL2
with Verilator 5.020 and Python 3.12. Sandbox access required approved tool
escalation. WSL's simulator was below baseline; no global installations or changes
to unrelated containers were made. An initial project image source build was
cancelled to use the official prebuilt version instead.
