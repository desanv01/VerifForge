import { identifier } from './engineeringInvestigationApi'
import type { VersionSummary } from './engineeringInvestigationApi'
import { parseInspectionJson } from './json'

export type EdaDomain = 'vivado' | 'kicad'
export interface EdaRequest { project_id: string; original_version_id: string; candidate_version_id: string }
export interface EdaBinding { domain: EdaDomain; request: EdaRequest }
export const EDA_STATES = ['preparing','blocked','queued','claimed','completed','failed','cancelled','quarantined'] as const
export const EDA_BLOCKERS = ['missing_io_requirements','unreviewed_mapping','unsupported_configuration'] as const
type EdaState = typeof EDA_STATES[number]
interface Completion { ticket_hash: string; configuration_hash: string; native_result_sha256: string; retained: true; settled: true; execution_status: 'completed'|'failed'|'cancelled'; design_verdict: 'not_evaluated' }
export interface EdaJob extends EdaRequest { job_id: string; domain: EdaDomain; status: EdaState; cancel_requested: boolean; blockers: (typeof EDA_BLOCKERS[number])[]; result: Completion|null; design_verdict: 'not_evaluated'; physical_electrical_verdict: 'inconclusive' }
export class EdaRepairError extends Error { constructor(public status: number, message: string) { super(message) } }
function invalid(): never { throw new EdaRepairError(0,'Native candidate metadata is invalid or changed. No result was interpreted.') }
function closed(raw: unknown, fields: string[]): Record<string,unknown> { if(!raw||typeof raw!=='object'||Array.isArray(raw))invalid();const o=raw as Record<string,unknown>;if(Object.keys(o).length!==fields.length||fields.some(k=>!Object.hasOwn(o,k)))invalid();return o }
function one<T extends string>(v: unknown, values: readonly T[]): T { if(!values.includes(v as T))invalid();return v as T }
function hash(v: unknown): string { if(typeof v!=='string'||! /^[a-f0-9]{64}$/.test(v))invalid();return v }
export function parseEdaRequest(raw: unknown): EdaRequest { const o=closed(raw,['project_id','original_version_id','candidate_version_id']);const r={project_id:identifier(o.project_id),original_version_id:identifier(o.original_version_id),candidate_version_id:identifier(o.candidate_version_id)};if(r.original_version_id===r.candidate_version_id)invalid();return r }
export function edaDomain(version: VersionSummary): EdaDomain { if(version.schemaVersion!=='engineering.admission.v3'||version.domains.length!==1)invalid();return one(version.domains[0],['vivado','kicad']) }
export function edaBinding(original: VersionSummary, candidate: VersionSummary): EdaBinding { const domain=edaDomain(original);if(candidate.projectId!==original.projectId||edaDomain(candidate)!==domain)invalid();return {domain,request:parseEdaRequest({project_id:original.projectId,original_version_id:original.id,candidate_version_id:candidate.id})} }
export function parseEdaJob(raw: unknown, binding: EdaBinding, jobId?: string): EdaJob {
  const o=closed(raw,['job_id','domain','project_id','original_version_id','candidate_version_id','status','cancel_requested','blockers','result','design_verdict','physical_electrical_verdict'])
  const request=parseEdaRequest({project_id:o.project_id,original_version_id:o.original_version_id,candidate_version_id:o.candidate_version_id})
  if(Object.entries(request).some(([key,value])=>value!==binding.request[key as keyof EdaRequest])||o.domain!==binding.domain||jobId!==undefined&&o.job_id!==jobId||o.design_verdict!=='not_evaluated'||o.physical_electrical_verdict!=='inconclusive'||typeof o.cancel_requested!=='boolean')invalid()
  if(!Array.isArray(o.blockers)||o.blockers.length>3)invalid()
  const blockers=o.blockers.map(v=>one(v,EDA_BLOCKERS));if(new Set(blockers).size!==blockers.length)invalid()
  const status=one(o.status,EDA_STATES)
  let result: Completion|null=null
  if(o.result!==null){const r=closed(o.result,['ticket_hash','configuration_hash','native_result_sha256','retained','settled','execution_status','design_verdict']);if(r.retained!==true||r.settled!==true||r.design_verdict!=='not_evaluated')invalid();result={ticket_hash:hash(r.ticket_hash),configuration_hash:hash(r.configuration_hash),native_result_sha256:hash(r.native_result_sha256),retained:true,settled:true,execution_status:one(r.execution_status,['completed','failed','cancelled']),design_verdict:'not_evaluated'}}
  if(status==='blocked'&&!blockers.length||['preparing','queued','claimed','completed','failed'].includes(status)&&blockers.length||['completed','failed'].includes(status)&&(!result||result.execution_status!==status)||result&&!['completed','failed','cancelled','quarantined'].includes(status)||status==='cancelled'&&!o.cancel_requested&&result?.execution_status==='completed')invalid()
  return {...request,job_id:identifier(o.job_id),domain:binding.domain,status,cancel_requested:o.cancel_requested,blockers,result,design_verdict:'not_evaluated',physical_electrical_verdict:'inconclusive'}
}
export function edaTerminal(job: EdaJob) { return !['preparing','queued','claimed'].includes(job.status) }
// Polling can stop while native effects remain unknown. Such jobs retain their
// original request/key and cannot authorize a replacement operation.
export function edaReplaceable(job: EdaJob) { return ['blocked','completed','failed','cancelled'].includes(job.status) }
export function compatibleEdaJob(previous: EdaJob|null, next: EdaJob): EdaJob {
  // A cancelled preparation may acquire its ticket after cancellation. Once
  // blockers or a prepared/claimed outcome were observed, ticket scope is fixed.
  if(previous&&(previous.blockers.length>0||['queued','claimed','blocked','completed','failed','quarantined'].includes(previous.status))&&[...previous.blockers].sort().join()!==[...next.blockers].sort().join())invalid()
  if(previous&&(previous.job_id!==next.job_id||previous.domain!==next.domain||Object.keys(previous).filter(k=>['project_id','original_version_id','candidate_version_id'].includes(k)).some(k=>previous[k as keyof EdaJob]!==next[k as keyof EdaJob])||previous.cancel_requested&&!next.cancel_requested||previous.result&&JSON.stringify(previous.result)!==JSON.stringify(next.result)||edaTerminal(previous)&&previous.status!==next.status&&!(previous.status==='blocked'&&next.status==='cancelled'&&next.cancel_requested)||previous.status==='claimed'&&['preparing','queued','blocked'].includes(next.status)||previous.status==='queued'&&['preparing','blocked'].includes(next.status)))invalid()
  return next
}
export const EDA_POLL_WINDOW=5*60*1000
export function edaPollInterval(job: EdaJob|undefined, until: number) { return !job||edaTerminal(job)||Date.now()>=until?false:1500 }
export async function edaRequest<T>(path: string, parse: (raw: unknown)=>T, options: {method?: 'GET'|'POST';body?: EdaRequest;key?: string;signal?: AbortSignal}={}): Promise<T> {
  const method=options.method??'GET',signal=options.signal?AbortSignal.any([options.signal,AbortSignal.timeout(10000)]):AbortSignal.timeout(10000)
  let response: Response
  try { response=await fetch(`/api${path}`,{method,cache:'no-store',signal,headers:{Accept:'application/json',...(options.body?{'Content-Type':'application/json'}:{}),...(options.key?{'Idempotency-Key':identifier(options.key)}:{})},body:options.body?JSON.stringify(parseEdaRequest(options.body)):undefined}) }
  catch { throw new EdaRepairError(0,method==='POST'?'Submission outcome is unknown. Retry only the preserved request with the same key.':'The native candidate API is unavailable. Refresh explicitly when reachable.') }
  if(!response.ok){await response.body?.cancel().catch(()=>undefined);throw new EdaRepairError(response.status,[404,405,409,503].includes(response.status)?'Native candidate execution is unavailable, disabled or conflicts with this selection. No automatic retry.':`Native candidate request failed (HTTP ${response.status}). No automatic retry.`)}
  const reader=response.body?.getReader();if(!reader)invalid()
  let size=0,raw='';const decoder=new TextDecoder('utf-8',{fatal:true})
  try { while(true){const {done,value}=await reader.read();if(done)break;size+=value.byteLength;if(size>65536)invalid();raw+=decoder.decode(value,{stream:true})}raw+=decoder.decode();if(signal.aborted)invalid();return parse(parseInspectionJson(raw)) }
  catch { invalid() } finally { await reader.cancel().catch(()=>undefined);reader.releaseLock() }
}
export interface PendingEda { binding: EdaBinding; key: string; jobId: string|null }
export function readPendingEda(storageKey: string, original: VersionSummary): PendingEda|null { const text=sessionStorage.getItem(storageKey);if(text===null)return null;if(text.length>4096)invalid();const o=closed(JSON.parse(text),['binding','key','jobId']),b=closed(o.binding,['domain','request']);const request=parseEdaRequest(b.request);if(b.domain!==edaDomain(original)||request.project_id!==original.projectId||request.original_version_id!==original.id)invalid();return {binding:{domain:b.domain as EdaDomain,request},key:identifier(o.key),jobId:o.jobId===null?null:identifier(o.jobId)} }
