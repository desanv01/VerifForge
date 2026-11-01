import { InvestigationError } from './engineeringInvestigationApi'
import type { VersionSummary } from './engineeringInvestigationApi'

function invalid(): never { throw new InvestigationError(0, 'Investigation projection is invalid or belongs to another immutable version. No projection evidence was interpreted.') }
export function object(v: unknown): Record<string, unknown> { if (!v || typeof v !== 'object' || Array.isArray(v)) invalid(); return v as Record<string, unknown> }
export function text(v: unknown, max = 256): string { if (typeof v !== 'string' || !v.length || v.length > max || /[\u0000-\u0008\u000b\u000c\u000e-\u001f]/.test(v)) invalid(); return v }
export function hash(v: unknown): string { const s = text(v, 64); if (!/^[a-f0-9]{64}$/.test(s)) invalid(); return s }
export function integer(v: unknown, max = Number.MAX_SAFE_INTEGER): number { if (typeof v !== 'number' || !Number.isSafeInteger(v) || v < 0 || v > max) invalid(); return v }
export function finite(v: unknown): number { if (typeof v !== 'number' || !Number.isFinite(v)) invalid(); return v }
export function bool(v: unknown): boolean { if (typeof v !== 'boolean') invalid(); return v }
export function enumeration<T extends string>(v: unknown, values: readonly T[]): T { if (!values.includes(v as T)) invalid(); return v as T }
export function array<T>(v: unknown, parse: (v: unknown) => T, max = 256): T[] { if (!Array.isArray(v) || v.length > max) invalid(); return v.map(parse) }
export function nullable<T>(v: unknown, parse: (v: unknown) => T): T | null { return v === null ? null : parse(v) }
const strings = (v: unknown) => array(v, v => text(v), 256)

export function parseVivado(raw: unknown) {
  const o = object(raw), project = object(o.project), execution = object(o.execution)
  enumeration(o.schema_version, ['vivado.investigation.v1'])
  enumeration(o.provenance, ['recorded_report_interpretation_not_new_execution']); if (o.new_execution !== false) invalid()
  if (execution.status === 'completed' && execution.exit_code !== 0) invalid()
  const result = {
    domain: 'vivado' as const, identity: hash(o.investigation_sha256), parent: nullable(o.parent_investigation_sha256, hash),
    phase: enumeration(o.design_phase, ['synthesis', 'routed', 'mixed', 'unknown']), tool: nullable(o.tool_version, v => text(v, 64)),
    project: { part: text(project.part), top: text(project.top), clockPeriod: finite(project.clock_period_ns), recipe: hash(project.recipe_sha256), specification: nullable(project.functional_specification_sha256, hash), sources: array(project.sources, v => { const s = object(v); return { name: text(s.name), sha256: hash(s.sha256) } }, 32) },
    execution: { status: enumeration(execution.status, ['completed', 'failed', 'resource_limited', 'timed_out', 'cancelled', 'cleanup_incomplete', 'not_run']), receipt: hash(execution.receipt_sha256) },
    reports: array(o.reports, v => { const r = object(v); return { name: enumeration(r.name, ['methodology', 'check_timing', 'timing_summary', 'clocks', 'clock_interaction', 'cdc', 'utilization', 'route_status', 'drc', 'exceptions']), sha256: nullable(r.sha256, hash), bytes: nullable(r.size_bytes, integer), availability: enumeration(r.availability, ['parsed', 'parsed_context_bound', 'absent', 'unrecognized_or_identity_conflict']), phase: nullable(r.design_phase, v => enumeration(v, ['synthesis', 'routed'])) } }, 10),
    findings: array(o.findings, v => { const f = object(v); return { code: text(f.code, 128), severity: enumeration(f.severity, ['warning', 'limitation']), scope: text(f.scope, 64), reports: strings(f.evidence_reports) } }, 64),
    missingInput: nullable(o.missing_input_delays, integer), missingOutput: nullable(o.missing_output_delays, integer), methodologyMissingIo: nullable(o.methodology_missing_io, integer),
    timing: array(Array.isArray(o.internal_timing) ? o.internal_timing : Object.entries(object(o.internal_timing)), v => { if (!Array.isArray(v) || v.length !== 2) invalid(); return { name: enumeration(v[0], ['wns_ns', 'tns_ns', 'whs_ns', 'ths_ns', 'wpws_ns', 'tpws_ns']), value: finite(v[1]) } }, 6),
    routed: nullable(o.fully_routed_nets, integer), routingErrors: nullable(o.routing_errors, integer), drcChecks: nullable(o.drc_reported_checks, integer), complete: bool(o.complete_report_set),
    verdict: enumeration(o.design_verdict, ['not_evaluated']), functional: enumeration(o.functional_status, ['not_established']), cdc: enumeration(o.cdc_status, ['not_established']), repair: enumeration(o.repair_status, ['not_validated']),
  }
  if (new Set(result.reports.map(r => r.name)).size !== result.reports.length || new Set(result.timing.map(r => r.name)).size !== result.timing.length) invalid()
  if (result.reports.some(r => (r.availability === 'absent') !== (r.sha256 === null && r.bytes === null))) invalid()
  if (['mixed', 'unknown'].includes(result.phase) && result.timing.length) invalid()
  return result
}

