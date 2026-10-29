# KiCad configured-check provenance — 30 October 2026 MYT

**This README and [identity-manifest.json](identity-manifest.json) are authored, derived public projections, not original tool receipts.** They describe previously retained and independently reviewed runs; preparing them started no KiCad command, renderer or test. The prior projection was accepted; this public-availability documentation revision awaits narrow independent review. Original path-bearing receipts, native XML and generated configs remain unchanged and withheld.

The public [KiCad scope](../../../project/KICAD.md), [fixture overview](../../../../targets/kicad/README.md) and [carrier overview](../../../../targets/kicad/carrier/README.md) now carry availability notes. Their historical helper invocations describe prior actual operations, not included runnable public scripts. Private control/correction/preparation helpers, full receipts/XML/renders/configs and vendor research remain outside this slice; selected fixtures, raw reports and historical normalized projections are available as enumerated below.

The accepted scope is a distinct carrier correction from 26 reported ERC / 26 DRC findings to zero reported findings **under the same configured checks**. Four ERC and five DRC checks were ignored in both runs. This is not exact-part, electrical, physical, manufacturing or product acceptance.

## Exact source and retained results

Use the [original control](../../../../targets/kicad/carrier/control/carrier.kicad_sch) and [separate corrected control](../../../../targets/kicad/carrier/control_corrected_v1/carrier.kicad_sch), each with its complete seven-file local project/library set. The [preparation manifest](../../../../targets/kicad/corrections/U7-CONTROL-CORRECTION/manifest.json) and [candidate patch](../../../../targets/kicad/corrections/U7-CONTROL-CORRECTION/candidate.patch) identify lineage and three changed files. Preparation's `prepared_not_executed` status predates the actual corrected run; its unchanged historical text is not a new execution receipt.

| Stage | Original control, 29 October 2026 | Corrected control, 29 October 2026 |
|---|---|---|
| ERC | Exit5;26 `endpoint_off_grid` warnings;3.390s | Exit0;0 reported findings;3.360s |
| DRC with schematic parity | Exit5;24 `net_conflict` +2 `footprint_symbol_field_mismatch`;1.640s | Exit0;0 reported findings;1.531s |
| Native XML export | Exit0;24 endpoints;0.828s | Exit0;24 endpoints;0.953s |
| Schematic SVG export | Exit0;0.890s | Exit0;0.797s |
| PCB SVG export | Exit0;1.188s | Exit0;0.765s |
| Serial subprocess sum | 7.936s | 7.406s |

All five recorded stages completed with 30 seconds per stage /150 seconds total limits. SVG/XML rows report historical execution status; those raw outputs are not included in this focused public slice. No rendering or physical review occurred during this projection.

Exact native public reports:

- Original [ERC](../../../../targets/kicad/evidence/control-20261029T143637914406Z/reports/erc.json) and [DRC](../../../../targets/kicad/evidence/control-20261029T143637914406Z/reports/drc.json).
- Corrected [ERC](../../../../targets/kicad/evidence/control-corrected-v1-20261029T145057431340Z/reports/erc.json) and [DRC](../../../../targets/kicad/evidence/control-corrected-v1-20261029T145057431340Z/reports/drc.json).
- Original [review](../../../../targets/kicad/evidence/control-20261029T143637914406Z/review/review.json), historical [code identity](../../../../targets/kicad/evidence/control-20261029T143637914406Z/review/code-identity.json) and corrected [comparison](../../../../targets/kicad/evidence/control-corrected-v1-20261029T145057431340Z/review/comparison.json).

The initial audit selected 65 repository files:50 source/fixture/preparation files and15 retained evidence files,337814bytes then. The revised manifest preserves that baseline, identifies the three subsequent public-availability documentation revisions with initial/current hashes, and enumerates the final67-path publication set including these two derived files. The other62 selected files remain byte-identical to the audit. No copies of those files are created here. The manifest supplies exact hashes, per-run source inventories before/after, native report hashes, normalized projection identities, all recorded output hashes and explicit withheld XML/receipt identities. Hash references to omitted files do not imply those bytes are distributed. Its own recursive self-hash is intentionally excluded; the publication receipt carries the exact manifest digest.

| Withheld original identity | SHA256 |
|---|---|
| Original execution receipt | `468c941536114440c2294d7e9fd824297fdd0f9caca0bfacd40216178eaa7b6c` |
| Corrected execution receipt | `ee7cc692428a475f96bc4ae31f5026aa4d7c3ba267548b0558146a421dd5ecda` |
| Original native XML | `0ae9dc3dcc90048f0c3ef1c35289976f7e3a5bb8b54908bbd10bd08d5ff9ad44` |
| Corrected native XML | `0f96ffcdbb8ace1c7c17be454f01f9438934b8147a54a63d5b6e1905a3d225ea` |

