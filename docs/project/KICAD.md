# KiCad baseline — U7

## Public availability

The public slice contains selected native fixtures, raw ERC/DRC reports and
historical normalized projections. Start with the [derived public provenance
and exact selection](../evidence/kicad/2026-10-30/README.md). It links the retained
reports and distinguishes actual prior execution from source-only fixtures.

Private control/correction/preparation helpers, original full receipts, native
XML, renders, generated runtime configs and vendor research are withheld.
References and command transcripts below describe the historical operator
environment; they are not included runnable public scripts or a self-contained
reproduction workflow. Use the projection's safe relative command templates and
explicit prerequisites for any separately authorized new run. Originals remain
unchanged; no physical/electrical or exact-part acceptance follows.

Status: **import-only source support**. Installed Windows KiCad CLI reports
**10.0.6**, at `C:/Program Files/KiCad/10.0/bin/kicad-cli.exe`; it is absent from
the observed PATH. Version and ERC/DRC help were actually executed on 2026-10-29.
They emitted HKCU registry-access warnings in the sandbox. The first bounded
control gate subsequently executed successfully as tool operations, but exposed
fixture-rule violations; see the recorded live result below. Product/API support
remains import-only, with a finite local audit helper rather than a live job adapter.
The separate `control_corrected_v1` candidate subsequently completed the same
checks with zero reported ERC/DRC findings under unchanged configured checks
(4 ERC and 5 DRC checks ignored); this is narrow rule-repair evidence, not
electrical correctness or exact-part acceptance.

## Supported interfaces

Public functions are exported by `veriforge.kicad`. No new dependencies or shared
contracts are introduced. Shared provenance integration belongs to main.

| Function | Input and output | Boundary |
|---|---|---|
| `inventory()` | PATH and fixed Windows KiCad installation candidates | Does not run a tool or infer a version |
| `command_argv(Check, executable, source, output)` | Fixed ERC/DRC/XML export arguments for trusted host paths | Prepares only; never executes; no arbitrary model flags |
| `parse_report(raw_bytes, kind)` | KiCad 10 JSON ERC/DRC; frozen normalized findings | Claimed tool/source metadata, unverified imported origin |
| `import_report(raw_bytes, kind, output_root)` | Unique bundle of unchanged original bytes and normalized JSON | Preserves malformed inputs with parse-error receipt; does not open report paths |
| `parse_netlist(raw_bytes)` | KiCad XML export D/E, components, exact-part fields, pin/net endpoints | No DTD/entities; UTF-8 only; missing fields are not invented |
| `parse_board(raw_bytes)` | Board format `20241229`, footprint properties and pad net assignments | Not copper continuity, routing, layer, mechanical, or electrical signoff |
| `check_mapping(schematic, board, requirement)` | Explicit symbol-pin/pad/net, manufacturer, MPN, footprint, reviewed provenance | Selected mapping consistency only; missing facts are inconclusive |

Reports are bounded to 8 MiB. JSON duplicate keys, malformed required structures,
unsupported schema/version/severity, malformed item coordinates and oversized
inputs fail. Findings retain category/JSON-pointer location, item UUIDs, rule ID,
exclusion state, deterministic signature and raw SHA-256. Included severity and
ignored-check metadata are retained where supplied. Missing filter metadata does
not establish complete coverage. Original bytes retain all other metadata.

`execution_status=not_observed`, `design_verdict=inconclusive` and
`evidence_origin=imported_unverified` apply to every imported rule report,
including an empty report. `rule_status` only describes active findings present
in that input. An excluded finding is retained but not treated as active. The
parser is a supported-shape importer, not full JSON Schema validation.

Connectivity facts include the exact source hash and XPath-like or S-expression
location. Component MPN/Manufacturer/Datasheet fields remain
`unreviewed_source_claim`. A generic connector's pin number is never automatically
equated with its physical pad number. Duplicate board pads with conflicting nets
are inconclusive. The mapping checker requires a trusted host-selected review
record, nonempty reviewer/location and SHA-256 identities for datasheet and intent;
the controller must authenticate these records. A populated string is not proof
that a human reviewed it. A consistent result covers only the selected mapping.

