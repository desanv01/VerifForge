# Local API agreement (VF-020)

## Typed campaign family agreement (VF-054A)

U3 adds explicit `adapter_id:"rtl.axi_lite.v1"` with
`plan.schema_version:"axi_lite.v1"`. The four fixed AXI projects are
`axil_registers_correct`, `axil_aw_w_order_bug`, `axil_strobe_bug`, and
`axil_response_stability_bug`; their reviewed contract is `axil-registers-v1`
(`axi_lite.contract.v1`). Plans contain bounded exact pre-edge `cycles`, not FIFO
or arithmetic operations. The discriminator and descriptor both reject family
mismatches; omitted adapter/schema compatibility remains FIFO only. AXI catalog
and draft admission are source integration. `trace_artifact:"observed.json"`
names a typed `axi_lite.trace.v1` envelope with `observations`. AXI evidence
capability is independently gated: registration does not add AXI to health's
`evidence_adapters` or establish replay/export/reduction acceptance. The U3
registry/API tests create isolated drafts and invoke labeled stub dispatch;
they perform no simulator runs.

Campaign creation accepts `adapter_id: "rtl.fifo.v1" | "rtl.arithmetic.v1" | "rtl.axi_lite.v1"` beside `project_id` and `plan`. Omitted adapter remains legacy FIFO only. FIFO plans use `schema_version:"1"`; arithmetic requires explicit `adapter_id:"rtl.arithmetic.v1"` and `plan.schema_version:"arithmetic.v1"`. Registry descriptors validate installed fixtures and exact family schemas; cross-family payloads fail422. Arithmetic operations/contract/parameters are never coerced to FIFO. The common execution result preserves its native shape: arithmetic valid/product rows are in `arithmetic-trace.json` (`arithmetic.trace.v1`), with empty common FIFO-shaped `trace`.

Project/campaign responses add `adapter_id`; projects also advertise `experiment_schema_version`, `trace_schema`, and `trace_artifact`. GET contracts returns that family's versioned typed contract. Existing FIFO records are backfilled with `rtl.fifo.v1` by additive migration, retaining original hashes/stimulus. Campaign `contract_snapshot` uses its stored descriptor family and exact parameters. VF-054D connects the separately reviewed generic evidence hooks for FIFO and arithmetic; other families remain explicitly unsupported. Native/container dispatch is selected only by the trusted static descriptor; no client commands/imports/paths or execution-mode override exists. This is source integration, not permission to launch jobs outside the coordinator's runtime gate.

Migration `7bf054adapter` follows `6bf022evidence`. The registry hooks are `descriptor.fixture_source(fixture_id)` (exact trusted source-file hash and variant) and `descriptor.expected_identity(experiment)` (source/checker/contract/experiment hashes, no fabricated environment). Source metadata is frozen in the catalog; current installed identity is independently rechecked before execution and completion. Typed contract parsing uses the campaign's stored descriptor, rather than selecting a family from a client filename.

Example arithmetic create (with `Idempotency-Key`):

```json
{"adapter_id":"rtl.arithmetic.v1","project_id":"multiplier_correct","plan":{"schema_version":"arithmetic.v1","experiment_id":"arithmetic-example","fixture_id":"multiplier_correct","operations":[{"op":"reset"},{"op":"multiply","a":255,"b":255},{"op":"idle","cycles":2}]}}
```

This creates a draft. Start/cancel/events/attempts/findings retain the existing routes and semantics. The reviewed contract requires a drained or reset pipeline; the example includes two idle edges after the multiplication. An arithmetic mismatch names its own requirement (for example `ARITH_PRODUCT` or `ARITH_LATENCY`) and keeps valid/product evidence in its typed sidecar.

Base URL: `http://127.0.0.1:8000`. Start with `uvicorn veriforge.api.app:app --host 127.0.0.1`.
This milestone uses one local identity; it is not a remote authentication implementation.

All paths are under `/api`. JSON errors have `detail`. Campaign/attempt execution status is separate from `verdict` (null until checked); cancelled/interrupted jobs are never presented as passing.

| Method/path | Payload/response |
| --- | --- |
| GET /health | database-backed readiness |
| GET /projects | `{items: [{id,name,family,support_level,contract_id}]}` curated fixtures |
| GET /contracts/{id} | reviewed immutable fixture contract |
| POST /campaigns | `{project_id,plan: <typed experiment>}`; required `Idempotency-Key` header; creates draft |
| GET /campaigns | `{items: [campaign...]}` newest first |
| GET /campaigns/{id} | campaign including `attempts`, `findings`, `execution_status`, `verdict`, `version` |
| POST /campaigns/{id}/start | required `Idempotency-Key`; creates one durable queued attempt; retries return same campaign |
| POST /campaigns/{id}/cancel | required `Idempotency-Key`; revokes current attempt; late results ignored |
| GET /campaigns/{id}/events?after=0 | `{items:[{sequence,kind,payload,created_at}]}` ascending sequence |
| GET /campaigns/{id}/events/stream?after=0 | SSE `id: sequence`, `event: kind`, `data: JSON event`; supports Last-Event-ID, closes after terminal event |
| GET /findings/{id} | persisted checked mismatch |

