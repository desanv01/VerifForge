// Synthetic metadata and mocked HTTP only: no simulator, native tool, DB or
// retained execution origin is established by this suite.
import { cleanup, render, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { EngineeringInvestigation } from './EngineeringInvestigation'
import { investigationGet, MAX_RESPONSE_BYTES, parseArtifacts, parseDetail, parseInvestigations, parseVersions } from './engineeringInvestigationApi'

const digest = (c: string) => c.repeat(64)
const ref = (id: string, sha = digest('a'), attempt: string | null = null) => ({ artifact_id: id, revision_id: 'native-revision', attempt_id: attempt, sha256: sha })
function fixture(project = 'project-a', version = 'version-1', ordinal = 1) {
  const investigationId = `investigation-${project}`, revision = ref('revision'), manifest = ref('manifest', digest('b')), bridge = ref('bridge', digest('c')), source = ref('source', digest('d')), first = ref('native-first', digest('e')), second = ref('native-second', digest('f')), result = ref('result', digest('a'), 'native-attempt')
  const references = [revision, manifest, bridge, source, first, second, result]
  const artifacts = references.map((reference, index) => ({ reference, size_bytes: 100 + index, roles: [index === 6 ? 'attempt_result' : index > 3 ? 'native_fact' : ['project_revision', 'manifest', 'bridge_report', 'project_source'][index]], canonical_record_hash: null }))
  const wireSummary = { id: version, investigation_id: investigationId, project_id: project, ordinal, content_hash: digest(ordinal === 1 ? 'a' : 'b'), revision_id: 'revision-1', revision_hash: revision.sha256, manifest_hash: manifest.sha256, schema_version: 'engineering.admission.v1', scope: 'recorded_import', execution_status: 'not_run', created_at: '2026-10-30T00:00:00Z', domains: ['kicad', 'vivado'], native_investigation_id: 'native-investigation' }
  const binding = { campaign_id: 'db-campaign', db_attempt_id: 'db-attempt', experiment_record_id: 'db-experiment', native_attempt_id: 'native-attempt', wire_experiment_id: 'wire-experiment', result_ref: result }
  const facts = [first, second].map((reference, index) => ({ fact_id: `fact-${index}`, domain: index ? 'vivado' : 'kicad', native_schema_id: index ? 'PinObservation.v1' : 'ConnectorObservation.v1', native_record_hash: reference.sha256, native_record: reference, category: 'observation', review_state: 'unreviewed', conditions: ['Private bytes C:\\private\\source'], provenance: { state: 'complete', sources: [{ artifact: source, location: { kind: 'json_pointer', pointer: '/private' } }], extraction: { method: 'native_parser', extractor_id: 'extractor', extractor_version: '1', implementation_hash: digest('c'), tool: null }, derived_from: [], uncertainties: [] } }))
  const relations = ['inconclusive', 'conflict'].map((verdict, index) => ({ relation_id: `relation-${index}`, relation: 'connects_to', fact_ids: ['fact-0', 'fact-1'], requirement_fact_ids: [], basis: 'reviewed', verdict, dependence: 'same_source', checker: null, evidence: [], uncertainties: ['Private path /home/private/source'] }))
  const detail = { ...wireSummary, envelope: { schema_version: 'engineering.admission.v1', operation: 'admission_only', new_execution: 'not_run', claim_scope: 'resolved_bytes_and_typed_consistency_only', attempt_context_scope: 'host_pinned_association_not_execution_origin', record_hash: digest('a'), record: { schema_version: 'engineering.investigation.v1', investigation_id: 'native-investigation', project_id: project, revision_id: 'revision-1', project_revision_ref: revision, manifest_ref: manifest, bridge_report_ref: bridge, native_records: facts.map(f => ({ fact_id: f.fact_id, domain: f.domain, native_schema_id: f.native_schema_id, reference: f.native_record })), attempts: [binding] }, artifacts, total_artifact_bytes: artifacts.reduce((n, a) => n + a.size_bytes, 0) }, project_revision: { schema_version: 'engineering.project-revision.v1', project_id: project, revision_id: 'revision-1', source_refs: [source], configuration_hash: source.sha256 }, manifest: { schema_version: 'engineering.manifest.v1', manifest_id: 'manifest', revision_id: 'manifest-revision', review_state: 'draft', reviewed_by: null, adapters: [], facts, relations }, bridge_report: { schema_version: 'system.bridge.v1', execution_status: 'not_run', family: 'board_connector', checker_hash: digest('c'), comparisons: relations.map((r, i) => ({ binding_id: String(i), fact_ids: r.fact_ids, verdict: r.verdict, value_comparison: 'conflict' })), private: 'C:\\private\\bytes', native_records: [{ private: 'secret-native-value' }] }, execution_contexts: [{ binding, origin_at_admission: 'context_only' }] }
  const item = { id: investigationId, project_id: project, created_at: wireSummary.created_at, latest_version: wireSummary }
  const downloads = { items: artifacts.map((a, index) => ({ id: `opaque-${index}`, ...a.reference, sha256: a.reference.sha256, size_bytes: a.size_bytes, media_type: 'application/octet-stream', roles: a.roles })) }
  const summary = parseInvestigations({ items: [item], next_cursor: null }, project).items[0].latest
  return { project, investigationId, wireSummary, summary, detail, item, downloads }
}
const json = (value: unknown, status = 200) => new Response(JSON.stringify(value), { status, headers: { 'Content-Type': 'application/json' } })
function mockHttp(f = fixture(), override?: (url: string, init?: RequestInit) => Response | Promise<Response> | undefined) {
  return vi.spyOn(globalThis, 'fetch').mockImplementation(async (input, init) => {
    const url = String(input), custom = override?.(url, init); if (custom) return custom
    if (url.startsWith(`/api/projects/${f.project}/investigations?`)) return json({ items: [f.item], next_cursor: null })
    if (url.startsWith(`/api/investigations/${f.investigationId}/versions?`)) return json({ items: [f.wireSummary], next_cursor: null })
    if (url.endsWith('/artifacts')) return json(f.downloads)
    if (url.endsWith(`/${f.wireSummary.id}`)) return json(f.detail)
    return json({ detail: 'private server error C:\\private' }, 404)
  })
}
function mount(project = 'project-a') {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false, gcTime: 0 } } })
  const wrapper = ({ children }: { children: React.ReactNode }) => <QueryClientProvider client={client}>{children}</QueryClientProvider>
  const view = render(<EngineeringInvestigation projectId={project} />, { wrapper })
  return { client, ...view }
}
afterEach(() => { cleanup(); vi.restoreAllMocks() })

