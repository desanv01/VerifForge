import { useId, useState } from 'react'
import { humanize } from './api'
import { artifactHref, referenceKey } from './engineeringInvestigationApi'
import type { Artifact, InvestigationDetail } from './engineeringInvestigationApi'

const views = [
  ['rtl', 'RTL'], ['riscv', 'RISC-V'], ['firmware', 'Firmware / MMIO'],
  ['vivado', 'Vivado'], ['kicad', 'KiCad'], ['system', 'Cross-domain'],
] as const

export function DomainEvidenceNavigator({ detail, artifacts, factAnchor }: {
  detail: InvestigationDetail; artifacts: Artifact[]; factAnchor: (id: string) => string
}) {
  const uid = useId(), [domain, setDomain] = useState('system')
  const selectedFacts = detail.facts.filter(f => domain === 'system' || f.domain === domain)
  const ids = new Set(selectedFacts.map(f => f.id))
  const relations = detail.relations.filter(r => domain === 'system' || r.facts.some(id => ids.has(id)))
  return <section className="panel" aria-labelledby={`${uid}-heading`}>
    <span className="eyebrow">Connected workbench</span><h3 id={`${uid}-heading`}>Domain evidence and conflicts</h3>
    <div className="domain-selector" role="group" aria-label="Evidence domain">{views.map(([id, name]) => <button key={id} aria-pressed={domain === id} onClick={() => setDomain(id)}>{name}<span className="domain-count">{id === 'system' ? detail.relations.length : detail.facts.filter(f => f.domain === id).length} {id === 'system' ? 'relationships' : 'facts'}</span></button>)}</div>
    <p role="status">{selectedFacts.length} retained facts · {relations.length} connected relationships in {views.find(v => v[0] === domain)?.[1]}.</p>
    {!selectedFacts.length ? <p className="notice">No {views.find(v => v[0] === domain)?.[1]} facts are admitted in this version. No tool result or supported configuration is inferred.</p> : <ul className="artifact-list">{selectedFacts.map(fact => {
      const artifact = artifacts.find(a => referenceKey(a) === referenceKey(fact.reference) && a.sha256 === fact.reference.sha256)
      return <li key={fact.id}><div><a href={`#${factAnchor(fact.id)}`}>{fact.id}</a><span>{humanize(fact.domain)} · {humanize(fact.category)} · {humanize(fact.review)}</span><span>{humanize(fact.provenance)} provenance · {fact.limitations} conditions / uncertainties</span></div>{artifact ? <a className="ei-download" href={artifactHref(detail, artifact)} download={`${artifact.sha256}.bin`} aria-label={`Download native fact ${fact.id}`}>Exact native fact</a> : null}</li>
    })}</ul>}
    {relations.length ? <div className="table-scroll" tabIndex={0} role="region" aria-label="Connected relationship comparisons"><table><caption>Value comparison and provenance-aware verdict</caption><thead><tr><th scope="col">Relationship</th><th scope="col">Native values</th><th scope="col">Scoped verdict</th><th scope="col">Ancestry / authority</th></tr></thead><tbody>{relations.map(relation => <tr key={relation.id}><th scope="row">{relation.id}</th><td>{humanize(relation.comparison)}</td><td>{humanize(relation.verdict)}</td><td>{humanize(relation.dependence)} · {humanize(relation.basis)}</td></tr>)}</tbody></table></div> : <p>No connected relationships recorded for this domain.</p>}
    <p className="muted">A literal value conflict can remain inconclusive when review or independent ancestry is missing. Corrected sources require the affected checks to be rerun; this view starts no execution.</p>
  </section>
}
