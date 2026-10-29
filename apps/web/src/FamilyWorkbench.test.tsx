import { cleanup, render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { App } from './App'
import type { Campaign, Contract, Project, SavedExperiment } from './api'
import { createPlan } from './api'
import { ARITHMETIC, campaignAdapter, evidenceAvailable, FIFO, projectAdapter, setupError } from './families'
import { readArithmeticTrace } from './arithmeticTrace'

const fifo:Project = {id:'fifo_correct',name:'FIFO correct',family:'fifo',support_level:'experimental',contract_id:'fifo-v1'}
const arithmetic:Project = {id:'multiplier_correct',name:'Multiplier correct',family:'arithmetic',support_level:'experimental',contract_id:'unsigned-multiplier-v1',adapter_id:ARITHMETIC,experiment_schema_version:'arithmetic.v1',trace_schema:'arithmetic.trace.v1',trace_artifact:'arithmetic-trace.json'}
const common = {version:'1',review_status:'reviewed',checker:{checker_id:'checker',version:'1',sha256:'a'.repeat(64)},assumptions:['Inputs stable before each edge.'],requirements:[{requirement_id:'FIFO_BEHAVIOR',description:'FIFO behavior'}]}
const fifoContract:Contract = {...common,contract_id:'fifo-v1',parameters:{width:8,depth:4},reset_priority:'reset_over_read_write',acceptance:'pre_edge_flags',read_semantics:'oldest_on_accepted_edge'}
const arithmeticContract:Contract = {...common,contract_id:'unsigned-multiplier-v1',schema_version:'arithmetic.contract.v1',target:'arithmetic',parameters:{width:8,latency:2},requirements:[{requirement_id:'ARITH_PRODUCT',description:'Full-width unsigned product.'},{requirement_id:'ARITH_LATENCY',description:'Valid after two edges.'}],timing:'Accepted edge n produces output at n+2.',reset:'Reset cancels in-flight products.',bubbles:'Invalid output product is unspecified.'}
const plan:SavedExperiment = {...createPlan(arithmetic,arithmeticContract,0),operations:[{op:'reset',cycles:1},{op:'multiply',a:7,b:9},{op:'idle',cycles:2}]}
const identity = Object.fromEntries(['source_hash','checker_hash','contract_hash','experiment_hash','environment_hash'].map(k=>[k,'a'.repeat(64)]))
const result = {identity,tool_versions:[],trace:[],artifacts:[],uncertainties:[],failure_signature:null}
const saved:Campaign = {id:'saved-arithmetic',project_id:arithmetic.id,contract_id:arithmetic.contract_id,adapter_id:ARITHMETIC,trace_schema:'arithmetic.trace.v1',trace_artifact:'arithmetic-trace.json',plan,execution_status:'completed',verdict:'pass_within_scope',created_at:'2026-10-29T00:00:00Z',updated_at:'2026-10-29T00:00:00Z',version:4,findings:[],attempts:[{id:'database-attempt',execution_status:'completed',verdict:'pass_within_scope',result}]}
const sidecar = {schema_version:'arithmetic.trace.v1',expected:[{cycle:0,operation_index:0,valid:false,product:0,reset:true},{cycle:1,operation_index:1,valid:false,product:null,reset:false},{cycle:2,operation_index:2,valid:false,product:null,reset:false},{cycle:3,operation_index:2,valid:true,product:63,reset:false}],observed:[{cycle:0,valid:false,product:0},{cycle:1,valid:false,product:0},{cycle:2,valid:false,product:0},{cycle:3,valid:true,product:63}]}
const artifact = {artifact_id:'sidecar-id',path:'arithmetic-trace.json',media_type:'application/json',size_bytes:1024,sha256:'b'.repeat(64),downloadable:true}
function mount() {render(<QueryClientProvider client={new QueryClient({defaultOptions:{queries:{retry:false},mutations:{retry:false}}})}><App/></QueryClientProvider>)}
function mockApi(override?: (url:string,init?:RequestInit)=>unknown) {
  return vi.spyOn(globalThis,'fetch').mockImplementation(async(input,init)=>{
    const url=String(input), custom=override?.(url,init)
    if(custom instanceof Response)return custom
    const value=custom??(url==='/api/projects'?{items:[fifo,arithmetic]}:url==='/api/contracts/fifo-v1'?fifoContract:url==='/api/contracts/unsigned-multiplier-v1'?arithmeticContract:url==='/api/health'?{status:'ok',evidence_api:'1',evidence_adapters:[FIFO,ARITHMETIC]}:url.endsWith('/artifacts')?{items:[artifact]}:url.endsWith('/download')?sidecar:url.includes('/events?')?{items:[]}:url==='/api/campaigns'&&init?.method!=='POST'?{items:[saved]}:saved)
    return new Response(JSON.stringify(value),{status:200})
  })
}
afterEach(()=>{cleanup();vi.restoreAllMocks();history.replaceState(null,'','/')})

describe('family workbench',()=>{
  it('switches from FIFO to arithmetic semantics, resets acknowledgement, and submits an explicit drained arithmetic plan',async()=>{
    const calls=mockApi();mount();const user=userEvent.setup()
    await screen.findByText('FIFO behavior');await user.click(screen.getByRole('checkbox'))
    await user.selectOptions(screen.getByLabelText('Curated project'),arithmetic.id)
    await screen.findByText('Full-width unsigned product.')
    expect(screen.getByRole('checkbox')).not.toBeChecked()
    await user.click(screen.getByText('Semantics, assumptions and checker identity'))
    expect(screen.getByText(arithmeticContract.timing!)).toBeVisible()
    expect(screen.queryByText('Reset priority',{exact:true})).not.toBeInTheDocument()
    const a=screen.getByLabelText('Operand A');await user.clear(a);await user.type(a,'256')
    await user.click(screen.getByRole('checkbox'))
    expect(screen.getByRole('button',{name:/Create campaign draft/})).toBeDisabled()
    await user.clear(a);await user.type(a,'255')
    await user.click(screen.getByRole('button',{name:/Create campaign draft/}))
    await screen.findByText('saved-arithmetic',{exact:true})
    const call=calls.mock.calls.find(([url,init])=>url==='/api/campaigns'&&init?.method==='POST')!
    const payload=JSON.parse(call[1]!.body as string)
    expect(payload.adapter_id).toBe(ARITHMETIC)
    expect(payload.plan).toMatchObject({schema_version:'arithmetic.v1',target:'arithmetic',adapter:ARITHMETIC,parameters:{width:8,latency:2},seed:123})
    expect(payload.plan.operations).toEqual([{op:'reset',cycles:2},{op:'multiply',a:255,b:9},{op:'idle',cycles:2}])
    expect(payload.plan.parameters).not.toHaveProperty('depth')
  })
  it('keeps unknown catalog adapters explicit and prevents accidental FIFO creation',async()=>{
    const calls=mockApi(url=>url==='/api/projects'?{items:[{...fifo,family:'axi_lite',adapter_id:'rtl.axi.v1'}]}:undefined)
    mount();const user=userEvent.setup();await screen.findByText('FIFO behavior');await user.click(screen.getByRole('checkbox'))
    expect(screen.getByText(/This catalog family or experiment schema is not supported/)).toBeInTheDocument()
    expect(screen.getByRole('button',{name:/Create campaign draft/})).toBeDisabled()
    expect(calls.mock.calls.some(([,init])=>init?.method==='POST')).toBe(false)
  })
  it('displays arithmetic sidecar valid/product rows and never synthesizes FIFO flags',async()=>{
    history.replaceState(null,'','/?campaign=saved-arithmetic');const calls=mockApi();mount()
    const table=await screen.findByRole('table',{name:/Arithmetic observations/})
    expect(table).toHaveTextContent('Expected valid')
    expect(table).toHaveTextContent('63')
    expect(table).toHaveTextContent('Unspecified (bubble)')
    expect(screen.queryByText('Write / read accepted')).not.toBeInTheDocument()
    expect(calls.mock.calls.some(([url])=>url==='/api/attempts/database-attempt/artifacts/sidecar-id/download')).toBe(true)
  })
  it('does not infer arithmetic evidence support from an older FIFO-only marker',async()=>{
    history.replaceState(null,'','/?campaign=saved-arithmetic');const calls=mockApi(url=>url==='/api/health'?{status:'ok',evidence_api:'1'}:undefined);mount()
    await screen.findByText(/Arithmetic trace inspection requires/)
    expect(screen.getByRole('button',{name:'Replay exact stimulus'})).toBeDisabled()
    expect(calls.mock.calls.some(([url])=>String(url).includes('/artifacts'))).toBe(false)
  })
  it('explains a missing sidecar without calling the empty common trace a pass',async()=>{
    history.replaceState(null,'','/?campaign=saved-arithmetic');mockApi(url=>url.endsWith('/artifacts')?{items:[]}:undefined);mount()
    await screen.findByText(/empty common trace is not a passing arithmetic result/)
    expect(screen.queryByRole('table')).not.toBeInTheDocument()
  })
  it('reports malformed arithmetic evidence without a fallback FIFO table',async()=>{
    history.replaceState(null,'','/?campaign=saved-arithmetic');mockApi(url=>url.endsWith('/download')?{...sidecar,schema_version:'unexpected.trace.v1'}:undefined);mount()
    await screen.findByText(/No trace was interpreted/)
    expect(screen.queryByRole('table')).not.toBeInTheDocument()
    expect(screen.getByRole('button',{name:'Retry arithmetic trace'})).toBeEnabled()
  })
  it('preserves distinct 64-bit expected and observed product digits in the native sidecar',async()=>{
    history.replaceState(null,'','/?campaign=saved-arithmetic')
    const wide={...sidecar,expected:sidecar.expected.map(r=>r.cycle===3?{...r,product:'expected-wide'}:r),observed:sidecar.observed.map(r=>r.cycle===3?{...r,product:'observed-wide'}:r)}
    mockApi(url=>url==='/api/campaigns/saved-arithmetic'?{...saved,plan:{...plan,parameters:{width:32,latency:2}}}:url.endsWith('/download')?new Response(JSON.stringify(wide).replace('"expected-wide"','18446744065119617025').replace('"observed-wide"','18446744065119617026')):undefined);mount()
    await screen.findByText('18446744065119617025',{exact:true})
    expect(screen.getByText('18446744065119617026',{exact:true})).toBeInTheDocument()
  })
  it('does not substitute the seed experiment for a sequential attempt without its authoritative plan API',async()=>{
    history.replaceState(null,'','/?campaign=saved-arithmetic');const calls=mockApi(url=>url==='/api/campaigns/saved-arithmetic'?{...saved,completion_policy:'sequential'}:undefined);mount()
    await screen.findByText(/contains multiple experiment records/)
    expect(screen.queryByRole('table')).not.toBeInTheDocument()
    expect(screen.queryByRole('button',{name:'Replay exact stimulus'})).not.toBeInTheDocument()
    expect(screen.getByRole('button',{name:'Start campaign'})).toBeDisabled()
    expect(calls.mock.calls.some(([url])=>String(url).includes('/artifacts'))).toBe(false)
  })
})

describe('family wire boundaries',()=>{
  it('rejects cross-family identity, contract semantics, and missing arithmetic discovery markers',()=>{
    expect(projectAdapter({...arithmetic,experiment_schema_version:undefined})).toBeNull()
    expect(projectAdapter({...arithmetic,trace_schema:'unknown.trace'})).toBeNull()
    expect(setupError(arithmetic,fifoContract)).not.toBeNull()
    expect(setupError(arithmetic,{...arithmeticContract,timing:undefined})).not.toBeNull()
    expect(()=>createPlan({...arithmetic,adapter_id:FIFO},arithmeticContract,0)).toThrow()
    expect(campaignAdapter({...saved,adapter_id:FIFO})).toBeNull()
    expect(evidenceAvailable({status:'ok',evidence_api:'1',evidence_adapters:[FIFO]},ARITHMETIC)).toBe(false)
  })
  it('supports narrow FIFO widths and bounded 32-bit arithmetic operands without changing their schemas',()=>{
    const small=createPlan(fifo,{...fifoContract,parameters:{width:1,depth:4}},0)
    expect(small.operations[1]).toEqual({op:'write',value:1})
    const wide=createPlan(arithmetic,{...arithmeticContract,parameters:{width:32,latency:2}},0,120,{a:4294967295,b:4294967295})
    expect(wide.operations[1]).toEqual({op:'multiply',a:4294967295,b:4294967295})
    expect(()=>createPlan(arithmetic,arithmeticContract,0,120,{a:256,b:9})).toThrow()
  })
  it('rejects malformed arithmetic trace schema, cycle order, boolean fields and row lengths',()=>{
    expect(readArithmeticTrace(sidecar,plan)).toHaveLength(4)
    for(const raw of [{...sidecar,schema_version:'fifo.v1'},{...sidecar,observed:[]},{...sidecar,observed:sidecar.observed.map(r=>({...r,cycle:1}))},{...sidecar,observed:sidecar.observed.map(r=>({...r,valid:'true'}))}])expect(()=>readArithmeticTrace(raw,plan)).toThrow(/No trace was interpreted/)
  })
})
