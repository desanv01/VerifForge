# VerifForge implementation

Read docs/project/IMPLEMENTATION_GAMEPLAN.md and your assigned scope before editing. In the coordinator's local checkout, also read the latest private planning handoff and .coordination/current-state.json. The user authorized implementation and persistent phase worker chats, with no three-worker limit. Human resumed implementation, workers and the existing schedule on 29 October 2026, explicitly superseding the October24 pause. The active coordinator and current controls are recorded in private canonical state; migration archives remain historical.

## Coordination
- Main owns integration, root configuration, shared dependency locks, canonical .coordination/current-state.json, worker-registry.json, and MASTER_CHECKLIST.md unless explicitly delegated.
- Workers edit only assigned paths. Do not switch branches, commit other workers' files, create workers, deploy, publish, or create schedules.
- The human explicitly authorized project team assignments, questions, and reports between main and registered workers in the invoked orchestration document. Report a finite delivery to main using send_message_to_thread; write evidence in your own .coordination/deliveries/<task-id>.md.
- Read current-state.json for later pause controls before starting a new operation. Stop at your task boundary; do not start another phase without assignment.
- Shared checkout: do not overwrite others' edits. Request shared-interface changes from main.
- Record actual commands, outcomes, limitations, and changed paths. Never mark mock execution as real simulator/model/Vivado evidence.

## Product constraints
- One engineering investigation engine. October29 direction retains sequences 1-6: RTL, real RISC-V, firmware/MMIO, live Vivado, KiCad and independent cross-domain checks. Sequence7 is stretch; broader software sequence8 deferred.
- Model proposes typed experiments; independent immutable checker determines verdict. Execution status and design verdict are separate.
- Preserve exact stimulus, source/checker/contract identity, tool versions, evidence, and failure signature.
- A timeout is not automatically a design defect. A successful build is not functional correctness.
- Existing RTL targets: FIFO, arithmetic pipeline, bounded AXI-Lite. Expanded adapters need explicit supported configurations and independent acceptance. Track Import / Execute / Repair separately.
- Curated trusted fixtures first; no arbitrary shell commands or uploaded Python execution. Credentials stay out of simulator jobs and reports.
- Initial implementation is local. Public deployment/release needs separate authorization.
- Use official current documentation for evolving dependencies. Read required relevant skills, especially 12ui before substantial UI creation.

## Layout and ownership baseline
Python src layout: src/veriforge/{contracts,core,rtl,api,planner,evidence,vivado}; React in apps/web; HDL in targets/{fifo,arithmetic,axi_lite}; tests mirror component names. Python dependencies owned by contracts/bootstrap worker initially, transferred explicitly afterward. Avoid unnecessary packaging layers.
