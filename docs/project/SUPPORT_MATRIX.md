# Actual support and open acceptance gates

This matrix records demonstrated scope. The implementation gameplan describes the intended release, not present capability. Main updates this file at accepted milestones.

| Area | Current evidence | Open gate |
|---|---|---|
| Shared contracts | Typed, bounded, immutable FIFO schemas; 42 owner tests; independent review accepted | Additional families have separate schemas until registry integration |
| Durable API | Initial real flow and independently reviewed evidence API; real export/download and exact replay passed on identified image | Broader recovery and family adoption pending |
| Local services | PostgreSQL and Valkey healthy; migration succeeded; API health on localhost 18000 | Actual correct/mutant/duplicate/queued-cancel accepted; further recovery tests pending |
| FIFO | Real correct pass/reset mutant fail, three exact replays, build failure, process cleanup and host evidence bridge accepted | Additional fault coverage and release corpus pending |
| Meta planner | Provider protocol verified against official docs; bounded controller and local bridge independently reviewed | Human deferred live inference until keys available; durable application bridge pending |
| Replay/reduction/export | Independently audited real curated FIFO workflow: exact replays, 18-to-14 reduction, export and candidate checks; real API export/replay passed | Browser integration and actual ZIP-recovered execution remain separate |
| Arithmetic | Three real WIDTH8/latency2 cases accepted: correct pass, latency and data mismatches; independent artifact and hand-derived timeline audit | API/evidence adapter adoption in source review; broader fault coverage pending |
| AXI-Lite | Bounded contract/oracle accepted after 79 owner and 39 independent pure checks | Runner/fixtures in development; no live AXI execution acceptance |
| Vivado | Reviewed, tested bounded log import and isolated candidate preparation | Tool absent in bounded inventory; no live execution or functional validation |
| Workbench | Local UI and precision-safe 64-bit evidence inspection reviewed | Live upgraded browser workflow underway |
| GitHub | Public repository, scoped commits, issue tracking and reviewed CI workflow | First hosted CI and integration PR acceptance pending |
| Benchmark | Protocol in gameplan | Frozen valid corpus and actual comparable measurements |
| Public deployment | Not performed | Separate authorization and remote-access controls |

Local credentials, generated logs and private coordination records are not committed. Reported test counts describe scoped checks and must not be added together as a unique total without deduplicating overlapping suites.
