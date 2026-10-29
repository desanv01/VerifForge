# Three-minute demonstration handoff

Prepared 29 October 2026; refreshed **30 October, 02:51 MYT (29 October, 18:51 UTC)**. This is a script and rehearsal plan, not a recorded demonstration or final submission. Organizer approval is settled. Submission remains due **5 November 2026, 10:00 AM MYT**. The [implementation gameplan](IMPLEMENTATION_GAMEPLAN.md) and [support matrix](SUPPORT_MATRIX.md) retain the broader gates.

## Story and configuration boundary

Show how exact observations distinguish a core-register fault from a firmware register-address fault, then show the preserved candidate and replay evidence. The two accepted hosted demonstrations share pinned upstream PicoRV32 RTL; they are **different configurations, programs, images and checkers**, not one connected engineering-system execution. The RAM-only RISC-V case compares against Spike. The firmware case adds a native MMIO peripheral and literal independent checker; Spike MMIO is unsupported. Neither is a physical-board demonstration.

The proposed evidence spine is accepted and inspectable. The final release snapshot/cut is not frozen. Live Meta feedback, native workbench views and their actual browser acceptance remain pending. Do not present file inspection as an implemented workbench screen, a curated candidate as generated repair, or the shared core as proof of cross-domain agreement. No video, captions, media assets, render or upload has been produced by this plan.

## Shot list: exactly 180 seconds

| Time | Visible evidence and suggested narration | Qualification / gate |
|---|---|---|
| 0:00–0:15 | Show source revision and problem: “A plausible explanation is not enough; keep the failing input, independent check and exact executable.” Briefly identify the reused 24 October RTL/evidence foundation and the 29–30 October additions | Contribution ledger, not an all-code-authored-during-event claim |
| 0:15–0:45 | Show [RISC-V summary and raw traces](../evidence/riscv/2026-10-30/README.md): control 17 retirements/68 cycles agrees with Spike; seeded register fault first diverges at order 1, PC `0x80000004`, DUT cycle 14: `0x8000fef0` expected versus `0x8000fef1` | Recorded run 40920450439, source 44b8b5e; curated seed, one RAM smoke, not ISA certification |
| 0:45–1:20 | Switch to the separately identified [firmware observation and check](../evidence/firmware/2026-10-30/README.md): bad STATUS write at cycle 95/bus 31/MMIO index 2, `0x40000008` versus required `0x40000004`; literal probe reads0 instead of `0x55667788`; mailbox 2 | Recorded run 40929665294, source 5565985; native MMIO checker, not Spike; bad 39 retirements/166 cycles |
| 1:20–1:50 | Show preserved bad header beside actual candidate.diff: one STATUS address restored. Show unchanged checker/contract identities and candidate's neighbor/byte-lane observations, mailbox 1, 55 retirements/218 cycles | Hand-authored curated restoration. No Meta-generated diff or general firmware-correctness claim |
| 1:50–2:15 | Compare replay identities: RISC-V controls reassemble/relink then reproduce exact object/ELF/binary/traces; firmware bad/candidate replays consume exact saved program bytes with simulator-only commands | Firmware replay rebuilds unchanged HDL model, not firmware. These are distinct replay operations; first RISC-V ELF failure remains preserved |
| 2:15–2:45 | Required final Meta shot: actual proposal, admitted budget, checked result and subsequent proposal in the identified product view | **Missing: human key readiness/live feedback and native workbench implementation/browser gate.** Rehearsal uses a labeled pending card; it does not fulfill this shot or imply inference occurred |
| 2:45–3:00 | Show exact public evidence manifests, upstream attribution and concise limits; identify local setup and remaining product/demo gates | Sanitized recorded evidence fallback is available. No live application, deployment, completed video or external submission claim |

Each row occupies its stated interval; total15+30+35+30+25+30+15=180-seconds. Keep only the legible values needed to follow the failure and correction. Do not compress the two configurations into one apparent continuous execution.

## Evidence and labels for the operator