function views(raw: unknown) {
  const o = object(raw)
  const result = { source: hash(o.source_sha256), missing: strings(o.missing_views), entries: array(o.views, v => {
    const r = object(v)
    return { id: text(r.artifact_id, 128), sha256: hash(r.sha256), source: hash(r.source_sha256), kind: enumeration(r.kind, ['schematic', 'pcb_copper', 'pcb_silkscreen', 'pcb_mask', 'pcb_courtyard', 'pcb_edge']), media: enumeration(r.media_type, ['image/svg+xml', 'image/png']), lineage: text(r.lineage), freshness: enumeration(r.freshness, ['matches_source', 'stale_source']), review: enumeration(r.visual_review, ['not_recorded']) }
  }, 128) }
  if (new Set(result.entries.map(e => e.id)).size !== result.entries.length || result.entries.some(e => (e.freshness === 'matches_source') !== (e.source === result.source))) invalid()
  return result
}
function parseVivadoProjection(raw: unknown) {
  const o = object(raw), wrapped = o.schema_version === 'vivado.projection.v1'
  const investigation = parseVivado(wrapped ? o.investigation : raw)
  const candidate = wrapped ? nullable(o.candidate_comparison, v => {
    const c = object(v); enumeration(c.schema_version, ['vivado.candidate-comparison.v1']); enumeration(c.repair_status, ['not_validated']); enumeration(c.functional_status, ['not_established'])
    if (hash(c.candidate_sha256) !== investigation.identity) invalid()
    return { identity: hash(c.comparison_sha256), baseline: hash(c.baseline_sha256), candidate: hash(c.candidate_sha256), status: enumeration(c.status, ['not_comparable', 'reported_internal_timing_improvement', 'reported_checks_compared_no_repair_demonstrated']), unchanged: bool(c.unchanged_requirements), changed: strings(c.changed_sources), rechecks: strings(c.required_rechecks), blockers: strings(c.blockers), external: strings(c.external_validation_required) }
  }) : null
  return { ...investigation, candidate }
}
export function parseKicad(raw: unknown) {
  const o = object(raw), mapping = object(o.mapping)
  enumeration(o.domain, ['kicad']); enumeration(o.execution_status, ['not_observed'])
  const findings = (v: unknown) => array(v, raw => { const f = object(raw); return { signature: text(f.signature, 128), rule: text(f.rule_id), severity: enumeration(f.severity, ['error', 'warning', 'exclusion']), excluded: bool(f.excluded), count: integer(f.count) } }, 4096)
  const comparisons=array(o.configured_comparisons,v=>{
    const c=object(v),ignored=object(c.ignored_checks)
    enumeration(c.execution_status,['not_observed']);enumeration(c.design_verdict,['inconclusive']);enumeration(c.repair_acceptance,['not_established'])
    const result={status:enumeration(c.status,['inconclusive','comparable_configured_observations']),reasons:strings(c.reasons),beforeSource:hash(c.before_source_sha256),candidateSource:hash(c.candidate_source_sha256),beforeReport:hash(c.before_report_sha256),candidateReport:hash(c.candidate_report_sha256),beforeConfiguration:hash(c.before_configuration_sha256),candidateConfiguration:hash(c.candidate_configuration_sha256),ignoredBefore:strings(ignored.before),ignoredAfter:strings(ignored.candidate),beforeStatus:text(c.before_rule_status),candidateStatus:text(c.candidate_rule_status),introduced:findings(c.introduced),resolved:findings(c.resolved),retained:findings(c.retained)}
    if(result.status==='comparable_configured_observations'&&(result.reasons.length||result.beforeConfiguration!==result.candidateConfiguration||[...result.ignoredBefore].sort().join()!==[...result.ignoredAfter].sort().join()))invalid()
    return result
  },2)
  if(Object.hasOwn(mapping,'gap')) {
    enumeration(mapping.gap,['reviewed_mapping_assertions_missing']);enumeration(mapping.status,['inconclusive']);enumeration(o.physical_electrical_verdict,['inconclusive'])
    if(Object.keys(mapping).some(k=>!['gap','status'].includes(k))||Object.keys(o).some(k=>!['domain','mapping','configured_reports','configured_comparisons','execution_status','physical_electrical_verdict'].includes(k)))invalid()
    const reports=array(o.configured_reports,v=>{const r=object(v);return {kind:enumeration(r.kind,['erc','drc']),sha256:hash(r.sha256),status:text(r.rule_status),ignored:strings(r.ignored_checks)}},2)
    if(new Set(reports.map(r=>r.kind)).size!==reports.length)invalid()
    return {domain:'kicad' as const,partial:true as const,gap:'reviewed_mapping_assertions_missing' as const,reports,comparisons}
  }
  enumeration(o.design_verdict, ['inconclusive']); enumeration(o.repair_acceptance, ['not_established'])
  const result = {
    domain: 'kicad' as const, partial:false as const, status: enumeration(mapping.status, ['mismatch', 'inconclusive', 'consistent_with_reviewed_mapping']), schematic: hash(mapping.schematic_sha256), board: hash(mapping.board_sha256),
    rows: array(mapping.rows, v => { const r = object(v); return { reference: text(r.reference, 96), pin: text(r.symbol_pin, 96), pad: text(r.footprint_pad, 96), net: text(r.net, 96), status: enumeration(r.status, ['mismatch', 'inconclusive', 'consistent_with_reviewed_mapping']), reasons: strings(r.reasons), requirement: hash(r.requirement_sha256), sources: array(r.sources, v => { const s = object(v); return { sha256: hash(s.source_sha256) } }, 256) } }, 4096),
    unexpectedPins: array(mapping.unexpected_symbol_pins, v => array(v, v => text(v, 96), 2), 4096), unexpectedPads: array(mapping.unexpected_footprint_pads, v => array(v, v => text(v, 96), 2), 4096),
    missingComparisons: array(o.missing_comparisons, v => enumeration(v, ['erc', 'drc']), 2),
    comparisons, schematicViews: views(o.schematic_views), pcbViews: views(o.pcb_views),
  }
  if (result.comparisons.some(c => c.status === 'comparable_configured_observations' && (c.reasons.length || c.beforeConfiguration !== c.candidateConfiguration || [...c.ignoredBefore].sort().join() !== [...c.ignoredAfter].sort().join()))) invalid()
  return result
}

