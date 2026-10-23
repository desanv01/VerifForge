# Implementation checklist

Coordinator maintains acceptance based on evidence, not worker assertions. Latest user instruction: implement now; persistent phase workers with no fixed three-worker cap.

| ID | Priority | Deliverable | Dependencies | Status | Acceptance |
|---|---|---|---|---|---|
| VF-000 | P0 | Workspace and orchestration | None | ACCEPTED | Plan preserved, ownership assigned, one heartbeat |
| VF-010 | P0 | Typed contracts and Python bootstrap | VF-000 | ACCEPTED | Validated experiments/results, separate verdict/status, tests |
| VF-011 | P0 | Real FIFO simulation | VF-010 interface | IN_PROGRESS | Correct passes, faulty fails, saved input replay, actual simulator evidence |
| VF-020 | P0 | Durable API and worker execution | VF-010, VF-011 | IN_PROGRESS | Create/read/events/cancel/recovery with durable records |
| VF-030 | P0 | Meta investigation loop | VF-010, VF-011 | IN_PROGRESS; live key missing | Typed live proposal, execution feedback, enforced budgets |
| VF-040 | P0 | Replay, reduction, candidate validation | VF-011 | IN_PROGRESS | Same failure preserved, immutable checker, regression evidence |
| VF-050 | P1 | Arithmetic and AXI-Lite | VF-011 | PENDING | Real positive/negative cases for each family |
| VF-060 | P1 | Vivado workflow | Tool inventory, contracts | IN_PROGRESS; live tool missing | Actual batch reproduction and repair plus functional checks |
| VF-070 | P0 | Workbench UI | VF-010 API agreement, VF-020 | IN_PROGRESS | Project-to-finding-to-replay flow; accurate capability/error states |
| VF-080 | P1 | Independent evaluation | VF-030, VF-040, VF-050 | PENDING | Frozen corpus, comparable baselines, raw evidence |
| VF-090 | P1 | Delivery documentation and local demo | Accepted integration | PENDING | Clean install/replay and accurate support matrix |

Public release/deployment is separately controlled. Live Meta and Vivado acceptance remain pending until actual account/tool access is verified. Do not report planned benchmark counts as completed measurements.


## Accepted evidence

- VF-000: empty-workspace inventory, saved plan, registered workers, active single five-minute heartbeat verified in automation.toml.
- VF-010: 42 owner contract tests pass; independent 43 schema/oracle checks (one known socket-boundary test deselected) and six additional foundation checks pass. Four explicit integration xfails belong to VF-020 and remain open until corrected.

