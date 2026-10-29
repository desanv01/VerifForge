import { useEffect, useRef, useState } from 'react'
import { useQuery, useQueryClient } from '@tanstack/react-query'
import type { Campaign, Experiment, FifoExperiment, Project } from './api'
import { humanize, isActive } from './api'
import { curatedPlannerProject, PlannerError, plannerBudget, plannerCampaign, plannerFingerprint, plannerHealth, plannerPage, plannerPlan, plannerPost, plannerRequest, plannerSeed, plannerStatus, PLANNER_PAGE_SIZE } from './plannerApi'

type Props = { project?: Project; campaignId: string; campaign?: Campaign; contextReady: boolean; seedReady: boolean; prepareSeed: () => Experiment; onSeedSelected: (id: string) => void }
export function PlannerPanel(props: Props) {
  if (!curatedPlannerProject(props.project)) return null
  return <PlannerScope key={`${props.project!.id}:${props.campaignId}`} {...props} project={props.project!}/>
}
function PlannerScope({ project, campaignId, campaign, contextReady, seedReady, prepareSeed, onSeedSelected }: Props & { project: Project }) {
  const client = useQueryClient()
  // An actual remount gets a different cache namespace and lifetime. React's
  // effect replay only reactivates this same instance; it cannot revive old ones.
  const [scope] = useState(() => crypto.randomUUID())
  const lifetime = useRef({ active: false, epoch: 0, ready: contextReady }).current
  if (lifetime.ready !== contextReady) { lifetime.ready = contextReady; lifetime.epoch++ }
  useEffect(() => { lifetime.active = true; return () => { lifetime.active = false; lifetime.epoch++ } }, [lifetime])
  const root = ['planner', scope, project.id, campaignId]
  const base = `/campaigns/${encodeURIComponent(campaignId)}`
  const health = useQuery({ queryKey: [...root, 'health'], queryFn: ({ signal }) => plannerRequest('/health', plannerHealth, { signal }), retry: false, gcTime: 0, staleTime: 0, refetchOnMount: 'always', refetchInterval: 15000 })
  const available = contextReady && health.isSuccess && health.isFetchedAfterMount && !health.isFetching && health.data.available
  const admission = available && health.data?.admission === true
  let binding: ReturnType<typeof plannerCampaign> | null = null
  try { if (campaignId && campaign && contextReady) binding = plannerCampaign(campaign, campaignId, project.id) } catch { /* Inconsistent/legacy campaigns have no planner authority. */ }
  const readable = available && !!binding
  const status = useQuery({ queryKey: [...root, 'status'], queryFn: async ({ signal }) => {
    try { return await plannerRequest(`${base}/planner`, raw => plannerStatus(raw, campaignId), { signal }) }
    catch (error) { if (error instanceof PlannerError && error.status === 404) return null; throw error }
  }, enabled: readable, retry: false, gcTime: 0, staleTime: 0, refetchOnMount: 'always' })
  const statusReady = readable && status.isSuccess && status.isFetchedAfterMount && !status.isFetching
  const attached = statusReady ? status.data : null
  const budget = useQuery({ queryKey: [...root, 'budget'], queryFn: ({ signal }) => plannerRequest(`${base}/planner/budget`, raw => {
    const parsed = plannerBudget(raw, campaignId)
    if (!attached || parsed.priceStatus !== attached.priceStatus || plannerFingerprint(parsed.limits) !== plannerFingerprint(attached.limits)) throw new PlannerError(0, 'Planner budget and frozen status policy disagree.')
    return parsed
  }, { signal }), enabled: !!attached, retry: false, gcTime: 0, staleTime: 0, refetchOnMount: 'always' })
  const [busy, setBusy] = useState<string | null>(null)
  const [error, setError] = useState<string | null>(null)
  const seedIntent = useRef<{ fingerprint: string; plan: FifoExperiment; key: string } | null>(null)
  const [attachKey] = useState(() => crypto.randomUUID())
  const [cancelKey] = useState(() => crypto.randomUUID())
  async function act(kind: 'seed' | 'attach' | 'cancel') {
    if (busy || !available || (kind !== 'cancel' && !admission)) return
    if (kind === 'seed' ? !!campaignId || !seedReady : !binding || (kind === 'attach' && (!binding.eligible || !statusReady || status.data !== null)) || (kind === 'cancel' && !isActive(binding.execution))) return
    const epoch = lifetime.epoch
    const current = () => lifetime.active && lifetime.ready && lifetime.epoch === epoch
    setBusy(kind); setError(null)
    try {
      if (kind === 'seed') {
        const candidate = plannerPlan(prepareSeed(), project.id)
        const fingerprint = plannerFingerprint({ ...candidate, experiment_id: null })
        if (seedIntent.current?.fingerprint !== fingerprint) seedIntent.current = { fingerprint, plan: candidate, key: crypto.randomUUID() }
        const intent = seedIntent.current
        const created = await plannerPost('/planner-seeds', { schema_version: 'planner.seed.v1', project_id: project.id, plan: intent.plan }, intent.key, plannerSeed)
        if (!current()) return
        // The seed response contains no project identity. Read the saved record
        // and bind the exact submitted plan before changing selection/history.
        await plannerRequest(`/campaigns/${encodeURIComponent(created.campaignId)}`, raw => plannerCampaign(raw, created.campaignId, project.id, intent.plan))
        if (current()) onSeedSelected(created.campaignId)
      } else {
        if (kind === 'attach') await plannerPost(`${base}/planner`, { schema_version: 'planner.attach.v1' }, attachKey, raw => plannerStatus(raw, campaignId))
        else await plannerPost(`${base}/cancel`, undefined, cancelKey, raw => plannerCampaign(raw, campaignId, project.id))
        if (!current()) return
        await client.invalidateQueries({ queryKey: root })
        if (current()) void client.invalidateQueries({ queryKey: ['campaign', campaignId], exact: true })
      }
    } catch (failure) { if (current()) setError(failure instanceof PlannerError ? failure.message : 'Planner request could not be validated. Retry explicitly after checking the local API.') }
    finally { if (lifetime.active) setBusy(null) }
  }
  return <section className="panel" aria-label="Bounded FIFO planner">
    <h2>Bounded FIFO planner</h2>
    <p>Start a reviewed FIFO seed, then attach the server-configured planner after its execution settles. The independent checker determines design verdicts.</p>
    <p className="meta">Admission configuration does not establish provider readiness. This interface requires the runtime model marker <code>not_integrated</code>; no live Meta connection is established by this view.</p>
    {!contextReady ? <p role="status">Planner state and actions are withheld until the selected project and campaign are revalidated.</p> : health.isPending || health.isFetching ? <p role="status">Checking planner availability…</p> : !available ? <p role="status">Planner API version 1 and a valid admission flag are required. This runtime is unavailable or unsupported.</p> : <p role="status">{admission ? 'Planner admission enabled by server configuration.' : 'Planner admission disabled. Existing planner records and cancellation remain available.'}</p>}
    <button disabled={health.isFetching || !!busy} onClick={() => void client.invalidateQueries({ queryKey: root })}>Refresh planner records</button>
    {health.isError ? <p role="alert">Planner capability could not be checked. Refresh planner records to retry.</p> : null}
    {!campaignId ? <><p className="notice">Starting a seed immediately queues bounded RTL execution. It uses the reviewed contract, seed and execution budget above. The server may reject a budget beyond its configured allowance. Planner attachment requires a separate action.</p><button className="primary" disabled={!admission || !seedReady || !!busy} onClick={() => void act('seed')}>{busy === 'seed' ? 'Starting seed…' : 'Start bounded FIFO seed'}</button>{!seedReady ? <p>Review the supported FIFO contract and valid experiment inputs above before starting.</p> : null}</> : <>
      {contextReady && !binding ? <p role="status">The saved campaign cannot be bound to a supported FIFO planner context. No planner records or actions are available.</p> : null}
      {readable && (status.isPending || status.isFetching) ? <p role="status">Loading planner status…</p> : null}
      {readable && status.isError ? <p role="alert">Planner status could not be validated. Refresh planner records to retry.</p> : null}
      {statusReady && status.data === null ? <><p>No planner is attached to this campaign.</p><p>Attachment requires a running sequential campaign with one immutable seed experiment and only completed, independently checked attempts.</p><button disabled={!admission || !binding?.eligible || !!busy} onClick={() => void act('attach')}>{busy === 'attach' ? 'Attaching planner…' : 'Attach configured planner'}</button></> : null}
      {attached ? <><dl className="identities"><div><dt>Planner phase</dt><dd>{humanize(attached.phase)}</dd></div><div><dt>Campaign execution</dt><dd>{humanize(attached.execution)}</dd></div><div><dt>Independent checker verdict</dt><dd>{attached.verdict ? humanize(attached.verdict) : 'Not evaluated'}</dd></div><div><dt>Requested model / strategy</dt><dd>{attached.model} / {attached.strategy}</dd></div><div><dt>Generation</dt><dd>{attached.generation}</dd></div><div><dt>Stop reason</dt><dd>{attached.stopReason ?? 'None recorded'}</dd></div><div><dt>Deadline</dt><dd><time dateTime={attached.deadline}>{attached.deadline}</time></dd></div></dl>
        <p>Planner status is operational state. A timeout or interrupted model call does not establish a design defect. These records are separate reads, not an atomic execution snapshot.</p>
        {attached.stopReason === 'model_call_uncertain' ? <p className="notice">Remote model outcome unknown. Local cleanup cannot establish whether a remote request completed.</p> : null}
        <h3>Reserved budget</h3>
        {budget.isPending || budget.isFetching ? <p role="status">Loading planner budget…</p> : budget.isError ? <p role="alert">Planner budget could not be validated. Refresh planner records to retry.</p> : budget.isSuccess && budget.isFetchedAfterMount ? <><p>Reserved tokens: {budget.data.tokens} / {budget.data.limits.max_model_tokens}. Reserved cycles: {budget.data.cycles} / {budget.data.limits.max_total_cycles}. Reserved execution seconds: {budget.data.seconds}.</p><p>{budget.data.cost === null ? 'Cost unknown — pricing is unavailable.' : `Reserved cost: USD ${budget.data.cost}.`} Reservations are not a billing statement.</p><details><summary>Frozen server limits</summary><dl>{Object.entries(budget.data.limits).map(([key,value]) => <div key={key}><dt>{humanize(key)}</dt><dd>{value === null ? 'Not configured' : String(value)}</dd></div>)}</dl></details></> : null}
      </> : null}
      {status.data ? <div hidden={!attached}><PlannerPages root={root} base={base} kind="rounds" enabled={!!attached}/><PlannerPages root={root} base={base} kind="cleanup" enabled={!!attached}/></div> : null}
      <button disabled={!readable || !binding || !isActive(binding.execution) || !!busy} onClick={() => void act('cancel')}>{busy === 'cancel' ? 'Cancelling…' : 'Cancel bounded campaign'}</button>
    </>}
    {error ? <p role="alert">{error} Use the same action to retry; a seed retry preserves its exact plan and idempotency key.</p> : null}
  </section>
}
function PlannerPages({ root, base, kind, enabled }: { root: string[]; base: string; kind: 'rounds' | 'cleanup'; enabled: boolean }) {
  const [cursors, setCursors] = useState([0])
  const after = cursors[cursors.length - 1]
  const page = useQuery({ queryKey: [...root, kind, after], queryFn: ({ signal }) => plannerRequest(`${base}/planner/${kind}?after=${after}&limit=${PLANNER_PAGE_SIZE}`, raw => plannerPage(raw, after, kind), { signal }), enabled, retry: false, gcTime: 0, staleTime: 0, refetchOnMount: 'always' })
  const ready = enabled && page.isSuccess && page.isFetchedAfterMount && !page.isFetching
  return <section aria-label={kind === 'rounds' ? 'Planner rounds' : 'Planner cleanup'}><h3>{kind === 'rounds' ? 'Planner rounds' : 'Local cleanup'}</h3>
    {kind === 'cleanup' ? <p>Pending means local cleanup remains outstanding. Unknown means the local owner was lost. Settled describes local cleanup only; none of these states proves the remote model outcome.</p> : null}
    {page.isPending || page.isFetching ? <p role="status">Loading {kind}…</p> : page.isError ? <p role="alert">Planner {kind} could not be validated. <button onClick={() => void page.refetch()}>Retry {kind}</button></p> : null}
    {ready ? page.data.items.length ? <ol>{page.data.items.map(row => <li key={row.id}><strong>Round {row.ordinal}: {humanize(row.status)}</strong><p><code>{row.id}</code> · generation {row.generation}{row.experiment ? ` · experiment ${row.experiment}` : ''}</p>{row.status === 'uncertain' ? <p>Remote model outcome unknown; no design verdict follows from this state.</p> : null}<p>{row.registered ? `Registered ${row.registered}. ` : ''}{row.ownerLost ? `Owner lost ${row.ownerLost}. ` : ''}{row.settled ? `Local cleanup settled ${row.settled}.` : ''}{row.finished ? `Finished ${row.finished}.` : ''}</p></li>)}</ol> : <p>No {kind} records on this page.</p> : null}
    <p>Page {cursors.length} · at most {PLANNER_PAGE_SIZE} records per page · capped at 20 pages per view.</p>
    <button disabled={!enabled || page.isFetching || cursors.length <= 1} onClick={() => setCursors(old => old.slice(0,-1))}>Previous {kind}</button>
    <button disabled={!ready || page.data.next === null || cursors.length >= 20} onClick={() => { if (ready && page.data.next !== null && cursors.length < 20) setCursors(old => [...old,page.data.next!]) }}>Next {kind}</button>
  </section>
}
