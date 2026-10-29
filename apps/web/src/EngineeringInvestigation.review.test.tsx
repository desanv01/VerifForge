// Independent synthetic metadata/jsdom review. No browser, backend, DB or tools.
import { cleanup, render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { afterEach, expect, it, vi } from 'vitest'
import { EngineeringInvestigation } from './EngineeringInvestigation'
import { artifactHref, parseArtifacts, parseDetail, parseInvestigations, parseVersions } from './engineeringInvestigationApi'

const sha = (c: string) => c.repeat(64)
const ref = (id: string, hash: string, attempt: string | null = null) => ({ artifact_id: id, revision_id: 'native-v1', attempt_id: attempt, sha256: sha(hash) })
function fixture(project = 'review-project', version = 'review-v1', ordinal = 1) {
  const refs = [ref('project', 'a'), ref('manifest', 'b'), ref('bridge', 'c'), ref('private-source', 'd'), ref('input-header', 'e'), ref('input-rtl', 'f'), ref('intent-row', '1'), ref('native-result', '2', 'native-attempt')]
  const facts = refs.slice(4, 7).map((r, index) => ({ fact_id: `fact-${index}`, domain: 'system', native_schema_id: index === 2 ? 'IntentObservation.v1' : 'RootObservation.v1', native_record_hash: r.sha256, native_record: r, category: index === 2 ? 'requirement' : 'observation', review_state: 'unreviewed', conditions: ['PRIVATE_NATIVE_TEXT'], provenance: { state: 'complete', sources: [{ artifact: refs[3] }], extraction: { method: 'native_parser', implementation_hash: sha('c') }, derived_from: [], uncertainties: [] } }))
  const artifacts = refs.map((reference, index) => ({ reference, size_bytes: index + 1, roles: [index === 7 ? 'attempt_result' : index > 3 ? 'native_fact' : ['project_revision', 'manifest', 'bridge_report', 'project_source'][index]] }))
  const summary = { id: version, investigation_id: 'review-investigation', project_id: project, ordinal, content_hash: sha(ordinal === 1 ? 'a' : 'b'), revision_id: 'source-v1', revision_hash: refs[0].sha256, manifest_hash: refs[1].sha256, created_at: '2026-10-30T00:00:00Z', domains: ['system'], native_investigation_id: 'native-investigation', schema_version: 'engineering.admission.v1', scope: 'recorded_import', execution_status: 'not_run' }
  const binding = { campaign_id: 'db-campaign', db_attempt_id: 'db-attempt', experiment_record_id: 'db-experiment', native_attempt_id: 'native-attempt', wire_experiment_id: 'wire-experiment', result_ref: refs[7] }
  const detail = { ...summary, envelope: { schema_version: 'engineering.admission.v1', operation: 'admission_only', new_execution: 'not_run', claim_scope: 'resolved_bytes_and_typed_consistency_only', attempt_context_scope: 'host_pinned_association_not_execution_origin', record_hash: sha('a'), record: { schema_version: 'engineering.investigation.v1', investigation_id: 'native-investigation', project_id: project, revision_id: 'source-v1', project_revision_ref: refs[0], manifest_ref: refs[1], bridge_report_ref: refs[2], native_records: facts.map(f => ({ fact_id: f.fact_id, domain: f.domain, native_schema_id: f.native_schema_id, reference: f.native_record })), attempts: [binding] }, artifacts, total_artifact_bytes: artifacts.reduce((n, a) => n + a.size_bytes, 0) }, project_revision: { schema_version: 'engineering.project-revision.v1', project_id: project, revision_id: 'source-v1', source_refs: [refs[3]], configuration_hash: refs[3].sha256 }, manifest: { schema_version: 'engineering.manifest.v1', review_state: 'draft', facts, relations: [] }, bridge_report: { schema_version: 'system.bridge.v1', execution_status: 'not_run', family: 'firmware_decode', checker_hash: sha('c'), comparisons: [] as Array<{ binding_id: string; fact_ids: string[]; value_comparison: string; verdict: string }> }, execution_contexts: [{ binding, origin_at_admission: 'context_only' }] }
  const item = { id: summary.investigation_id, project_id: project, created_at: summary.created_at, latest_version: summary }
  const downloads = { items: artifacts.map((a, i) => ({ id: `opaque-${i}`, ...a.reference, size_bytes: a.size_bytes, roles: a.roles, media_type: 'application/octet-stream' })) }
  const selected = parseInvestigations({ items: [item], next_cursor: null }, project).items[0].latest
  return { project, summary, selected, detail, item, downloads }
}
const json = (data: unknown, status = 200) => new Response(JSON.stringify(data), { status })
function mount(project = 'review-project') {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false, gcTime: 0 } } })
  const wrapper = ({ children }: { children: React.ReactNode }) => <QueryClientProvider client={client}>{children}</QueryClientProvider>
  return { client, ...render(<EngineeringInvestigation projectId={project} />, { wrapper }) }
}
function mock(f = fixture(), custom?: (url: string) => Response | Promise<Response> | undefined) {
  return vi.spyOn(globalThis, 'fetch').mockImplementation(async input => {
    const url = String(input), override = custom?.(url)
    if (override) return override
    if (url.startsWith(`/api/projects/${f.project}/`)) return json({ items: [f.item], next_cursor: null })
    if (url.includes('/versions?')) return json({ items: [f.summary], next_cursor: null })
    if (url.endsWith('/artifacts')) return json(f.downloads)
    return json(f.detail)
  })
}
afterEach(() => { cleanup(); vi.restoreAllMocks() })

