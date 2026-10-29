import { useEffect, useId, useRef, useState } from 'react'
import { humanize } from './api'
import { artifactHref, referenceKey } from './engineeringInvestigationApi'
import type { Artifact, NativeCaseView, NativeInvestigationDetail, Reference } from './engineeringInvestigationApi'

const hex = (v: number) => `0x${v.toString(16).padStart(8,'0')}`
function Identity({ label, value }: { label: string; value: string | number }) { return <div><dt>{label}</dt><dd><code>{value}</code></dd></div> }
function ReferenceLabel({ label, value }: { label: string; value: Reference }) { return <div><dt>{label}</dt><dd>{value.artifactId}<br/><code>{value.sha256}</code><span className="muted">Revision {value.revisionId}{value.attemptId ? ` · Producer attempt ${value.attemptId}` : ''}</span></dd></div> }
function DivergenceValue({ value }: { value: unknown }) {
  if (typeof value === 'number') return <code>{hex(value)}</code>
  if (typeof value === 'boolean') return <code>{String(value)}</code>
  // Only the validated fixed memory-effect projection reaches this branch.
  return <code>{value === null ? 'No memory effect' : JSON.stringify(value)}</code>
}
export function NativeToolInvestigation({ detail, artifacts }: { detail: NativeInvestigationDetail; artifacts: Artifact[] }) {
  const uid = useId(), [label,setLabel] = useState<string>(detail.cases[0].locator.label)
  const selected: NativeCaseView = detail.cases.find(c=>c.locator.label===label) ?? detail.cases[0]
  const diff = detail.family === 'firmware' ? artifacts.find(a=>referenceKey(a)===referenceKey(detail.candidate.diff) && a.sha256===detail.candidate.diff.sha256) : undefined
  return <>
    <section className="panel" aria-label="Recorded native evidence">
      <span className="eyebrow">Immutable native version {detail.ordinal}</span><h3>{detail.family === 'riscv' ? 'RISC-V architectural investigation' : 'Firmware MMIO investigation'}</h3>
      <div className="status-strip"><div className="status-cell"><span className="eyebrow">Operation</span><strong>Admission only</strong></div><div className="status-cell"><span className="eyebrow">New execution</span><strong>Not run</strong></div><div className="status-cell"><span className="eyebrow">Model involvement</span><strong>Not established by this record</strong></div></div>
      <p>Support: recorded import view. Import / Execute / Repair maturity declarations are not recorded in this native manifest. Viewing evidence starts no tool, model or repair operation.</p>
      <p>Admission resolves exact bytes and registered native checks. Validator provenance is host-pinned installed source; it is not remote execution attestation or current tool readiness.</p>
      <dl className="ei-grid"><Identity label="Version ID" value={detail.id}/><Identity label="Envelope SHA-256" value={detail.contentHash}/><Identity label="Associated project revision" value={detail.revisionId}/><Identity label="Project revision SHA-256" value={detail.revisionHash}/><Identity label="Manifest SHA-256" value={detail.manifestHash}/><Identity label="Configuration source SHA-256" value={detail.configurationHash}/><Identity label="Validator implementation SHA-256" value={detail.validatorHash}/></dl>
      <h4>Original producer provenance</h4><p>These external producer identifiers are not local campaign or attempt associations.</p>
      <dl className="ei-grid"><Identity label="Repository" value={detail.origin.repository}/><Identity label="Producer source commit" value={detail.origin.commit}/><Identity label="Hosted run" value={detail.origin.runId}/><Identity label="Run attempt" value={detail.origin.runAttempt}/><Identity label="Hosted artifact" value={detail.origin.artifactId}/><ReferenceLabel label="Extracted receipt" value={detail.origin.receipt}/></dl>
      {detail.origin.digest ? <p>Transport digest: <code>{detail.origin.digest.sha256}</code> · {humanize(detail.origin.digest.verification)}. This is transport provenance, not remote attestation.</p> : <p>Transport digest verification not recorded.</p>}
      <p>{detail.bindings.length} selected bindings from {detail.inventoryCount} producer inventory entries; {detail.omittedCount} paths omitted. Manifest review: {detail.manifestReview}.</p>
    </section>
    <section className="panel" aria-label="Native case detail">
      <label htmlFor={`${uid}-case`}>Recorded native case</label><select id={`${uid}-case`} value={selected.locator.label} onChange={event=>setLabel(event.target.value)}>{detail.cases.map(c=><option key={c.locator.label} value={c.locator.label}>{c.locator.label} · {humanize(c.verdict)}</option>)}</select>
      <h3>{selected.locator.label}</h3>
      <div className="status-strip"><div className="status-cell"><span className="eyebrow">Producer execution</span><strong>{selected.family==='riscv' ? humanize(selected.execution) : selected.completed ? 'Completed' : 'Incomplete'}</strong></div><div className="status-cell"><span className="eyebrow">Checker evidence</span><strong>{humanize(selected.evidence)}</strong></div><div className="status-cell"><span className="eyebrow">Scoped checker verdict</span><strong>{humanize(selected.verdict)}</strong></div></div>
      <p>Termination: {humanize(selected.termination)}. Cycles {selected.cycles} / {selected.cycleLimit}; retired instructions {selected.retired} / {selected.retirementLimit}. A bound or incomplete execution does not establish a design defect.</p>
      <p>Recorded producer phases: {selected.phases.join(', ')}.</p>
      <dl className="ei-grid">{selected.locator.kind==='producer_attempt' ? <><Identity label="Producer native attempt" value={selected.locator.nativeId}/><Identity label="Producer wire experiment" value={selected.locator.experimentId}/></> : <Identity label="Producer fixture" value={selected.locator.fixtureId}/>}<ReferenceLabel label="Producer result / execution bytes" value={selected.locator.result}/><Identity label="Configuration SHA-256" value={selected.configurationHash}/>{Object.entries(selected.identity).map(([name,value])=><Identity key={name} label={`${humanize(name)} identity SHA-256`} value={value}/>)}</dl>
      <p>Checker {selected.checker.id}, version {selected.checker.version}. Hashes identify recorded evidence; they do not establish a new run.</p>
      {selected.family==='riscv' ? <><h4>Architectural comparison</h4><p>Supported captured profile: PicoRV32 RV32I, with an independent Spike architectural comparison. Retained profile and environment artifacts identify the captured configuration; no live reference readiness is claimed.</p><p>Compared {selected.compared} retirements; DUT {selected.retired}, reference {selected.referenceCount}.</p>{selected.first ? <><p>First divergence: {selected.first.field}, retirement order {selected.first.order}, cycle {selected.first.cycle}.</p><table><caption>First architectural divergence</caption><thead><tr><th>Expected</th><th>Observed</th></tr></thead><tbody><tr><td><DivergenceValue value={selected.first.expected}/></td><td><DivergenceValue value={selected.first.observed}/></td></tr></tbody></table></> : <p>No first architectural divergence recorded for this case.</p>}</> : <><h4>MMIO comparison</h4><p>Independent literal MMIO checker; Spike is unsupported for this peripheral check.</p><p>{selected.mmioCount} checked MMIO transfers out of {selected.transfers} bus transfers. Mailbox: {selected.mailbox.observed ? `${hex(selected.mailbox.address)} = ${hex(selected.mailbox.value!)} at cycle ${selected.mailbox.cycle}` : 'Not observed'}.</p><dl className="ei-grid"><Identity label="Semantic observation SHA-256" value={selected.semanticHash}/><Identity label="Profile SHA-256" value={selected.profileHash}/></dl>{selected.first ? <><p>First MMIO conflict: index {selected.first.index}, bus sequence {selected.first.busSequence}, cycle {selected.first.cycle}; {selected.first.operation}.</p><table><caption>First MMIO conflict</caption><thead><tr><th>Quantity</th><th>Expected</th><th>Observed</th></tr></thead><tbody><tr><th>Address</th><td>{hex(selected.first.expectedAddress)}</td><td>{hex(selected.first.observedAddress)}</td></tr><tr><th>Probe</th><td>{hex(selected.first.expectedProbe)}</td><td>{hex(selected.first.observedProbe)}</td></tr></tbody></table><p>Write strobes {selected.first.strobes}; active-lane value {hex(selected.first.value)}.</p></> : <p>No first MMIO conflict recorded for this case.</p>}{selected.replayOf ? <dl><ReferenceLabel label="Original execution for saved-program replay" value={selected.replayOf}/></dl> : null}</>}
      <details><summary>Exact case input references</summary><dl className="ei-grid">{Object.entries(selected.input).map(([name,reference])=><ReferenceLabel key={name} label={humanize(name)} value={reference}/>)}{selected.family==='firmware' ? <>{Object.entries(selected.program).map(([name,reference])=><ReferenceLabel key={`program-${name}`} label={`Program ${humanize(name)}`} value={reference}/>)}{Object.entries(selected.sources).map(([name,reference])=><ReferenceLabel key={`source-${name}`} label={`Source ${humanize(name)}`} value={reference}/>)}</> : null}</dl></details>
    </section>
    <section className="panel" aria-label="Recorded comparisons">
      {detail.family==='riscv' ? <><h3>Recompiled control comparison</h3><p>{humanize(detail.recompiled.outcome)}</p><p>The control was assembled and compiled again. This comparison is distinct from replaying saved executable bytes and does not start a new replay.</p><dl><ReferenceLabel label="Recompiled-control record" value={detail.recompiled.record}/></dl></> : <><h3>Curated header correction</h3><p>{humanize(detail.candidate.outcome)} · MMIO indices 0–8 covered.</p><p>This is a curated recorded candidate. Model-generated repair provenance is not established.</p><dl className="ei-grid"><ReferenceLabel label="Original bad header" value={detail.candidate.originalHeader}/><ReferenceLabel label="Candidate header" value={detail.candidate.candidateHeader}/><ReferenceLabel label="Recorded header diff" value={detail.candidate.diff}/><Identity label="Protected checker SHA-256" value={detail.candidate.checkerHash}/><Identity label="Protected contract SHA-256" value={detail.candidate.contractHash}/><Identity label="Protected profile SHA-256" value={detail.candidate.profileHash}/></dl>{diff ? <DiffPreview key={`${detail.id}:${detail.contentHash}:${selected.locator.label}:${diff.sha256}`} detail={detail} artifact={diff}/> : null}<h3>Saved-program replay</h3><p>These recorded replays use saved program bytes and simulator-only phases. They are not recompilations or new executions by this view.</p>{detail.replays.map(replay=><article className="ei-record" key={replay.original}><strong>{replay.original} → {replay.repeated}</strong><p>{humanize(replay.outcome)}</p><dl><ReferenceLabel label="Saved-program replay record" value={replay.record}/></dl></article>)}</>}
    </section>
    <section className="panel" aria-label="Native artifacts"><h3>Exact native version artifacts</h3><p>{artifacts.length} artifacts · {detail.totalBytes.toLocaleString()} bytes. Downloads use this immutable local version and blob identity. Original producer paths are labels only.</p><details><summary>Inspect artifact identities and downloads</summary><ul className="artifact-list">{artifacts.map(artifact=>{
      const binding=detail.bindings.find(b=>referenceKey(b.reference)===referenceKey(artifact)), admitted=detail.artifacts.find(a=>referenceKey(a)===referenceKey(artifact))!
      return <li key={artifact.id}><div><strong>{artifact.artifactId}</strong><span>{artifact.roles.join(', ')} · {artifact.size} bytes</span>{binding?.originalPath ? <span>Producer path: {binding.originalPath}</span> : null}<code>Raw SHA-256: {artifact.sha256}</code>{admitted.canonicalHash ? <code>Canonical record SHA-256: {admitted.canonicalHash}</code> : null}</div><a className="ei-download" href={artifactHref(detail,artifact)} download={`${artifact.sha256}.bin`} aria-label={`Download ${artifact.artifactId}`}>Download exact bytes</a></li>
    })}</ul></details></section>
  </>
}
function DiffPreview({ detail, artifact }: { detail: NativeInvestigationDetail; artifact: Artifact }) {
  const life=useRef({active:false,epoch:0,controller:null as AbortController|null}).current
  const [state,setState]=useState<{loading:boolean;text?:string;error?:string}>({loading:false})
  useEffect(()=>{life.active=true;return()=>{life.active=false;life.epoch++;life.controller?.abort()}},[life])
  async function preview() {
    if(state.loading || artifact.size>65536)return
    const epoch=++life.epoch, controller=new AbortController();life.controller?.abort();life.controller=controller
    const current=()=>life.active&&life.epoch===epoch&&!controller.signal.aborted
    setState({loading:true})
    try {
      const response=await fetch(artifactHref(detail,artifact),{method:'GET',cache:'no-store',signal:AbortSignal.any([controller.signal,AbortSignal.timeout(10000)])})
      if(!response.ok||!response.body)throw new Error()
      const reader=response.body.getReader(), chunks:Uint8Array[]=[];let length=0
      try {while(true){const {done,value}=await reader.read();if(done)break;length+=value.byteLength;if(length>65536){await reader.cancel();throw new Error()}chunks.push(value)}}finally{reader.releaseLock()}
      if(length!==artifact.size)throw new Error()
      const bytes=new Uint8Array(length);let offset=0;for(const chunk of chunks){bytes.set(chunk,offset);offset+=chunk.length}
      const digest=Array.from(new Uint8Array(await crypto.subtle.digest('SHA-256',bytes)),v=>v.toString(16).padStart(2,'0')).join('')
      if(digest!==artifact.sha256)throw new Error()
      const text=new TextDecoder('utf-8',{fatal:true}).decode(bytes)
      if(current())setState({loading:false,text})
    }catch{if(current())setState({loading:false,error:'Recorded diff preview is unavailable or failed byte, hash or UTF-8 validation. Exact artifact download remains available.'})}
  }
  return <div><button disabled={state.loading||artifact.size>65536} onClick={()=>void preview()}>{state.loading?'Checking diff bytes…':'Preview recorded header diff'}</button><p className="muted">Plain-text preview only, at most 64 KiB. No source is executed or applied.</p>{artifact.size>65536?<p>Diff exceeds the preview limit. Download exact bytes below.</p>:null}{state.error?<p role="alert">{state.error}</p>:null}{state.text!==undefined?<pre>{state.text}</pre>:null}</div>
}
