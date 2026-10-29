// Independent AXI workbench review. All fetch results are synthetic UI evidence.
import { createHash } from 'node:crypto'
import { act, cleanup, render, screen, waitFor } from '@testing-library/react'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { afterEach, expect, it, vi } from 'vitest'
import { App } from './App'
import type { AxiLiteExperiment, Campaign, Contract, Project, Result } from './api'
import { createPlan } from './api'
import { AXI_LITE, AXI_TRACE_SCHEMA, readAxiTrace } from './axiLite'
import { AXI_OBJECTIVES, AXI_PARAMETERS, AXI_SCENARIOS } from './axiLitePlans'
import { evidenceAvailable, FIFO, ARITHMETIC } from './families'

const projects: Project[] = AXI_SCENARIOS.map(s => ({ id: s.fixtureId, name: s.key, family: 'axi_lite', adapter_id: AXI_LITE, support_level: 'experimental', experiment_schema_version: 'axi_lite.v1', trace_schema: AXI_TRACE_SCHEMA, trace_artifact: 'observed.json', contract_id: 'axil-registers-v1' }))
const contract: Contract = { contract_id: 'axil-registers-v1', schema_version: 'axi_lite.contract.v1', target: 'axi_lite', review_status: 'reviewed', parameters: { ...AXI_PARAMETERS }, checker: { checker_id: 'synthetic-checker', version: '1', sha256: 'a'.repeat(64) }, requirements: AXI_OBJECTIVES.map(requirement_id => ({ requirement_id, description: 'Synthetic reviewed requirement' })), assumptions: ['Synthetic fixed configuration'], sampling: 'settled_pre_rising_edge', read_write_collision: 'read_before_write', invalid_access: 'slverr_no_write_read_zero', reset: 'clear_registers_cancel_all_pending' }
const plan = createPlan(projects[3], contract, 0) as AxiLiteExperiment
const identity = Object.fromEntries(['source_hash', 'checker_hash', 'contract_hash', 'experiment_hash', 'environment_hash'].map(key => [key, 'a'.repeat(64)]))
const artifact = { artifact_id: 'observed-native', path: 'observed.json', sha256: 'b'.repeat(64), size_bytes: 2000, media_type: 'application/json', downloadable: true }
const result: Result = { attempt_id: 'native-axi-review', experiment_id: plan.experiment_id, execution_status: 'completed', identity, tool_versions: [], trace: [], artifacts: [artifact], uncertainties: ['Synthetic UI observations; no tool executed'], failure_signature: { requirement_id: 'AXIL_HANDSHAKE', failure_class: 'synthetic-response-change', first_cycle: 4, expected: 'stable', observed: 'changed', signature_hash: 'c'.repeat(64) } }
const saved: Campaign = { id: 'selected-axi', project_id: plan.fixture_id, adapter_id: AXI_LITE, contract_id: plan.contract_id, trace_schema: AXI_TRACE_SCHEMA, trace_artifact: 'observed.json', identity, plan, execution_status: 'completed', verdict: 'design_mismatch', version: 1, created_at: '2026-10-30T00:00:00Z', updated_at: '2026-10-30T00:00:00Z', attempts: [{ id: 'db-axi-review', execution_status: 'completed', verdict: 'design_mismatch', result }], findings: [] }
const native = () => ({ schema_version: AXI_TRACE_SCHEMA, observations: plan.cycles.map((_, cycle) => ({ cycle, aw_ready: true, w_ready: false, b_valid: cycle === 4, b_resp: 2, ar_ready: true, r_valid: cycle === 5, r_data: 0xffffffff, r_resp: 2 })) })
const health = { status: 'ok', evidence_api: '1', evidence_adapters: [FIFO, ARITHMETIC, AXI_LITE] }
const json = (v: unknown, status = 200) => new Response(JSON.stringify(v), { status })
function mock(custom?: (url: string) => Response | Promise<Response> | undefined) {
  return vi.spyOn(globalThis, 'fetch').mockImplementation(async input => {
    const url = String(input), override = custom?.(url)
    if (override) return override
    if (url === '/api/projects') return json({ items: projects })
    if (url.includes('/contracts/')) return json(contract)
    if (url === '/api/health') return json(health)
    if (url === '/api/campaigns') return json({ items: [saved] })
    if (url.includes('/events?')) return json({ items: [] })
    if (url.endsWith('/artifacts')) return json({ items: [artifact] })
    if (url.endsWith('/download')) return json(native())
    return json(saved)
  })
}
function mount(client = new QueryClient({ defaultOptions: { queries: { retry: false, gcTime: 0 }, mutations: { retry: false } } })) {
  render(<QueryClientProvider client={client}><App /></QueryClientProvider>)
  return client
}
afterEach(() => { cleanup(); vi.restoreAllMocks(); history.replaceState(null, '', '/') })

const expected = [
  '5a0d6241aa61651491d1fc319a84b6381a80ec2e4e637576cd2b0303748f239d',
  '98b9738dad500c664e1204c32acf10201de41bdce049f5f83ffb42170e67a00f',
  '362acdf693055e10e68f06401e9b2c6ed4b50a40e2d9acce7dcd95abac020299',
  'a82dcf43e838faa3e058ec512943ed103704f3030127bd3fd73dc2cc448d9162',
]
it.each([0, 1, 2, 3])('keeps scenario %i byte-equivalent to the original typed experiment identity', index => {
  const sorted = (v: unknown): unknown => Array.isArray(v) ? v.map(sorted) : v && typeof v === 'object' ? Object.fromEntries(Object.entries(v).sort(([a], [b]) => a < b ? -1 : a > b ? 1 : 0).map(([k, value]) => [k, sorted(value)])) : v
  const value = createPlan(projects[index], contract, 123, 17) as AxiLiteExperiment
  const canonical = JSON.stringify(sorted(value)).replace('"wall_time_seconds":120', '"wall_time_seconds":120.0')
  expect(createHash('sha256').update(canonical).digest('hex')).toBe(expected[index])
  expect(value.seed).toBe(0)
  expect(value.budget).toEqual({ max_cycles: 10000, wall_time_seconds: 120, max_artifact_bytes: 10485760 })
  expect(value).not.toHaveProperty('operations')
})

