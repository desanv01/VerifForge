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


def _literal_command(raw):
    """Only a whole-line comment or a comment after a literal command separator.

    Remaining command text must still full-match the supported literal grammar;
    this is not a general Tcl lexer or evaluator.
    """
    text = raw.strip()
    if text.startswith("#"):
        return ""
    command, marker, _ = text.partition("#")
    if marker and not command.rstrip().endswith(";"):
        raise ValueError("active XDC comment requires a command separator")
    return command.strip()


def _reject_active_continuations(data):
    # Tcl joins backslash-newline before interpreting comments. Never treat the
    # next physical line as an independently active command in this subset.
    if b"\\\n" in data or b"\\\r" in data:
        raise ValueError("active XDC line continuations are unsupported")


def extract_package_pins(data: bytes, ports: tuple[str, ...], *, board_reference=False):
    if type(board_reference) is not bool or not 1 <= len(ports) <= 16 or len(set(ports)) != len(ports):
        raise ValueError("explicit unique bounded port selection required")
    if not isinstance(data, bytes) or len(data) > 1_048_576:
        raise ValueError("XDC must be bounded bytes")
    if any(re.fullmatch(PORT, port) is None for port in ports):
        raise ValueError("literal port selection required")
    if not board_reference:
        _reject_active_continuations(data)
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
        # Explicit commented-reference mode reads a template, not active Tcl.
        text = text.split("#", 1)[0].strip() if template else _literal_command(text)
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


def extract_clock_periods(data: bytes, ports: tuple[str, ...]):
    """Active literal create_clock only; integer picoseconds, never Tcl execution.

    Caller must bind returned records to the exact source hash and reviewed
    manifest. Generated clocks, variables and waveform overrides are unsupported.
    """
    from decimal import Decimal
    if type(data) is not bytes or len(data) > 1_048_576 or not 1 <= len(ports) <= 16 or len(set(ports)) != len(ports):
        raise ValueError("bounded unique clock selection required")
    if any(re.fullmatch(PORT, p) is None for p in ports):
        raise ValueError("literal clock port required")
    _reject_active_continuations(data)
    pattern = re.compile(r"create_clock\s+(?:-name\s+[A-Za-z_][A-Za-z0-9_]*\s+)?-period\s+([0-9]{1,9}(?:\.[0-9]{1,3})?)\s+\[get_ports\s+\{(" + PORT + r")\}\]\s*;?")
    rows = []
    for line, raw in enumerate(data.decode("utf-8-sig").splitlines(), 1):
        text = _literal_command(raw)
        if not text: continue
        match = pattern.fullmatch(text)
        if match:
            if match[2] in ports:
                period = int(Decimal(match[1]) * 1000)
                if period <= 0 or any(r["port"] == match[2] for r in rows):
                    raise ValueError("ambiguous or nonpositive clock period")
                rows.append(dict(kind="clock_period", port=match[2], period_ps=period, line=line))
        elif PATTERN.fullmatch(text) is None:
            raise ValueError("active XDC outside literal clock/pin subset")
    return tuple(rows)