Campaign: `{id,project_id,contract_id,execution_status,verdict,version,created_at,updated_at,plan,identity,contract_snapshot,attempts,findings}`. `contract_snapshot` is the reviewed shared `BehavioralContract` with the exact experiment parameters; `identity` freezes expected source/checker/contract/stimulus hashes. Attempt: `{id,execution_status,verdict,result,created_at,finished_at}`. Status: `draft`, `queued`, `running`, `completed`, `cancelled`, `interrupted`, `infrastructure_error`. A completed attempt may contain a mismatch or timeout verdict. Findings are `{id,attempt_id,status,verdict,result,contract_id,plan_hash}`; signature and evidence identity are inside `result`. Detail adds `campaign_id`.

Submission rejects unknown project/contract IDs, arbitrary paths/commands, and untyped stimulus. Idempotency keys are bounded to 128 characters; reusing a key with different create payload returns 409. List/event responses are bounded; events can be replayed with `after`.

Production baseline is PostgreSQL via `VERIFORGE_DATABASE_URL`; explicitly local fallback is SQLite `sqlite:///./veriforge.db`. Database migrations are required before startup. Redis/RQ dispatch is performed by a separate reconciler/worker, never by simulator work inside a request. Broker downtime leaves durable queued intent. Further setup and tested limitations will be appended with delivery evidence.

## Run locally

After the shared locked Python environment is installed, configure `VERIFORGE_DATABASE_URL` (PostgreSQL: `postgresql+psycopg://...`) and `VERIFORGE_REDIS_URL` (default loopback Redis database 0). `VERIFORGE_ARTIFACT_ROOT` defaults to `artifacts`. Both API and workers must share the same database and artifact location.

1. `python -m alembic upgrade head`
2. `uvicorn veriforge.api.app:app --host 127.0.0.1 --port 8000`
3. In a separate process: `python -m veriforge.core.worker --loop` to reconcile dispatch intent and stale leases.
4. In the supported Linux runtime: `rq worker --url redis://127.0.0.1:6379/0 veriforge`. Windows direct function execution selects the curated Docker bridge; native RQ process management should use the provided Linux runtime. See the runtime worker's `LOCAL_RUNTIME.md` for the integrated stack.

Example create body:

```json
{"project_id":"fifo_reset_bug","plan":{"experiment_id":"local-witness-1","fixture_id":"fifo_reset_bug","contract_id":"fifo-v1","objective_ids":["FIFO_BEHAVIOR"],"operations":[{"op":"reset"},{"op":"write","value":17},{"op":"reset"},{"op":"write","value":42},{"op":"read"}]}}
```

Submit with a fresh `Idempotency-Key`, then POST `/api/campaigns/{id}/start` with a key. Creating a campaign never executes a tool. Missing tools produce an explicit unsupported result from the worker; the API does not manufacture a successful run. `/api/health` checks database connectivity and identifies the separate-worker/local mode; it does not certify simulator, broker or model availability.

## Durability and recovery

The dispatch row and queued attempt commit atomically. The reconciler retries unsent records, and re-delivers still-queued sent records after 60 seconds to recover a broker loss. Delivery can duplicate; an atomic database claim authorizes only one worker execution. An RQ result is not campaign authority.

Workers heartbeat every 10 seconds. The reconciler revokes a running lease after 300 seconds without heartbeat and records `interrupted` with null verdict. Cancellation revokes the lease immediately; the runner polls a database-backed cancellation object and stops its bounded process. A cancelled/interrupted lease cannot later overwrite campaign state. Interrupted campaigns remain inspectable; make a new campaign with the exact persisted plan to retry, preserving original attempt history.

Every accepted result is validated as `AttemptResult` and compared against campaign-frozen source/checker/parameterized contract/stimulus identities at the finalization transaction. Only trusted in-process worker entrypoints can submit results; there is no public arbitrary result endpoint. Design mismatches create one observed finding per finalized attempt. Exact result evidence is persisted without allowing planner edits to active checkers.

SQLite uses foreign keys, WAL and serialized writes as an explicit local fallback. PostgreSQL is the intended concurrent deployment baseline; SQLite does not prove PostgreSQL operational behavior. Migrations are versioned and app startup never creates tables. Reviewed contract versions are immutable; a checker/contract change under an existing catalog version stops startup rather than silently replacing history.

Current scope: one bounded curated FIFO experiment per campaign; localhost identity only. Planner-driven multi-experiment campaigns, reduction/candidate endpoints, remote authentication, pagination cursors and automatic interrupted-run retry remain later integration work.

## Evidence endpoints (VF-022)

These routes require migration `6bf022evidence`, a shared `VERIFORGE_ARTIFACT_ROOT` for API/workers, and the existing reconciler/RQ worker. They use **database attempt IDs** (`campaign.attempts[].id`). `attempt.result.attempt_id` is an independent adapter record ID and is never a filesystem lookup authority. No request body or local path is accepted.

