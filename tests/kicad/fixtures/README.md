# Public carrier netlist fixture

`carrier-control.netlist.xml` derives from the retained KiCad export of the
public `targets/kicad/carrier/control_corrected_v1` reference carrier. Only the
top-level design source path was replaced with `carrier.kicad_sch` to remove the
operator's local path. Components, symbol pins, footprint names and nets remain
unchanged. The original export remains preserved separately.

This fixture supports portable parser and projection tests. Its bytes differ
from the original export, so tests bind their own hashes and do not claim a new
native run, original execution identity, exact-part or physical acceptance.
