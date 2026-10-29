export interface Project { id: string; name: string; family: string; support_level: string; contract_id: string; adapter_id?: string; experiment_schema_version?: string; trace_schema?: string | null; trace_artifact?: string | null }
export type FamilyParameters = { width?: number; depth?: number; latency?: number } & Partial<AxiLiteParameters>
export interface Contract { contract_id: string; version?: string; review_status: string; checker: { checker_id: string; version: string; sha256: string }; parameters: FamilyParameters; requirements: { requirement_id: string; description: string }[]; assumptions: string[]; target?: string; schema_version?: string; reset_priority?: string; acceptance?: string; read_semantics?: string; timing?: string; reset?: string; bubbles?: string; sampling?: string; read_write_collision?: string; invalid_access?: string }
export interface Signature { requirement_id: string; failure_class: string; first_cycle: number; expected: string; observed: string; signature_hash: string }
export interface Artifact { artifact_id: string; path: string; sha256: string; size_bytes: number; media_type: string }
export interface DownloadableArtifact extends Artifact { downloadable: boolean }
export interface Health { status: string; evidence_api?: string; evidence_adapters?: string[] }
export interface ExportJob { id: string; attempt_id: string; status: 'queued' | 'running' | 'completed' | 'failed' | 'cancelled'; error_type: string | null; sha256: string | null; size_bytes: number | null }
export interface Trace { cycle: number; operation_index: number; read_accepted: boolean; write_accepted: boolean; expected_data: InspectionInteger | null; observed_data: InspectionInteger | null; expected_empty: boolean | null; observed_empty: boolean | null; expected_full: boolean | null; observed_full: boolean | null }
export interface Result { attempt_id?: string; experiment_id?: string; execution_status?: string; identity: Record<string, string> | null; tool_versions: { name: string; version: string }[]; trace: Trace[]; artifacts: Artifact[]; uncertainties: string[]; failure_signature: Signature | null }
export interface Finding { id: string; attempt_id: string; status: string; verdict: string; result: Result; contract_id: string; plan_hash: string }
export interface Campaign { identity?: Record<string, string>; id: string; project_id: string; contract_id: string; adapter_id?: string; experiment_schema_version?: string; trace_schema?: string | null; trace_artifact?: string | null; completion_policy?: string; source_attempt_id?: string | null; contract_snapshot?: Contract; execution_status: string; verdict: string | null; version: number; created_at: string; updated_at: string; plan: SavedExperiment; attempts: { id: string; experiment_record_id?: string; execution_status: string; verdict: string | null; result: Result | null }[]; findings: Finding[] }
export interface RunEvent { sequence: number; kind: string; payload: unknown; created_at: string }
interface ExperimentBase { experiment_id: string; fixture_id: string; contract_id: string; objective_ids: string[]; seed: number; budget: { max_cycles: number; wall_time_seconds: number; max_artifact_bytes: number } }
export interface FifoExperiment extends ExperimentBase { schema_version: '1'; target: 'fifo'; operations: ({ op: 'reset' | 'idle'; cycles: number } | { op: 'write'; value: number } | { op: 'read' } | { op: 'cycle'; reset: boolean; write: boolean; read: boolean; value: number })[]; parameters: { width: number; depth: number } }
export interface ArithmeticExperiment extends ExperimentBase { schema_version: 'arithmetic.v1'; target: 'arithmetic'; adapter: 'rtl.arithmetic.v1'; operations: ({ op: 'reset' | 'idle'; cycles: number } | { op: 'multiply'; a: number; b: number } | { op: 'cycle'; reset: boolean; valid: boolean; a: number; b: number })[]; parameters: { width: number; latency: 2 } }
export interface AxiLiteParameters { data_width: 32; address_width: 8; register_count: 4; max_outstanding_writes: 1; max_outstanding_reads: 1; request_ready_bound: number; response_bound: number; write_pair_bound: number; max_response_stall: number }
export interface AxiLiteCycle { reset_n: boolean; aw_valid: boolean; aw_addr: number; aw_prot: number; w_valid: boolean; w_data: number; w_strb: number; b_ready: boolean; ar_valid: boolean; ar_addr: number; ar_prot: number; r_ready: boolean }
export interface AxiLiteExperiment extends ExperimentBase { schema_version: 'axi_lite.v1'; target: 'axi_lite'; adapter: 'rtl.axi_lite.v1'; parameters: AxiLiteParameters; cycles: AxiLiteCycle[] }
export type Experiment = FifoExperiment | ArithmeticExperiment | AxiLiteExperiment
export type SavedOperation = { op: string; cycles?: number; value?: InspectionInteger; a?: InspectionInteger; b?: InspectionInteger; reset?: boolean; valid?: boolean }
export type SavedExperiment = ExperimentBase & { schema_version: string; target: string; adapter?: string; operations?: SavedOperation[]; cycles?: AxiLiteCycle[]; parameters: FamilyParameters }

export class ApiError extends Error {
  constructor(public status: number, message: string) { super(message) }
}
async function responseFor(path: string, init?: RequestInit): Promise<Response> {
  let response: Response
  try { response = await fetch(`/api${path}`, { ...init, signal: init?.signal ?? AbortSignal.timeout(10000), headers: { 'Content-Type': 'application/json', ...init?.headers } }) }
  catch { throw new ApiError(0, 'The local API is unreachable. Start the local runtime and verify the Vite API proxy, then retry.') }
  if (!response.ok) {
    const body = await response.text().then(parseInspectionJson).catch(() => null) as { detail?: unknown } | null
    const detail = typeof body?.detail === 'string' ? body.detail : body?.detail ? JSON.stringify(body.detail) : response.statusText
    throw new ApiError(response.status, `API ${response.status}: ${detail || 'Request failed'}`)
  }
  return response
}
export async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const response = await responseFor(path, init)
  try { return parseInspectionJson(await response.text()) as T }
  catch { throw new ApiError(response.status, 'The local API returned invalid or oversized JSON; no result was interpreted.') }
}
export async function downloadEvidence(path: string, filename: string): Promise<void> {
  const response = await responseFor(path)
  const url = URL.createObjectURL(await response.blob())
  const link = document.createElement('a')
  link.href = url; link.download = filename
  document.body.append(link); link.click(); link.remove()
  setTimeout(() => URL.revokeObjectURL(url), 1000)
}
export const mutate = <T>(path: string, body?: unknown, key: string = crypto.randomUUID()) => request<T>(path, { method: 'POST', headers: { 'Idempotency-Key': key }, body: body === undefined ? undefined : stringifySubmission(body) })
export const isActive = (status?: string) => !!status && ['queued', 'preparing', 'running', 'finalizing'].includes(status)
export const humanize = (value: string) => value.replaceAll('_', ' ')
export const hasCompleteIdentity = (identity: Record<string, string> | null) => !!identity && ['source_hash', 'checker_hash', 'contract_hash', 'experiment_hash', 'environment_hash'].every(key => /^[a-f0-9]{64}$/.test(identity[key] ?? ''))
export { createPlan, MAX_SEED, DEFAULT_WALL_TIME, validSeed, validWallTime } from './families'
import { parseInspectionJson, stringifySubmission } from './json'

export type InspectionInteger = number | string
