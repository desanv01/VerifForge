export interface Project { id: string; name: string; family: string; support_level: string; contract_id: string }
export interface Contract { contract_id: string; version: string; review_status: string; checker: { checker_id: string; version: string; sha256: string }; parameters: { width: number; depth: number }; requirements: { requirement_id: string; description: string }[]; assumptions: string[]; reset_priority: string; acceptance: string; read_semantics: string }
export interface Signature { requirement_id: string; failure_class: string; first_cycle: number; expected: string; observed: string; signature_hash: string }
export interface Artifact { artifact_id: string; path: string; sha256: string; size_bytes: number; media_type: string }
export interface DownloadableArtifact extends Artifact { downloadable: boolean }
export interface Health { status: string; evidence_api?: string }
export interface ExportJob { id: string; attempt_id: string; status: 'queued' | 'running' | 'completed' | 'failed' | 'cancelled'; error_type: string | null; sha256: string | null; size_bytes: number | null }
export interface Trace { cycle: number; operation_index: number; read_accepted: boolean; write_accepted: boolean; expected_data: InspectionInteger | null; observed_data: InspectionInteger | null; expected_empty: boolean | null; observed_empty: boolean | null; expected_full: boolean | null; observed_full: boolean | null }
export interface Result { identity: Record<string, string> | null; tool_versions: { name: string; version: string }[]; trace: Trace[]; artifacts: Artifact[]; uncertainties: string[]; failure_signature: Signature | null }
export interface Finding { id: string; attempt_id: string; status: string; verdict: string; result: Result; contract_id: string; plan_hash: string }
export interface Campaign { id: string; project_id: string; contract_id: string; execution_status: string; verdict: string | null; version: number; created_at: string; updated_at: string; plan: SavedExperiment; attempts: { id: string; execution_status: string; verdict: string | null; result: Result | null }[]; findings: Finding[] }
export interface RunEvent { sequence: number; kind: string; payload: unknown; created_at: string }
export interface Experiment { schema_version: '1'; experiment_id: string; fixture_id: string; contract_id: string; target: 'fifo'; objective_ids: string[]; seed: number; operations: ({ op: 'reset'; cycles: number } | { op: 'write'; value: number } | { op: 'read' })[]; parameters: { width: number; depth: number }; budget: { max_cycles: number; wall_time_seconds: number; max_artifact_bytes: number } }
export type SavedExperiment = Omit<Experiment, 'operations'> & { operations: (Exclude<Experiment['operations'][number], { op: 'write' }> | { op: 'write'; value: InspectionInteger })[] }

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
export const MAX_SEED = 2 ** 32 - 1
export const DEFAULT_WALL_TIME = 120
export const validSeed = (seed: number) => Number.isSafeInteger(seed) && seed >= 0 && seed <= MAX_SEED
export const validWallTime = (seconds: number) => Number.isSafeInteger(seconds) && seconds >= 1 && seconds <= 3600
export function createPlan(project: Project, contract: Contract, seed: number, wallTimeSeconds = DEFAULT_WALL_TIME): Experiment {
  if (!validSeed(seed) || !validWallTime(wallTimeSeconds)) throw new Error('Seed or wall-time budget is outside the supported range.')
  return { schema_version: '1', experiment_id: `web-${crypto.randomUUID()}`, fixture_id: project.id, contract_id: contract.contract_id, target: 'fifo', objective_ids: contract.requirements.map(r => r.requirement_id), seed,
    operations: [{ op: 'reset', cycles: 2 }, { op: 'write', value: 17 }, { op: 'reset', cycles: 1 }, { op: 'write', value: 42 }, { op: 'read' }], parameters: contract.parameters,
    budget: { max_cycles: 100, wall_time_seconds: wallTimeSeconds, max_artifact_bytes: 10485760 } }
}
import { parseInspectionJson, stringifySubmission } from './json'

export type InspectionInteger = number | string
