# Durable planner campaigns — VF-033A design

Status: design baseline 24 October 2026, updated 29 October with the finite immutable-experiment foundation below. The remaining round/provider/budget/scheduler interfaces are proposals, not implemented guarantees. Live Meta acceptance is unverified; source work and explicitly labeled mock-provider tests do not require live credentials.

## Implemented foundation — U2-DURABLE-EXPERIMENTS, 29 October

This source slice implements immutable experiment identity, not the full durable planner loop. New migration `8u2experiments` follows `7bf054adapter`. It adds `ExperimentRecord`, `AttemptExperiment`, a unique attempt/campaign index, and `Campaign.completion_policy` defaulting to `single`. It does not rebuild the old attempts table, alter prior migrations, apply a live migration, call a provider or launch a simulator.

`ExperimentRecord` stores a database ID, campaign/adapter, ordinal, admission key, unique executable wire ID, exact plan and plan hash, expected four-hash identity, typed parameterized contract snapshot, origin and creation time. One immutable `AttemptExperiment` association per attempt replaces the earlier physical-column proposal: composite foreign keys require both the attempt and experiment to belong to the same campaign. Missing associations fail closed at claim/finalization/read. A binding is created atomically with each service-created attempt. Direct SQL can create an unbound raw attempt row, but it cannot be executed by this service; total participation is intentionally enforced at the admission/claim boundary rather than claimed as a reverse database FK.

ORM update/delete guards protect records and associations. Migrated SQLite/PostgreSQL also install database triggers rejecting direct SQL updates/deletes. `metadata.create_all` alone installs ORM/schema constraints, not those migration-owned triggers. Only isolated SQLite migrations were executed for this delivery; the PostgreSQL trigger branch still needs its controlled operational gate.

Legacy migration first checks that each stored typed contract/parameter snapshot matches its existing contract hash, before schema changes. It copies the exact old plan/identity into a deterministic record and associates all existing attempts. Original campaign/attempt IDs, raw stored JSON, hashes, timestamps, finding/dispatch/replay references and artifact directories remain untouched. Invalid historical contract provenance is rejected rather than silently rewritten. Downgrade is allowed only for legacy-compatible single-plan history; sequential or multi-experiment history is refused rather than discarded.

The existing public single-plan API remains unchanged apart from additive `completion_policy` and attempt `experiment_record_id` metadata. Initial campaign plan/identity/contract read fields now come from its immutable first experiment. Claim, native/container worker preflight, result finalization, finding plan hash, historical evidence and replay resolve the attempt's bound record. Replaying a later child preserves that child's exact plan/identity and creates a new single campaign; it does not reuse the parent's initial seed. Replay creation retains the fresh-key/no-existing-campaign safeguards. Replay origins are recorded for newly created replay experiments.

Trusted source-only continuation primitives live in `core/planner_experiments.py`:

```python
resolve_attempt_experiment(session, attempt) -> ExperimentRecord
admit_experiment(session, campaign_id, plan, admission_key) -> ExperimentRecord
settle_sequence(session, campaign_id, *, reason) -> Campaign
```

`create_campaign(..., completion_policy="sequential")` is an internal foundation option, not exposed by the frozen create API. It still requires a genuine initial typed experiment; no placeholder plan or planless planner root exists. Admission locks the parent, preserves fixture/parameters/contract/checker/source binding, deduplicates admission keys, rejects wire-ID aliasing and allows only one active execution. A trusted retry may bind another attempt to the same immutable record after prior execution settles, with separate attempt IDs/evidence. Retry policy and budget reservations are not implemented here.

Sequential child completion/interruption leaves the parent running with null aggregate verdict. Findings retain their actual attempt/experiment identity; a later pass cannot overwrite a prior mismatch. Interrupted/non-design-blocked results prohibit automatic continuation. Explicit settlement requires no active child and a bounded reason code; cancellation revokes active leases without erasing completed history. These primitives do not promise automatic resume or schedule another model round.

UI consumers must gate seed-plan-dependent controls for non-single campaigns until a separately assigned experiment/detail API supplies authoritative per-attempt input. No such endpoint or planner API is introduced by this slice. Model-round records, policy/budget ledgers, feedback consumption, coordinator outbox, ambiguous-provider recovery, provider configuration and live acceptance remain the proposed work below. Accepted pure planner helpers will be integrated only in that next reviewed scheduler slice, with requested model identity snapshotted once per admitted round.

