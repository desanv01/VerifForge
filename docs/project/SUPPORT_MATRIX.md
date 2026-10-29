# Actual support and open acceptance gates

Updated 30 October 2026, 1:50 AM MYT. The [implementation gameplan](IMPLEMENTATION_GAMEPLAN.md) retains sequences 1–6. Import, Execute and Repair are separate gates; source and UI presence do not establish live acceptance.

| Sequence / supported configuration | Import / inspect | Execute | Repair / validation | Next gate |
|---|---|---|---|---|
| 1 — curated FIFO | Typed contracts, evidence, exact stimulus accepted | Real correct/fault/replay/build-error/cleanup accepted | Curated candidate validation and reduction accepted within recorded scope; no generated-patch claim | Current browser, wider regression corpus and application candidate flow |
| 1 — arithmetic WIDTH8, latency2 | Family API/evidence merged in PR9 | Three actual API/RQ cases plus exact arithmetic replay accepted; FIFO replay also preserved; export bytes independently checked | Candidate/reduction source accepted; application gate open | Actual arithmetic browser gate and candidate regression flow |
| 1 — bounded32-bit AXI-Lite/four registers | Contract/oracle, runner and native evidence merged; three-family API image adopted with preserved history | Seven real standalone outcomes independently accepted; application attempt blocked before first POST by local headroom | Application/replay/repair pending | Bounded actual HTTP/queue/tool gate and application acceptance in issue16 |
| 2 — PicoRV32 RV32I + Spike | Pinned attributed source; [curated raw results](../evidence/riscv/2026-10-30/README.md) | Actual control17 retirements/68 cycles agrees with Spike; seeded mutant first diverges at retirement1/cycle14; exact recompiled object/ELF/binary/raw and normalized trace replay independently accepted | Earlier ELF reproducibility failure preserved; stable-object recipe fixed it under unchanged checks | Saved-executable replay without recompilation and workbench integration; no ISA certification or design-repair claim |
| 3 — firmware/register facts | Bounded extractors merged PR15; executable preparation/native MMIO checker merged PR25 | No actual compiler/firmware/MMIO execution | Synthetic source candidate only | Bounded parent compiler/execution controller, then real mismatch/candidate/replay |
| 4 — Vivado2026.1/Artix-7 | Report import accepted; actual version/selected-part query succeeded; readiness, storage supervision and recorded-attempt importer merged in PR21 | First synthesis attempt stopped at storage cap; retry blocked by local memory; no successful synthesis/timing/CDC | Not demonstrated | Isolated project batch and checked correction under unchanged requirements |
| 5 — KiCad10.0.6/passive2x6 carrier | Actual native netlist/schematic/PCB exports | Original ERC/DRC26 findings each | Separate candidate rerun0 reported ERC/DRC independently accepted under unchanged configured rules:4ERC/5DRC checks ignored | Exact MPN/hole/pin/pad/mating review and application integration |
| 6 — cross-domain manifest | Native bridge, exact-byte admission and immutable persistence/read API merged PR22; local view merged PR24 | Recorded original/candidate graphs remain inconclusive due common or unreviewed ancestry | No independent system repair claim | Explicit engineering project/import, runtime/browser adoption, independent facts and affected-check reruns |

Clean ERC/DRC is not electrical correctness. Carrier cable relation is reviewed logical intent, not verified physical wiring or mating. Exact part/hole fit remains unresolved.

| Shared area | Current evidence | Remaining gate |
|---|---|---|
| Durable execution | Initial real API/PostgreSQL/Valkey/RQ flow; durable round/transport/cleanup source merged PR20; global resource fence independently reviewed in PR26 | Dispatcher/runtime wiring, PostgreSQL migration/locking qualification, broker/API adoption and recovery |
| Meta | Provider/helpers reviewed, immutable admitted-model bug fixed | Human reaffirmed key not ready October29; no live Meta feedback. Independent work continues without repeated key requests |
| Runtime/recovery | Three-family image adopted after guarded sequential recovery; old database rows and110 artifact files unchanged; fresh backup archive inspected; earlier original backup restore accepted | Newer backup restore remains untested; local memory blocks new AXI execution; wider recovery remains separate |
| Workbench | Family-aware source and immutable native investigation view merged; fresh runtime-capability and full version-identity checks | Fresh family browser gate; AXI controls/native traces; engineering project/API runtime adoption; planner/repair views |
| Candidate provenance | Reviewed source/config diffs, family/witness binding, curated-vs-generated distinction | Actual API/UI candidate flow and generated-patch validation |
| GitHub | PR8,PR9,PR15 and PR17–25 merged after current-head CI; issues1 and4 closed within verified scope | PR26 resource fencing and focused follow-ups; remaining issue acceptance including AXI application issue16 |
| Evaluation/demo | Equal-budget protocol, contribution/provenance ledger,180-second demo and submission checklists | Frozen cases, raw results, real Meta, connected evidence, video and final submission |

Sequence7 is stretch after primary acceptance and submission-critical work; sequence8 deferred. Organizer approval is settled. Deadline: **5 November2026,10:00AM MYT**. Public source publication is authorized; application deployment and final external submission remain separate actions.

Private coordination, credentials, local databases and unrestricted evidence are excluded from public commits. Scoped test counts overlap and must not be summed into a fabricated project total.