## Curated carrier

Main selected a passive, cable-connected 2x6 through-hole 2.54 mm carrier. The
owned generated projects are under `targets/kicad/carrier/`:

- `control`: two nominal connectors, 24 pads, pass-through net assignments.
- `erc_open`: deliberately omitted J1 pin-1 wire/label.
- `drc_open`: deliberately omitted J1/J2 pad-1 middle copper segment.
- `mapping_wrong`: deliberately renamed JA1 to WRONG_GPIO in both schematic and
  PCB. This tests intent independently of schematic/board agreement.
- `candidate`: separate reconstruction of intended control connectivity.

The control has now loaded/exported in actual KiCad and received a rendered review,
but is **not rule-clean**. The other variants remain source-only; predicted seeded
fault categories are not real results.
`build_fixture.py` generates deterministically into new directories and refuses
to overwrite existing ones. It is a trusted source-authoring aid; it must never
be accepted as uploaded executable input. The symbol and footprint are original
nominal geometry with blank Manufacturer/MPN/Datasheet fields. They are not
manufacturer-validated parts. Exact-part mapping acceptance is therefore
inconclusive, as intended until that prerequisite is resolved.

Carrier pad intent, for both J1 and J2:

| Pad/symbol pin | Logical Pmod connection | Nexys Video FPGA ball (reference only) |
|---|---|---|
| 1 | JA1 | AB22 |
| 2 | JA7 | Y21 |
| 3 | JA2 | AB21 |
| 4 | JA8 | AA21 |
| 5 | JA3 | AB20 |
| 6 | JA9 | AA20 |
| 7 | JA4 | AB18 |
| 8 | JA10 | AA18 |
| 9, 10 | Pmod ground pins 5, 11 | No FPGA ball equivalence |
| 11, 12 | Pmod supply pins 6, 12 | No FPGA ball equivalence |

This table is drafted carrier intent, **not a verified cable pinout**. Carrier
odd/even pad numbering differs from logical Pmod 1–6/7–12 row numbering. Orientation,
pin-one marking, cable mapping, actual connector part and footprint suitability
need independent review. No direct mating, physical board, current rating,
thermal, voltage-safety or fabrication-readiness claim is made.

Independent board-reference source: Digilent Nexys Video rev. A reference manual,
revised 2020-09-01, section 10, pp. 19–20, tables 9–10. It identifies JA as a
3.3 V digital port with 200 ohm series protection and lists the above signal
balls. It was checked independently of the Vivado worker's Master XDC. These
are manufacturer reference facts, not observations of a connected physical board.

Part research found Samtec `TSW-106-07-G-D`, a 12-pin, double-row, 2.54 mm through-hole
candidate on the manufacturer page, cross-referenced by pcbparts JLC MPN lookup
as C3337759. It has **not** been assigned to the fixture: exact pad orientation,
hole/land geometry and datasheet identity review remain open. Do not treat the
nominal fixture as a validated Samtec footprint or copy supplier stock into a
fixed availability claim.

## Validation and next gate

### Corrected candidate result — 29 October 2026, 22:50 MYT

`targets/kicad/carrier/control_corrected_v1/` is a distinct candidate. It changes
three files: schematic coordinates, PCB hierarchical net names plus blank
Manufacturer/MPN/Datasheet fields, and matching blank local-footprint fields.
`targets/kicad/corrections/U7-CONTROL-CORRECTION/manifest.json` binds parent evidence,
before/after hashes, checker hashes and `candidate.patch`. Rules, embedded symbol
and pin identities, UUIDs, copper geometry, pads and outline are unchanged.

After main granted a new slot, `python targets/kicad/check_correction.py` executed
the same five commands once on a private candidate copy:

| Stage | Exit | Observed result |
|---|---|---|
| ERC | 0 | 0 reported findings, 4 ignored checks; versus26 original off-grid warnings |
| DRC including parity | 0 | 0 reported findings, 5 ignored checks; versus26 original parity findings |
| XML netlist | 0 | 24 endpoints, all pin/net/function/type semantics unchanged |
| Schematic SVG | 0 | Full and detail views reviewed; labels readable |
| PCB SVG | 0 | Full and detail views reviewed; detail pixels identical to original geometry |