describe('engineering investigation read-only workbench (mock HTTP)', () => {
  it('shows project identity, scoped relations and exact version downloads without native/private text or mutation controls', async () => {
    const f = fixture(), fetch = mockHttp(f); mount()
    const link = await screen.findByRole('link', { name: 'Download source' })
    expect(link).toHaveAttribute('href', `/api/investigations/${f.investigationId}/versions/version-1/artifacts/opaque-3/download`)
    expect(link).toHaveAttribute('download', `${digest('d')}.bin`)
    expect(screen.getByText('Admission only')).toBeVisible(); expect(screen.getByText('Not run')).toBeVisible()
    expect(screen.getByText('Recorded context only')).toBeVisible()
    expect(screen.getByText('inconclusive', { exact: true })).toBeVisible(); expect(screen.getAllByText('conflict', { exact: true })[0]).toBeVisible()
    expect(screen.getByText('103 bytes', { exact: false })).toBeVisible()
    expect(document.body.textContent).not.toMatch(/C:\\private|\/home\/private|secret-native-value|Private bytes/)
    expect(screen.queryByRole('button', { name: /create|start|execute|repair/i })).not.toBeInTheDocument()
    expect(fetch.mock.calls.every(([, init]) => init?.method === 'GET')).toBe(true)
    await userEvent.click(screen.getByText('fact-0 · kicad · observation · unreviewed'))
    expect(screen.getAllByText(digest('e')).length).toBeGreaterThan(0)
  })
  it('keeps retained-byte origin scoped to admission and distinct from a new run', async () => {
    const f = fixture(); f.detail.execution_contexts[0].origin_at_admission = 'retained_execution_bytes'; mockHttp(f); mount()
    await screen.findByText('Retained execution bytes matched at admission')
    expect(screen.getByText(/does not establish a new run or current tool readiness/)).toBeVisible()
    expect(screen.getByText('Not run')).toBeVisible()
  })
  it('shows loading, empty and refresh recovery without inventing a record', async () => {
    let resolve!: (r: Response) => void
    mockHttp(fixture(), url => url.includes('/projects/') ? new Promise(r => { resolve = r }) : undefined); mount()
    expect(screen.getByRole('status')).toHaveTextContent('Loading investigations')
    resolve(json({ items: [], next_cursor: null })); await screen.findByText('No investigations on this page')
    const fetch = vi.mocked(globalThis.fetch); fetch.mockImplementation(async () => json({ items: [], next_cursor: null }))
    await userEvent.click(screen.getByRole('button', { name: 'Refresh investigations' }))
    await screen.findByText('No investigations on this page')
    expect(screen.queryByText('Admission only')).not.toBeInTheDocument()
  })
  it.each([404, 409, 500])('handles HTTP %s without leaking server error bodies and recovers on retry', async status => {
    let failing = true; const f = fixture()
    mockHttp(f, url => url.endsWith('/version-1') && failing ? json({ detail: 'C:\\private\\secret' }, status) : undefined); mount()
    await screen.findByRole('alert'); expect(screen.queryByText('Admission only')).not.toBeInTheDocument()
    expect(document.body.textContent).not.toContain('secret')
    failing = false; await userEvent.click(screen.getByRole('button', { name: 'Retry investigation request' }))
    await screen.findByRole('link', { name: 'Download source' })
  })
  it.each(['detail', 'artifacts'])('clears prior detail and links after a %s 409 refresh', async endpoint => {
    const f = fixture(); let corrupt = false
    mockHttp(f, url => corrupt && url.endsWith(endpoint === 'detail' ? '/version-1' : '/artifacts') ? json({}, 409) : undefined)
    const { client } = mount(); await screen.findByRole('link', { name: 'Download source' })
    corrupt = true; await userEvent.click(screen.getByRole('button', { name: 'Refresh investigations' }))
    await screen.findByRole('alert')
    expect(screen.queryByText('Recorded context only')).not.toBeInTheDocument()
    expect(screen.queryByRole('link', { name: 'Download source' })).not.toBeInTheDocument()
    await waitFor(() => expect(client.getQueryCache().findAll().some(q => q.queryKey.includes('detail') && q.state.data !== undefined)).toBe(false))
  })
  it('rejects malformed or artifact-mismatched success responses', async () => {
    const f = fixture(); f.downloads.items[0].sha256 = digest('f'); mockHttp(f); mount()
    await screen.findByRole('alert'); expect(screen.queryByText('Admission only')).not.toBeInTheDocument()
  })
  it('switches versions and does not replace a missing historical selection with latest evidence', async () => {
    const f = fixture(), old = fixture('project-a', 'version-old', 2)
    mockHttp(f, url => url.includes('/versions?') ? json({ items: [f.wireSummary, old.wireSummary], next_cursor: null }) : url.endsWith('/version-old') ? json({}, 404) : undefined)
    mount(); await screen.findByRole('link', { name: 'Download source' })
    await userEvent.selectOptions(screen.getByLabelText('Investigation version'), 'version-old')
    await screen.findByRole('alert'); expect(screen.queryByRole('link', { name: 'Download source' })).not.toBeInTheDocument()
    expect(screen.getByLabelText('Investigation version')).toHaveValue('version-old')
    await userEvent.click(screen.getByRole('button', { name: 'Refresh investigations' }))
    await screen.findByRole('alert')
    expect(screen.getByLabelText('Investigation version')).toHaveValue('version-old')
    expect(screen.queryByRole('link', { name: 'Download source' })).not.toBeInTheDocument()
    await userEvent.selectOptions(screen.getByLabelText('Investigation version'), 'version-1')
    await screen.findByRole('link', { name: 'Download source' })
  })
  it('cancels or ignores an older project response after a project switch', async () => {
    const f = fixture(); let resolve!: (r: Response) => void; let oldSignal: AbortSignal | null | undefined
    mockHttp(f, (url, init) => url.endsWith('/version-1') ? new Promise(r => { resolve = r; oldSignal = init?.signal }) : url.startsWith('/api/projects/project-b/') ? json({ items: [], next_cursor: null }) : undefined)
    const view = mount(); await screen.findByText('Validating recorded version…')
    view.rerender(<EngineeringInvestigation projectId="project-b" />); await screen.findByText('No investigations on this page')
    resolve(json(f.detail)); await waitFor(() => expect(oldSignal?.aborted).toBe(true))
    expect(screen.queryByText('Admission only')).not.toBeInTheDocument()
  })
  it('ignores a late version response after another version is selected', async () => {
    const f = fixture(), other = fixture('project-a', 'version-2', 2)
    let resolve!: (r: Response) => void
    mockHttp(f, url => url.includes('/versions?') ? json({ items: [f.wireSummary, other.wireSummary], next_cursor: null }) : url.endsWith('/version-1') ? new Promise(r => { resolve = r }) : url.endsWith('/version-2') ? json(other.detail) : undefined)
    mount(); await screen.findByText('Validating recorded version…')
    await userEvent.selectOptions(screen.getByLabelText('Investigation version'), 'version-2')
    const link = await screen.findByRole('link', { name: 'Download source' })
    expect(link).toHaveAttribute('href', `/api/investigations/${f.investigationId}/versions/version-2/artifacts/opaque-3/download`)
    resolve(json(f.detail))
    await waitFor(() => expect(screen.getByLabelText('Investigation version')).toHaveValue('version-2'))
    expect(screen.getByRole('link', { name: 'Download source' })).toHaveAttribute('href', `/api/investigations/${f.investigationId}/versions/version-2/artifacts/opaque-3/download`)
  })
  it('requests opaque investigation and ordinal version pages without losing their boundaries', async () => {
    const f = fixture(); const fetch = mockHttp(f, url => url.includes('/projects/') ? json({ items: [f.item], next_cursor: url.includes('after=next-id') ? null : 'next-id' }) : url.includes('/versions?') ? json({ items: [f.wireSummary], next_cursor: url.includes('after=1&') ? null : 1 }) : undefined)
    mount(); await screen.findByRole('link', { name: 'Download source' })
    const versionPanel = screen.getByText('Immutable version history').closest('.panel')!
    await userEvent.click(within(versionPanel as HTMLElement).getByRole('button', { name: 'Next page' }))
    await waitFor(() => expect(fetch.mock.calls.some(([u]) => String(u).includes('/versions?after=1&limit=20'))).toBe(true))
    await screen.findByRole('link', { name: 'Download source' })
    const projectPanel = screen.getByText('Engineering investigations').closest('.panel')!
    await userEvent.click(within(projectPanel as HTMLElement).getByRole('button', { name: 'Next page' }))
    await waitFor(() => expect(fetch.mock.calls.some(([u]) => String(u).includes('investigations?after=next-id&limit=20'))).toBe(true))
  })
})

