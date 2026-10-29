"""Extract source facts; explicit reviewed requirements establish mapping intent.

Board pad net assignments are not routed copper connectivity. Missing exact part
or datasheet evidence remains unresolved, never inferred from a generic symbol.
"""

from dataclasses import dataclass
from hashlib import sha256
import json
import re
from xml.etree import ElementTree as ET

from .reports import ImportFailure, bounded, text


@dataclass(frozen=True)
class SourceFact:
    source_sha256: str
    location: str


@dataclass(frozen=True)
class Component:
    reference: str
    value: str | None
    symbol: str | None
    footprint: str | None
    manufacturer: str | None
    mpn: str | None
    datasheet: str | None
    source: SourceFact
    review_status: str = "unreviewed_source_claim"


@dataclass(frozen=True)
class Endpoint:
    reference: str
    number: str
    net: str | None
    function: str | None
    electrical_type: str | None
    source: SourceFact


@dataclass(frozen=True)
class Connectivity:
    kind: str
    raw_sha256: str
    components: tuple[Component, ...]
    endpoints: tuple[Endpoint, ...]
    parser_version: str = "kicad-connectivity-v1"


def parse_netlist(raw: bytes) -> Connectivity:
    bounded(raw)
    # Decode first so UTF-16 cannot evade the declaration check.
    try:
        xml = raw.decode("utf-8-sig")
        if "<!DOCTYPE" in xml.upper() or "<!ENTITY" in xml.upper() or "\x00" in xml:
            raise ImportFailure("DTD/entities and non-UTF8 XML are unsupported")
        root = ET.fromstring(xml)
        if root.tag != "export" or root.get("version") not in ("D", "E"):
            raise ImportFailure("unsupported KiCad XML netlist")
        if len(root.findall("components")) != 1 or len(root.findall("nets")) != 1:
            raise ImportFailure("missing or duplicate components/nets")
        digest = sha256(raw).hexdigest()
        components = []
        refs = set()
        for i, comp in enumerate(root.findall("components/comp")):
            ref = text(comp.get("ref"), "reference")
            if ref in refs:
                raise ImportFailure("duplicate component reference")
            refs.add(ref)
            for singleton in ("value", "footprint", "datasheet", "libsource", "fields"):
                if len(comp.findall(singleton)) > 1:
                    raise ImportFailure(f"duplicate component {singleton}")
            fields = {}
            for field in comp.findall("fields/field"):
                key = text(field.get("name"), "field name")
                if key in fields:
                    raise ImportFailure("duplicate component field")
                fields[key] = field.text
            lib = comp.find("libsource")
            symbol = None if lib is None else f"{lib.get('lib', '')}:{lib.get('part', '')}"
            components.append(Component(ref, comp.findtext("value"), symbol,
                comp.findtext("footprint"), fields.get("Manufacturer"), fields.get("MPN"),
                comp.findtext("datasheet"), SourceFact(digest, f"/export/components/comp[{i + 1}]")))
        endpoints = []
        pins, codes, names = set(), set(), set()
        for i, net in enumerate(root.findall("nets/net")):
            code = text(net.get("code"), "net code")
            name = text(net.get("name"), "net name")
            if code in codes or name in names:
                raise ImportFailure("duplicate net identity")
            codes.add(code)
            names.add(name)
            for j, node in enumerate(net.findall("node")):
                ref, pin = text(node.get("ref"), "reference"), text(node.get("pin"), "pin")
                if ref not in refs or (ref, pin) in pins:
                    raise ImportFailure("unknown component or multiply assigned pin")
                pins.add((ref, pin))
                endpoints.append(Endpoint(ref, pin, name, node.get("pinfunction"), node.get("pintype"),
                    SourceFact(digest, f"/export/nets/net[{i + 1}]/node[{j + 1}]")))
        if not components or not endpoints:
            raise ImportFailure("empty connectivity is unsupported")
        return Connectivity("schematic_netlist", digest, tuple(components), tuple(endpoints))
    except (ET.ParseError, UnicodeError, RecursionError) as exc:
        raise ImportFailure("malformed netlist") from exc


