import { useId, useState } from 'react'
import { useQuery } from '@tanstack/react-query'
import { humanize } from './api'
import { artifactHref, investigationGet, versionPath } from './engineeringInvestigationApi'
import type { Artifact, VersionSummary } from './engineeringInvestigationApi'
import { parseProjection } from './investigationProjectionApi'
import type { DomainProjection } from './investigationProjectionApi'
import { FamilyInvestigationProjection } from './FamilyInvestigationProjection'
import { SystemInvestigationProjection } from './SystemInvestigationProjection'

function Digest({ label, value }: { label: string; value: string | null }) { return <div><dt>{label}</dt><dd>{value ? <code>{value}</code> : 'Not recorded'}</dd></div> }
function ExactArtifact({ selected, artifacts, sha256, id }: { selected: VersionSummary; artifacts: Artifact[]; sha256: string | null; id?: string }) {
  const matches = artifacts.filter(a => a.sha256 === sha256 && (!id || a.artifactId === id))
  return matches.length === 1 ? <a className="ei-download" href={artifactHref(selected, matches[0])} download={`${matches[0].sha256}.bin`}>Download exact artifact</a> : <span className="muted">No unique admitted artifact match</span>
}

/** Optional read-only projection; never guesses a result from an unavailable endpoint. */
export function InvestigationProjection({ selected, artifacts }: { selected: VersionSummary; artifacts: Artifact[] }) {
  const [requested, setRequested] = useState(false), uid = useId()
  const query = useQuery({ queryKey: ['investigation-projection', selected.projectId, selected.investigationId, selected.id, selected.contentHash, uid], queryFn: ({ signal }) => investigationGet(`${versionPath(selected.investigationId, selected.id)}/projection`, v => parseProjection(v, selected), signal), enabled: requested, retry: false, staleTime: 0, gcTime: 0, refetchOnMount: 'always' })
  return <section className="panel" aria-labelledby={`${uid}-projection`}>
    <span className="eyebrow">Domain investigation</span><h3 id={`${uid}-projection`}>Reports, mappings and validation context</h3>
    <p>Load typed projections from this exact recorded version. Interpretation starts no engineering tool or validation run.</p>
    <button disabled={query.isFetching} onClick={() => { if (!requested) setRequested(true); else void query.refetch() }}>{query.isFetching ? 'Validating domain projections…' : requested ? 'Refresh connected domain views' : 'Load connected domain views'}</button>
    {requested && query.isFetching ? <p role="status">Revalidating immutable version and domain schemas…</p> : null}
    {requested && !query.isFetching && query.isError ? <p role="status" className="notice">Connected domain projections are unavailable or failed identity/schema validation. No projection result is displayed. Existing admitted evidence remains available.</p> : null}
    {requested && !query.isFetching && query.isSuccess ? <>
      {query.data.gaps.length ? <div className="notice"><strong>Recorded projection gaps</strong><ul>{query.data.gaps.map((gap, i) => <li key={i}>{gap}</li>)}</ul></div> : null}
      {!query.data.projections.length ? <p role="status">No supported domain projections were provided for this version.</p> : null}
      {query.data.projections.map(projection => <DomainView key={projection.domain} projection={projection} selected={selected} artifacts={artifacts}/>)}
    </> : null}
  </section>
}

