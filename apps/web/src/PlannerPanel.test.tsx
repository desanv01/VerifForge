// Synthetic HTTP/jsdom only: these tests never start a simulator or provider.
import { StrictMode } from 'react'
import { act, cleanup, render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { afterEach, expect, it, vi } from 'vitest'
import type { Campaign, Contract, FifoExperiment, RtlProject } from './api'
import { createPlan } from './api'
import { App } from './App'
import { PlannerPanel } from './PlannerPanel'
import { plannerBudget, plannerCampaign, plannerHealth, plannerPage, plannerPlan, plannerPost, plannerRequest, plannerStatus, PLANNER_MAX_BYTES } from './plannerApi'

const project: RtlProject = { id: 'fifo_correct', name: 'Curated FIFO', family: 'fifo', support_level: 'experimental', contract_id: 'fifo-v1' }
const contract: Contract = { contract_id: 'fifo-v1', version: '1', review_status: 'reviewed', checker: { checker_id: 'checker', version: '1', sha256: 'a'.repeat(64) }, parameters: { width: 8, depth: 4 }, requirements: [{ requirement_id: 'FIFO_BEHAVIOR', description: 'Synthetic FIFO behavior.' }], assumptions: [], reset_priority: 'reset_over_read_write', acceptance: 'pre_edge_flags', read_semantics: 'oldest_on_accepted_edge' }
const plan = createPlan(project, contract, 123, 20) as FifoExperiment
const campaign: Campaign = { id: 'seed-1', project_id: project.id, adapter_id: 'rtl.fifo.v1', contract_id: 'fifo-v1', plan, completion_policy: 'sequential', execution_status: 'running', verdict: null, version: 2, created_at: '2026-10-30T00:00:00Z', updated_at: '2026-10-30T00:00:01Z', attempts: [{ id: 'attempt-1', experiment_record_id: 'experiment-record-1', execution_status: 'completed', verdict: 'pass_within_scope', result: null }], findings: [] }
const limits = { max_rounds: 15, max_total_cycles: 100000, max_cycles_per_experiment: 10000, max_wall_seconds: 300, max_execution_seconds: 30, max_model_seconds: 30, max_input_bytes: 64000, max_output_tokens: 4096, max_model_tokens: 1000000, max_cost_usd: null, max_invalid_proposals: 2, max_transient_errors: 2, max_duplicate_proposals: 2, max_feedback_items: 5, stop_on_finding: true }
const status = { schema_version: 'planner.status.v1', campaign_id: campaign.id, execution_status: 'running', verdict: null, admission_enabled: true, strategy: 'meta', requested_model: 'server/test-model', phase: 'ready', generation: 0, stop_reason: null, started_at: '2026-10-30T00:00:00Z', deadline_at: '2026-10-30T00:05:00Z', limits, price_status: 'unknown' }
const budget = { schema_version: 'planner.budget.v1', campaign_id: campaign.id, limits, reserved: { tokens: 17, cycles: 50, seconds: '0.000000000000000001', cost: null }, price_status: 'unknown' }
const health = { status: 'ok', model: 'not_integrated', planner_api: '1', planner_admission_enabled: true }
const seedResponse = { schema_version: 'planner.seed.v1', campaign_id: campaign.id, execution_status: 'queued', verdict: null, completion_policy: 'sequential', planner_attached: false }
const json = (value: unknown, code = 200) => new Response(JSON.stringify(value), { status: code })
const round = (ordinal: number, state = 'accepted') => ({ round_id: `round-${ordinal}`, ordinal, generation: 1, status: state, experiment_id: state === 'accepted' ? `experiment-${ordinal}` : null, heartbeat_at: null, finished_at: null })
const cleanupRow = (state = 'pending') => ({ round_id: 'round-1', ordinal: 1, generation: 1, local_state: state, registered_at: status.started_at, owner_lost_at: state === 'unknown' ? '2026-10-30T00:00:01Z' : null, settled_at: state === 'settled' ? '2026-10-30T00:00:02Z' : null })
function mock(custom?: (url: string, init?: RequestInit) => unknown | Promise<unknown>) {
  return vi.spyOn(globalThis, 'fetch').mockImplementation(async (input, init) => {
    const url = String(input), value = await custom?.(url, init)
    if (value instanceof Response) return value
    return json(value ?? (url === '/api/health' ? health : url.endsWith('/planner/budget') ? budget : url.includes('/planner/rounds?') || url.includes('/planner/cleanup?') ? { items: [], next_cursor: null } : url.endsWith('/planner') ? status : url === '/api/planner-seeds' ? seedResponse : url === '/api/projects' ? { items: [project] } : url.includes('/contracts/') ? contract : url === '/api/campaigns' ? { items: [campaign] } : url.includes('/events?') ? { items: [] } : campaign))
  })
}
const makeClient = () => new QueryClient({ defaultOptions: { queries: { retry: false, gcTime: 0 }, mutations: { retry: false } } })
function mount(overrides: Partial<Parameters<typeof PlannerPanel>[0]> = {}, client = makeClient()) {
  const props = { project, campaignId: '', contextReady: true, seedReady: true, prepareSeed: () => ({ ...plan, experiment_id: crypto.randomUUID() }), onSeedSelected: vi.fn(), ...overrides }
  const view = render(<StrictMode><QueryClientProvider client={client}><PlannerPanel {...props}/></QueryClientProvider></StrictMode>)
  return { ...view, props, client, update: (next: Partial<typeof props>) => view.rerender(<StrictMode><QueryClientProvider client={client}><PlannerPanel {...props} {...next}/></QueryClientProvider></StrictMode>) }
}
const selected = { campaignId: campaign.id, campaign }
const posts = (calls: ReturnType<typeof mock>) => calls.mock.calls.filter(([, init]) => init?.method === 'POST')
afterEach(() => { cleanup(); vi.restoreAllMocks(); history.replaceState(null, '', '/') })

it.each([{}, { ...health, planner_api: '2' }, { ...health, planner_admission_enabled: 'true' }, { ...health, model: 'unknown' }])('fails closed for missing or unknown fresh health %#', async value => {
  const calls = mock(url => url === '/api/health' ? value : undefined); mount()
  await screen.findByText(/runtime is unavailable or unsupported/)
  expect(screen.getByRole('button', { name: 'Start bounded FIFO seed' })).toBeDisabled()
  expect(posts(calls)).toHaveLength(0)
  expect(plannerHealth(value).available).toBe(false)
})
it('does not query unsupported projects or submit anything automatically', async () => {
  const calls = mock(); const view = mount({ project: { ...project, id: 'untrusted-fifo' } })
  expect(screen.queryByRole('region', { name: 'Bounded FIFO planner' })).not.toBeInTheDocument(); expect(calls).not.toHaveBeenCalled()
  view.update({ project }); await screen.findByText(/Planner admission enabled/); expect(posts(calls)).toHaveLength(0)
  expect(screen.getByText(/does not establish provider readiness/)).toBeInTheDocument()
})
it('requires review and submits the exact seed only on click, preserving body and key on retry', async () => {
  let count = 0, submitted: FifoExperiment | undefined
  const calls = mock((url, init) => {
    if (url === '/api/planner-seeds') { submitted = JSON.parse(String(init?.body)).plan; if (++count === 1) return json({}, 503) }
    if (url === '/api/campaigns/seed-1') return { ...campaign, plan: submitted }
    return undefined
  })
  const view = mount({ seedReady: false }); await screen.findByText(/Planner admission enabled/)
  expect(screen.getByRole('button', { name: 'Start bounded FIFO seed' })).toBeDisabled()
  view.update({ seedReady: true }); await userEvent.click(screen.getByRole('button', { name: 'Start bounded FIFO seed' }))
  await screen.findByRole('alert'); expect(view.props.onSeedSelected).not.toHaveBeenCalled()
  await userEvent.click(screen.getByRole('button', { name: 'Start bounded FIFO seed' }))
  await waitFor(() => expect(view.props.onSeedSelected).toHaveBeenCalledWith(campaign.id))
  const sent = posts(calls); expect(sent).toHaveLength(2)
  expect(sent[0][1]?.body).toBe(sent[1][1]?.body)
  expect(new Headers(sent[0][1]?.headers).get('Idempotency-Key')).toBe(new Headers(sent[1][1]?.headers).get('Idempotency-Key'))
  expect(JSON.parse(String(sent[0][1]?.body))).toEqual({ schema_version: 'planner.seed.v1', project_id: project.id, plan: submitted })
  expect(submitted?.budget.wall_time_seconds).toBe(20)
  expect(sent.every(([url]) => url === '/api/planner-seeds')).toBe(true)
})
it.each(['id', 'project', 'plan'])('rejects a seed response whose saved campaign has a foreign %s binding', async field => {
  let submitted: FifoExperiment = plan
  mock((url, init) => {
    if (url === '/api/planner-seeds') submitted = JSON.parse(String(init?.body)).plan
    if (url === '/api/campaigns/seed-1') return { ...campaign, plan: field === 'plan' ? { ...submitted, seed: 999 } : submitted, id: field === 'id' ? 'foreign' : campaign.id, project_id: field === 'project' ? 'fifo_reset_bug' : project.id }
    return undefined
  })
  const view = mount(); await screen.findByText(/Planner admission enabled/)
  await userEvent.click(screen.getByRole('button', { name: 'Start bounded FIFO seed' })); await screen.findByRole('alert')
  expect(view.props.onSeedSelected).not.toHaveBeenCalled()
})
it.each([
  { completion_policy: 'single' }, { execution_status: 'completed' }, { attempts: [] },
  { attempts: [{ ...campaign.attempts[0], execution_status: 'running', verdict: null }] },
  { attempts: [{ ...campaign.attempts[0], verdict: 'timeout' }] },
  { attempts: [...campaign.attempts, { ...campaign.attempts[0], id: 'attempt-2', experiment_record_id: 'other-record' }] },
])('requires a settled sequential seed for attachment %#', async patch => {
  const calls = mock(url => url.endsWith('/planner') ? json({}, 404) : undefined)
  mount({ ...selected, campaign: { ...campaign, ...patch } }); await screen.findByText(/No planner is attached/)
  expect(screen.getByRole('button', { name: 'Attach configured planner' })).toBeDisabled(); expect(posts(calls)).toHaveLength(0)
})
it('attaches only an eligible seed and sends no model, prompt, price or credential fields', async () => {
  let attached = false
  const calls = mock((url, init) => {
    if (url.endsWith('/planner')) { if (init?.method === 'POST') attached = true; return attached ? status : json({}, 404) }
    return undefined
  })
  mount(selected); await screen.findByText(/No planner is attached/)
  await userEvent.click(screen.getByRole('button', { name: 'Attach configured planner' }))
  await screen.findByText('Requested model / strategy')
  expect(posts(calls)).toHaveLength(1)
  expect(JSON.parse(String(posts(calls)[0][1]?.body))).toEqual({ schema_version: 'planner.attach.v1' })
})
it('keeps disabled-admission status, unknown cost, uncertain outcome and local cleanup readable; cancellation is explicit', async () => {
  const calls = mock((url, init) => url === '/api/health' ? { ...health, planner_admission_enabled: false } : url.endsWith('/planner') ? { ...status, admission_enabled: false, stop_reason: 'model_call_uncertain', phase: 'interrupted' } : url.includes('/planner/rounds?') ? { items: [round(1, 'uncertain')], next_cursor: null } : url.includes('/planner/cleanup?') ? { items: [cleanupRow('unknown')], next_cursor: null } : url.endsWith('/cancel') && init?.method === 'POST' ? { ...campaign, execution_status: 'cancelled' } : undefined)
  mount(selected); await screen.findByText(/Cost unknown/)
  await screen.findByText('Round 1: unknown'); expect(screen.getAllByText(/Remote model outcome unknown/)).toHaveLength(2)
  expect(screen.getByText(/Reserved execution seconds: 0.000000000000000001/)).toBeInTheDocument()
  expect(screen.queryByText(/USD 0/)).not.toBeInTheDocument(); expect(posts(calls)).toHaveLength(0)
  await userEvent.click(screen.getByRole('button', { name: 'Cancel bounded campaign' }))
  await waitFor(() => expect(posts(calls)).toHaveLength(1))
  expect(posts(calls)[0][0]).toBe('/api/campaigns/seed-1/cancel')
  expect(new Headers(posts(calls)[0][1]?.headers).get('Idempotency-Key')).toBeTruthy()
})
it('pages rounds explicitly and independently, then hides stale rows on a failed refresh', async () => {
  let failed = false
  const calls = mock(url => url.includes('/planner/rounds?') ? failed ? json({}, 500) : url.includes('after=25') ? { items: [round(26)], next_cursor: null } : { items: Array.from({ length: 25 }, (_, i) => round(i + 1)), next_cursor: 25 } : undefined)
  mount(selected); await screen.findByText('Round 25: accepted')
  expect(calls.mock.calls.some(([url]) => String(url).includes('after=25'))).toBe(false)
  await userEvent.click(screen.getByRole('button', { name: 'Next rounds' })); await screen.findByText('Round 26: accepted')
  expect(screen.queryByText('Round 1: accepted')).not.toBeInTheDocument()
  expect(screen.getByRole('button', { name: 'Next rounds' })).toBeDisabled()
  failed = true
  await userEvent.click(screen.getByRole('button', { name: 'Refresh planner records' }))
  await screen.findByText(/Planner rounds could not be validated/)
  expect(screen.queryByText('Round 26: accepted')).not.toBeInTheDocument()
})
it('withholds stale capability and status after a failed health revalidation', async () => {
  let failed = false
  mock(url => url === '/api/health' && failed ? json({}, 503) : undefined)
  mount(selected); await screen.findByText('Requested model / strategy')
  failed = true; await userEvent.click(screen.getByRole('button', { name: 'Refresh planner records' }))
  await screen.findByText(/Planner capability could not be checked/)
  expect(screen.queryByText('Requested model / strategy')).not.toBeInTheDocument()
  expect(screen.getByRole('button', { name: 'Cancel bounded campaign' })).toBeDisabled()
})
it('preserves the selected rounds page across campaign revalidation while withholding stale rows', async () => {
  mock(url => url.includes('/planner/rounds?') ? url.includes('after=25') ? { items: [round(26)], next_cursor: null } : { items: Array.from({ length: 25 }, (_, i) => round(i + 1)), next_cursor: 25 } : undefined)
  const view = mount(selected); await screen.findByText('Round 25: accepted')
  await userEvent.click(screen.getByRole('button', { name: 'Next rounds' })); await screen.findByText('Round 26: accepted')
  view.update({ contextReady: false }); expect(screen.queryByText('Round 26: accepted')).not.toBeInTheDocument()
  view.update({ contextReady: true }); await screen.findByText('Round 26: accepted')
  expect(screen.queryByText('Round 1: accepted')).not.toBeInTheDocument()
})
it('rejects wrong-campaign status and never reads its budget, rounds or cleanup', async () => {
  const calls = mock(url => url.endsWith('/planner') ? { ...status, campaign_id: 'foreign' } : undefined)
  mount(selected); await screen.findByText(/Planner status could not be validated/)
  expect(calls.mock.calls.some(([url]) => /\/planner\/(budget|rounds|cleanup)/.test(String(url)))).toBe(false)
})
it('does not let an old POST select a campaign or start follow-up GETs after actual StrictMode unmount/remount', async () => {
  let resolve!: (r: Response) => void
  const calls = mock(url => url === '/api/planner-seeds' ? new Promise<Response>(done => { resolve = done }) : undefined)
  const client = makeClient(), first = mount({}, client)
  await screen.findByText(/Planner admission enabled/); await userEvent.click(screen.getByRole('button', { name: 'Start bounded FIFO seed' }))
  first.unmount(); const second = mount({}, client); await screen.findByText(/Planner admission enabled/)
  await act(async () => { resolve(json(seedResponse)); await new Promise(done => setTimeout(done, 20)) })
  expect(first.props.onSeedSelected).not.toHaveBeenCalled(); expect(second.props.onSeedSelected).not.toHaveBeenCalled()
  expect(calls.mock.calls.some(([url]) => url === '/api/campaigns/seed-1')).toBe(false)
  expect(client.getQueryData(['campaign', 'seed-1'])).toBeUndefined()
})
it('does not select a seed when project context changes while its binding GET is pending', async () => {
  let resolve!: (r: Response) => void, submitted: FifoExperiment = plan
  mock((url, init) => { if (url === '/api/planner-seeds') submitted = JSON.parse(String(init?.body)).plan; return url === '/api/campaigns/seed-1' ? new Promise<Response>(done => { resolve = done }) : undefined })
  const view = mount(); await screen.findByText(/Planner admission enabled/)
  await userEvent.click(screen.getByRole('button', { name: 'Start bounded FIFO seed' })); await waitFor(() => expect(resolve).toBeTypeOf('function'))
  view.update({ project: { ...project, id: 'fifo_reset_bug' } }); await screen.findByText(/Planner admission enabled/)
  await act(async () => { resolve(json({ ...campaign, plan: submitted })); await new Promise(done => setTimeout(done, 20)) })
  expect(view.props.onSeedSelected).not.toHaveBeenCalled()
})
it('does not invalidate a new selection or the global campaign cache after late attachment', async () => {
  let resolve!: (r: Response) => void
  mock((url, init) => url.endsWith('/planner') ? init?.method === 'POST' ? new Promise<Response>(done => { resolve = done }) : json({}, 404) : undefined)
  const view = mount(selected); await screen.findByText(/No planner is attached/)
  await userEvent.click(screen.getByRole('button', { name: 'Attach configured planner' }))
  const invalidate = vi.spyOn(view.client, 'invalidateQueries')
  view.update({ campaignId: 'seed-2', campaign: { ...campaign, id: 'seed-2' } })
  await act(async () => { resolve(json(status)); await new Promise(done => setTimeout(done, 20)) })
  expect(invalidate).not.toHaveBeenCalled()
})
it('discards late GETs after campaign selection changes even when HTTP ignores abort', async () => {
  let resolve!: (r: Response) => void
  mock(url => url === '/api/campaigns/seed-1/planner' ? new Promise<Response>(done => { resolve = done }) : url === '/api/campaigns/seed-2/planner' ? { ...status, campaign_id: 'seed-2', requested_model: 'new/model' } : url === '/api/campaigns/seed-2/planner/budget' ? { ...budget, campaign_id: 'seed-2' } : undefined)
  const view = mount(selected); await waitFor(() => expect(resolve).toBeTypeOf('function'))
  view.update({ campaignId: 'seed-2', campaign: { ...campaign, id: 'seed-2' } }); await screen.findByText('new/model / meta')
  await act(async () => { resolve(json(status)); await new Promise(done => setTimeout(done, 20)) })
  expect(screen.queryByText('server/test-model / meta')).not.toBeInTheDocument(); expect(screen.getByText('new/model / meta')).toBeInTheDocument()
})
it('revalidates campaign context before any action and clears displayed authority during revalidation', async () => {
  mock(); const view = mount(selected); await screen.findByText('Requested model / strategy')
  view.update({ contextReady: false }); expect(screen.queryByText('Requested model / strategy')).not.toBeInTheDocument()
  expect(screen.getByRole('button', { name: 'Cancel bounded campaign' })).toBeDisabled()
  view.update({ contextReady: true, campaign: { ...campaign, project_id: 'foreign' } })
  expect(screen.getByText(/cannot be bound to a supported FIFO/)).toBeInTheDocument()
})

it('binds the planner seed into App history while preserving the manual draft action', async () => {
  let submitted: FifoExperiment = plan
  const calls = mock((url, init) => { if (url === '/api/planner-seeds') submitted = JSON.parse(String(init?.body)).plan; return url === '/api/campaigns/seed-1' ? { ...campaign, plan: submitted } : undefined })
  render(<QueryClientProvider client={makeClient()}><App/></QueryClientProvider>)
  await screen.findByText('Synthetic FIFO behavior.'); await userEvent.click(screen.getByRole('checkbox'))
  await waitFor(() => expect(screen.getByRole('button', { name: 'Start bounded FIFO seed' })).toBeEnabled())
  expect(screen.getByRole('button', { name: /Create campaign draft/ })).toBeEnabled()
  await userEvent.click(screen.getByRole('button', { name: 'Start bounded FIFO seed' }))
  await waitFor(() => expect(location.search).toBe('?campaign=seed-1'))
  expect(posts(calls)).toHaveLength(1)
  await screen.findByText('Requested model / strategy')
})

it.each([
  { ...status, phase: 'complete' }, { ...status, generation: -1 }, { ...status, admission_enabled: 1 },
  { ...status, extra: true }, { ...status, requested_model: 'secret prompt with spaces' },
  { ...status, deadline_at: status.started_at }, { ...status, started_at: 'yesterday' },
  { ...status, limits: { ...limits, max_rounds: 101 } }, { ...status, limits: { ...limits, max_model_tokens: '100' } },
])('rejects unsupported status wire data %#', value => expect(() => plannerStatus(value, campaign.id)).toThrow())
it('preserves known cost spelling, requires null for unknown cost and rejects non-finite numeric metadata', () => {
  const cost = '0.000000000000000000123456789'
  expect(plannerBudget({ ...budget, price_status: 'known', reserved: { ...budget.reserved, cost } }, campaign.id).cost).toBe(cost)
  expect(() => plannerBudget({ ...budget, reserved: { ...budget.reserved, cost: '0' } }, campaign.id)).toThrow()
  expect(() => plannerBudget({ ...budget, reserved: { ...budget.reserved, tokens: Number.MAX_SAFE_INTEGER + 1 } }, campaign.id)).toThrow()
  expect(() => plannerBudget({ ...budget, reserved: { ...budget.reserved, seconds: 'Infinity' } }, campaign.id)).toThrow()
})
it.each([
  { items: [round(2), round(1)], next_cursor: null }, { items: [round(1), round(1)], next_cursor: null },
  { items: [round(1)], next_cursor: 1 }, { items: [round(1, 'unknown')], next_cursor: null },
  { items: Array.from({ length: 26 }, (_, i) => round(i + 1)), next_cursor: null },
  { items: [round(1)], next_cursor: '1' },
])('rejects invalid or unbounded round pagination %#', value => expect(() => plannerPage(value, 0, 'rounds')).toThrow())
it('rejects contradictory cleanup metadata, malformed seeds and hidden later attempt corruption', () => {
  expect(() => plannerPage({ items: [{ ...cleanupRow(), settled_at: status.started_at }], next_cursor: null }, 0, 'cleanup')).toThrow()
  expect(() => plannerPage({ items: [{ ...cleanupRow('unknown'), owner_lost_at: null }], next_cursor: null }, 0, 'cleanup')).toThrow()
  expect(() => plannerPlan({ ...plan, operations: [{ op: 'write', value: 42 }] }, project.id)).toThrow()
  expect(() => plannerPlan({ ...plan, parameters: { width: 65, depth: 4 } }, project.id)).toThrow()
  expect(() => plannerCampaign({ ...campaign, attempts: [{ ...campaign.attempts[0], execution_status: 'running' }, { id: 'bad' }] }, campaign.id, project.id)).toThrow()
})
it('bounds streamed response size and rejects invalid UTF-8 and non-JSON responses', async () => {
  const calls = vi.spyOn(globalThis, 'fetch').mockResolvedValue(new Response(' '.repeat(PLANNER_MAX_BYTES + 1)))
  await expect(plannerRequest('/health', plannerHealth)).rejects.toThrow()
  calls.mockResolvedValue(new Response(new Uint8Array([0xc3, 0x28])))
  await expect(plannerRequest('/health', plannerHealth)).rejects.toThrow()
  calls.mockResolvedValue(new Response('<html>error</html>'))
  await expect(plannerRequest('/health', plannerHealth)).rejects.toThrow()
})
it('rejects duplicate health keys before allowing admission or invoking the capability parser', async () => {
  const raw = '{"status":"ok","model":"not_integrated","planner_api":"1","planner_admission_enabled":false,"planner_admission_enabled":true}'
  mock(url => url === '/api/health' ? new Response(raw) : undefined)
  const parse = vi.fn(plannerHealth)
  await expect(plannerRequest('/health', parse)).rejects.toThrow()
  expect(parse).not.toHaveBeenCalled()
  const view = mount(); await screen.findByText(/Planner capability could not be checked/)
  expect(screen.getByRole('button', { name: 'Start bounded FIFO seed' })).toBeDisabled()
  expect(view.props.onSeedSelected).not.toHaveBeenCalled()
})
it.each([
  '{"campaign_id":"foreign","campaign_id":"seed-1"}',
  '{"campaign_id":"foreign","campaign_\\u0069d":"seed-1"}',
  '{"limits":{"max_rounds":1,"max_rounds":100}}',
  '{"items":[{"round_id":"one","round_id":"two"}]}',
  '{"__proto__":{},"__proto__":{}}',
])('rejects decoded duplicate identity and nested metadata keys before parsing %#', async raw => {
  vi.spyOn(globalThis, 'fetch').mockResolvedValue(new Response(raw))
  const parse = vi.fn((value: unknown) => value)
  await expect(plannerRequest('/campaigns/seed-1/planner', parse)).rejects.toThrow()
  expect(parse).not.toHaveBeenCalled()
})
it('allows separate object scopes and escaped strings without rounding numeric lexemes or decimal strings', async () => {
  const raw = '{"items":[{"name":"first","quoted":"brace } and quote \\\""},{"name":"second"}],"unsafe":9007199254740993,"decimal":"0.000000000000000000123456789"}'
  vi.spyOn(globalThis, 'fetch').mockResolvedValue(new Response(raw))
  const parsed = await plannerRequest('/campaigns/seed-1/planner', value => value)
  expect(parsed).toEqual({ items: [{ name: 'first', quoted: 'brace } and quote "' }, { name: 'second' }], unsafe: '9007199254740993', decimal: '0.000000000000000000123456789' })
})
it.each([500, 502, 503])('reports unknown POST outcome after HTTP %s and preserves explicit retry identity', async code => {
  let sends = 0, submitted: FifoExperiment = plan
  const calls = mock((url, init) => {
    if (url === '/api/planner-seeds') { submitted = JSON.parse(String(init?.body)).plan; if (++sends === 1) return json({}, code) }
    if (url === '/api/campaigns/seed-1') return { ...campaign, plan: submitted }
    return undefined
  })
  const view = mount(); await screen.findByText(/Planner admission enabled/)
  await userEvent.click(screen.getByRole('button', { name: 'Start bounded FIFO seed' }))
  const alert = await screen.findByRole('alert')
  expect(alert).toHaveTextContent(/outcome is unknown/)
  expect(alert).toHaveTextContent(/may already have been accepted/)
  expect(alert).toHaveTextContent(/Read the current campaign\/planner records/)
  expect(alert).toHaveTextContent(/exact request and Idempotency-Key/)
  expect(alert).not.toHaveTextContent(/No new planner state was accepted/)
  expect(posts(calls)).toHaveLength(1)
  await userEvent.click(screen.getByRole('button', { name: 'Start bounded FIFO seed' }))
  await waitFor(() => expect(view.props.onSeedSelected).toHaveBeenCalledWith(campaign.id))
  const sent = posts(calls); expect(sent).toHaveLength(2)
  expect(sent[0][1]?.body).toBe(sent[1][1]?.body)
  expect(new Headers(sent[0][1]?.headers).get('Idempotency-Key')).toBe(new Headers(sent[1][1]?.headers).get('Idempotency-Key'))
})
it('reports unknown POST outcome for malformed success bodies and keeps GET failure advice read-only', async () => {
  const calls = vi.spyOn(globalThis, 'fetch').mockResolvedValue(new Response('{"id":"first","id":"second"}'))
  await expect(plannerPost('/planner-seeds', {}, 'same-key', value => value)).rejects.toThrow(/outcome is unknown/)
  calls.mockResolvedValue(json({}, 502))
  await expect(plannerRequest('/health', plannerHealth)).rejects.toThrow(/records could not be read/)
  expect(calls).toHaveBeenCalledTimes(2)
})
