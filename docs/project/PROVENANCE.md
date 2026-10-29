# Source, tool and evidence provenance

Prepared 29 October 2026. This is an attribution framework and known-evidence inventory, not a completed license audit or release. Preserve earlier source history and the [contribution chronology](EVENT_CONTRIBUTIONS.md). Public source visibility does not grant a project license or remove upstream obligations.

Refreshed 30 October 2026 MYT from evidence through 02:51 MYT (29 October, 18:51 UTC). [PR #9](https://github.com/desanv01/VerifForge/pull/9), [#15](https://github.com/desanv01/VerifForge/pull/15), [#17](https://github.com/desanv01/VerifForge/pull/17), [#18](https://github.com/desanv01/VerifForge/pull/18), [#19](https://github.com/desanv01/VerifForge/pull/19), [#20](https://github.com/desanv01/VerifForge/pull/20) and [#21](https://github.com/desanv01/VerifForge/pull/21) are merged after their current-head CI. Native investigation [PR #22](https://github.com/desanv01/VerifForge/pull/22) merged as `496c89fb72aa2d4e6ff322dad9db279fba81cb21` after [CI run 40919086170](https://github.com/desanv01/VerifForge/actions/runs/40919086170). Preserve exact source revisions separately from runtime images and native execution records.

## Ownership and dependencies

| Material | VerifForge contribution / dependency boundary | Required release record |
|---|---|---|
| Investigation core, contracts, checker integration, API/evidence/UI | Project-authored integration; earlier foundation and new additions must be dated separately | Commit/diff, authoring origin, contract/checker hashes and confirmed contributor roles |
| FIFO/multiplier/AXI curated fixtures and seeded defects | Demonstration DUTs/variants; not claimed as discovered upstream bugs | Source hash, variant/parameters, fault-seeding method and independent witness/control |
| Verilator, cocotb, compiler, Python and OS image | Upstream execution tools, not authored by VerifForge | Exact version/image digest plus upstream license/notices for the selected distribution |
| FastAPI/Pydantic/SQLAlchemy/Alembic/RQ/Redis client and related Python packages | Third-party application dependencies | Exact [uv.lock](../../uv.lock) revision, installed-version receipt and license inventory |
| PostgreSQL, Valkey and container tooling | Third-party runtime/storage | Actual image ID/version and configuration; database content is not an upstream code license |
| React/Vite/TypeScript/TanStack and test packages | Third-party frontend/build tools | Exact [frontend lock](../../apps/web/package-lock.json), source/build receipt and license inventory |
| Meta model/provider | Product inference dependency, distinct from development assistance | Actual requested/returned model/provider, call date, terms/model license, prompt/schema hash and usage; no credentials |
| RISC-V core/reference/test programs | PicoRV32 and independent Spike selected after SHAKTI prerequisite review; VerifForge owns its integration, not either upstream | Pinned revisions below, ISA/configuration, source notices and integration modifications; actual curated smoke/seeded detection and exact recompiled-control gate accepted; saved-ELF execution without compilation unproven for this RAM-only case |
| Firmware/native MMIO fixture | Project-authored peripheral, C/assembly, curated bad header/restoration and literal immutable checker; upstream PicoRV32 reused | [Accepted raw-result subset](../evidence/firmware/2026-10-30/README.md), source 5565985, saved executable/observation/check identities; no Spike MMIO or generated-repair claim |
| Vivado/project/IP | Vendor tool and separately owned project; actual version/part query accepted, synthesis attempt resource-limited | Original project/source/XDC identity, installed tool/part, permitted redistribution; no completed reports/repair accepted |
| KiCad/designs/libraries/models/datasheets | KiCad 10.0.6 and project-authored original/corrected carrier; exact parts unresolved | Native original/candidate identity, unchanged configured checks including ignored rules, upstream notices and physical/electrical limits |
| System manifest/relationships | Project integration of independently extracted artifact facts | Fact source/revision/location, extraction version, reviewed mappings and ambiguity/units |
| Architecture illustration and future demo media | Original project illustration is labeled as such; it is not a product screenshot | Asset creator/source, generation/editing method, rights and recording revision; do not imply execution from a diagram |

The listed dependencies are not a license-compatibility conclusion. No root LICENSE/NOTICE file was present at this handoff inspection. Main must obtain the human's project-license decision and review actual upstream notices at pinned revisions before release/redistribution. Do not choose a license by copying another repository's badge, or assume a vendor tool's project files/IP may be bundled.

## Known execution provenance

Locally retained historical FIFO/arithmetic records report Verilator 5.042, cocotb 2.0.1, runner Python 3.12.3, GCC 13.3.0 and GNU Make 4.3. These are recorded combinations, not a promise that the current checkout, host interpreter or every new domain uses identical versions. Runtime application dependencies are uv-locked; the RTL image has a separate documented tool subset. Recheck installed versions for each frozen evaluation/demo image.

On October29, six resumed AXI cases and the preserved October24 control were independently accepted against those recorded RTL tool versions and exact source/checker/contract/stimulus/environment identities. Four controls pass and three seeded faults are detected; no new model-discovered bug or generated repair is implied. The isolated PostgreSQL 16.13 restore of the original October24 c67f9c9f19e5 backup was also independently accepted. It establishes that original database snapshot and saved cleanup, not newer data, external artifact restoration or application recovery.

The RISC-V baseline pins PicoRV32 revision `ef203c2b0a3fb793280f5114941416c425c5b461` and Spike revision `4c6a9bc79dab80616932949707e7c0471d8e9e54`, with the selected RV32I profile described in [FIRMWARE.md](FIRMWARE.md). Selection followed SHAKTI prerequisite review. Actual curated core/reference smoke and seeded-mutant detection are independently audited; the later exact recompiled-control gate is accepted, while saved-ELF execution without recompilation remains unproven for that RAM-only case. Preserve upstream notices and actual integration diffs in the final manifest.

Two local RISC-V tool-image builds ended without a complete image: deadline cancellation, then a host-memory-headroom guard. The later actual [hosted run 40912735570](https://github.com/desanv01/VerifForge/actions/runs/40912735570), at source `f6a85ea8c91620b934ff95b43b692f61b5f72df4`, used image `sha256:5fd610b32e3de74a317945494bd726781e475b34184a80e927bed543e4f6df27`. Recorded tools include Verilator 5.042, cross-GCC 13.2.0, cocotb 2.0.1 and Python 3.12.3; this cross-compiler version is distinct from the earlier RTL image's GCC. The bounded profile is little-endian RV32I, one hart, RAM only, with completion mailbox; it is not full ISA certification.

The first run control agreed with Spike for 17 retirements/68 cycles, and the seeded register mutant diverged at order 1/cycle 14 (`0x8000fef0` expected, `0x8000fef1` observed). That run remains failed at exact ELF replay: architecture/binary equality did not waive random temporary object-name metadata. Actual [rerun 40920450439](https://github.com/desanv01/VerifForge/actions/runs/40920450439) at source `44b8b5e40ce7841f076e4178df13f38ecdbd05fd` and image `sha256:77fb34efe7f2cda0daba0cb249358cbce8020158a023f2030e2c6726099dbcf5` passes the independently audited curated gate: both newly compiled controls 17/68, exact object/ELF/binary/raw/normalized records and the same seeded discrepancy. The [retained public CI evidence](../evidence/riscv/2026-10-30/README.md) includes exact identities, original producer data and omission/provenance limits. This recompiles the control; it does not execute the original saved ELF without compilation. The separate firmware configuration now has its own accepted actual gate, described below; it does not enlarge the RISC-V/Spike scope.

Actual [firmware run 40929665294](https://github.com/desanv01/VerifForge/actions/runs/40929665294) at source `5565985bbe2bb2a0be869d366baa48c715057657`, image `sha256:41215acab7a23238d62b5bfc9a6e09187afffbc689c0ad2689a9778133a85f85`, is independently accepted within the bounded native-MMIO fixture. Control/candidate each 55 retirements/218 cycles/72 bus transfers; bad 39/166/54 with first STATUS conflict cycle 95/bus 31/MMIO index 2. Bad/candidate saved-program replays preserve exact program/observation/identity bytes, with no compiler/binutils replay phases; unchanged HDL is rebuilt. The [firmware evidence README](../evidence/firmware/2026-10-30/README.md) and [identity manifest](../evidence/firmware/2026-10-30/identity-manifest.json) identify selected bytes and omissions. Candidate provenance is curated_header_offset_restoration, not model-generated.

Both CPU cases reuse PicoRV32 `ef203c2b0a3fb793280f5114941416c425c5b461`; programs/platforms/images/checkers differ. RISC-V uses RAM-only Spike comparison and recompiled controls. Firmware uses a literal independent MMIO contract/checker; Spike MMIO remains unsupported. Tool versions in the firmware packet include RISC-V GCC 13.2.0, binutils 2.42, Verilator 5.042, cocotb 2.0.1 and Python 3.12.3; do not equate either execution image with the application runtime. Their shared core establishes reuse, not authenticated connected-system agreement.

Both public subsets preserve original bytes and exact per-file identities, disclose omissions and retain complete extracted artifacts locally. Transport ZIP digests are labeled GitHub-reported rather than independently recomputed locally. Firmware preparation records retain historical not_run text; later execution/check records establish actual outcomes. Curation/audits do not create new native executions, and public result access does not complete a project/upstream license audit.

[PR #23](https://github.com/desanv01/VerifForge/pull/23) and [#24](https://github.com/desanv01/VerifForge/pull/24) are merged; [#25](https://github.com/desanv01/VerifForge/pull/25) merged as `1db9916dff8e2fbdf181727bc8ba4a4862b254fa` after [current-head CI 40922456584](https://github.com/desanv01/VerifForge/actions/runs/40922456584). Source publication is separate from actual execution acceptance and application/browser adoption. The accepted RISC-V and firmware hosted gates have their own exact source/image/checker records below.

Five actual family API/RQ executions use a separately pinned runtime image: arithmetic control/two faults, one retained FIFO replay and one arithmetic replay. Independent review verified exact stimulus/identities, native arithmetic sidecars, portable ZIP/download bytes and preservation of 39 earlier artifact files and saved rows. That establishes selected execution/replay/export paths; it does not attest to all dirty source, browser interaction or ZIP-recovered execution.

Vivado's first startup exceeded the cache cap; its revised query subsequently returned 2026.1 and all three selected part matches. The later synthesis attempt passed source read and began elaboration but hit the scratch/result cap before completed synthesis/reports. The revised retry is blocked by host headroom below 2 GiB. Preserve those outcomes distinctly from a vendor/design defect or timing conclusion.

The first local AXI overlay was blocked before build at 632.54 MiB headroom below its 768 MiB floor. A later image build and source verification succeeded; API data/artifacts were preserved during adoption, but consumer startup hit the host-memory abort floor. API, queue worker and reconciler were quiesced at 499 MiB headroom below the 512 MiB abort floor. Subsequent sequential recovery of the existing verified `e8eb` containers succeeded: healthy API with three adapters, idle queue worker and running reconciler; schema `7bf`, all prior nine-table rows and all 110 artifact files were preserved. The separate [hosted AXI run 40934406068](https://github.com/desanv01/VerifForge/actions/runs/40934406068) failed at 30 October 02:50:41 MYT (29 October 18:50:41 UTC), before migrations or any attempt, because Docker logging compression was incompatible with `max-file=1`. Owned resources were removed with no cleanup errors. This is infrastructure failure with no AXI execution or application/browser acceptance. Native investigation source checks report 242 passed/two private-evidence skips without runtime/browser adoption; overlapping counts are not a project total.

KiCad's original 26 ERC/26 DRC findings and distinct corrected zero-finding candidate have separate saved sources/outputs. Independent review confirms grid/net-name/blank-field changes only, with original files, copper/pads and 24 endpoint semantics preserved. Both use the same four ignored ERC/five ignored DRC checks. Exact MPN, cable orientation, hole fit and electrical/physical suitability remain unresolved; a curated grid/parity correction is not exact-part validation.

Native graph bridge source fixes are independently accepted, while current graph/candidate results remain inconclusive/no demonstrated improvement. Retain full derivation ancestry, review authority and native source references; a self-consistent hash graph or commented reference XDC cannot become authenticated independent evidence or an executed project constraint.

Every selected finding must bind exact source/variant/parameter, checker, reviewed contract, experiment and environment hashes; tool versions; actual observations; independent verdict/signature; artifact size/hash; execution status; and remaining uncertainty. Actual numerical semantics and validity windows are domain-specific. A known-passing DUT is not automatically an independent oracle; retain hand-derived controls/reference checks and any disagreements.

Hash consistency is not authentication against an actor replacing every record. Historical evidence relies on trusted immutable storage/producer and server provenance. Public summaries should state that boundary rather than claim tamper-proof authenticity.

## Model and repair provenance

At this snapshot, product live Meta use is **unverified**. Provider/controller mocks and coding-assistant use cannot be relabeled as real product inference. For a real model run preserve context information policy, exact response/proposal, requested/returned model, provider, prompt/schema identity, validation rejection/acceptance, usage/unknown completion and the checker feedback consumed by the next round. Record what source/report facts were sent to the provider without logging keys.

The human deferred model-key readiness. FIFO helpers/controller, durable experiment/round state and bounded transport source are independently accepted after fixes; no real Meta call or end-to-end durable broker/provider feedback follows from those tests. Family source/UI merged in PR #9 and selected API execution is accepted separately; fresh browser evidence remains pending. Preserve exact requested/returned model identity and actual usage when live use is later authorized and possible.

Use separate repair origins: `prepared_variant`, `manual_patch`, `model_generated_patch`. Historical switching from fifo_reset_bug to fifo_correct is prepared-variant validation; the same raw HDL with a different elaboration variant can have a distinct source identity. It does not show a generated patch. A generated claim needs an actual stored response/diff against a base revision, allowed-file provenance, unchanged checker/contract and witness/neighbor/regression receipts. A reviewed requirement change is a new contract, not a repair under the old one.

## Evidence tiers and sharing

| Tier | What it supports |
|---|---|
| Source/static review | Implementation intent and compatibility; not tool execution |
| Synthetic/mock test | Decision rules, persistence/transport behavior under labeled doubles |
| Pure reference calculation | Semantics for tested vectors; not actual DUT behavior |
| Actual tool execution | Recorded tool output under its source/environment/configuration |
| Independent saved-evidence audit | Recomputed hashes/observations/claims without rerunning the tool |
| Browser observation | Display/action behavior through the tested API; its underlying execution origin remains separately identified |

A later audit or browser display does not create a new simulator execution. A test pass can mean an observation driver ran correctly while the independent design verdict is mismatch. Timeout, unsupported tool and harness/reference failure remain separate from a functional defect.

Keep `.env`, keys/tokens/licenses, private coordination, database dumps, private HDL, machine paths and unsanitized logs out of the public packet. Exports use reviewed allowlists/content screening and preserve exact included bytes; screening is not a universal secret detector. Review the actual selected files and video before sharing. Upstream attribution does not authorize disclosure of private design material.

Local receipt filenames are not public evidence links. Publish a separately reviewed sanitized claim manifest or reproducible open fixture when authorized, with exact record hashes, scope and dependencies. Do not fabricate a permalink to unpublished evidence. The portable ZIP requires the matching installed runner; it is not a bundled executable environment or proof of an actual ZIP-recovered replay unless that run is recorded.

## Items still needed for the final packet

- Project license decision and complete pinned upstream license/notice inventory, including newly selected CPU/reference, circuits/libraries and model terms.
- Confirmed human contributor roles and date-bounded event additions; origin of preserved dirty files reconciled.
- Current source/image/tool/prompt/model freeze manifest and actual live Meta usage record.
- Candidate patch lineage and independent cross-domain source facts for every new demonstration claim.
- Reviewed sanitized evidence/media inventory and accessible links; private originals retained unchanged.

See [EVALUATION.md](EVALUATION.md), [DEMO_PLAN.md](DEMO_PLAN.md) and [SUBMISSION_CHECKLIST.md](SUBMISSION_CHECKLIST.md). No license was selected, upstream code imported, raw evidence published, model/tool executed or external submission made by this document.
