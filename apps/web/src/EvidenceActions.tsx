import { useRef, useState } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { downloadEvidence, humanize, mutate, request } from './api'
import type { Campaign, DownloadableArtifact, ExportJob, Health } from './api'
import { AXI_LITE, evidenceAvailable, familyName, FIFO } from './families'

function Failure({ error, retry }: { error: Error | null; retry?: () => void }) {
  return error ? <div role="alert" className="notice error"><p>{error.message}</p>{retry ? <button onClick={retry}>Retry evidence request</button> : null}</div> : null
}

export function EvidenceActions({ attemptId, adapterId = FIFO, hasIdentity, onReplay }: { attemptId: string; adapterId?: string | null; hasIdentity: boolean; onReplay: (campaign: Campaign) => void }) {
  const client = useQueryClient()
  const [exportId, setExportId] = useState(() => new URLSearchParams(location.search).get('export') ?? '')
  const replayKey = useRef(crypto.randomUUID())
  const exportKey = useRef(crypto.randomUUID())
  const cancelKey = useRef(crypto.randomUUID())
  const health = useQuery({ queryKey: adapterId === AXI_LITE ? ['health', 'axi-workbench'] : ['health'], queryFn: ({ signal }) => request<Health>('/health', { signal }), staleTime: adapterId === AXI_LITE ? 0 : 10000, refetchOnMount: 'always', retry: false, refetchInterval: 15000 })
  const available = evidenceAvailable(health.data, adapterId) && !health.isError && (adapterId !== AXI_LITE || (health.isSuccess && health.isFetchedAfterMount && !health.isFetching))
  const artifacts = useQuery({ queryKey: ['artifacts', attemptId], queryFn: () => request<{ items: DownloadableArtifact[] }>(`/attempts/${encodeURIComponent(attemptId)}/artifacts`), enabled: available && hasIdentity })
  const exportJob = useQuery({ queryKey: ['export', exportId], queryFn: () => request<ExportJob>(`/exports/${encodeURIComponent(exportId)}`), enabled: available && !!exportId, refetchInterval: q => ['queued', 'running'].includes(q.state.data?.status ?? '') ? 1500 : false })
  const job = exportJob.data?.attempt_id === attemptId ? exportJob.data : undefined
  const replay = useMutation({ mutationFn: () => mutate<Campaign>(`/attempts/${encodeURIComponent(attemptId)}/replay`, undefined, replayKey.current), onSuccess: campaign => { replayKey.current = crypto.randomUUID(); onReplay(campaign) } })
  const prepare = useMutation({ mutationFn: () => mutate<ExportJob>(`/attempts/${encodeURIComponent(attemptId)}/exports`, undefined, exportKey.current), onSuccess: job => {
    exportKey.current = crypto.randomUUID(); setExportId(job.id); client.setQueryData(['export', job.id], job)
    const url = new URL(location.href); url.searchParams.set('export', job.id); history.replaceState(null, '', url)
  } })
  const cancel = useMutation({ mutationFn: () => mutate<ExportJob>(`/exports/${encodeURIComponent(exportId)}/cancel`, undefined, cancelKey.current), onSuccess: job => { client.setQueryData(['export', job.id], job); cancelKey.current = crypto.randomUUID() } })
  const download = useMutation({ mutationFn: ({ path, filename }: { path: string; filename: string }) => downloadEvidence(path, filename) })
  const exportActive = job?.status === 'queued' || job?.status === 'running'
  const ready = available && hasIdentity && artifacts.isSuccess && !artifacts.isError
  const restoringExport = !!exportId && (!job || exportJob.isPending || exportJob.isError)
  return <section className="evidence-actions" aria-labelledby="evidence-actions-title">
    <h3 id="evidence-actions-title">Replay & portable evidence</h3>
    {adapterId === AXI_LITE ? <p className="notice">AXI-Lite reduction is unsupported. Candidate verification has no accepted application endpoint on this runtime; no repair action is available here.</p> : null}
    <p className="meta">Attempt record <code>{attemptId}</code></p>
    {health.isPending ? <p role="status">Checking evidence API support…</p> : !available ? <p className="notice">This server has not advertised evidence API support for {familyName(adapterId)}. Replay and export are unavailable for this family.</p> : !hasIdentity ? <p className="notice">This attempt has no complete checked identity. Portable evidence and exact replay are unavailable.</p> : null}
    <Failure error={health.error} retry={() => { void health.refetch() }}/>
    <Failure error={artifacts.error} retry={() => { void artifacts.refetch() }}/>
    {artifacts.isFetching && !artifacts.data ? <p role="status">Loading authorized artifacts…</p> : null}
    {artifacts.data ? <ul className="artifact-list">{artifacts.data.items.map(artifact => <li key={artifact.artifact_id}><div><strong>{artifact.path}</strong><span className="meta">{artifact.media_type} · {artifact.size_bytes.toLocaleString()} bytes</span><code>{artifact.sha256}</code></div>{artifact.downloadable ? <button disabled={!ready || download.isPending} onClick={() => download.mutate({ path: `/attempts/${encodeURIComponent(attemptId)}/artifacts/${encodeURIComponent(artifact.artifact_id)}/download`, filename: `artifact-${artifact.artifact_id}` })}>Download {artifact.path}</button> : <span className="meta">Metadata only</span>}</li>)}</ul> : null}
    {artifacts.data?.items.length === 0 ? <p className="muted">No authorized artifacts were recorded for this attempt.</p> : null}
    <p className="meta">Exact replay queues a new campaign immediately using the stored stimulus, seed, budget and identities. It calls no model. A queued replay is not yet a reproduced finding.</p>
    <div className="actions"><button disabled={!ready || replay.isPending} onClick={() => replay.mutate()}>{replay.isPending ? 'Queuing replay…' : 'Replay exact stimulus'}</button><button disabled={!ready || prepare.isPending || restoringExport || exportActive || job?.status === 'completed'} onClick={() => prepare.mutate()}>{prepare.isPending ? 'Requesting export…' : job?.status === 'failed' || job?.status === 'cancelled' ? 'Prepare new evidence bundle' : 'Prepare evidence bundle'}</button></div>
    <Failure error={replay.error}/><Failure error={prepare.error}/><Failure error={download.error}/>{download.isSuccess ? <p role="status" className="meta">Verified download requested.</p> : null}
    {exportId ? <div className="export-job"><h3>Evidence export</h3><code>{exportId}</code><Failure error={exportJob.error} retry={() => { void exportJob.refetch() }}/>{exportJob.isPending ? <p role="status">Restoring export status…</p> : null}{exportJob.data && !job ? <p role="alert" className="notice error">This export belongs to another attempt. Open the matching campaign to inspect it.</p> : null}{exportJob.isError || (exportJob.data && !job) ? <button onClick={() => { setExportId(''); const url = new URL(location.href); url.searchParams.delete('export'); history.replaceState(null, '', url) }}>Dismiss export view</button> : null}
    {job ? <><p role="status">Export status: <strong>{humanize(job.status)}</strong></p>{job.error_type ? <p role="alert">Export could not complete: {job.error_type}. Original evidence is unchanged.</p> : null}{job.status === 'queued' ? <p className="meta">Waiting for the evidence worker. This job is saved durably.</p> : null}{job.status === 'completed' && job.sha256 && job.size_bytes !== null ? <><p className="meta">Verified bundle · {job.size_bytes.toLocaleString()} bytes</p><code>{job.sha256}</code><button disabled={!ready || download.isPending} onClick={() => download.mutate({ path: `/exports/${encodeURIComponent(job.id)}/download`, filename: `verifforge-${job.id}.zip` })}>Download evidence bundle</button></> : null}{exportActive ? <button disabled={!available || cancel.isPending} onClick={() => cancel.mutate()}>{cancel.isPending ? 'Cancelling export…' : 'Cancel export'}</button> : null}</> : null}<Failure error={cancel.error}/></div> : null}
    <p className="meta">Only portable, server-verified files can be downloaded. Logs and private environment diagnostics remain metadata. The server rechecks evidence before replay or download.</p>
  </section>
}