Receipt: `targets/kicad/evidence/control-corrected-v1-20261029T145057431340Z/receipt.json`.
Elapsed subprocess sum7.406s, all within30s/stage150s/total. Original control,
first-run evidence and candidate source hashes remain unchanged during execution.
`review/comparison.json` records the24-endpoint comparison, actual rule counts,
render hashes and scope. Artifact directory remains below10MiB. Nonfatal registry/
font warnings persist. The inherited title still says control; folder, manifest
and receipt distinguish the candidate. Whitespace remains large in full-sheet views.

Focused tests now **48 passed** (43 baseline plus5 correction invariants), including
grid coordinates, unchanged rules/UUIDs/pins/copper/pads and unresolved exact-part
identity. Seeded variants have not yet executed. The completed correction proves
removal of these observed grid/parity findings under unchanged configured checks.
It does not complete the exact-part, cable, mechanical or electrical gates.

### U8 bridge and part review

`corrections/U7-CONTROL-CORRECTION/bridge-facts.json` under `targets/kicad` contains
12 explicit mapping rows for both J1/J2, with source hashes and native locations.
Carrier symbol-pin/pad/net facts are independently extracted from actual KiCad
XML and the candidate PCB. Host references come from the official Digilent XDC,
SHA256 `2debf0afb537c0a9f8f2c1569aa99e0366629a354f2fbdf369bd5142670086cc`, lines109–116.
The host-to-carrier relation is proposed cable intent and needs explicit review;
matching names alone do not authenticate a cross-domain relationship. The earlier
mapping table applies with `/` prefixed to actual KiCad net names. The carrier has
no oscillator; host R4/10ns clock facts are separate reference information.

The official manual was read through the web tool, but its direct binary download
returned a site challenge. Its binary hash is therefore null, explicitly labeled;
no HTML/challenge bytes are called a PDF identity. The Master XDC was fetched and
hashed successfully. Raw vendor documents remain local research, excluded from
source publication by a directory-local ignore file.

`targets/kicad/part-review/facts.json` records visual source review of Samtec
footprint revisionA page1 and series revisionDS pages1,2,6. Position/row pitch is
2.54mm; recommended hole diameter1.02mm differs from the fixture's1.00mm. No fit
equivalence is inferred. Manufacturer pin-one orientation to the carrier/cable,
land dimensions, tolerance/plating interpretation and operating conditions remain
unresolved. The reviewed candidate MPN is still unassigned; all schematic/PCB
manufacturer fields remain blank.

The subsequent `U7-EXACT-PART-REVIEW` is recorded in
`targets/kicad/part-review/EXACT_PART_REVIEW.md` and `exact-review-v2.json`, with
five manufacturer PDF identities and page-specific facts. Exact ordering,
pitch and straight-post dimensions are supported. The numbered double-row
diagram was reviewed, but physical carrier/cable orientation remains unbound.
The catalog's 1.02 +/-0.03 mm hole recommendation belongs to optional `-LL`,
which this MPN does not contain; it does not validate the existing 1.00 mm hole.
Copper lands and finished-hole/tool-drill interpretation remain unresolved.
The product specification's 4.7 A and catalog's 5.7 A SSW-mated ratings remain
source-specific and unreconciled; neither is an accepted carrier current limit.
No exact-part project was created or MPN assigned. Existing projects/evidence
were preserved, and no further native KiCad operation was run. The review
includes finite prerequisites for a separately assigned geometry/mating check.

### First actual control result — 29 October 2026, 22:36 MYT

Main granted the serial slot, and `python targets/kicad/check_control.py` ran once
on a private copy. All five subprocesses completed within their budgets. Receipt:
`targets/kicad/evidence/control-20261029T143637914406Z/receipt.json`.

| Stage | Exit | Observed result |
|---|---|---|
| ERC | 5 | 26 `endpoint_off_grid` warnings |
| DRC with parity | 5 | 24 `net_conflict` and 2 `footprint_symbol_field_mismatch` findings, all in schematic parity; no geometric/unconnected findings reported |
| XML netlist | 0 | 24 pin endpoints; importer accepted actual export |
| Schematic SVG | 0 | Full sheet and connector detail reviewed |
| PCB SVG | 0 | Combined F.Cu/B.Cu/F.SilkS/Edge.Cuts view and detail reviewed |

