import { createHash } from 'node:crypto'
import { cleanup, render, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { App } from './App'
import type { AxiLiteExperiment, Campaign, Contract, Project, Result } from './api'
import { createPlan } from './api'
import { AXI_LITE, AXI_TRACE_SCHEMA, axiContextError, readAxiTrace, validAxiPlan } from './axiLite'
import { AXI_OBJECTIVES, AXI_PARAMETERS, AXI_SCENARIOS } from './axiLitePlans'
import { ARITHMETIC, FIFO, campaignAdapter, projectAdapter, setupError } from './families'

// All HTTP observations below are synthetic UI fixtures, not simulator evidence.
const projects: Project[] = AXI_SCENARIOS.map(s => ({ id: s.fixtureId, name: s.label, family: 'axi_lite', support_level: 'experimental', contract_id: 'axil-registers-v1', adapter_id: AXI_LITE, experiment_schema_version: 'axi_lite.v1', trace_schema: AXI_TRACE_SCHEMA, trace_artifact: 'observed.json' }))
const contract: Contract = { contract_id: 'axil-registers-v1', schema_version: 'axi_lite.contract.v1', target: 'axi_lite', review_status: 'reviewed', parameters: { ...AXI_PARAMETERS }, checker: { checker_id: 'axil-checker', version: '1', sha256: 'a'.repeat(64) }, requirements: AXI_OBJECTIVES.map(requirement_id => ({ requirement_id, description: `Reviewed ${requirement_id} behavior.` })), assumptions: ['Curated manager stimulus; settled pre-edge sampling.'], sampling: 'settled_pre_rising_edge', read_write_collision: 'read_before_write', invalid_access: 'slverr_no_write_read_zero', reset: 'clear_registers_cancel_all_pending' }
const plan = createPlan(projects[3], contract, 0) as AxiLiteExperiment
const identity = Object.fromEntries(['source_hash', 'checker_hash', 'contract_hash', 'experiment_hash', 'environment_hash'].map(k => [k, 'a'.repeat(64)]))
const artifact = { artifact_id: 'native-observed', path: 'observed.json', sha256: 'b'.repeat(64), size_bytes: 2048, media_type: 'application/json', downloadable: true }
const result: Result = { attempt_id: 'native-attempt', experiment_id: plan.experiment_id, execution_status: 'completed', identity, tool_versions: [{ name: 'mock-simulator', version: 'test-only' }], trace: [], artifacts: [artifact], uncertainties: [], failure_signature: { requirement_id: 'AXIL_HANDSHAKE', failure_class: 'response_changed', first_cycle: 4, expected: 'Stable stalled response', observed: 'Changed stalled response', signature_hash: 'c'.repeat(64) } }
const saved: Campaign = { id: 'saved-axi', project_id: plan.fixture_id, contract_id: plan.contract_id, adapter_id: AXI_LITE, experiment_schema_version: 'axi_lite.v1', trace_schema: AXI_TRACE_SCHEMA, trace_artifact: 'observed.json', identity, plan, execution_status: 'completed', verdict: 'design_mismatch', version: 4, created_at: '2026-10-29T00:00:00Z', updated_at: '2026-10-29T00:00:00Z', attempts: [{ id: 'database-attempt', execution_status: 'completed', verdict: 'design_mismatch', result }], findings: [{ id: 'finding-axi', attempt_id: 'database-attempt', status: 'observed', verdict: 'design_mismatch', result, contract_id: plan.contract_id, plan_hash: identity.experiment_hash }] }
const native = { schema_version: AXI_TRACE_SCHEMA, observations: plan.cycles.map((_, cycle) => ({ cycle, aw_ready: true, w_ready: true, b_valid: cycle === 4, b_resp: 2, ar_ready: true, r_valid: cycle === 5, r_data: 0xffffffff, r_resp: 2 })) }
const health = { status: 'ok', evidence_api: '1', evidence_adapters: [FIFO, ARITHMETIC, AXI_LITE] }
function mount() { const client = new QueryClient({ defaultOptions: { queries: { retry: false }, mutations: { retry: false } } }); render(<QueryClientProvider client={client}><App/></QueryClientProvider>); return client }
function mockApi(override?: (url: string, init?: RequestInit) => unknown) {
  return vi.spyOn(globalThis, 'fetch').mockImplementation(async (input, init) => {
    const url = String(input), custom = await override?.(url, init)
    if (custom instanceof Response) return custom
    const value = custom ?? (url === '/api/projects' ? { items: projects } : url.includes('/contracts/') ? contract : url === '/api/health' ? health : url.endsWith('/artifacts') ? { items: [artifact] } : url.endsWith('/download') ? native : url.includes('/events?') ? { items: [] } : url === '/api/campaigns' && init?.method !== 'POST' ? { items: [saved] } : saved)
    return new Response(JSON.stringify(value), { status: 200 })
  })
}
afterEach(() => { cleanup(); vi.restoreAllMocks(); history.replaceState(null, '', '/') })

describe('reviewed AXI scenarios', () => {
  it.each(AXI_SCENARIOS.map((s, index) => ({ ...s, index })))('submits the exact $key plan and matching fixture, independent of investigation API support', async scenario => {
    const calls = mockApi(); mount(); const user = userEvent.setup()
    await screen.findByText('Reviewed AXIL_HANDSHAKE behavior.')
    await user.selectOptions(screen.getByLabelText('Curated project'), scenario.fixtureId)
    await user.click(screen.getByRole('checkbox'))
    await waitFor(() => expect(screen.getByRole('button', { name: /Create campaign draft/ })).toBeEnabled())
    expect(screen.queryByLabelText('Seed')).not.toBeInTheDocument()
    expect(screen.queryByLabelText('Wall-time budget (seconds)')).not.toBeInTheDocument()
    expect(screen.getByRole('region', { name: 'Selected AXI scenario' })).toHaveTextContent(scenario.sourceExperimentHash)
    await user.click(screen.getByRole('button', { name: /Create campaign draft/ }))
    await waitFor(() => expect(calls.mock.calls.some(([url, init]) => url === '/api/campaigns' && init?.method === 'POST')).toBe(true))
    const call = calls.mock.calls.find(([url, init]) => url === '/api/campaigns' && init?.method === 'POST')!
    const body = JSON.parse(call[1]!.body as string)
    expect(body).toEqual({ project_id: scenario.fixtureId, adapter_id: AXI_LITE, plan: createPlan(projects[scenario.index], contract, 0) })
    expect(body.plan.cycles).toEqual(scenario.cycles)
    expect(body.plan).not.toHaveProperty('operations')
    expect(call[1]?.headers).toHaveProperty('Idempotency-Key')
  })
  it('retains all four independently supplied canonical experiment hashes', () => {
    // Python canonical_hash sorts object keys and preserves the model float 120.0.
    const sorted = (v: unknown): unknown => Array.isArray(v) ? v.map(sorted) : v && typeof v === 'object' ? Object.fromEntries(Object.entries(v).sort(([a], [b]) => a < b ? -1 : a > b ? 1 : 0).map(([k, n]) => [k, sorted(n)])) : v
    projects.forEach((project, i) => {
      const exact = createPlan(project, contract, 0)
      const canonical = JSON.stringify(sorted(exact)).replace('"wall_time_seconds":120', '"wall_time_seconds":120.0')
      expect(createHash('sha256').update(canonical).digest('hex')).toBe(AXI_SCENARIOS[i].sourceExperimentHash)
      expect(validAxiPlan(exact)).toBe(true)
    })
  })
  it('refuses creation and starting a saved draft when the runtime advertises only FIFO/arithmetic, even with investigation support', async () => {
    history.replaceState(null, '', '/?campaign=saved-axi')
    const calls = mockApi(url => url === '/api/health' ? { ...health, evidence_adapters: [FIFO, ARITHMETIC], investigation_api: '1' } : url === '/api/campaigns/saved-axi' ? { ...saved, execution_status: 'draft', attempts: [], findings: [] } : undefined)
    mount(); const user = userEvent.setup(); await screen.findByText('Reviewed AXIL_HANDSHAKE behavior.'); await user.click(screen.getByRole('checkbox'))
    expect(screen.getByRole('button', { name: /Create campaign draft/ })).toBeDisabled()
    expect(screen.getByRole('button', { name: 'Start campaign' })).toBeDisabled()
    expect(calls.mock.calls.some(([, init]) => init?.method === 'POST')).toBe(false)
  })
  it('rejects unreviewed semantics, unknown fixtures and fixture/plan mismatches', () => {
    expect(setupError(projects[0], { ...contract, sampling: 'post_edge' })).not.toBeNull()
    expect(setupError(projects[0], { ...contract, parameters: { ...AXI_PARAMETERS, response_bound: 5 } })).not.toBeNull()
    expect(projectAdapter({ ...projects[0], id: 'uploaded-axi' })).toBeNull()
    expect(campaignAdapter({ ...saved, project_id: projects[0].id })).toBeNull()
  })
  it('does not accept a malformed adapter string as an advertised capability array', async () => {
    const calls = mockApi(url => url === '/api/health' ? { ...health, evidence_adapters: AXI_LITE } : undefined)
    mount(); const user = userEvent.setup(); await screen.findByText('Reviewed AXIL_HANDSHAKE behavior.'); await user.click(screen.getByRole('checkbox'))
    expect(screen.getByRole('button', { name: /Create campaign draft/ })).toBeDisabled()
    expect(calls.mock.calls.some(([, init]) => init?.method === 'POST')).toBe(false)
  })
})

describe('native AXI evidence', () => {
  it('renders all five channels and recorded failure without a FIFO fallback or new verdict', async () => {
    history.replaceState(null, '', '/?campaign=saved-axi'); mockApi(); mount()
    const table = await screen.findByRole('table', { name: /AXI observations/ })
    expect(within(table).getAllByRole('columnheader')).toHaveLength(6)
    expect(table).toHaveTextContent('0xffffffff')
    expect(screen.getByRole('row', { name: 'First divergence cycle 4' })).toHaveTextContent('First divergence')
    expect(screen.getByText('Stable stalled response')).toBeInTheDocument()
    expect(screen.getByText('Changed stalled response')).toBeInTheDocument()
    expect(screen.getByText(result.failure_signature!.signature_hash)).toBeInTheDocument()
    expect(screen.queryByText('Write / read accepted')).not.toBeInTheDocument()
    expect(screen.getByText(/AXI-Lite reduction is unsupported/)).toBeInTheDocument()
    expect(screen.queryByRole('button', { name: /repair|candidate|reduce/i })).not.toBeInTheDocument()
  })
  it('requests exact replay by database attempt and retains the returned lineage', async () => {
    history.replaceState(null, '', '/?campaign=saved-axi')
    const replayed = { ...saved, id: 'replay-axi', source_attempt_id: 'database-attempt', execution_status: 'queued', verdict: null, attempts: [], findings: [] }
    const calls = mockApi(url => url.endsWith('/replay') || url === '/api/campaigns/replay-axi' ? replayed : undefined); mount(); const user = userEvent.setup()
    await waitFor(() => expect(screen.getByRole('button', { name: 'Replay exact stimulus' })).toBeEnabled())
    await user.click(screen.getByRole('button', { name: 'Replay exact stimulus' }))
    await screen.findByText(/Replay of database attempt/)
    const replayCall = calls.mock.calls.find(([url]) => url === '/api/attempts/database-attempt/replay')!
    expect(replayCall[1]?.method).toBe('POST'); expect(replayCall[1]?.body).toBeUndefined()
    expect(location.search).toContain('campaign=replay-axi')
    expect(screen.getByText('queued', { exact: true })).toBeInTheDocument()
  })
  it('requests an export for the same database attempt and displays its bundle identity', async () => {
    history.replaceState(null, '', '/?campaign=saved-axi')
    const job = { id: 'export-axi', attempt_id: 'database-attempt', status: 'completed', error_type: null, sha256: 'd'.repeat(64), size_bytes: 12345 }
    const calls = mockApi(url => url.endsWith('/exports') || url === '/api/exports/export-axi' ? job : undefined); mount(); const user = userEvent.setup()
    await waitFor(() => expect(screen.getByRole('button', { name: 'Prepare evidence bundle' })).toBeEnabled())
    await user.click(screen.getByRole('button', { name: 'Prepare evidence bundle' }))
    await screen.findByText(job.sha256)
    expect(screen.getByRole('button', { name: 'Download evidence bundle' })).toBeEnabled()
    expect(calls.mock.calls.find(([url]) => url === '/api/attempts/database-attempt/exports')?.[1]?.body).toBeUndefined()
  })
  it.each(['missing', 'duplicate', 'hash mismatch'])('withholds native rows for a %s artifact reference', async fault => {
    history.replaceState(null, '', '/?campaign=saved-axi')
    const calls = mockApi(url => url.endsWith('/artifacts') ? { items: fault === 'missing' ? [] : fault === 'duplicate' ? [artifact, { ...artifact, artifact_id: 'duplicate' }] : [{ ...artifact, sha256: 'e'.repeat(64) }] } : undefined)
    mount(); await screen.findByText(/No uniquely authorized observed.json/)
    expect(screen.queryByRole('table', { name: /AXI observations/ })).not.toBeInTheDocument()
    expect(calls.mock.calls.some(([url]) => String(url).endsWith('/download'))).toBe(false)
  })
  it('rejects a malformed native sidecar without a passing empty trace', async () => {
    history.replaceState(null, '', '/?campaign=saved-axi'); mockApi(url => url.endsWith('/download') ? { ...native, schema_version: 'fifo.v1' } : undefined); mount()
    await screen.findByText(/AXI trace is missing, malformed/)
    expect(screen.queryByRole('table', { name: /AXI observations/ })).not.toBeInTheDocument()
  })
  it('hides cached rows and disables evidence actions when health capability is revoked', async () => {
    history.replaceState(null, '', '/?campaign=saved-axi'); let supported = true
    mockApi(url => url === '/api/health' ? { ...health, evidence_adapters: supported ? health.evidence_adapters : [FIFO, ARITHMETIC] } : undefined)
    const client = mount(); await screen.findByRole('table', { name: /AXI observations/ }); supported = false
    await client.invalidateQueries({ queryKey: ['health', 'axi-workbench'] })
    await waitFor(() => expect(screen.queryByRole('table', { name: /AXI observations/ })).not.toBeInTheDocument())
    expect(screen.getByRole('button', { name: 'Replay exact stimulus' })).toBeDisabled()
    expect(screen.getByRole('button', { name: 'Prepare evidence bundle' })).toBeDisabled()
  })
  it('rejects a mismatched saved evidence identity before fetching trace or enabling replay', async () => {
    history.replaceState(null, '', '/?campaign=saved-axi')
    const calls = mockApi(url => url === '/api/campaigns/saved-axi' ? { ...saved, identity: { ...identity, experiment_hash: 'f'.repeat(64) } } : undefined); mount()
    await screen.findByText(/AXI saved experiment or evidence identity is incomplete/)
    expect(screen.getByRole('button', { name: 'Replay exact stimulus' })).toBeDisabled()
    expect(calls.mock.calls.some(([url]) => String(url).includes('/artifacts'))).toBe(false)
  })
  it('withholds start for a foreign AXI draft response without substituting the selected project', async () => {
    history.replaceState(null, '', '/?campaign=saved-axi')
    const calls = mockApi(url => url === '/api/campaigns/saved-axi' ? { ...saved, id: 'foreign-draft', execution_status: 'draft', attempts: [], findings: [] } : undefined)
    const client = mount(); await screen.findByText('foreign-draft')
    await waitFor(() => expect(client.isFetching()).toBe(0))
    expect(screen.queryByRole('button', { name: 'Start campaign' })).not.toBeInTheDocument()
    expect(screen.queryByRole('button', { name: 'Replay exact stimulus' })).not.toBeInTheDocument()
    expect(calls.mock.calls.some(([, init]) => init?.method === 'POST')).toBe(false)
  })
  it('withholds rows/actions during selected-campaign revalidation and after mismatch, then restores only the exact selected response', async () => {
    history.replaceState(null, '', '/?campaign=saved-axi')
    let pending = false, settle: (value: Campaign) => void = () => {}
    mockApi(url => url === '/api/campaigns/saved-axi' && pending ? new Promise<Campaign>(resolve => { settle = resolve }) : undefined)
    const client = mount(); const user = userEvent.setup()
    await screen.findByRole('table', { name: /AXI observations/ })
    pending = true
    const refresh = client.invalidateQueries({ queryKey: ['campaign', 'saved-axi'], exact: true })
    await screen.findByText(/AXI execution and evidence are withheld/)
    expect(screen.queryByRole('table', { name: /AXI observations/ })).not.toBeInTheDocument()
    expect(screen.queryByRole('button', { name: 'Replay exact stimulus' })).not.toBeInTheDocument()
    expect(screen.queryByRole('button', { name: 'Prepare evidence bundle' })).not.toBeInTheDocument()
    settle({ ...saved, id: 'foreign-after-refetch' }); await refresh
    await screen.findByText('foreign-after-refetch')
    expect(screen.queryByRole('table', { name: /AXI observations/ })).not.toBeInTheDocument()
    expect(screen.queryByRole('button', { name: 'Replay exact stimulus' })).not.toBeInTheDocument()
    pending = false
    await user.click(screen.getByRole('button', { name: 'Reload selected campaign' }))
    await screen.findByRole('table', { name: /AXI observations/ })
    await waitFor(() => expect(screen.getByRole('button', { name: 'Replay exact stimulus' })).toBeEnabled())
  })
  it('withholds cached native rows after a failed health refresh', async () => {
    history.replaceState(null, '', '/?campaign=saved-axi'); let broken = false
    mockApi(url => url === '/api/health' && broken ? new Response('unavailable', { status: 503 }) : undefined)
    const client = mount(); await screen.findByRole('table', { name: /AXI observations/ }); broken = true
    await client.invalidateQueries({ queryKey: ['health', 'axi-workbench'] })
    await waitFor(() => expect(screen.queryByRole('table', { name: /AXI observations/ })).not.toBeInTheDocument())
    expect(screen.getByRole('button', { name: 'Replay exact stimulus' })).toBeDisabled()
  })
  it('validates cycle count, ordering, field types, bounds, and exact saved experiment identity', () => {
    expect(readAxiTrace(native, plan)).toHaveLength(7)
    const badRows = [{ cycle: 99 }, { aw_ready: 'true' }, { r_data: '4294967295' }, { r_data: 4294967296 }, { b_resp: 4 }, { extra: true }]
    for (const changed of badRows) expect(() => readAxiTrace({ ...native, observations: native.observations.map((r, i) => i === 0 ? { ...r, ...changed } : r) }, plan)).toThrow(/No native trace/)
    expect(() => readAxiTrace({ ...native, observations: [] }, plan)).toThrow()
    expect(validAxiPlan({ ...plan, cycles: [{ ...plan.cycles[0], reset_n: true }, ...plan.cycles.slice(1)] })).toBe(false)
    expect(axiContextError(saved, { ...result, experiment_id: 'different-plan' })).not.toBeNull()
    expect(axiContextError(saved, { ...result, identity: null })).not.toBeNull()
  })
})