[RISC-V run 40920450439](https://github.com/desanv01/VerifForge/actions/runs/40920450439) is independently accepted within the curated recompiled-control gate at source `44b8b5e40ce7841f076e4178df13f38ecdbd05fd`. The earlier [run 40912735570](https://github.com/desanv01/VerifForge/actions/runs/40912735570) failed full ELF equality because random temporary-object metadata differed; the fixed-object recipe preserved exact equality checks. Keep that history visible in supporting material. This RAM case does not establish original-saved-ELF execution without recompilation.

[Firmware run 40929665294](https://github.com/desanv01/VerifForge/actions/runs/40929665294) is independently accepted within five curated cases at source `5565985bbe2bb2a0be869d366baa48c715057657`: control, bad, candidate and saved-program replays of bad/candidate. Passing cases55 retirements/218 cycles/72 bus transfers; bad 39/166/54. Bus totals include RAM/instruction traffic. Original bad header, candidate diff, exact ELF/bin, observations, command records and typed identities are preserved in the public subset; its manifest lists omissions. Earlier preparation.json still says not_run: it is a frozen preparation record, read alongside later execution/check records.

For an authorized later recording, label every evidence playback “Recorded hosted execution — 30 October 2026 MYT — [run, source, tool]”. Label local JSON/source inspection “Inspection of retained evidence”; it is not live native computation. Mark cuts and acceleration. An authored pending card or diagram must be identified as such and cannot stand in for a product screenshot or actual Meta response.

## Local rehearsal without starting engineering jobs

1. Pin the selected source/run/image/checker/contract identities from both evidence READMEs and manifests. Open retained local files in a read-only viewer; do not execute copied artifacts or rebuild anything for this rehearsal.
2. Rehearse the RISC-V control→first-divergence path using summary, raw RVFI/Spike and normalized pair. Keep diagnostic DUT cycle separate from architectural retirement order.
3. Rehearse the firmware bad observation→independent check→preserved header/diff→candidate path. Confirm the displayed cycle/address/probe/mailbox values match the actual records and the spoken labels say curated.
4. Rehearse the two replay explanations using execution commands and matching hashes, not a new invocation. Point to the subset/omission bounds; neither directory is a full distributed runtime image.
5. Reserve the30-second Meta slot and list the missing native product views. After key readiness is supplied through the established secure path, a separate authorized run must establish real proposal→tool→checked-feedback→next-proposal and actual UI behavior. Do not repeat key requests while deferred.
6. Review source/tool/upstream attribution, contribution dates, privacy and the180-second reading. Main later chooses a frozen cut and grants recording/runtime slots; no service/DB/browser/native tool is started by these steps.

A recorded-file fallback can honestly demonstrate the accepted engineering results locally while native views are unfinished. It cannot fulfill the missing real Meta/product interaction shots. If the intended final narrative still requires a connected cross-domain discrepancy/improvement, that shot also remains missing: native graph comparisons are inconclusive and the two CPU demonstrations do not close it.

## Other material and open gates

The 24 October FIFO cycle14 mismatch, same-semantic18→14 reduction, historical replays/export and curated validation remain earlier foundation; arithmetic control/latency/product cases and five later family API attempts/export provide additional scoped material. The original tamper-tested FIFO package remains incomplete; use intact accepted packages, never edit historical evidence to conceal tampering. A portable ZIP requires its matching runner, not a self-contained executable environment.

Earlier AXI fixture evidence is accepted separately. [Hosted AXI run 40934406068](https://github.com/desanv01/VerifForge/actions/runs/40934406068) failed at **30 October 02:50:41 MYT (29 October 18:50:41 UTC)** before migrations or any attempt: Docker logging compression was incompatible with `max-file=1`. Owned resources were removed with no cleanup errors. This is infrastructure failure, not AXI design evidence; reserve no success shot for it. Earlier memory blocks, quiescence and sequential service recovery remain history, not proof of a completed AXI application campaign. Native investigation views are being implemented; prior source/API evidence does not establish their actual UI/browser behavior.

Vivado part query succeeded; synthesis/report/repair acceptance remains open after resource stops. KiCad's curated grid/parity correction has zero reported findings under unchanged configured checks, with4 ERC/5 DRC checks ignored; exact part/mate/physical/electrical acceptance remains unresolved. None is inserted into this180-second spine as an invented connected-system improvement.

| Final asset or decision | Current state |
|---|---|
| Accepted raw-result spine and exact manifests | RISC-V and firmware subsets available; final release cut and release-head hero replay 3/3 remain open |
| Actual Meta two-step feedback and visible product shot | Deferred key; no accepted live Meta feedback |
| Native workbench finding/candidate/replay/inspect views | Implementation and actual browser/refresh/error validation pending |
| Generated repair if claimed | No actual model response/diff lineage; firmware candidate is curated |
| Connected cross-domain improvement if claimed | Missing independent connected facts and affected-tool reruns |
| Three-minute recording, captions/transcript and playback URL | Not produced; capture only after critical story/evidence gates settle |
| Access and delivery | Local-only rehearsal; hosting/deployment needs separate authorization; final packet/portal submission not performed |

Use [EVENT_CONTRIBUTIONS.md](EVENT_CONTRIBUTIONS.md), [PROVENANCE.md](PROVENANCE.md), [EVALUATION.md](EVALUATION.md) and [SUBMISSION_CHECKLIST.md](SUBMISSION_CHECKLIST.md) for supporting detail. No judging weights are assumed. This document authorizes no job, recording, upload, deployment or submission.
