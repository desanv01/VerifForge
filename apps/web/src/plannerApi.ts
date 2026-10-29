import type { FifoExperiment, Project } from './api'
import { FIFO, projectAdapter } from './families'
import { parseInspectionJson, stringifySubmission } from './json'

export const PLANNER_MAX_BYTES = 262144
export const PLANNER_PAGE_SIZE = 25
const MAX = 2147483647
const fixtures = ['fifo_correct', 'fifo_reset_bug', 'fifo_full_bug', 'fifo_order_bug']
export const curatedPlannerProject = (p?: Project) => !!p && projectAdapter(p) === FIFO && fixtures.includes(p.id) && p.contract_id === 'fifo-v1'
export class PlannerError extends Error { constructor(public status: number, message: string) { super(message) } }
const invalid = (): never => { throw new PlannerError(0, 'Planner metadata is invalid, unsupported or inconsistent. No planner state was interpreted.') }
const obj = (v: unknown): Record<string, unknown> => { if (!v || typeof v !== 'object' || Array.isArray(v)) return invalid(); return v as Record<string, unknown> }
const exact = (v: unknown, fields: string[]) => { const o = obj(v); if (Object.keys(o).length !== fields.length || fields.some(k => !Object.hasOwn(o, k))) invalid(); return o }
export function plannerId(v: unknown): string { if (typeof v !== 'string' || !/^[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}$/.test(v)) return invalid(); return v }
const integer = (v: unknown, max = MAX, min = 0): number => { if (typeof v !== 'number' || !Number.isSafeInteger(v) || v < min || v > max) return invalid(); return v }
const positive = (v: unknown, max: number): number => { if (typeof v !== 'number' || !Number.isFinite(v) || v <= 0 || v > max) return invalid(); return v }
const bool = (v: unknown): boolean => { if (typeof v !== 'boolean') return invalid(); return v }
const one = <T extends string>(v: unknown, values: readonly T[]): T => { if (!values.includes(v as T)) return invalid(); return v as T }
const literal = (v: unknown, expected: unknown) => { if (v !== expected) invalid() }
const nullableId = (v: unknown) => v === null ? null : plannerId(v)
const date = (v: unknown): string => { if (typeof v !== 'string' || v.length > 40 || !/^\d{4}-\d\d-\d\dT[\d:.]+(?:Z|[+-]\d\d:\d\d)$/.test(v) || !Number.isFinite(Date.parse(v))) return invalid(); return v }
const nullableDate = (v: unknown) => v === null ? null : date(v)
// Preserve decimal spelling; never convert unknown cost to zero or round it.
const decimal = (v: unknown, nonzero = false): string => { if (typeof v !== 'string' || v.length > 96 || !/^(?:0|[1-9]\d*)(?:\.\d+)?(?:[eE][+-]?\d{1,3})?$/.test(v) || !Number.isFinite(Number(v)) || (nonzero && Number(v) <= 0)) return invalid(); return v }
export const executionStates = ['draft', 'queued', 'preparing', 'running', 'finalizing', 'completed', 'interrupted', 'cancelled', 'infrastructure_error'] as const
const verdicts = ['pass_within_scope', 'design_mismatch', 'property_failure', 'build_error', 'harness_error', 'timeout', 'resource_limit', 'unsupported', 'inconclusive'] as const
const verdict = (v: unknown) => v === null ? null : one(v, verdicts)
export function plannerHealth(raw: unknown) {
  const o = obj(raw)
  return { available: o.status === 'ok' && o.planner_api === '1' && typeof o.planner_admission_enabled === 'boolean' && o.model === 'not_integrated', admission: o.planner_admission_enabled === true }
}
export function plannerLimits(raw: unknown) {
  const o = exact(raw, ['max_rounds','max_total_cycles','max_cycles_per_experiment','max_wall_seconds','max_execution_seconds','max_model_seconds','max_input_bytes','max_output_tokens','max_model_tokens','max_cost_usd','max_invalid_proposals','max_transient_errors','max_duplicate_proposals','max_feedback_items','stop_on_finding'])
  return { max_rounds: integer(o.max_rounds,100,1), max_total_cycles: integer(o.max_total_cycles,10000000,1), max_cycles_per_experiment: integer(o.max_cycles_per_experiment,1000000,1), max_wall_seconds: positive(o.max_wall_seconds,3600), max_execution_seconds: positive(o.max_execution_seconds,3600), max_model_seconds: positive(o.max_model_seconds,120), max_input_bytes: integer(o.max_input_bytes,1000000,1), max_output_tokens: integer(o.max_output_tokens,32000,1), max_model_tokens: integer(o.max_model_tokens,100000000,1), max_cost_usd: o.max_cost_usd === null ? null : decimal(o.max_cost_usd,true), max_invalid_proposals: integer(o.max_invalid_proposals,10,1), max_transient_errors: integer(o.max_transient_errors,10,1), max_duplicate_proposals: integer(o.max_duplicate_proposals,10,1), max_feedback_items: integer(o.max_feedback_items,20,1), stop_on_finding: bool(o.stop_on_finding) }
}
export function plannerStatus(raw: unknown, campaignId: string) {
  const o = exact(raw, ['schema_version','campaign_id','execution_status','verdict','admission_enabled','strategy','requested_model','phase','generation','stop_reason','started_at','deadline_at','limits','price_status'])
  literal(o.schema_version,'planner.status.v1'); literal(plannerId(o.campaign_id),campaignId)
  if (typeof o.requested_model !== 'string' || !/^[A-Za-z0-9][A-Za-z0-9_.:/-]{0,127}$/.test(o.requested_model)) invalid()
  const started = date(o.started_at), deadline = date(o.deadline_at)
  if (Date.parse(deadline) <= Date.parse(started)) invalid()
  return { campaignId, execution: one(o.execution_status,executionStates), verdict: verdict(o.verdict), admission: bool(o.admission_enabled), strategy: one(o.strategy,['meta','mock_planner']), model: o.requested_model as string, phase: one(o.phase,['ready','round_queued','model_running','waiting_execution','settled','cancelled','interrupted']), generation: integer(o.generation), stopReason: nullableId(o.stop_reason), started, deadline, limits: plannerLimits(o.limits), priceStatus: one(o.price_status,['known','unknown']) }
}
export function plannerBudget(raw: unknown, campaignId: string) {
  const o = exact(raw,['schema_version','campaign_id','limits','reserved','price_status'])
  literal(o.schema_version,'planner.budget.v1'); literal(plannerId(o.campaign_id),campaignId)
  const r = exact(o.reserved,['tokens','cycles','seconds','cost']), priceStatus = one(o.price_status,['known','unknown'])
  if (priceStatus === 'unknown' && r.cost !== null) invalid()
  return { campaignId, limits: plannerLimits(o.limits), tokens: integer(r.tokens,Number.MAX_SAFE_INTEGER), cycles: integer(r.cycles,Number.MAX_SAFE_INTEGER), seconds: decimal(r.seconds), cost: priceStatus === 'unknown' ? null : decimal(r.cost), priceStatus }
}
export function plannerPage(raw: unknown, after: number, kind: 'rounds' | 'cleanup') {
  integer(after); const o = exact(raw,['items','next_cursor'])
  if (!Array.isArray(o.items) || o.items.length > PLANNER_PAGE_SIZE) invalid()
  let previous = after
  const ids = new Set<string>()
  const items = (o.items as unknown[]).map(value => {
    const row = exact(value,kind === 'rounds' ? ['round_id','ordinal','generation','status','experiment_id','heartbeat_at','finished_at'] : ['round_id','ordinal','generation','local_state','registered_at','owner_lost_at','settled_at'])
    const id = plannerId(row.round_id), ordinal = integer(row.ordinal,MAX,1), generation = integer(row.generation)
    if (ordinal <= previous || ids.has(id)) invalid(); previous = ordinal; ids.add(id)
    if (kind === 'rounds') return { id, ordinal, generation, status: one(row.status,['queued','running','accepted','invalid','duplicate','failed','stopped','uncertain','cancelled']), experiment: nullableId(row.experiment_id), heartbeat: nullableDate(row.heartbeat_at), finished: nullableDate(row.finished_at), registered: null, ownerLost: null, settled: null }
    const status = one(row.local_state,['pending','unknown','settled']), registered = date(row.registered_at), ownerLost = nullableDate(row.owner_lost_at), settled = nullableDate(row.settled_at)
    if ((status === 'pending' && (ownerLost || settled)) || (status === 'unknown' && (!ownerLost || settled)) || (status === 'settled' && !settled) || (ownerLost && Date.parse(ownerLost)<Date.parse(registered)) || (settled && Date.parse(settled)<Date.parse(ownerLost ?? registered))) invalid()
    return { id, ordinal, generation, status, registered, ownerLost, settled, experiment: null, heartbeat: null, finished: null }
  })
  const next = o.next_cursor === null ? null : integer(o.next_cursor,MAX,1)
  if (next !== null && (items.length !== PLANNER_PAGE_SIZE || next !== previous || next <= after)) invalid()
  return { items, next }
}
export function plannerSeed(raw: unknown) {
  const o = exact(raw,['schema_version','campaign_id','execution_status','verdict','completion_policy','planner_attached'])
  literal(o.schema_version,'planner.seed.v1'); literal(o.completion_policy,'sequential')
  return { campaignId: plannerId(o.campaign_id), execution: one(o.execution_status,executionStates), verdict: verdict(o.verdict), attached: bool(o.planner_attached) }
}
export function plannerPlan(raw: unknown, projectId: string): FifoExperiment {
  const o = exact(raw,['schema_version','experiment_id','fixture_id','contract_id','target','objective_ids','seed','operations','parameters','budget'])
  literal(o.schema_version,'1'); literal(o.target,'fifo'); literal(o.contract_id,'fifo-v1'); literal(o.fixture_id,projectId)
  if (!fixtures.includes(projectId)) invalid(); plannerId(o.experiment_id); integer(o.seed,0xffffffff)
  if (!Array.isArray(o.objective_ids) || !o.objective_ids.length || o.objective_ids.length > 100 || new Set(o.objective_ids).size !== o.objective_ids.length) invalid()
  ;(o.objective_ids as unknown[]).forEach(plannerId)
  const p = exact(o.parameters,['width','depth']), width = integer(p.width,64,1); integer(p.depth,1024,2)
  const b = exact(o.budget,['max_cycles','wall_time_seconds','max_artifact_bytes']); integer(b.max_cycles,1000000,1); positive(b.wall_time_seconds,3600); integer(b.max_artifact_bytes,1073741824,1024)
  if (!Array.isArray(o.operations) || !o.operations.length || o.operations.length > 1000) invalid()
  let cycles = 0, reset = false
  for (const value of o.operations as unknown[]) {
    const op = obj(value)
    if (op.op === 'reset' || op.op === 'idle') { exact(op,['op','cycles']); cycles += integer(op.cycles,1000000,1); if (op.op === 'reset') reset = true }
    else if (op.op === 'read') { exact(op,['op']); cycles++; if (!reset) invalid() }
    else if (op.op === 'write' || op.op === 'cycle') {
      exact(op,op.op === 'write' ? ['op','value'] : ['op','reset','write','read','value'])
      const data = integer(op.value,Number.MAX_SAFE_INTEGER); if (BigInt(data) >= 1n << BigInt(width)) invalid()
      if (op.op === 'cycle') { if (bool(op.reset)) reset = true; bool(op.write); bool(op.read) }
      if (!reset && (op.op === 'write' || op.write || op.read)) invalid(); cycles++
    } else invalid()
  }
  if (!reset || cycles > (b.max_cycles as number)) invalid()
  return structuredClone(o) as unknown as FifoExperiment
}
export const plannerFingerprint = (v: unknown): string => JSON.stringify(v, (_k,value) => value && typeof value === 'object' && !Array.isArray(value) ? Object.fromEntries(Object.entries(value).sort(([a],[b])=>a.localeCompare(b))) : value)
export function plannerCampaign(raw: unknown, campaignId: string, projectId: string, seed?: FifoExperiment) {
  const o = obj(raw); literal(plannerId(o.id),campaignId); literal(o.project_id,projectId); literal(o.adapter_id,FIFO); literal(o.contract_id,'fifo-v1')
  const plan = plannerPlan(o.plan,projectId), completion = one(o.completion_policy,['single','sequential']), execution = one(o.execution_status,executionStates)
  if (seed && (completion !== 'sequential' || plannerFingerprint(plan) !== plannerFingerprint(seed))) invalid()
  if (!Array.isArray(o.attempts) || o.attempts.length > 1000) invalid()
  const ids = new Set<string>(), records = new Set<string>()
  const attempts = (o.attempts as unknown[]).map(value => { const a=obj(value), id=plannerId(a.id); if(ids.has(id))invalid();ids.add(id); records.add(plannerId(a.experiment_record_id)); const execution=one(a.execution_status,executionStates), result=verdict(a.verdict); return execution==='completed' && ['pass_within_scope','design_mismatch','property_failure'].includes(String(result)) })
  const settled = attempts.length > 0 && attempts.every(Boolean)
  return { campaignId, projectId, execution, completion, eligible: completion === 'sequential' && execution === 'running' && settled && records.size === 1 }
}
// Validate object-key uniqueness before any capability, identity or policy is
// interpreted. Decode key escapes so "key" and "\u006bey" are the same key.
// This scanner retains no numeric values; the shared inspection parser remains
// responsible for preserving unsafe numeric lexemes and exact decimal strings.
function parsePlannerJson(source: string): unknown {
  let cursor = 0
  const whitespace = () => { while (/[\x20\t\r\n]/.test(source[cursor] ?? '')) cursor++ }
  const stringToken = () => {
    const start = cursor
    if (source[cursor++] !== '"') invalid()
    while (cursor < source.length) {
      const character = source[cursor++]
      if (character === '\\') cursor++
      else if (character === '"') return source.slice(start, cursor)
    }
    return invalid()
  }
  const scalar = /(?:true|false|null|-?(?:0|[1-9]\d*)(?:\.\d+)?(?:[eE][+-]?\d+)?)/y
  function value(depth: number): void {
    if (depth > 64) invalid()
    whitespace()
    const opening = source[cursor]
    if (opening === '{' || opening === '[') {
      cursor++; whitespace()
      const closing = opening === '{' ? '}' : ']'
      const keys = new Set<string>()
      if (source[cursor] === closing) { cursor++; return }
      while (cursor < source.length) {
        if (opening === '{') {
          whitespace()
          const key: string = JSON.parse(stringToken())
          if (keys.has(key)) invalid()
          keys.add(key); whitespace()
          if (source[cursor++] !== ':') invalid()
        }
        value(depth + 1); whitespace()
        if (source[cursor] === closing) { cursor++; return }
        if (source[cursor++] !== ',') invalid()
      }
      invalid()
    } else if (opening === '"') stringToken()
    else {
      scalar.lastIndex = cursor
      const token = scalar.exec(source)
      if (!token) invalid()
      cursor = scalar.lastIndex
    }
  }
  value(0); whitespace()
  if (cursor !== source.length) invalid()
  // Validate the original full JSON grammar before numeric preservation.
  return parseInspectionJson(source)
}
export async function plannerRequest<T>(path: string, parse: (raw: unknown) => T, init: RequestInit = {}): Promise<T> {
  const advice = init.method?.toUpperCase() === 'POST'
    ? 'The operation outcome is unknown; it may already have been accepted. Read the current campaign/planner records where available. To retry explicitly, use the same action with its exact request and Idempotency-Key; do not create a new intent or key.'
    : 'Planner records could not be read. Refresh the current selection to retry.'
  const failure = (status: number, reason: string) => new PlannerError(status, `${reason} ${advice}`)
  let response: Response
  try { response = await fetch(`/api${path}`, { ...init, signal: init.signal ? AbortSignal.any([init.signal,AbortSignal.timeout(10000)]) : AbortSignal.timeout(10000), headers: { 'Content-Type':'application/json', ...init.headers } }) }
  catch { throw failure(0,'Planner request could not complete.') }
  if (!response.ok) { await response.body?.cancel().catch(() => undefined); throw failure(response.status,`Planner request failed (HTTP ${response.status}).`) }
  try {
    if (!response.body) invalid()
    const reader=response.body!.getReader(), chunks:Uint8Array[]=[];let size=0
    try { while(true) { const {done,value}=await reader.read();if(done)break;size+=value.byteLength;if(size>PLANNER_MAX_BYTES){await reader.cancel();invalid()}chunks.push(value) } }
    finally { reader.releaseLock() }
    const bytes=new Uint8Array(size);let offset=0;for(const chunk of chunks){bytes.set(chunk,offset);offset+=chunk.length}
    return parse(parsePlannerJson(new TextDecoder('utf-8',{fatal:true}).decode(bytes)))
  } catch (error) { throw failure(0, error instanceof PlannerError ? error.message : 'Planner response was invalid or could not be read.') }
}
export const plannerPost = <T>(path:string,body:unknown,key:string,parse:(raw:unknown)=>T) => plannerRequest(path,parse,{method:'POST',headers:{'Idempotency-Key':key},body:body===undefined?undefined:stringifySubmission(body)})
