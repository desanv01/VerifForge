import { humanize } from './api'
import type { parseRepairReport } from './repairApi'

type Report=ReturnType<typeof parseRepairReport>
export function RepairReportDetails({report,kind}:{report:Report;kind:'candidate'|'reduction'}){
  const reduction=report.reduction,candidate=report.candidate
  return <div aria-label="Settled repair report"><h4>Settled report</h4><code>{report.sha256}</code>
    <p>{kind==='reduction'?report.improved?'Server-accepted failure-preserving progress was recorded; minimality is not guaranteed.':'No accepted reduction progress was recorded; minimality is not guaranteed.':'Candidate validation remains scoped to the exact recorded checks.'}</p>
    {reduction?<>
      <h4>Original and reduced witness</h4><p>Submitted row counts are not expanded stimulus cycles. Observed cycles below belong to the recorded executions; they are not a new measurement by this view.</p>
      <div className="table-scroll" tabIndex={0} role="region" aria-label="Reduction witness comparison"><table><caption>Exact preserved scope and recorded outcomes</caption><thead><tr><th scope="col">Quantity</th><th scope="col">Original</th><th scope="col">Reduced</th></tr></thead><tbody>
        <tr><th scope="row">Experiment</th><td>{reduction.originalPlan.id}</td><td>{reduction.reducedPlan.id}</td></tr>
        <tr><th scope="row">Attempt</th><td>{reduction.originalResult.attempt}</td><td>{reduction.reducedResult.attempt}</td></tr>
        <tr><th scope="row">Submitted {reduction.originalPlan.unit}</th><td>{reduction.originalPlan.rows}</td><td>{reduction.reducedPlan.rows}</td></tr>
        <tr><th scope="row">Observed cycles / seconds</th><td>{reduction.originalResult.cycles} / {reduction.originalResult.seconds}</td><td>{reduction.reducedResult.cycles} / {reduction.reducedResult.seconds}</td></tr>
        <tr><th scope="row">Scoped verdict</th><td>{humanize(reduction.originalResult.verdict)}</td><td>{humanize(reduction.reducedResult.verdict)}</td></tr>
        <tr><th scope="row">Exact experiment SHA-256</th><td><code>{reduction.originalResult.identity.experiment}</code></td><td><code>{reduction.reducedResult.identity.experiment}</code></td></tr>
      </tbody></table></div>
      <p>Preserved fixture <code>{reduction.originalPlan.fixture}</code> · contract <code>{reduction.originalPlan.contract}</code> · seed {reduction.originalPlan.seed}. Budget: {reduction.originalPlan.cycles} cycles, {reduction.originalPlan.seconds} seconds, {reduction.originalPlan.bytes} artifact bytes.</p>
      <dl className="ei-grid">{Object.entries(report.original).filter(([key])=>['source','checker','contract'].includes(key)).map(([name,value])=><div key={name}><dt>Preserved {name} SHA-256</dt><dd><code>{value}</code></dd></div>)}</dl>
      <details><summary>Failure signatures and observed values</summary>{(['originalResult','reducedResult'] as const).map(key=>{const r=reduction[key];return <div key={key}><h5>{key==='originalResult'?'Original failure':'Reduced failure'}</h5>{r.failure?<><p>{r.failure.requirement} · {r.failure.kind} · first cycle {r.failure.cycle}</p><code>{r.failure.sha256}</code><dl><div><dt>Expected</dt><dd><pre>{r.failure.expected}</pre></dd></div><div><dt>Observed</dt><dd><pre>{r.failure.observed}</pre></dd></div></dl></>:<p>No failure signature is recorded.</p>}</div>})}</details>
    </>:null}
    {candidate?<><p>Validation: {humanize(candidate.status)} · neighboring cases {candidate.neighbors} · regression cases {candidate.regressions}.</p><p>Protected checker <code>{candidate.checker}</code> · contract <code>{candidate.contract}</code>.</p><p>Source review <code>{candidate.reviewHash??'Unavailable'}</code>.</p>
      {candidate.sources?<><div className="table-scroll" tabIndex={0} role="region" aria-label="Candidate source identities"><table><caption>Curated materialization and exact source bytes</caption><thead><tr><th scope="col">Quantity</th><th scope="col">Original</th><th scope="col">Candidate</th></tr></thead><tbody>{(['fixture','variant','bytes','fileHash','identityHash'] as const).map(key=><tr key={key}><th scope="row">{({fixture:'Fixture ID',variant:'Curated variant',bytes:'Source bytes',fileHash:'Source file SHA-256',identityHash:'Design source identity SHA-256'})[key]}</th><td><code>{candidate.sources!.original[key]}</code></td><td><code>{candidate.sources!.candidate[key]}</code></td></tr>)}</tbody></table></div><p>Source bytes changed: {String(candidate.sources.bytesChanged)} · curated configuration changed: {String(candidate.sources.configurationChanged)}. A curated variant change can leave source bytes unchanged; neither is model-generated authorship.</p></>:<p>Exact source review identities are unavailable.</p>}
      {candidate.sourceDiff!==null?<details><summary>Recorded source diff</summary><pre>{candidate.sourceDiff||'No source bytes changed.'}</pre></details>:<p>Source diff unavailable or exceeds64KiB preview limit.</p>}{candidate.configurationDiff!==null?<details><summary>Recorded configuration diff</summary><pre>{candidate.configurationDiff||'No curated configuration changed.'}</pre></details>:null}
    </>:kind==='candidate'?<p>No completed candidate-validation record is bound to this report.</p>:null}
    <p className="notice">Operational acceptance and physical cleanup verification are not established. A completed job is not automatically a validated repair; curated source is not model-generated.</p>
  </div>
}
