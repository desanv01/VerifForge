# KiCad baseline fixtures

## Public availability

Selected [native fixtures](carrier/README.md), raw ERC/DRC reports and historical
normalized projections are available in this public slice; the [derived public
provenance and exact manifest](../../docs/evidence/kicad/2026-10-30/README.md)
identify every selected file and its scope.

Private control/correction/preparation helpers, original full receipts, native
XML, renders, generated runtime configs and vendor research are withheld. Any
historical helper invocation in the linked documentation records an actual prior
operation; it is not an included runnable public script. The slice is not a
self-contained execution environment. Use the projection's relative command
templates and prerequisites for separately authorized reproduction; retained
originals and exact-part/electrical/physical limits remain unchanged.

`synthetic/` contains deliberately minimal parser inputs authored for tests. They
are not KiCad-generated reports, exported netlists, reviewed electrical designs,
manufacturable boards, or evidence of real ERC/DRC execution. `Example Devices`
and `SYNTHETIC-2P` are fictional exact-part strings that test identity handling.
No datasheet, stock, package compatibility, or electrical claim is attached.

`carrier/` contains authored native projects for the approved passive 2x6 2.54mm
cable-connected PicoRV32/MMIO/Nexys Video demonstration. Control, seeded faults
and candidate are separate. The first actual control load/check/export completed
and its renders were reviewed, but exposed26 ERC and26 schematic-parity findings.
Other variants remain source-only. Exact MPN and mechanical compatibility are unresolved.
The distinct `control_corrected_v1` subsequently returned zero reported ERC/DRC
findings under unchanged configured checks (4 ERC and 5 DRC checks ignored),
with all24 exported endpoint semantics preserved. It has its own manifest/diff,
actual execution receipt and render review. This is narrow rule-repair evidence.
See [KiCad scope](../../docs/project/KICAD.md) for interfaces, pad/logical-pin distinction, evidence,
part research and the pending live acceptance gate.
