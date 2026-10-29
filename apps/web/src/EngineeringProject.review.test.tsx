// Independent adversarial context review. Synthetic HTTP/jsdom only; no server.
import { StrictMode } from 'react'
import { act, cleanup, render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { afterEach, expect, it, vi } from 'vitest'
import { App } from './App'
import { EvidenceActions } from './EvidenceActions'
import { createPlan } from './api'
import type { Campaign, Contract, RecordedEngineeringProject, RtlProject } from './api'
import { FIFO, projectKind, projectAdapter } from './families'

const board: RecordedEngineeringProject = { id: 'review-board', name: 'Independent recorded board', kind: 'engineering_recorded', contract_id: null, description: 'Recorded only', domains: ['kicad'] }
const rtl: RtlProject = { id: 'fifo_correct', name: 'Independent FIFO', kind: 'rtl_fixture', family: 'fifo', contract_id: 'fifo-v1', support_level: 'experimental' }
const contract: Contract = { contract_id: 'fifo-v1', version: '1', review_status: 'reviewed', checker: { checker_id: 'synthetic', version: '1', sha256: 'a'.repeat(64) }, parameters: { width: 8, depth: 4 }, requirements: [{ requirement_id: 'FIFO_BEHAVIOR', description: 'Independent synthetic semantics' }], assumptions: [], reset_priority: 'reset_over_read_write', acceptance: 'pre_edge_flags', read_semantics: 'oldest_on_accepted_edge' }
const saved: Campaign = { id: 'review-saved', project_id: rtl.id, contract_id: 'fifo-v1', plan: createPlan(rtl, contract, 7), execution_status: 'completed', verdict: 'pass_within_scope', version: 2, created_at: '2026-10-30T00:00:00Z', updated_at: '2026-10-30T00:00:00Z', findings: [], attempts: [{ id: 'review-attempt', execution_status: 'completed', verdict: 'pass_within_scope', result: { identity: Object.fromEntries(['source_hash','checker_hash','contract_hash','experiment_hash','environment_hash'].map(k => [k,'b'.repeat(64)])), tool_versions: [], artifacts: [], trace: [], uncertainties: [], failure_signature: null } }] }
const response = (body: unknown) => new Response(JSON.stringify(body))
const clientFor = () => new QueryClient({ defaultOptions: { queries: { retry: false, gcTime: 0 }, mutations: { retry: false } } })
function deferred() { let resolve!: (value: Response) => void; const promise = new Promise<Response>(done => { resolve = done }); return { promise, resolve } }
function transport(items: unknown[], override?: (url: string, init?: RequestInit) => Promise<Response> | Response | undefined) {
  return vi.spyOn(globalThis, 'fetch').mockImplementation(async (input, init) => {
    const url = String(input), custom = override?.(url, init)
    if (custom) return custom
    if (url === '/api/projects') return response({ items })
    if (url === '/api/health') return response({ status: 'ok', investigation_api: '1', evidence_api: '1', evidence_adapters: [FIFO] })
    if (url.startsWith('/api/contracts/')) return response(contract)
    if (url.includes('/investigations?')) return response({ items: [], next_cursor: null })
    if (url.includes('/events?') || url.endsWith('/artifacts')) return response({ items: [] })
    if (url === '/api/campaigns' && init?.method !== 'POST') return response({ items: [saved] })
    return response(saved)
  })
}
const appTree = (client: QueryClient) => <StrictMode><QueryClientProvider client={client}><App/></QueryClientProvider></StrictMode>
afterEach(() => { cleanup(); vi.restoreAllMocks(); history.replaceState(null, '', '/') })

it.each([
  { ...board, contract_id: false }, { ...board, domains: null },
  { ...board, kind: 'engineering_import' }, { ...board, experiment_schema_version: '1' },
])('withholds hybrid or unknown metadata before contract/FIFO/investigation requests %#', async value => {
  const calls = transport([value]); const client = clientFor(); render(appTree(client))
  await screen.findByText(/Project metadata is unavailable or inconsistent/)
  await waitFor(() => expect(client.isFetching()).toBe(0))
  expect(projectKind(value as unknown as RecordedEngineeringProject)).toBeNull()
  expect(projectAdapter(value as unknown as RecordedEngineeringProject)).toBeNull()
  expect(calls.mock.calls.some(([url]) => /\/contracts\/|\/investigations\?/.test(String(url)))).toBe(false)
  expect(screen.queryByRole('button', { name: /Create campaign draft/ })).not.toBeInTheDocument()
})

it('requires fresh scoped health despite a prewarmed positive cache after StrictMode mount', async () => {
  const fresh = deferred(), client = clientFor()
  client.setQueryData(['health','investigation-api',board.id], { status: 'ok', investigation_api: '1' })
  const calls = transport([board], url => url === '/api/health' ? fresh.promise.then(value => value.clone()) : undefined)
  render(appTree(client))
  await screen.findByRole('region', { name: 'Recorded project support' })
  expect(calls.mock.calls.some(([url]) => String(url).includes('/investigations?'))).toBe(false)
  await act(async () => { fresh.resolve(response({ status: 'ok' })) })
  await screen.findByText(/Investigation API version 1 is required/)
  expect(calls.mock.calls.some(([url]) => String(url).includes('/investigations?'))).toBe(false)
})

it.each(['replay','exports'] as const)('keeps an old %s token dead across actual StrictMode unmount/remount with the same attempt', async route => {
  const old = deferred(), client = clientFor(), onReplay = vi.fn()
  const calls = transport([], url => url === `/api/attempts/review-attempt/${route}` ? old.promise : undefined)
  const tree = () => <StrictMode><QueryClientProvider client={client}><EvidenceActions attemptId="review-attempt" hasIdentity onReplay={onReplay}/></QueryClientProvider></StrictMode>
  const first = render(tree())
  const label = route === 'replay' ? 'Replay exact stimulus' : 'Prepare evidence bundle'
  await waitFor(() => expect(screen.getByRole('button', { name: label })).toBeEnabled())
  await userEvent.click(screen.getByRole('button', { name: label }))
  await waitFor(() => expect(calls.mock.calls.some(([url]) => url === `/api/attempts/review-attempt/${route}`)).toBe(true))
  first.unmount(); render(tree())
  await act(async () => { old.resolve(response(route === 'replay' ? saved : { id: 'stale-bundle', attempt_id: 'review-attempt', status: 'queued', error_type: null, sha256: null, size_bytes: null })) })
  await waitFor(() => expect(client.isMutating()).toBe(0))
  expect(onReplay).not.toHaveBeenCalled()
  expect(location.search).not.toContain('export=')
  expect(client.getQueryData(['export','stale-bundle'])).toBeUndefined()
})

it('does not allow late creation from an unmounted App to rewrite a remounted recorded project URL', async () => {
  const old = deferred(), client = clientFor()
  const calls = transport([rtl,board], (url, init) => url === '/api/campaigns' && init?.method === 'POST' ? old.promise : undefined)
  const first = render(appTree(client))
  await screen.findByText('Independent synthetic semantics')
  await userEvent.click(screen.getByRole('checkbox'))
  await userEvent.click(screen.getByRole('button', { name: /Create campaign draft/ }))
  await waitFor(() => expect(calls.mock.calls.some(([url,init]) => url === '/api/campaigns' && init?.method === 'POST')).toBe(true))
  first.unmount()
  client.setQueryData(['projects'], { items: [board] })
  calls.mockRestore(); transport([board])
  render(appTree(client))
  await screen.findByRole('region', { name: 'Recorded project support' })
  await act(async () => { old.resolve(response({ ...saved, id: 'orphaned-create' })) })
  await waitFor(() => expect(client.isMutating()).toBe(0))
  expect(location.search).not.toContain('campaign=')
  expect(screen.queryByText('FIFO transaction trace')).not.toBeInTheDocument()
})

it('holds saved-campaign project authority even when only a different recorded project is in the catalog', async () => {
  history.replaceState(null, '', '/?campaign=review-saved&export=old-export')
  const calls = transport([board]), client = clientFor(); render(appTree(client))
  await waitFor(() => expect(screen.getByText(/Recorded investigations for/)).toHaveTextContent(rtl.id))
  expect(location.search).toContain('campaign=review-saved')
  expect(calls.mock.calls.some(([url]) => String(url).includes('/contracts/'))).toBe(false)
  await userEvent.click(screen.getByRole('button', { name: 'Open recorded project' }))
  await waitFor(() => expect(screen.getByText(/Recorded investigations for/)).toHaveTextContent(board.id))
  expect(location.search).not.toMatch(/campaign=|export=/)
})