describe('bounded investigation client projections', () => {
  it.each([{ factIds: [] as string[] }, { factIds: ['fact-0'] }])('keeps an unresolved comparison with $factIds visible without fabricating a relation', async ({ factIds }) => {
    const f = fixture()
    f.detail.manifest.relations = []
    f.detail.bridge_report.comparisons = [{ binding_id: 'missing-native', fact_ids: factIds, value_comparison: 'inconclusive', verdict: 'inconclusive' }]
    Object.assign(f.detail.bridge_report.comparisons[0], { reasons: ['PRIVATE_UNRESOLVED_REASON C:\\private'] })
    const detail = parseDetail(f.detail, f.summary)
    expect(detail.relations).toEqual([])
    expect(detail.unresolvedComparisons).toEqual([{ bindingId: 'missing-native', facts: factIds }])
    mockHttp(f); mount()
    await screen.findByText('1 unresolved native comparisons')
    expect(screen.getByText('No relationships recorded.')).toBeVisible()
    expect(screen.getByText(/Value comparison and verdict remain inconclusive/)).toBeVisible()
    expect(document.body.textContent).not.toContain('PRIVATE_UNRESOLVED_REASON')
    expect(screen.queryByRole('article', { name: /Relationship/ })).not.toBeInTheDocument()
  })
  it('still rejects missing, extra and mismatched relationships for complete comparisons', () => {
    const f = fixture()
    expect(() => parseDetail({ ...f.detail, manifest: { ...f.detail.manifest, relations: [] } }, f.summary)).toThrow()
    expect(() => parseDetail({ ...f.detail, manifest: { ...f.detail.manifest, relations: [...f.detail.manifest.relations, { ...f.detail.manifest.relations[0], relation_id: 'relation-extra' }] } }, f.summary)).toThrow()
    expect(() => parseDetail({ ...f.detail, manifest: { ...f.detail.manifest, relations: f.detail.manifest.relations.map(r => ({ ...r, fact_ids: [...r.fact_ids].reverse() })) } }, f.summary)).toThrow()
    f.detail.manifest.relations = []
    f.detail.bridge_report.comparisons = [{ binding_id: 'bad-comparison', fact_ids: [], value_comparison: 'conflict', verdict: 'inconclusive' }]
    expect(() => parseDetail(f.detail, f.summary)).toThrow()
  })
  it.each(['revision', 'native'])('rejects coordinated immutable %s identity drift at an unchanged version hash', kind => {
    const f = fixture()
    if (kind === 'revision') {
      f.detail.revision_id = 'changed-revision'
      f.detail.envelope.record.revision_id = 'changed-revision'
      f.detail.project_revision.revision_id = 'changed-revision'
    } else {
      f.detail.native_investigation_id = 'changed-native'
      f.detail.envelope.record.investigation_id = 'changed-native'
    }
    expect(() => parseDetail(f.detail, f.summary)).toThrow()
  })
  it('fences the remaining immutable summary metadata before projection', () => {
    const f = fixture()
    expect(() => parseDetail({ ...f.detail, created_at: '2026-10-31T00:00:00Z' }, f.summary)).toThrow()
    expect(() => parseDetail(f.detail, { ...f.summary, domains: ['system'] })).toThrow()
  })
  it('rejects scope upgrades, wrong project/version, unknown enums and missing identity', () => {
    const f = fixture()
    for (const change of [{ execution_status: 'completed' }, { project_id: 'foreign' }, { id: 'other-version' }, { content_hash: digest('f') }, { domains: ['unknown'] }]) expect(() => parseDetail({ ...f.detail, ...change }, f.summary)).toThrow()
    expect(() => parseDetail({ ...f.detail, project_revision: { ...f.detail.project_revision, configuration_hash: digest('f') } }, f.summary)).toThrow()
    expect(() => parseDetail({ ...f.detail, envelope: { ...f.detail.envelope, new_execution: 'completed' } }, f.summary)).toThrow()
  })
  it('rejects duplicate download identities, unsafe sizes and project/ordinal pagination corruption', () => {
    const f = fixture(), detail = parseDetail(f.detail, f.summary)
    expect(() => parseArtifacts({ items: [...f.downloads.items, f.downloads.items[0]] }, detail)).toThrow()
    expect(() => parseArtifacts({ items: f.downloads.items.map(a => ({ ...a, size_bytes: Number.MAX_SAFE_INTEGER + 1 })) }, detail)).toThrow()
    expect(() => parseInvestigations({ items: [f.item], next_cursor: '/private/path' }, f.project)).toThrow()
    expect(() => parseVersions({ items: [f.wireSummary, f.wireSummary], next_cursor: null }, f.project, f.investigationId)).toThrow()
    expect(() => parseDetail({ ...f.detail, execution_contexts: [] }, f.summary)).toThrow()
  })
  it('caps streaming bytes and rejects malformed JSON without returning source text', async () => {
    vi.spyOn(globalThis, 'fetch').mockResolvedValue(new Response(' '.repeat(MAX_RESPONSE_BYTES + 1)))
    await expect(investigationGet('/test', v => v)).rejects.toThrow('invalid or inconsistent')
    vi.mocked(globalThis.fetch).mockResolvedValue(new Response('{"private":"C:\\private"'))
    await expect(investigationGet('/test', v => v)).rejects.toThrow('invalid or inconsistent')
  })
})