it.each(['duplicate-row', 'truncated', 'fifo-shape', 'integer-boolean'])('rejects %s as native AXI trace', fault => {
  const wire = native()
  if (fault === 'duplicate-row') wire.observations[1] = { ...wire.observations[0] }
  if (fault === 'truncated') wire.observations.pop()
  if (fault === 'integer-boolean') Object.assign(wire.observations[0], { aw_ready: 1 })
  expect(() => readAxiTrace(fault === 'fifo-shape' ? { schema_version: '1', trace: [] } : wire, plan)).toThrow()
})

it('does not infer AXI support from U9 marker or malformed family metadata', () => {
  expect(evidenceAvailable({ ...health, evidence_adapters: [FIFO, ARITHMETIC], investigation_api: '1' } as typeof health, AXI_LITE)).toBe(false)
  expect(evidenceAvailable({ ...health, evidence_adapters: AXI_LITE } as unknown as typeof health, AXI_LITE)).toBe(false)
})

it('renders exact native channels and divergence with unsupported repair controls', async () => {
  history.replaceState(null, '', '/?campaign=selected-axi')
  mock(); mount()
  expect(await screen.findByRole('table', { name: /AXI observations/ })).toHaveTextContent('0xffffffff')
  expect(screen.getByRole('row', { name: 'First divergence cycle 4' })).toBeVisible()
  expect(screen.queryByText('FIFO transaction trace')).not.toBeInTheDocument()
  expect(screen.getByText(/AXI-Lite reduction is unsupported/)).toBeVisible()
  expect(screen.queryByRole('button', { name: /candidate|reduce|repair/i })).not.toBeInTheDocument()
})

it.each(['duplicate', 'hash', 'size'])('does not download ambiguous or mismatched observed artifact %s', async fault => {
  history.replaceState(null, '', '/?campaign=selected-axi')
  const calls = mock(url => url.endsWith('/artifacts') ? json({ items: fault === 'duplicate' ? [artifact, artifact] : [{ ...artifact, ...(fault === 'hash' ? { sha256: 'd'.repeat(64) } : { size_bytes: 2001 }) }] }) : undefined)
  mount()
  await screen.findByText(/No uniquely authorized observed.json/)
  expect(screen.queryByRole('table')).not.toBeInTheDocument()
  expect(calls.mock.calls.some(([url]) => String(url).endsWith('/download'))).toBe(false)
})

it('does not show foreign campaign AXI evidence when its ID differs from selection', async () => {
  history.replaceState(null, '', '/?campaign=selected-axi')
  mock(url => url === '/api/campaigns/selected-axi' ? json({ ...saved, id: 'foreign-axi' }) : undefined)
  const client = mount()
  await screen.findByText('foreign-axi')
  await waitFor(() => expect(client.isFetching()).toBe(0))
  await act(async () => { await new Promise(resolve => setTimeout(resolve, 50)) })
  const replay = screen.queryByRole('button', { name: 'Replay exact stimulus' })
  expect(replay === null || replay.hasAttribute('disabled')).toBe(true)
  expect(screen.queryByRole('table', { name: /AXI observations/ })).not.toBeInTheDocument()
})

it('removes AXI evidence authority after a failed selected-campaign refetch', async () => {
  history.replaceState(null, '', '/?campaign=selected-axi')
  let broken = false
  mock(url => url === '/api/campaigns/selected-axi' && broken ? json({}, 503) : undefined)
  const client = mount()
  await screen.findByRole('table', { name: /AXI observations/ })
  await waitFor(() => expect(screen.getByRole('button', { name: 'Replay exact stimulus' })).toBeEnabled())
  broken = true
  await client.invalidateQueries({ queryKey: ['campaign', 'selected-axi'], exact: true })
  await screen.findByRole('alert')
  expect(screen.queryByRole('table', { name: /AXI observations/ })).not.toBeInTheDocument()
  const replay = screen.queryByRole('button', { name: 'Replay exact stimulus' })
  expect(replay === null || replay.hasAttribute('disabled')).toBe(true)
})

it('requires fresh AXI family capability before fetching even if a good marker is cached', async () => {
  history.replaceState(null, '', '/?campaign=selected-axi')
  const resolvers: Array<(response: Response) => void> = []
  const calls = mock(url => url === '/api/health' ? new Promise(r => { resolvers.push(r) }) : undefined)
  const client = new QueryClient({ defaultOptions: { queries: { retry: false, gcTime: 0 } } })
  client.setQueryData(['health', 'axi-workbench'], health)
  mount(client)
  await screen.findByText('Loading AXI trace…')
  expect(calls.mock.calls.some(([url]) => String(url).includes('/artifacts'))).toBe(false)
  resolvers.forEach(resolve => resolve(json({ ...health, evidence_adapters: [FIFO] })))
  await waitFor(() => expect(client.isFetching()).toBe(0))
  expect(screen.queryByRole('table')).not.toBeInTheDocument()
  expect(screen.getByRole('button', { name: 'Replay exact stimulus' })).toBeDisabled()
  expect(calls.mock.calls.some(([url]) => String(url).includes('/artifacts'))).toBe(false)
})
