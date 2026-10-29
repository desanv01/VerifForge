import { useQuery } from '@tanstack/react-query'
import { request } from './api'
import type { Campaign, DownloadableArtifact, Health, Result } from './api'
import { evidenceAvailable } from './families'
import { AXI_LITE, AXI_TRACE_ARTIFACT, axiContextError, hex32, readAxiTrace } from './axiLite'
import { axiScenario } from './axiLitePlans'

export function AxiScenarioDetails({ projectId }: { projectId: string }) {
  const scenario = axiScenario(projectId)
  if (!scenario) return null
  return <section aria-label="Selected AXI scenario"><h3>{scenario.label}</h3>
    <p>Choose a curated project above to select its matching control or seeded fault. Each uses a frozen cycle schedule; no random stimulus is generated.</p>
    <p className="meta">Seed 0 · {scenario.cycles.length} exact stimulus cycles · budget 10,000 cycles / 120 seconds / 10 MiB artifacts</p>
    <details><summary>Exact scenario stimulus and reference identity</summary><p>Reviewed experiment SHA-256</p><code>{scenario.sourceExperimentHash}</code><p>The server independently binds the saved experiment and selected fixture to its evidence identity.</p><pre>{JSON.stringify(scenario.cycles, null, 2)}</pre></details>
  </section>
}

export function AxiTrace({ campaign, attemptId, result }: { campaign: Campaign; attemptId: string; result: Result }) {
  const contextError = axiContextError(campaign, result)
  const health = useQuery({ queryKey: ['health', 'axi-workbench'], queryFn: ({ signal }) => request<Health>('/health', { signal }), retry: false, staleTime: 0, refetchOnMount: 'always', refetchInterval: 15000 })
  const available = health.isSuccess && health.isFetchedAfterMount && !health.isFetching && evidenceAvailable(health.data, AXI_LITE) && !contextError
  const artifacts = useQuery({ queryKey: ['axi-artifacts', attemptId, result.identity?.experiment_hash], queryFn: ({ signal }) => request<{ items: DownloadableArtifact[] }>(`/attempts/${encodeURIComponent(attemptId)}/artifacts`, { signal }), enabled: available, retry: false })
  const matches = artifacts.data?.items.filter(a => a.path === AXI_TRACE_ARTIFACT) ?? []
  const references = result.artifacts.filter(a => a.path === AXI_TRACE_ARTIFACT)
  const candidate = matches.length === 1 ? matches[0] : undefined
  const sidecar = candidate?.downloadable && references.length === 1 && candidate.artifact_id === references[0].artifact_id && /^[a-f0-9]{64}$/.test(candidate.sha256) && candidate.sha256 === references[0].sha256 && Number.isSafeInteger(candidate.size_bytes) && candidate.size_bytes > 0 && candidate.size_bytes === references[0].size_bytes ? candidate : undefined
  const trace = useQuery({ queryKey: ['axi-trace', attemptId, result.identity?.experiment_hash, sidecar?.artifact_id, sidecar?.sha256], queryFn: async ({ signal }) => readAxiTrace(await request<unknown>(`/attempts/${encodeURIComponent(attemptId)}/artifacts/${encodeURIComponent(sidecar!.artifact_id)}/download`, { signal }), campaign.plan), enabled: available && artifacts.isSuccess && !artifacts.isFetching && !!sidecar, retry: false })
  const error = health.error ?? artifacts.error ?? trace.error
  return <section aria-label="AXI native trace"><h3>AXI-Lite AW / W / B / AR / R trace</h3>
    <p className="meta">Recorded outputs from axi_lite.trace.v1 paired with the saved exact inputs, sampled before each rising edge. Transfer means VALID and READY while reset is released. This view does not compute a new checker verdict.</p>
    {contextError ? <p role="alert" className="notice error">{contextError}</p> : null}
    {error ? <div role="alert" className="notice error"><p>{error.message}</p><button onClick={() => { if (health.isError) void health.refetch(); else if (artifacts.isError) void artifacts.refetch(); else void trace.refetch() }}>Retry AXI trace</button></div> : null}
    {!contextError && !error && !available && !health.isFetching ? <p className="notice">Native AXI inspection requires advertised evidence support for rtl.axi_lite.v1.</p> : null}
    {available && artifacts.isSuccess && !sidecar ? <p role="alert" className="notice">No uniquely authorized observed.json matches this attempt’s artifact identity. An empty common trace is not a passing AXI result.</p> : null}
    {!contextError && (health.isFetching || (available && (artifacts.isPending || artifacts.isFetching || (!!sidecar && trace.isPending)))) ? <p role="status">Loading AXI trace…</p> : null}
    {trace.data && available && artifacts.isSuccess && !artifacts.isFetching && !!sidecar && !trace.isError && !trace.isFetching ? <div className="table-scroll"><table><caption>AXI observations (first 200 of {trace.data.length} cycles). V = VALID, R = READY. Response codes: 0 OKAY, 1 EXOKAY, 2 SLVERR, 3 DECERR.</caption><thead><tr><th>Cycle / reset_n</th><th>AW — write address</th><th>W — write data</th><th>B — write response</th><th>AR — read address</th><th>R — read response</th></tr></thead><tbody>{trace.data.slice(0, 200).map(({ input: i, observed: o }) => <tr key={o.cycle} aria-label={result.failure_signature?.first_cycle === o.cycle ? `First divergence cycle ${o.cycle}` : undefined}>
      <td>{o.cycle} / {String(i.reset_n)}{result.failure_signature?.first_cycle === o.cycle ? <strong> First divergence</strong> : null}</td>
      <td>V {String(i.aw_valid)} / R {String(o.aw_ready)}<br/>Address {hex32(i.aw_addr)} · PROT {i.aw_prot}<br/>Transfer {String(i.reset_n && i.aw_valid && o.aw_ready)}</td>
      <td>V {String(i.w_valid)} / R {String(o.w_ready)}<br/>Data {hex32(i.w_data)} · STRB {i.w_strb.toString(2).padStart(4, '0')}<br/>Transfer {String(i.reset_n && i.w_valid && o.w_ready)}</td>
      <td>V {String(o.b_valid)} / R {String(i.b_ready)}<br/>RESP {o.b_resp}{!o.b_valid ? ' (not valid)' : ''}<br/>Transfer {String(i.reset_n && o.b_valid && i.b_ready)}</td>
      <td>V {String(i.ar_valid)} / R {String(o.ar_ready)}<br/>Address {hex32(i.ar_addr)} · PROT {i.ar_prot}<br/>Transfer {String(i.reset_n && i.ar_valid && o.ar_ready)}</td>
      <td>V {String(o.r_valid)} / R {String(i.r_ready)}<br/>Data {hex32(o.r_data)} · RESP {o.r_resp}{!o.r_valid ? ' (not valid)' : ''}<br/>Transfer {String(i.reset_n && o.r_valid && i.r_ready)}</td>
    </tr>)}</tbody></table></div> : null}
  </section>
}
