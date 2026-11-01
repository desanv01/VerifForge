"""Path-free investigation of bounded Vivado report bytes and candidate lineage.

This is report interpretation, not execution, receipt authentication or physical
signoff. The host must authorize and resolve immutable artifacts before calling.
"""
from dataclasses import asdict, dataclass
from hashlib import sha256
import json
import math
import re
from pathlib import Path
from typing import Mapping

from .readiness import EXERCISE3_HASHES, SourceIdentity

REPORTS = ('methodology', 'check_timing', 'timing_summary', 'clocks', 'clock_interaction',
           'cdc', 'utilization', 'route_status', 'drc', 'exceptions')
SYNTH_REPORTS = ('methodology', 'check_timing', 'timing_summary', 'clocks', 'cdc', 'utilization')
MAX_BYTES = 1048576
IMPLEMENTATION_SHA256 = sha256(Path(__file__).read_bytes()).hexdigest()


def _hash(value):
    return sha256(json.dumps(value, sort_keys=True, separators=(',', ':'), allow_nan=False).encode()).hexdigest()


def _sha(value):
    if not isinstance(value, str) or not re.fullmatch('[a-f0-9]{64}', value):
        raise ValueError('SHA256 required')


@dataclass(frozen=True)
class VivadoProjectIdentity:
    part: str
    top: str
    clock_period_ns: float
    sources: tuple[SourceIdentity, ...]
    recipe_sha256: str
    functional_specification_sha256: str | None = None

    def __post_init__(self):
        if self.part != 'xc7a200tsbg484-1' or self.top != 'exercise3_nexys_video_top':
            raise ValueError('unsupported project configuration')
        if type(self.clock_period_ns) not in (int, float) or not math.isfinite(self.clock_period_ns) or self.clock_period_ns <= 0:
            raise ValueError('invalid clock requirement')
        if not isinstance(self.sources, tuple) or len(self.sources) != 3 or not all(isinstance(s, SourceIdentity) for s in self.sources) or {s.name for s in self.sources} != set(EXERCISE3_HASHES):
            raise ValueError('exact HDL/XDC source identity set required')
        for source in self.sources:
            _sha(source.sha256)
            if type(source.size_bytes) is not int or not 0 < source.size_bytes <= MAX_BYTES:
                raise ValueError('invalid source size')
        _sha(self.recipe_sha256)
        if self.functional_specification_sha256 is not None: _sha(self.functional_specification_sha256)


@dataclass(frozen=True)
class VivadoExecution:
    status: str
    exit_code: int | None
    receipt_sha256: str

    def __post_init__(self):
        if self.status not in ('completed', 'failed', 'resource_limited', 'timed_out', 'cancelled', 'cleanup_incomplete', 'not_run'):
            raise ValueError('invalid execution status')
        if self.exit_code is not None and type(self.exit_code) is not int: raise ValueError('invalid exit code')
        if self.status == 'completed' and self.exit_code != 0: raise ValueError('contradictory completion')
        _sha(self.receipt_sha256)


@dataclass(frozen=True)
class VivadoReportFact:
    name: str
    sha256: str | None
    size_bytes: int | None
    availability: str
    design_phase: str | None


@dataclass(frozen=True)
class VivadoFinding:
    code: str
    severity: str
    scope: str
    evidence_reports: tuple[str, ...]


@dataclass(frozen=True)
class VivadoInvestigation:
    schema_version: str
    investigation_sha256: str
    parent_investigation_sha256: str | None
    project: VivadoProjectIdentity
    execution: VivadoExecution
    design_phase: str
    tool_version: str | None
    reports: tuple[VivadoReportFact, ...]
    findings: tuple[VivadoFinding, ...]
    missing_input_delays: int | None
    missing_output_delays: int | None
    methodology_missing_io: int | None
    internal_timing: tuple[tuple[str, float], ...]
    fully_routed_nets: int | None
    routing_errors: int | None
    drc_reported_checks: int | None
    complete_report_set: bool
    parser_version: str = 'vivado.investigation.v1'
    parser_sha256: str = IMPLEMENTATION_SHA256
    provenance: str = 'recorded_report_interpretation_not_new_execution'
    new_execution: bool = False
    design_verdict: str = 'not_evaluated'
    functional_status: str = 'not_established'
    cdc_status: str = 'not_established'
    repair_status: str = 'not_validated'


