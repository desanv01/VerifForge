# Scoped actual Meta/FIFO feedback evidence

Status reconciled 5 November 2026 MYT; actual operations occurred on 4 November. This public summary records the accepted narrow demonstration and its limits. It does not distribute private model payloads, databases or raw coordination receipts.

| Step | Accepted observation |
|---|---|
| First actual Meta proposal | `muse-spark-1.3` returned a valid typed 17-cycle FIFO experiment; 704 input / 3066 output tokens |
| Hosted native execution | [Run 41786694029](https://github.com/desanv01/VerifForge/actions/runs/41786694029), attempt 1, job 126430357030, source `4d0c539b944c3bc38cad86078db30f49ef39a64a`: 17 cycles, execution completed, immutable checker verdict `pass_within_scope`, no failure signature |
| Persisted feedback | The observed native samples were checked again on import; the checked result and feedback were persisted and linked to the subsequent request |
| Separately authorized actual followup | `muse-spark-1.3` returned HTTP200 and a valid typed 13-cycle proposal using that saved feedback; 1236 input / 3009 output tokens. This new proposal was not executed |

The original bounded round expired without invoking its provider call and was later cancelled. Its deadline was unchanged. The actual followup had separate explicit authorization; these observations do not establish one uninterrupted successful campaign or success within the original round's budget. Owned local operations were settled. Provider price and independent remote-work settlement remain unknown; hosted job success does not independently prove post-job container absence.

The two accepted linked model calls total **1940 input / 6075 output tokens**. This is a two-call subtotal, not project-wide usage or spend. Earlier HTTP500 failures, invalid replies and other diagnostic calls remain historical; unknown usage/cost is not zero.

[PR81](https://github.com/desanv01/VerifForge/pull/81) supplied the versioned FIFO wire decision source. Its [exact-head CI41784213837](https://github.com/desanv01/VerifForge/actions/runs/41784213837), at `486dcd343df2c945164d95951e973226ffac23be`, passed selected checks; web and real FIFO smoke were skipped. That source CI and the actual native run above are distinct evidence. The [published acceptance comment](https://github.com/desanv01/VerifForge/issues/6#issuecomment-6625515551) records the narrow feedback result; [issue6 remains open](https://github.com/desanv01/VerifForge/issues/6) for broader application/two-stage API adoption, recovery/populated restore, exact-plan replay and workbench qualification.

The 17-cycle experiment used only an initial empty reset and did not expose the reset bug. There is no generated repair, new defect discovery, general functional-correctness, actual browser/full-runtime, restore/recovery or comparative-benchmark claim. Final video, synchronized captions, playback and external submission are not established by this evidence.

See the [support matrix](../../../project/SUPPORT_MATRIX.md), [demo plan](../../../project/DEMO_PLAN.md) and [submission checklist](../../../project/SUBMISSION_CHECKLIST.md) for the remaining gates.