Actual verification: `python -m pytest tests/core tests/api -q --basetemp tests/core/.tmp-u2-final` using the shared `.venv` and approved loopback TestClient execution: **67 passed, 1 existing Windows-symlink skip, 1 existing Starlette/httpx warning, 34.96s**. Outcomes and runner callbacks are explicitly synthetic; no live database/broker/model/simulator was used. Detailed evidence: private delivery `U2-DURABLE-EXPERIMENTS.md`.

## Current boundary and selected architecture

`planner/controller.py` is a single-use, in-memory sequential loop. It validates FIFO proposals, reserves finite budgets, supplies bounded checker feedback, and checks returned identities. `planner/local.py` supplies cancellation-aware runner settlement and local atomic checkpoint files; those files explicitly do not promise resumability. `planner/models.py` and the current provider request schema are FIFO-only. A saved final report is useful evidence, but is not a durable scheduler checkpoint.

Before the October 29 foundation, `core/service.py` treated one campaign as one experiment: claim read `Campaign.plan`, finalization checked `Campaign.identity`, and a terminal attempt settled its campaign. The implemented foundation above replaces that execution authority with immutable per-attempt records while preserving single-plan behavior. RQ delivers work; the database owns attempts, leases, findings and event sequence. Evidence paths retain database attempt IDs, independent of adapter result IDs.

Keep one investigation engine: extend these records and transaction boundaries rather than introducing a second agent framework, a campaign per model round, or a simulator inside the API. Extract reusable, bounded planner decision functions from the controller; leave its local CLI compatibility intact. Do not wrap `InvestigationController.run()` in an RQ job and call its final report durable: a crash would lose its in-memory budgets, proposal fingerprints, calls, and results between checkpoints.

The durable coordinator advances one eligible persisted step at a time. Provider and simulator work run in separate worker processes. Completion writes a durable continuation intent in the same transaction as evidence; a reconciler can deliver it after an outage. A broker result or worker's private Python object is never the investigation's authority.

## Family and strategy scope

| Capability | First durable slice | Follow-up boundary |
| --- | --- | --- |
| FIFO manual single experiment | Existing behavior retained | No relabeling as model work |
| FIFO `mock_planner` investigation | Proposed initial implementation and tests | Operator-owned finite scripts; no uploaded Python or arbitrary reply endpoint |
| FIFO `meta` investigation | Same controller boundaries, readiness unavailable without service configuration | Explicit future provider/live-runtime grant; no automatic mock fallback |
| Arithmetic manual campaign | VF-054A source, independently reviewed; runtime/UI gates separate | Keep `arithmetic.v1` and valid/product sidecar |
| Arithmetic planner | Unsupported in this first slice | New family-specific proposal/context schema plus reviewed typed feedback; registry presence alone is insufficient |
| AXI-Lite planner or Vivado planning | Unsupported | Separate adapter/checker and planner capability review |

Persist the strategy actually used. `mock_planner` with a real simulator is mock model evidence plus real simulator evidence; `mock_planner` with a stub executor is entirely labeled test execution. Neither is Meta inference. Live provider failure returns a visible model-unavailable reason, never an unannounced strategy change.

## Proposed durable records

All names below are proposed, not existing schema. Use additive, separately reviewed Alembic revisions; do not rewrite migrations already applied to retained runtime data. Identity data and typed JSON retain explicit schema/application versions.

