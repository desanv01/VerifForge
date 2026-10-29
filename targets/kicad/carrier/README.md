# Passive carrier source fixture

## Public availability

The selected native projects and local libraries are available here. Selected
raw ERC/DRC reports and historical normalized projections are listed and linked
in the [derived public provenance](../../../docs/evidence/kicad/2026-10-30/README.md).
Original full receipts, native XML, renders, runtime configs, vendor research
and private control/correction/preparation helpers are withheld. Evidence-folder
references below therefore identify retained history, not complete public run
bundles; historical helper commands are not shipped runnable scripts.

The projection supplies safe relative templates and prerequisites for a
separately authorized new run. Public source availability does not establish a
self-contained runtime, manufacturer-approved footprint or physical/electrical
acceptance. No original evidence was redacted or rewritten for publication.

Main approved a cable-connected 2x6 through-hole, 2.54 mm carrier for the bounded
Nexys Video / PicoRV32 MMIO demonstration on 2026-10-29. Sources are original
VerifForge nominal geometry, not imported vendor designs. No part MPN is assigned.
All geometry and pin/cable orientation require review before manufacture or use.

Native versions: schematic 20250114, PCB 20241229, local symbol library 20241209.
Each project has self-contained symbol/footprint libraries and project library
tables. No external library download or environment variable overrides are needed.

`control` is intended pass-through connectivity. `erc_open` omits J1 pin 1's
schematic connection. `drc_open` removes pad 1's middle track. `mapping_wrong`
renames JA1 to WRONG_GPIO consistently in both sources; rule checks alone should
not establish intended mapping. `candidate` is a separate correct-intent copy.
These are curated seeded cases, not naturally discovered defects.

The initial source stage is not a live execution claim. No physical board exists
as part of this delivery. Do not energize or manufacture based on this fixture.
Its pending exact-part gate is a real limitation, not a placeholder pass.

Control update: an actual KiCad10.0.6 readiness run completed on a private copy,
with26 off-grid ERC warnings and26 schematic-parity DRC findings. Netlist/SVG
exports succeeded and renders were reviewed. Originals remain unchanged. The
control is not rule-clean; other variants have not executed. Evidence is in
`../evidence/control-20261029T143637914406Z/` relative to this carrier directory.

The separate `control_corrected_v1` fixes grid, hierarchical net names and blank
part-field parity, with no rule relaxation. Actual KiCad10.0.6 ERC/DRC both return
zero reported findings under unchanged configured checks (4 ERC and 5 DRC checks
ignored). Its withheld original receipt is retained under `../evidence/control-corrected-v1-20261029T145057431340Z/`.
The original control is preserved; exact-part/cable/electrical acceptance stays open.

See [KiCad scope](../../../docs/project/KICAD.md) for explicit pad/Pmod numbering and evidence boundaries.
