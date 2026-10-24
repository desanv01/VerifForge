# Implementation checklist

Updated 24 October 2026 after direct human resume. Implementation and retained-worker coordination are RUNNING; the existing five-minute heartbeat is ACTIVE. Read private canonical current-state.json/worker-registry.json for current controls, operation owners and later human overrides. Main owns integration/acceptance; persistent workers implement finite scopes and independently review substantive changes.

The gameplan defines intended release scope. Source and mocked tests do not establish live tool/model execution. Counts overlap and must not be added into a project total.

| ID | Priority | Outcome / owner | Status | Next acceptance gate |
|---|---|---|---|---|
| VF-000/010 | P0 | Workspace and contracts / main, contracts | ACCEPTED | Reviewed immutable FIFO contract and independent schema/oracle checks |
| VF-011 | P0 | Real FIFO execution / RTL | ACCEPTED | Linux nine-test gate: correct/reset mutant, three exact replays, build error and process cleanup; byte-cap correction reviewed |
| VF-020/021 | P0 | Durable API/runtime / API, runtime | INITIAL GATE ACCEPTED | Actual HTTP/PostgreSQL/Valkey/RQ/Linux FIFO; correct/mutant, duplicate delivery, queued cancellation; broader recovery separate |
| VF-022/023 | P0 | Evidence API source / API, reviewer | SOURCE ACCEPTED | Replay collision corrected; prior40pass1skip plus final separately passed screening case; live upgrade VF024 |
| VF-040A/041/081 | P0 | Evidence module and real curated FIFO workflow / evidence, review | ACCEPTED WITHIN SCOPE | Independent saved-artifact audit verified seven runs,18-to14 reduction, ZIP and candidate checks; ZIP-recovered execution/API/UI still separate |
| VF-024 | P0 | Upgrade runtime/evidence routes / runtime | LIVE GATES PASSED | Backup/migration, real export/download and exact replay passed on frozen image; independent audit and browser checks remain |
| VF-025 | P1 | Recovery and backup/restore / runtime, review | NOT_STARTED | Controlled restart/lease/broker recovery, isolated restore and running-process cancellation; preserve data |
| VF-030A/B/053 | P0 | Meta planner/local bridge / Meta, reviewer | SOURCE REVIEWED |61 owner mocked checks and focused independent review; no live Meta or durable application bridge |
| VF-032 | P0 | Meta readiness / Meta | DEFERRED BY HUMAN | No keys yet; continue all independent work; no inference performed |
| VF-033 | P0 | Live feedback/durable campaign integration / Meta, API | NOT_STARTED | Real valid proposals execute and use checker feedback; budget/cancel/usage and resumable API/UI |
| VF-050A/051 | P1 | Arithmetic source/oracle / additional RTL, contracts | SOURCE REVIEWED |34pass3gatedskip and20 independent pure cases; real HDL below |
| VF-050B | P0 | Real arithmetic positive/negative gate / additional RTL | ACCEPTED | Three actual WIDTH8/latency2 cases; independent saved-artifact/oracle audit accepted |
| VF-052/053 | P0 | Trusted family registry / RTL, review | SOURCE REVIEWED |27 owner stub checks plus independent cases; adoption remains separate |
| VF-054 | P1 | Versioned family adoption / API, RTL, evidence | SOURCE IN REVIEW | API and shared identity helpers delivered; evidence family support active; live application execution remains separate |
| VF-055 | P1 | Bounded AXI-Lite package / contracts, RTL | IN_PROGRESS | Contract79 owner and39 independent pure checks accepted; runner/fixtures source active; live qualification remains |
| VF-056 | P1 | Three-family corpus/coverage / RTL, review | NOT_STARTED | Additional FIFO/arithmetic faults, semantic coverage/reduction and equivalent-mutant classification |
| VF-060A | P1 | Vivado import/preparation / Vivado | IMPORT-ONLY ACCEPTED |30 combined checks after redaction fixes; no vendor execution/functional repair |
| VF-061 | P1 | Live Vivado investigation / Vivado | BLOCKED_EXTERNAL | Actual installed tool/device/project unavailable at prior inventory; retain honest import-only support |
| VF-070A/B | P0 | Local workbench / UI | SOURCE + BASELINE VIEWS VERIFIED |20 mocked interactions/typecheck/build; real old-baseline findings and draft; new actions disabled on old server |
| VF-071 | P0 | Live evidence UI flow / UI | IN_PROGRESS | Browser inspection of actual upgraded artifacts/export/replay lineage; no duplicate simulator job |
| VF-072 | P1 | Planner/reduction/candidate UI / UI | NOT_STARTED | Accepted APIs and real flow, responsive accessible contract/source/trace views |
| VF-082 | P1 | Frozen evaluation / review | NOT_STARTED | Hidden-label-safe corpus and directed/random/one-pass/feedback comparisons with equal budgets/raw records |
| VF-091 | P0 | CI/publication preparation / RTL, main | SOURCE REVIEWED | Python/web and bounded Linux FIFO CI committed; first hosted run pending |
| VF-092 | P0 | GitHub delivery / main, review | IN_PROGRESS | Public desanv01/VerifForge created; coherent initial publication, per-task commits/PRs/current-head CI/guarded merge and issue closure |
| VF-090 | P1 | Clean setup/docs/local demo / main, workers | NOT_STARTED | Fresh install/replay, exact identities, limitations and labeled recorded fallback |
| VF-093 | P1 | Submission / main | NOT_STARTED | Current event rules/deliverables/links and eligibility checked before claims; deployment/release separate |

## Evidence limits and operating rules

- Preserve intentionally tampered original FIFO stimulus; use intact replay packages. A timeout/harness error is not a design defect.
- Preserve baseline image13931f23712e46893507637357354e58f9e8d19780171945b7261f472a109323 and valuable DB/artifact volumes through upgrade. Frozen reviewed evidence-API image now runs; later family source is not yet adopted.
- Real evidence workflow: one passed252.96s; independent audit verifies96 artifact hashes/sizes, seven runner records, same-semantic reduction and three distinct candidate checks. No actual ZIP-recovered replay or arbitrary generated patch claim.
- Arithmetic WIDTH1 truncation mutant is equivalent; negative acceptance uses WIDTH8. General software adapters remain roadmap.
- UI stays entirely local. Credentials/private coordination/generated evidence/local databases stay out of Git; curated trusted execution only.
- Reuse workers and the same schedule. Shared simulator/build/migration/Git operations each have one recorded owner. Public release/deployment and destructive cleanup retain their authorization boundaries.
