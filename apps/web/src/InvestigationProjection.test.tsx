// Synthetic wire projections only. No admitted bytes, HTTP service or native run.
import { cleanup, render, screen, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { afterEach, expect, it, vi } from 'vitest'
import { InvestigationProjection } from './InvestigationProjection'
import { parseProjection } from './investigationProjectionApi'
import type { Artifact, VersionSummary } from './engineeringInvestigationApi'

const h = 'a'.repeat(64), other = 'b'.repeat(64)
const selected: VersionSummary = { id: 'version-1', investigationId: 'investigation-1', projectId: 'project-1', ordinal: 1, contentHash: h, revisionId: 'revision-1', revisionHash: h, manifestHash: h, createdAt: '2026-11-01T00:00:00Z', domains: ['system'], nativeId: 'native-1' }
const ref = { artifact_id: 'exact-source', revision_id: 'revision-1', attempt_id: null, sha256: h }
const file: Artifact = { id: 'opaque-1', artifactId: 'exact-source', revisionId: 'revision-1', attemptId: null, sha256: h, size: 1, roles: ['native_fact'] }
const envelope = (projections: unknown[] = []) => ({ schema_version: 'engineering.projection.v1', project_id: selected.projectId, investigation_id: selected.investigationId, version_id: selected.id, content_hash: h, scope: 'recorded_import', execution_status: 'not_run', projections, gaps: ['No dedicated report is bound to this version.'] })
const system = () => ({ domain: 'system', schema_version: 'system.investigation.v1', data: { family: 'board_connector', manifest_hash: h, checker_hash: h, comparisons: [{ binding_id: 'J1-pin1', fact_ids: ['cad', 'constraint'], value_comparison: 'conflict', verdict: 'inconclusive', reasons: ['common_source_ancestry'] }], manifest_checks: null, limitations: ['Physical acceptance is unresolved.'], correction_status: 'no_candidate_rerun_evidence' } })
function mount() { const client = new QueryClient({ defaultOptions: { queries: { retry: false } } }); const view = render(<QueryClientProvider client={client}><InvestigationProjection selected={selected} artifacts={[file]}/></QueryClientProvider>); return { client, view } }
afterEach(() => { cleanup(); vi.restoreAllMocks() })

it.each(['project_id', 'investigation_id', 'version_id', 'content_hash', 'scope', 'execution_status'])('rejects projection scope/identity drift: %s', field => {
  const value = { ...envelope(), [field]: field === 'content_hash' ? other : 'other' }
  expect(() => parseProjection(value, selected)).toThrow()
})
it('rejects unknown/duplicate domain schemas and never interprets arbitrary JSON', () => {
  expect(() => parseProjection(envelope([{ domain: 'system', schema_version: 'future.v99', data: { html: '<script>bad()</script>' } }]), selected)).toThrow()
  expect(() => parseProjection(envelope([system(), system()]), selected)).toThrow()
})
it('fetches only on explicit request and discards prior comparisons after a failed refresh', async () => {
  let corrupt = false
  const fetch = vi.spyOn(globalThis, 'fetch').mockImplementation(async () => corrupt ? new Response('PRIVATE SERVER ERROR', { status: 409 }) : new Response(JSON.stringify(envelope([system()]))))
  mount(); expect(fetch).not.toHaveBeenCalled()
  await userEvent.click(screen.getByRole('button', { name: 'Load connected domain views' }))
  const region = await screen.findByRole('region', { name: 'Manifest comparisons' })
  expect(within(region).getByText('conflict')).toBeInTheDocument(); expect(within(region).getByText('inconclusive')).toBeInTheDocument()
  corrupt = true; await userEvent.click(screen.getByRole('button', { name: 'Refresh connected domain views' }))
  await screen.findByText(/Connected domain projections are unavailable/)
  expect(screen.queryByRole('region', { name: 'Manifest comparisons' })).not.toBeInTheDocument()
  expect(document.body.textContent).not.toContain('PRIVATE SERVER ERROR')
})
it('reports an unavailable optional endpoint without inventing tool results', async () => {
  vi.spyOn(globalThis, 'fetch').mockResolvedValue(new Response('{}', { status: 404 }))
  mount(); await userEvent.click(screen.getByRole('button', { name: 'Load connected domain views' }))
  await screen.findByText(/Connected domain projections are unavailable/)
  expect(screen.queryByText(/timing requirements/i)).not.toBeInTheDocument()
})

const common = (domain: string) => ({ schema_version: `${domain}.investigation.v1`, domain, case_label: 'control', projection_status: 'partial', identity: { source_hash: h, checker_hash: h, contract_hash: h, experiment_hash: h, environment_hash: h }, execution: { new_execution: 'not_run', producer_status: 'completed', termination: 'completion_store_retired', design_verdict: 'pass_within_scope', checker_status: 'valid' }, evidence: [{ role: 'elf', reference: ref, status: 'available', size_bytes: 1 }], gaps: [{ code: 'missing_artifact', role: 'reference_trace' }] })
it('shows unknown unwritten registers and a missing saved proof without upgrading replay', async () => {
  const regs = Array.from({ length: 32 }, (_, register) => ({ register, value: register === 0 ? 0 : null, last_order: null }))
  const c = { ...common('riscv'), first_divergence: null, retirements: { rows: [] }, registers: { at_order: 0, reference: regs, observed: regs }, lineage: { mode: 'unknown', status: 'not_supplied', program_equal: null, proof: null }, assumptions: { privilege: 'machine', hart: 0, compressed: false, mmio: false, initial_registers: 'unknown_except_x0', cycle_relation: 'DUT cycles only', scope: 'bounded RAM smoke' } }
  const wire = envelope([{ domain: 'riscv', schema_version: 'riscv.investigation-set.v1', data: { report_kind: 'riscv.recorded-report.v2', cases: [c] } }])
  vi.spyOn(globalThis, 'fetch').mockResolvedValue(new Response(JSON.stringify(wire))); mount()
  await userEvent.click(screen.getByRole('button', { name: 'Load connected domain views' }))
  await screen.findByText('No bound saved-program proof.')
  expect(screen.getByText('Unwritten registers remain unknown; x0 is the only assumed zero.')).toBeInTheDocument()
  c.lineage.status = 'saved_program_bound'; expect(() => parseProjection(wire, selected)).toThrow()
  for(const status of ['exact_recompiled_replay','recompiled_evidence_differs','original_run_missing','program_bytes_missing']) {
    c.lineage.status=status; expect(parseProjection(wire,selected).projections).toHaveLength(1)
  }
})
it('retains explicit firmware unknown PC and links only matching admitted evidence', async () => {
  const c = { ...common('firmware'), elf: { reference: ref, facts: null }, disassembly: { corroborated: false, total: 0, rows: [], truncated: false }, registers: [], mmio: [{ index: 0, bus_sequence: 1, cycle: 8, operation: 'write', address: 0x40000000, data: 1, write_strobes: 15, expected_address: 0x40000000, expected_register: 'CONTROL', retirement_order: null, pc: null, instruction: null, instruction_link: 'unavailable' }], candidate: null, lineage: { status: 'not_a_replay', original_execution: null }, assumptions: { profile_hash: h, register_contract_hash: null, reset: 'Reset unknown', reference_support: 'independent_literal_mmio_checker_spike_unsupported', scope: 'fixed native MMIO' } }
  vi.spyOn(globalThis, 'fetch').mockResolvedValue(new Response(JSON.stringify(envelope([{ domain: 'firmware', schema_version: 'firmware.investigation-set.v1', data: { report_kind: 'firmware.recorded-report.v1', cases: [c] } }])))); mount()
  await userEvent.click(screen.getByRole('button', { name: 'Load connected domain views' }))
  const region = await screen.findByRole('region', { name: 'MMIO transfer links' })
  expect(within(region).getByText('Unknown / Unknown')).toBeInTheDocument()
  expect(screen.getByRole('link', { name: 'Download exact projection evidence' })).toHaveAttribute('href', '/api/investigations/investigation-1/versions/version-1/artifacts/opaque-1/download')
})

it('validates KiCad view freshness and never inlines indexed SVG', async () => {
  const index = { source_sha256: h, missing_views: ['pcb_copper'], views: [{ artifact_id: 'exact-source', sha256: h, source_sha256: h, kind: 'schematic', media_type: 'image/svg+xml', lineage: 'source_only', freshness: 'matches_source', visual_review: 'not_recorded' }] }
  const kicad = { domain: 'kicad', mapping: { status: 'inconclusive', schematic_sha256: h, board_sha256: h, rows: [], unexpected_symbol_pins: [], unexpected_footprint_pads: [] }, configured_comparisons: [], missing_comparisons: ['erc', 'drc'], schematic_views: index, pcb_views: { ...index, views: [] }, execution_status: 'not_observed', design_verdict: 'inconclusive', repair_acceptance: 'not_established' }
  const wire = envelope([{ domain: 'kicad', schema_version: 'kicad.investigation.v1', data: kicad }])
  vi.spyOn(globalThis, 'fetch').mockResolvedValue(new Response(JSON.stringify(wire))); mount()
  await userEvent.click(screen.getByRole('button', { name: 'Load connected domain views' }))
  await screen.findByRole('article', { name: 'KiCad mapping investigation' }); expect(document.querySelector('svg,iframe,img')).toBeNull()
  expect(screen.getByRole('link', { name: 'Download exact artifact' })).toHaveAttribute('href', '/api/investigations/investigation-1/versions/version-1/artifacts/opaque-1/download')
  index.views[0].source_sha256 = other; expect(() => parseProjection(wire, selected)).toThrow()
})

it('keeps unknown Vivado I/O coverage separate from successful recorded execution', async () => {
  const data = { schema_version: 'vivado.investigation.v1', investigation_sha256: h, parent_investigation_sha256: null, provenance: 'recorded_report_interpretation_not_new_execution', new_execution: false, project: { part: 'xc7a200tsbg484-1', top: 'exercise3_nexys_video_top', clock_period_ns: 10, recipe_sha256: h, functional_specification_sha256: null, sources: [] }, execution: { status: 'completed', exit_code: 0, receipt_sha256: h }, design_phase: 'routed', tool_version: '2025.1', reports: [], findings: [], missing_input_delays: null, missing_output_delays: 0, methodology_missing_io: null, internal_timing: [['wns_ns', 7]], fully_routed_nets: 35, routing_errors: 0, drc_reported_checks: 0, complete_report_set: false, design_verdict: 'not_evaluated', functional_status: 'not_established', cdc_status: 'not_established', repair_status: 'not_validated' }
  const wire = envelope([{ domain: 'vivado', schema_version: 'vivado.investigation.v1', data }])
  vi.spyOn(globalThis, 'fetch').mockResolvedValue(new Response(JSON.stringify(wire))); mount()
  await userEvent.click(screen.getByRole('button', { name: 'Load connected domain views' }))
  const view = await screen.findByRole('article', { name: 'Vivado report investigation' })
  expect(within(view).getByText(/Missing input delays: Unknown · missing output delays: 0/)).toBeInTheDocument()
  expect(within(view).getByText(/Reported internal timing and build completion do not establish/)).toBeInTheDocument()
  data.execution.exit_code = 1; expect(() => parseProjection(wire, selected)).toThrow()
  data.execution.exit_code = 0
  const wrapped={schema_version:'vivado.projection.v1',investigation:{...data,internal_timing:{wns_ns:7}},candidate_comparison:{schema_version:'vivado.candidate-comparison.v1',comparison_sha256:other,baseline_sha256:other,candidate_sha256:h,status:'not_comparable',unchanged_requirements:false,changed_sources:['exercise3.v'],required_rechecks:['timing_summary'],blockers:['requirements_changed'],external_validation_required:['independent_functional_check'],repair_status:'not_validated',functional_status:'not_established'}}
  const parsed=parseProjection(envelope([{domain:'vivado',schema_version:'vivado.projection.v1',data:wrapped}]),selected).projections[0]
  expect(parsed.domain).toBe('vivado')
  if(parsed.domain==='vivado')expect(parsed.candidate?.external).toEqual(['independent_functional_check'])
})
