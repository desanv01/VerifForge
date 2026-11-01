# Evaluation protocol and evidence register

Prepared 29 October 2026 for U10. This is a proposed evaluation protocol plus a receipt-based historical summary, **not an executed benchmark**. No new model, simulator, browser, restore or engineering-tool run was performed to write it. The [implementation gameplan](IMPLEMENTATION_GAMEPLAN.md) and [support matrix](SUPPORT_MATRIX.md) target sequences 1–6; U11/sequence 7 is stretch and sequence 8 is deferred. Missing gates below remain open until their artifacts are reviewed.

Reconciled 1 November 2026 MYT after direct human resume. Earlier dated failures and snapshot-specific acceptances remain historical; the current scoped milestones below supersede obsolete preparation-only statuses. No new tool, model, browser or restore execution was performed for this documentation update.

## Current accepted milestones and remaining boundaries

- [Four-case RISC-V run 41202693632](../evidence/riscv/2026-10-31/README.md), source `c14525e3791569215ba385a1c5582d52d0b8858f`: control, seeded mutant, exact recompiled replay and same-run saved object/ELF/binary replay independently accepted. Saved replay omits RISC-V program compilation; Spike executes and Verilator HDL/C++ rebuilds. New four-case recorded-import candidate source is accepted but application adoption remains blocked by legacy persisted-validator compatibility; no ISA certification or core-repair claim.
- [Fourth hosted AXI run40948230223](../evidence/axi/2026-10-30/README.md) accepted seven bounded API/tool attempts, exact strobe replay/export and preservation/cleanup. Earlier local and hosted failures remain historical; browser/candidate repair and local revision adoption remain separate.
- Vivado 2026.1 completed one bounded retry in 190.187 seconds; independent audit accepted synthesis and six post-synthesis report captures for the exact private design snapshot. Missing I/O delays, asynchronous-input safety, routed timing, functional correctness, repair and workbench integration remain unaccepted. Private HDL/reports are not published here.
- Native API r2 on frozen `c14525e` independently accepted isolated recorded import/readback: two admissions, 301 stored artifacts, zero execution rows, 23 HTTP responses and four exact downloads. This uses historical RISC-V/firmware bundles, not the new four-case bundle. It is not browser acceptance, new engineering execution or general runtime adoption; r1 failures remain preserved.
- [Fifth PostgreSQL qualification run 40958744363](../evidence/planner/2026-10-31/README.md) accepted six bounded PostgreSQL/Valkey/RQ/process cases with **mock HTTP**: duplicate delivery, resource fence, known/unknown transport, fixture pause, late cancellation, and worker-death quarantine with blocked replacement. Quarantine is not successful automatic recovery; no live Meta or populated-database restore follows.
- Approved light/dark workbench source merged in [PR60](https://github.com/desanv01/VerifForge/pull/60), merge `b81bed3a5e1f8046eeca372fccc72b66247ee8c8`, after [CI41210807765](https://github.com/desanv01/VerifForge/actions/runs/41210807765) passed web tests/types/build at `aeb6ca0788d78f9ccc7da9f2bec7528e6fee5abc`. Original Windows conversion failure remains historical; later source integration does not establish actual browser/visual/keyboard/narrow-width acceptance.

Live Meta key readiness remains deferred without repeated requests. Newer restore, generated repair, independent connected-system improvement, comparative evaluation results, release-snapshot reproduction and final video remain open.

## Historical evidence and its original acceptance scope

Raw acceptance records are retained locally and are not published in this document. The linked test source describes reproducible procedures; it is not a public raw-result receipt. Overlapping test counts must not be summed into a project accuracy or unique-test total.

| Recorded scope | Observed result | What this does not establish |
|---|---|---|
| FIFO directed simulator slice, 24 Oct | Correct pass; reset mutant mismatch; three replays with the same trace/signature. Mutant's first empty-flag divergence is cycle 14. [Tests](../../tests/rtl/test_runner.py) | Broad bug coverage or model discovery; descriptive fixture names disclose the seeded fault |
| FIFO evidence workflow, 24 Oct | Seven real runner results; one passing reduction trial rejected, same-semantic witness reduced from 18 to 14 operations; three distinct curated-candidate checks pass; exact portable export. Independent saved-evidence audit accepted | Global minimality, a generated patch, or a new execution recovered from the ZIP |
| Arithmetic directed slice, 24 Oct | WIDTH=8, latency=2: correct pass, latency mismatch at cycle 2, data mismatch at cycle 6 (65025 expected, 1 observed). All three have 15 observed cycles. [Tests](../../tests/arithmetic/test_live.py) | Other widths or general arithmetic correctness; three cases are not three independently discovered real bugs |
| FIFO HTTP/database/queue baseline and evidence upgrade, 24 Oct | Real correct/mutant, duplicate delivery and queued cancellation; selected artifact/export/download and one exact API replay passed on recorded images. [Baseline](../../tests/integration/test_local_runtime.py), [evidence tests](../../tests/integration/test_evidence_runtime.py) | Current checkout adoption, running-process cancellation latency, restart recovery, or isolated restoration |
| AXI-Lite resumed fixture gate, 29 Oct | Six actual cases plus the preserved 149-cycle October24 correct control independently accepted: four correct passes, two property failures and one data mismatch. All 112 artifact references verified. Independent audit retained locally. | Seven new executions, broad AXI correctness, model discovery, or completed API/browser/replay/repair/export gates. The interrupted October24 second case still has no verdict |
| Original-backup isolated restore, 29 Oct | Actual restore independently accepted: original October24 backup, revision c67f9c9f19e5, all seven row fingerprints and initial schema checks; saved owned-resource stop/removal receipts verified | Restoration of newer database data, external artifacts, broker jobs, or application recovery |
| Family HTTP/database/queue execution and export, 29 Oct | Five actual attempts independently reviewed: arithmetic WIDTH=8/latency=2 control pass, latency mismatch at cycle 2, product mismatch at cycle 6 (65025 versus 1), retained FIFO replay and exact arithmetic product replay. Native artifacts, six downloads and portable export verified; all 39 prior artifact files and earlier rows preserved | Five distinct bugs, fresh browser acceptance, current-source blanket adoption, generated repair, AXI application adoption or release-head replay3/3 |
| Planner state and transport source, earlier checkpoint | Durable state/transport source and synthetic SQLite checks accepted; fifth actual PG/RQ/process qualification is separately recorded above | Mock HTTP is not live Meta; operational adoption, populated restore and successful owner-loss recovery remain separate |
| Vivado readiness and synthesis attempt, 29 Oct | Startup cache-cap failure, later synthesis storage stop and below-2-GiB retry block remain historical; October31 synthesis/six-report acceptance supersedes that blocked status | No routed timing, complete I/O timing, asynchronous-input safety, functional correctness or repair signoff |
| KiCad configured-check correction, 29 Oct | Original 10.0.6 control had26 ERC and26 DRC/parity findings. Distinct grid/net-name/blank-field candidate produced zero reported findings under unchanged configured checks; independently reviewed with 24 endpoint semantics and copper/pads preserved | Every rule enabled: four ERC and five DRC checks remain ignored as before. Exact MPN, cable, physical/electrical acceptance and product integration remain unresolved |
| Hosted RISC-V curated smoke and seeded mutant | [Run 40920450439](https://github.com/desanv01/VerifForge/actions/runs/40920450439): actual PicoRV32/Spike control and recompiled control each 17 retirements/68 cycles; seeded mutant first divergence order 1/cycle 14, expected `0x8000fef0`, observed `0x8000fef1`. Independent saved-evidence audit accepted; [retained public CI evidence](../evidence/riscv/2026-10-30/README.md) | Full ISA certification, model discovery, executable repair or firmware/MMIO execution |
| RISC-V curated recompilation gate, historical October30 | Exact recompiled controls accepted; first run40912735570 remains failed on random temporary-object ELF metadata. October31 four-case saved-program acceptance is separately recorded above | Arbitrary program replay, ISA certification or release-snapshot hero replay3/3 |
| Firmware native-MMIO actual gate | [Run40929665294](../evidence/firmware/2026-10-30/README.md): control/candidate55 retirements/218cycles; seeded bad39/166, first STATUS conflict cycle95; curated header restoration and exact saved bad/candidate program replay accepted, HDL rebuilt | Arbitrary firmware, Spike MMIO, model-generated repair, physical board or browser acceptance |
| Native cross-domain graph bridge | Corrected source independently accepted for provenance/relationship authority and conservative ancestry checks; retained native graph/candidate comparisons remain inconclusive/no demonstrated improvement | Authenticated review authority, physical mapping, independent cross-domain discrepancy, affected-tool rerun or repair |

[PR #9](https://github.com/desanv01/VerifForge/pull/9), [#15](https://github.com/desanv01/VerifForge/pull/15), [#17](https://github.com/desanv01/VerifForge/pull/17), [#18](https://github.com/desanv01/VerifForge/pull/18), [#19](https://github.com/desanv01/VerifForge/pull/19), [#20](https://github.com/desanv01/VerifForge/pull/20) and [#21](https://github.com/desanv01/VerifForge/pull/21) are merged after their current-head CI. Native investigation [PR #22](https://github.com/desanv01/VerifForge/pull/22) merged as `496c89fb72aa2d4e6ff322dad9db279fba81cb21` after [CI run 40919086170](https://github.com/desanv01/VerifForge/actions/runs/40919086170): its frozen source checks report 242 passed and two private-evidence skips, with no runtime/browser adoption. These counts overlap other suites and are not a project total. The first local AXI overlay was blocked before build at 632.54 MiB headroom below its 768 MiB floor. A later image build and source verification succeeded; API data/artifacts were preserved during adoption, but consumer startup hit the host-memory abort floor. API, queue worker and reconciler were quiesced at 499 MiB headroom below the 512 MiB abort floor. Subsequent sequential recovery of the existing verified `e8eb` containers succeeded: healthy API with three adapters, idle queue worker and running reconciler; schema `7bf`, all prior nine-table rows and all 110 artifact files were preserved. No new AXI campaign/attempt or browser acceptance follows. Merge status, runtime adoption, actual tool results and browser observations remain separate records.

[PR #23](https://github.com/desanv01/VerifForge/pull/23) and [#24](https://github.com/desanv01/VerifForge/pull/24) are merged; [#25](https://github.com/desanv01/VerifForge/pull/25) merged as `1db9916dff8e2fbdf181727bc8ba4a4862b254fa` after [current-head CI 40922456584](https://github.com/desanv01/VerifForge/actions/runs/40922456584). Source publication does not establish firmware execution or application/browser adoption.

Historical runtime image acceptance is snapshot-specific. For example, the reviewed FIFO evidence image was `sha256:a37049a056a33392a9df79c41bafae23f61bc0c398756a390046191324126433`; it does not attest to today's dirty source. Earlier failures/timeouts remain part of the record, not silently replaced by successful retries. Mocks, pure reference calculations, actual tools and browser observations are separate evidence tiers.

## Freeze before measuring

U0 reconciles the source/runtime snapshot; U1 freezes interfaces/checkers; U2 supplies the durable model loop; U3–U8 supply demonstrated cases; U9 supplies browser evidence. U10 evaluates that identified product. No source or evaluation-label edits are permitted after a run starts without creating a new evaluation revision.

The freeze manifest must identify the commit and dirty-patch hash if applicable, per-domain source/contract/checker hashes, exact tool/image/compiler versions, requested and returned model/provider, prompt/schema hashes, generation parameters, strategy implementation hashes, case list, predetermined seeds/repeats, information policy and budget profile. Keep secrets out. A missing model/tool version is unknown, not an inferred latest version.

Each scored case requires a reviewed correct control, a distinguishable defect under that contract, a known independently checked witness, and an exact replay bundle. Classify invalid builds, unsupported/equivalent mutants and harness/reference failures before scoring. Keep those exclusions and their reasons visible. A seeded fixture is labeled seeded; an upstream or user-reported defect needs its own provenance. Multiple faults in one design do not become independent real-world projects.

Current IDs such as `fifo_reset_bug` and `multiplier_latency_bug` reveal fault information. Until neutral planner-facing case IDs with a trusted execution mapping and an audited context boundary exist, those cases are **disclosed-defect development demonstrations**, not hidden-defect evaluation. Do not send held-out labels, known witnesses, expected repairs or this private mapping to the model. Record whether RTL is visible; use the same information policy for compared strategies.

## Comparable strategy experiment

Compare directed/manual, seeded constrained-random, one-pass Meta and feedback-driven Meta within the same selected RTL/RISC-V case. These strategy names are a protocol, not a claim all four evaluators are already implemented.

| Resource or condition | Required equality / treatment |
|---|---|
| Semantics | Same source revision, parameters, contract, checker, legal operation space and observation policy |
| Execution | Same aggregate admitted simulation cycles, tool-attempt count, cumulative tool wall-time and artifact limits per campaign; same per-attempt limits |
| Model allowance | Same total model-token allowance and input-information policy for the two Meta methods; one-pass spends it in one proposal, feedback divides it across rounds |
| Non-model baselines | Zero model usage; report their actual compute separately rather than pretending model spend is equal |
| Repeats | Freeze seeds/repeat count before held-out runs; pair case/seed comparisons. Do not count repeated deterministic directed input as independent trials |
| Build/cache | Record cold/warm policy, image/cache keys and actual compile/simulate time separately; serialize shared heavy jobs to avoid load bias |
| Repair/reduction | Evaluate separately from detection or reserve identical additional budgets; do not give only one method unlimited follow-up |

Choose numerical budget values only after development-only calibration under the selected tools. Historical successful directed checks used explicit 120-second per-attempt limits; they do not establish a suitable evaluation campaign budget or a 30-second cold-build guarantee. Freeze a per-domain budget profile rather than forcing unlike tools into a misleading shared cycle count.

Record a provider seed only if the provider actually supports and returns its use; otherwise record it as unavailable. Model-call reproducibility is not required for exact failure replay: replay the saved accepted stimulus without inference.

Count rejected proposals, failed model calls and infrastructure work in elapsed/cost records. Provider completion with unknown usage remains unknown or conservatively reserved; it is never silently free or automatically retried without a recorded decision. Keep model, compile, execute, check, replay, reduction and repair timing distinct. End-to-end time starts at the same recorded campaign-admission point for every method; time to first useful finding includes unsuccessful work. Budget exhaustion/no detection is right-censored at its observed limit, not omitted.

## Raw record contract and implementation status

Append-only attempt/model-round record source and its rejected-response correction were independently accepted and merged in PR58. The bounded FIFO harness, locked ledger snapshots and owned journal/storage interfaces were integrated in PR62. Operational provider/tool transport and actual evaluation remain separate. One campaign row links immutable per-call and per-attempt records, not a flattened overwritten final plan. No real comparative dataset, calibrated study or score is established.

## Offline collection and reproduction validation

The collection implementation reuses the existing `Study`, `Trial`, `EvaluationLog` and content-addressed `BlobStore`. Its request and reproduction models are in [collection.py](../../src/veriforge/evaluation/collection.py); the offline entry points are [collect_evaluation.py](../../scripts/collect_evaluation.py) and [reproduce_release.py](../../scripts/reproduce_release.py). This implementation has focused synthetic developer integration checks; independent feature review and actual release acceptance remain separate.

A `CollectionRequest` freezes the exact study/case/strategy/seed/repeat matrix, source/checker/contract and information-policy identities, allocated budget profile, source commit/dirty-patch state and version inventory. Every coordinate must have either a captured measurement ledger with its independently retained expected head or a reviewed, reasoned exclusion. Missing/duplicate/foreign coordinates are rejected, not silently removed from a denominator. Case bindings include ground-truth classification and review hash; this private operator metadata is never sent to a planner.

Versions must explicitly include Python, Verilator, cocotb, GCC and runtime image, plus model when a model strategy is planned. Unknown versions remain null and are listed as missing. No host version is guessed for an old run. Captured attempt versions, where available, must agree. Declared evidence kind cannot relabel existing mock/recorded ledgers as actual. Rejected/unknown model responses, usage nulls, partial trials, terminal stop reasons, right-censoring and exclusion reasons remain visible in retained records. No score or superiority claim is accepted as a request field or computed by collection.

Use the installed locked environment and an operator-prepared private request:

```sh
uv run --no-sync python scripts/collect_evaluation.py --request /path/to/private-request.json --root /path/to/private-input --output /path/to/new-private-export
uv run --no-sync python scripts/reproduce_release.py --root /path/to/new-private-export --expected-manifest-sha256 RETAINED_SHA256
```

The output directory must not already exist. Validation precedes export; any partial output after an I/O failure is preserved for review rather than overwritten or automatically deleted. Original measurement JSONL bytes and referenced hashed blob bytes are retained. Export contains `collection.json`, exact per-trial ledgers/blobs and `release-manifest.json`; retain the reported manifest hash independently. The verifier rechecks matrix/budget/identity/ledger/blob closure and the derived report, and rejects changed or unlisted files. It reports `manifest_validation_passed`, `execution=not_run` and `clean_tool_reproduction=not_established`.

These are **private, unreviewed exports**. Raw proposals/responses and classification metadata require an explicit publication allowlist/privacy review; do not publish the directory or pass it to a model. Trusted local directory ACLs, cooperating writers and trusted producer/operator provenance remain assumptions. Hash consistency is not remote attestation, and metadata validation is not a new simulator execution, clean replay or release3/3. Actual tool/model execution is not exposed by either CLI. Live Meta holds and resource reservations remain unchanged.

Required fields:

- `evaluation_revision`, `case_id`, domain/profile, control/defect classification, ground-truth provenance and visibility policy (sensitive labels held separately).
- `strategy`, replicate/seed, campaign/experiment/attempt IDs and lineage; exact accepted plans and rejected proposals with reason.
- Frozen source/checker/contract/environment/model/prompt/schema identities; actual versions and candidate base/diff identity when used.
- Allocated/reserved/consumed budgets, model usage including unknowns, stage timings, build-cache state and terminal stop reason.
- Execution status **and** design verdict, requirement/class/signature, first divergence, evidence artifact paths/size/hash and uncertainties.
- Replay attempts/results, reduction before/after counts and semantic identity, candidate kind (`prepared_variant`, `manual_patch`, `model_generated_patch`), unchanged checks, witness/neighbor/regression results.
- Review/exclusion disposition and missing fields; each public claim's exact wording, Import/Execute/Repair capability and maturity, contribution period, supporting record IDs/hashes and release revision.

Keep immutable raw records even when a later explanation or classification changes; append a reviewed correction. A model explanation is not a checked finding. A model-generated repair requires stored actual response/diff provenance; choosing `fifo_correct` is prepared-candidate selection.

## Analysis and acceptance

Report per-case detection and false alarms on controls, detection frequency across predetermined repeats, replay success, reduction size achieved, candidate/regression outcomes, elapsed time and actual usage/cost when available. Give numerator/denominator and exclusions for every metric. Report timeouts/build/harness/unsupported outcomes separately from functional mismatches. Do not combine RTL, timing reports, ERC/DRC and cross-artifact facts into one accuracy percentage.

Use paired case/seed comparisons; show small-sample uncertainty and case-level outcomes. If numbers are insufficient, publish descriptive results rather than a superiority claim or significance assertion. Repeated runs of one mutant do not establish broad generalization. No rubric weights or organizer scoring formula are assumed.

Release targets from the approved plan: an actual Meta-to-tool-to-feedback investigation, deterministic hero replay 3/3 on the release snapshot, no unexplained control failure, independent checks for every claimed tool capability, and exact evidence for every shown finding. Earlier 3/3 FIFO replay is historical evidence, not automatic release-head requalification.

## Precise outstanding evaluation artifacts

1. Frozen current source/runtime manifest and reviewed case/control inventory; neutral planner-facing aliases plus leakage audit for a held-out claim.
2. Real Meta proposal, usage/identity, tool execution, independent feedback and subsequent model step; current live status is pending.
3. Integrate the source-reviewed strategy harness after its separate acceptance gates, calibrate/freeze budget profiles and predetermined repeats, then collect complete raw records. Accepted record/harness source is not actual comparative measurement; no results have been collected under this protocol.
4. Release-snapshot replay and regression receipts, actual candidate diff/provenance, independently extracted cross-domain evidence, and fresh browser flow.
5. A reviewed sanitized claim/result summary and accessible reproduction prerequisites. Local private raw evidence is not a public permalink.

Use [DEMO_PLAN.md](DEMO_PLAN.md) for the narrative, [PROVENANCE.md](PROVENANCE.md) for attribution and [SUBMISSION_CHECKLIST.md](SUBMISSION_CHECKLIST.md) for delivery gates. None of these documents authorizes a job, publication or submission.
