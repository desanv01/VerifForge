import { humanize } from './api'
import type { parseSystem } from './investigationProjectionApi'

export function SystemInvestigationProjection({ projection: p }: { projection: ReturnType<typeof parseSystem> }) {
  return <article className="ei-record" aria-label="Cross-domain manifest investigation">
    <h4>Cross-domain · {humanize(p.family)}</h4>
    <dl className="ei-grid"><div><dt>Manifest</dt><dd><code>{p.manifest}</code></dd></div><div><dt>Bridge checker implementation</dt><dd><code>{p.checker}</code></dd></div></dl>
    <p>Correction status: no candidate rerun evidence. New execution not run.</p>
    <div className="table-scroll" tabIndex={0} role="region" aria-label="Manifest comparisons"><table><caption>Literal comparison and independent verdict remain separate</caption><thead><tr><th scope="col">Binding</th><th scope="col">Facts</th><th scope="col">Values</th><th scope="col">Verdict</th><th scope="col">Reasons</th></tr></thead><tbody>{p.comparisons.map(c => <tr key={c.binding}><th scope="row">{c.binding}</th><td>{c.facts.join(', ')}</td><td>{c.comparison}</td><td>{c.verdict}</td><td>{c.reasons.map(humanize).join(', ')}</td></tr>)}</tbody></table></div>
    {p.checks ? <><h4>Reviewed manifest checks</h4><p>Source comparison completed. Physical verdict remains inconclusive.</p><dl className="ei-grid"><div><dt>Checks identity</dt><dd><code>{p.checks.checks}</code></dd></div><div><dt>Manifest checker implementation</dt><dd><code>{p.checks.checker}</code></dd></div></dl><div className="table-scroll" tabIndex={0} role="region" aria-label="Reviewed manifest values"><table><caption>Expected and observed native values</caption><thead><tr><th scope="col">Check</th><th scope="col">Quantity</th><th scope="col">Expected</th><th scope="col">Observed</th><th scope="col">Values / verdict</th><th scope="col">Limitations</th></tr></thead><tbody>{p.checks.rows.map(c => <tr key={c.id}><th scope="row">{c.id}</th><td>{humanize(c.quantity)}</td><td>{c.expected}</td><td>{c.observed.join(', ')}</td><td>{c.comparison} / {c.verdict}</td><td>{c.reasons.map(humanize).join(', ')}</td></tr>)}</tbody></table></div></> : <p>No additional reviewed manifest check projection is bound to this bridge.</p>}
    <ul>{p.limitations.map((s, i) => <li key={i}>{s}</li>)}</ul>
    <p className="notice">Common ancestry or incomplete review can leave literal conflicts inconclusive. Candidate corrections must retain requirements and rerun affected checks. No correction/rerun acceptance is inferred.</p>
  </article>
}