def _count(text, pattern):
    values = re.findall(pattern, text, re.M)
    return int(values[0]) if values and len(set(values)) == 1 else None


def _timing(text):
    # Parse aggregate table only, never a later individual path slack.
    lines = text.splitlines()
    for index, line in enumerate(lines):
        if 'WNS(ns)' not in line or 'TNS(ns)' not in line: continue
        for candidate in lines[index + 1:index + 5]:
            tokens = candidate.split()
            if len(tokens) != 12 or not all(re.fullmatch(r'-?\d+(?:\.\d+)?', t) for t in tokens): continue
            values = [float(t) for t in tokens]
            if not all(math.isfinite(v) for v in values): return ()
            return tuple(zip(('wns_ns', 'tns_ns', 'whs_ns', 'ths_ns', 'wpws_ns', 'tpws_ns'),
                             (values[0], values[1], values[4], values[5], values[8], values[9])))
        return ()
    return ()


def investigate_vivado_reports(project: VivadoProjectIdentity, execution: VivadoExecution,
                               reports: Mapping[str, bytes], *, parent_investigation_sha256=None) -> VivadoInvestigation:
    """Interpret fixed report names; return only hashes, typed facts and fixed codes.

    No raw host/path/diagnostic text is returned. Hashes bind original bytes but
    do not authenticate a native execution or independent requirement authority.
    """
    if not isinstance(project, VivadoProjectIdentity) or not isinstance(execution, VivadoExecution):
        raise ValueError('typed project and execution required')
    if parent_investigation_sha256 is not None: _sha(parent_investigation_sha256)
    if set(reports) - {n + '.rpt' for n in REPORTS}: raise ValueError('unsupported report name')
    if any(type(raw) is not bytes or len(raw) > MAX_BYTES for raw in reports.values()) or sum(map(len, reports.values())) > 8*MAX_BYTES:
        raise ValueError('report byte limit')
    facts, texts, phases, versions = [], {}, set(), set()
    for name in REPORTS:
        raw = reports.get(name + '.rpt')
        if raw is None:
            facts.append(VivadoReportFact(name, None, None, 'absent', None)); continue
        text = '\n'.join(raw.decode('utf-8', errors='replace').splitlines())
        phase_values = re.findall(r'^\| Design State\s*:\s*(Synthesized|Routed|Fully Routed)\s*$', text, re.M)
        top = re.findall(r'^\| Design\s*:\s*(\S+)\s*$', text, re.M)
        device = re.findall(r'^\| Device\s*:\s*(\S+)\s*$', text, re.M)
        version = re.findall(r'^\| Tool Version\s*:\s*Vivado v\.(20\d{2}\.\d{1,2}(?:\.\d{1,2})?)\b', text, re.M)
        build = re.findall(r'^\| Tool Version[^\n]*\bBuild (\d{1,12})\b', text, re.M)
        command = re.findall(r'^\| Command\s*:\s*(\w+)(?:[ \t].*)?$', text, re.M)
        expected_command = 'check_timing' if name == 'check_timing' else 'report_' + name
        phase = 'synthesis' if phase_values == ['Synthesized'] else 'routed' if phase_values in (['Routed'], ['Fully Routed']) else None
        valid = phase is not None and top == [project.top] and device in ([project.part], ['7a200t-sbg484']) and version == ['2026.1'] and len(build) == 1 and command == [expected_command]
        context_bound = (name == 'route_status' and not any((phase_values, top, device, version, build, command))
                         and text.lstrip().startswith('Design Route Status')
                         and _count(text, r'# of fully routed nets\.+\s*:\s*(\d+)') is not None
                         and _count(text, r'# of nets with routing errors\.+\s*:\s*(\d+)') is not None)
        facts.append(VivadoReportFact(name, sha256(raw).hexdigest(), len(raw), 'parsed' if valid else 'parsed_context_bound' if context_bound else 'unrecognized_or_identity_conflict', phase))
        if valid:
            texts[name] = text; phases.add(phase); versions.add(version[0] + '+build.' + build[0])
        elif context_bound:
            texts[name] = text
    phase = next(iter(phases)) if len(phases) == 1 else 'mixed' if phases else 'unknown'
    valid_identity = len(versions) == 1 and len(phases) == 1 and all(f.availability in ('parsed', 'parsed_context_bound', 'absent') for f in facts)
    if 'clocks' in texts:
        clock = re.findall(r'^sys_clk_pin\s+(\d+(?:\.\d+)?)\s+', texts['clocks'], re.M)
        if len(clock) != 1 or float(clock[0]) != project.clock_period_ns:
            valid_identity = False
    # Never combine metrics from different design phases or tool identities.
    if not valid_identity: texts = {}
    check = texts.get('check_timing', texts.get('timing_summary', ''))
    missing_in = _count(check, r'checking no_input_delay \((\d+)\)')
    missing_out = _count(check, r'checking no_output_delay \((\d+)\)')
    methodology = _count(texts.get('methodology', ''), r'^\|\s*TIMING-18\s*\|\s*Warning\s*\|[^\r\n|]+\|\s*(\d+)\s*\|')
    metrics = _timing(texts.get('timing_summary', ''))
    fully = _count(texts.get('route_status', ''), r'# of fully routed nets\.+\s*:\s*(\d+)')
    errors = _count(texts.get('route_status', ''), r'# of nets with routing errors\.+\s*:\s*(\d+)')
    drc = _count(texts.get('drc', ''), r'^\s*Checks found:\s*(\d+)\s*$')
    findings = []
    def add(code, severity, scope, *names): findings.append(VivadoFinding(code, severity, scope, tuple(names)))
    if not valid_identity: add('REPORT_IDENTITY_OR_PHASE_UNRESOLVED', 'warning', 'evidence', *tuple(texts))
    if missing_in: add('MISSING_INPUT_TIMING_REQUIREMENTS', 'warning', 'external_io', 'check_timing')
    if missing_out: add('MISSING_OUTPUT_TIMING_REQUIREMENTS', 'warning', 'external_io', 'check_timing')
    if methodology: add('METHODOLOGY_TIMING18', 'warning', 'external_io', 'methodology')
    if missing_in is None or missing_out is None: add('IO_TIMING_COVERAGE_UNKNOWN', 'warning', 'external_io', 'check_timing')
    if metrics and any(value < 0 for _, value in metrics): add('INTERNAL_TIMING_VIOLATION_REPORTED', 'warning', phase, 'timing_summary')
    if not metrics: add('INTERNAL_TIMING_UNAVAILABLE', 'warning', phase, 'timing_summary')
    if errors: add('ROUTING_ERRORS_REPORTED', 'warning', 'routing', 'route_status')
    if drc: add('DRC_FINDINGS_REPORTED', 'warning', 'configured_drc', 'drc')
    add('CDC_IO_COVERAGE_NOT_PROVEN', 'limitation', 'async_ingress', 'cdc', 'check_timing')
    known = all(s.sha256 == EXERCISE3_HASHES[s.name] for s in project.sources)
    add('RAW_ASYNC_INPUTS_UNVALIDATED' if known else 'CANDIDATE_INPUT_TOPOLOGY_UNREVIEWED', 'limitation', 'async_ingress')
    add('RESET_LATENCY_AND_WORD_COHERENCE_UNSPECIFIED', 'limitation', 'board_contract')
    if project.functional_specification_sha256 is None: add('INDEPENDENT_FUNCTIONAL_SPECIFICATION_MISSING', 'limitation', 'functional')
    else: add('FUNCTIONAL_SPECIFICATION_IDENTITY_NOT_EXECUTED', 'limitation', 'functional')
    required = REPORTS if phase == 'routed' else SYNTH_REPORTS
    complete = valid_identity and all(n in texts for n in required)
    if not complete: add('REPORT_SET_INCOMPLETE', 'warning', 'evidence')
    if execution.status != 'completed': add('EXECUTION_NOT_COMPLETED', 'warning', 'infrastructure')
    record = VivadoInvestigation('vivado.investigation.v1', '', parent_investigation_sha256, project, execution,
        phase, next(iter(versions)) if len(versions) == 1 else None, tuple(facts), tuple(findings),
        missing_in, missing_out, methodology, metrics, fully, errors, drc, complete)
    return VivadoInvestigation(**{**record.__dict__, 'investigation_sha256': _hash(asdict(record))})