| Record | Minimum fields and invariants |
| --- | --- |
| Campaign extension | `strategy` defaulting to `single_experiment`; planner phase, stop reason, started/deadline/settled timestamps, control generation. Existing owner, adapter and event sequence remain authoritative. Legacy plan/identity JSON is retained for manual campaigns; planner campaigns have no fabricated seed experiment. |
| CampaignBinding | One per campaign: adapter ID, fixture ID, parameters, exact reviewed typed contract snapshot, fixed source/checker/contract hashes, source/application revision, policy version. Immutable after creation. A planner cannot select another fixture, source, checker, contract or adapter. |
| Experiment | Campaign FK, immutable exact typed plan, canonical plan hash, full expected four-hash identity, origin (`manual`, `planner`, `replay`), optional originating round FK. Unique accepted experiment per originating round. Parameters/fixture/requirements must match CampaignBinding. |
| PlannerPolicy | Immutable `InvestigationLimits`, provider/strategy ID, requested model, prompt/schema versions/hashes, feedback limit, optional operator-reviewed pricing and price timestamp. Model-visible limits cannot change controller policy. No credentials. |
| PlannerRound | Campaign FK, monotonic ordinal, state, lease/generation, heartbeat/deadline, input/context hash, optional accepted normalized decision/experiment FK, safe correction/stop code. Unique `(campaign_id, ordinal)`. A round is one admitted provider attempt, including retry/correction attempts. |
| ModelCall | Round FK unique, request model/version/hash and bounded private request snapshot, reservation ledger reference, sent/settled timestamps, usage, requested/returned model, latency, safe outcome. A call with an uncertain remote outcome remains explicitly unknown. No authorization headers or raw exceptions. |
| BudgetEntry | Campaign/round/experiment FK and unique admission key; resource kind, conservative reserved amount, actual measured/reported amount if known, status/source. Reservations and actual usage are separate columns, not one mutable balance. |
| FeedbackReceipt | Round and finalized attempt FK, result hash, bounded typed feedback snapshot/hash, safe correction, parent continuation version. Unique consumption of an eligible result by the next context. |
| WorkIntent | Unique `(campaign, step kind, step generation)` for coordinator/proposal work. Existing execution dispatch remains tied to an attempt. Sent status is delivery bookkeeping, not proof of execution. |

Every execution attempt must identify its immutable Experiment. A transition migration can backfill one Experiment per existing manual campaign, then populate each existing attempt's experiment FK without changing plan bytes, hashes, artifact directories, timestamps, leases or finding IDs. Verify the backfill before enforcing the FK's non-null invariant. Legacy `Campaign.plan`/`identity` stay compatible response fields for manual campaigns; proposed planner roots require an explicit strategy-aware nullable representation, not `{}` silently parsed as FIFO. Constraint relaxation and response changes need an explicit migration/API review.

Execution claim/finalization must read `Attempt.experiment_id`, not a mutable campaign-wide current plan. Finalizing one experiment must not automatically finish a planner campaign. Existing manual campaigns retain that settlement behavior through their strategy handler. Finding identity remains attached to its actual finalized attempt and experiment; a later passing result cannot erase an earlier mismatch.

## Concrete proposed service boundaries

These are interface sketches for the next implementation owner, not instructions to modify these modules in VF-033A:

```python
create_investigation(session, spec, idempotency_key) -> Campaign
advance_investigation(session, campaign_id, expected_generation) -> AdvanceResult
claim_model_round(session, round_id) -> ModelLease | None
finish_model_round(session, lease, normalized_outcome) -> bool
accept_proposal(session, round_id, lease, typed_proposal) -> Experiment
finish_execution(session, attempt_id, lease, typed_result) -> bool
cancel_investigation(session, campaign_id) -> Campaign
recover_investigations(session, now) -> RecoverySummary
```

`advance_investigation` performs no provider I/O or simulation. Under the campaign write lock it validates the persisted phase, current controls, deadline, binding and budget, then either settles or creates one uniquely keyed next intent. Delivery and claims may duplicate; step generations and leases guard side effects. A caller cannot submit arbitrary model completions, shell commands or results through public API routes.

The planner package should expose pure bounded helpers for context construction, schema-specific decision validation, proposal fingerprinting, legal Experiment construction, feedback construction and stop-policy evaluation. Reuse the existing controller's validation and limit semantics through these helpers rather than maintaining a separate weaker validator in the API. Registry descriptors validate the final typed experiment and trusted identities; an independently reviewed planner-capability map chooses the proposal schema. It is not inferred solely from an adapter's existence.

## Normal state and transaction flow

