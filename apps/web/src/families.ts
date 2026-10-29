import type { Campaign, Contract, Experiment, Health, Project } from './api'
import { AXI_LITE, AXI_TRACE_ARTIFACT, AXI_TRACE_SCHEMA, validAxiParameters, validAxiPlan } from './axiLite'
import { AXI_OBJECTIVES, AXI_PARAMETERS, axiScenario } from './axiLitePlans'
export { AXI_LITE } from './axiLite'
export const MAX_SEED = 2 ** 32 - 1
export const DEFAULT_WALL_TIME = 120
export const validSeed = (seed: number) => Number.isSafeInteger(seed) && seed >= 0 && seed <= MAX_SEED
export const validWallTime = (seconds: number) => Number.isSafeInteger(seconds) && seconds >= 1 && seconds <= 3600

export const FIFO = 'rtl.fifo.v1'
export const ARITHMETIC = 'rtl.arithmetic.v1'
export const ARITHMETIC_TRACE_SCHEMA = 'arithmetic.trace.v1'
export const ARITHMETIC_TRACE_ARTIFACT = 'arithmetic-trace.json'
export const familyName = (adapter?: string | null) => adapter === FIFO ? 'FIFO' : adapter === ARITHMETIC ? 'Arithmetic pipeline' : adapter === AXI_LITE ? 'Bounded AXI-Lite' : 'Unsupported family'
export function projectKind(project?: Project): 'rtl_fixture' | 'engineering_recorded' | null {
  const identifier = (v: unknown) => typeof v === 'string' && /^[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}$/.test(v)
  if (!project || !identifier(project.id) || typeof project.name !== 'string' || !project.name.trim() || project.name.length > 256) return null
  if (project.kind === 'engineering_recorded') {
    const fields = ['id', 'name', 'kind', 'contract_id', 'description', 'domains']
    const domains = ['rtl', 'riscv', 'firmware', 'vivado', 'kicad', 'system']
    return project.contract_id === null && Object.keys(project).every(key => fields.includes(key)) && typeof project.description === 'string' && project.description.length <= 1000 && Array.isArray(project.domains) && project.domains.length >= 1 && project.domains.length <= 6 && new Set(project.domains).size === project.domains.length && project.domains.every(domain => domains.includes(domain)) ? 'engineering_recorded' : null
  }
  return (project.kind === undefined || project.kind === 'rtl_fixture') && identifier(project.contract_id) && typeof project.family === 'string' && !!project.family.trim() && typeof project.support_level === 'string' && !!project.support_level.trim() ? 'rtl_fixture' : null
}
export function projectAdapter(project?: Project): string | null {
  if (!project || projectKind(project) !== 'rtl_fixture') return null
  if (project.family === 'fifo' && (!project.adapter_id || project.adapter_id === FIFO) && (!project.experiment_schema_version || project.experiment_schema_version === '1')) return FIFO
  if (project.family === 'arithmetic' && project.adapter_id === ARITHMETIC && project.experiment_schema_version === 'arithmetic.v1' && project.trace_schema === ARITHMETIC_TRACE_SCHEMA && project.trace_artifact === ARITHMETIC_TRACE_ARTIFACT) return ARITHMETIC
  if (project.family === 'axi_lite' && project.adapter_id === AXI_LITE && project.experiment_schema_version === 'axi_lite.v1' && project.trace_schema === AXI_TRACE_SCHEMA && project.trace_artifact === AXI_TRACE_ARTIFACT && project.contract_id === 'axil-registers-v1' && axiScenario(project.id)) return AXI_LITE
  return null
}
export function campaignAdapter(campaign?: Campaign): string | null {
  if (!campaign) return null
  if ((!campaign.adapter_id || campaign.adapter_id === FIFO) && campaign.plan.target === 'fifo' && campaign.plan.schema_version === '1') return FIFO
  if (campaign.adapter_id === ARITHMETIC && campaign.plan.target === 'arithmetic' && campaign.plan.schema_version === 'arithmetic.v1' && campaign.plan.adapter === ARITHMETIC && campaign.trace_schema === ARITHMETIC_TRACE_SCHEMA && campaign.trace_artifact === ARITHMETIC_TRACE_ARTIFACT) return ARITHMETIC
  if (campaign.adapter_id === AXI_LITE && campaign.trace_schema === AXI_TRACE_SCHEMA && campaign.trace_artifact === AXI_TRACE_ARTIFACT && validAxiPlan(campaign.plan) && campaign.project_id === campaign.plan.fixture_id && campaign.contract_id === campaign.plan.contract_id) return AXI_LITE
  return null
}
export function evidenceAvailable(health: Health | undefined, adapter: string | null): boolean {
  if (health?.status !== 'ok' || health.evidence_api !== '1' || !adapter) return false
  // Older qualified FIFO snapshots have no family array. They never imply arithmetic.
  return health.evidence_adapters === undefined ? adapter === FIFO : Array.isArray(health.evidence_adapters) && health.evidence_adapters.every(value => typeof value === 'string') && health.evidence_adapters.includes(adapter)
}
export function setupError(project: Project | undefined, contract: Contract | undefined): string | null {
  if (!project || !contract) return 'Load the project and reviewed contract before preparing an experiment.'
  const adapter = projectAdapter(project)
  if (!adapter) return 'This catalog family or experiment schema is not supported by this workbench. Creation is unavailable.'
  if (project.contract_id !== contract.contract_id || (contract.target && contract.target !== project.family)) return 'Project and contract family identities do not agree.'
  if (contract.review_status !== 'reviewed') return 'The active contract has not been reviewed.'
  const p = contract.parameters
  if (adapter === AXI_LITE) return contract.schema_version === 'axi_lite.contract.v1' && contract.target === 'axi_lite' && validAxiParameters(p, true) && contract.sampling === 'settled_pre_rising_edge' && contract.read_write_collision === 'read_before_write' && contract.invalid_access === 'slverr_no_write_read_zero' && contract.reset === 'clear_registers_cancel_all_pending' && contract.requirements.length === AXI_OBJECTIVES.length && AXI_OBJECTIVES.every(id => contract.requirements.some(r => r.requirement_id === id)) && contract.assumptions.length > 0 && /^[a-f0-9]{64}$/.test(contract.checker.sha256) ? null : 'The AXI contract differs from the reviewed fixed configuration, requirements or sampling semantics.'
  if (typeof p?.width !== 'number') return 'The reviewed contract has unsupported parameters.'
  if (!Number.isInteger(p?.width) || p.width < 1) return 'The reviewed contract has unsupported parameters.'
  if (adapter === FIFO && ((contract.schema_version && contract.schema_version !== '1') || p.width > 64 || !Number.isInteger(p.depth) || p.depth! < 2 || p.depth! > 1024 || !contract.reset_priority || !contract.acceptance || !contract.read_semantics)) return 'The FIFO contract is missing supported parameters or semantics.'
  if (adapter === ARITHMETIC && (contract.schema_version !== 'arithmetic.contract.v1' || contract.target !== 'arithmetic' || p.width > 32 || p.latency !== 2 || !contract.timing || !contract.reset || !contract.bubbles)) return 'The arithmetic contract is missing supported parameters or semantics.'
  return null
}
export const validOperand = (value: number, width: number) => Number.isSafeInteger(value) && value >= 0 && Number.isInteger(width) && width >= 1 && width <= 32 && value < 2 ** width
export function createPlan(project: Project, contract: Contract, seed: number, wallTimeSeconds = DEFAULT_WALL_TIME, operands?: { a: number; b: number }): Experiment {
  const error = setupError(project, contract)
  if (error) throw new Error(error)
  if (!validSeed(seed) || !validWallTime(wallTimeSeconds)) throw new Error('Seed or wall-time budget is outside the supported range.')
  const common = { experiment_id: `web-${crypto.randomUUID()}`, fixture_id: project.id, contract_id: contract.contract_id, objective_ids: contract.requirements.map(r => r.requirement_id), seed,
    budget: { max_cycles: 100, wall_time_seconds: wallTimeSeconds, max_artifact_bytes: 10485760 } }
  if (projectAdapter(project) === AXI_LITE) {
    const scenario = axiScenario(project.id)!
    return { ...common, experiment_id: scenario.experimentId, objective_ids: [...AXI_OBJECTIVES], schema_version: 'axi_lite.v1', target: 'axi_lite', adapter: AXI_LITE, parameters: { ...AXI_PARAMETERS }, cycles: scenario.cycles.map(row => ({ ...row })), seed: 0, budget: { max_cycles: 10000, wall_time_seconds: 120, max_artifact_bytes: 10485760 } }
  }
  const width = contract.parameters.width!
  if (projectAdapter(project) === ARITHMETIC) {
    const a = operands?.a ?? Math.min(7, 2 ** width - 1), b = operands?.b ?? Math.min(9, 2 ** width - 1)
    if (!validOperand(a, width) || !validOperand(b, width)) throw new Error('Operands must be unsigned integers within the reviewed width.')
    return { ...common, schema_version: 'arithmetic.v1', target: 'arithmetic', adapter: ARITHMETIC, parameters: { width, latency: 2 }, operations: [{ op: 'reset', cycles: 2 }, { op: 'multiply', a, b }, { op: 'idle', cycles: 2 }] }
  }
  const maximum = 2 ** width - 1
  return { ...common, schema_version: '1', target: 'fifo', parameters: { width, depth: contract.parameters.depth! }, operations: [{ op: 'reset', cycles: 2 }, { op: 'write', value: Math.min(17, maximum) }, { op: 'reset', cycles: 1 }, { op: 'write', value: Math.min(42, maximum) }, { op: 'read' }] }
}
