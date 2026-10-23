# Local API agreement (VF-020)

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

Current scope: one bounded curated FIFO experiment per campaign; localhost identity only. Planner-driven multi-experiment campaigns, replay/reduce/export endpoints, artifact download authorization, remote authentication, pagination cursors and automatic interrupted-run retry remain later integration work. The artifact references inside attempt results are metadata, not a download endpoint.

Official implementation references: [SQLAlchemy sessions](https://docs.sqlalchemy.org/en/20/orm/session_basics.html), [FastAPI heavy background task guidance](https://fastapi.tiangolo.com/tutorial/background-tasks/), and [RQ queue delivery](https://python-rq.org/docs/).