export function reference(v: unknown) {
  const r = object(v)
  return { artifactId: text(r.artifact_id, 128), revisionId: text(r.revision_id, 128), attemptId: nullable(r.attempt_id, v => text(v, 128)), sha256: hash(r.sha256) }
}
const u32 = (v: unknown) => integer(v, 0xffffffff)
const hex = (v: unknown) => `0x${u32(v).toString(16).padStart(8, '0')}`
function commonCase(raw: unknown, domain: 'riscv' | 'firmware') {
  const o = object(raw), execution = object(o.execution), identity = object(o.identity)
  enumeration(o.domain, [domain]); enumeration(o.schema_version, [`${domain}.investigation.v1`]); enumeration(execution.new_execution, ['not_run'])
  return { label: text(o.case_label, 96), status: enumeration(o.projection_status, ['partial', 'complete']),
    execution: { producer: enumeration(execution.producer_status, ['completed', 'bounded_incomplete', 'failed', 'incomplete']), verdict: enumeration(execution.design_verdict, ['pass_within_scope', 'design_mismatch', 'inconclusive', 'timeout', 'unsupported', 'invalid_evidence']), termination: text(execution.termination, 96), checker: text(execution.checker_status, 96) },
    identity: { source: hash(identity.source_hash), checker: hash(identity.checker_hash), contract: hash(identity.contract_hash), experiment: hash(identity.experiment_hash), environment: hash(identity.environment_hash) },
    evidence: array(o.evidence, v => { const e = object(v); return { role: text(e.role, 96), reference: reference(e.reference), status: enumeration(e.status, ['available', 'missing']), bytes: nullable(e.size_bytes, integer) } }, 64),
    gaps: array(o.gaps, v => { const g = object(v); return { code: text(g.code, 96), role: text(g.role, 96) } }, 64),
  }
}
function retirement(raw: unknown) {
  const r = object(raw)
  enumeration(r.schema_version, ['riscv.retirement.v1'])
  return { order: integer(r.order, 10000), pc: u32(r.pc), instruction: u32(r.instruction), rd: integer(r.rd, 31), value: u32(r.rd_value), cycle: nullable(r.source_cycle, integer), trap: bool(r.trap), interrupt: bool(r.interrupt) }
}
function divergenceValue(raw: unknown, field: string): string {
  if (['trap', 'interrupt'].includes(field)) return String(bool(raw))
  if (field !== 'memory') return hex(raw)
  if (raw === null) return 'No memory effect'
  const m = object(raw)
  const width = integer(m.width, 4); if (![1, 2, 4].includes(width)) invalid()
  return `${enumeration(m.kind, ['read', 'write'])} ${hex(m.address)} · ${width} bytes · mask ${integer(m.byte_mask, 15)} · ${hex(m.value)}`
}
function parseRiscvCase(raw: unknown) {
  const o = object(raw), common = commonCase(raw, 'riscv'), rows = object(o.retirements), regs = object(o.registers), lineage = object(o.lineage), assumptions = object(o.assumptions)
  const first = nullable(o.first_divergence, v => { const f = object(v), field = enumeration(f.field, ['pc', 'instruction', 'rd', 'rd_value', 'trap', 'interrupt', 'memory']); enumeration(f.requirement, ['RISCV_ARCH_RETIREMENT']); return { order: integer(f.order, 10000), cycle: integer(f.cycle), field, expected: divergenceValue(f.expected, field), observed: divergenceValue(f.observed, field) } })
  const registers = (v: unknown) => array(v, v => { const r = object(v); return { register: integer(r.register, 31), value: nullable(r.value, u32), last: nullable(r.last_order, integer) } }, 32)
  enumeration(assumptions.privilege, ['machine']); if (assumptions.hart !== 0 || assumptions.compressed !== false || assumptions.mmio !== false) invalid()
  const result = { ...common, domain: 'riscv' as const, first,
    rows: array(rows.rows, v => { const r = object(v); return { order: integer(r.order, 10000), reference: nullable(r.reference, retirement), observed: nullable(r.observed, retirement) } }, 33),
    registers: { order: integer(regs.at_order, 10000), reference: registers(regs.reference), observed: registers(regs.observed) },
    lineage: { mode: enumeration(lineage.mode, ['saved_program', 'fresh_compilation', 'recompiled', 'unknown']), status: enumeration(lineage.status, ['not_supplied', 'program_bytes_equal', 'program_bytes_differ', 'exact_recompiled_replay', 'recompiled_evidence_differs', 'saved_program_bound', 'original_bytes_missing', 'original_run_missing', 'program_bytes_missing']), equal: nullable(lineage.program_equal, bool), proof: nullable(lineage.proof, reference) },
    assumptions: { registers: text(assumptions.initial_registers), cycles: text(assumptions.cycle_relation), scope: text(assumptions.scope) },
  }
  if ([result.registers.reference, result.registers.observed].some(rows => rows.length !== 32 || new Set(rows.map(r => r.register)).size !== 32)) invalid()
  if (result.lineage.status === 'saved_program_bound' && (result.lineage.mode !== 'saved_program' || !result.lineage.equal || !result.lineage.proof)) invalid()
  return result
}
function instruction(raw: unknown) {
  const i = object(raw)
  return { address: u32(i.address), encoding: u32(i.encoding), width: integer(i.width_bits, 32), mnemonic: text(i.mnemonic, 64), operands: typeof i.operands === 'string' && i.operands.length === 0 ? '' : text(i.operands, 256) }
}
function parseFirmwareCase(raw: unknown) {
  const o = object(raw), common = commonCase(raw, 'firmware'), elf = object(o.elf), assembly = object(o.disassembly), assumptions = object(o.assumptions), lineage = object(o.lineage)
  const source = (v: unknown) => { const s = object(v); return { reference: reference(s.reference), value: nullable(s.value, u32) } }
  const facts = nullable(elf.facts, v => { const e = object(v); return { entry: u32(e.entry), segments: array(e.load_segments, v => { const s = object(v); return { address: u32(s.virtual_address), physical: u32(s.physical_address), fileBytes: u32(s.file_size), memoryBytes: u32(s.memory_size), sha256: hash(s.file_sha256) } }, 128) } })
  return { ...common, domain: 'firmware' as const,
    elf: { reference: reference(elf.reference), facts },
    disassembly: { corroborated: bool(assembly.corroborated), total: integer(assembly.total), rows: array(assembly.rows, instruction, 64), truncated: bool(assembly.truncated) },
    registers: array(o.registers, v => { const r = object(v); return { name: text(r.name, 96), address: u32(r.expected_address), reset: u32(r.reset_value), width: integer(r.width_bits, 32), access: enumeration(r.access, ['rw']), header: source(r.header), rtl: source(r.rtl), status: enumeration(r.address_status, ['missing', 'agrees', 'mismatch']), indices: array(r.mmio_indices, v => integer(v, 8), 9) } }, 64),
    mmio: array(o.mmio, v => { const r = object(v); return { index: integer(r.index, 8), bus: integer(r.bus_sequence), cycle: integer(r.cycle), operation: enumeration(r.operation, ['read', 'write']), address: u32(r.address), data: u32(r.data), strobes: integer(r.write_strobes, 15), expected: nullable(r.expected_address, u32), register: nullable(r.expected_register, v => text(v, 96)), pc: nullable(r.pc, u32), order: nullable(r.retirement_order, integer), instruction: nullable(r.instruction, instruction), link: enumeration(r.instruction_link, ['corroborated', 'unavailable']) } }, 9),
    candidate: nullable(o.candidate, v => { const c = object(v), diff = object(c.diff); enumeration(c.origin, ['curated_header_correction']); return { origin: 'curated_header_correction' as const, outcome: enumeration(c.outcome, ['scoped_curated_correction', 'inconclusive']), validation: enumeration(c.validation_status, ['recorded_scoped_validation', 'inconclusive']), source: hash(c.source_hash), checker: hash(c.checker_hash), contract: hash(c.contract_hash), profile: hash(c.profile_hash), badVerdict: text(c.bad_verdict, 64), candidateVerdict: text(c.candidate_verdict, 64), diff: reference(diff.reference), truncated: bool(diff.truncated), sourceAvailable: bool(c.source_bytes_available), covered: array(c.covered_mmio_indices, v => integer(v, 8), 9), limitations: strings(c.limitations) } }),
    lineage: { status: enumeration(lineage.status, ['not_a_replay', 'recorded_saved_program_replay', 'original_bytes_missing']), original: nullable(lineage.original_execution, reference) },
    assumptions: { profile: hash(assumptions.profile_hash), contract: nullable(assumptions.register_contract_hash, hash), reset: text(assumptions.reset), reference: enumeration(assumptions.reference_support, ['independent_literal_mmio_checker_spike_unsupported']), scope: text(assumptions.scope) },
  }
}
export function parseFamilySet(raw: unknown, domain: 'riscv' | 'firmware') {
  const o = object(raw)
  const report = enumeration(o.report_kind, domain === 'riscv' ? ['riscv.recorded-report.v1', 'riscv.recorded-report.v2'] : ['firmware.recorded-report.v1'])
  const cases = domain === 'riscv' ? array(o.cases, parseRiscvCase, 4) : array(o.cases, parseFirmwareCase, 5)
  if (!cases.length || new Set(cases.map(c => c.label)).size !== cases.length) invalid()
  return { domain, report, cases }
}
export function parseSystem(raw: unknown) {
  const o = object(raw)
  enumeration(o.correction_status, ['no_candidate_rerun_evidence'])
  const checks = nullable(o.manifest_checks, v => {
    const c = object(v); enumeration(c.execution_status, ['completed']); enumeration(c.scope, ['source_comparison_only']); enumeration(c.physical_verdict, ['inconclusive'])
    if (hash(c.manifest_hash) !== hash(o.manifest_hash)) invalid()
    return { checker: hash(c.checker_hash), checks: hash(c.checks_hash), rows: array(c.comparisons, v => {
      const r = object(v)
      const scalar = (v: unknown) => v === null ? 'Missing' : typeof v === 'number' ? String(integer(v)) : text(v, 160)
      return { id: text(r.check_id, 128), quantity: enumeration(r.quantity, ['register_address', 'package_pin', 'net', 'clock_period_ps']), expected: scalar(r.expected), observed: array(r.observed, scalar, 16), comparison: enumeration(r.value_comparison, ['consistent', 'conflict', 'inconclusive']), verdict: enumeration(r.verdict, ['consistent', 'conflict', 'inconclusive']), reasons: strings(r.reasons) }
    }, 128) }
  })
  return { domain: 'system' as const, family: enumeration(o.family, ['firmware_decode', 'board_connector']), manifest: hash(o.manifest_hash), checker: hash(o.checker_hash), limitations: strings(o.limitations),
    comparisons: array(o.comparisons, v => { const c = object(v); return { binding: text(c.binding_id, 128), facts: array(c.fact_ids, v => text(v, 128), 3), comparison: enumeration(c.value_comparison, ['consistent', 'conflict', 'inconclusive']), verdict: enumeration(c.verdict, ['consistent', 'conflict', 'inconclusive']), reasons: strings(c.reasons) } }, 16),
    checks,
  }
}
export type DomainProjection = ReturnType<typeof parseVivadoProjection> | ReturnType<typeof parseKicad> | ReturnType<typeof parseFamilySet> | ReturnType<typeof parseSystem>
export function parseProjection(raw: unknown, selected: VersionSummary) {
  const o = object(raw)
  enumeration(o.schema_version, ['engineering.projection.v1']); enumeration(o.scope, ['recorded_import']); enumeration(o.execution_status, ['not_run'])
  if (o.project_id !== selected.projectId || o.investigation_id !== selected.investigationId || o.version_id !== selected.id || hash(o.content_hash) !== selected.contentHash) invalid()
  const projections = array(o.projections, v => {
    const item = object(v)
    switch (item.domain) {
      case 'vivado': enumeration(item.schema_version, ['vivado.investigation.v1', 'vivado.projection.v1']); if (object(item.data).schema_version !== item.schema_version) invalid(); return parseVivadoProjection(item.data)
      case 'kicad': enumeration(item.schema_version, ['kicad.investigation.v1']); return parseKicad(item.data)
      case 'riscv': enumeration(item.schema_version, ['riscv.investigation-set.v1']); return parseFamilySet(item.data, 'riscv')
      case 'firmware': enumeration(item.schema_version, ['firmware.investigation-set.v1']); return parseFamilySet(item.data, 'firmware')
      case 'system': enumeration(item.schema_version, ['system.investigation.v1']); return parseSystem(item.data)
      default: return invalid()
    }
  }, 6)
  if (new Set(projections.map(p => p.domain)).size !== projections.length) invalid()
  if(selected.schemaVersion==='engineering.admission.v3'&&projections.some(p=>p.domain!==selected.domains[0]))invalid()
  return { projections, gaps: array(o.gaps, v => text(v, 512), 64) }
}
