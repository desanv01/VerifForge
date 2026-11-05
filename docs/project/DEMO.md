# VerifForge demo and evidence

**[Watch the three-minute video →](https://youtu.be/9nSpILaVUCE)**

Meta Global AI Developer Hackathon 2026 · Push the Frontier · Indie Developer Track · Team SkyNet / Desan Vasu.

The narrated film uses authored graphics to inspect retained engineering evidence. It presents two distinct configurations, with the model's proposal, tool execution and independent verdict kept separate.

| Video chapter | What to look for | Evidence |
| --- | --- | --- |
| 0:00–0:25 | Why an engineering claim needs observed results and checker identity | [Architecture](../assets/veriforge/investigation-flow.svg) |
| 0:25–1:35 | Actual Meta proposal, 17-cycle native FIFO experiment, checked persisted feedback and a separately authorized subsequent proposal | [Planner record](../evidence/planner/2026-11-05/README.md) · [Hosted run](https://github.com/desanv01/VerifForge/actions/runs/41786694029) |
| 1:35–2:15 | Seeded firmware STATUS-address conflict, hand-authored correction and saved-program replay | [Firmware record](../evidence/firmware/2026-10-30/README.md) · [Hosted run](https://github.com/desanv01/VerifForge/actions/runs/40929665294) |
| 2:15–3:00 | Proposal/execution/checker separation, provenance and contribution boundaries | [Interfaces](INTERFACES.md) · [Provenance](PROVENANCE.md) |

## Meta/FIFO chain

An actual `muse-spark-1.3` response supplied a typed 17-cycle FIFO proposal. Hosted run **41786694029**, attempt 1, source **4d0c539b944c3bc38cad86078db30f49ef39a64a**, completed and received `pass_within_scope` from the immutable checker. The stimulus reset an initially empty FIFO; it did not expose the known reset defect.

The observed result was imported, checked again and persisted as feedback. The original bounded followup expired without invoking its provider and was later cancelled. A separately authorized actual Meta request consumed the saved feedback and returned a valid **13-cycle proposal that was not executed**. The record establishes this scoped chain; it is not an uninterrupted autonomous investigation or a model-generated repair. [Full model/run lineage →](../evidence/planner/2026-11-05/README.md)

## Firmware mismatch, correction and replay

A separate curated firmware header writes STATUS to `0x40000008` instead of `0x40000004`. The native MMIO checker identifies the first conflict at **cycle 95**, bus record 31, the third MMIO observation. A hand-authored one-line restoration passes under the unchanged checker and contract. Saved-program replay uses retained object/ELF/binary artifacts without recompiling the firmware; HDL is rebuilt.

These are seeded simulation cases, not physical hardware or an upstream defect discovered by Meta. [Candidate diff](../evidence/firmware/2026-10-30/candidate/candidate.diff) · [Identity manifest](../evidence/firmware/2026-10-30/identity-manifest.json)

## Event work and reused foundation

Our event-period contribution is the integration behind these recorded Meta/FIFO feedback and firmware/MMIO check/replay paths, demonstrated on 4 November and 30 October. The earlier deterministic FIFO/platform foundation predates the event. PicoRV32 and execution tools remain upstream work. The [contribution ledger](EVENT_CONTRIBUTIONS.md) records dated source and execution milestones separately; operation dates alone do not establish source authorship.

Team **SkyNet**, project **VerifForge**, sole entrant **Desan Vasu**. Original first-party code is [AGPL-3.0-only](../../LICENSE); third-party notices remain intact. The [support matrix](SUPPORT_MATRIX.md) gives exact supported configurations and application/recovery/reproduction boundaries. The film does not claim a qualified live workbench, arbitrary-project support, accepted KiCad exports, physical hardware or a completed 144-coordinate evaluation.

[Frame provenance](../assets/demo/README.md) · [Local setup](LOCAL_RUNTIME.md) · [Documentation index](README.md) · [Project home](../../README.md)
