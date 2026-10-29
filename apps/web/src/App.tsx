import { useRef, useState } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { createPlan, DEFAULT_WALL_TIME, hasCompleteIdentity, humanize, isActive, MAX_SEED, mutate, request, validSeed, validWallTime } from './api'
import type { Campaign, Contract, Experiment, Finding, Project, RunEvent } from './api'
import { EvidenceActions } from './EvidenceActions'
import { ARITHMETIC, campaignAdapter, familyName, projectAdapter, setupError, validOperand } from './families'
import { ContractSemantics, ExperimentControls } from './FamilyPanels'
import { TraceView } from './TraceView'

function ErrorNotice({ error, retry }: { error: Error | null; retry?: () => void }) {
  if (!error) return null
  return <div role="alert" className="notice error"><strong>Request needs attention</strong><p>{error.message}</p>{retry ? <button onClick={retry}>Retry connection</button> : null}</div>
}
function Status({ label, value }: { label: string; value: string | null }) {
  return <div className="status-cell"><span className="eyebrow">{label}</span><strong>{value ? humanize(value) : 'Not evaluated'}</strong></div>
}
export function App() {
  const [projectId, setProjectId] = useState('')
  const [campaignId, setCampaignId] = useState(() => new URLSearchParams(location.search).get('campaign') || '')
  const [seed, setSeed] = useState(123)
  const [wallTime, setWallTime] = useState(DEFAULT_WALL_TIME)
  const [reviewed, setReviewed] = useState(false)
  const [operandA, setOperandA] = useState<number | null>(null)
  const [operandB, setOperandB] = useState<number | null>(null)
  const draftSubmission = useRef<{ fingerprint: string; key: string; body: { project_id: string; adapter_id: string; plan: Experiment } } | null>(null)
  const client = useQueryClient()
  const projects = useQuery({ queryKey: ['projects'], queryFn: () => request<{ items: Project[] }>('/projects') })
  const selected = projects.data?.items.find(p => p.id === projectId) ?? projects.data?.items[0]
  const contract = useQuery({ queryKey: ['contract', selected?.contract_id], queryFn: () => request<Contract>(`/contracts/${encodeURIComponent(selected!.contract_id)}`), enabled: !!selected })
  const adapter = projectAdapter(selected)
  const width = contract.data?.parameters.width ?? 8
  const a = operandA ?? Math.min(7, 2 ** width - 1), b = operandB ?? Math.min(9, 2 ** width - 1)
  const unsupported = setupError(selected, contract.data)
  const campaigns = useQuery({ queryKey: ['campaigns'], queryFn: () => request<{ items: Campaign[] }>('/campaigns'), refetchInterval: 5000 })
  const campaign = useQuery({ queryKey: ['campaign', campaignId], queryFn: () => request<Campaign>(`/campaigns/${encodeURIComponent(campaignId)}`), enabled: !!campaignId, refetchInterval: q => isActive(q.state.data?.execution_status) ? 1500 : false })
  const events = useQuery({ queryKey: ['events', campaignId], queryFn: () => request<{ items: RunEvent[] }>(`/campaigns/${encodeURIComponent(campaignId)}/events?after=0`), enabled: !!campaignId, refetchInterval: isActive(campaign.data?.execution_status) ? 1500 : false })
  function selectCampaign(id: string) {
    setCampaignId(id)
    const url = new URL(location.href); url.searchParams.set('campaign', id); url.searchParams.delete('export'); history.replaceState(null, '', url)
  }
  const create = useMutation({ mutationFn: () => {
    const fingerprint = JSON.stringify([selected!.id, adapter, contract.data, seed, wallTime, a, b])
    if (draftSubmission.current?.fingerprint !== fingerprint) draftSubmission.current = { fingerprint, key: crypto.randomUUID(), body: { project_id: selected!.id, adapter_id: adapter!, plan: createPlan(selected!, contract.data!, seed, wallTime, { a, b }) } }
    return mutate<Campaign>('/campaigns', draftSubmission.current.body, draftSubmission.current.key)
  }, onSuccess: c => { draftSubmission.current = null; selectCampaign(c.id); void client.invalidateQueries({ queryKey: ['campaigns'] }) } })
  const action = useMutation({ mutationFn: (name: 'start' | 'cancel') => mutate<Campaign>(`/campaigns/${encodeURIComponent(campaignId)}/${name}`), onSuccess: c => { client.setQueryData(['campaign', c.id], c); void client.invalidateQueries({ queryKey: ['events', c.id] }); void client.invalidateQueries({ queryKey: ['campaigns'] }) } })
  const c = campaign.data
  const attempt = c?.attempts.at(-1)
  const result = attempt?.result
  const finding = c?.findings[0]
  const savedAdapter = campaignAdapter(c)
  const singleExperiment = !c?.completion_policy || c.completion_policy === 'single'
  const canCreate = !!selected && !unsupported && contract.data?.review_status === 'reviewed' && reviewed && validSeed(seed) && validWallTime(wallTime) && (adapter !== ARITHMETIC || (validOperand(a, width) && validOperand(b, width)))
  return <div className="app-shell">
    <a className="skip-link" href="#main">Skip to investigation</a>
    <aside className="sidebar"><a className="brand" href="/">◈ <span>VerifForge</span></a><span className="workspace-label">LOCAL WORKSPACE</span><nav aria-label="Workspace"><a className="nav-active" href="#main">Investigations</a><a href="#contract">Contract review</a><a href="#evidence">Evidence</a></nav><div className="sidebar-note"><span className="local-dot"/> Local development<p>Curated RTL fixtures<br/>Family-specific schemas</p></div></aside>
    <div className="workspace"><header className="topbar"><span>Workspace <span aria-hidden="true">/</span> RTL verification</span><span className="local-badge">Local only</span></header>
    <main id="main"><div className="page-heading"><div><p className="eyebrow">INVESTIGATION WORKBENCH</p><h1>From behavior to evidence.</h1><p>Review the contract. Run a bounded experiment. Inspect what actually happened.</p></div><span className="tag">{familyName(adapter)} · Local catalog</span></div>
      <div className="notice"><strong>Execution and correctness are separate.</strong> A completed run can contain a mismatch, build error or timeout. Only the independent checker determines the verdict.</div>
      <ErrorNotice error={projects.error} retry={() => { void projects.refetch() }}/>
      <div className="work-grid"><section className="panel setup" aria-labelledby="setup-title"><div className="section-heading"><span className="section-number">01</span><h2 id="setup-title">Prepare investigation</h2></div>
        {projects.isPending ? <p role="status">Loading curated projects…</p> : projects.isError ? <p className="muted">Project selection is unavailable until the local API reconnects.</p> : projects.data?.items.length === 0 ? <p>No curated projects are installed. Install an approved target package to begin.</p> : <>
        <label htmlFor="project">Curated project</label><select id="project" value={selected?.id ?? ''} onChange={e => { setProjectId(e.target.value); setReviewed(false); setOperandA(null); setOperandB(null) }}>{projects.data?.items.map(p => <option key={p.id} value={p.id}>{p.name} · {familyName(projectAdapter(p))}</option>)}</select>
        {selected ? <p className="meta">{selected.family.toUpperCase()} <span>·</span> {humanize(selected.support_level)}</p> : null}
        <div id="contract" className="contract"><div className="subheading"><h3>Behavioral contract</h3><span className="tag">{contract.data?.review_status ?? 'Unavailable'}</span></div>
        <ErrorNotice error={contract.error} retry={() => { void contract.refetch() }}/>
        {contract.isPending && selected ? <p role="status">Loading immutable contract…</p> : null}
        {contract.data ? <><code>{contract.data.contract_id} / v{contract.data.version}</code><ul className="requirements">{contract.data.requirements.map(r => <li key={r.requirement_id}><strong>{r.requirement_id}</strong><p>{r.description}</p></li>)}</ul><ContractSemantics contract={contract.data} adapter={adapter}/><label className="checkbox"><input type="checkbox" checked={reviewed} onChange={e => setReviewed(e.target.checked)}/> I reviewed these active semantics.</label></> : null}</div>
        {unsupported && contract.data ? <p role="status" className="notice">{unsupported}</p> : null}<ExperimentControls adapter={adapter} contract={contract.data} a={a} b={b} setA={setOperandA} setB={setOperandB}/><p className="muted">Deterministic curated stimulus; no model call is required. Model planning is not connected in this slice.</p>
        <label htmlFor="seed">Seed</label><input id="seed" type="number" min="0" max={MAX_SEED} step="1" value={Number.isNaN(seed) ? '' : seed} aria-invalid={!validSeed(seed)} aria-describedby="seed-help" onChange={e => setSeed(e.target.valueAsNumber)}/><p id="seed-help" className="meta">Whole number from 0 to {MAX_SEED}.</p><label htmlFor="wall-time">Wall-time budget (seconds)</label><input id="wall-time" type="number" min="1" max="3600" step="1" value={Number.isNaN(wallTime) ? '' : wallTime} aria-invalid={!validWallTime(wallTime)} aria-describedby="wall-time-help" onChange={e => setWallTime(e.target.valueAsNumber)}/><p id="wall-time-help" className="meta">1–3600 seconds. Includes compile time. Changes apply to new drafts only.</p><p className="meta">New draft budget: 100 cycles · {validWallTime(wallTime) ? wallTime : '—'} seconds · 10 MiB artifacts</p>
        <button className="primary" disabled={!canCreate || create.isPending} onClick={() => create.mutate()}>{create.isPending ? 'Creating draft…' : 'Create campaign draft'} <span aria-hidden="true">→</span></button><ErrorNotice error={create.error}/></>}
      </section>
      <section className="investigation" aria-labelledby="campaign-title"><div className="panel"><div className="section-heading"><span className="section-number">02</span><h2 id="campaign-title">Campaign execution</h2></div>
        <label htmlFor="campaign">Saved campaigns</label><select id="campaign" value={campaignId} onChange={e => selectCampaign(e.target.value)}><option value="">Select a campaign</option>{campaigns.data?.items.map(item => <option key={item.id} value={item.id}>{item.id} · {humanize(item.execution_status)}</option>)}</select><ErrorNotice error={campaigns.error} retry={() => { void campaigns.refetch() }}/>
        <ErrorNotice error={campaign.error} retry={() => { void campaign.refetch() }}/>{campaign.isFetching && !c && campaignId ? <p role="status">Restoring saved campaign…</p> : null}
        {!campaignId ? <div className="empty"><span aria-hidden="true">⌁</span><h3>No experiment running</h3><p>Create a campaign after reviewing the contract, or open a saved investigation. Results appear only after actual execution.</p></div> : c ? <><div className="campaign-meta"><code>{c.id}</code><span>State version {c.version}</span></div><p className="meta">Saved target: <strong>{c.project_id}</strong> · {familyName(savedAdapter)} · adapter {c.adapter_id ?? savedAdapter ?? "unknown"} · contract {c.contract_id}</p>{c.source_attempt_id ? <p className="notice">Replay of database attempt <code>{c.source_attempt_id}</code>. The displayed verdict belongs to this saved execution.</p> : null}<details><summary>Saved experiment (parsed view)</summary><p className="meta">Large integer tokens are shown as strings to preserve their digits. This inspection view is not raw JSON; exact replay uses the server record.</p><pre>{JSON.stringify(c.plan, null, 2)}</pre></details><div className="status-strip" aria-live="polite"><Status label="EXECUTION" value={c.execution_status}/><Status label="CHECKER VERDICT" value={c.verdict}/></div>
        {c.verdict === 'timeout' ? <p className="notice">The execution exceeded its budget. A timeout does not establish a design defect.</p> : null}{c.verdict === 'unsupported' ? <p className="notice">The worker reported an unsupported execution environment. Check simulator availability and tool evidence below.</p> : null}
        <p className="meta">Saved budget: {c.plan.budget?.max_cycles ?? '—'} cycles · {c.plan.budget?.wall_time_seconds ?? '—'} seconds · seed {c.plan.seed ?? '—'}. This campaign is immutable.</p><div className="actions"><button className="primary" disabled={c.execution_status !== 'draft' || action.isPending || !singleExperiment || !savedAdapter || !projects.data?.items.some(p => p.id === c.project_id && projectAdapter(p) === savedAdapter)} onClick={() => action.mutate('start')}>Start campaign</button><button disabled={!isActive(c.execution_status) || action.isPending} onClick={() => action.mutate('cancel')}>Cancel execution</button></div><ErrorNotice error={action.error}/><p className="meta">Queued work waits for a configured worker and simulator. Refreshing this page preserves the campaign URL.</p></> : null}
        <h3>Durable progress</h3><ErrorNotice error={events.error} retry={() => { void events.refetch() }}/><ol className="timeline" aria-live="polite">{events.data?.items.map(e => <li key={e.sequence}><span className="event-sequence">{String(e.sequence).padStart(2, '0')}</span><div><strong>{humanize(e.kind)}</strong><time dateTime={e.created_at}>{new Date(e.created_at).toLocaleString()}</time><details><summary>Event payload</summary><pre>{JSON.stringify(e.payload, null, 2)}</pre></details></div></li>)}</ol>{!events.data?.items.length ? <p className="muted">No persisted events yet.</p> : null}</div>
        <section className="panel" id="evidence"><div className="section-heading"><span className="section-number">03</span><h2>Finding & evidence</h2></div>{finding ? <FindingDetail finding={finding}/> : <p className="muted">No checked finding has been recorded. This does not establish correctness.</p>}
        {result ? <><TraceView campaign={c!} attemptId={attempt!.id} result={result}/><h3>Evidence identity</h3><dl className="identities">{Object.entries(result.identity ?? {}).map(([k, v]) => <div key={k}><dt>{humanize(k)}</dt><dd><code>{v}</code></dd></div>)}</dl><h3>Observed tools</h3>{result.tool_versions.length ? <ul>{result.tool_versions.map(t => <li key={t.name}>{t.name} <code>{t.version}</code></li>)}</ul> : <p>No tool versions recorded.</p>}<h3>Artifacts</h3>{result.artifacts.length ? <ul>{result.artifacts.map(a => <li key={a.artifact_id}><strong>{a.path}</strong> · {a.size_bytes.toLocaleString()} bytes<code>{a.sha256}</code></li>)}</ul> : <p>No artifacts recorded.</p>}{singleExperiment ? <EvidenceActions key={attempt!.id} attemptId={attempt!.id} adapterId={savedAdapter} hasIdentity={hasCompleteIdentity(result.identity)} onReplay={replayed => { client.setQueryData(['campaign', replayed.id], replayed); selectCampaign(replayed.id); void client.invalidateQueries({ queryKey: ['campaigns'] }) }}/> : <p className="notice">Replay and export need the selected attempt’s experiment context for this campaign policy.</p>}{result.uncertainties.map(u => <p className="notice" key={u}>{u}</p>)}</> : <div className="empty small"><h3>Evidence will appear here</h3><p>Source, checker, contract and environment identities are preserved with each checked attempt.</p></div>}</section>
      </section></div><footer>VerifForge / local development <span>FIFO / arithmetic controls · Support follows the local catalog · Other families require a supported adapter</span></footer>
    </main></div></div>
}
function FindingDetail({ finding }: { finding: Finding }) {
  const signature = finding.result.failure_signature
  if (!signature) return <p className="notice">Finding {finding.id} has no failure signature. Evidence is incomplete.</p>
  return <article className="finding"><div className="subheading"><h3>{signature.requirement_id}</h3><span className="tag">{humanize(finding.status)}</span></div><p>Independent checker recorded {humanize(finding.verdict)}.</p><p className="meta">First divergence: cycle {signature.first_cycle} · {humanize(signature.failure_class)}</p><div className="comparison"><div><span className="eyebrow">EXPECTED</span><p>{signature.expected}</p></div><div><span className="eyebrow">OBSERVED</span><p>{signature.observed}</p></div></div><details><summary>Failure signature and finding identity</summary><code>{signature.signature_hash}</code><code>{finding.id}</code><dl>{Object.entries(finding.result.identity ?? {}).map(([k,v]) => <div key={k}><dt>{humanize(k)}</dt><dd><code>{v}</code></dd></div>)}</dl></details>{finding.result.uncertainties.map(u => <p key={u}>{u}</p>)}</article>
}
