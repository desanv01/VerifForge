"""Literal observation subset only: no Tcl evaluator, substitution or imports."""
import re

from .models import PinObservation

VERSION = "literal-xdc-package-pin-v1"
PORT = r"[A-Za-z_][A-Za-z0-9_]*(?:\[[0-9]{1,3}\])?"
PIN = r"[A-Z]{1,3}[1-9][0-9]{0,2}"
PATTERN = re.compile(
    r"set_property\s+(?:-dict\s+\{\s*PACKAGE_PIN\s+(?P<dict_pin>" + PIN +
    r")\s*(?:IOSTANDARD\s+[A-Z0-9_]+\s*)?\}|PACKAGE_PIN\s+(?P<pin>" + PIN +
    r"))\s+\[get_ports\s+\{\s*(?P<port>" + PORT + r")\s*\}\]\s*;?\s*")


def extract_package_pins(data: bytes, ports: tuple[str, ...], *, board_reference=False):
    if type(board_reference) is not bool or not 1 <= len(ports) <= 16 or len(set(ports)) != len(ports):
        raise ValueError("explicit unique bounded port selection required")
    if not isinstance(data, bytes) or len(data) > 1_048_576:
        raise ValueError("XDC must be bounded bytes")
    if any(re.fullmatch(PORT, port) is None for port in ports):
        raise ValueError("literal port selection required")
    rows = []
    for line, raw in enumerate(data.decode("utf-8-sig").splitlines(), 1):
        text = raw.strip()
        template = text.startswith("#")
        if board_reference and not template:
            # Reference mode interprets commented manufacturer pin templates only.
            # Active global configuration cannot change a comment's literal text.
            continue
        if template:
            if not board_reference:
                continue
            text = text[1:].strip()
        text = text.split("#", 1)[0].strip()
        matched = PATTERN.fullmatch(text)
        if matched:
            if matched["port"] in ports:
                rows.append(PinObservation(port=matched["port"], package_pin=matched["dict_pin"] or matched["pin"],
                    line=line, interpretation="commented_board_reference" if template else "active_literal"))
        elif not template and text:
            # A project script may redefine ports/properties through arbitrary Tcl.
            # Even an unrelated active command makes this subset nonauthoritative.
            raise ValueError("active XDC outside literal package-pin subset")
        elif template and "PACKAGE_PIN" in text and any("{" + p + "}" in text.replace(" ", "") for p in ports):
            raise ValueError("unsupported selected board-reference pin declaration")
        if len(rows) > 64:
            raise ValueError("pin declaration limit exceeded")
    return tuple(rows)
