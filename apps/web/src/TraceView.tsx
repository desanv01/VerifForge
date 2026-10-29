import { useQuery } from '@tanstack/react-query'
import { hasCompleteIdentity, request } from './api'
import type { Campaign, DownloadableArtifact, Health, Result } from './api'
import { ARITHMETIC, campaignAdapter, evidenceAvailable, FIFO } from './families'
import { readArithmeticTrace } from './arithmeticTrace'

export function TraceView({ campaign, attemptId, result }: { campaign: Campaign; attemptId: string; result: Result }) {
  if (campaign.completion_policy && campaign.completion_policy !== 'single') return <p className="notice">This campaign contains multiple experiment records. Native trace inspection requires the selected attempt's authoritative experiment; the campaign seed plan is not used as a substitute.</p>
  const adapter = campaignAdapter(campaign)
  if (adapter === FIFO) return <><h3>FIFO transaction trace</h3><div className="table-scroll"><table><caption>Accepted transactions and independent observations (first 200 samples; large integer digits preserved)</caption><thead><tr><th>Cycle</th><th>Operation</th><th>Write / read accepted</th><th>Expected data</th><th>Observed data</th><th>Expected / observed flags</th></tr></thead><tbody>{result.trace.slice(0,200).map(t => <tr key={`${t.cycle}-${t.operation_index}`}><td>{t.cycle}</td><td>{t.operation_index}</td><td>{String(t.write_accepted)} / {String(t.read_accepted)}</td><td>{t.expected_data ?? '—'}</td><td>{t.observed_data ?? '—'}</td><td>Empty {String(t.expected_empty)} / {String(t.observed_empty)}; full {String(t.expected_full)} / {String(t.observed_full)}</td></tr>)}</tbody></table></div></>
  if (adapter === ARITHMETIC) return <ArithmeticTrace campaign={campaign} attemptId={attemptId} hasIdentity={hasCompleteIdentity(result.identity)}/>
  return <p className="notice">The saved family or trace schema is not supported. Common evidence remains inspectable; no FIFO trace was inferred.</p>
}

function ArithmeticTrace({ campaign, attemptId, hasIdentity }: { campaign: Campaign; attemptId: string; hasIdentity: boolean }) {
  const health = useQuery({queryKey:['health'],queryFn:() => request<Health>('/health'),staleTime:10000,refetchInterval:15000})
  const available = evidenceAvailable(health.data, ARITHMETIC) && !health.isError && hasIdentity
  const artifacts = useQuery({queryKey:['artifacts',attemptId],queryFn:() => request<{items:DownloadableArtifact[]}>(`/attempts/${encodeURIComponent(attemptId)}/artifacts`),enabled:available})
  const sidecar = artifacts.data?.items.find(a => a.path === 'arithmetic-trace.json' && a.downloadable)
  const trace = useQuery({queryKey:['arithmetic-trace',attemptId,sidecar?.artifact_id,sidecar?.sha256],queryFn:async () => readArithmeticTrace(await request<unknown>(`/attempts/${encodeURIComponent(attemptId)}/artifacts/${encodeURIComponent(sidecar!.artifact_id)}/download`),campaign.plan),enabled:available && !!sidecar})
  const error = health.error ?? artifacts.error ?? trace.error
  return <><h3>Arithmetic valid / product trace</h3><p className="meta">Native arithmetic.trace.v1 sidecar. Invalid-cycle product is unspecified; these observations do not establish a new checker verdict.</p>
    {error ? <div role="alert" className="notice error"><p>{error.message}</p>{trace.isError ? <button disabled={trace.isFetching} onClick={() => { void trace.refetch() }}>Retry arithmetic trace</button> : null}</div> : null}
    {!available && !health.isPending ? <p className="notice">Arithmetic trace inspection requires a complete evidence identity and server evidence support for rtl.arithmetic.v1.</p> : available && artifacts.isSuccess && !sidecar ? <p className="notice">No authorized arithmetic-trace.json sidecar is available. The empty common trace is not a passing arithmetic result.</p> : health.isPending || (available && (artifacts.isPending || (!!sidecar && trace.isPending))) ? <p role="status">Loading arithmetic trace…</p> : null}
    {trace.data && available && !trace.isError && !artifacts.isError ? <div className="table-scroll"><table><caption>Arithmetic observations (first 200 cycles; integer digits preserved)</caption><thead><tr><th>Cycle</th><th>Operation</th><th>Reset</th><th>Expected valid</th><th>Observed valid</th><th>Expected product</th><th>Observed product</th></tr></thead><tbody>{trace.data.slice(0,200).map(t => <tr key={t.cycle}><td>{t.cycle}</td><td>{t.operation_index}</td><td>{String(t.reset)}</td><td>{String(t.expected_valid)}</td><td>{String(t.observed_valid)}</td><td>{t.expected_product ?? 'Unspecified (bubble)'}</td><td>{t.observed_product}</td></tr>)}</tbody></table></div> : null}</>
}
