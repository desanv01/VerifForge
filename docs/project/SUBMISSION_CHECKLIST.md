# Submission checklist and remaining gates

Prepared 29 October 2026. Deadline supplied by the human's participant guide: **5 November 2026, 10:00 AM MYT (02:00 UTC)**, equivalent to 4 November, 9:00 PM EST. Aim for a reviewed package before the final buffer. These are preparation tasks, not a scheduler or permission to submit.

Refreshed 30 October 2026 MYT; evidence status checked through **02:51 MYT (29 October, 18:51 UTC)**. No video assets/render/recording yet: critical actual evidence and final story must stabilize first.

The supplied guide requires public source, Meta-model incorporation, a three-minute demo, clear event-contribution identification and submission through Cerebral Valley. Its pasted rubric contained no judging weights; none are assumed. Organizer approval of continuation is settled and must not be reopened. Keep private correspondence private.

## Recorded decisions and preparation

- [x] Continue the existing [VerifForge repository](https://github.com/desanv01/VerifForge), preserving earlier public history; no replacement repository is needed.
- [x] Organizer approval is settled by the human's confirmation.
- [x] U10 evaluation protocol, demo plan, contribution chronology, provenance framework and this checklist drafted; this is documentation preparation, not completed product/evaluation acceptance.
- [x] October29 AXI fixture evidence independently accepted: six resumed cases plus one preserved October24 control, four correct passes and three expected seeded-fault detections. This closes the finite fixture gate only.
- [x] October29 isolated restore of the original October24 database backup independently accepted, including original row fingerprints, initial schema checks and saved owned-resource cleanup evidence.
- [x] Five actual family API/RQ attempts plus arithmetic export/download independently accepted: control, two arithmetic faults, FIFO replay and arithmetic replay. Earlier rows/artifacts preserved.
- [x] KiCad native grid/parity candidate independently accepted with zero reported findings under unchanged configured checks; four ERC/five DRC ignored checks and unresolved exact-part/physical limits retained.
- [x] [PR #9](https://github.com/desanv01/VerifForge/pull/9) family integration and [PR #15](https://github.com/desanv01/VerifForge/pull/15) provenance/firmware-facts foundation merged after their CI. These do not close all runtime/demo gates.
- [x] [PR #17](https://github.com/desanv01/VerifForge/pull/17), [#18](https://github.com/desanv01/VerifForge/pull/18), [#19](https://github.com/desanv01/VerifForge/pull/19), [#20](https://github.com/desanv01/VerifForge/pull/20) and [#21](https://github.com/desanv01/VerifForge/pull/21) merged after current-head CI; source publication remains separate from runtime adoption.
- [x] Actual [run 40920450439](https://github.com/desanv01/VerifForge/actions/runs/40920450439) independently audited and accepted within the curated recompiled-control gate: controls 17 retirements/68 cycles, exact object/ELF/binary/raw+normalized records, seeded first divergence order 1/cycle 14. See [retained public CI evidence](../evidence/riscv/2026-10-30/README.md); the earlier hosted exact-ELF failure remains preserved.
- [x] Actual [firmware run 40929665294](https://github.com/desanv01/VerifForge/actions/runs/40929665294) independently accepted: compiled control, seeded STATUS mismatch, curated restoration and exact saved-program replays. [Raw-result subset and scope](../evidence/firmware/2026-10-30/README.md) retained; this does not close all product/firmware gates.
- [ ] Reconfirm final public repository access and exact submission commit; the planning audit's recorded public visibility is not a final link check.
- [ ] Main reconciles release source, runtime image and current support matrix. Do not stage private receipts or blanket-add ignored evidence.
- [ ] Select the project license with the human and retain required upstream notices. No root LICENSE/NOTICE was present at this handoff inspection; public visibility does not choose a license.

## Product evidence before claiming completion

- [ ] U0/U1: frozen source/tool/image/contract/checker manifest, bounded capability/status declarations and independently reviewed baseline.
- [ ] U2: actual Meta proposal → execution → independent feedback → subsequent model step, durable IDs and usage. Helpers/round-state/transport source accepted; dispatch glue/public API/live PostgreSQL recovery remain separate. Human deferred the key; do not repeat key requests or substitute mocks.
- [ ] U3/sequence 1: complete selected family application/repair path. Prior five family API attempts/export and AXI fixture evidence accepted. Later AXI image built and API data/artifacts preserved, but consumer startup memory-aborted at 499 MiB and app services quiesced. Later sequential recovery of verified e8eb containers succeeded: API healthy/three adapters, worker idle, reconciler running; schema7bf/prior nine-table rows/110files preserved. Initial prebuild block retained. Hosted AXI run 40934406068 failed at 30 October 02:50:41 MYT before migrations or attempts due to Docker logging compression with `max-file=1`; owned resources removed, no cleanup errors. No AXI execution resulted; fresh browser/application and release repair/replay gates stay open.
- [ ] U4/sequence 2: complete broader core/reference investigation and required replay/repair scope. Actual curated recompiled-control gate is accepted, with exact object/ELF/binary/traces and seeded divergence. Saved-ELF execution without compilation for this RAM-only case, full ISA and repair remain unproven; earlier failed local builds and first hosted ELF failure stay recorded.
- [ ] U5/sequence 3: complete wider firmware/product integration. The bounded five-case native-MMIO control/bad/curated-candidate/saved-program-replay gate is independently accepted; arbitrary firmware/devices, model-generated repair, workbench integration and physical hardware remain open.
- [ ] U6/sequence 4: completed Vivado synthesis/reports and scoped before/after checks. Part query succeeded; later synthesis stopped at scratch/result cap without accepted reports. Revised retry blocked by host headroom below 2 GiB.
- [ ] U7/sequence 5: complete intended connectivity/part-mapping/product scope. Scoped grid/parity correction accepted; exact MPN, mating/cable, physical/electrical validation and product integration remain open.
- [ ] U8/sequence 6: independently supported cross-domain discrepancy and improvement/reruns. Bridge source fixes accepted; current native graphs/candidate remain inconclusive/no demonstrated improvement.
- [ ] U9: fresh browser finding/inspect/replay/candidate/validate/export and refresh/error behavior on identified runtime. Native investigation [PR #22](https://github.com/desanv01/VerifForge/pull/22) merged after [CI run 40919086170](https://github.com/desanv01/VerifForge/actions/runs/40919086170); frozen source checks report 242 passed/two private-evidence skips, while native workbench views implementation and actual browser adoption remain pending. API/GET evidence and UI source build alone do not close browser acceptance.
- [ ] Reliability: running cancellation, stale/late results and restart/broker recovery. Original October24 backup restoration is accepted separately; newer database state, external artifact restore and application recovery remain open.
- [ ] U10: freeze comparable strategy budgets/corpus/information policy, collect raw results and exclusions, independently audit claims. No comparative results exist under the new protocol yet.
- [ ] Start U11/sequence 7 only after required work and submission-critical gates are stable. Sequence 8 remains deferred.

[PR #23](https://github.com/desanv01/VerifForge/pull/23) and [#24](https://github.com/desanv01/VerifForge/pull/24) are merged; [#25](https://github.com/desanv01/VerifForge/pull/25) merged as `1db9916dff8e2fbdf181727bc8ba4a4862b254fa` after [current-head CI 40922456584](https://github.com/desanv01/VerifForge/actions/runs/40922456584). Source publication is separate from actual execution acceptance and application/browser adoption. The accepted RISC-V and firmware hosted gates have their own exact source/image/checker records below.

An open checkbox is a precise missing gate, not an instruction to invent a favorable outcome. Keep incomplete sequences visible and report blockers early to main.

## Submission packet to assemble

| Required artifact | Completion evidence to record | Current status |
|---|---|---|
| Public repository | Final commit/tag, README/setup/support boundaries, accessible links | Existing repository recorded; final snapshot not frozen |
| Meta incorporation | Actual requested/returned model/provider, prompt/schema identity, call/attempt/feedback lineage | Live gate pending |
| Three-minute demo | Reviewed final video/transcript and accessible playback URL | Not produced |
| Contribution description | [EVENT_CONTRIBUTIONS.md](EVENT_CONTRIBUTIONS.md) with dated source/execution distinctions and confirmed human roles | Source merges and scoped new evidence recorded; final release/roles pending |
| Attribution/license | [PROVENANCE.md](PROVENANCE.md), actual upstream revisions/licenses/notices and project license decision | Inventory incomplete; license decision pending |
| Evaluation | [EVALUATION.md](EVALUATION.md), frozen methods and complete raw/summary records | Protocol only; no new benchmark results |
| Reproduction | Exact selected plans, source/checker/contract/environment, dependencies and verified clean replay | Accepted RISC-V recompiled and firmware saved-program records exist; release-head packet/replay 3/3 pending |
| Product access | Local setup or an explicitly authorized hosted route if required by the final form | Local-only product; no hosted deployment claimed |
| Portal confirmation | Submitted record ID, timestamp MYT/UTC, final URLs and confirmation/receipt | Not submitted |

The [demo plan](DEMO_PLAN.md) provides the 180-second sequence. Repository About/README and video claims must agree with the selected release evidence. Link to reviewed sanitized evidence only; local private receipt paths are not public proof links.

## Missing demonstration shots and access gates

- [ ] Capture the actual Meta proposal→tool→checked-feedback→subsequent-proposal in the identified product. Key readiness remains deferred; no repeat request or mock substitution.
- [ ] Complete native RISC-V/firmware workbench views and actual browser inspect/candidate/replay/refresh/error validation before calling recorded-file inspection product interaction.
- [ ] Freeze the [180-second script](DEMO_PLAN.md), separate the two configurations, retain curated-candidate labels and identify any recorded execution/inspection fallback. No video is recorded yet.
- [ ] If a connected cross-domain improvement or generated repair is claimed, obtain its distinct independent facts/reruns or actual model-response/diff lineage; the two accepted CPU cases do not establish those claims.
- [ ] Review local access/reproduction instructions and final media links. Application deployment/hosting needs separate authorization; no deployment or final portal submission has occurred.

## Final operator checks

- [ ] Main verifies all required CI at the exact final head, plus appropriate real-tool/browser gates; record test filters/skips and avoid summed overlapping counts.
- [ ] Compare every public claim with a supporting record/hash and contribution period; label seeded versus real faults and prepared versus generated repairs.
- [ ] Review exports/video/docs for secrets, local paths, private HDL, dumps and license terms; do not upload raw coordination or environment material.
- [ ] Validate public source/video/selected product links from the intended reviewer access context. Do not expose the single-local-identity development API to satisfy a product-link field.
- [ ] Complete the actual portal fields and contributor details from the human's information; do not infer names, roles or contact details.
- [ ] Obtain the human/main's explicit external-submission authorization when the concrete packet is reviewable, unless separately already granted for that action.
- [ ] Submit once authorized, inspect confirmation, preserve the actual receipt and timestamp. Never mark submission complete from a prepared form or an upload attempt alone.

No publishing, upload, submission, live model/tool operation or deadline automation was performed by this handoff.