1. Create a draft investigation with immutable binding, strategy/policy, and idempotency identity. Reject unknown family/strategy combinations. The API returns before any expensive work. Start records a deadline and a unique coordinator intent.
2. Coordinator locks the campaign, checks control/deadline/budgets and builds a bounded context from committed binding and eligible finalized feedback. Reserve the full provider allowance and create the round/call/intent atomically. Hash and retain the exact bounded request body privately, without its authorization header. Record software, prompt and schema identity so later code changes do not invalidate request reconstruction.
3. A proposal worker claims the round, rechecks campaign generation and deadline, constructs the configured provider inside the service boundary, and performs one bounded call outside a database transaction. No simulator is reachable from this step.
4. Under the campaign lock, accept a result only for the current round lease/generation. Record usage/model/outcome before determining continuation. Validate finish reason, known usage bounds, exact requested model, typed decision, reviewed requirement IDs, fixture/parameters, legality and duplicate fingerprint. Invalid output receives a fixed bounded correction; raw invalid response text is neither feedback nor executable data.
5. Stop decisions settle with a stop reason. A legal experiment admission reserves its whole allowed cycle/time allowance, inserts immutable Experiment plus queued Attempt plus dispatch intent, and moves the planner phase to `waiting_execution`, all in one transaction. The model does not set the experiment ID, execution budget, fixture or checker. No second execution is admitted while one is active in the initial sequential policy.
6. The existing trusted runner worker rechecks installed provenance and executes the typed Experiment with a revocable cancellation token. Independent checker outcome and verified artifact record are finalized against the attempt lease and immutable expected identity. Persist measured cycles, actual tool identity, usage and a finding where appropriate. Finalization commits an eligible feedback/continuation intent, rather than directly making another model call.
7. A coordinator consumes each finalized attempt once. It builds bounded checker-derived feedback and evaluates stop-on-finding, unavailable execution, remaining limits and no-progress policy. A pass can lead to another proposal; timeout/build/harness/resource/unsupported outcomes stop the branch visibly and never become design defects. An already committed result is not rerun merely because the continuation message was lost.

Parent execution status remains distinct from design interpretation. Keep per-attempt verdicts authoritative. A planner campaign can settle normally on round/cycle limits with no overall design verdict; expose a separate `verification_summary` of scoped passes, observed findings and blocked attempts. Do not copy the last verdict onto a multi-experiment campaign or claim an aggregate functional pass. Existing manual campaign verdicts remain unchanged.

## Budget admission and deadlines

Preserve `InvestigationLimits` semantics initially: each provider attempt, including transient retries and invalid/correction replies, consumes one admitted round. Invalid and duplicate counters persist. Fingerprints persist across restarts. Reserve full allowed simulator cycles before dispatch; do not refund unused/failed/interrupted budgets. Actual cycles remain a separate measurement. Provider calls reserve input allowance plus output cap before sending; retain reservations on failure, unknown usage, retries, cancellation and process loss.

Use the existing conservative UTF-8 request-size allowance only with its existing caveat: it is not a verified provider tokenizer bound or an account-level hard billing limit. Report actual usage independently. Unknown prices are null, not zero. A configured dollar cap with unknown prices blocks provider admission. Store the operator-supplied rate/version when available; do not invent current model pricing. Provider-side spend controls remain a separate future readiness requirement.

Persist the campaign start/deadline from database time; initial wall-time semantics include queue delays, provider backoff and execution. Each step's allowed timeout is the minimum of remaining campaign time, its frozen per-step limit and its lease policy. Workers also use monotonic elapsed timing for bounded local work. Deadline expiration stops new admissions and requests cleanup of active work. Cleanup may finish after the admission deadline; display `cleanup_pending` rather than claiming the process stopped at the exact instant. Restarting or resuming cannot reset consumed reservations or extend the immutable deadline. Explicitly authorized new work uses a new campaign/policy.

## Cancellation, pause and recovery

Cancellation atomically marks the campaign terminal, increments control generation and revokes every active round/attempt lease. Queued jobs claim nothing. A provider cancellation closes the transport; it cannot prove the remote request was unbilled. Simulator cancellation uses the runner's token/process cleanup contract. Late output may remain private forensic evidence, but cannot create findings, feedback or a next round. Track cleanup settlement independently of the already revoked authorization.

Initial durable implementation should expose cancel and safe boundary stop. A future pause/resume API may persist a paused phase, but cannot freeze simulator memory or replay an uncertain provider call as if it never happened. Operator coordination pause forbids new claims/admissions; active bounded work is settled or interrupted under the same cleanup rule. Cancellation stays terminal; idempotent repeated start/cancel cannot revive it.

| Interruption point | Durable recovery rule |
| --- | --- |
| DB intent committed, broker unavailable | Reconciler redelivers; unique claim admits one step |
| Model round claimed, worker lost before confirmed send | Treat remote outcome as unknown once claimed; retain reservations, revoke lease, stop as interrupted/uncertain |
| Provider response received, not committed | Do not assert exactly-once provider execution or automatically replay the same call. Outcome/cost is unknown; a future authorized continuation consumes a new call reservation |
| Accepted proposal and dispatch committed, broker message lost | Redispatch same attempt; do not create a second experiment |
| Execution worker lost or lease expired | Mark interrupted without design verdict; revoke lease. No automatic rerun in first slice |
| Result committed, coordinator lost | Consume same persisted result/feedback receipt once; no model or simulator replay |
| Cancel races either completion | Campaign write lock plus control generation decides authorization; late lease cannot publish or schedule |
| Installed source/checker/contract changed | Block execution/continuation with provenance-change reason. Preserve history; never silently rebind |

