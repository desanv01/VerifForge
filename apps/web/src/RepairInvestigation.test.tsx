// Synthetic durable API responses only; no dispatcher, simulator or model runs.
import { act, cleanup, render, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { afterEach, expect, it, vi } from 'vitest'
import { RepairInvestigation } from './RepairInvestigation'
import { compatibleStatus, parseRepairCapabilities, parseRepairReport, parseRepairRequest, parseRepairStatus, readPending, repairPollInterval, repairStorageKey } from './repairApi'

const h='a'.repeat(64),other='b'.repeat(64)
const health={status:'ok',repair_api:'1',repair_admission_enabled:true,repair_families:['arithmetic','axi_lite']}
const provenance={schema_version:'candidate.provenance.v1',recommendation_origin:'curated_catalog',materialization:'curated_fixture_selection',authorship_claim:'curated_source_not_generated_patch',assertion_basis:'controller_supplied_record',proposal_id:null,proposal_sha256:null,external_reference_id:null,model_call:null}
const identity={source_hash:h,checker_hash:h,contract_hash:h,experiment_hash:h,environment_hash:h}
function fixture(kind:'candidate'|'reduction'='candidate',state='active',attempt='original-1',family='arithmetic'){
  return {job_id:'job-1',kind,execution_status:state,reason:state==='active'?null:'original_not_reproduced',version:1,request_hash:h,policy_hash:h,limits:{enabled:true,runtime_configured:true,runtime_identity:h,max_cases:16,max_reduction_attempts:12,max_wall_seconds:600,max_total_cycles:100000,max_reserved_artifact_bytes:268435456},max_attempts:12,original_attempt_id:attempt,original_result_hash:h,candidate_fixture_id:kind==='candidate'?'candidate-correct':null,suite_hash:kind==='candidate'?h:null,provenance,active_case_id:state==='active'?'case-1':null,best_case_id:null,deadline_at:new Date(Date.now()+600000).toISOString(),reserved_cycles:100,reserved_artifact_bytes:1024,cleanup_status:'worker_finalized_external_cleanup_unverified',cases:[{case_id:'case-1',ordinal:1,role:'reproduce',label:'original-failure-replay',plan:{target:family,adapter:family==='arithmetic'?'rtl.arithmetic.v1':'rtl.axi_lite.v1',fixture_id:'original-bug'},plan_hash:h,campaign_id:'campaign-1',experiment_id:'experiment-1',attempt_id:'child-1',execution_lease:'lease-1',status:state==='active'?'queued':'completed',execution_status:state==='active'?'queued':'completed',design_verdict:state==='active'?null:'design_mismatch',result_hash:state==='active'?null:h,result:state==='active'?null:{attempt_id:'child-1',identity},accepted_progress:false}],report_ready:state!=='active',report_hash:state==='active'?null:h}
}
function report(job:ReturnType<typeof fixture>){return {sha256:h,report:{schema_version:'repair.report.v1',job_id:job.job_id,kind:job.kind,execution_status:job.execution_status,reason:job.reason,original_attempt_id:job.original_attempt_id,original_result_hash:h,request_hash:h,policy_hash:h,candidate_fixture_id:job.candidate_fixture_id,suite_hash:job.suite_hash,provenance,cases:job.cases,original_identity:identity,claim:job.kind==='candidate'?'validation_under_exact_recorded_checks':'bounded_search_no_minimality_guarantee',cleanup_status:job.cleanup_status,physical_cleanup_verified:false,operational_acceptance:false,improved:false}}}
function validatedCandidate(){
  const raw=fixture('candidate','completed');raw.reason='validated_under_checks'
  const original=raw.cases[0]
  raw.cases.push(...['witness','neighbor','regression'].map((role,i)=>({...original,case_id:`case-${i+2}`,ordinal:i+2,role,label:role,plan:{...original.plan,fixture_id:'candidate-correct'},campaign_id:`campaign-${i+2}`,experiment_id:`experiment-${i+2}`,attempt_id:`child-${i+2}`,execution_lease:`lease-${i+2}`,design_verdict:'pass_within_scope',result:{attempt_id:`child-${i+2}`,identity}})))
  const review={original_attempt_id:'original-1',candidate_attempt_id:'child-2',checker_sha256:h,contract_sha256:h,provenance,unified_source_diff:'',unified_configuration_diff:'+curated fixture'}
  const validation={status:'validated_under_checks',candidate_fixture_id:'candidate-correct',provenance,neighboring_plan_count:1,regression_plan_count:1,source_review:review,source_review_sha256:h,source_review_status:'available',original_result:{attempt_id:'original-1'},original_replay:{attempt_id:'child-1'},witness:{attempt_id:'child-2'},neighbors:[{attempt_id:'child-3'}],regressions:[{attempt_id:'child-4'}],candidate_experiments:raw.cases.slice(1).map(c=>c.plan)}
  const candidate={schema_version:'family.candidate-report.v1',suite_sha256:h,suite:{schema_version:'family.validation-suite.v1',adapter_id:'rtl.arithmetic.v1',checker_sha256:h,contract_sha256:h,cases:raw.cases.slice(1).map(c=>({role:c.role,label:c.label}))},validation}
  return {raw,wrapper:{...report(raw),report:{...report(raw).report,family_candidate_report:candidate}},validation}
}
const json=(v:unknown,status=200)=>new Response(JSON.stringify(v),{status})
function mount(attempt='original-1',adapter='rtl.arithmetic.v1'){
  const client=new QueryClient({defaultOptions:{queries:{retry:false},mutations:{retry:false}}})
  const view=render(<QueryClientProvider client={client}><RepairInvestigation attemptId={attempt} adapterId={adapter} hasIdentity/></QueryClientProvider>)
  return {client,view}
}
afterEach(()=>{cleanup();vi.restoreAllMocks();sessionStorage.clear();history.replaceState(null,'','/')})

it('rejects scope upgrades, malformed capabilities and non-ID-only requests',()=>{
  expect(parseRepairCapabilities(health,'axi_lite')).toEqual({supported:true,admission:true})
  expect(parseRepairCapabilities({...health,repair_admission_enabled:false},'arithmetic').admission).toBe(false)
  expect(()=>parseRepairCapabilities({...health,repair_families:['axi_lite','axi_lite']},'axi_lite')).toThrow()
  expect(()=>parseRepairRequest({kind:'candidate',candidate_fixture_id:'candidate',source:'uploaded code'})).toThrow()
  expect(()=>parseRepairRequest({kind:'reduction',max_attempts:true})).toThrow()
  expect(()=>parseRepairRequest({kind:'candidate',candidate_fixture_id:'../unsafe'})).toThrow()
})
it('rejects foreign jobs, identity drift, false provenance and unbound reports',()=>{
  const raw=fixture(),job=parseRepairStatus(raw,'original-1','arithmetic')
  expect(()=>parseRepairStatus(raw,'another-attempt','arithmetic')).toThrow()
  expect(()=>parseRepairStatus(raw,'original-1','axi_lite')).toThrow()
  expect(()=>compatibleStatus(job,parseRepairStatus({...raw,policy_hash:other},'original-1','arithmetic'))).toThrow()
  expect(()=>parseRepairStatus({...raw,provenance:{...provenance,model_call:{provider:'private'}}},'original-1','arithmetic')).toThrow()
  const done=fixture('reduction','completed'),status=parseRepairStatus(done,'original-1','arithmetic'),record=report(done)
  expect(parseRepairReport(record,status,'arithmetic').improved).toBe(false)
  expect(()=>parseRepairReport({...record,sha256:other},status,'arithmetic')).toThrow()
  record.report.operational_acceptance=true;expect(()=>parseRepairReport(record,status,'arithmetic')).toThrow()
})
it('bounds automatic polling by active status, fresh availability, job deadline and window',()=>{
  const now=Date.now(),job=parseRepairStatus(fixture(),'original-1','arithmetic')
  expect(repairPollInterval(job,true,now+300000,now)).toBe(1500)
  expect(repairPollInterval(job,false,now+300000,now)).toBe(false)
  expect(repairPollInterval(job,true,now,now)).toBe(false)
  expect(repairPollInterval({...job,deadline:new Date(now).toISOString()},true,now+300000,now)).toBe(false)
  expect(repairPollInterval({...job,state:'completed'},true,now+300000,now)).toBe(false)
})
it('shows a candidate validation claim only with exact witness, neighbor and regression bindings',()=>{
  const f=validatedCandidate(),job=parseRepairStatus(f.raw,'original-1','arithmetic')
  expect(parseRepairReport(f.wrapper,job,'arithmetic').candidate?.status).toBe('validated_under_checks')
})
it.each(['count','witness','original','source-review'])('rejects contradictory candidate report %s',field=>{
  const f=validatedCandidate(),job=parseRepairStatus(f.raw,'original-1','arithmetic')
  if(field==='count')f.validation.neighboring_plan_count=0
  if(field==='witness')f.validation.witness.attempt_id='wrong-child'
  if(field==='original')f.validation.original_replay.attempt_id='wrong-original-replay'
  if(field==='source-review')f.validation.source_review.candidate_attempt_id='wrong-source-attempt'
  expect(()=>parseRepairReport(f.wrapper,job,'arithmetic')).toThrow()
})
it('keeps FIFO outside the durable API and shows an unsupported runtime without job controls',async()=>{
  const fetch=vi.spyOn(globalThis,'fetch').mockResolvedValue(json({status:'ok'}))
  const first=mount('original-1','rtl.fifo.v1');expect(fetch).not.toHaveBeenCalled();first.view.unmount()
  mount('original-1','rtl.axi_lite.v1');await screen.findByText(/AXI-Lite reduction is unsupported on this runtime/)
  expect(screen.queryByRole('button',{name:'Submit repair investigation'})).not.toBeInTheDocument()
})
it('submits only IDs and preserves the exact body/key across unknown failures and remounts',async()=>{
  const posts:{key:string;body:string}[]=[]
  vi.spyOn(globalThis,'fetch').mockImplementation(async(input,init)=>{
    if(String(input)==='/api/health')return json(health)
    if(init?.method==='POST'){posts.push({key:(init.headers as Record<string,string>)['Idempotency-Key'],body:String(init.body)});throw new Error('PRIVATE NETWORK DETAIL')}
    throw new Error('Unexpected test GET')
  })
  const first=mount();await screen.findByRole('button',{name:'Submit repair investigation'})
  await userEvent.type(screen.getByLabelText('Installed candidate fixture ID'),'candidate-correct')
  await userEvent.click(screen.getByRole('button',{name:'Submit repair investigation'}));await screen.findByRole('alert')
  await userEvent.click(screen.getByRole('button',{name:'Retry preserved repair request'}));await waitFor(()=>expect(posts).toHaveLength(2))
  first.view.unmount();mount();await screen.findByRole('button',{name:'Retry preserved repair request'})
  await userEvent.click(screen.getByRole('button',{name:'Retry preserved repair request'}));await waitFor(()=>expect(posts).toHaveLength(3))
  expect(new Set(posts.map(p=>p.key)).size).toBe(1)
  expect(JSON.parse(posts[0].body)).toEqual({kind:'candidate',candidate_fixture_id:'candidate-correct'})
  expect(posts.every(p=>p.body===posts[0].body)).toBe(true)
  expect(document.body.textContent).not.toContain('PRIVATE NETWORK DETAIL')
})
it('blocks new admission on hold while still reading and cancelling the exact saved job',async()=>{
  history.replaceState(null,'','/?repair=job-1');let cancelled=false
  const active=fixture(),cancelledJob={...active,execution_status:'cancelled',reason:'cancel_requested_no_automatic_retry',version:2,cleanup_status:'external_settlement_required',report_ready:true,report_hash:h,cases:active.cases.map(c=>({...c,status:'cancelled',execution_status:'cancelled'}))}
  const fetch=vi.spyOn(globalThis,'fetch').mockImplementation(async(input,init)=>{
    const path=String(input)
    if(path==='/api/health')return json({...health,repair_admission_enabled:false})
    if(path.endsWith('/cancel')&&init?.method==='POST'){cancelled=true;return json(cancelledJob)}
    if(path==='/api/repairs/job-1')return json(active)
    throw new Error('Unexpected test request')
  })
  mount();await screen.findByRole('button',{name:'Cancel durable repair job'})
  expect(screen.getByText(/Repair admission is held or disabled/)).toBeInTheDocument()
  await userEvent.click(screen.getByRole('button',{name:'Cancel durable repair job'}));await waitFor(()=>expect(cancelled).toBe(true))
  await screen.findByText('cancelled',{exact:true,selector:'strong'})
  expect(fetch.mock.calls.filter(([,init])=>init?.method==='POST').every(([path])=>String(path).endsWith('/cancel'))).toBe(true)
})
it('discards a late successful submission after the attempt selection changes',async()=>{
  let resolve!:(r:Response)=>void
  vi.spyOn(globalThis,'fetch').mockImplementation(async(input,init)=>String(input)==='/api/health'?json(health):init?.method==='POST'?new Promise<Response>(done=>{resolve=done}):json(fixture()))
  const {view,client}=mount();await screen.findByRole('button',{name:'Submit repair investigation'})
  await userEvent.type(screen.getByLabelText('Installed candidate fixture ID'),'candidate-correct');await userEvent.click(screen.getByRole('button',{name:'Submit repair investigation'}));await waitFor(()=>expect(resolve).toBeTypeOf('function'))
  view.rerender(<QueryClientProvider client={client}><RepairInvestigation attemptId="original-2" adapterId="rtl.arithmetic.v1" hasIdentity/></QueryClientProvider>)
  await act(async()=>{resolve(json(fixture()));await new Promise(done=>setTimeout(done,10))})
  expect(screen.queryByText('job-1')).not.toBeInTheDocument();expect(new URLSearchParams(location.search).get('repair')).toBeNull()
  expect(readPending(repairStorageKey('original-1','rtl.arithmetic.v1'))?.key).toBeTruthy()
})
it('hides child/report data after a failed status refresh instead of presenting old success',async()=>{
  history.replaceState(null,'','/?repair=job-1');let corrupt=false
  vi.spyOn(globalThis,'fetch').mockImplementation(async input=>String(input)==='/api/health'?json(health):String(input).endsWith('/report')?json(report(fixture('reduction','completed'))):corrupt?json({private:'SECRET'},409):json(fixture('reduction','completed')))
  mount();await screen.findByRole('button',{name:'Inspect settled repair report'});await userEvent.click(screen.getByRole('button',{name:'Inspect settled repair report'}))
  const result=await screen.findByLabelText('Settled repair report');expect(within(result).getByText(/minimality is not guaranteed/)).toBeInTheDocument()
  corrupt=true;await userEvent.click(screen.getByRole('button',{name:'Refresh repair job'}));await screen.findByRole('alert')
  expect(screen.queryByLabelText('Settled repair report')).not.toBeInTheDocument();expect(screen.queryByRole('region',{name:'Repair child executions'})).not.toBeInTheDocument()
  expect(document.body.textContent).not.toContain('SECRET')
})
it('blocks replacements when browser request storage is corrupt',async()=>{
  sessionStorage.setItem(repairStorageKey('original-1','rtl.arithmetic.v1'),'broken-json')
  const fetch=vi.spyOn(globalThis,'fetch').mockResolvedValue(json(health));mount()
  await screen.findByRole('button',{name:'Submit repair investigation'})
  await userEvent.type(screen.getByLabelText('Installed candidate fixture ID'),'candidate-correct')
  expect(screen.getByRole('button',{name:'Submit repair investigation'})).toBeDisabled();expect(screen.getByRole('alert')).toHaveTextContent('storage is unavailable or corrupt')
  expect(fetch.mock.calls.every(([,init])=>init?.method!=='POST')).toBe(true)
})
