"""Bounded KiCad 10 JSON ERC/DRC import. Imported findings are observations."""

from dataclasses import asdict, dataclass
from hashlib import sha256
import json
import math
from pathlib import Path
import re
from uuid import uuid4

MAX_BYTES = 8 * 1024 * 1024
PARSER_VERSION = "kicad-report-v1"


class ImportFailure(ValueError):
    """Malformed, oversized, or unsupported evidence; never a clean result."""


def text(value: object, label: str) -> str:
    if not isinstance(value, str) or not value.strip() or len(value) > 16384:
        raise ImportFailure(f"invalid {label}")
    return value


def bounded(raw: bytes) -> None:
    if not isinstance(raw, bytes) or not raw or len(raw) > MAX_BYTES:
        raise ImportFailure("input must be nonempty bytes within 8 MiB")


def _object(value: object) -> dict:
    if not isinstance(value, dict):
        raise ImportFailure("expected object")
    return value


def _array(value: object) -> list:
    if not isinstance(value, list):
        raise ImportFailure("expected array")
    return value


def _unique_object(pairs: list) -> dict:
    result = {}
    for key, value in pairs:
        if key in result:
            raise ImportFailure("duplicate JSON key")
        result[key] = value
    return result


@dataclass(frozen=True)
class RuleFinding:
    rule_id: str
    severity: str
    excluded: bool
    description: str
    location: str
    item_uuids: tuple[str, ...]
    signature: str


@dataclass(frozen=True)
class RuleReport:
    kind: str
    raw_sha256: str
    claimed_tool_version: str
    claimed_source: str
    coordinate_units: str | None
    included_severities: tuple[str, ...]
    ignored_checks: tuple[str, ...]
    findings: tuple[RuleFinding, ...]
    rule_status: str
    parser_version: str = PARSER_VERSION
    execution_status: str = "not_observed"
    design_verdict: str = "inconclusive"
    evidence_origin: str = "imported_unverified"


def parse_report(raw: bytes, kind: str) -> RuleReport:
    bounded(raw)
    if kind not in ("erc", "drc"):
        raise ImportFailure("expected erc or drc")
    try:
        obj = _object(json.loads(raw, object_pairs_hook=_unique_object,
                                 parse_constant=lambda _: (_ for _ in ()).throw(
                                     ImportFailure("non-finite JSON value"))))
        schema = obj.get("$schema")
        if schema is not None and schema != f"https://schemas.kicad.org/{kind}.v1.json":
            raise ImportFailure("unsupported report schema")
        version = text(obj["kicad_version"], "tool version")
        if not re.fullmatch(r"10\.\d+\.\d+", version):
            raise ImportFailure("only KiCad 10 report shape supported")
        source = text(obj["source"], "source")
        text(obj["date"], "date")
        units = obj.get("coordinate_units")
        if units not in ("mm", "in", "mils"):
            raise ImportFailure("missing or unsupported coordinate units")
        severities = tuple(_array(obj.get("included_severities", [])))
        if any(s not in ("warning", "error", "exclusion") for s in severities):
            raise ImportFailure("unsupported included severity")
        ignored_list = []
        for check in _array(obj.get("ignored_checks", [])):
            check = _object(check)
            text(check["description"], "ignored check description")
            ignored_list.append(text(check["key"], "ignored check"))
        ignored = tuple(ignored_list)
        groups = []
        if kind == "erc":
            sheets = _array(obj["sheets"])
            if not sheets:
                raise ImportFailure("ERC report has no sheets")
            for i, sheet in enumerate(sheets):
                sheet = _object(sheet)
                text(sheet["path"], "sheet path")
                text(sheet["uuid_path"], "sheet UUID path")
                groups.append((f"/sheets/{i}/violations", _array(sheet["violations"])))
        else:
            groups = [(f"/{key}", _array(obj[key])) for key in (
                "violations", "unconnected_items", "schematic_parity")]
        findings = []
        for path, entries in groups:
            for i, entry in enumerate(entries):
                entry = _object(entry)
                rule = text(entry["type"], "rule id")
                description = text(entry["description"], "description")
                severity = entry["severity"]
                if severity not in ("error", "warning"):
                    raise ImportFailure("unsupported finding severity")
                excluded = entry.get("excluded", False)
                if type(excluded) is not bool:
                    raise ImportFailure("excluded must be boolean")
                item_ids = []
                for item in _array(entry["items"]):
                    item = _object(item)
                    item_uuid = text(item["uuid"], "item UUID")
                    if not re.fullmatch(r"[0-9a-fA-F]{8}(?:-[0-9a-fA-F]{4}){3}-[0-9a-fA-F]{12}", item_uuid):
                        raise ImportFailure("invalid item UUID")
                    item_ids.append(item_uuid)
                    text(item["description"], "item description")
                    pos = _object(item["pos"])
                    for axis in ("x", "y"):
                        if type(pos[axis]) not in (int, float) or not math.isfinite(pos[axis]):
                            raise ImportFailure("invalid item coordinate")
                ids = tuple(item_ids)
                signature = sha256(json.dumps([kind, rule, sorted(ids), description],
                                              ensure_ascii=True).encode()).hexdigest()
                findings.append(RuleFinding(rule, severity, excluded, description,
                                            f"{path}/{i}", ids, signature))
        active = any(not f.excluded and f.severity in ("error", "warning") for f in findings)
        return RuleReport(kind, sha256(raw).hexdigest(), version, source, units,
                          severities, ignored, tuple(findings),
                          "reported_violations" if active else "no_active_violations_reported")
    except (KeyError, TypeError, UnicodeError, json.JSONDecodeError, RecursionError, OverflowError) as exc:
        raise ImportFailure("malformed report") from exc


def import_report(raw: bytes, kind: str, output_root: Path) -> tuple[Path, RuleReport]:
    """Trusted host path only. Unique bundle, exact bytes, never overwrite originals.

    Persist raw evidence before parsing so malformed reports remain inspectable.
    Paths inside a report are inert text and are never opened.
    """
    bounded(raw)
    if kind not in ("erc", "drc"):
        raise ImportFailure("expected erc or drc")
    directory = Path(output_root) / ("kicad-import-" + uuid4().hex)
    directory.mkdir(parents=True, exist_ok=False)
    (directory / "original.json").write_bytes(raw)
    try:
        report = parse_report(raw, kind)
    except ImportFailure:
        (directory / "parse-error.json").write_text(json.dumps({
            "parser_version": PARSER_VERSION, "raw_sha256": sha256(raw).hexdigest(),
            "execution_status": "not_observed", "design_verdict": "inconclusive",
            "parse_status": "malformed_or_unsupported",
        }), encoding="utf-8")
        raise
    (directory / "normalized.json").write_text(
        json.dumps(asdict(report), indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return directory, report
