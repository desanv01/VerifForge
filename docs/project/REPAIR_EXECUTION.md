# Durable family repair execution

The arithmetic and bounded AXI-Lite repair API persists candidate-validation
cases and reduction trials as ordinary campaigns, experiments and attempts.
It never runs engineering tools in an HTTP handler. FIFO retains its existing
evidence workflow; these durable routes do not expand the supported families.

The original attempt must have checked, owned evidence. Candidate requests name
an installed fixture; the service derives the witness, neighboring cases and
regressions without accepting replacement checkers or expected outputs.
Prepared/manual/model-generated provenance remains explicit. A queued job or
passing witness is not a validated repair.

## Trusted startup and storage

Revision `14u3repairjobs` adds `repair_jobs` and `repair_cases` to revision13.
It does not alter existing investigation/evidence tables. Import compatibility
explicitly accepts these two revisions; unknown future revisions remain refused.
The migration preserves history and refuses automatic downgrade. Application
startup does not create tables or apply migrations.

Execution is disabled by default. A trusted launcher must construct one
`RepairRuntime` configuration with a fresh `RepairPolicy` reader, a canonical
control reader composed with its resource authority, and an absolute artifact
root. Pass it as `repair_runtime` to `create_app`, together with the matching
`artifact_root`. Install the matching configuration in every executing worker
and dispatcher/reconciler process with `repair_runtime.install`, or explicitly
inject it into their entrypoints. RQ messages continue to carry attempt IDs only.
An API process's installation does not configure a separate worker process.

The policy identity, bounds and artifact root must agree across roles. No
environment switch, browser request or candidate fixture ID enables execution.
The API rereads its policy for admission; dispatch, claim, execution polling and
finalization recheck current authority. Revocation fences further work.

Jobs retain policy and suite hashes, original evidence, each plan/result,
attempt and execution lease, budgets, deadline, candidate provenance and final
report. A database uniqueness constraint permits one queued child per job.
Continuation commits the preceding terminal state before dispatching another
case. Ambiguous or interrupted effects are quarantined, not automatically retried.
The final report separates execution state from the independently checked result.
Worker finalization alone leaves external process cleanup explicitly unverified.

## API and acceptance boundaries

- `POST /api/attempts/{attempt_id}/repairs` uses an `Idempotency-Key` and a typed
  candidate or reduction request. The response is durable job state, not execution.
- `GET /api/repairs/{job_id}` returns cases, identities, reservations and status.
- `POST /api/repairs/{job_id}/cancel` records cancellation and fences continuation.
- `GET /api/repairs/{job_id}/report` returns a settled report and its checked hash.

Developer evidence covers synthetic family observations, isolated SQLite,
fake worker/dispatcher execution, reopen/reconciliation and in-process HTTP.
An isolated SQLite upgrade preserves catalog rows and existing import support.
These checks do not establish PostgreSQL concurrency, broker delivery, native
candidate execution, physical cleanup, browser acceptance or complete recovery.

Before operational enablement, qualify the current schema and trusted process
binding under an explicit resource grant. Capture a new backup/restore baseline
including both added tables. Earlier revision13 evidence and its 23-table
restore requirements remain historical; they cannot establish a revision14
restore. Keep the original data and evidence intact. Full feature review and
real tool/application acceptance remain separate gates.
