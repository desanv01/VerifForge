import { useId, useState } from 'react'
import { humanize } from './api'
import { artifactHref, referenceKey } from './engineeringInvestigationApi'
import type { Artifact, EdaInvestigationDetail, EdaRunView, Reference } from './engineeringInvestigationApi'
import { ArtifactPreview } from './RecordedArtifactPreview'

export function EdaInvestigation({ detail, artifacts }: { detail: EdaInvestigationDetail; artifacts: Artifact[] }) {
  const uid=useId(),[artifactId,setArtifactId]=useState('')
  const selected=artifacts.find(a=>a.id===artifactId)??artifacts.find(a=>referenceKey(a)===referenceKey(detail.requirements)&&a.sha256===detail.requirements.sha256)??artifacts[0]
  const download=(ref:Reference)=>{
    const found=artifacts.find(a=>referenceKey(a)===referenceKey(ref)&&a.sha256===ref.sha256)
    return found?<a className="ei-download" href={artifactHref(detail,found)} download={`${found.sha256}.bin`}>Download {ref.artifactId}</a>:<span>Exact admitted artifact unavailable</span>
  }
  function run(label:string,run:EdaRunView) { return <section className="panel" aria-label={`${label} EDA source associations`}>
    <h3>{label} source and report associations</h3><p>Revision <code>{run.revisionId}</code>. These are host-pinned recorded associations; no native execution or cleanup is authenticated by this view.</p>
    <dl className="ei-grid"><div><dt>Configuration SHA-256</dt><dd><code>{run.configurationHash}</code>{download(run.configuration)}</dd></div><div><dt>Recorded receipt SHA-256</dt><dd><code>{run.receipt.sha256}</code>{download(run.receipt)}</dd></div></dl>
    <details><summary>{run.sources.length} exact source identities</summary><ul className="artifact-list">{run.sources.map(source=><li key={source.name}><div><strong>{source.name}</strong><code>{source.reference.sha256}</code></div>{download(source.reference)}</li>)}</ul></details>
    <div className="table-scroll" tabIndex={0} role="region" aria-label={`${label} EDA report bindings`}><table><caption>Reports and their exact source associations</caption><thead><tr><th scope="col">Report</th><th scope="col">Raw bytes SHA-256</th><th scope="col">Bound source artifacts</th><th scope="col">Evidence</th></tr></thead><tbody>{run.reports.map(report=><tr key={report.name}><th scope="row">{report.name}</th><td><code>{report.reference.sha256}</code></td><td>{report.sources.map(source=><div key={referenceKey(source)}>{source.artifactId}<code>{source.sha256}</code></div>)}</td><td>{download(report.reference)}</td></tr>)}</tbody></table></div>
  </section> }
  return <>
    <section className="panel" aria-label="Recorded EDA evidence"><span className="eyebrow">Immutable EDA version {detail.ordinal}</span><h3>{detail.domain==='vivado'?'Vivado':'KiCad'} recorded investigation</h3>
      <div className="status-strip"><div className="status-cell"><span className="eyebrow">Execution observation</span><strong>Not observed</strong></div><div className="status-cell"><span className="eyebrow">Design verdict</span><strong>Inconclusive</strong></div></div>
      <p>Recorded byte admission only. Clean reports do not establish functional, physical or electrical correctness. Loading domain interpretation starts no tool, model, repair or validation run.</p>
      <dl className="ei-grid">{Object.entries({Version:detail.id,Envelope:detail.contentHash,Record:detail.recordHash,'Project revision':detail.revisionHash,'Host-installed checker':detail.checkerHash,'Requirements':detail.requirements.sha256}).map(([name,value])=><div key={name}><dt>{name} identity</dt><dd><code>{value}</code></dd></div>)}</dl>
      <p>{download(detail.requirements)}</p><p className="muted">Requirements identity is retained separately from source/configuration identity. Host-pinned authority is not remote execution attestation.</p>
    </section>
    {run('Candidate',detail.candidate)}
    {detail.baseline?run('Baseline',detail.baseline):<section className="panel"><h3>Baseline and candidate lineage</h3><p>No baseline is admitted for this version. No before/after improvement or repair is inferred.</p></section>}
    <section className="panel" aria-label="EDA artifact inspector"><h3>Exact EDA artifact inspector</h3><p>{artifacts.length} artifacts · {detail.totalBytes.toLocaleString()} bytes. Only this immutable version’s admitted references are selectable. Binary formats are downloads; text previews never execute or render source.</p>
      {selected?<><label htmlFor={`${uid}-artifact`}>Recorded EDA artifact</label><select id={`${uid}-artifact`} value={selected.id} onChange={event=>setArtifactId(event.target.value)}>{artifacts.map(a=><option key={a.id} value={a.id}>{a.artifactId} · {a.size} bytes</option>)}</select><p><code>{selected.sha256}</code> · revision {selected.revisionId} · {selected.roles.map(humanize).join(', ')}</p>{download(selected)}<ArtifactPreview detail={detail} artifact={selected} label="Preview recorded EDA text"/></>:<p role="status">No admitted artifact is available.</p>}
    </section>
  </>
}
