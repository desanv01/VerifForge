# Evaluation protocol and evidence register

Prepared 29 October 2026 for U10. This is a proposed evaluation protocol plus a receipt-based historical summary, **not an executed benchmark**. No new model, simulator, browser, restore or engineering-tool run was performed to write it. The [implementation gameplan](IMPLEMENTATION_GAMEPLAN.md) and [support matrix](SUPPORT_MATRIX.md) target sequences 1–6; U11/sequence 7 is stretch and sequence 8 is deferred. Missing gates below remain open until their artifacts are reviewed.

Refreshed 30 October 2026 MYT; evidence checked through **01:44 MYT (29 October, 17:44 UTC)**. Source merges, actual hosted RISC-V results and current API evidence below supersede earlier preparation-only statuses; fresh browser acceptance remains pending.

## What the existing evidence establishes

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
| Planner state and transport source | FIFO helpers/controller, durable experiment/round state and bounded transport source independently accepted after fixes; synthetic crash/duplicate/ledger tests exercised isolated SQLite | Real Meta calls, linked broker/provider dispatch, live PostgreSQL recovery or end-to-end feedback; human has deferred the key |
| Vivado readiness and synthesis attempt, 29 Oct | Initial startup cache-cap failure preserved; revised device query returned version2026.1 and three exact part matches. Later read-sources passed and synthesis began, then scratch/result storage cap stopped it; owned processes settled. Revised retry blocked by host headroom below 2 GiB | Completed synthesis, accepted timing/CDC/methodology reports, routed closure or repair; a resource stop is not a design defect |
| KiCad configured-check correction, 29 Oct | Original 10.0.6 control had26 ERC and26 DRC/parity findings. Distinct grid/net-name/blank-field candidate produced zero reported findings under unchanged configured checks; independently reviewed with 24 endpoint semantics and copper/pads preserved | Every rule enabled: four ERC and five DRC checks remain ignored as before. Exact MPN, cable, physical/electrical acceptance and product integration remain unresolved |
| Hosted RISC-V curated smoke and seeded mutant | [Run 40920450439](https://github.com/desanv01/VerifForge/actions/runs/40920450439): actual PicoRV32/Spike control and recompiled control each 17 retirements/68 cycles; seeded mutant first divergence order 1/cycle 14, expected `0x8000fef0`, observed `0x8000fef1`. Independent saved-evidence audit accepted; [retained public CI evidence](../evidence/riscv/2026-10-30/README.md) | Full ISA certification, model discovery, executable repair or firmware/MMIO execution |
| RISC-V curated recompilation gate | Actual rerun accepted: independently assembled/linked controls have byte-identical object, full ELF, binary, raw RVFI/Spike logs and normalized pair/comparison. First hosted run 40912735570 remains failed due random temporary object-name ELF metadata; fixed-object recipe retains exact checks | Executing the original saved ELF without recompilation, arbitrary program replay or release-snapshot hero replay 3/3 |
| Executable firmware preparation | Separate native-MMIO harness source reviewed after fixes | Actual compiled firmware, MMIO execution, correction or replay |
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

## Raw record contract to implement

No evaluation harness or raw-result file is created by this handoff. The evaluation owner must implement an append-only structured record and validate it before collecting results. One campaign row links immutable per-call and per-attempt records, not a flattened overwritten final plan.

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
3. Implemented comparable strategy harness, calibrated/frozen budget profiles, predetermined repeats and complete raw records; no measurements have been collected under this protocol.
4. Release-snapshot replay and regression receipts, actual candidate diff/provenance, independently extracted cross-domain evidence, and fresh browser flow.
5. A reviewed sanitized claim/result summary and accessible reproduction prerequisites. Local private raw evidence is not a public permalink.

Use [DEMO_PLAN.md](DEMO_PLAN.md) for the narrative, [PROVENANCE.md](PROVENANCE.md) for attribution and [SUBMISSION_CHECKLIST.md](SUBMISSION_CHECKLIST.md) for delivery gates. None of these documents authorizes a job, publication or submission.
