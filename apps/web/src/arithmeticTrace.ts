import type { InspectionInteger, SavedExperiment } from './api'

export interface ArithmeticTraceRow { cycle: number; operation_index: number; reset: boolean; expected_valid: boolean; observed_valid: boolean; expected_product: InspectionInteger | null; observed_product: InspectionInteger }
const record = (v: unknown): v is Record<string, unknown> => !!v && typeof v === 'object' && !Array.isArray(v)
function product(value: unknown, width: number): value is InspectionInteger {
  if (typeof value === 'number' && (!Number.isSafeInteger(value) || value < 0)) return false
  if (typeof value !== 'number' && !(typeof value === 'string' && /^(0|[1-9]\d{0,19})$/.test(value))) return false
  return BigInt(value) < (1n << BigInt(2 * width))
}
export function readArithmeticTrace(raw: unknown, plan: SavedExperiment): ArithmeticTraceRow[] {
  const fail = () => { throw new Error('Arithmetic trace is missing, malformed, or does not match the saved experiment. No trace was interpreted.') }
  if (!record(raw) || raw.schema_version !== 'arithmetic.trace.v1' || !Array.isArray(raw.expected) || !Array.isArray(raw.observed)) return fail()
  const width = plan.parameters.width
  if (typeof width !== 'number' || !Number.isInteger(width) || width < 1 || width > 32 || plan.parameters.latency !== 2 || plan.target !== 'arithmetic') return fail()
  const indices: number[] = []
  if (!Array.isArray(plan.operations) || !plan.operations.length || plan.operations.length > 10000) return fail()
  for (const [index, op] of plan.operations.entries()) {
    const cycles = op.cycles ?? 1
    if (!Number.isSafeInteger(cycles) || cycles < 1 || cycles > 10000 || indices.length + cycles > 1_000_000 || indices.length + cycles > plan.budget.max_cycles) return fail()
    for (let n = 0; n < cycles; n++) indices.push(index)
  }
  if (raw.expected.length !== indices.length || raw.observed.length !== indices.length) return fail()
  return raw.expected.map((want: unknown, cycle: number) => {
    const got: unknown = (raw.observed as unknown[])[cycle]
    if (!record(want) || !record(got) || want.cycle !== cycle || got.cycle !== cycle || want.operation_index !== indices[cycle] || typeof want.valid !== 'boolean' || typeof want.reset !== 'boolean' || typeof got.valid !== 'boolean' || !product(got.product, width) || (want.product !== null && !product(want.product, width)) || ((want.valid || want.reset) && want.product === null)) return fail()
    return {cycle,operation_index:indices[cycle],reset:want.reset,expected_valid:want.valid,observed_valid:got.valid,expected_product:want.product as InspectionInteger | null,observed_product:got.product}
  })
}
