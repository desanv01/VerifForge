import { cleanup, render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { App } from './App'
import { createPlan, hasCompleteIdentity, isActive, MAX_SEED, request } from './api'

const project = { id: 'fifo_correct', name: 'Correct FIFO', family: 'fifo', support_level: 'experimental', contract_id: 'fifo-v1' }
const contract = { contract_id: 'fifo-v1', version: '1', review_status: 'reviewed', checker: { checker_id: 'fifo-checker', version: '1', sha256: 'a'.repeat(64) }, parameters: { width: 8, depth: 4 }, requirements: [{ requirement_id: 'RESET_DISCARDS_PENDING_DATA', description: 'Reset discards all pending values.' }], assumptions: [], reset_priority: 'reset_over_read_write', acceptance: 'pre_edge_flags', read_semantics: 'oldest_on_accepted_edge' }
const campaign = { id: 'campaign-test', project_id: project.id, contract_id: contract.contract_id, execution_status: 'draft', verdict: null, version: 1, created_at: '2026-10-23T00:00:00Z', updated_at: '2026-10-23T00:00:00Z', attempts: [], findings: [], plan: {} }
function mount() { const client = new QueryClient({ defaultOptions: { queries: { retry: false }, mutations: { retry: false } } }); return render(<QueryClientProvider client={client}><App/></QueryClientProvider>) }
function mockApi(override?: (url: string, init?: RequestInit) => unknown) {
  return vi.spyOn(globalThis, 'fetch').mockImplementation(async (input, init) => {
    const url = String(input)
    const custom = override?.(url, init)
    const value = custom ?? (url === '/api/projects' ? { items: [project] } : url.includes('/contracts/') ? contract : url === '/api/campaigns' && init?.method !== 'POST' ? { items: [] } : url.includes('/events?') ? { items: [] } : campaign)
    return new Response(JSON.stringify(value), { status: 200, headers: { 'Content-Type': 'application/json' } })
  })
}
afterEach(() => { cleanup(); vi.restoreAllMocks(); history.replaceState(null, '', '/') })
describe('investigation workflow', () => {
  it('requires contract review, creates typed draft, then explicitly starts it with idempotency', async () => {
    const calls = mockApi((url) => url.endsWith('/start') ? { ...campaign, execution_status: 'queued', version: 2 } : undefined)
    const user = userEvent.setup(); mount()
    await screen.findByText('Reset discards all pending values.')
    expect(screen.getByRole('button', { name: /Create campaign/ })).toBeDisabled()
    await user.click(screen.getByRole('checkbox'))
    await user.click(screen.getByRole('button', { name: /Create campaign/ }))
    await screen.findByText('campaign-test')
    expect(location.search).toContain('campaign=campaign-test')
    const createCall = calls.mock.calls.find(([url, init]) => url === '/api/campaigns' && init?.method === 'POST')!
    const payload = JSON.parse(createCall[1]!.body as string)
    expect(payload.plan.operations).toEqual([{op:'reset',cycles:2},{op:'write',value:17},{op:'reset',cycles:1},{op:'write',value:42},{op:'read'}])
    expect(payload.plan.budget.wall_time_seconds).toBe(120)
    expect(createCall[1]?.headers).toHaveProperty('Idempotency-Key')
    expect(screen.getByText('Not evaluated')).toBeInTheDocument()
    await user.click(screen.getByRole('button', { name: 'Start campaign' }))
    await screen.findByText('queued')
    expect(calls.mock.calls.filter(([url]) => String(url).endsWith('/start'))).toHaveLength(1)
    expect(screen.getByRole('button', { name: 'Cancel execution' })).toBeEnabled()
  })
  it('restores a timeout without calling it a design defect', async () => {
    history.replaceState(null, '', '/?campaign=campaign-test')
    mockApi(url => url === '/api/campaigns/campaign-test' ? {...campaign,execution_status:'completed',verdict:'timeout'} : undefined)
    mount()
    await screen.findByText(/timeout does not establish a design defect/i)
    expect(screen.getByText('completed')).toBeInTheDocument()
    expect(screen.getByText('timeout', { exact: true })).toBeInTheDocument()
    expect(screen.getByRole('button', {name:'Start campaign'})).toBeDisabled()
    expect(screen.getByRole('button', {name:'Cancel execution'})).toBeDisabled()
  })
  it('retries an unchanged failed submission with its exact payload and idempotency key', async () => {
    let submissions = 0
    const calls = mockApi()
    const original = calls.getMockImplementation()!
    calls.mockImplementation(async (input, init) => {
      if (String(input) === '/api/campaigns' && init?.method === 'POST' && ++submissions === 1) throw new TypeError('lost response')
      return original(input, init)
    })
    const user = userEvent.setup(); mount()
    await screen.findByText('Reset discards all pending values.')
    await user.click(screen.getByRole('checkbox'))
    await user.click(screen.getByRole('button', { name: /Create campaign/ }))
    await screen.findByText(/local API is unreachable/)
    await user.click(screen.getByRole('button', { name: /Create campaign/ }))
    await screen.findByText('campaign-test')
    const creates = calls.mock.calls.filter(([url, init]) => url === '/api/campaigns' && init?.method === 'POST')
    expect(creates).toHaveLength(2)
    expect(creates[0][1]?.body).toBe(creates[1][1]?.body)
    expect(creates[0][1]?.headers).toEqual(creates[1][1]?.headers)
  })
  it('shows API unavailability without substituting preview results', async () => {
    vi.spyOn(globalThis,'fetch').mockRejectedValue(new TypeError('network'))
    mount()
    await screen.findAllByText(/local API is unreachable/)
    expect(screen.queryByText('Correct FIFO')).not.toBeInTheDocument()
    expect(screen.queryByText('design mismatch', {exact:true})).not.toBeInTheDocument()
  })
  it('shows expected/observed evidence and immutable identity from the attempt', async () => {
    history.replaceState(null, '', '/?campaign=campaign-test')
    const signature = { requirement_id: 'RESET_DISCARDS_PENDING_DATA', failure_class:'data_mismatch', first_cycle:5, expected:'42', observed:'17', signature_hash:'b'.repeat(64) }
    const result = {identity:{source_hash:'a'.repeat(64)},tool_versions:[],trace:[],artifacts:[],uncertainties:[],failure_signature:signature}
    const finding = {id:'finding-1',attempt_id:'attempt-1',status:'observed',verdict:'design_mismatch',result,contract_id:'fifo-v1',plan_hash:'c'.repeat(64)}
    mockApi(url => url === '/api/campaigns/campaign-test' ? {...campaign, execution_status:'completed', verdict:'design_mismatch', findings:[finding], attempts:[{id:'attempt-1',result}]} : undefined)
    mount()
    await screen.findByText('Independent checker recorded design mismatch.')
    expect(screen.getByText('EXPECTED')).toBeInTheDocument()
    expect(screen.getByText('OBSERVED')).toBeInTheDocument()
    expect(screen.getByText('42')).toBeInTheDocument()
    expect(screen.getByText('17')).toBeInTheDocument()
    expect(screen.getByRole('button', {name:'Replay exact stimulus'})).toBeDisabled()
  })
  it('renders wide saved stimulus and distinct trace integers without rounded digits or raw-JSON claims', async () => {
    history.replaceState(null,'','/?campaign=campaign-test')
    const result = {identity:null,tool_versions:[],artifacts:[],uncertainties:[],failure_signature:null,trace:[{cycle:0,operation_index:0,write_accepted:false,read_accepted:true,expected_data:'odd-placeholder',observed_data:'max-placeholder',expected_empty:true,observed_empty:false,expected_full:false,observed_full:false}]}
    const wire = JSON.stringify({...campaign,execution_status:'completed',verdict:'design_mismatch',plan:{operations:[{op:'write',value:'max-placeholder'}]},attempts:[{id:'attempt-wide',result}]})
      .replaceAll('"odd-placeholder"','9007199254740993').replaceAll('"max-placeholder"','18446744073709551615')
    const calls = mockApi(); const original = calls.getMockImplementation()!
    calls.mockImplementation((input,init) => String(input) === '/api/campaigns/campaign-test' ? Promise.resolve(new Response(wire)) : original(input,init))
    mount(); const user = userEvent.setup()
    await screen.findByText('9007199254740993',{exact:true})
    expect(screen.getByText('18446744073709551615',{exact:true})).toBeInTheDocument()
    expect(screen.queryByText('9007199254740992',{exact:true})).not.toBeInTheDocument()
    await user.click(screen.getByText('Saved experiment (parsed view)',{exact:true}))
    expect(screen.getByText(/"value": "18446744073709551615"/)).toBeVisible()
    expect(screen.getByText(/This inspection view is not raw JSON/)).toBeVisible()
    expect(screen.queryByText('Exact saved experiment',{exact:true})).not.toBeInTheDocument()
  })
  it('keeps an empty curated installation explicit', async () => {
    mockApi(url => url === '/api/projects' ? {items:[]} : undefined)
    mount()
    await screen.findByText(/No curated projects are installed/)
    expect(screen.queryByRole('button',{name:/Create campaign/})).not.toBeInTheDocument()
  })
  it('bounds seed and wall time, and submits the displayed custom budget only to a new draft', async () => {
    const calls = mockApi(); const user = userEvent.setup(); mount()
    await screen.findByText('Reset discards all pending values.')
    await user.click(screen.getByRole('checkbox'))
    const seed = screen.getByLabelText('Seed')
    await user.clear(seed); await user.type(seed, String(MAX_SEED + 1))
    expect(screen.getByRole('button', {name:/Create campaign/})).toBeDisabled()
    await user.clear(seed); await user.type(seed, String(MAX_SEED))
    const wall = screen.getByLabelText('Wall-time budget (seconds)')
    await user.clear(wall); await user.type(wall, '3601')
    expect(screen.getByRole('button', {name:/Create campaign/})).toBeDisabled()
    await user.clear(wall); await user.type(wall, '180')
    expect(screen.getByText('New draft budget: 100 cycles · 180 seconds · 10 MiB artifacts')).toBeInTheDocument()
    await user.click(screen.getByRole('button', {name:/Create campaign/}))
    await screen.findByText('campaign-test')
    const submissions = calls.mock.calls.filter(([url,init]) => url === '/api/campaigns' && init?.method === 'POST')
    const payload = JSON.parse(submissions[0][1]!.body as string)
    expect(payload.plan.seed).toBe(MAX_SEED)
    expect(payload.plan.budget.wall_time_seconds).toBe(180)
    expect(calls.mock.calls.some(([,init]) => init?.method === 'PATCH' || init?.method === 'PUT')).toBe(false)
  })
})
describe('wire boundary', () => {
  it('retains API validation errors', async () => {
    vi.spyOn(globalThis,'fetch').mockResolvedValue(new Response(JSON.stringify({detail:'Contract not reviewed'}),{status:409}))
    await expect(request('/campaigns')).rejects.toThrow('API 409: Contract not reviewed')
  })
  it('classifies only in-flight statuses as cancellable', () => {
    expect(isActive('running')).toBe(true); expect(isActive('completed')).toBe(false); expect(isActive('timeout')).toBe(false)
  })
  it('requires all immutable identity hashes before portable evidence controls', () => {
    expect(hasCompleteIdentity({source_hash:'a'.repeat(64)})).toBe(false)
    expect(hasCompleteIdentity(Object.fromEntries(['source_hash','checker_hash','contract_hash','experiment_hash','environment_hash'].map(key => [key,'a'.repeat(64)])))).toBe(true)
  })
  it('uses bounded typed fixture stimulus without shell inputs', () => {
    const plan = createPlan(project,contract,123)
    expect(plan.fixture_id).toBe(project.id); expect(plan.budget.max_cycles).toBe(100); expect(plan.target).toBe('fifo')
    expect(plan.budget.wall_time_seconds).toBe(120)
    expect(() => createPlan(project,contract,MAX_SEED + 1)).toThrow()
    expect(() => createPlan(project,contract,1,3601)).toThrow()
  })
})
