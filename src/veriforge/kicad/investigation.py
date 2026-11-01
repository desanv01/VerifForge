"""Source-only KiCad investigation projections for trusted host/API integration.

No tool launch, filesystem dereference, rendering or physical signoff. Bindings
must be supplied by trusted evidence storage, never inferred from report paths.
"""
from collections import Counter
from dataclasses import asdict, dataclass
import re

from .facts import Connectivity, MappingRequirement, check_mapping, parse_board, parse_netlist
from .reports import RuleReport, parse_report


def identity(value: str) -> str:
    if not isinstance(value, str) or not re.fullmatch(r"[0-9a-f]{64}", value):
        raise ValueError("expected SHA256 identity")
    return value


@dataclass(frozen=True)
class ReportEvidence:
    report: RuleReport
    source_sha256: str
    configuration_sha256: str
    lineage: str


def bind_report(raw: bytes, kind: str, *, source_sha256: str,
                configuration_sha256: str, lineage: str) -> ReportEvidence:
    """Import a report with host-provided bindings; does not attest execution."""
    if not isinstance(lineage, str) or not lineage.strip():
        raise ValueError("explicit report lineage required")
    return ReportEvidence(parse_report(raw, kind), identity(source_sha256),
                          identity(configuration_sha256), lineage)


def compare_configured_checks(before: ReportEvidence, candidate: ReportEvidence) -> dict:
    """Compare observations only under unchanged configuration/check scope.

    Source hashes intentionally differ for a candidate; configuration hashes must
    match. Excluded/severity transitions remain visible, including duplicates.
    """
    a, b = before.report, candidate.report
    for item in (before, candidate):
        identity(item.source_sha256)
        identity(item.configuration_sha256)
    reasons = []
    if a.kind != b.kind: reasons.append("different_check_kind")
    if a.claimed_tool_version != b.claimed_tool_version: reasons.append("different_tool_version")
    if before.configuration_sha256 != candidate.configuration_sha256: reasons.append("different_configuration")
    if set(a.ignored_checks) != set(b.ignored_checks): reasons.append("changed_ignored_checks")
    if len(set(a.ignored_checks)) != len(a.ignored_checks) or len(set(b.ignored_checks)) != len(b.ignored_checks):
        reasons.append("duplicate_ignored_check")
    if set(a.included_severities) != set(b.included_severities): reasons.append("different_severity_scope")
    if not {"warning", "error", "exclusion"} <= set(b.included_severities):
        reasons.append("incomplete_severity_scope")
    def findings(report):
        return Counter((f.signature, f.rule_id, f.severity, f.excluded) for f in report.findings)
    old, new = findings(a), findings(b)
    def rows(counts):
        return [dict(signature=k[0], rule_id=k[1], severity=k[2], excluded=k[3], count=n)
                for k, n in sorted(counts.items())]
    return {
        "status": "inconclusive" if reasons else "comparable_configured_observations",
        "reasons": reasons, "before_source_sha256": before.source_sha256,
        "candidate_source_sha256": candidate.source_sha256,
        "before_report_sha256": a.raw_sha256, "candidate_report_sha256": b.raw_sha256,
        "before_configuration_sha256": before.configuration_sha256,
        "candidate_configuration_sha256": candidate.configuration_sha256,
        "lineage": [before.lineage, candidate.lineage],
        "ignored_checks": {"before": list(a.ignored_checks), "candidate": list(b.ignored_checks)},
        "before_rule_status": a.rule_status, "candidate_rule_status": b.rule_status,
        "introduced": rows(new-old) if not reasons else [],
        "resolved": rows(old-new) if not reasons else [],
        "retained": rows(old & new) if not reasons else [],
        "execution_status": "not_observed", "design_verdict": "inconclusive",
        "repair_acceptance": "not_established",
    }


def investigate_mapping(schematic: Connectivity, board: Connectivity,
                        requirements: tuple[MappingRequirement, ...]) -> dict:
    """Batch existing endpoint checker with selected-reference coverage checks."""
    if not requirements or len(requirements) > 4096:
        raise ValueError("expected bounded reviewed requirement set")
    keys = [(r.reference, r.symbol_pin) for r in requirements]
    if len(set(keys)) != len(keys): raise ValueError("duplicate symbol-pin requirement")
    results = [check_mapping(schematic, board, r) for r in requirements]
    refs = {r.reference for r in requirements}
    required_pads = {(r.reference, r.footprint_pad) for r in requirements}
    unexpected_pins = sorted({(p.reference, p.number) for p in schematic.endpoints
                              if p.reference in refs} - set(keys))
    unexpected_pads = sorted({(p.reference, p.number) for p in board.endpoints
                              if p.reference in refs} - required_pads)
    mismatch = any(r.status == "mismatch" for r in results)
    unknown = any(r.status == "inconclusive" or r.reasons for r in results)
    return {"status": "mismatch" if mismatch or unexpected_pins or unexpected_pads else
            "inconclusive" if unknown else "consistent_with_reviewed_mapping",
            "schematic_sha256": schematic.raw_sha256, "board_sha256": board.raw_sha256,
            "rows": [dict(reference=r.reference, symbol_pin=r.symbol_pin,
                          footprint_pad=r.footprint_pad, net=r.net, **asdict(result))
                     for r, result in zip(requirements, results)],
            "unexpected_symbol_pins": unexpected_pins, "unexpected_footprint_pads": unexpected_pads,
            "lineage_limit": "CAD and generated XML may share ancestry; agreement is not independent physical validation",
            "execution_status": "not_observed", "physical_electrical_verdict": "inconclusive"}


