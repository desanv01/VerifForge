# Bounded AXI application evidence — 30 October 2026 MYT

The seven-outcome ephemeral API gate, exact strobe-plan replay and export readback are independently accepted within the curated configuration below. This directory is a **selected raw evidence subset plus authored explanation/manifest**, not the complete hosted artifact or a new execution. Publication remains main's action after independent subset review.

[Run 40948230223](https://github.com/desanv01/VerifForge/actions/runs/40948230223) used [source 19e08ef7c968811eb0148b2eb5197373608d926a](https://github.com/desanv01/VerifForge/commit/19e08ef7c968811eb0148b2eb5197373608d926a). Actual execution status is completed for all seven cases. Gate success means controls pass and deliberately seeded faults are detected; it does not mean all seven design verdicts pass or that seven bugs were discovered.

## Recorded outcomes

| Case / retained result | Executed cycles | Design verdict and first failure |
|---|---:|---|
| [FIFO control](application/fifo-attempt/result.json) | 5 | `pass_within_scope` |
| [Arithmetic control](application/arithmetic-attempt/result.json) | 15 | `pass_within_scope` |
| [AXI control](application/control-attempt/result.json) | 149 | `pass_within_scope` |
| [AXI order seed](application/order-attempt/result.json) | 11 | `property_failure`, cycle7, `AXIL_PROGRESS` / `axil.response_bound` |
| [AXI strobe seed](application/strobe-attempt/result.json) | 9 | `design_mismatch`, cycle7, `AXIL_READ` / `axil.read_value` |
| [AXI response seed](application/response-attempt/result.json) | 7 | `property_failure`, cycle4, `AXIL_HANDSHAKE` / `axil.stalled_response` |
| [Exact strobe replay](application/replay-attempt/result.json) | 9 | Same strobe `design_mismatch`, cycle7 and exact signature |

Negative AXI checker prefixes are8/8/5 observations for order/strobe/response, respectively; these are distinct from executed11/9/7 cycles. The immutable checker distinguishes property failure from data mismatch. Order expects `bVALID within 4 edges`, observed `4`; strobe expects `(0, 297481181)`, observed `(0, 2864434397)`; response expects stable VALID/payload, observed channel `b`. Full expected/observed and signature hashes are retained in each `check.json` and `result.json`.

All faults are curated seeds. There is no model proposal, model discovery, live provider call, candidate RTL correction or general AXI correctness claim in this evidence.

## Raw records and semantic recomputation

[identity-manifest.json](identity-manifest.json) enumerates every selected path, original artifact-relative path, SHA256 and byte count, plus the complete147-file original inventory and76 omissions. All71 selected raw files were inspected before copying and remain byte-identical to the accepted retained artifact. This README and manifest are newly authored; `derived_execution_summary` is explicitly derived, not an original producer receipt.

Each case has exact `experiment.json`, `stimulus.json`, `observed.json`, `contract.json`, `checker.py`, RTL and `result.json`. AXI cases also retain the typed `checker-contracts.py` and raw `check.json`; arithmetic retains its native scheduled-product trace. Validation-status records are original controller outcomes, not an independent oracle for the design verdict. Their preservation status is qualified by the omission boundary below.

To inspect or independently recompute outcomes, use the immutable source at the pinned commit, compare its identities with [source-manifest.json](source-manifest.json) and the retained checker/source bytes, then reconstruct the FIFO mathematical trace, scheduled arithmetic trace or AXI handshake/register ledger from the saved input/observation pairs. The prior independent audit performed that recomputation; this curation did not import copied Python or run any checker/compiler/simulator. A new review should use a trusted matching implementation, not execute arbitrary artifact code. Raw file hashes and canonical typed contract/experiment identities are separate calculations; use the pinned normalization algorithms rather than assuming a serialized file hash equals a typed identity.

The authored safe case summary projects only source basename and elaboration parameters from omitted job records, preserving the original job hash. FIFO is WIDTH8/DEPTH4/RESET_BUG0; arithmetic WIDTH8/LATENCY2/MUTANT0; AXI MUTANT0/1/2/3 means control/order/strobe/response, with replay MUTANT2. Build/output paths and complete runtime jobs are not published. AXI protocol parameters are32-bit data,8-bit address,four registers, one outstanding read/write, request-ready/response bounds4, write-pair bound8 and maximum response stall8. These bounds are contract-specific, not universal AXI latency requirements.

## Exact replay and export closure

The original [strobe](application/strobe-attempt/experiment.json) and [replay](application/replay-attempt/experiment.json) share exact stimulus, observations, check, contract, experiment, RTL and checker bytes: eight retained stable files match byte-for-byte. Their source/checker/contract/experiment identities and failure signatures match. The [frozen replay request](application/frozen-replay-request.json) binds the source attempt. Distinct wire attempt IDs are not interchangeable with database attempt IDs. This is native reexecution of the same curated plan, not saved-compiled-executable reuse.

A ninth stable file, runtime `environment.json`, was verified equal in the full audit and rehashed during selection, but is deliberately withheld here. Its equal SHA256 and result environment identity remain recorded; this subset alone cannot reconstruct its bytes or a complete runtime environment. The replay claim's eight public byte comparisons can be checked directly; the additional opaque environment identity rests on the accepted retained-artifact provenance.

The actual [API export ZIP](application/axi-export.zip),10791bytes, SHA256 `b2c9e675284499eaa126b87c37a2c0abbe445f577e229859e5606979d77859f9`, matches its original [export readback record](application/export.json). It is included because exact exported transport bytes are needed to verify export closure. All10 ZIP members were inspected before copying: `manifest.json`, `experiment.json`, `REPORT.txt`, and seven public artifacts (`check.json`, `checker-contracts.py`, `checker.py`, `contract.json`, `observed.json`, `registers.sv`, `stimulus.json`). CRC/unique paths/no absolute or traversal/backslash/encrypted/symlink entries were checked; all member hashes/sizes are in the selection manifest. Exported artifacts and experiment equal the retained strobe bytes. No environment, job, execution log or DB data is inside this ZIP.

The export manifest's failure is an intentional four-field identity projection; full expected/observed values are in raw check/result records. The ZIP requires the matching installed curated runner; it is not a self-contained executable environment. Its exported Python is evidence source, not code executed by this publication task. The bounded privacy review is not a universal secret detector.

## Source, tools and reproduction prerequisites

The [fixed hosted controller](https://github.com/desanv01/VerifForge/blob/19e08ef7c968811eb0148b2eb5197373608d926a/scripts/ci_axi_application.py), [workflow](https://github.com/desanv01/VerifForge/blob/19e08ef7c968811eb0148b2eb5197373608d926a/.github/workflows/axi-application-acceptance.yml) and captured162-source manifest identify the producer and dependencies. No current mutable source is substituted for this recorded run. Fixture/checker integration is project-authored; Verilator, cocotb, Python, GNU compiler/Make and application/storage tools are upstream dependencies. No project license is chosen by this packet.

Recorded tool versions are Verilator5.042, cocotb2.0.1, Python3.12.3, Ubuntu g++13.3.0 and GNU Make4.3. Native result records retain these versions. Recorded image ID is `sha256:c88253fa78f6d2a93ef1c25cce9af74720520a4131f4d22219cf93e6fd76b678`. Full runtime environment, image/host configuration and storage/runtime metadata are excluded. Image identity and tool versions do not certify arbitrary toolchains or promise identical future rebuilds.

Reproduction requires separately authorized resources, the exact pinned source and trusted checker/import dependencies, compatible recorded tools and the fixed controller's isolated API/queue/tool environment. Do not treat this subset as a distributed application or dispatch a new job simply to inspect it. No workflow, HTTP request, native job or service was started to prepare this directory.

Earlier hosted logging/mount/controller-verdict failures remain preserved separately; this later success does not rewrite them. In particular, the third run executed four campaigns but stopped before copying the order package because its controller expected the wrong verdict class. The accepted fourth run preserves the immutable checker's property/data distinction.

## Full-artifact and privacy limits

Original GitHub artifact12931210028 has reported outer ZIP188244bytes/SHA256 `22846ffd355a5ae964234e7f37a530938ebe85421474e8541335476bff13db32`. That outer transport digest is GitHub-reported, not locally recomputed here. It differs from the retained API export ZIP, whose actual included bytes were rehashed. The complete extracted artifact is147files/982942bytes; its sorted compact path→{bytes,sha256} inventory digest is `fae8ae582a25974dba2365ba415ac726b9b0bcd87d3df2f4855502fc929f2e52`, independently recomputed during selection. Complete extracted originals remain locally retained.

This subset omits all DB snapshots, campaign/queue metadata, host logs, runtime environment/job files, waveforms and other unselected bytes. Mixed raw summary/application-summary records are not copied; safe scalar outcomes/identities are explicitly projected in the authored manifest. Its full inventory names/hashes omitted files without publishing their contents. Original result artifact lists intentionally reference omitted members; that is not a claim of complete package or environment closure here.

The prior full-artifact audit accepted API-row/file preservation and owned cleanup. Those remain attributed recorded claims; without omitted DB/queue/runtime records this public subset cannot independently reconstruct complete preservation, final database census or cleanup. Selected raw outcomes, eight public replay identities and the export ZIP closure are directly inspectable/recomputable within the stated prerequisites. Hash consistency is not authentication against replacement of every record.

No browser/workbench execution, local runtime adoption, arbitrary AXI configuration, candidate repair, planner/model/live-provider investigation, restart/distributed recovery, physical hardware or final release is established by this packet.
