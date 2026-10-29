import { useEffect, useId, useState } from 'react'
import { useQuery, useQueryClient } from '@tanstack/react-query'
import { humanize } from './api'
import { artifactHref, identifier, investigationGet, parseArtifacts, parseDetail, parseInvestigations, parseVersions, referenceKey, versionPath } from './engineeringInvestigationApi'
import type { Artifact, Investigation, InvestigationDetail, Reference, VersionSummary } from './engineeringInvestigationApi'

const styles = `.engineering-investigation{display:grid;gap:20px;min-width:0}.engineering-investigation .ei-heading{display:flex;justify-content:space-between;align-items:start;gap:16px;flex-wrap:wrap}.engineering-investigation .ei-grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(min(100%,240px),1fr));gap:18px}.engineering-investigation .ei-record{padding:16px 0;border-top:1px solid var(--line)}.engineering-investigation .ei-record:target{outline:2px solid var(--teal);outline-offset:4px}.engineering-investigation dd,.engineering-investigation a{overflow-wrap:anywhere}.engineering-investigation .ei-tags{display:flex;gap:8px;flex-wrap:wrap;margin:10px 0}.engineering-investigation .ei-links{display:flex;gap:12px;flex-wrap:wrap;padding-left:20px}.engineering-investigation .ei-download{display:inline-flex;align-items:center;min-height:44px}.engineering-investigation .artifact-list li{flex-wrap:wrap}.engineering-investigation .ei-record summary{overflow-wrap:anywhere}.engineering-investigation .ei-heading h2{margin-bottom:8px}.engineering-investigation .ei-pagination{margin-top:12px}`

function Failure({ error, retry }: { error: Error; retry: () => void }) {
  return <div role="alert" className="notice error"><p>{error.message}</p><button onClick={retry}>Retry investigation request</button></div>
}
function Loading({ label }: { label: string }) { return <p role="status" className="muted">{label}</p> }
function Digest({ label, value }: { label: string; value: string }) { return <div><dt>{label}</dt><dd><code>{value}</code></dd></div> }
function Paging({ previous, next, busy }: { previous?: () => void; next?: () => void; busy: boolean }) {
  return <div className="actions ei-pagination"><button disabled={!previous || busy} onClick={previous}>Previous page</button><button disabled={!next || busy} onClick={next}>Next page</button></div>
}

