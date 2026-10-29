# Contribution chronology and event-work ledger

Prepared 29 October 2026. Organizer approval of the continued project is settled. This ledger preserves the chronology; it does not re-open eligibility or turn earlier work into newly authored event work.

Refreshed 30 October 2026 MYT from evidence through 01:44 MYT (29 October, 17:44 UTC). Execution dates remain those of the original receipts; documentation written after midnight does not redetermine authorship or run dates.

The [implementation gameplan](IMPLEMENTATION_GAMEPLAN.md) and [support matrix](SUPPORT_MATRIX.md) distinguish earlier foundations from event-period additions. Git author/committer dates, delivery/execution receipts and first-visible source inventories are different evidence. A commit date alone does not prove when code was authored, and an untracked file on 29 October is not automatically new that day.

## Earlier foundation, verified 24 October

| Existing contribution | Evidence basis | Permitted description |
|---|---|---|
| Typed bounded FIFO contracts, independent checker and deterministic runner/replay | Earlier source history plus locally retained real-tool acceptance | Pre-event foundation reused by the current project |
| Durable FIFO API, local PostgreSQL/Valkey/RQ and selected evidence replay/export | Earlier commits and exact-image runtime receipts | Existing scoped engineering evidence; not a new live Meta investigation |
| Evidence reduction/export and curated candidate validation | Locally retained 24 Oct simulator workflow | Prepared-variant correction checks, not model-generated patch discovery |
| Arithmetic, registry, planner, AXI, UI and recovery work delivered or prepared on 24 Oct | Dated receipts and source inventory, including uncommitted work | Earlier implementation/preparation, with source versus live scope stated individually |
| Public workbench/CI integration | [PR #8](https://github.com/desanv01/VerifForge/pull/8), merged [9163187338cacf31313886629faf8d2294f59c4c](https://github.com/desanv01/VerifForge/commit/9163187338cacf31313886629faf8d2294f59c4c), 24 Oct 10:20:21 MYT | Earlier published foundation; preserve its history |

The 29 October audit found 48 modified/untracked source paths beyond that baseline before adding planning documents. Their preserved inventory establishes existence at inspection, not new authorship on resume. Several already have 24 October receipts. Keep these classified as earlier or provenance-to-be-resolved until actual history supports a narrower date.

## Event-period additions: record only completed work

| Work package | Intended addition after resume | Actual status at this handoff | Required ledger evidence |
|---|---|---|---|
| U0/U1 | Reconciled baseline and reviewed shared capabilities | Resume/source preservation and independent AXI/original-backup restore review accepted; no blanket new-code claim | Inventory/accepted revision and explicit origin classification |
| U2 | Durable Meta rounds and actual feedback investigation | Pure helpers, experiment/round state and transport source accepted after independent fixes; mock/SQLite evidence. Key deferred; linked dispatch/public API/live feedback pending | New code diff, actual model/tool/dispatch and recovery receipts before live claim |
| U3 | Complete RTL families and constrained patch provenance | AXI six new+one preserved case and five family API attempts/export accepted. Later AXI image built and API data/artifacts preserved, but consumer startup memory-aborted at 499 MiB and app services quiesced. Later sequential recovery of existing verified e8eb containers succeeded: API healthy/three adapters, worker idle, reconciler running, schema7bf/prior nine-table rows/110files preserved | Preserve initial prebuild memory block and later abort; no new AXI campaign/attempt/browser claim |
| U4 | Core/reference integration | Actual run40920450439 independently accepted: controls17retirements/68cycles and exact recompiled object/ELF/binary/raw+normalized traces; seeded divergence order1/cycle14. First hosted exact-ELF failure remains recorded | [retained public CI evidence](../evidence/riscv/2026-10-30/README.md); no original-saved-ELF execution without compilation, full ISA, firmware or repair claim |
| U5 | Firmware/register/boot investigation | Bounded facts foundation merged in PR #15; executable native-MMIO harness source accepted after fixes, no compilation/core run | Pinned upstream/integration diff; actual ELF/disassembly and executable before/after/replay |
| U6 | Live Vivado readiness/investigation | Initial cache-cap failure, then successful version/three-part query; later synthesis scratch-cap failure, revised retry blocked by host headroom below 2 GiB | Preserve failed/blocked attempts; no synthesis/reports/repair success without actual result |
| U7 | KiCad checks and exact-part mapping | Actual 26 ERC/26 DRC original → zero reported findings for distinct grid/parity candidate under unchanged configured checks; independent review accepted | Four ERC/five DRC ignored checks remain; exact part, physical/electrical and integration unresolved |
| U8 | Cross-domain system relationships | Native bridge source fixes independently accepted; actual graph comparisons/candidate remain inconclusive/no demonstrated improvement | Authenticated review identities, independent artifact authority and affected-tool reruns still needed |
| U9 | Family/domain-aware workbench | Family UI source merged in PR #9; accepted API evidence. Native investigation PR #22 merged after CI run 40919086170; 242 passed/two private-evidence skips in frozen source checks, no runtime/browser adoption | Actual browser/refresh/error/candidate flow on frozen runtime; overlapping test counts are not a project total |
| U10 | Evaluation and submission preparation | Five docs refreshed across 29–30 Oct with actual source/execution boundaries; no comparative benchmark/video/submission | Reviewed docs delta; later raw evaluation, final media and confirmed roles |
| U11 | Specialist stretch | Conditional, not started/claimed here | Separate assignment and executed scope after core gates |

This is a dated handoff snapshot. Future rows must replace intended-status text only after real delivery review, not when a worker is merely assigned.

The October29 original-backup restore is a new execution of earlier data, not new authorship of that data. Its acceptance covers original c67f9c9f19e5 rows/schema checks and saved cleanup; newer database/artifact/application recovery remains open. AXI's preserved control retains its October24 date, and an additional FIFO replay is not a newly authored fault. Private receipts remain local; final public evidence needs a sanitized reviewed summary.

Public source milestones at this snapshot: [PR #9](https://github.com/desanv01/VerifForge/pull/9), merged as `4c273cc15ae326b28b024a9085dad7cc2009e7c8`, adds family API/evidence/workbench source; [PR #15](https://github.com/desanv01/VerifForge/pull/15), merged as `c51212ed086fdecda63bbe384bc7965b618704ff`, adds provenance contracts and firmware facts. [PR #17](https://github.com/desanv01/VerifForge/pull/17), [#18](https://github.com/desanv01/VerifForge/pull/18), [#19](https://github.com/desanv01/VerifForge/pull/19), [#20](https://github.com/desanv01/VerifForge/pull/20) and [#21](https://github.com/desanv01/VerifForge/pull/21) are also merged after current-head CI; the PR #21 baseline was `70b60a35fea55293ba386b38cc1e600ea6888751`. Native investigation [PR #22](https://github.com/desanv01/VerifForge/pull/22) merged as `496c89fb72aa2d4e6ff322dad9db279fba81cb21` after [CI run 40919086170](https://github.com/desanv01/VerifForge/actions/runs/40919086170). Each PR describes its finite scope; neither a merge nor overlapping CI totals establishes all-domain runtime acceptance.

The first [hosted RISC-V run 40912735570](https://github.com/desanv01/VerifForge/actions/runs/40912735570) failed full ELF equality because of independently diagnosed random temporary object-name `STT_FILE` metadata. The later [run 40920450439](https://github.com/desanv01/VerifForge/actions/runs/40920450439), at public source `44b8b5e40ce7841f076e4178df13f38ecdbd05fd`, passes the curated recompiled-control gate with the fixed-object recipe; [retained public CI evidence](../evidence/riscv/2026-10-30/README.md) preserves that distinction and the earlier failed summary. The audit and publication preparation do not create another execution.

[PR #23](https://github.com/desanv01/VerifForge/pull/23) and [#24](https://github.com/desanv01/VerifForge/pull/24) are merged; [#25](https://github.com/desanv01/VerifForge/pull/25) merged as `1db9916dff8e2fbdf181727bc8ba4a4862b254fa` after [current-head CI 40922456584](https://github.com/desanv01/VerifForge/actions/runs/40922456584). Source publication does not establish firmware execution or application/browser adoption.

## Entry format for the final ledger

For each addition record: human contributor/role as confirmed by the human; task and capability; authored/modified/executed/reviewed dates with timezone; pre-existing/upstream material reused; exact commit/PR and source hashes; executed command/model/tool evidence; independent review; limitations; and public demo claim IDs. Mark uncertain origin explicitly. Link a public commit only after it exists; private raw receipts may stay private with a reviewed sanitized summary.

Distinguish implementation labor from runtime execution. AI-assisted coding/tooling does not establish product use of a Meta model. Record the actual development assistance where relevant and separately record the actual Meta model/provider used by the application. Never attribute a public CPU, simulator, ISA reference, CAD tool, component datasheet or third-party model to VerifForge authorship.

## Submission wording boundary

Accurate current wording: “We continued our earlier local verification foundation. During the event we added [completed, dated additions with links], and demonstrated [specific newly executed evidence].” The brackets are fields to complete, not accomplishments already established. Do not claim all 48 dirty paths, all repo code, or all historical tests were authored/run during the event.

A model-generated repair requires stored proposal/diff provenance. Historical curated candidate selection must remain labeled curated even if replayed during the event. An imported report remains an import even when newly displayed in the UI.

Use [PROVENANCE.md](PROVENANCE.md) for upstream attribution and [EVALUATION.md](EVALUATION.md) for evidence tiers. No commit, publication, source-history rewrite or submission was performed to prepare this ledger.
