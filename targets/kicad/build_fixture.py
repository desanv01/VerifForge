"""Generate owned, curated carrier fixtures. Does not run KiCad or edit originals.

Nominal 2x6 2.54mm geometry only; exact connector MPN and mechanical suitability
are unresolved. This script is a trusted source authoring aid, not a job input.
"""

import json
from pathlib import Path
from uuid import NAMESPACE_URL, uuid5

ROOT = Path(__file__).resolve().parent / "carrier"
SYMBOL = "CarrierHeader2x6"
LIB = "VerifForge"
FP = "Header2x6_2.54_Unreviewed"
PINS = ["JA1", "JA7", "JA2", "JA8", "JA3", "JA9", "JA4", "JA10", "GND", "GND", "VCC3V3", "VCC3V3"]
FONT = '(effects (font (size 1.27 1.27)))'


def uid(key):
    return str(uuid5(NAMESPACE_URL, "veriforge/kicad/carrier/" + key))


def prop(key, value, x=0, y=0, hide=False):
    return f'(property "{key}" "{value}" (at {x} {y} 0) (effects (font (size 1.27 1.27)) {"(hide yes)" if hide else ""}))'


def library_symbol(name):
    pins = []
    for n in range(1, 13):
        x, angle = (-7.62, 0) if n % 2 else (7.62, 180)
        y = -((n - 1) // 2) * 2.54
        pins.append(f'(pin passive line (at {x} {y} {angle}) (length 5.08) (name "Pin_{n}" {FONT}) (number "{n}" {FONT}))')
    return f'''(symbol "{name}" (pin_names (offset 0.508) (hide yes))
      (in_bom yes) (on_board yes)
      {prop("Reference", "J", 0, 3.81)} {prop("Value", SYMBOL, 0, -16.51)}
      {prop("Footprint", LIB + ":" + FP, hide=True)} {prop("Datasheet", "", hide=True)}
      (symbol "{SYMBOL}_0_1" (rectangle (start -2.54 1.27) (end 2.54 -13.97)
        (stroke (width 0.254) (type default)) (fill (type background))))
      (symbol "{SYMBOL}_1_1" {" ".join(pins)}))'''


def footprint(reference, x, y, names, board=False):
    props = f'''(property "Reference" "{reference}" (at 1.27 -3 0) (layer "F.SilkS") {FONT})
        (property "Value" "MPN_UNRESOLVED" (at 1.27 16 0) (layer "F.Fab") {FONT})'''
    parts = []
    codes = {name: i + 1 for i, name in enumerate(dict.fromkeys(names))}
    for n, name in enumerate(names, 1):
        net = f'(net {codes[name]} "{name}")' if board else ""
        parts.append(f'''(pad "{n}" thru_hole {"rect" if n == 1 else "circle"}
          (at {0 if n % 2 else 2.54} {((n - 1) // 2) * 2.54})
          (size 1.7 1.7) (drill 1) (layers "*.Cu" "*.Mask") {net})''')
    return f'''(footprint "{LIB + ':' + FP if board else FP}" (layer "F.Cu")
      {f'(at {x} {y})' if board else ''}
      {f'(path "/{uid("root")}/{uid(reference)}")' if board else ''}
      (attr through_hole) {props}
      (fp_rect (start -1.4 -1.4) (end 3.94 14.1) (stroke (width 0.12) (type default)) (fill none) (layer "F.SilkS"))
      (fp_rect (start -1.65 -1.65) (end 4.19 14.35) (stroke (width 0.05) (type default)) (fill none) (layer "F.CrtYd"))
      {" ".join(parts)})'''


def generate(case):
    directory = ROOT / case
    directory.mkdir(parents=True, exist_ok=False)
    names = ["WRONG_GPIO" if case == "mapping_wrong" and n == "JA1" else n for n in PINS]
    symbols, connections = [], []
    for ref, x in (("J1", 70), ("J2", 140)):
        y = 70
        symbols.append(f'''(symbol (lib_id "{LIB}:{SYMBOL}") (at {x} {y} 0) (unit 1)
          (in_bom yes) (on_board yes) (dnp no) (uuid "{uid(ref)}")
          {prop("Reference", ref, x, y - 5.08)}
          {prop("Value", "MPN_UNRESOLVED", x, y + 17.78)}
          {prop("Footprint", LIB + ":" + FP, x, y, True)}
          {prop("Datasheet", "", x, y, True)}
          {prop("Manufacturer", "", x, y, True)} {prop("MPN", "", x, y, True)}
          {" ".join(f'(pin "{n}" (uuid "{uid(ref + str(n))}"))' for n in range(1, 13))}
          (instances (project "carrier" (path "/{uid('root')}" (reference "{ref}") (unit 1)))))''')
        for n, name in enumerate(names, 1):
            if case == "erc_open" and ref == "J1" and n == 1:
                continue  # deliberately unconnected passive pin; genuine ERC gate pending
            sign = -1 if n % 2 else 1
            px, lx, py = x + sign * 7.62, x + sign * 12.7, y + ((n - 1) // 2) * 2.54
            connections.append(f'''(wire (pts (xy {px} {py}) (xy {lx} {py}))
              (stroke (width 0) (type default)) (uuid "{uid(ref + str(n) + 'wire')}"))
              (label "{name}" (at {lx} {py} 0) (effects (font (size 1.27 1.27)) (justify {"right" if sign < 0 else "left"} bottom))
                (uuid "{uid(ref + str(n) + 'label')}"))''')
    schematic = f'''(kicad_sch (version 20250114) (generator "veriforge")
      (uuid "{uid('root')}") (paper "A4")
      (title_block (title "VerifForge passive cable carrier - {case}") (rev "U7-source-v1")
        (comment 1 "Curated demo; exact MPN and mechanical compatibility UNREVIEWED"))
      (lib_symbols {library_symbol(LIB + ':' + SYMBOL)})
      {" ".join(connections)} {" ".join(symbols)})'''
    codes = {name: i + 1 for i, name in enumerate(dict.fromkeys(names))}
    segments = []
    for n, name in enumerate(names, 1):
        dx, y = (0 if n % 2 else 2.54), 40 + ((n - 1) // 2) * 2.54
        off = -1.27 if n % 2 else 1.27
        layer = "B.Cu" if n % 2 else "F.Cu"
        points = [(40 + dx, y), (40 + dx, y + off), (65 + dx, y + off), (65 + dx, y)]
        for j, (start, end) in enumerate(zip(points, points[1:])):
            if case == "drc_open" and n == 1 and j == 1:
                continue  # seeded missing copper segment, never called an organic tool defect
            segments.append(f'(segment (start {start[0]} {start[1]}) (end {end[0]} {end[1]}) (width 0.2) (layer "{layer}") (net {codes[name]}) (uuid "{uid(str(n) + str(j) + "segment")}"))')
    for n in (9, 11):
        y = 40 + ((n - 1) // 2) * 2.54
        segments.append(f'(segment (start 40 {y}) (end 42.54 {y}) (width 0.2) (layer "F.Cu") (net {codes[names[n - 1]]}) (uuid "{uid(str(n) + "join")}"))')
    board = f'''(kicad_pcb (version 20241229) (generator "veriforge")
      (general (thickness 1.6)) (paper "A4")
      (layers (0 "F.Cu" signal) (2 "B.Cu" signal) (5 "F.SilkS" user) (1 "F.Mask" user)
        (3 "B.Mask" user) (25 "Edge.Cuts" user) (31 "F.CrtYd" user) (35 "F.Fab" user))
      (setup (pad_to_mask_clearance 0))
      (net 0 "") {" ".join(f'(net {code} "{name}")' for name, code in codes.items())}
      {footprint('J1', 40, 40, names, True)} {footprint('J2', 65, 40, names, True)}
      (gr_rect (start 35 34) (end 73 60) (stroke (width 0.05) (type default)) (fill none) (layer "Edge.Cuts") (uuid "{uid('outline')}"))
      {" ".join(segments)})'''
    (directory / "carrier.kicad_sch").write_text(schematic + "\n", encoding="utf-8")
    (directory / "carrier.kicad_pcb").write_text(board + "\n", encoding="utf-8")
    (directory / "carrier.kicad_pro").write_text(json.dumps({"meta": {"filename": "carrier.kicad_pro", "version": 1}}) + "\n")
    (directory / "VerifForge.kicad_sym").write_text(f'(kicad_symbol_lib (version 20241209) (generator "veriforge") {library_symbol(SYMBOL)})\n', encoding="utf-8")
    (directory / "VerifForge.pretty").mkdir()
    (directory / "VerifForge.pretty" / (FP + ".kicad_mod")).write_text(footprint("REF**", 0, 0, names) + "\n", encoding="utf-8")
    (directory / "sym-lib-table").write_text('(sym_lib_table (version 7) (lib (name "VerifForge") (type "KiCad") (uri "${KIPRJMOD}/VerifForge.kicad_sym") (options "") (descr "Owned unreviewed carrier symbol")))\n')
    (directory / "fp-lib-table").write_text('(fp_lib_table (version 7) (lib (name "VerifForge") (type "KiCad") (uri "${KIPRJMOD}/VerifForge.pretty") (options "") (descr "Owned nominal carrier footprint, no exact MPN claim")))\n')


if __name__ == "__main__":
    for case in ("control", "erc_open", "drc_open", "mapping_wrong", "candidate"):
        generate(case)