@dataclass(frozen=True)
class VivadoCandidateComparison:
    schema_version: str
    comparison_sha256: str
    baseline_sha256: str
    candidate_sha256: str
    status: str
    unchanged_requirements: bool
    changed_sources: tuple[str, ...]
    required_rechecks: tuple[str, ...]
    blockers: tuple[str, ...]
    external_validation_required: tuple[str, ...] = ('independent_functional_check', 'async_ingress_reset_contract', 'external_io_requirements')
    repair_status: str = 'not_validated'
    functional_status: str = 'not_established'


def compare_vivado_candidate(before: VivadoInvestigation, after: VivadoInvestigation) -> VivadoCandidateComparison:
    """Compare report-scoped observations, rejecting lost lineage/requirements.

    Timing improvement is meaningful only within the same reported design phase.
    No report comparison is a functional repair validation.
    """
    for record in (before, after):
        data = asdict(record); claimed = data['investigation_sha256']; data['investigation_sha256'] = ''
        if _hash(data) != claimed: raise ValueError('investigation identity changed')
    a, b = before.project, after.project
    old = {s.name: s.sha256 for s in a.sources}; new = {s.name: s.sha256 for s in b.sources}
    changed = tuple(sorted(name for name in old if old[name] != new[name]))
    unchanged = (a.part, a.top, a.clock_period_ns, old['nexys_video_exercise3.xdc'], a.functional_specification_sha256) == (b.part, b.top, b.clock_period_ns, new['nexys_video_exercise3.xdc'], b.functional_specification_sha256)
    blockers = []
    if after.parent_investigation_sha256 != before.investigation_sha256: blockers.append('candidate_parent_identity_missing_or_changed')
    if not unchanged: blockers.append('requirements_changed')
    if before.design_phase != after.design_phase: blockers.append('design_phase_changed')
    if not before.tool_version or before.tool_version != after.tool_version: blockers.append('tool_identity_changed_or_unknown')
    if before.parser_sha256 != after.parser_sha256: blockers.append('interpretation_identity_changed')
    if not before.complete_report_set or not after.complete_report_set: blockers.append('report_set_incomplete')
    if before.execution.status != 'completed' or after.execution.status != 'completed': blockers.append('execution_not_completed')
    required = REPORTS if after.design_phase == 'routed' else SYNTH_REPORTS
    old_metrics, new_metrics = dict(before.internal_timing), dict(after.internal_timing)
    if not old_metrics or not new_metrics: blockers.append('timing_metrics_missing')
    for key in ('missing_input_delays', 'missing_output_delays', 'methodology_missing_io'):
        left, right = getattr(before, key), getattr(after, key)
        if left is None or right is None or right > left: blockers.append('coverage_missing_or_regressed:' + key)
    if after.design_phase == 'routed' and (after.routing_errors != 0 or after.drc_reported_checks != 0): blockers.append('route_or_drc_not_clear')
    improved = bool(old_metrics) and any(v < 0 for v in old_metrics.values()) and bool(new_metrics) and all(v >= 0 for v in new_metrics.values())
    status = 'not_comparable' if blockers else 'reported_internal_timing_improvement' if improved else 'reported_checks_compared_no_repair_demonstrated'
    record = VivadoCandidateComparison('vivado.candidate-comparison.v1', '', before.investigation_sha256,
        after.investigation_sha256, status, unchanged, changed, tuple(required), tuple(blockers))
    return VivadoCandidateComparison(**{**record.__dict__, 'comparison_sha256': _hash(asdict(record))})


def vivado_projection(record: VivadoInvestigation, comparison: VivadoCandidateComparison | None = None) -> dict:
    """JSON-safe payload for a host-authorized, version-scoped engineering API.

    This serializes data only. It grants no artifact access or candidate action.
    """
    data = asdict(record); claimed = data['investigation_sha256']; data['investigation_sha256'] = ''
    if _hash(data) != claimed: raise ValueError('investigation identity changed')
    data['investigation_sha256'] = claimed
    data['internal_timing'] = dict(record.internal_timing)
    candidate = None if comparison is None else asdict(comparison)
    if candidate is not None:
        digest = candidate['comparison_sha256']; candidate['comparison_sha256'] = ''
        if _hash(candidate) != digest or comparison.candidate_sha256 != claimed:
            raise ValueError('candidate comparison identity or lineage changed')
        candidate['comparison_sha256'] = digest
    return {'schema_version': 'vivado.projection.v1', 'investigation': data, 'candidate_comparison': candidate}