@dataclass(frozen=True)
class ViewEvidence:
    artifact_id: str
    sha256: str
    source_sha256: str
    kind: str
    media_type: str
    lineage: str


def index_views(views: tuple[ViewEvidence, ...], *, source_sha256: str) -> dict:
    """Index inert artifact IDs; never open report paths or render untrusted SVG."""
    identity(source_sha256)
    if len(views) > 128: raise ValueError("view index bound")
    ids = set()
    rows = []
    for view in views:
        if not re.fullmatch(r"[A-Za-z0-9_-]{1,128}", view.artifact_id) or view.artifact_id in ids:
            raise ValueError("invalid or duplicate artifact ID")
        ids.add(view.artifact_id)
        identity(view.sha256); identity(view.source_sha256)
        if view.kind not in {"schematic", "pcb_copper", "pcb_silkscreen", "pcb_mask", "pcb_courtyard", "pcb_edge"}:
            raise ValueError("unsupported diagnostic view")
        if view.media_type not in {"image/svg+xml", "image/png"} or not view.lineage.strip():
            raise ValueError("unsupported media or missing lineage")
        rows.append(dict(**asdict(view), freshness="matches_source" if view.source_sha256 == source_sha256 else "stale_source",
                         visual_review="not_recorded"))
    current = {v.kind for v in views if v.source_sha256 == source_sha256}
    return {"source_sha256": source_sha256, "views": rows,
            "missing_views": sorted({"schematic", "pcb_copper", "pcb_silkscreen", "pcb_mask", "pcb_courtyard", "pcb_edge"}-current),
            "rendering": "artifact_service_required_SVG_must_not_be_inlined_unsanitized",
            "physical_electrical_verdict": "inconclusive"}


def investigate_candidate(*, schematic_xml: bytes, board_source: bytes,
                          requirements: tuple[MappingRequirement, ...],
                          before_reports: tuple[ReportEvidence, ...] = (),
                          candidate_reports: tuple[ReportEvidence, ...] = (),
                          views: tuple[ViewEvidence, ...] = ()) -> dict:
    """Connected production projection; trusted storage resolves all raw artifacts.

    Views index each source separately, so a schematic cannot appear fresh merely
    because the candidate PCB identity matches. No API/storage mutation here.
    """
    schematic, board = parse_netlist(schematic_xml), parse_board(board_source)
    checks = []
    for reports in (before_reports, candidate_reports):
        kinds = [r.report.kind for r in reports]
        if len(set(kinds)) != len(kinds): raise ValueError("duplicate report kind")
    for candidate in candidate_reports:
        expected = schematic.raw_sha256 if candidate.report.kind == "erc" else board.raw_sha256
        if candidate.source_sha256 != expected: raise ValueError("candidate report source binding mismatch")
        before = [r for r in before_reports if r.report.kind == candidate.report.kind]
        if before: checks.append(compare_configured_checks(before[0], candidate))
    # Validate IDs globally, including stale views, before splitting by source.
    index_views(views, source_sha256=board.raw_sha256)
    schematic_views=index_views(tuple(v for v in views if v.kind == "schematic"),source_sha256=schematic.raw_sha256)
    pcb_views=index_views(tuple(v for v in views if v.kind != "schematic"),source_sha256=board.raw_sha256)
    schematic_views['missing_views']=[v for v in schematic_views['missing_views'] if v == 'schematic']
    pcb_views['missing_views']=[v for v in pcb_views['missing_views'] if v != 'schematic']
    return {"domain":"kicad", "mapping":investigate_mapping(schematic,board,requirements),
            "configured_comparisons":checks,
            "missing_comparisons":sorted({"erc","drc"}-{r.report.kind for r in candidate_reports
                if any(b.report.kind == r.report.kind for b in before_reports)}),
            "schematic_views":schematic_views,
            "pcb_views":pcb_views,
            "execution_status":"not_observed", "design_verdict":"inconclusive",
            "repair_acceptance":"not_established"}