/** Mount under the existing QueryClientProvider. Project changes discard selection. */
export function EngineeringInvestigation({ projectId }: { projectId: string }) {
  try { identifier(projectId) } catch { return <div className="notice error" role="alert">Select an existing project to inspect engineering investigations.</div> }
  return <ProjectInvestigations key={projectId} projectId={projectId} />
}
function ProjectInvestigations({ projectId }: { projectId: string }) {
  const uid = useId(), client = useQueryClient()
  const base = ['engineering-investigations', projectId]
  const [pages, setPages] = useState(['']), [selectedId, setSelectedId] = useState('')
  const [version, setVersion] = useState<VersionSummary | null>(null)
  const after = pages.at(-1)!
  const query = useQuery({ queryKey: [...base, 'list', after], queryFn: ({ signal }) => investigationGet(`/projects/${encodeURIComponent(projectId)}/investigations?after=${encodeURIComponent(after)}&limit=20`, v => parseInvestigations(v, projectId), signal), retry: false })
  const selected = query.data?.items.find(item => item.id === selectedId) ?? (!selectedId ? query.data?.items[0] : undefined)
  function page(next?: string) { setSelectedId(''); setVersion(null); setPages(old => next === undefined ? old.slice(0, -1) : [...old, next]) }
  return <section className="engineering-investigation" aria-labelledby={`${uid}-heading`}>
    <style>{styles}</style>
    <div className="panel">
      <div className="ei-heading"><div><span className="eyebrow">Project evidence</span><h2 id={`${uid}-heading`}>Engineering investigations</h2><p className="muted">Recorded investigations for <strong>{projectId}</strong></p></div><button disabled={query.isFetching} onClick={() => { void client.invalidateQueries({ queryKey: base }) }}>Refresh investigations</button></div>
      <p className="notice">Read-only recorded evidence. Admission checks stored bytes and typed consistency; it does not run a design, establish correctness or validate a repair.</p>
      {query.isFetching || query.isPending ? <Loading label="Loading investigations…" /> : query.isError ? <Failure error={query.error} retry={() => void query.refetch()} /> : !query.data.items.length ? <div className="empty small"><h3>No investigations on this page</h3><p>This project has no recorded investigation here. Refresh after evidence has been admitted.</p></div> : <><label htmlFor={`${uid}-select`}>Recorded investigation</label><select id={`${uid}-select`} value={selected?.id ?? ''} onChange={event => { setSelectedId(event.target.value); setVersion(null) }}>{!selected && <option value="">Select an investigation</option>}{query.data.items.map(item => <option key={item.id} value={item.id}>{item.id} · latest v{item.latest.ordinal} · {item.latest.domains.join(', ')}</option>)}</select></>}
      <Paging busy={query.isFetching} previous={pages.length > 1 ? () => page() : undefined} next={query.data?.next && !query.isError ? () => page(query.data!.next!) : undefined} />
    </div>
    {!query.isFetching && !query.isError && selected ? <InvestigationVersions key={selected.id} investigation={selected} base={[...base, selected.id]} chosen={version?.investigationId === selected.id ? version : null} choose={setVersion} /> : null}
  </section>
}
function InvestigationVersions({ investigation, base, chosen, choose }: { investigation: Investigation; base: string[]; chosen: VersionSummary | null; choose: (version: VersionSummary) => void }) {
  const uid = useId(), [pages, setPages] = useState([0])
  const after = pages.at(-1)!, selected = chosen ?? investigation.latest
  const query = useQuery({ queryKey: [...base, 'versions', after], queryFn: ({ signal }) => investigationGet(`/investigations/${encodeURIComponent(investigation.id)}/versions?after=${after}&limit=20`, v => parseVersions(v, investigation.projectId, investigation.id), signal), retry: false })
  const options = [selected, ...(query.data?.items.filter(v => v.id !== selected.id) ?? [])]
  return <>
    <div className="panel">
      <h3>Immutable version history</h3><p className="muted">Latest recorded version: {investigation.latest.ordinal}. Selecting a version preserves its exact evidence identity.</p>
      {query.isFetching || query.isPending ? <Loading label="Loading versions…" /> : query.isError ? <Failure error={query.error} retry={() => void query.refetch()} /> : <><label htmlFor={`${uid}-version`}>Investigation version</label><select id={`${uid}-version`} value={selected.id} onChange={event => choose(options.find(v => v.id === event.target.value)!)}>{options.map(v => <option key={v.id} value={v.id}>Version {v.ordinal} · {v.createdAt} · {v.id}</option>)}</select>{!query.data.items.length && <p role="status">No versions on this page. Refresh the investigation list if the selected version is unavailable.</p>}</>}
      <Paging busy={query.isFetching} previous={pages.length > 1 ? () => setPages(old => old.slice(0, -1)) : undefined} next={query.data?.next && !query.isError ? () => setPages(old => [...old, query.data!.next!]) : undefined} />
    </div>
    {!query.isFetching && !query.isError ? <VersionDetail key={`${selected.id}:${selected.contentHash}`} selected={selected} base={[...base, selected.id]} /> : null}
  </>
}
function VersionDetail({ selected, base }: { selected: VersionSummary; base: string[] }) {
  const client = useQueryClient()
  const query = useQuery({ queryKey: [...base, 'detail', selected.contentHash], queryFn: async ({ signal }) => {
    const path = versionPath(selected.investigationId, selected.id)
    const detail = await investigationGet(path, v => parseDetail(v, selected), signal)
    const artifacts = await investigationGet(`${path}/artifacts`, v => parseArtifacts(v, detail), signal)
    return { detail, artifacts }
  }, retry: false })
  useEffect(() => {
    if (query.isError) {
      // Remove retained successful data on a failed refresh, including 409.
      client.removeQueries({ queryKey: [...base, 'artifacts'] })
      client.removeQueries({ queryKey: [...base, 'detail'] })
    }
  }, [query.isError, client, ...base]) // base is a fixed-length immutable route key.
  if (query.isFetching || query.isPending) return <div className="panel"><Loading label="Validating recorded version…" /></div>
  if (query.isError) return <Failure error={query.error} retry={() => void query.refetch()} />
  return <EvidenceDetail detail={query.data.detail} artifacts={query.data.artifacts} />
}
function EvidenceDetail({ detail, artifacts }: { detail: InvestigationDetail; artifacts: Artifact[] }) {
  const uid = useId(), factAnchor = (id: string) => `${uid}-fact-${id}`
  return <>
    <section className="panel" aria-labelledby={`${uid}-identity`}>
      <div className="ei-heading"><div><span className="eyebrow">Recorded version {detail.ordinal}</span><h3 id={`${uid}-identity`}>Source and configuration identity</h3></div><span className="tag">Recorded import</span></div>
      <div className="status-strip"><div className="status-cell"><span className="eyebrow">Operation</span><strong>Admission only</strong><code>admission_only</code></div><div className="status-cell"><span className="eyebrow">New execution</span><strong>Not run</strong><code>not_run</code></div></div>
      <dl className="ei-grid"><Digest label="Version ID" value={detail.id} /><Digest label="Native investigation ID" value={detail.nativeId} /><Digest label="Source revision" value={detail.revisionId} /><Digest label="Envelope SHA-256" value={detail.contentHash} /><Digest label="Project revision SHA-256" value={detail.revisionHash} /><Digest label="Configuration source SHA-256" value={detail.configurationHash} /><Digest label="Manifest SHA-256" value={detail.manifestHash} /></dl>
      <p className="muted">Configuration identity names retained source bytes, not executed tool settings. Manifest review: {detail.manifestReview}.</p>
      <details><summary>{detail.sources.length} retained project sources</summary><ul className="artifact-list">{detail.sources.map(source => <li key={referenceKey(source)}><div><strong>{source.artifactId}</strong><span>Revision: {source.revisionId}</span><code>{source.sha256}</code></div></li>)}</ul></details>
    </section>
    <section className="panel" aria-labelledby={`${uid}-relations`}>
      <h3 id={`${uid}-relations`}>System relationships</h3><p className="muted">Bridge: {humanize(detail.bridgeFamily)} · recorded source comparison</p><p className="muted">A relationship verdict covers its named facts and provenance. Consistency is not system correctness; common ancestry can leave a comparison inconclusive.</p>
      {!detail.relations.length && <p>No relationships recorded.</p>}
      {detail.unresolvedComparisons.length > 0 && <div className="notice"><strong>{detail.unresolvedComparisons.length} unresolved native comparisons</strong><p>Insufficient native facts to form relationships. Value comparison and verdict remain inconclusive.</p><ul>{detail.unresolvedComparisons.map(comparison => <li key={comparison.bindingId}><strong>{comparison.bindingId}</strong>: {comparison.facts.length} native facts available; relationship not formed.</li>)}</ul></div>}
      {detail.relations.map(relation => <article className="ei-record" key={relation.id} aria-label={`Relationship ${relation.id}`}><strong>{relation.id}</strong><div className="ei-tags"><span className="tag">{humanize(relation.kind)}</span><span className="tag">{relation.verdict}</span><span className="tag">{humanize(relation.basis)}</span><span className="tag">{humanize(relation.dependence)} ancestry</span></div><p className="muted">Native value comparison: {relation.comparison}. Relationship verdict: {relation.verdict}.</p><ul className="ei-links">{relation.facts.map(id => <li key={id}><a href={`#${factAnchor(id)}`}>{id}</a></li>)}</ul>{relation.requirements.length > 0 && <><p className="muted">Requirement context</p><ul className="ei-links">{relation.requirements.map(id => <li key={id}><a href={`#${factAnchor(id)}`}>{id}</a></li>)}</ul></>}<p className="muted">{relation.limitations} recorded limitations · {relation.evidence.length} evidence references. Detailed native evidence is available in the exact artifacts below.</p>{relation.checkerHash && <dl><Digest label="Relationship checker SHA-256" value={relation.checkerHash} /></dl>}</article>)}
    </section>
    <section className="panel" aria-labelledby={`${uid}-facts`}>
      <h3 id={`${uid}-facts`}>Native facts and provenance</h3><p className="muted">Source observations, requirements and hypotheses retain their separate scopes. Review status alone does not establish independent execution.</p>
      {detail.facts.map(fact => <details className="ei-record" id={factAnchor(fact.id)} key={fact.id}><summary>{fact.id} · {fact.domain} · {fact.category} · {fact.review}</summary><div className="ei-tags"><span className="tag">{fact.provenance} provenance</span><span className="tag">{fact.method ? humanize(fact.method) : 'No extraction identity'}</span></div><dl><Digest label="Native schema" value={fact.schema} /><Digest label="Native record SHA-256" value={fact.reference.sha256} />{fact.implementationHash && <Digest label="Extractor implementation SHA-256" value={fact.implementationHash} />}</dl><p className="muted">{fact.limitations} recorded conditions or uncertainties. Native payload text and source locations are not displayed.</p>{fact.parents.length > 0 && <><p>Derived from</p><ul className="ei-links">{fact.parents.map(id => <li key={id}><a href={`#${factAnchor(id)}`}>{id}</a></li>)}</ul></>}<ul className="artifact-list">{fact.sources.map((source, index) => <li key={`${referenceKey(source)}:${index}`}><div><strong>{source.artifactId}</strong><code>{source.sha256}</code></div></li>)}</ul></details>)}
    </section>
    <section className="panel" aria-labelledby={`${uid}-context`}>
      <h3 id={`${uid}-context`}>Execution associations</h3><p className="muted">Database associations describe previously recorded attempts. This investigation starts no new execution.</p>
      {!detail.contexts.length && <p>No campaign or attempt association recorded.</p>}
      {detail.contexts.map(context => <article className="ei-record" key={context.dbAttemptId}><strong>{context.origin === 'retained_execution_bytes' ? 'Retained execution bytes matched at admission' : 'Recorded context only'}</strong><p className="muted">{context.origin === 'retained_execution_bytes' ? 'The server matched retained execution bytes when admitting this association. This does not establish a new run or current tool readiness.' : 'The database association is recorded; retained execution-byte origin was not established.'}</p><dl className="ei-grid"><Digest label="DB campaign" value={context.campaignId} /><Digest label="DB attempt" value={context.dbAttemptId} /><Digest label="DB experiment record" value={context.experimentRecordId} /><Digest label="Native attempt" value={context.nativeAttemptId} /><Digest label="Wire experiment" value={context.wireExperimentId} /><Digest label="Result bytes SHA-256" value={context.result.sha256} /></dl></article>)}
    </section>
    <ArtifactDownloads detail={detail} artifacts={artifacts} />
  </>
}
function ArtifactDownloads({ detail, artifacts }: { detail: InvestigationDetail; artifacts: Artifact[] }) {
  const uid = useId()
  function row(artifact: Artifact) { return <li key={artifact.id}><div><strong>{artifact.artifactId}</strong><span className="muted">{artifact.roles.map(humanize).join(', ')} · {artifact.size.toLocaleString()} bytes</span><span className="muted">Revision: {artifact.revisionId}{artifact.attemptId ? ` · Native attempt: ${artifact.attemptId}` : ''}</span><code>{artifact.sha256}</code></div><a className="ei-download" href={artifactHref(detail, artifact)} download={`${artifact.sha256}.bin`} aria-label={`Download ${artifact.artifactId}`}>Download exact bytes</a></li> }
  return <section className="panel" aria-labelledby={`${uid}-artifacts`}><h3 id={`${uid}-artifacts`}>Exact version artifacts</h3><p className="muted">{detail.artifacts.length} admitted artifacts · {detail.totalBytes.toLocaleString()} bytes. Each link identifies this version and its stored artifact; the server revalidates bytes on download.</p><ul className="artifact-list">{artifacts.map(row)}</ul></section>
}