Actual reports identify KiCad **10.0.6** and both parsers accepted the supported
shape. Total subprocess elapsed time was 7.936 seconds. All original source and
library hashes remained unchanged. Raw logs retain nonfatal HKCU and Fontconfig /
SHGetFolderPathA warnings; successful rendering does not establish full font or
desktop environment readiness. Review notes and normalized facts are under
`review/` beside the receipt. Original receipt and tool outputs are preserved.

The schematic has readable connector labels and pin numbers, visibly unresolved
MPN values, and excessive full-sheet whitespace. The PCB view shows the outline,
connectors and front/back tracks; no 3D or exact-part interference review occurred.
Root causes identified from actual reports: 70/140 mm schematic placement is off
the 1.27 mm grid; local labels export `/JA1` etc. while PCB nets are `JA1` etc.;
PCB footprints omit the schematic Manufacturer field. A subsequent candidate
should snap placements, preserve hierarchical net names, and keep unresolved
fields parity-consistent. No corrections or retries were run during this slot.

The engineering subprocess slot was released to main after completion. Seeded
cases and exact-part acceptance remained pending at this first-run boundary;
the later corrected-control result is recorded above. These control findings are real fixture issues, not
the seeded case results and not evidence of a KiCad software defect.

Pure tests use synthetic reports/netlists/board fragments, clearly labeled in
`targets/kicad/synthetic/`. Fictional manufacturer/MPN values exist only there.
They test parser failure semantics, lineage, exclusions, seeded intent conflict,
missing exact identity, ambiguous pads and fixed command construction.

Read-only native-file parser probe succeeded on the installed KiCad
`TI-LaunchPad-BoosterPack-40pin` template: 3 footprints and 40 pads; board SHA-256
`293b6fc539c8fe24afbbe7d59e062b65bb9233eb5c8708f8ca54bb8233bf6930`.
This is a file parse, not a KiCad execution or review of that board's correctness.

From the repository root, using the existing environment:

```powershell
$env:PYTHONPATH='src'
$env:TEMP=(Resolve-Path '.uv-cache/tmp').Path
$env:TMP=$env:TEMP
& ./.venv/Scripts/python.exe -m pytest tests/kicad -q --tb=short
```

The scheduled resource owner must grant a new KiCad slot before further live work.
The completed control gate used serial ERC, DRC, netlist, schematic SVG and PCB SVG; each at most
30 seconds, total 150 seconds, 10 MiB artifacts, no `--save-board`. Capture exact
argv, exit code, duration, stdout/stderr, tool version, source/project/library
hashes and unchanged-original hashes. Status 5 with `--exit-code-violations`
means reported rule violations; a timeout is infrastructure/budget evidence.
Review renders, then run seeded variants and candidate with unchanged checker
and intent. No live runner/API/job acceptance is provided in this baseline.

## Primary references

- [KiCad 10 CLI](https://docs.kicad.org/10.0/en/cli/cli.html)
- [ERC report schema](https://gitlab.com/kicad/code/kicad/-/raw/10.0/resources/schemas/erc.v1.json)
- [DRC report schema](https://gitlab.com/kicad/code/kicad/-/raw/10.0/resources/schemas/drc.v1.json)
- [KiCad board format](https://dev-docs.kicad.org/en/file-formats/sexpr-pcb/)
- [Schematic netlist documentation](https://docs.kicad.org/10.0/en/eeschema/eeschema.html)
- [Nexys Video reference manual](https://digilent.com/reference/_media/reference/programmable-logic/nexys-video/nexys-video_rm.pdf)
- [Digilent Master XDC](https://github.com/Digilent/digilent-xdc/blob/master/Nexys-Video-Master.xdc)
- [Samtec candidate part](https://www.samtec.com/products/tsw-106-07-g-d)
- [Samtec footprint drawing](https://suddendocs.samtec.com/prints/tsw-xxx-xx-x-x-xx-xxx-footprint.pdf)