function DomainView({ projection: p, selected, artifacts }: { projection: DomainProjection; selected: VersionSummary; artifacts: Artifact[] }) {
  const [shown, setShown] = useState(50)
  if ('cases' in p) return <FamilyInvestigationProjection projection={p} selected={selected} artifacts={artifacts}/>
  if (p.domain === 'system') return <SystemInvestigationProjection projection={p}/>
  if (p.domain === 'vivado') return <article className="ei-record" aria-label="Vivado report investigation">
    <h4>Vivado · {humanize(p.phase)}</h4>
    <div className="status-strip"><div className="status-cell"><span className="eyebrow">Recorded execution</span><strong>{humanize(p.execution.status)}</strong></div><div className="status-cell"><span className="eyebrow">Design verdict</span><strong>{humanize(p.verdict)}</strong></div></div>
    <p>{p.project.top} · {p.project.part} · clock requirement {p.project.clockPeriod} ns · Vivado {p.tool ?? 'not recorded'}. {p.complete ? 'Required report set present.' : 'Required report set incomplete.'}</p>
    <dl className="ei-grid"><Digest label="Investigation SHA-256" value={p.identity}/><Digest label="Parent investigation" value={p.parent}/><Digest label="Native receipt" value={p.execution.receipt}/><Digest label="Recipe" value={p.project.recipe}/><Digest label="Independent functional specification" value={p.project.specification}/></dl>
    <h4>Timing requirements and coverage</h4><p>Missing input delays: {p.missingInput ?? 'Unknown'} · missing output delays: {p.missingOutput ?? 'Unknown'} · methodology missing-I/O count: {p.methodologyMissingIo ?? 'Unknown'}.</p>
    <dl className="ei-grid">{p.timing.map(metric => <div key={metric.name}><dt>{humanize(metric.name)}</dt><dd>{metric.value} ns</dd></div>)}</dl>
    {!p.timing.length ? <p>No comparable internal timing metrics were interpreted.</p> : null}
    <p>Fully routed nets: {p.routed ?? 'Unknown'} · routing errors: {p.routingErrors ?? 'Unknown'} · DRC reported checks: {p.drcChecks ?? 'Unknown'}.</p>
    <p className="notice">Reported internal timing and build completion do not establish external I/O timing, CDC, reset behavior, functional correctness or physical acceptance. Functional: {humanize(p.functional)}; CDC: {humanize(p.cdc)}; repair: {humanize(p.repair)}.</p>
    <h4>Candidate comparison under unchanged requirements</h4>{p.candidate ? <><p>{humanize(p.candidate.status)} · requirements {p.candidate.unchanged ? 'unchanged' : 'changed'} · repair not validated.</p><dl className="ei-grid"><Digest label="Baseline investigation" value={p.candidate.baseline}/><Digest label="Candidate investigation" value={p.candidate.candidate}/><Digest label="Comparison" value={p.candidate.identity}/></dl><p>Changed sources: {p.candidate.changed.join(', ') || 'None recorded'}.</p><p>Required rechecks: {p.candidate.rechecks.join(', ') || 'None provided'}.</p><p>External validation required: {p.candidate.external.map(humanize).join(', ') || 'None provided'}.</p><ul>{p.candidate.blockers.map(reason => <li key={reason}>{humanize(reason)}</li>)}</ul></> : <p>No candidate comparison is bound to this report projection.</p>}
    <ul>{p.findings.map((f, i) => <li key={i}><strong>{humanize(f.code)}</strong> · {f.severity} · {humanize(f.scope)}{f.reports.length ? ` · ${f.reports.join(', ')}` : ''}</li>)}</ul>
    <div className="table-scroll" tabIndex={0} role="region" aria-label="Vivado report identities"><table><caption>Exact report availability and design phase</caption><thead><tr><th scope="col">Report</th><th scope="col">Availability</th><th scope="col">Phase</th><th scope="col">Evidence</th></tr></thead><tbody>{p.reports.map(r => <tr key={r.name}><th scope="row">{humanize(r.name)}</th><td>{humanize(r.availability)}</td><td>{r.phase ?? 'Unknown'}</td><td>{r.sha256 ? <><code>{r.sha256}</code><ExactArtifact selected={selected} artifacts={artifacts} sha256={r.sha256}/></> : 'Not recorded'}</td></tr>)}</tbody></table></div>
    <details><summary>Source identities</summary><dl>{p.project.sources.map(s => <Digest key={s.name} label={s.name} value={s.sha256}/>)}</dl></details>
  </article>
  if(p.partial) return <article className="ei-record" aria-label="Partial KiCad report investigation"><h4>KiCad · configured recorded reports</h4><p className="notice">Reviewed mapping assertions are missing. Mapping remains inconclusive. No pin/pad/net rows or schematic/PCB view identities are provided by this projection.</p><p>Execution not observed · physical/electrical verdict inconclusive. Clean configured reports do not establish functional, physical or electrical correctness.</p><ul className="artifact-list">{p.reports.map(r=><li key={r.kind}><div><strong>{r.kind.toUpperCase()} · {humanize(r.status)}</strong><code>{r.sha256}</code><span>Ignored rules: {r.ignored.join(', ')||'None recorded'}</span></div><ExactArtifact selected={selected} artifacts={artifacts} sha256={r.sha256}/></li>)}</ul><h4>Configured report comparisons</h4>{p.comparisons.length?p.comparisons.map((c,i)=><details key={i}><summary>{humanize(c.status)} · {humanize(c.beforeStatus)} → {humanize(c.candidateStatus)}</summary><p>{c.reasons.map(humanize).join(', ')||'Configured observation scopes comparable.'}</p><dl className="ei-grid"><Digest label="Before report" value={c.beforeReport}/><Digest label="Candidate report" value={c.candidateReport}/><Digest label="Before configuration" value={c.beforeConfiguration}/><Digest label="Candidate configuration" value={c.candidateConfiguration}/></dl><p>Ignored rules: before {c.ignoredBefore.join(', ')||'None recorded'}; candidate {c.ignoredAfter.join(', ')||'None recorded'}.</p>{(['introduced','resolved','retained'] as const).map(kind=><div key={kind}><h5>{humanize(kind)} configured findings</h5><ul>{c[kind].map((f,j)=><li key={j}>{f.rule} · {f.severity} · {f.excluded?'Excluded':'Included'} · count {f.count}</li>)}</ul></div>)}</details>):<p>No baseline/candidate comparison is provided.</p>}</article>
  return <article className="ei-record" aria-label="KiCad mapping investigation">
    <h4>KiCad · selected pin / pad / net mapping</h4><p>Mapping: {humanize(p.status)}. Execution not observed · physical/electrical verdict inconclusive · repair acceptance not established.</p>
    <dl className="ei-grid"><Digest label="Schematic bytes" value={p.schematic}/><Digest label="Board bytes" value={p.board}/></dl>
    <div className="table-scroll" tabIndex={0} role="region" aria-label="KiCad mapping rows"><table><caption>Reviewed mapping requirements · {p.rows.length} rows</caption><thead><tr><th scope="col">Reference</th><th scope="col">Symbol pin</th><th scope="col">Footprint pad</th><th scope="col">Net</th><th scope="col">Scoped result</th><th scope="col">Requirement</th></tr></thead><tbody>{p.rows.slice(0, shown).map((r, i) => <tr key={i}><th scope="row">{r.reference}</th><td>{r.pin}</td><td>{r.pad}</td><td>{r.net}</td><td>{humanize(r.status)}{r.reasons.length ? <ul>{r.reasons.map(reason => <li key={reason}>{humanize(reason)}</li>)}</ul> : null}</td><td><code>{r.requirement}</code></td></tr>)}</tbody></table></div>
    {shown < p.rows.length ? <button onClick={() => setShown(n => n + 50)}>Show next 50 mapping rows</button> : null}
    {p.unexpectedPins.length || p.unexpectedPads.length ? <p className="notice">Unexpected symbol pins: {p.unexpectedPins.map(r => r.join(':')).join(', ') || 'None'} · unexpected footprint pads: {p.unexpectedPads.map(r => r.join(':')).join(', ') || 'None'}.</p> : null}
    <h4>Configured ERC / DRC comparison</h4>
    {p.missingComparisons.length ? <p>Missing comparisons: {p.missingComparisons.join(', ')}.</p> : null}
    {p.comparisons.map((c, i) => <details className="ei-record" key={i}><summary>{humanize(c.status)} · {humanize(c.beforeStatus)} → {humanize(c.candidateStatus)}</summary>
      <p>{c.reasons.map(humanize).join(', ') || 'Configured observation scopes comparable.'}</p><p>Ignored rules before: {c.ignoredBefore.join(', ') || 'None recorded'}; candidate: {c.ignoredAfter.join(', ') || 'None recorded'}.</p>
      <dl className="ei-grid"><Digest label="Before source" value={c.beforeSource}/><Digest label="Candidate source" value={c.candidateSource}/><Digest label="Before report" value={c.beforeReport}/><Digest label="Candidate report" value={c.candidateReport}/><Digest label="Before configuration" value={c.beforeConfiguration}/><Digest label="Candidate configuration" value={c.candidateConfiguration}/></dl>
      {(['introduced', 'resolved', 'retained'] as const).map(kind => <div key={kind}><h5>{humanize(kind)} configured findings</h5><ul>{c[kind].map((f, j) => <li key={j}>{f.rule} · {f.severity} · {f.excluded ? 'Excluded' : 'Included'} · count {f.count}<code>{f.signature}</code></li>)}</ul>{!c[kind].length ? <p>No {kind} findings provided.</p> : null}</div>)}
    </details>)}
    <h4>Schematic / PCB view evidence</h4><p>Downloads only. View indexing is not visual review; unsanitized SVG is never embedded.</p>
    {[p.schematicViews, p.pcbViews].map((index, i) => <div key={i}><p>Missing views: {index.missing.join(', ') || 'None recorded'}.</p><ul className="artifact-list">{index.entries.map(v => <li key={v.id}><div><strong>{humanize(v.kind)} · {humanize(v.freshness)}</strong><code>{v.sha256}</code><span>Visual review {humanize(v.review)} · source {v.source}</span></div><ExactArtifact selected={selected} artifacts={artifacts} sha256={v.sha256} id={v.id}/></li>)}</ul></div>)}
    <p className="muted">CAD/generated XML may share ancestry. Mapping and configured checks do not establish routing continuity, exact physical assembly, electrical ratings or board/cable compatibility.</p>
  </article>
}
