# VerifForge shared interfaces — schema version 1

Owner: main coordinator. This preserves the initial FIFO wire contract in `veriforge.contracts`. Arithmetic has separate reviewed schemas and API/evidence support. AXI-Lite has accepted standalone execution; application adoption remains separate. See [ADAPTERS.md](ADAPTERS.md), [ENGINEERING_INTERFACES.md](ENGINEERING_INTERFACES.md) and [SUPPORT_MATRIX.md](SUPPORT_MATRIX.md).

## Execution boundary

`run_experiment(experiment: Experiment, output_dir: pathlib.Path) -> AttemptResult`

The RTL adapter owns this function. `output_dir` is a trusted controller-selected unique attempt directory, never a planner command. Fixture IDs: `fifo_correct`, `fifo_reset_bug`, `fifo_full_bug`, `fifo_order_bug`. Only installed curated sources may be selected. Unsupported installed fixtures must produce an explicit unsupported result. No input contains shell commands, arbitrary source paths, Python, or build flags.

`Experiment` fields: `schema_version="1"`, `experiment_id`, `fixture_id`, `contract_id="fifo-v1"`, `target="fifo"`, `objective_ids`, `seed=0`, `operations`, `parameters` (`width=8`, `depth=4`), and `budget` (`max_cycles=10000`, `wall_time_seconds=30`, `max_artifact_bytes=10485760`). Operation discriminant is `op`: reset/idle have `cycles=1`; write has `value`; read has no other fields; cycle has `reset=false`, `write=false`, `read=false`, `value=0`. Every operation is one cycle except reset/idle. At least one reset must precede traffic; reset has priority over simultaneous read/write. Read/write acceptance uses pre-edge empty/full flags: a full FIFO rejects writes even with a simultaneous read; an empty FIFO rejects reads even with a simultaneous write. Read observes the oldest queued item on an accepted edge. Data is unsigned and bounded by width. All operations, including idle/reset, count toward the cycle budget.

`AttemptResult` fields: `schema_version`, `attempt_id`, `experiment_id`, `execution_status`, `verdict`, optional `identity`, `tool_versions`, `trace`, `artifacts`, optional `failure_signature`, `requirement_coverage`, `usage`, `uncertainties`. Identity records source, checker, contract, experiment and environment SHA-256 hashes. Trace records cycle, operation index, acceptance, expected/observed data and flags. Artifacts use relative POSIX paths under the attempt directory, SHA-256, size and media type.

Execution statuses: queued, preparing, running, finalizing, completed, interrupted, cancelled, infrastructure_error. Verdicts: pass_within_scope, design_mismatch, property_failure, build_error, harness_error, timeout, resource_limit, unsupported, inconclusive. In-flight status has null verdict. Completed means execution settled, including timeout/build failure; it does not mean functional pass. Cancelled/interrupted/infrastructure_error cannot claim pass or design failure. Design mismatch/property failure require a failure signature; passing requires complete evidence identity. Tool versions must be actually observed. A missing tool is unsupported/infrastructure evidence, never a simulated pass.

## Identity and serialization

All models forbid unknown fields and are frozen. Collections use tuples. Integer inputs reject strings/bools; finite bounded floats reject NaN/infinity. `model_dump(mode="json")` / `model_validate_json()` provide wire round trips. `canonical_hash(model)` hashes canonical sorted compact UTF-8 JSON. Schema changes require a version update and coordination with consumers. Worker-generated identity is verified against trusted active revision/contract/checker records by the controller; hashes supplied by a planner are not authority.

`ProjectRevision` stores safe snapshot-relative source paths and source hashes, approved fixture ID, top module and revision identity. `BehavioralContract` stores reviewed FIFO semantics, requirements, checker identity and explicit assumptions. `Finding` links a failure signature and requirement to immutable attempt/evidence identities; an explanation alone is not a confirmed finding.

## Bootstrap

Use Python 3.12 and `uv sync --locked`; on sandboxed Windows set `UV_CACHE_DIR` to `.uv-cache` within the checkout. Baseline packages are locked in `uv.lock`. A dependency installation/import smoke check does not establish simulator compatibility. The RTL worker owns the real simulator spike and its version evidence.

Validated bootstrap: CPython 3.12.13, Pydantic 2.14.1, FastAPI 0.161.1, SQLAlchemy 2.1.6, Alembic 1.20.1, RQ 2.12.0, redis-py 6.4.0, HTTPX 0.28.1, cocotb 2.0.1, psycopg 3.3.6, pytest 9.1.1. All baseline modules import; offline lock consistency check succeeds. Contract tests: 42 passing, including HTTPX ASGI/FastAPI validation. On this sandboxed Windows host, asyncio socketpair loopback blocks; request tests require execution outside that sandbox. Set `TEMP` and `TMP` to `.uv-cache/tmp` when uv's Windows trampoline cannot use the default sandbox temp directory.

Official references consulted: [Pydantic models](https://docs.pydantic.dev/latest/concepts/models/), [uv lock/sync](https://docs.astral.sh/uv/concepts/projects/sync/), [cocotb installation](https://docs.cocotb.org/en/stable/install.html).
