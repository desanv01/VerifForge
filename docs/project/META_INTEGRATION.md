# Meta planner integration — VF-030A / VF-030B / VF-032

Implemented locally on October 23, 2026. Python direct controller architecture selected by the implementation gameplan; no second orchestration framework was introduced. The build-agents skill was read; its exception for an explicitly selected framework applies.

## Verified protocol

Current official [authentication documentation](https://dev.meta.ai/docs/authentication) specifies `POST https://api.meta.ai/v1/chat/completions` with `Authorization: Bearer <MODEL_API_KEY>`. The adapter fixes this endpoint and refuses redirects, avoiding credential forwarding to another host.

The [Chat Completions guide](https://dev.meta.ai/docs/protocols/chat-completions) confirms `model`, `messages`, `max_completion_tokens`, the developer role, and the `choices[0].message.content` / `usage.prompt_tokens` / `usage.completion_tokens` response shape. The documented model default used here is `muse-spark-1.3`. Reasoning tokens count toward completion usage. HTTP 429 and server failures are transient; malformed requests are not retried automatically.

The [structured output guide](https://dev.meta.ai/docs/structured-output) and [official structured-output cookbook](https://github.com/meta-models/meta-model-cookbook/blob/main/01_api_fundamentals/04_structured_output.ipynb) establish `response_format={type: "json_schema", json_schema: {name, schema}}`. The Pydantic decision model supplies this schema. Local validation remains mandatory before execution. OpenAI compatibility is documented by Meta, rather than assumed from a model name. No OpenAI SDK dependency is needed: `httpx` sends the documented wire format.

## Configuration and access status

- `MODEL_API_KEY`: server process environment only, as documented by Meta. No credential files are searched or loaded. `SecretStr` excludes the key from configuration serialization and repr.
- `VERIFORGE_META_MODEL`: optional model identifier; default `muse-spark-1.3`.
- Prices: pass an operator-reviewed `Pricing(input_usd_per_million=Decimal(...), output_usd_per_million=Decimal(...))`. No price is inferred from coding subscriptions, historical/free-preview statements, or credentials.

The current worker's documented environment had no `MODEL_API_KEY` and no model override (presence-only check). `MetaProvider.available` is false and calls return `ProviderError("model_unavailable")` without network access. **No live Meta generation or model-to-simulator acceptance has been demonstrated.** HTTP mocks and fake-provider tests below are unit evidence only.

No raw HTTP body, raw invalid model output, secret, or underlying HTTP exception is included in public error codes or call records. The transport does not inherit proxy environment settings; installations requiring a corporate proxy need a separately reviewed transport integration. Do not enable HTTP header debug logging in the embedding service. Provider credentials must never enter simulator environments.

## Public boundary and integration

Import from `veriforge.planner`:

```python
from veriforge.planner import InvestigationController, InvestigationLimits, MetaProvider

controller = InvestigationController(
    MetaProvider(),
    execute_checked_experiment,  # async (Experiment) -> AttemptResult
    revision=approved_revision,  # trusted ProjectRevision; never supplied by model
    contract=reviewed_contract,  # trusted BehavioralContract
    limits=InvestigationLimits(max_rounds=15, max_total_cycles=100_000),
    control=read_current_control,  # optional synchronous callback returning "RUNNING"
)
report = await controller.run()
```

The executor is trusted application code. It owns unique attempt directories, dispatch/persistence, isolation, tool construction, credential removal, and independent checking. It must settle simulator/compiler child processes when its async task is cancelled. **Do not use `asyncio.to_thread` around the blocking RTL runner as a cancellation implementation.** Cancelling that thread does not terminate its subprocesses. VF-030B supplies `LocalRunnerExecutor` for local use, with explicit cancellation tokens and shielded settlement as described below. API/core integration remains separate; this worker does not edit those components.

For the installed FIFO runner, construct `reviewed_contract(parameters)` using `from veriforge.rtl import reviewed_contract`, and pass that exact typed contract to the controller. Its `canonical_hash(BehavioralContract)` is the runner/API shared identity; do not hash the older raw oracle dictionary or weaken identity checks. The controller remains provider/runner-neutral and receives the contract as a trusted explicit argument.

`cancel()` interrupts an active awaited provider/executor and awaits its cancellation cleanup. `stop()` finishes the current bounded operation and prevents the next operation. Non-RUNNING coordination control pauses at operation boundaries. Direct cancellation of the caller's task propagates normally; its executor cleanup still runs. Cleanup depends on the trusted executor honoring cancellation; this module cannot force-terminate arbitrary Python tasks or replace OS runner process controls.

The controller is single-use and sequential. Durable state, leases, outbox dispatch, resumed campaigns, API wiring, and event streaming remain core/API responsibilities. Persist `report` and immutable attempts; model unavailability must not disable deterministic replay.

## Authority and evidence

The model can return only an experiment proposal (reviewed objective IDs, seed, typed FIFO operations) or a stop proposal. It cannot select fixtures, set budgets, introduce commands, patch contracts, or produce verdicts. The controller binds fixture, contract, parameters, and experiment identity from trusted inputs. Current shared contracts support FIFO only; arithmetic and AXI-Lite remain unsupported here until shared types exist.

Fixture names and fault labels never enter model context. Context includes reviewed contract semantics, remaining cycle limits, a bounded recent feedback window, and a fixed correction after rejected output. Feedback retains execution status separately from verdict, actual coverage, failure signature, and artifact IDs. It omits raw logs, artifact paths, hidden labels, and source files. Feedback is explicitly untrusted data in the instruction message.

Every accepted experiment preserves exact operations/seed. Returned evidence source, contract, checker, and experiment hashes are checked against trusted active identities. Result requirement IDs and cycle usage are validated. A timeout/build/harness/unsupported outcome stops with `execution_blocked`; it never becomes a design mismatch. Findings only come from the independent result. A completed passing result is scoped evidence, not proof of arbitrary design correctness.

Model-call records include requested/returned model identity checks, prompt/schema hashes, latency, reported input/output usage, conservative reservations, actual calculated cost where known, and a safe outcome code. Accepted proposals become immutable Experiment records. Reports retain complete checked AttemptResults including tool versions, artifact identities, and failure signatures. Prompt version is `vf-planner-1`. The exact request is reconstructible from contract, prior feedback, limits, and prompt/schema code at the recorded source revision; external persistence should retain the corresponding application revision.

## Bounds and accounting

Rounds count every provider attempt, including retries and invalid responses. Correction attempts, transient errors with bounded exponential backoff/jitter, duplicate proposals, input bytes, response bytes, completion tokens, per-attempt cycles/time, campaign cycles/time, and model token reservations all have finite bounds. No automatic fallback is silently labeled Meta. Controller limits are frozen values unavailable to model proposals.

The controller reserves each experiment's entire permitted cycle count before dispatch, including failed/interrupted attempts. It does not recycle unused cycle budget. This is conservative and may stop early.

Before every model call it reserves UTF-8 request payload bytes plus 1024 framing units as an **estimated upper allowance for input tokens**, plus the full completion token cap. Reservations are not released after retries or invalid output. This is an admission estimate, not a verified Meta tokenizer bound. Missing usage or usage exceeding the allowance stops further work without executing that proposal. Provider-side billing cannot be proven to remain below a dollar/token ceiling without a verified tokenizer/provider quota; do not advertise this implementation as an account-level hard spend limiter. Reported actual usage remains separately recorded; unavailable usage on failed calls remains unknown.

Unknown price is explicit (`price_status="unknown"`, null costs). If a dollar cap is configured with unknown price, no model call is permitted (`price_unknown`). With supplied rates, pre-call cost reservations and actual reported token cost are retained separately. Cached-token pricing tiers or negotiated account rates are not automatically discovered; use conservative input rates. Hard account spend limits, if required, must also be enforced by the provider account.

Primary stop codes include `round_limit`, `cycle_limit`, `wall_time_limit`, `model_token_limit`, `input_size_limit`, `price_unknown`, `cost_limit`, `invalid_proposal_limit`, `no_progress`, `model_stopped`, `finding_observed`, `model_unavailable`, `model_usage_unknown`, `model_usage_limit_violated`, `model_identity_mismatch`, `result_identity_mismatch`, `execution_blocked`, `execution_unavailable`, `executor_error`, `cancelled`, `stopped`, and `paused`. None is itself a design verdict.

## Verification

Run `.venv/Scripts/python.exe -m pytest tests/planner -q`. Tests label both provider and executor doubles explicitly and use `httpx.MockTransport` for request/auth/schema/usage/error behavior. They exercise typed rejection, bounded correction/retry/no-progress, cycle/token/cost admission, feedback, evidence identity, timeout classification, cancellation cleanup, pause/stop hooks, immutable limits, and missing credentials. These tests do not establish live Meta schema acceptance, real tokenization, real simulator cancellation, or simulator/model benchmark performance.

Remaining live gate: configure an authorized Meta key, run one valid model proposal through the real cancellation-aware RTL executor, feed actual checked evidence into a subsequent round, and retain the report plus simulator artifacts. This is not claimed complete by VF-030A.

## Local CLI and runner bridge — VF-030B

Implemented October 24, 2026. The local CLI connects the reviewed FIFO contract, typed model proposals, real runner interface, independent checker results, and subsequent feedback without an API service. This is implementation readiness, not live model acceptance. Simulator starts in the shared development workspace must still respect the coordinator's serialized execution gate.

The CLI reads only normal process environment through `MetaConfig.from_env()`. If the human configures the ignored root `.env`, launch using the existing uv loader; the planner does not search or print credential files:

```powershell
uv run --env-file .env python -m veriforge.planner --fixture fifo_correct --rounds 2 --cycles 32 --cycles-per-round 16 --seconds 540 --execution-seconds 180 --model-seconds 60 --model-tokens 50000 --output-tokens 4096 --output artifacts/planner
```

This is the prepared bounded smoke command, **not an instruction to bypass readiness or the serial gate**. It admits at most two provider attempts and reserves at most 32 simulator cycles; validation/retry can consume rounds without simulation. It stops early on an independently observed finding or unavailable infrastructure. No fallback is labeled Meta. Check the boolean `MetaProvider.available` without printing config/key, obtain the coordinator's available gate, then use this command only when the human has indicated key readiness. Provider authorization errors still produce `model_unavailable`.

VF-032 readiness review (24 October 2026) supersedes the original 45-second execution / 120-second campaign example. The preserved VF-080C review records a roughly 107.78-second cold compile followed by a harness error, **not a functional pass**. A 45-second wrapper would cancel that work before completion. The revised 180-second per-execution allowance includes build time and gives margin over that observation. Two 180-second execution admissions plus two 60-second model admissions consume 480 seconds; 540 campaign seconds add 60 seconds of orchestration margin. These are finite proposed smoke bounds, not measured performance guarantees. Windows container startup/finalization and cancellation cleanup must settle before propagation and can extend elapsed time beyond a controller admission deadline. No new build was run to set these values. Generic CLI/controller defaults remain unchanged; use the explicit smoke arguments for cold-build readiness.

`--help` lists finite round, total/per-round cycle, model/output token, model/execution/campaign time, width/depth, and optional current-price/cost limits. Both input and output rates must be supplied together. Dollar caps without known rates stop before any provider call. CLI fixture choices come from installed `veriforge.rtl.runner.FIXTURES`; no arbitrary source, checker, command, adapter, or credential path is accepted.

Windows selects `run_container_experiment` against the prebuilt image. Linux selects `run_experiment`. Neither installs a toolchain on submission. `curated_fifo_inputs()` uses the RTL owner's `reviewed_contract(parameters)` and exact source identity formula: canonical encoded source-file SHA, curated variant, and parameters. It checks that installed source/checker/contract identities remain unchanged immediately before every runner invocation. The controller independently validates returned identities. Model context still excludes fixture labels.

`LocalRunnerExecutor` invokes the blocking trusted runner in a dedicated non-daemon thread and passes a private `threading.Event` as `cancel_event`. On coroutine cancellation it sets that token, shields the completion future, tolerates repeated task cancellation, and joins the thread before propagating cancellation. The runner owns process-group/taskkill/container cleanup. The bridge never accepts a successful result returned after cancellation. It retains original runner files and writes a sibling `discarded_after_cancel` settlement rather than overwriting immutable runner evidence. A faulty runner that ignores its token can delay cleanup; the bridge does not falsely claim that Python can kill such a thread. OS cleanup remains an independently testable runner responsibility.

SIGINT/SIGTERM requests controller cancellation, allowing a final report after runner settlement. CLI exit codes are 0 for ordinary finite completion/finding/model stop, 2 for unavailable/blocked/limited/error states, 130 for requested cancellation, and 1 for a local workflow failure. Output is safe JSON containing the workspace/report paths, stop code, and checked-result count. No API key, raw HTTP error, or model output is printed.

Each invocation creates `investigation-<uuid>` under `--output`, never reusing an existing workspace:

```text
manifest.json                   reviewed contract/revision/limits/model, no credentials
state.json                      atomic current local checkpoint
report.json                     final InvestigationReport on a settled controller run
rounds/round-0001/
  experiment.json               exact controller-bound plan before execution
  settlement.json               delivered / discarded / error and runner settlement
  runner-result.json            returned result, pending controller identity checks
  attempt/                      empty on entry, exclusively owned by RTL runner
    experiment.json, result.json, stimulus.json, contract.json, ...
```

Round directories count executed proposals; exact `experiment_id` maps them to the controller's model round (rejected proposals do not allocate a runner directory). Runner evidence layout remains compatible with the evidence module. `runner-result.json` is not itself a validated finding: the final report contains only controller-accepted checked results. Cancellation can retain a late successful raw runner file under `attempt/`; its sibling settlement excludes it from accepted feedback. Inspect settlement and the final report before using a runner artifact as campaign evidence.

Checkpoints use same-directory atomic replacement and file flush/fsync. Completed attempts remain inspectable after a crash. This is **not** DB durability, a lease/outbox implementation, full power-loss safety across directories, or automatic resume. A force-cancelled caller task waits for runner cleanup and writes an interrupted checkpoint; it may have no final report. Hard process/OS termination can leave a running checkpoint and requires inspection. Normal controller cancellation produces a final report.

Controlled verification: planner tests inject explicitly fake providers and runners, test two-round feedback and local report persistence, repeated cancellation and discarded late success, external cancellation, identity changes before dispatch, missing credentials, CLI argument bounds, and actual Python child/grandchild termination through the RTL `_bounded` primitive. They start **no simulator or Docker container**. Actual Meta schema acceptance and actual model-to-RTL evidence remain pending authorized credentials and the serialized live gate.

## VF-032 current account and execution prerequisites

Official documentation rechecked on 24 October 2026 still documents the fixed [Chat Completions endpoint and request parameters](https://dev.meta.ai/docs/protocols/chat-completions) used by the adapter. The [models page](https://dev.meta.ai/docs/models) lists `muse-spark-1.3` as a current supported default. It is the Standard-mode identifier; do not silently select the separate Contributor identifier. [Authentication](https://dev.meta.ai/docs/authentication) specifies a dashboard-created API key in the Bearer header. The adapter treats the key as opaque rather than enforcing a historical example format.

An authorized [Model API account](https://dev.meta.ai/docs/quickstart) and valid key are required. Current [billing setup guidance](https://dev.meta.ai/help/billing/set-up-billing) calls for a team admin to configure a payment method and account/business information. The public [pricing page](https://dev.meta.ai/docs/pricing-rate-limits) describes token-based pay-as-you-go billing; a coding subscription or historical preview statement does not establish API credit. Account billing, model permissions and quota remain unverified locally. User/account-specific credits must be confirmed by the human in the dashboard; no credit or card data is inspected here. [Error guidance](https://dev.meta.ai/docs/error-handling) distinguishes billing failure (402) and permission failure (403); the adapter preserves safe status codes or `model_unavailable` without printing server bodies. No automatic retries of billing/permission errors are performed.

Readiness check result: the root `.env` was confirmed ignored by Git. A bounded, read-only parser examined only the literal `MODEL_API_KEY` assignment and emitted booleans: process key nonempty **false**, ignored-file key nonempty **false**, parse ambiguous **false**. No value, file content, inferred credential length, or credential-derived hash was printed. No shell evaluation, interpolation, file import, inference request, network authentication probe, simulator, Docker job, or token spend occurred. This is a local presence result, not an account-authentication result.

Exact pending live prerequisites are: the human configures a valid authorized key locally and confirms readiness; the human verifies account billing/credit, selected model permissions and quota; main grants an explicit serialized model/simulator slot after runtime readiness; independent VF-053 bridge review is accepted or any concrete finding is resolved. Then an authorized bounded run must retain live usage, accepted plan, actual checker/tool evidence, next-round feedback, final stop reason and cleanup outcome. A model stop, invalid proposal or model timeout may consume both rounds without producing two checked results; two checked results with a follow-up request containing real prior feedback are the feedback-loop acceptance evidence. Missing access does not block deterministic work or this documentation delivery.