it('projects safe accessible admission/context labels and opaque artifact URLs', async () => {
  const f = fixture(); f.detail.execution_contexts[0].origin_at_admission = 'retained_execution_bytes'
  mock(f); mount()
  const link = await screen.findByRole('link', { name: 'Download private-source' })
  expect(link).toHaveAttribute('href', '/api/investigations/review-investigation/versions/review-v1/artifacts/opaque-3/download')
  expect(link).toHaveAttribute('download', `${sha('d')}.bin`)
  expect(screen.getByLabelText('Recorded investigation')).toBeEnabled()
  expect(screen.getByLabelText('Investigation version')).toBeEnabled()
  expect(screen.getByText('Admission only')).toBeVisible()
  expect(screen.getByText('Not run')).toBeVisible()
  expect(screen.getByText(/does not establish a new run or current tool readiness/)).toBeVisible()
  expect(document.body.textContent).not.toContain('PRIVATE_NATIVE_TEXT')
})

it('refuses foreign project and unsafe download metadata', () => {
  const f = fixture(), detail = parseDetail(f.detail, f.selected)
  expect(() => parseInvestigations({ items: [f.item], next_cursor: null }, 'foreign')).toThrow()
  expect(() => parseVersions({ items: [f.summary], next_cursor: null }, f.project, 'foreign')).toThrow()
  expect(() => parseArtifacts({ items: f.downloads.items.map(a => ({ ...a, id: 'https://private/redirect' })) }, detail)).toThrow()
  expect(() => artifactHref(f.selected, { ...parseArtifacts(f.downloads, detail)[0], id: '../escape' })).toThrow()
})

it('clears cached evidence and links on artifact409 and provides safe retry', async () => {
  let corrupt = false
  mock(fixture(), url => corrupt && url.endsWith('/artifacts') ? json({ detail: 'PRIVATE_SERVER_PATH' }, 409) : undefined)
  const { client } = mount()
  await screen.findByRole('link', { name: 'Download private-source' })
  corrupt = true
  await userEvent.click(screen.getByRole('button', { name: 'Refresh investigations' }))
  await screen.findByRole('alert')
  expect(screen.queryByText('Admission only')).not.toBeInTheDocument()
  expect(screen.queryByRole('link', { name: 'Download private-source' })).not.toBeInTheDocument()
  expect(document.body.textContent).not.toContain('PRIVATE_SERVER_PATH')
  expect(client.getQueryCache().findAll({ queryKey: ['engineering-investigations', 'review-project', 'review-investigation', 'review-v1', 'detail'] }).some(q => q.state.data !== undefined)).toBe(false)
  corrupt = false
  await userEvent.click(screen.getByRole('button', { name: 'Retry investigation request' }))
  await screen.findByRole('link', { name: 'Download private-source' })
})

