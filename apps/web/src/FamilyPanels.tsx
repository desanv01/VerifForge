import type { Contract } from './api'
import { humanize } from './api'
import { ARITHMETIC, FIFO, validOperand } from './families'

export function ContractSemantics({ contract, adapter }: { contract: Contract; adapter: string | null }) {
  const entries = adapter === FIFO ? [['Reset priority', contract.reset_priority], ['Acceptance', contract.acceptance], ['Read behavior', contract.read_semantics]] : adapter === ARITHMETIC ? [['Timing', contract.timing], ['Reset', contract.reset], ['Bubbles & validity', contract.bubbles]] : []
  return <details><summary>Semantics, assumptions and checker identity</summary><dl><dt>Family parameters</dt><dd><code>{JSON.stringify(contract.parameters)}</code></dd>{entries.map(([label, value]) => <div key={label}><dt>{label}</dt><dd>{value ? humanize(value) : 'Not specified'}</dd></div>)}<dt>Checker</dt><dd>{contract.checker.checker_id} v{contract.checker.version}<code>{contract.checker.sha256}</code></dd></dl>{contract.assumptions.map(a => <p key={a}>{a}</p>)}</details>
}

export function ExperimentControls({ adapter, contract, a, b, setA, setB }: { adapter: string | null; contract?: Contract; a: number; b: number; setA: (n: number) => void; setB: (n: number) => void }) {
  const width = contract?.parameters.width ?? 8
  const operands = [{label:'a',value:a,set:setA},{label:'b',value:b,set:setB}]
  if (adapter === ARITHMETIC) return <><h3>Fixed-latency product experiment</h3><p className="muted">Unsigned {width}-bit operands; a {2 * width}-bit product after two edges. The final two idle cycles drain the pipeline. No backpressure is assumed.</p><div className="comparison">{operands.map(({label,value,set}) => <div key={label}><label htmlFor={`operand-${label}`}>Operand {label.toUpperCase()}</label><input id={`operand-${label}`} type="number" min="0" max={2 ** width - 1} step="1" aria-invalid={!validOperand(value,width)} aria-describedby="operand-help" value={Number.isNaN(value) ? '' : value} onChange={e => set(e.target.valueAsNumber)}/></div>)}</div><p id="operand-help" className="meta">Whole unsigned numbers from 0 to {2 ** width - 1}.</p><ol className="stimulus"><li>Reset for 2 cycles</li><li>Multiply A × B on one valid edge</li><li>Idle for 2 cycles to observe the result</li></ol></>
  if (adapter === FIFO) return <><h3>Reset boundary experiment</h3><ol className="stimulus"><li>Reset for 2 cycles</li><li>Write {Math.min(17, 2 ** width - 1)}</li><li>Reset for 1 cycle</li><li>Write {Math.min(42, 2 ** width - 1)}</li><li>Read oldest accepted value</li></ol></>
  return <p className="notice">No experiment controls are available for this family.</p>
}
