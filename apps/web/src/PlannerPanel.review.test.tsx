// Independent review: synthetic HTTP/jsdom only, no server/provider/native tool.
import { StrictMode } from 'react'
import { act, cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { afterEach, expect, it, vi } from 'vitest'
import type { Campaign, Contract, FifoExperiment, RtlProject } from './api'
import { App } from './App'
import { PlannerPanel } from './PlannerPanel'
import { plannerBudget, plannerCampaign, plannerHealth, plannerPage, plannerPost, plannerRequest, plannerStatus, PLANNER_MAX_BYTES } from './plannerApi'

const project: RtlProject = { id: 'fifo_correct', name: 'Review FIFO', family: 'fifo', support_level: 'experimental', contract_id: 'fifo-v1' }
const plan: FifoExperiment = { schema_version: '1', experiment_id: 'review-seed', fixture_id: project.id,
  contract_id: 'fifo-v1', target: 'fifo', objective_ids: ['FIFO_BEHAVIOR'], seed: 19,
  parameters: { width: 8, depth: 4 }, budget: { max_cycles: 8, wall_time_seconds: 2, max_artifact_bytes: 10485760 },
  operations: [{ op: 'reset', cycles: 1 }, { op: 'write', value: 9 }, { op: 'read' }] }
const campaign: Campaign = { id: 'review-campaign', project_id: project.id, adapter_id: 'rtl.fifo.v1', contract_id: 'fifo-v1',
  plan, completion_policy: 'sequential', execution_status: 'running', verdict: null, version: 1,
  created_at: '2026-10-30T01:00:00Z', updated_at: '2026-10-30T01:00:01Z', findings: [],
  attempts: [{ id: 'review-attempt', experiment_record_id: 'review-record', execution_status: 'completed', verdict: 'pass_within_scope', result: null }] }
const limits = { max_rounds: 3, max_total_cycles: 1000, max_cycles_per_experiment: 100, max_wall_seconds: 60,
  max_execution_seconds: 5, max_model_seconds: 5, max_input_bytes: 64000, max_output_tokens: 1000, max_model_tokens: 10000,
  max_cost_usd: null, max_invalid_proposals: 2, max_transient_errors: 2, max_duplicate_proposals: 2, max_feedback_items: 5, stop_on_finding: true }
const health = { status: 'ok', planner_api: '1', planner_admission_enabled: true, model: 'not_integrated' }
const status = { schema_version: 'planner.status.v1', campaign_id: campaign.id, execution_status: 'running', verdict: null,
  admission_enabled: true, strategy: 'meta', requested_model: 'review/server-model', phase: 'ready', generation: 0,
  stop_reason: null, started_at: '2026-10-30T01:00:00Z', deadline_at: '2026-10-30T01:01:00Z', limits, price_status: 'unknown' }
const budget = { schema_version: 'planner.budget.v1', campaign_id: campaign.id, limits,
  reserved: { tokens: 0, cycles: 0, seconds: '0', cost: null }, price_status: 'unknown' }
const seed = { schema_version: 'planner.seed.v1', campaign_id: campaign.id, execution_status: 'queued', verdict: null, completion_policy: 'sequential', planner_attached: false }
const json = (value: unknown, code = 200) => new Response(JSON.stringify(value), { status: code })
const defer = <T,>() => { let resolve!: (value: T) => void; const promise = new Promise<T>(done => { resolve = done }); return { promise, resolve } }
function transport(custom?: (url: string, init?: RequestInit) => unknown | Promise<unknown>) {
  return vi.spyOn(globalThis, 'fetch').mockImplementation(async (input, init) => {
    const url = String(input), result = await custom?.(url, init)
    if (result instanceof Response) return result
    if (result !== undefined) return json(result)
    if (url === '/api/health') return json(health)
    if (url.endsWith('/planner/budget')) return json(budget)
    if (/\/planner\/(rounds|cleanup)\?/.test(url)) return json({ items: [], next_cursor: null })
    if (url.endsWith('/planner')) return json(status)
    if (url === '/api/planner-seeds') return json(seed)
    if (url === '/api/campaigns/review-campaign') return json(campaign)
    throw new Error(`Unmocked review URL ${url}`)
  })
}
const client = () => new QueryClient({ defaultOptions: { queries: { retry: false, gcTime: 0 }, mutations: { retry: false } } })
function mount(overrides: Partial<Parameters<typeof PlannerPanel>[0]> = {}, cache = client()) {
  let props = { project, campaignId: '', contextReady: true, seedReady: true, prepareSeed: () => structuredClone(plan), onSeedSelected: vi.fn(), ...overrides }
  const tree = () => <StrictMode><QueryClientProvider client={cache}><PlannerPanel {...props}/></QueryClientProvider></StrictMode>
  const view = render(tree())
  return { ...view, cache, selected: props.onSeedSelected, update(next: Partial<typeof props>) { props = { ...props, ...next }; view.rerender(tree()) } }
}
const postCalls = (calls: ReturnType<typeof transport>) => calls.mock.calls.filter(([, init]) => init?.method === 'POST')
afterEach(() => { cleanup(); vi.restoreAllMocks(); history.replaceState(null, '', '/') })

it('does not POST after mount, refresh, or a fresh disabled capability', async () => {
  const calls = transport(url => url === '/api/health' ? { ...health, planner_admission_enabled: false } : undefined)
  mount(); await screen.findByText(/Planner admission disabled/)
  const before = calls.mock.calls.filter(([url]) => url === '/api/health').length
  await userEvent.click(screen.getByRole('button', { name: 'Refresh planner records' }))
  await waitFor(() => expect(calls.mock.calls.filter(([url]) => url === '/api/health').length).toBeGreaterThan(before))
  expect(postCalls(calls)).toHaveLength(0)
  expect(screen.getByRole('button', { name: 'Start bounded FIFO seed' })).toBeDisabled()
})

it('preserves exact seed body/key after an ambiguous transport failure, then requires separate attach', async () => {
  let sends = 0
  const calls = transport((url, init) => {
    if (url === '/api/planner-seeds' && ++sends === 1) throw new TypeError('synthetic lost response')
    if (url.endsWith('/planner')) return json({}, 404)
    if (url.endsWith('/cancel') && init?.method === 'POST') return { ...campaign, execution_status: 'cancelled' }
    return undefined
  })
  const view = mount(); await screen.findByText(/Planner admission enabled/)
  await userEvent.click(screen.getByRole('button', { name: 'Start bounded FIFO seed' }))
  await screen.findByRole('alert')
  await userEvent.click(screen.getByRole('button', { name: 'Start bounded FIFO seed' }))
  await waitFor(() => expect(view.selected).toHaveBeenCalledExactlyOnceWith(campaign.id))
  const sent = postCalls(calls)
  expect(sent).toHaveLength(2)
  expect(sent[0][1]?.body).toBe(sent[1][1]?.body)
  expect(new Headers(sent[0][1]?.headers).get('Idempotency-Key')).toBe(new Headers(sent[1][1]?.headers).get('Idempotency-Key'))
  expect(sent.every(([url]) => url === '/api/planner-seeds')).toBe(true)
  view.update({ campaignId: campaign.id, campaign })
  await screen.findByText(/No planner is attached/)
  expect(postCalls(calls)).toHaveLength(2)
  expect(screen.getByRole('button', { name: 'Attach configured planner' })).toBeEnabled()
})

it('drops an old mount response even after remounting the identical project with the same query client', async () => {
  const pending = defer<Response>()
  const calls = transport(url => url === '/api/planner-seeds' ? pending.promise : undefined)
  const cache = client(), first = mount({}, cache)
  await screen.findByText(/Planner admission enabled/)
  await userEvent.click(screen.getByRole('button', { name: 'Start bounded FIFO seed' }))
  first.unmount(); const second = mount({}, cache)
  await screen.findByText(/Planner admission enabled/)
  await act(async () => { pending.resolve(json(seed)); await pending.promise })
  expect(first.selected).not.toHaveBeenCalled(); expect(second.selected).not.toHaveBeenCalled()
  expect(calls.mock.calls.some(([url]) => url === '/api/campaigns/review-campaign')).toBe(false)
})

it('invalidates continuation authority even if context becomes ready again before the old POST resolves', async () => {
  const pending = defer<Response>()
  const calls = transport(url => url === '/api/planner-seeds' ? pending.promise : undefined)
  const view = mount(); await screen.findByText(/Planner admission enabled/)
  await userEvent.click(screen.getByRole('button', { name: 'Start bounded FIFO seed' }))
  view.update({ contextReady: false }); view.update({ contextReady: true })
  await act(async () => { pending.resolve(json(seed)); await pending.promise })
  expect(view.selected).not.toHaveBeenCalled()
  expect(calls.mock.calls.some(([url]) => url === '/api/campaigns/review-campaign')).toBe(false)
})

it('rejects a pending saved-seed binding GET after switching to a different curated project', async () => {
  const pending = defer<Response>()
  const calls = transport(url => url === '/api/campaigns/review-campaign' ? pending.promise : undefined)
  const view = mount(); await screen.findByText(/Planner admission enabled/)
  await userEvent.click(screen.getByRole('button', { name: 'Start bounded FIFO seed' }))
  await waitFor(() => expect(calls.mock.calls.some(([url]) => url === '/api/campaigns/review-campaign')).toBe(true))
  view.update({ project: { ...project, id: 'fifo_reset_bug' } })
  await screen.findByText(/Planner admission enabled/)
  await act(async () => { pending.resolve(json(campaign)); await pending.promise })
  expect(view.selected).not.toHaveBeenCalled()
})

it('ignores a late cancellation after selecting another campaign without global invalidation', async () => {
  const pending = defer<Response>()
  transport((url, init) => url.endsWith('/cancel') && init?.method === 'POST' ? pending.promise : url.endsWith('/planner') ? json({}, 404) : undefined)
  const view = mount({ campaignId: campaign.id, campaign })
  await screen.findByText(/No planner is attached/)
  await userEvent.click(screen.getByRole('button', { name: 'Cancel bounded campaign' }))
  const invalidate = vi.spyOn(view.cache, 'invalidateQueries')
  view.update({ campaignId: 'other-campaign', campaign: { ...campaign, id: 'other-campaign' } })
  await act(async () => { pending.resolve(json({ ...campaign, execution_status: 'cancelled' })); await pending.promise })
  expect(invalidate).not.toHaveBeenCalled()
})

it('keeps read-only status and cancellation available without admitting new attachment', async () => {
  const calls = transport((url, init) => url === '/api/health' ? { ...health, planner_admission_enabled: false }
    : url.endsWith('/cancel') && init?.method === 'POST' ? { ...campaign, execution_status: 'cancelled' } : undefined)
  mount({ campaignId: campaign.id, campaign }); await screen.findByText(/Cost unknown/)
  expect(screen.getByText(/does not establish provider readiness/)).toBeInTheDocument()
  expect(screen.getByText(/Reservations are not a billing statement/)).toBeInTheDocument()
  expect(screen.getByText('Not evaluated')).toBeInTheDocument()
  expect(screen.getByRole('button', { name: 'Cancel bounded campaign' })).toBeEnabled()
  expect(postCalls(calls)).toHaveLength(0)
  await userEvent.click(screen.getByRole('button', { name: 'Cancel bounded campaign' }))
  await waitFor(() => expect(postCalls(calls)).toHaveLength(1))
  expect(postCalls(calls)[0][0]).toBe('/api/campaigns/review-campaign/cancel')
})

it.each(['price', 'limits'])('withholds budget when the %s differs from frozen status', async field => {
  transport(url => url.endsWith('/planner/budget') ? field === 'price' ? { ...budget, price_status: 'known', reserved: { ...budget.reserved, cost: '0' } }
    : { ...budget, limits: { ...limits, max_rounds: 4 } } : undefined)
  mount({ campaignId: campaign.id, campaign })
  await screen.findByText(/Planner budget could not be validated/)
  expect(screen.queryByText(/Reserved tokens:/)).not.toBeInTheDocument()
})

it('does not read records for mismatched campaign/project identity', async () => {
  const calls = transport(); mount({ campaignId: campaign.id, campaign: { ...campaign, project_id: 'fifo_full_bug' } })
  await screen.findByText(/cannot be bound to a supported FIFO/)
  expect(calls.mock.calls.every(([url]) => url === '/api/health')).toBe(true)
  expect(postCalls(calls)).toHaveLength(0)
})

const contract: Contract = { contract_id: 'fifo-v1', version: '1', review_status: 'reviewed', checker: { checker_id: 'review', version: '1', sha256: 'a'.repeat(64) }, parameters: { width: 8, depth: 4 }, requirements: [{ requirement_id: 'FIFO_BEHAVIOR', description: 'Independent review contract.' }], assumptions: [], reset_priority: 'reset_over_read_write', acceptance: 'pre_edge_flags', read_semantics: 'oldest_on_accepted_edge' }
it.each(['seed', 'wall-time', 'project'])('App isolates an in-flight seed intent after editing %s', async field => {
  const pending = defer<Response>()
  const calls = transport(url => url === '/api/planner-seeds' ? pending.promise : url === '/api/projects' ? { items: [project, { ...project, id: 'fifo_reset_bug' }] }
    : url.startsWith('/api/contracts/') ? contract : url === '/api/campaigns' || url.includes('/events?') ? { items: [] } : undefined)
  render(<StrictMode><QueryClientProvider client={client()}><App/></QueryClientProvider></StrictMode>)
  await screen.findByText('Independent review contract.')
  await userEvent.click(screen.getByRole('checkbox'))
  await waitFor(() => expect(screen.getByRole('button', { name: 'Start bounded FIFO seed' })).toBeEnabled())
  await userEvent.click(screen.getByRole('button', { name: 'Start bounded FIFO seed' }))
  if (field === 'project') await userEvent.selectOptions(screen.getByLabelText('Curated project'), 'fifo_reset_bug')
  else fireEvent.change(screen.getByLabelText(field === 'seed' ? 'Seed' : 'Wall-time budget (seconds)'), { target: { value: '7' } })
  await act(async () => { pending.resolve(json(seed)); await pending.promise })
  expect(location.search).toBe('')
  expect(postCalls(calls)).toHaveLength(1)
  expect(calls.mock.calls.some(([url]) => url === '/api/campaigns/review-campaign')).toBe(false)
})

it.each([undefined, 0, 'true', null])('requires a strict Boolean capability: %s', value => {
  expect(plannerHealth({ ...health, planner_admission_enabled: value }).available).toBe(false)
})
it.each([
  { ...status, campaign_id: 'other' }, { ...status, strategy: 'shell' }, { ...status, generation: 2147483648 },
  { ...status, phase: 'pass_within_scope' }, { ...status, verdict: 'success' },
  { ...status, limits: { ...limits, stop_on_finding: 'true' } },
])('rejects unsupported status without treating it as a design verdict %#', value => {
  expect(() => plannerStatus(value, campaign.id)).toThrow()
})
it('preserves cost precision and unknown price while rejecting unsafe reservation values', () => {
  expect(plannerBudget(budget, campaign.id).cost).toBeNull()
  const cost = '0.00000000000000000000000123456789'
  expect(plannerBudget({ ...budget, price_status: 'known', reserved: { ...budget.reserved, cost } }, campaign.id).cost).toBe(cost)
  expect(() => plannerBudget({ ...budget, reserved: { ...budget.reserved, cycles: 2 ** 53 } }, campaign.id)).toThrow()
})
it('requires every attempt to be valid before using the campaign as an attach seed', () => {
  expect(plannerCampaign(campaign, campaign.id, project.id).eligible).toBe(true)
  expect(() => plannerCampaign({ ...campaign, attempts: [...campaign.attempts, { ...campaign.attempts[0], id: 'later', verdict: 'invented' }] }, campaign.id, project.id)).toThrow()
})
it.each([
  { items: [], next_cursor: 1 }, { items: [], next_cursor: '1' },
  { items: [], next_cursor: null, command: 'unsupported' },
])('rejects a contradictory pagination envelope %#', value => expect(() => plannerPage(value, 0, 'rounds')).toThrow())
it.each(['pending', 'unknown', 'settled'])('rejects contradictory local cleanup timestamps for %s', state => {
  const row = { round_id: 'round-1', ordinal: 1, generation: 0, local_state: state,
    registered_at: status.started_at, owner_lost_at: null, settled_at: state === 'pending' ? status.started_at : null }
  expect(() => plannerPage({ items: [row], next_cursor: null }, 0, 'cleanup')).toThrow()
})
it('bounds the aggregate of stream chunks and cancels on overflow', async () => {
  const cancel = vi.fn()
  vi.spyOn(globalThis, 'fetch').mockResolvedValue(new Response(new ReadableStream({ start(controller) {
    controller.enqueue(new Uint8Array(PLANNER_MAX_BYTES)); controller.enqueue(new Uint8Array(1))
  }, cancel })))
  await expect(plannerRequest('/health', plannerHealth)).rejects.toThrow()
  expect(cancel).toHaveBeenCalledOnce()
})

// Review regressions: the owner must fix these; reviewer does not edit source.
it('rejects contradictory duplicate capability keys rather than enabling the last value', async () => {
  vi.spyOn(globalThis, 'fetch').mockResolvedValue(new Response('{"status":"ok","model":"not_integrated","planner_api":"1","planner_admission_enabled":false,"planner_admission_enabled":true}'))
  await expect(plannerRequest('/health', plannerHealth)).rejects.toThrow()
})
it('does not assert a failed HTTP response proves no server write was accepted', async () => {
  vi.spyOn(globalThis, 'fetch').mockResolvedValue(json({}, 502))
  try { await plannerPost('/planner-seeds', {}, 'review-key', value => value) }
  catch (error) { expect(String(error)).not.toContain('No new planner state was accepted'); return }
  throw new Error('Expected failed HTTP request')
})