it('ignores a late response after another immutable version is selected', async () => {
  const first = fixture(), second = fixture('review-project', 'review-v2', 2)
  let resolve!: (response: Response) => void
  mock(first, url => url.includes('/versions?') ? json({ items: [first.summary, second.summary], next_cursor: null }) : url.endsWith('/review-v1') ? new Promise(r => { resolve = r }) : url.endsWith('/review-v2') ? json(second.detail) : undefined)
  mount()
  await screen.findByText('Validating recorded version…')
  await userEvent.selectOptions(screen.getByLabelText('Investigation version'), 'review-v2')
  expect(await screen.findByRole('link', { name: 'Download private-source' })).toHaveAttribute('href', '/api/investigations/review-investigation/versions/review-v2/artifacts/opaque-3/download')
  resolve(json(first.detail))
  await waitFor(() => expect(screen.getByLabelText('Investigation version')).toHaveValue('review-v2'))
  expect(screen.getByRole('link', { name: 'Download private-source' })).toHaveAttribute('href', '/api/investigations/review-investigation/versions/review-v2/artifacts/opaque-3/download')
})

it('accepts an inconclusive comparison with no native observation relationship', () => {
  const f = fixture()
  // Bridge.compare emits a comparison even when extraction yields <2 facts;
  // manifest relationships exist only for comparisons with >=2 observations.
  f.detail.bridge_report.comparisons = [{ binding_id: 'unsupported-native', fact_ids: [], value_comparison: 'inconclusive', verdict: 'inconclusive' }]
  expect(() => parseDetail(f.detail, f.selected)).not.toThrow()
})

it.each(['revision', 'native'])('rejects selected/detail %s identity drift even if other summary fields match', kind => {
  const f = fixture()
  if (kind === 'revision') {
    f.detail.revision_id = 'changed-source-revision'
    f.detail.envelope.record.revision_id = 'changed-source-revision'
    f.detail.project_revision.revision_id = 'changed-source-revision'
  } else {
    f.detail.native_investigation_id = 'changed-native-investigation'
    f.detail.envelope.record.investigation_id = 'changed-native-investigation'
  }
  expect(() => parseDetail(f.detail, f.selected)).toThrow()
})

it.each([{ facts: [] as string[] }, { facts: ['fact-0'] }])('renders incomplete comparison $facts as unresolved without inventing a relationship', async ({ facts }) => {
  const f = fixture()
  f.detail.bridge_report.comparisons = [{ binding_id: 'partial-native', fact_ids: facts, value_comparison: 'inconclusive', verdict: 'inconclusive' }]
  mock(f); mount()
  await screen.findByText('1 unresolved native comparisons')
  expect(screen.getByText('No relationships recorded.')).toBeVisible()
  expect(screen.getByText(/Value comparison and verdict remain inconclusive/)).toBeVisible()
  expect(screen.queryByRole('article', { name: /Relationship/ })).not.toBeInTheDocument()
  expect(screen.getByText('Not run')).toBeVisible()
})

it('does not weaken relationship coverage or promote incomplete comparisons', () => {
  const f = fixture()
  f.detail.bridge_report.comparisons = [{ binding_id: 'complete-native', fact_ids: ['fact-0', 'fact-1'], value_comparison: 'inconclusive', verdict: 'inconclusive' }]
  expect(() => parseDetail(f.detail, f.selected)).toThrow()
  f.detail.bridge_report.comparisons = [{ binding_id: 'partial-native', fact_ids: ['fact-0'], value_comparison: 'consistent', verdict: 'consistent' }]
  expect(() => parseDetail(f.detail, f.selected)).toThrow()
})

it.each(['createdAt', 'domains'])('fences selected immutable %s metadata', field => {
  const f = fixture()
  const selected = field === 'createdAt' ? { ...f.selected, createdAt: '2026-10-31T00:00:00Z' } : { ...f.selected, domains: ['rtl'] }
  expect(() => parseDetail(f.detail, selected)).toThrow()
})