The original receipts bind exact working-copy source and output bytes. Their personal absolute executable/workspace/output argv are not republished. This derived manifest selects safe scalar/hash fields, substitutes generic `kicad-cli` and relative run-root operands, and preserves all flags, argument order and basenames. It does not redact or replace originals, and its command arrays are explicitly named `argv_derived_relative`.

## Historical checkers and configured coverage

Both native report pairs record KiCad **10.0.6**, millimetres and included error/warning/exclusion severities. No executable binary hash was supplied; the projection records that identity as unknown. Source project bytes match and contain only meta filename/version, not a complete effective rule configuration. Of eight generated JSON configs per run, seven match exactly; only `/kicad_common.json/system/working_dir` differs. Their byte hashes are retained without raw configs or working-directory values. Defaults remain tool-version dependent; a full machine-independent effective-config freeze is not claimed.

Ignored ERC keys, unchanged: `single_global_label`, `four_way_junction`, `simulation_model_issue`, `footprint_filter`.

Ignored DRC keys, unchanged: `missing_courtyard`, `track_not_centered_on_via`, `tuning_profile_track_geometries`, `footprint_filters_mismatch`, `footprint_type_mismatch`.

Normalized imports intentionally retain `execution_status=not_observed`, `design_verdict=inconclusive`, `evidence_origin=imported_unverified`. These are importer semantics, not a denial of the separately attributed recorded native runs. Their original labels and bytes are unchanged; execution attribution comes from the retained receipts and this clearly derived projection.

**Historical producer/parser identity is not the current source identity.** Both run-era parser sets are bound by original code identity and correction preparation. Two modules evolved later:

| Module | Historical SHA256 used for retained projections | Current snapshot SHA256, not execution identity |
|---|---|---|
| `facts.py` | `367ce7ff725681fda77627a486d55a0d7a569a060d17e49cfd21f10e2fd08d03` | `26cb5ab481bf73ff3fe05855109c6d5ede9b084c37acdf327b61580156b39e73` |
| `reports.py` | `6fe36543831fa54ea1f28118944de28d5c0de8f2d9711014ab28c096029efe89` | `822e05f91fc069598557497dece145d015e023b69908d7fcd5c1d2a28fb04ac5` |

Historical `__init__.py` and `readiness.py` still match their current snapshots; all four exact run-era hashes and private operator/preparation helper identities are visible in the manifest. Historical source bytes for changed parsers and private operator helpers are not supplied by this slice. Do not attribute old normalized outputs to today's parser or claim this is a self-contained parser replay environment.

## Reproduction prerequisites and safe command interpretation

A new run needs separately authorized resources, installed KiCad10.0.6, the complete selected seven-file project/library directory copied to a new writable workspace, a new reports directory and a reviewed configuration matching the intended ignored-check policy. `${KIPRJMOD}` libraries resolve within that copied project. Preserve original bytes; do not execute withheld operator scripts or modify historical evidence. Fresh results have new receipts and cannot be substituted into this record.

The relative arrays in the manifest describe the five historical operations without private paths. For example, from a fresh working directory containing a copied `project-copy/` and empty `reports/`:

```text
kicad-cli sch erc --format json --units mm --severity-all --exit-code-violations --output reports/erc.json project-copy/carrier.kicad_sch
kicad-cli pcb drc --format json --units mm --severity-all --schematic-parity --refill-zones --exit-code-violations --output reports/drc.json project-copy/carrier.kicad_pcb
```

These are templates, not executed commands or a guarantee of identical default rules on another machine. DRC zone refill acts on a working copy. Exit5 with violation flags denotes reported rule findings; zero exit does not establish functional/electrical correctness. The withheld runtime/configuration and historical parser gaps remain explicit.

## Omission and acceptance limits

The audited scope was166files;101 are excluded. The manifest lists each repository-relative omission and grouped counts: three private operator helpers, two raw receipts,16 execution project-copy/local-state files,20 generated-config/cache files,34 nonselected logs/XML/SVG/PNG outputs,25 separate part-review research/fact/vendor files and one cross-domain bridge file. That is the reviewed selection boundary, not full environment or raw receipt closure. No private coordination, personal host paths or raw vendor documents are copied into this projection.

The accepted correction changed schematic grid placement, PCB hierarchical net names and parity-consistent blank part fields. The independent correction review preserved24 endpoint semantics, copper, pads, outline and configured rules. Matching shared UUIDs identify lineage, not independently generated boards. Only `control` and `control_corrected_v1` have the accepted actual runs. The directory named `candidate` is an earlier source reconstruction; `erc_open`, `drc_open` and `mapping_wrong` remain unexecuted curated fixtures.

MPN assignment, hole/land/finished-hole tolerances, pin-one/cable/mating geometry, electrical/current/thermal conditions, physical/3D/manufacturing checks, seeded-variant execution and product/API/job acceptance remain open. Source fixture geometry is original project authoring, not a manufacturer-validated footprint. A publication projection and zero configured findings cannot close those gates. See the [KiCad scope document](../../../project/KICAD.md) for broader context and its public-availability note; private helper/vendor/receipt references remain historical and outside this slice.