`GET /api/health` advertises `evidence_api: "1"` when these routes are installed. VF-054D also advertises `evidence_adapters:["rtl.arithmetic.v1","rtl.fifo.v1"]`. These are source capability markers, not simulator readiness. Older snapshots without the family array support only their previously qualified FIFO evidence routes; new clients must not infer arithmetic support solely from `evidence_api`. UI must keep evidence execution actions unavailable when the marker or required family support is absent.

| Method/path | Result |
| --- | --- |
| GET /attempts/{attempt_id}/artifacts | `{items:[{artifact_id,path,sha256,size_bytes,media_type,downloadable}]}` |
| GET /attempts/{attempt_id}/artifacts/{artifact_id}/download | bounded verified bytes as attachment; application/octet-stream; nosniff |
| POST /attempts/{attempt_id}/replay | `Idempotency-Key` required; 202 full Campaign with `source_attempt_id`, already queued |
| POST /attempts/{attempt_id}/exports | `Idempotency-Key` required; 202 export job |
| GET /exports/{export_id} | export job status |
| POST /exports/{export_id}/cancel | `Idempotency-Key` required; revoke queued/running job; terminal state unchanged |
| GET /exports/{export_id}/download | completed ZIP bytes attachment; application/zip; nosniff |

All table paths retain `/api` prefix. Export job: `{id,attempt_id,status,error_type,sha256,size_bytes}`. Status is `queued`, `running`, `completed`, `failed`, or `cancelled`. Hash/size stay null until publication; `error_type` is a sanitized exception class only, never a message/path. Same key and same source returns the same replay campaign/export job; key reuse for another source returns 409. Replay campaign state/attempts use the existing campaign shape and cancellation endpoint. Retrying a cancelled replay with the original key returns that same cancelled record; use a fresh key for a distinct replay request.

Unknown IDs, cross-owner access and unknown/excluded artifact IDs return 404. Missing/changed/unsafe evidence, mismatched provenance, unavailable replay installation, and downloading an unfinished/failed/cancelled export return 409 with `{detail: string}`. An export request may be accepted while filesystem verification later fails: observe its durable job status. `downloadable` means the artifact name belongs to the portable allowlist and a checked identity exists; the download still verifies bytes and content screening before serving.

Artifacts are selected by matching the stored artifact ID within that authorized database attempt. The server maps the attempt to `<artifact-root>/<DB-attempt-id>`; a URL cannot choose a directory or path. Completed evidence must match the immutable DB result and exact campaign experiment; source/checker/contract/stimulus hashes must agree with the campaign. All referenced artifacts are hash/size checked under fixed bounds: 100 files, 20 MiB total, bounded metadata JSON. Every symlink/junction and ancestor is rejected. Bytes are read into a bounded immutable response snapshot before returning, avoiding a later path reopen.

Only `stimulus.json`, `contract.json`, `fifo.sv`, `checker.py`, and `observed.json` are eligible for raw download. Shared evidence content screening rejects common credential/private-key/local-path markers and invalid UTF-8. Logs, job metadata and environment diagnostics are browse-only relative metadata. Screening is conservative protection for curated fixtures, not a universal secret classifier or arbitrary-upload support.

For arithmetic, the reviewed descriptor replaces `fifo.sv` with `multiplier.sv` and adds `arithmetic-trace.json`; a wrong-family source remains excluded even if referenced in the result. The sidecar is validated as `arithmetic.trace.v1`, with strict boolean/integer fields, complete cycles/operation indices and width bounds. It stays distinct from the empty common FIFO trace. Export manifests include the adapter and a `family_trace` reference. API DB ownership/exact DB-disk result/immutable campaign identity checks apply before these family hooks. Historical load/export does not require the current installation to match old source identities; replay does.

Export runs as a bounded RQ job (`job_timeout=60`) from a private DB-verified byte snapshot, using the shared safe exporter. Exclusive server-generated output paths cannot overwrite original evidence. Only a successfully published ZIP hash/size permits download. Duplicate deliveries claim one job; stale running jobs fail after 120 seconds and revoke publication. The reconciler retries queued delivery after broker failures; a failed export needs a fresh request key. Cancellation and late worker completion cannot overwrite terminal state.

Replay preserves the original experiment ID, seed, operations, budgets, parameters and four immutable identities. It records `source_attempt_id` and never changes the original campaign/finding. Installed curated source/checker/contract must still match at submission **and before worker execution**; changed installations reject exact replay. Before finalization, fresh replay files must match the returned typed result, submitted experiment and bounded artifact hashes. Replay calls no model. A fresh checked outcome is still required to claim reproduction; a queued replay is not a reproduced finding. Reduction/candidate validation remains outside this slice.

Replay request idempotency is owned by `ReplayLink`. Internal campaign creation uses a fresh server identity and explicitly forbids existing-record reuse; public campaign idempotency keys cannot alias or mutate a replay source or another existing campaign.

Official implementation references: [SQLAlchemy sessions](https://docs.sqlalchemy.org/en/20/orm/session_basics.html), [FastAPI heavy background task guidance](https://fastapi.tiangolo.com/tutorial/background-tasks/), and [RQ queue delivery](https://python-rq.org/docs/).
