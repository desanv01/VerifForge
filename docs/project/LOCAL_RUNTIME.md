# Local Linux runtime (VF-021)

This development stack uses PostgreSQL for durable state, Valkey for RQ delivery,
and the curated Linux Verilator worker. Only the API publishes a port, bound to
`127.0.0.1:18000`. The host Vite UI can proxy `/api` to that address. No model key
is needed for deterministic fixture execution.

## Start and inspect

From the repository root with Docker Desktop's Linux engine available:

```powershell
# Build this only if veriforge-rtl:dev does not already exist.
docker build -t veriforge-rtl:dev -f infra/rtl/Dockerfile .
docker compose up -d --build
docker compose ps -a
docker compose logs --tail 100 migrate api reconciler rtl-worker
docker compose exec -T api python -m pytest tests/integration -v -s
```

`scripts/local-runtime.ps1` provides `up`, `status`, `logs`, `test`, and `stop`.
Optional `.env` settings are listed in `.env.example`. Use a URL-safe local
PostgreSQL password. This single local identity is for development only.
The container still listens on port 8000; host port 18000 avoids another local
application already publishing port 8000 on the tested machine.

Compose defaults to project `veriforge-local`. Its network and named volumes
(`postgres-data`, `valkey-data`, `artifacts`) are scoped by that project name.
Do not use another application's database or broker. `docker compose stop`
preserves containers and data. Avoid `down --volumes`: it destroys evidence and
database history. No runtime script deletes volumes.

## Runtime contract

`migrate` runs `python -m alembic upgrade head` after PostgreSQL is healthy. API
startup waits for migration success, then seeds the curated catalog. Reconciler
and worker start after API and broker readiness. The API creates a durable draft;
`POST /api/campaigns/{id}/start` creates the durable queued attempt. A separate
reconciler delivers that attempt ID through RQ. The worker claims it in the
database, executes the curated RTL fixture, checks evidence identity, and
persists its checked result. Queue retries cannot authorize another claim after
completion or cancellation. API details: [API.md](API.md).

The app image extends the existing `veriforge-rtl:dev` image; it does not rebuild
the simulator. Its own `/opt/app-venv` uses Python 3.12 and `uv sync --locked`,
including the locked development test dependency. Spike dependencies in the base
image do not override this environment. Source/fixtures/migrations are copied
into the app image: rebuild after source changes. Only attempt artifacts use a
writable shared volume. There is no Docker socket mount or arbitrary uploaded
code interface. The RTL runner uses an environment allowlist before launching
the simulator, excluding database/model credentials.

The API healthcheck verifies database access. PostgreSQL and Valkey have native
readiness checks. Reconciler and worker restart on process exit; runtime logs and
persisted events show job progress. Health alone is not functional verification.
API/reconciler/worker currently share one image including the compiler, suitable
for this local milestone; they are separate processes and containers.

## Verification and limitations

The opt-in `tests/integration/test_local_runtime.py` requires the running stack
and PostgreSQL URL. It sends real HTTP requests and real duplicate RQ deliveries,
expects correct FIFO pass and reset mutant mismatch, verifies evidence and event
ordering, and checks cancellation cannot be revived by late queue delivery.
It skips outside PostgreSQL configuration; skipped tests are not acceptance.
Campaign and attempt IDs are printed with `-s` for later database inspection.

The first baseline acceptance is deliberately isolated from subsequent API work.
`infra/app/Dockerfile.baseline-acceptance` extends the recorded local VF-020 app
snapshot, installs the corrected shared Python library, and copies only the RTL
owner's files and integration tests. Select it with
`VERIFORGE_APP_IMAGE=veriforge-app:baseline-acceptance` and `docker compose up -d --no-build`.
This image is an acceptance checkpoint, not proof of newer source revisions.
Ordinary builds use `infra/app/Dockerfile` and the corrected RTL base image.

Tested versions and actual execution evidence are recorded in
`.coordination/deliveries/VF-021.md`. This stack does not establish live model or
Vivado support, public authentication, arbitrary RTL upload support, or a hosted
release. Current implemented runtime scope is the installed FIFO fixtures.

Baseline acceptance on October 24, 2026: three real integration tests passed in
31.44 seconds. Correct FIFO passed, reset mutant produced a checked mismatch,
duplicate delivery and cancellation passed. Tests used explicit120s simulator
budgets; default30s clean-build compatibility is not established. Evidence and
JUnit XML: `infra/app/evidence/vf021-baseline/`. This proves the preserved baseline
image only, not later API source revisions.

Official references: [Compose readiness dependencies](https://docs.docker.com/compose/how-tos/startup-order/)
and [uv Docker integration](https://docs.astral.sh/uv/guides/integration/docker/).
