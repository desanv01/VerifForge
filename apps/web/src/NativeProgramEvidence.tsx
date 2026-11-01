import { useId, useState } from 'react'
import { humanize } from './api'
import { artifactHref, referenceKey } from './engineeringInvestigationApi'
import type { Artifact, NativeCaseView, NativeInvestigationDetail, Reference } from './engineeringInvestigationApi'
import { ArtifactPreview } from './RecordedArtifactPreview'

export function NativeProgramEvidence({ detail, selected, artifacts }: {
  detail: NativeInvestigationDetail; selected: NativeCaseView; artifacts: Artifact[]
}) {
  const uid = useId(), [choice, setChoice] = useState('')
  const entries: { name: string; reference: Reference; text: boolean }[] = selected.family === 'firmware'
    ? [...Object.entries(selected.program).map(([name, reference]) => ({ name: `Program ${humanize(name)}`, reference, text: ['map', 'disassembly', 'elf_attributes'].includes(name) })),
      ...Object.entries(selected.sources).map(([name, reference]) => ({ name: `Source ${humanize(name)}`, reference, text: true })),
      ...['register_contract', 'selected_header', 'observation', 'check'].map(name => ({ name: humanize(name), reference: selected.input[name as keyof typeof selected.input], text: true }))]
    : Object.entries(selected.input).map(([name, reference]) => ({ name: humanize(name), reference, text: !['elf', 'binary', 'object'].includes(name) }))
  const entry = entries.find(e => e.name === choice) ?? entries[0]
  const artifact = artifacts.find(a => referenceKey(a) === referenceKey(entry.reference) && a.sha256 === entry.reference.sha256)
  return <section className="panel" aria-label="Program and source evidence">
    <span className="eyebrow">Connected evidence</span><h3>Program, sources and checker inputs</h3>
    <p>Inspect the selected case’s retained inputs. ELF and object bytes are downloads; text previews verify the exact version, byte count and SHA-256 before display.</p>
    <label htmlFor={`${uid}-artifact`}>Case evidence artifact</label>
    <select id={`${uid}-artifact`} value={entry.name} onChange={event => setChoice(event.target.value)}>{entries.map(item => <option key={item.name} value={item.name}>{item.name}</option>)}</select>
    <dl className="ei-grid"><div><dt>Artifact</dt><dd>{entry.reference.artifactId}</dd></div><div><dt>Source revision</dt><dd>{entry.reference.revisionId}</dd></div><div><dt>Exact bytes SHA-256</dt><dd><code>{entry.reference.sha256}</code></dd></div></dl>
    {artifact ? <><a className="ei-download" href={artifactHref(detail, artifact)} download={`${artifact.sha256}.bin`}>Download selected case evidence</a>{entry.text ? <ArtifactPreview detail={detail} artifact={artifact} label="Preview selected case evidence" /> : <p className="muted">Binary artifact. No browser execution or disassembly is performed.</p>}</> : <p role="status">This reference has no matching admitted download. Evidence is unavailable.</p>}
    <p className="muted">ISA, reset and memory-map assumptions must be checked against the retained profile and source inputs. A program hash alone does not establish support or correctness.</p>
  </section>
}

export function NativeValidationLineage({ detail }: { detail: NativeInvestigationDetail }) {
  return <section className="panel" aria-label="Candidate and validation lineage">
    <h3>Candidate and validation lineage</h3>
    <p>{detail.family === 'firmware' ? 'The recorded correction is curated. Its checker outcomes and saved-program replays remain separate evidence steps.' : 'Recorded control, defect and comparison executions retain their own source and checker identities. A recompiled control is not saved-program replay or a validated repair.'}</p>
    <div className="table-scroll" tabIndex={0} role="region" aria-label="Recorded validation cases"><table><caption>Recorded case lineage · no new validation run</caption><thead><tr><th scope="col">Case</th><th scope="col">Producer execution</th><th scope="col">Scoped verdict</th><th scope="col">Program binary</th><th scope="col">Protected checker / contract</th></tr></thead><tbody>{detail.cases.map(item => <tr key={item.locator.label}><th scope="row">{item.locator.label}</th><td>{item.family === 'riscv' ? humanize(item.execution) : item.completed ? 'Completed' : 'Incomplete'}</td><td>{humanize(item.verdict)}</td><td><code>{item.family === 'firmware' ? item.program.binary.sha256 : item.input.binary.sha256}</code>{item.family === 'firmware' && item.replayOf ? <p>Saved-program replay of <code>{item.replayOf.sha256}</code></p> : null}</td><td><code>{item.identity.checker}</code><br/><code>{item.identity.contract}</code></td></tr>)}</tbody></table></div>
    <p className="muted">Neighboring-case, broader regression and model-generated repair acceptance are not established by this recorded view. Compare exact candidate, source and execution identities before reusing an outcome.</p>
  </section>
}
