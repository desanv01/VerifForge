// Application composition with synthetic HTTP only. No import or execution occurs.
import { act, cleanup, render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { afterEach, expect, it, vi } from 'vitest'
import { App } from './App'
import { StrictMode } from 'react'
import { EvidenceActions } from './EvidenceActions'
import type { Campaign, Contract, Project, RecordedEngineeringProject, RtlProject } from './api'
import { createPlan } from './api'
import { FIFO, projectAdapter, projectKind } from './families'

const recorded: RecordedEngineeringProject = { id: 'recorded-board', name: 'Recorded board', kind: 'engineering_recorded', contract_id: null, description: 'Board evidence admitted locally.', domains: ['kicad', 'system'] }
const second: RecordedEngineeringProject = { ...recorded, id: 'recorded-firmware', name: 'Recorded firmware', domains: ['firmware', 'system'] }
const rtl: RtlProject = { id: 'fifo_correct', name: 'Curated FIFO', family: 'fifo', support_level: 'experimental', contract_id: 'fifo-v1' }
const contract: Contract = { contract_id: 'fifo-v1', version: '1', review_status: 'reviewed', checker: { checker_id: 'test-checker', version: '1', sha256: 'a'.repeat(64) }, parameters: { width: 8, depth: 4 }, requirements: [{ requirement_id: 'FIFO_BEHAVIOR', description: 'Synthetic FIFO behavior.' }], assumptions: [], reset_priority: 'reset_over_read_write', acceptance: 'pre_edge_flags', read_semantics: 'oldest_on_accepted_edge' }
const plan = createPlan(rtl, contract, 0)
const identity = Object.fromEntries(['source_hash', 'checker_hash', 'contract_hash', 'experiment_hash', 'environment_hash'].map(key => [key, 'a'.repeat(64)]))
const saved: Campaign = { id: 'saved-rtl', project_id: rtl.id, contract_id: contract.contract_id, plan, execution_status: 'completed', verdict: 'pass_within_scope', version: 3, created_at: '2026-10-30T00:00:00Z', updated_at: '2026-10-30T00:00:00Z', findings: [], attempts: [{ id: 'db-rtl', execution_status: 'completed', verdict: 'pass_within_scope', result: { identity, tool_versions: [], trace: [], artifacts: [], uncertainties: [], failure_signature: null } }] }
const json = (value: unknown, status = 200) => new Response(JSON.stringify(value), { status })
function mock(items: unknown[] = [recorded, second, rtl], custom?: (url: string, init?: RequestInit) => unknown) {
  return vi.spyOn(globalThis, 'fetch').mockImplementation(async (input, init) => {
    const url = String(input), override = await custom?.(url, init)
    if (override instanceof Response) return override
    const value = override ?? (url === '/api/projects' ? { items } : url.includes('/contracts/') ? contract : url === '/api/health' ? { status: 'ok', investigation_api: '1', evidence_api: '1', evidence_adapters: [FIFO] } : url.includes('/investigations?') ? { items: [], next_cursor: null } : url.includes('/events?') || url.endsWith('/artifacts') ? { items: [] } : url === '/api/campaigns' && init?.method !== 'POST' ? { items: [saved] } : saved)
    return json(value)
  })
}
function mount(client = new QueryClient({ defaultOptions: { queries: { retry: false, gcTime: 0 }, mutations: { retry: false } } })) {
  render(<QueryClientProvider client={client}><App/></QueryClientProvider>); return client
}
afterEach(() => { cleanup(); vi.restoreAllMocks(); history.replaceState(null, '', '/') })
function expectNoExecution() {
  expect(screen.queryByRole('button', { name: /Create campaign|Start campaign|Replay exact|Prepare evidence bundle/ })).not.toBeInTheDocument()
  expect(screen.queryByLabelText('Seed')).not.toBeInTheDocument()
  expect(screen.queryByText('Behavioral contract')).not.toBeInTheDocument()
}

it('shows recorded/import support and domains through the real investigation view without any contract or executable control', async () => {
  const calls = mock(); mount()
  await screen.findByText('No investigations on this page')
  expect(screen.getByRole('region', { name: 'Recorded project support' })).toHaveTextContent('Domains: kicad, system')
  expect(screen.getByText(/Recorded \/ import support/)).toBeInTheDocument()
  expect(screen.getByText(/Recorded investigations for/)).toHaveTextContent(recorded.id)
  expectNoExecution()
  expect(calls.mock.calls.some(([url]) => String(url).includes('/contracts/'))).toBe(false)
  expect(calls.mock.calls.some(([, init]) => init?.method === 'POST')).toBe(false)
  expect(projectAdapter(recorded)).toBeNull()
})

it.each([
  { ...recorded, kind: 'unknown' }, { ...recorded, kind: undefined }, { ...recorded, contract_id: 'fifo-v1' },
  { ...recorded, family: 'fifo', adapter_id: FIFO }, { ...recorded, domains: ['unknown'] }, { ...recorded, domains: ['kicad', 'kicad'] },
  { ...rtl, kind: 'rtl_fixture', contract_id: null }, { ...rtl, contract_id: '' },
])('fails closed for malformed project metadata %#', async invalid => {
  const calls = mock([invalid]); const client = mount()
  await screen.findByText(/Project metadata is unavailable or inconsistent/)
  await waitFor(() => expect(client.isFetching()).toBe(0))
  expectNoExecution()
  expect(projectKind(invalid as unknown as Project)).toBeNull()
  expect(projectAdapter(invalid as unknown as Project)).toBeNull()
  expect(calls.mock.calls.some(([url]) => /\/contracts\/|\/investigations\?/.test(String(url)))).toBe(false)
})

it.each([undefined, 'rtl_fixture'] as const)('retains reviewed FIFO behavior for RTL kind %s', async kind => {
  const calls = mock([{ ...rtl, ...(kind ? { kind } : {}) }]); mount(); const user = userEvent.setup()
  await screen.findByText('Synthetic FIFO behavior.'); await user.click(screen.getByRole('checkbox'))
  expect(screen.getByRole('button', { name: /Create campaign draft/ })).toBeEnabled()
  expect(calls.mock.calls.some(([url]) => url === '/api/contracts/fifo-v1')).toBe(true)
})

it('retains selected saved-campaign ownership until the user explicitly opens the recorded project', async () => {
  history.replaceState(null, '', '/?campaign=saved-rtl&export=old-export')
  const calls = mock(); const client = mount(); const user = userEvent.setup()
  await screen.findByText(/Recorded investigations for/)
  expect(screen.getByText(/Recorded investigations for/)).toHaveTextContent(rtl.id)
  expect(screen.getByText(/selected saved campaign remains the active evidence context/)).toBeInTheDocument()
  expect(location.search).toContain('campaign=saved-rtl')
  await user.click(screen.getByRole('button', { name: 'Open recorded project' }))
  await waitFor(() => expect(screen.getByText(/Recorded investigations for/)).toHaveTextContent(recorded.id))
  await waitFor(() => expect(client.isFetching()).toBe(0))
  expect(location.search).not.toMatch(/campaign=|export=/)
  expectNoExecution()
  expect(calls.mock.calls.some(([url]) => String(url).includes('/contracts/null'))).toBe(false)
})

it('makes a project-selector change an explicit context switch and clears saved evidence', async () => {
  history.replaceState(null, '', '/?campaign=saved-rtl&export=old-export'); mock([rtl, recorded]); mount()
  await screen.findByText('FIFO transaction trace')
  await userEvent.selectOptions(screen.getByLabelText('Curated project'), recorded.id)
  await waitFor(() => expect(screen.getByText(/Recorded investigations for/)).toHaveTextContent(recorded.id))
  expect(screen.queryByText('FIFO transaction trace')).not.toBeInTheDocument()
  expectNoExecution(); expect(location.search).not.toMatch(/campaign=|export=/)
})

it('does not restore old campaign context when its delayed fetch resolves after an explicit project switch', async () => {
  history.replaceState(null, '', '/?campaign=saved-rtl')
  let resolve: (value: Campaign) => void = () => {}
  mock([rtl, recorded], url => url === '/api/campaigns/saved-rtl' ? new Promise<Campaign>(done => { resolve = done }) : undefined)
  const client = mount(); await screen.findByText('Synthetic FIFO behavior.')
  await userEvent.selectOptions(screen.getByLabelText('Curated project'), recorded.id)
  await screen.findByText(/Recorded investigations for/)
  await act(async () => { resolve(saved) }); await waitFor(() => expect(client.isFetching()).toBe(0))
  expect(screen.getByText(/Recorded investigations for/)).toHaveTextContent(recorded.id)
  expectNoExecution(); expect(location.search).not.toContain('campaign=')
})

it('does not let a late create response reselect RTL after choosing a recorded project', async () => {
  let resolve: (value: Campaign) => void = () => {}
  const calls = mock([rtl, recorded], (url, init) => url === '/api/campaigns' && init?.method === 'POST' ? new Promise<Campaign>(done => { resolve = done }) : undefined)
  const client = mount(); const user = userEvent.setup(); await screen.findByText('Synthetic FIFO behavior.')
  await user.click(screen.getByRole('checkbox')); await user.click(screen.getByRole('button', { name: /Create campaign draft/ }))
  await waitFor(() => expect(calls.mock.calls.some(([, init]) => init?.method === 'POST')).toBe(true))
  await user.selectOptions(screen.getByLabelText('Curated project'), recorded.id)
  await act(async () => { resolve(saved) }); await waitFor(() => expect(client.isMutating()).toBe(0))
  await screen.findByText(/Recorded investigations for/)
  expect(screen.getByText(/Recorded investigations for/)).toHaveTextContent(recorded.id)
  expectNoExecution(); expect(location.search).not.toContain('campaign=')
})

it('does not let a late replay callback restore a departed campaign context', async () => {
  history.replaceState(null, '', '/?campaign=saved-rtl')
  let resolve: (value: Campaign) => void = () => {}
  const calls = mock([rtl, recorded], url => url.endsWith('/replay') ? new Promise<Campaign>(done => { resolve = done }) : undefined)
  const client = mount(); const user = userEvent.setup()
  await waitFor(() => expect(screen.getByRole('button', { name: 'Replay exact stimulus' })).toBeEnabled())
  await user.click(screen.getByRole('button', { name: 'Replay exact stimulus' }))
  await waitFor(() => expect(calls.mock.calls.some(([url]) => String(url).endsWith('/replay'))).toBe(true))
  await user.selectOptions(screen.getByLabelText('Curated project'), recorded.id)
  await act(async () => { resolve({ ...saved, id: 'late-replay', source_attempt_id: 'db-rtl' }) })
  await waitFor(() => expect(client.isMutating()).toBe(0))
  await screen.findByText(/Recorded investigations for/)
  expect(screen.getByText(/Recorded investigations for/)).toHaveTextContent(recorded.id)
  expectNoExecution(); expect(location.search).not.toContain('campaign=')
})

it('keeps orphaned App creation from invalidating the shared cache or rewriting history after Strict Mode remount', async () => {
  let resolve: (value: Campaign) => void = () => {}
  const calls = mock([rtl, recorded], (url, init) => url === '/api/campaigns' && init?.method === 'POST' ? new Promise<Campaign>(done => { resolve = done }) : undefined)
  const client = new QueryClient({ defaultOptions: { queries: { retry: false, gcTime: 0 }, mutations: { retry: false } } })
  const tree = () => <StrictMode><QueryClientProvider client={client}><App/></QueryClientProvider></StrictMode>
  const first = render(tree()); const user = userEvent.setup()
  await screen.findByText('Synthetic FIFO behavior.'); await user.click(screen.getByRole('checkbox'))
  await user.click(screen.getByRole('button', { name: /Create campaign draft/ }))
  await waitFor(() => expect(calls.mock.calls.some(([, init]) => init?.method === 'POST')).toBe(true))
  first.unmount(); client.setQueryData(['projects'], { items: [recorded] })
  calls.mockRestore(); mock([recorded]); render(tree())
  await screen.findByText('No investigations on this page')
  await waitFor(() => expect(client.isFetching()).toBe(0))
  const invalidations = vi.spyOn(client, 'invalidateQueries')
  await act(async () => { resolve({ ...saved, id: 'orphaned-owner-create' }) })
  await waitFor(() => expect(client.isMutating()).toBe(0))
  expect(invalidations).not.toHaveBeenCalled()
  expect(location.search).not.toMatch(/campaign=|export=/)
  expectNoExecution()
})

it('allows creation from the current App lifetime after Strict Mode effect rehearsal', async () => {
  mock([rtl]); const client = new QueryClient({ defaultOptions: { queries: { retry: false }, mutations: { retry: false } } })
  render(<StrictMode><QueryClientProvider client={client}><App/></QueryClientProvider></StrictMode>)
  const user = userEvent.setup(); await screen.findByText('Synthetic FIFO behavior.')
  await user.click(screen.getByRole('checkbox')); await user.click(screen.getByRole('button', { name: /Create campaign draft/ }))
  await waitFor(() => expect(location.search).toContain('campaign=saved-rtl'))
  await screen.findByText('FIFO transaction trace')
})

it('requires a fresh project-scoped investigation capability, including after switching projects', async () => {
  let available = true
  const calls = mock([recorded, second], url => url === '/api/health' ? { status: 'ok', investigation_api: available ? '1' : undefined } : undefined)
  mount(); await screen.findByText('No investigations on this page'); available = false
  await userEvent.selectOptions(screen.getByLabelText('Curated project'), second.id)
  await screen.findByText(/Investigation API version 1 is required/)
  expect(screen.queryByText(/Recorded investigations for/)).not.toBeInTheDocument()
  expect(calls.mock.calls.some(([url]) => String(url).startsWith(`/api/projects/${second.id}/investigations?`))).toBe(false)
  expectNoExecution()
})

it('does not let a late export response add a departed attempt to the recorded project URL', async () => {
  history.replaceState(null, '', '/?campaign=saved-rtl')
  let resolve: (value: unknown) => void = () => {}
  const calls = mock([rtl, recorded], url => url.endsWith('/exports') ? new Promise(done => { resolve = done }) : undefined)
  const client = mount(); const user = userEvent.setup()
  await waitFor(() => expect(screen.getByRole('button', { name: 'Prepare evidence bundle' })).toBeEnabled())
  await user.click(screen.getByRole('button', { name: 'Prepare evidence bundle' }))
  await waitFor(() => expect(calls.mock.calls.some(([url]) => String(url).endsWith('/exports'))).toBe(true))
  await user.selectOptions(screen.getByLabelText('Curated project'), recorded.id)
  await act(async () => { resolve({ id: 'late-export', attempt_id: 'db-rtl', status: 'queued', error_type: null, sha256: null, size_bytes: null }) })
  await waitFor(() => expect(client.isMutating()).toBe(0))
  expect(location.search).not.toMatch(/campaign=|export=/)
  expectNoExecution()
})

it('withholds recorded project ownership when a catalog refresh fails', async () => {
  let broken = false
  mock([recorded], url => url === '/api/projects' && broken ? json({}, 503) : undefined)
  const client = mount(); await screen.findByText('No investigations on this page'); broken = true
  await client.invalidateQueries({ queryKey: ['projects'], exact: true })
  await screen.findByRole('alert')
  expect(screen.queryByText(/Recorded investigations for/)).not.toBeInTheDocument()
  expectNoExecution()
})

it.each(['replay', 'exports', 'error'])('ignores an old %s completion after an attempt context changes under Strict Mode', async operation => {
  let resolve: (value: unknown) => void = () => {}
  const route = operation === 'replay' ? 'replay' : 'exports'
  const calls = mock([], url => url === `/api/attempts/old-attempt/${route}` ? new Promise(done => { resolve = done }) : undefined)
  const client = new QueryClient({ defaultOptions: { queries: { retry: false }, mutations: { retry: false } } })
  const onReplay = vi.fn(), tree = (attemptId: string) => <StrictMode><QueryClientProvider client={client}><EvidenceActions attemptId={attemptId} adapterId={FIFO} hasIdentity onReplay={onReplay}/></QueryClientProvider></StrictMode>
  const view = render(tree('old-attempt')); const user = userEvent.setup()
  const label = route === 'replay' ? 'Replay exact stimulus' : 'Prepare evidence bundle'
  await waitFor(() => expect(screen.getByRole('button', { name: label })).toBeEnabled())
  await user.click(screen.getByRole('button', { name: label }))
  await waitFor(() => expect(calls.mock.calls.some(([url]) => url === `/api/attempts/old-attempt/${route}`)).toBe(true))
  view.rerender(tree('new-attempt'))
  await act(async () => { resolve(operation === 'error' ? json({ detail: 'Old context failure' }, 503) : route === 'replay' ? saved : { id: 'old-export', attempt_id: 'old-attempt', status: 'queued', error_type: null, sha256: null, size_bytes: null }) })
  await waitFor(() => expect(client.isMutating()).toBe(0))
  expect(onReplay).not.toHaveBeenCalled()
  expect(location.search).not.toContain('export=')
  expect(screen.queryByText(/Old context failure/)).not.toBeInTheDocument()
  await waitFor(() => expect(screen.getByRole('button', { name: 'Replay exact stimulus' })).toBeEnabled())
  await user.click(screen.getByRole('button', { name: 'Replay exact stimulus' }))
  await waitFor(() => expect(onReplay).toHaveBeenCalledWith(saved))
})