Remote provider exactly-once semantics are not established by RQ or an internal idempotency key. Avoid a hidden automatic retry after an ambiguous crashed call. A known transient response can start a new bounded round only within the frozen retry policy; its previous reservation remains consumed.

## Credentials, feedback and public API sketch

Only the proposal service process reads normal provider configuration at construction. Reuse `MetaConfig.from_env()` without searching credential files. Keys never enter Campaign, policy JSON, request body snapshots, queue arguments, events, exports, planner context, simulator jobs or worker diagnostics. The simulator's minimal environment remains separate. Do not request keys while the human has deferred live use.

Feedback contains committed requirement IDs, execution status/verdict, measured coverage, bounded failure signature and database-scoped artifact references. Bind references to campaign/attempt/artifact IDs and hashes, not just runner-local names such as `artifact-0`. Do not send raw logs, environment paths, hidden fixture/fault labels, benchmark answers or stale superseded results. A structured summary is still untrusted data and cannot change policy/checkers. Historical results remain available through authorized evidence routes independent of provider availability.

Proposed routes, all requiring the existing local ownership boundary and idempotency on mutation:

| Route | Proposed shape |
| --- | --- |
| `POST /api/planner-campaigns` | Explicit schema `planner.campaign.v1`, adapter/project, strategy `mock_planner` or `meta`, reviewed parameters/objectives, bounded policy; no plan, secrets, model-selected fixture or raw completion |
| Existing campaign start/get/list/cancel/events | Strategy-aware response adds phase/stop reason, binding, reservations/actual usage, cleanup state and scoped verification summary; preserves manual fields |
| `GET /api/campaigns/{id}/experiments` | Bounded immutable typed experiments with origin/round and attempts |
| `GET /api/campaigns/{id}/planner-rounds` | Bounded safe round/call outcomes, hashes, reservations and reported usage; no raw provider bodies/headers |

Client strategy/model selection is restricted to operator-configured capabilities. A disabled live provider returns an explicit readiness/unsupported response; it never falls back to mock mode. Mock scripts are application-owned fixture IDs, not client Python/JSON completion upload. No reduction/repair endpoint or live model acceptance is included in this design slice.

## Small next implementation assignments and gates

1. **Durable experiment foundation:** separate additive migration/backfill and worker/service refactor so attempts bind immutable Experiments; manual routes remain identical. Test historical FIFO/arithmetic identities, artifacts, findings and replay-key isolation before adding a planner API.
2. **Stateless FIFO planner steps:** planner owner extracts pure context/proposal/result/stop helpers from the existing controller without weakening validation or local CLI cleanup. Add versioned capability selection; arithmetic planner stays unsupported. No provider call required.
3. **Mock-backed durable scheduler:** core owner adds policy/round/call/budget/feedback/intent tables, transaction handlers and sequential mock proposal worker. Prove duplicate delivery, crash-point recovery, no-refund accounting, cancellation/late results, no-progress and provenance drift in isolated SQLite and then controlled PostgreSQL. Stub outcomes are labeled; no model/simulator claim.
4. **Read/API slice:** expose explicit create/read/start/cancel/events plus safe summaries. UI owner adopts strategy-aware phases, costs and family capability gating. A standalone reviewed migration/response gate precedes deployment into the retained runtime.
5. **Provider boundary and future live gates:** reuse the bounded service-only Meta transport behind the same persisted call lifecycle, with `httpx.MockTransport` tests first. Live model use waits for explicit human readiness; simulator/runtime use waits for main's recorded serial slot. Replay and manual deterministic work remain usable without inference keys.

Acceptance requires crash tests around each DB/broker/provider/executor boundary, evidence that completed results are never overwritten, deterministic finite mock scripts feeding at least two checker-feedback rounds, unchanged independent checker/contract/source identities, no secret-bearing queue/storage content, legacy manual API compatibility, and explicit unsupported arithmetic planner behavior. Do not mark any production guarantee complete merely because these interfaces are documented.
