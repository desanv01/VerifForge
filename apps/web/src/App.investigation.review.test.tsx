// Independent App composition only: mocked HTTP and a stateful child sentinel.
import { useEffect, useState } from 'react'
import { cleanup, render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { afterEach, expect, it, vi } from 'vitest'
import { App } from './App'

// This App-only review owns the recorded-investigation health sequence. The
// separately reviewed planner panel has its own independent health requests.
vi.mock('./PlannerPanel', () => ({ PlannerPanel: () => null }))

vi.mock('./EngineeringInvestigation', () => ({ EngineeringInvestigation: ({ projectId }: { projectId: string }) => {
  const [version, setVersion] = useState('initial')
  useEffect(() => { void fetch(`/api/projects/${encodeURIComponent(projectId)}/investigations?after=&limit=20`, { method: 'GET' }) }, [projectId])
  return <section data-testid="review-child"><p>Exact project: {projectId}</p><button onClick={() => setVersion('version-seven')}>Select immutable sentinel version</button><p>{version}</p></section>
} }))

const projectA = { id: 'fifo_correct', name: 'Draft A', family: 'fifo', support_level: 'experimental', contract_id: 'fifo-v1', adapter_id: 'rtl.fifo.v1' }
const projectB = { ...projectA, id: 'fifo_reset_bug', name: 'Saved B' }
const contract = { contract_id: 'fifo-v1', version: '1', review_status: 'reviewed', parameters: { width: 8, depth: 4 }, requirements: [], assumptions: [], checker: { checker_id: 'checker', version: '1', sha256: 'a'.repeat(64) } }
const saved = { id: 'saved-review', project_id: projectB.id, contract_id: contract.contract_id, adapter_id: 'rtl.fifo.v1', execution_status: 'completed', verdict: null, version: 1, attempts: [], findings: [], plan: { schema_version: '1', target: 'fifo', experiment_id: 'saved-plan', fixture_id: projectB.id, contract_id: contract.contract_id, objective_ids: [], seed: 1, parameters: { width: 8, depth: 4 }, operations: [{ op: 'reset', cycles: 1 }], budget: { max_cycles: 10, wall_time_seconds: 1, max_artifact_bytes: 10000 } } }
const json = (value: unknown, status = 200) => new Response(JSON.stringify(value), { status })
function mock(custom?: (url: string, init?: RequestInit) => Response | Promise<Response> | undefined) {
  return vi.spyOn(globalThis, 'fetch').mockImplementation(async (input, init) => {
    const url = String(input), override = custom?.(url, init)
    if (override) return override
    if (url === '/api/projects') return json({ items: [projectA, projectB] })
    if (url.includes('/contracts/')) return json(contract)
    if (url === '/api/campaigns') return json({ items: [saved] })
    if (url.includes('/events?') || url.includes('/investigations?')) return json({ items: [], next_cursor: null })
    if (url === '/api/health') return json({ investigation_api: '1' })
    if (url === `/api/campaigns/${saved.id}`) return json(saved)
    return json({}, 404)
  })
}
function mount(client = new QueryClient({ defaultOptions: { queries: { retry: false, gcTime: 0 }, mutations: { retry: false } } })) {
  return { client, ...render(<QueryClientProvider client={client}><App /></QueryClientProvider>) }
}
const reads = (calls: ReturnType<typeof mock>) => calls.mock.calls.filter(([url]) => String(url).includes('/investigations?'))
afterEach(() => { cleanup(); vi.restoreAllMocks(); history.replaceState(null, '', '/') })

it.each([undefined, 1, '2'])('does not mount or GET investigations for unsupported capability %s', async marker => {
  const calls = mock(url => url === '/api/health' ? json({ investigation_api: marker }) : undefined)
  mount()
  await screen.findByText(/Investigation API version 1 is required/)
  expect(screen.queryByTestId('review-child')).not.toBeInTheDocument()
  expect(reads(calls)).toHaveLength(0)
})

it('requires a fresh scoped health result even when a valid marker is cached', async () => {
  let resolve!: (response: Response) => void
  const calls = mock(url => url === '/api/health' ? new Promise(r => { resolve = r }) : undefined)
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  client.setQueryData(['health', 'investigation-api', projectA.id], { investigation_api: '1' })
  mount(client)
  await screen.findByText('Checking recorded investigation availability…')
  expect(reads(calls)).toHaveLength(0)
  resolve(json({ investigation_api: '1' }))
  await screen.findByText(`Exact project: ${projectA.id}`)
  expect(reads(calls)).toHaveLength(1)
})

it('ignores an old project capability completion after changing catalog project', async () => {
  let healthCalls = 0, resolveOld!: (response: Response) => void
  const calls = mock(url => url === '/api/health' && ++healthCalls === 1 ? new Promise(r => { resolveOld = r }) : undefined)
  mount()
  await screen.findByText('Checking recorded investigation availability…')
  await userEvent.selectOptions(screen.getByLabelText('Curated project'), projectB.id)
  await screen.findByText(`Exact project: ${projectB.id}`)
  resolveOld(json({ investigation_api: '1' }))
  await waitFor(() => expect(screen.getByTestId('review-child')).toHaveTextContent(projectB.id))
  expect(reads(calls).every(([url]) => String(url).includes(`/projects/${projectB.id}/`))).toBe(true)
})

it('waits for exact saved campaign then anchors to it rather than draft project', async () => {
  history.replaceState(null, '', `/?campaign=${saved.id}`)
  let resolve!: (response: Response) => void
  const calls = mock(url => url === `/api/campaigns/${saved.id}` ? new Promise(r => { resolve = r }) : undefined)
  mount()
  await screen.findByText('Load the selected saved campaign to identify its engineering project.')
  expect(reads(calls)).toHaveLength(0)
  resolve(json(saved))
  await screen.findByText(`Exact project: ${projectB.id}`)
  expect(screen.getByLabelText('Curated project')).toHaveValue(projectA.id)
  await userEvent.selectOptions(screen.getByLabelText('Saved campaigns'), '')
  await screen.findByText(`Exact project: ${projectA.id}`)
})

it('rejects a mismatched selected saved campaign response instead of using draft context', async () => {
  history.replaceState(null, '', `/?campaign=${saved.id}`)
  const calls = mock(url => url === `/api/campaigns/${saved.id}` ? json({ ...saved, id: 'foreign-campaign' }) : undefined)
  mount()
  await screen.findByText('Load the selected saved campaign to identify its engineering project.')
  await screen.findByText('foreign-campaign')
  expect(screen.queryByTestId('review-child')).not.toBeInTheDocument()
  expect(reads(calls)).toHaveLength(0)
})

it('removes saved context after campaign refetch failure without falling back to draft', async () => {
  history.replaceState(null, '', `/?campaign=${saved.id}`)
  let fail = false
  const calls = mock(url => url === `/api/campaigns/${saved.id}` && fail ? json({}, 404) : undefined)
  const { client } = mount()
  await screen.findByText(`Exact project: ${projectB.id}`)
  fail = true
  await client.invalidateQueries({ queryKey: ['campaign', saved.id], exact: true })
  await screen.findByText('Load the selected saved campaign to identify its engineering project.')
  expect(screen.queryByTestId('review-child')).not.toBeInTheDocument()
  expect(reads(calls).some(([url]) => String(url).includes(`/projects/${projectA.id}/`))).toBe(false)
})

it('preserves immutable child selection during health polling and unmounts revoked capability', async () => {
  let mode: 'ready' | 'polling' | 'revoked' = 'ready', resolve!: (response: Response) => void
  const calls = mock(url => url !== '/api/health' ? undefined : mode === 'polling' ? new Promise(r => { resolve = r }) : mode === 'revoked' ? json({ investigation_api: '2' }) : undefined)
  const { client } = mount()
  await screen.findByText(`Exact project: ${projectA.id}`)
  await userEvent.click(screen.getByRole('button', { name: 'Select immutable sentinel version' }))
  mode = 'polling'
  const pending = client.invalidateQueries({ queryKey: ['health', 'investigation-api', projectA.id] })
  await screen.findByText('Checking recorded investigation availability…')
  expect(screen.getByText('version-seven')).not.toBeVisible()
  resolve(json({ investigation_api: '1' }))
  await pending
  await waitFor(() => expect(screen.getByText('version-seven')).toBeVisible())
  expect(reads(calls)).toHaveLength(1)
  mode = 'revoked'
  await client.invalidateQueries({ queryKey: ['health', 'investigation-api', projectA.id] })
  await screen.findByText(/Investigation API version 1 is required/)
  expect(screen.queryByTestId('review-child')).not.toBeInTheDocument()
  expect(reads(calls)).toHaveLength(1)
})
