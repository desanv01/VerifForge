# Meta planner integration — VF-030A

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

The executor is trusted application code. It owns unique attempt directories, dispatch/persistence, isolation, tool construction, credential removal, and independent checking. It must settle simulator/compiler child processes when its async task is cancelled. **Do not use `asyncio.to_thread` around the blocking RTL runner as a cancellation implementation.** Cancelling that thread does not terminate its subprocesses. API/core must provide a cancellation-aware bridge before claiming end-to-end cancellation. This worker does not edit those components.

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
