# Implementation checklist

Updated 29 October 2026 after direct human resume superseding the October24 pause. Implementation and worker coordination are RUNNING; the same five-minute heartbeat is ACTIVE. The October29 UPDATED_GAMEPLAN and HANDOFF govern expanded direction; private canonical records govern live controls/ownership. Existing deliveries remain accepted only within their recorded scope. Organizer approval is settled by direct human confirmation. Deadline: 5 November 2026, 10:00 AM MYT.

The gameplan defines intended release scope. Source and mocked tests do not establish live tool/model execution. Counts overlap and must not be added into a project total.

## October30 integration checkpoint

PR9, PR15 and PR17–36 merged after their respective current-head CI. They cover family API/workbench, engineering provenance, bounded AXI/native evidence and controls, RISC-V execution and replay correction, durable planner foundations/resource fencing/dispatch, Vivado recorded evidence, native investigation persistence/views, explicit engineering projects and trusted import, and bounded hosted firmware/AXI controllers. Issue4 is closed for the seven-case bounded target qualification; issue16 retains the live AXI application gates. Five actual family API attempts, exact replays, downloads/export and preservation received independent review. The arithmetic browser gate remains pending.

RISC-V run40920450439 passed and its retained evidence was independently audited: control17 retirements/68 cycles, seeded mutant first divergence at retirement1/cycle14, and exact recompiled object/ELF/binary/raw and normalized trace replay. The earlier random-object-name ELF failure remains preserved. Saved-executable replay without recompilation and workbench integration remain open. [Curated raw results](../evidence/riscv/2026-10-30/README.md) preserve this scoped acceptance.

The AXI image is adopted with all earlier rows and110 artifact files preserved after a guarded sequential service recovery. The subsequent actual API gate stopped before its first POST because headroom fell below the start floor; no AXI application run is claimed. Hosted firmware run40929665294 on commit5565985 completed and its285 retained files were independently audited: control/candidate55 retirements and218 cycles, seeded bad39/166 with first MMIO discrepancy at cycle95, and exact saved bad/candidate program replay without firmware recompilation. HDL was rebuilt. This is a curated header correction under unchanged checks, not a model-generated repair. The hosted AXI gate awaits the current schema integration. Durable driver source passed independent review and156 frozen integration checks; operational migrations and runtime/API adoption remain open. Explicit engineering project kinds passed327 frozen checks with two gated skips; PR32 historical fixture CI correction preserves all original assertions. Trusted import and project UI passed separate independent reviews. Vivado synthesis is resource-limited; KiCad exact-part/physical gates remain; native cross-domain graphs remain inconclusive.

The coordinator continues finite assignments, reviews and GitHub integrations under the human's weekly allowance guard, pausing all project work and the existing schedule at five percent remaining. Live Meta remains deferred because the key is not ready. No live Meta or completed six-sequence release is claimed.

Later hosted AXI checkpoints: run40934406068 failed before migration because of Docker logging configuration; PR35 fixed that and exact terminal receipt byte accounting. Run40938359555 then qualified an empty PostgreSQL database at13engineeringprojects, passed the schema comparison and started healthy services, but failed before the first campaign POST because the evidence bind mount was not writable. Both owned ephemeral stacks were removed and evidence preserved. Zero AXI application attempts are claimed. The permission correction is being reviewed before a new run.

## October29 work packages (updated October30)

Current [support matrix](SUPPORT_MATRIX.md) separates Import / Execute / Repair and supersedes historical status shorthand below.

| Package | Current finite outcome / next gate |
|---|---|
| U0 | Preservation reconciled; original backup restore accepted; family image adopted and five real attempts/export independently reviewed; newer restore pending |
| U1 | Engineering provenance/support schema accepted after transitive review-state correction; native adoption pending |
| U2 | Durable foundations/resource authority merged PR20/26; bounded driver and owner-loss quarantine source accepted; dedicated runtime/control and two-stage API independent findings being corrected; operational adoption and live Meta open |
| U3 | Actual family API and AXI seven-case native gates accepted; three-family image adopted/preserved; AXI controls merged PR30 and hosted gate merged PR31; local API execution blocked before POST, actual hosted/browser gates open |
| U4 | Actual PicoRV32/Spike control/seeded divergence/exact recompiled replay independently accepted; saved executable replay and workbench open |
| U5 | Facts, executable harness and bounded controller merged PR15/25/29; actual hosted run40929665294 control/seeded mismatch/curated candidate/saved-program replay independently accepted; workbench open |
| U6 | Actual Vivado2026.1 selected-part query passed; synthesis resource-limited; retry memory-blocked; recorded importer/readiness/storage source merged PR21 |
| U7 | Actual candidate configured ERC/DRC checks independently accepted with4ERC/5DRC ignored checks unchanged; exact-part/physical acceptance unresolved |
| U8 | Manifest/native bridge source accepted; recorded graphs remain inconclusive; independent system evidence pending |
| U9 | Native admission/persistence/views merged PR22/24; explicit engineering projects, trusted import and project UI source reviewed; additive typed native-tool reports being implemented; actual runtime/browser adoption pending |
| U10 | Evaluation/demo/submission/provenance prepared; permanent curated RISC-V and firmware raw evidence reviewed; complete comparative evaluation/video pending |
| U11 | Analog/ASIC stretch after primary scope and submission-critical gates |

## Earlier foundation milestones