def _sexpr(raw: bytes) -> list:
    bounded(raw)
    try:
        data = raw.decode("utf-8-sig")
        stack, roots = [], []
        token_re = re.compile(r'\s+|\(|\)|"(?:\\.|[^"\\])*"|[^\s()"]+')
        end = 0
        for count, match in enumerate(token_re.finditer(data)):
            if count > 500_000 or match.start() != end:
                raise ImportFailure("invalid or oversized board token stream")
            end = match.end()
            token = match.group()
            if token.isspace():
                continue
            if token == "(":
                node = []
                (stack[-1] if stack else roots).append(node)
                stack.append(node)
                if len(stack) > 64:
                    raise ImportFailure("board nesting limit")
            elif token == ")":
                if not stack:
                    raise ImportFailure("unbalanced board")
                stack.pop()
            else:
                if not stack:
                    raise ImportFailure("unexpected board token")
                stack[-1].append(json.loads(token) if token.startswith('"') else token)
        if end != len(data) or stack or len(roots) != 1:
            raise ImportFailure("unbalanced board")
        return roots[0]
    except (UnicodeError, json.JSONDecodeError) as exc:
        raise ImportFailure("unsupported board string encoding") from exc


def _children(node: list, tag: str) -> list:
    return [x for x in node if isinstance(x, list) and x and x[0] == tag]


def _one(node: list, tag: str, required: bool = True) -> list | None:
    matches = _children(node, tag)
    if len(matches) > 1 or (required and not matches):
        raise ImportFailure(f"missing or repeated {tag}")
    return matches[0] if matches else None


def parse_board(raw: bytes) -> Connectivity:
    root = _sexpr(raw)
    if not root or root[0] != "kicad_pcb":
        raise ImportFailure("expected KiCad PCB")
    try:
        version = _one(root, "version")[1]
        # Tested subset: KiCad 9 native board structure, read by installed KiCad 10.
        if version != "20241229":
            raise ImportFailure("unsupported board format version")
        digest = sha256(raw).hexdigest()
        nets = {}
        for entry in _children(root, "net"):
            if len(entry) != 3 or entry[1] in nets or not isinstance(entry[2], str):
                raise ImportFailure("invalid or duplicate board net")
            nets[text(entry[1], "net code")] = entry[2]
        components, endpoints, refs = [], [], set()
        for i, footprint in enumerate(_children(root, "footprint")):
            props = {}
            for prop in _children(footprint, "property"):
                key, value = text(prop[1], "property"), prop[2]
                if key in props or not isinstance(value, str):
                    raise ImportFailure("duplicate or malformed footprint property")
                props[key] = value
            ref = text(props.get("Reference"), "reference")
            if ref in refs:
                raise ImportFailure("duplicate board reference")
            refs.add(ref)
            location = f"/kicad_pcb/footprint[{i + 1}]"
            components.append(Component(ref, props.get("Value"), None,
                text(footprint[1], "footprint"), props.get("Manufacturer"), props.get("MPN"),
                props.get("Datasheet"), SourceFact(digest, location)))
            for j, pad in enumerate(_children(footprint, "pad")):
                if pad[1] == "":  # mechanical unnumbered pad
                    continue
                number = text(pad[1], "pad number")
                net = _one(pad, "net", required=False)
                name = None
                if net is not None:
                    if len(net) != 3 or net[1] not in nets or nets[net[1]] != net[2]:
                        raise ImportFailure("pad net disagrees with board net table")
                    name = net[2] or None
                endpoints.append(Endpoint(ref, number, name, None, None,
                    SourceFact(digest, f"{location}/pad[{j + 1}]")))
        if not components or not endpoints:
            raise ImportFailure("empty board connectivity is unsupported")
        return Connectivity("board_pad_assignments", digest, tuple(components), tuple(endpoints))
    except (IndexError, TypeError) as exc:
        raise ImportFailure("malformed board facts") from exc


