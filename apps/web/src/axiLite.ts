import type { AxiLiteCycle, AxiLiteExperiment, AxiLiteParameters, Campaign, Result } from './api'
import { AXI_OBJECTIVES, AXI_PARAMETERS, axiScenario } from './axiLitePlans'

export const AXI_LITE = 'rtl.axi_lite.v1'
export const AXI_TRACE_SCHEMA = 'axi_lite.trace.v1'
export const AXI_TRACE_ARTIFACT = 'observed.json'
const object = (v: unknown): v is Record<string, unknown> => !!v && typeof v === 'object' && !Array.isArray(v)
const integer = (v: unknown, max: number, min = 0): v is number => typeof v === 'number' && Number.isSafeInteger(v) && v >= min && v <= max
const id = (v: unknown) => typeof v === 'string' && /^[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}$/.test(v)
const keys = (o: Record<string, unknown>, fields: readonly string[]) => Object.keys(o).length === fields.length && fields.every(k => Object.hasOwn(o, k))
export function validAxiParameters(v: unknown, frozen = false): v is AxiLiteParameters {
  if (!object(v) || !keys(v, Object.keys(AXI_PARAMETERS))) return false
  if (frozen) return Object.entries(AXI_PARAMETERS).every(([k, n]) => v[k] === n)
  return v.data_width === 32 && v.address_width === 8 && v.register_count === 4 && v.max_outstanding_writes === 1 && v.max_outstanding_reads === 1 && integer(v.request_ready_bound, 16, 1) && integer(v.response_bound, 16, 1) && integer(v.write_pair_bound, 32, 1) && integer(v.max_response_stall, 32)
}
const cycleFields = ['reset_n', 'aw_valid', 'aw_addr', 'aw_prot', 'w_valid', 'w_data', 'w_strb', 'b_ready', 'ar_valid', 'ar_addr', 'ar_prot', 'r_ready']
function cycle(v: unknown): v is AxiLiteCycle {
  if (!object(v) || !keys(v, cycleFields)) return false
  return ['reset_n', 'aw_valid', 'w_valid', 'b_ready', 'ar_valid', 'r_ready'].every(k => typeof v[k] === 'boolean') && integer(v.aw_addr, 255) && integer(v.ar_addr, 255) && integer(v.aw_prot, 7) && integer(v.ar_prot, 7) && integer(v.w_data, 0xffffffff) && integer(v.w_strb, 15) && (v.reset_n || (!v.aw_valid && !v.w_valid && !v.ar_valid)) === true
}
export function validAxiPlan(v: unknown): v is AxiLiteExperiment {
  if (!object(v) || !keys(v, ['schema_version', 'adapter', 'contract_id', 'target', 'experiment_id', 'fixture_id', 'objective_ids', 'seed', 'parameters', 'cycles', 'budget'])) return false
  if (v.schema_version !== 'axi_lite.v1' || v.adapter !== AXI_LITE || v.target !== 'axi_lite' || v.contract_id !== 'axil-registers-v1' || !id(v.experiment_id) || typeof v.fixture_id !== 'string' || !axiScenario(v.fixture_id) || !integer(v.seed, 0xffffffff) || !validAxiParameters(v.parameters)) return false
  if (!Array.isArray(v.objective_ids) || !v.objective_ids.length || v.objective_ids.length > 6 || new Set(v.objective_ids).size !== v.objective_ids.length || !v.objective_ids.every(id => AXI_OBJECTIVES.includes(id))) return false
  if (!object(v.budget) || !keys(v.budget, ['max_cycles', 'wall_time_seconds', 'max_artifact_bytes']) || !integer(v.budget.max_cycles, 1_000_000, 1) || typeof v.budget.wall_time_seconds !== 'number' || !Number.isFinite(v.budget.wall_time_seconds) || v.budget.wall_time_seconds <= 0 || v.budget.wall_time_seconds > 3600 || !integer(v.budget.max_artifact_bytes, 1073741824, 1024)) return false
  if (!Array.isArray(v.cycles) || !v.cycles.length || v.cycles.length > 4096 || v.cycles.length > v.budget.max_cycles || !v.cycles.every(cycle) || v.cycles[0].reset_n) return false
  return v.cycles.every((row, i, rows) => i === 0 || rows[i - 1].reset_n || !row.reset_n || (!row.aw_valid && !row.w_valid && !row.ar_valid))
}
export function axiContextError(campaign: Campaign, result: Result): string | null {
  const plan = campaign.plan
  const hashes = ['source_hash', 'checker_hash', 'contract_hash', 'experiment_hash']
  if (campaign.adapter_id !== AXI_LITE || campaign.trace_schema !== AXI_TRACE_SCHEMA || campaign.trace_artifact !== AXI_TRACE_ARTIFACT || !validAxiPlan(plan) || campaign.project_id !== plan.fixture_id || campaign.contract_id !== plan.contract_id || result.experiment_id !== plan.experiment_id || !id(result.attempt_id) || result.execution_status !== 'completed' || !Array.isArray(result.trace) || result.trace.length !== 0 || !hashes.every(k => /^[a-f0-9]{64}$/.test(campaign.identity?.[k] ?? '') && campaign.identity?.[k] === result.identity?.[k]) || !/^[a-f0-9]{64}$/.test(result.identity?.environment_hash ?? '')) return 'AXI saved experiment or evidence identity is incomplete or inconsistent. No native trace was interpreted.'
  return null
}
export interface AxiObservation { cycle: number; aw_ready: boolean; w_ready: boolean; b_valid: boolean; b_resp: number; ar_ready: boolean; r_valid: boolean; r_data: number; r_resp: number }
export interface AxiTraceRow { input: AxiLiteCycle; observed: AxiObservation }
export function readAxiTrace(raw: unknown, plan: unknown): AxiTraceRow[] {
  const fail = (): never => { throw new Error('AXI trace is missing, malformed, or differs from the saved exact cycles. No native trace was interpreted.') }
  if (!validAxiPlan(plan) || !object(raw) || !keys(raw, ['schema_version', 'observations']) || raw.schema_version !== AXI_TRACE_SCHEMA || !Array.isArray(raw.observations) || raw.observations.length !== plan.cycles.length) return fail()
  return raw.observations.map((row: unknown, index) => {
    if (!object(row) || !keys(row, ['cycle', 'aw_ready', 'w_ready', 'b_valid', 'b_resp', 'ar_ready', 'r_valid', 'r_data', 'r_resp']) || row.cycle !== index || !['aw_ready', 'w_ready', 'b_valid', 'ar_ready', 'r_valid'].every(k => typeof row[k] === 'boolean') || !integer(row.b_resp, 3) || !integer(row.r_resp, 3) || !integer(row.r_data, 0xffffffff)) return fail()
    return { input: plan.cycles[index], observed: row as unknown as AxiObservation }
  })
}
export const hex32 = (value: number) => `0x${value.toString(16).padStart(8, '0')}`