| ID | Priority | Outcome / owner | Status | Next acceptance gate |
|---|---|---|---|---|
| VF-000/010 | P0 | Workspace and contracts / main, contracts | ACCEPTED | Reviewed immutable FIFO contract and independent schema/oracle checks |
| VF-011 | P0 | Real FIFO execution / RTL | ACCEPTED | Linux nine-test gate: correct/reset mutant, three exact replays, build error and process cleanup; byte-cap correction reviewed |
| VF-020/021 | P0 | Durable API/runtime / API, runtime | INITIAL GATE ACCEPTED | Actual HTTP/PostgreSQL/Valkey/RQ/Linux FIFO; correct/mutant, duplicate delivery, queued cancellation; broader recovery separate |
| VF-022/023 | P0 | Evidence API source / API, reviewer | SOURCE ACCEPTED | Replay collision corrected; prior40pass1skip plus final separately passed screening case; live upgrade VF024 |
| VF-040A/041/081 | P0 | Evidence module and real curated FIFO workflow / evidence, review | ACCEPTED WITHIN SCOPE | Independent saved-artifact audit verified seven runs,18-to14 reduction, ZIP and candidate checks; ZIP-recovered execution/API/UI still separate |
| VF-024 | P0 | Upgrade runtime/evidence routes / runtime | LIVE GATES PASSED | Backup/migration, real export/download and exact replay passed on frozen image; independent audit and browser checks remain |
| VF-025 | P1 | Recovery and backup/restore / runtime, review | RESTORE SOURCE ACCEPTED; LIVE PENDING | Controlled restart/lease/broker recovery, isolated restore and running-process cancellation; preserve data |
| VF-030A/B/053 | P0 | Meta planner/local bridge / Meta, reviewer | SOURCE REVIEWED |61 owner mocked checks and focused independent review; no live Meta or durable application bridge |
| VF-032 | P0 | Meta readiness / Meta | BLOCKED_EXTERNAL | Oct29 process/project .env safe check: key absent; setup action surfaced once; no live inference |
| VF-033 | P0 | Live feedback/durable campaign integration / Meta, API | SOURCE IMPLEMENTATION RESUMED | Real valid proposals execute and use checker feedback; budget/cancel/usage and resumable API/UI |
| VF-050A/051 | P1 | Arithmetic source/oracle / additional RTL, contracts | SOURCE REVIEWED |34pass3gatedskip and20 independent pure cases; real HDL below |
| VF-050B | P0 | Real arithmetic positive/negative gate / additional RTL | ACCEPTED | Three actual WIDTH8/latency2 cases; independent saved-artifact/oracle audit accepted |
| VF-052/053 | P0 | Trusted family registry / RTL, review | SOURCE REVIEWED |27 owner stub checks plus independent cases; adoption remains separate |
| VF-054 | P1 | Versioned family adoption / API, RTL, evidence | SOURCE ACCEPTED / PR9 | Reviewed family API/evidence and UI; actual application gate remains separate |
| VF-055 | P1 | Bounded AXI-Lite package / contracts, RTL | LIVE DETECTION ACCEPTED | Four correct controls and three distinct faults independently audited; API/repair gates remain |
| VF-056 | P1 | Three-family corpus/coverage / RTL, review | NOT_STARTED | Additional FIFO/arithmetic faults, semantic coverage/reduction and equivalent-mutant classification |
| VF-060A | P1 | Vivado import/preparation / Vivado | IMPORT-ONLY ACCEPTED |30 combined checks after redaction fixes; no vendor execution/functional repair |
| VF-061 | P1 | Live Vivado investigation / Vivado | TOOL/PART READINESS VERIFIED | Actual2026.1 query succeeded; design synthesis/timing/CDC/repair remain |
| VF-070A/B | P0 | Local workbench / UI | SOURCE + BASELINE VIEWS VERIFIED |20 mocked interactions/typecheck/build; real old-baseline findings and draft; new actions disabled on old server |
| VF-071 | P0 | Live evidence UI flow / UI | IN_PROGRESS | Browser inspection of actual upgraded artifacts/export/replay lineage; no duplicate simulator job |
| VF-072 | P1 | Planner/reduction/candidate UI / UI | NOT_STARTED | Accepted APIs and real flow, responsive accessible contract/source/trace views |
| VF-082 | P1 | Frozen evaluation / review | NOT_STARTED | Hidden-label-safe corpus and directed/random/one-pass/feedback comparisons with equal budgets/raw records |
| VF-091 | P0 | CI/publication preparation / RTL, main | SOURCE REVIEWED | Corrected hosted run40053589790 passed Python/web/real FIFO smoke; PR8 merged at9163187 |
| VF-092 | P0 | GitHub delivery / main, review | FIRST INTEGRATION ACCEPTED | PR8 merged and issue1 closed; remaining focused integrations/CI/issues continue |
| VF-090 | P1 | Clean setup/docs/local demo / main, workers | NOT_STARTED | Fresh install/replay, exact identities, limitations and labeled recorded fallback |
| VF-093 | P1 | Submission / main | NOT_STARTED | Organizer approval settled; prepare actual deliverables/links and honest provenance; final external submission/deployment separate |

## Evidence limits and operating rules

- Preserve intentionally tampered original FIFO stimulus; use intact replay packages. A timeout/harness error is not a design defect.
- Preserve baseline image13931f23712e46893507637357354e58f9e8d19780171945b7261f472a109323 and valuable DB/artifact volumes through upgrade. Retained image remains available; DB/Valkey recovered, controlled family adoption underway.
- Real evidence workflow: one passed252.96s; independent audit verifies96 artifact hashes/sizes, seven runner records, same-semantic reduction and three distinct candidate checks. No actual ZIP-recovered replay or arbitrary generated patch claim.
- Arithmetic WIDTH1 truncation mutant is equivalent; negative acceptance uses WIDTH8. General software adapters remain roadmap.
- UI stays entirely local. Credentials/private coordination/generated evidence/local databases stay out of Git; curated trusted execution only.
- Reuse workers and the same schedule. Shared simulator/build/migration/Git operations each have one recorded owner. Public release/deployment and destructive cleanup retain their authorization boundaries.