@dataclass(frozen=True)
class MappingRequirement:
    reference: str
    symbol_pin: str
    footprint_pad: str
    net: str
    manufacturer: str
    mpn: str
    footprint: str
    datasheet_sha256: str
    datasheet_location: str
    reviewed_by: str
    intent_sha256: str


@dataclass(frozen=True)
class MappingResult:
    status: str
    reasons: tuple[str, ...]
    sources: tuple[SourceFact, ...]
    requirement_sha256: str
    scope: str = "selected_pin_pad_net_and_exact_part_identity_only"


def check_mapping(schematic: Connectivity, board: Connectivity,
                  requirement: MappingRequirement) -> MappingResult:
    """Requirement must come from trusted reviewed storage, never the planner.

    Checks one explicitly mapped endpoint. Does not infer pin==pad, resolve aliases,
    assess voltage/thermal/routing, or certify datasheet review authenticity.
    A consistent status is limited to the selected pin/pad/net/footprint mapping
    and available explicit identity claims. Missing optional board identity is
    reported in reasons, not treated as a matched exact part or electrical verdict.
    """
    from dataclasses import asdict

    if schematic.kind != "schematic_netlist" or board.kind != "board_pad_assignments":
        raise ValueError("expected independently extracted schematic and board facts")
    identity = sha256(json.dumps(asdict(requirement), sort_keys=True).encode()).hexdigest()
    values = asdict(requirement)
    if any(not isinstance(v, str) or not v.strip() for v in values.values()) or any(
        not re.fullmatch(r"[0-9a-f]{64}", values[k])
        for k in ("datasheet_sha256", "intent_sha256")
    ):
        return MappingResult("inconclusive", ("missing_reviewed_exact_part_or_intent",), (), identity)
    r = requirement
    pins = [p for p in schematic.endpoints if (p.reference, p.number) == (r.reference, r.symbol_pin)]
    pads = [p for p in board.endpoints if (p.reference, p.number) == (r.reference, r.footprint_pad)]
    parts = [c for c in schematic.components if c.reference == r.reference]
    footprints = [c for c in board.components if c.reference == r.reference]
    sources = tuple(x.source for x in (*pins, *pads, *parts, *footprints))
    if len(pins) != 1 or not pads or len(parts) != 1 or len(footprints) != 1:
        return MappingResult("inconclusive", ("missing_or_ambiguous_endpoint",), sources, identity)
    if len({p.net for p in pads}) != 1:
        return MappingResult("inconclusive", ("duplicate_pad_conflicting_nets",), sources, identity)
    if not parts[0].manufacturer or not parts[0].mpn:
        return MappingResult("inconclusive", ("missing_exact_part_identity",), sources, identity)
    reasons = []
    missing_board_identity = []
    if (parts[0].manufacturer, parts[0].mpn) != (r.manufacturer, r.mpn):
        reasons.append("exact_part_mismatch")
    for field in ("manufacturer", "mpn"):
        actual = getattr(footprints[0], field)
        if not actual or not actual.strip():
            missing_board_identity.append(f"board_{field}_not_reported")
        elif actual != getattr(r, field):
            reasons.append(f"board_{field}_mismatch")
    if any(c.footprint != r.footprint for c in (*parts, *footprints)):
        reasons.append("footprint_mismatch")
    if pins[0].net != r.net:
        reasons.append("symbol_pin_net_mismatch")
    if pads[0].net != r.net:
        reasons.append("footprint_pad_net_mismatch")
    return MappingResult("mismatch" if reasons else "consistent_with_reviewed_mapping",
                         tuple(reasons or missing_board_identity), sources, identity)
