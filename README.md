<div align="center">

# VerifForge

**Investigate engineering failures with bounded experiments, independent checks, and replayable evidence.**

[![CI](https://github.com/desanv01/VerifForge/actions/workflows/ci.yml/badge.svg)](https://github.com/desanv01/VerifForge/actions/workflows/ci.yml)

[Quick start](#quick-start) · [Workflow](#workflow) · [Current capabilities](#current-capabilities) · [Development](#development) · [Documentation](#documentation)

</div>

![VerifForge architecture: reviewed contracts and typed proposals enter a bounded controller; trusted execution supplies observations to an independent checker and replayable evidence.](docs/assets/veriforge/investigation-flow.svg)

<p align="center"><em>Original architecture illustration · not a product screenshot or execution evidence.</em></p>

VerifForge is a local engineering investigation assistant connecting specifications, source files, tools and test results. Its supported configurations grow from RTL into RISC-V, firmware, FPGA tools, PCB checks and cross-domain evidence. Start from reviewed requirements, run controlled experiments, inspect expected versus observed behavior, and retain the exact stimulus and evidence needed to replay a finding.

The model proposes experiments. The independent checker determines the verdict. A completed execution can contain a mismatch, timeout, or build failure; a passing result establishes only the checked scope.

## Workflow

| Step | What happens | What stays inspectable |
|---|---|---|
| Define | Bind a curated source revision to reviewed requirements and a checker | Contract, parameters, source and checker identities |
| Plan | Select typed stimulus and finite resource limits | Exact operations, seed, objectives and budget |
| Execute | Dispatch a bounded experiment to its trusted adapter | Tool versions, execution status, logs and observations |
| Check | Compare observations with an independent reference model | Expected/observed values, verdict and failure signature |
| Reproduce | Replay, reduce or export a curated finding | Original evidence, fresh attempts and remaining uncertainty |

Deterministic FIFO execution and replay require no model key. Model-driven planning is optional; its operational acceptance is separate from source review. The planner cannot introduce shell commands, choose arbitrary files, change the active checker, or enlarge its own budget.

## Current capabilities

Active development, as of 3 November 2026 MYT. These are scoped evidence and source-review states, not a completed release or benchmark. The [support matrix](docs/project/SUPPORT_MATRIX.md) separates Import, Execute and Repair gates. Further reviewed source updates were merged in [PR74](https://github.com/desanv01/VerifForge/pull/74) at `11f4665`, after CI passed for head `6fdf2b5`.

| Area | Accepted evidence / source | Boundary / remaining work |
|---|---|---|
| FIFO verification | Real correct-fixture pass, seeded reset-mutant mismatch, exact replay and independent checking | Curated installed fixtures; no arbitrary RTL uploads or model-discovered bug claim |
| Evidence workflows | Bounded reduction, curated candidate validation, exact replay and tested API export/download | Generated repair, current browser flow and clean release-snapshot reproduction remain open |
| API and local runtime | Family FIFO/arithmetic API execution; isolated native recorded readback: two admissions, 301 stored artifacts, zero execution rows, 23 HTTP responses and four downloads | Snapshot-specific results; native candidate API/worker integration, new four-case RISC-V admission, browser acceptance and general runtime adoption remain pending |
| Workbench | Approved light/dark source in [PR60](https://github.com/desanv01/VerifForge/pull/60); reviewed repair reports bind child plans/results, fractional budgets and source/configuration diffs, with campaign navigation | Source/jsdom scope; actual visual, keyboard, narrow-width and browser workflows remain open |
| Meta planner | Typed experiments, bounded role/runtime integration source reviewed; [six PG/RQ/process cases](docs/evidence/planner/2026-10-31/README.md) qualified with **mock HTTP** | Human-authorized live work: one bounded request returned HTTP500, with no typed completion, usage/cost evidence or accepted feedback. Quarantine is not successful recovery |
| Arithmetic pipeline | WIDTH=8, latency=2 control, latency/data mutants, API execution and exact replay accepted | Broader corpus, candidate regression and actual browser gate remain |
| AXI-Lite | [Fourth hosted gate](docs/evidence/axi/2026-10-30/README.md): seven bounded API/tool attempts, exact strobe replay, export and preservation/cleanup | 32-bit, four-register supported subset; local revision adoption, candidate RTL repair and browser gate remain |
| RISC-V | [Four-case PicoRV32/Spike gate](docs/evidence/riscv/2026-10-31/README.md): control, seeded divergence, exact recompiled replay and same-run saved object/ELF/binary replay | RAM-only RV32I smoke, not ISA certification/core repair. Saved replay rebuilds HDL/C++; new-format application import remains unadopted |
| Firmware | [Native-MMIO gate](docs/evidence/firmware/2026-10-30/README.md): control, seeded STATUS-address defect, curated header correction and exact saved-program replay | HDL rebuilt; no Spike MMIO, arbitrary firmware, generated repair, physical-board or full workbench claim |
| Vivado | 2026.1 synthesis/six reports and 344.797s routed characterization/ten reports accepted; 35/35 routable nets routed. Portable controller/runtime source enforces an external candidate-plan ticket | Exact snapshot: 12 inputs/6 outputs lack delays; 20 methodology warnings. Native candidate application binding and complete I/O/CDC/function/board/repair acceptance remain open |
| KiCad | Configured ERC/DRC correction; concrete fixed-command KiCad 10.0.6 backend source reviewed and merged in [PR73](https://github.com/desanv01/VerifForge/pull/73) | Four ERC/five DRC ignores unchanged; installed backend/native candidate/API qualification, exact mating/physical/electrical acceptance remain open |
| Cross-domain | Missing-observation projections and conservative literal-XDC parsing source reviewed | Common/unreviewed ancestry stays inconclusive; no independently demonstrated system discrepancy or repair |
| Evaluation | FIFO/arithmetic/bounded-AXI family harness connects nine-case frozen inputs to private collection/export and reproduction-manifest validation; legacy FIFO behavior retained | Source/fake-boundary acceptance; family classifications remain unresolved. Actual transport, calibrated measurements, model usage and clean tool reproduction remain open |

There is no hosted application release or general-purpose code-execution service. Newer database/artifact restore, independent connected-system improvement, accepted live Meta feedback, final video and release reproduction remain open. A successful build is not functional correctness, and a timeout is not automatically a design defect.

## Quick start

Use **Docker Engine with Compose v2** (Docker Desktop with Linux containers on Windows), **Git**, and **Node.js 24 / npm** for the web application. Source development also uses **Python 3.12** and **uv**. The container supplies the supported Verilator/cocotb combination; native Windows simulation is unsupported.

From a new checkout:

```sh
git clone https://github.com/desanv01/VerifForge.git
cd VerifForge

# Build the simulator base before the application image.
docker build -t veriforge-rtl:dev -f infra/rtl/Dockerfile .
docker compose up -d --build
docker compose ps -a
```

In a second terminal, from the repository root:

```sh
cd apps/web
npm ci
npm run dev
```

Open the [local workbench](http://127.0.0.1:5173). The API binds to [127.0.0.1:18000](http://127.0.0.1:18000/api/health), and Vite proxies `/api` there. Review the installed FIFO contract before creating and starting an experiment. Compilation counts toward its wall-time budget; cold builds need more time than the default 30-second runner allowance.

This is a local development recipe for the current checkout. It does not certify every newer API workflow. See [local runtime setup](docs/project/LOCAL_RUNTIME.md) for migrations, logs, runtime limitations and image updates, and [FIFO toolchain setup](docs/project/RTL_TOOLCHAIN.md) for direct execution/replay. Docker must be able to fetch the declared base images and dependencies during a first build.

Optional local configuration is documented in [.env.example](.env.example). Keep real values in an ignored local file; no model key is needed for deterministic use. The provided local database defaults and single local identity are for loopback development, not remote exposure.

To stop services while retaining local data:

```sh
docker compose stop
```

Named volumes preserve database history and artifacts. Removing volumes deletes that history.

## How it works

- **One investigation engine:** the Python backend separates contracts, campaign control, trusted adapters, independent checking and evidence workflows.
- **Independent authority:** reviewed contracts and checker identities remain fixed during an investigation. Planner intent and requested coverage never substitute for observed results.
- **Durable execution records:** PostgreSQL holds campaign authority; Valkey/RQ delivers bounded work to a separate worker. Attempts retain their own results and artifacts.
- **Replay without inference:** stored typed stimulus, source/checker/contract hashes and tool evidence let a finding be inspected and replayed independently of a new model call.

The frontend uses React, Vite, TypeScript and TanStack Query. RTL execution uses cocotb and Verilator. Each adapter reports its supported configuration and separates imported evidence, actual execution and independently checked repair.

## Development

Install the locked Python environment from the repository root:

```sh
uv sync --locked --dev
uv run --no-sync pytest tests/contracts -q
```

For the frontend:

```sh
cd apps/web
npm ci
npm test
npm run typecheck
npm run build
```

Choose tests for the component being changed. HTTP/async tests need local socket access; planner tests can launch controlled test processes. Real simulator and runtime suites have separate prerequisites and are not interchangeable with mock-provider or SQLite checks. See [CONTRIBUTING.md](CONTRIBUTING.md) for evidence expectations and [the CI workflow](.github/workflows/ci.yml) for the exact selected checks. The badge reports selected GitHub source checks; actual tool/model/runtime acceptance is recorded separately in the support matrix.

## Repository layout

```text
apps/web/                 Local React workbench
src/veriforge/            Contracts, engine, API, adapters, planner and evidence
targets/                  Curated HDL fixtures
tests/                    Component, acceptance and opt-in integration checks
infra/                    Simulator and application container definitions
migrations/               Database migrations
docs/project/             Interfaces, setup, support boundaries and implementation plan
```

## Documentation

| Start here | Reference |
|---|---|
| Local services and workbench | [Runtime](docs/project/LOCAL_RUNTIME.md) · [UI](docs/project/UI.md) |
| Contracts and adapters | [Shared interfaces](docs/project/INTERFACES.md) · [Adapter registry](docs/project/ADAPTERS.md) |
| Execution and evidence | [FIFO toolchain](docs/project/RTL_TOOLCHAIN.md) · [Evidence workflows](docs/project/EVIDENCE.md) · [API](docs/project/API.md) |
| Additional integrations | [Arithmetic](docs/project/ARITHMETIC.md) · [RISC-V / firmware](docs/project/FIRMWARE.md) · [Meta planner](docs/project/META_INTEGRATION.md) · [Vivado](docs/project/VIVADO.md) |
| Evidence and evaluation | [RISC-V results](docs/evidence/riscv/2026-10-31/README.md) · [Firmware results](docs/evidence/firmware/2026-10-30/README.md) · [Evaluation protocol](docs/project/EVALUATION.md) · [Provenance](docs/project/PROVENANCE.md) |
| Intended release scope | [Implementation gameplan](docs/project/IMPLEMENTATION_GAMEPLAN.md) |

## Contributing and security

Please include the affected contract, exact reproduction, tool versions and verification basis when reporting a problem. Separate mocked results from actual tool execution. See [CONTRIBUTING.md](CONTRIBUTING.md) and [SECURITY.md](SECURITY.md); never put credentials or private project evidence in public issues.

License selection is pending. No project license or license badge is asserted here.
