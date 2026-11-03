// Synthetic durable API responses only; no dispatcher, simulator or model runs.
import { act, cleanup, render, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { afterEach, expect, it, vi } from 'vitest'
import { RepairInvestigation } from './RepairInvestigation'
import { RepairReportDetails } from './RepairReportDetails'
import { compatibleStatus, parseRepairCapabilities, parseRepairReport, parseRepairRequest, parseRepairStatus, readPending, repairPollInterval, repairStorageKey } from './repairApi'

const h='a'.repeat(64),other='b'.repeat(64)
const health={status:'ok',repair_api:'1',repair_admission_enabled:true,repair_families:['arithmetic','axi_lite']}
const provenance={schema_version:'candidate.provenance.v1',recommendation_origin:'curated_catalog',materialization:'curated_fixture_selection',authorship_claim:'curated_source_not_generated_patch',assertion_basis:'controller_supplied_record',proposal_id:null,proposal_sha256:null,external_reference_id:null,model_call:null}
const identity={source_hash:h,checker_hash:h,contract_hash:h,experiment_hash:h,environment_hash:h}
function fixture(kind:'candidate'|'reduction'='candidate',state='active',attempt='original-1',family='arithmetic'){
  return {job_id:'job-1',kind,execution_status:state,reason:state==='active'?null:'original_not_reproduced',version:1,request_hash:h,policy_hash:h,limits:{enabled:true,runtime_configured:true,runtime_identity:h,max_cases:16,max_reduction_attempts:12,max_wall_seconds:600,max_total_cycles:100000,max_reserved_artifact_bytes:268435456},max_attempts:12,original_attempt_id:attempt,original_result_hash:h,candidate_fixture_id:kind==='candidate'?'candidate-correct':null,suite_hash:kind==='candidate'?h:null,provenance,active_case_id:state==='active'?'case-1':null,best_case_id:null,deadline_at:new Date(Date.now()+600000).toISOString(),reserved_cycles:100,reserved_artifact_bytes:1024,cleanup_status:'worker_finalized_external_cleanup_unverified',cases:[{case_id:'case-1',ordinal:1,role:'reproduce',label:'original-failure-replay',plan:{target:family,adapter:family==='arithmetic'?'rtl.arithmetic.v1':'rtl.axi_lite.v1',fixture_id:'original-bug'},plan_hash:h,campaign_id:'campaign-1',experiment_id:'experiment-1',attempt_id:'child-1',execution_lease:'lease-1',status:state==='active'?'queued':'completed',execution_status:state==='active'?'queued':'completed',design_verdict:state==='active'?null:'design_mismatch',result_hash:state==='active'?null:h,result:state==='active'?null:reportResult('child-1'),accepted_progress:false}],report_ready:state!=='active',report_hash:state==='active'?null:h}
}
function reportPlan(family='arithmetic',count=4){return {schema_version:family==='arithmetic'?'arithmetic.v1':'axi_lite.v1',adapter:family==='arithmetic'?'rtl.arithmetic.v1':'rtl.axi_lite.v1',target:family,experiment_id:'wire-original',fixture_id:'original-bug',contract_id:'fixed-contract',seed:1,parameters:family==='arithmetic'?{width:8,latency:2}:{data_width:32,address_width:8},budget:{max_cycles:1000,wall_time_seconds:120,max_artifact_bytes:1048576},...(family==='arithmetic'?{operations:Array.from({length:count},()=>({op:'idle',cycles:1}))}:{cycles:Array.from({length:count},()=>({reset_n:true}))})}}
function reportResult(attempt='original-1',pass=false){return {attempt_id:attempt,execution_status:'completed',verdict:pass?'pass_within_scope':'design_mismatch',identity,usage:{cycles:10,wall_time_seconds:0.1},failure_signature:pass?null:{requirement_id:'fixed-requirement',failure_class:'fixed-failure',first_cycle:8,expected:'expected',observed:'observed',signature_hash:h}}}
function report(job:ReturnType<typeof fixture>){const original_plan=reportPlan(job.cases[0].plan.target),original_result=reportResult(job.original_attempt_id);return {sha256:h,report:{schema_version:'repair.report.v1',job_id:job.job_id,kind:job.kind,execution_status:job.execution_status,reason:job.reason,original_attempt_id:job.original_attempt_id,original_result_hash:h,original_plan,original_result,request_hash:h,policy_hash:h,candidate_fixture_id:job.candidate_fixture_id,suite_hash:job.suite_hash,provenance,cases:job.cases,original_identity:identity,claim:job.kind==='candidate'?'validation_under_exact_recorded_checks':'bounded_search_no_minimality_guarantee',cleanup_status:job.cleanup_status,physical_cleanup_verified:false,operational_acceptance:false,improved:false,best_case_id:job.best_case_id,...(job.kind==='reduction'?{reduced_experiment:original_plan,reduced_result:original_result}:{})}}}
function validatedCandidate(){
  const raw=fixture('candidate','completed');raw.reason='validated_under_checks'
  const original=raw.cases[0]
  raw.cases.push(...['witness','neighbor','regression'].map((role,i)=>({...original,case_id:`case-${i+2}`,ordinal:i+2,role,label:role,plan:{...original.plan,fixture_id:'candidate-correct'},campaign_id:`campaign-${i+2}`,experiment_id:`experiment-${i+2}`,attempt_id:`child-${i+2}`,execution_lease:`lease-${i+2}`,design_verdict:'pass_within_scope',result:reportResult(`child-${i+2}`,true)})))
  const review={original_attempt_id:'original-1',candidate_attempt_id:'child-2',checker_sha256:h,contract_sha256:h,original_source:{fixture_id:'original-bug',file_sha256:h,source_identity_sha256:h,size_bytes:100,curated_variant:1},candidate_source:{fixture_id:'candidate-correct',file_sha256:h,source_identity_sha256:h,size_bytes:100,curated_variant:0},source_bytes_changed:false,configuration_changed:true,provenance,unified_source_diff:'',unified_configuration_diff:'+curated fixture'}
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
function reducedFixture(){
  const raw=fixture('reduction','completed'),reducedPlan=reportPlan('arithmetic',2),reducedResult={...reportResult('child-2'),identity:{...identity,experiment_hash:other},usage:{cycles:6,wall_time_seconds:0.05}}
  raw.reason='bounded_candidates_exhausted';Object.assign(raw,{best_case_id:'case-2'})
  raw.cases.push({...raw.cases[0],case_id:'case-2',ordinal:2,role:'reduction',label:'trial-2',plan:reducedPlan,plan_hash:other,campaign_id:'campaign-2',experiment_id:'experiment-2',attempt_id:'child-2',execution_lease:'lease-2',result:reducedResult,accepted_progress:true})
  const wrapper={...report(raw),report:{...report(raw).report,improved:true,best_case_id:'case-2',reduced_experiment:reducedPlan,reduced_result:reducedResult}}
  return {raw,reducedPlan,reducedResult,wrapper}
}
it('connects exact reduction plans, checked outcomes and failure signatures',()=>{
  const {raw,reducedResult,wrapper}=reducedFixture(),job=parseRepairStatus(raw,'original-1','arithmetic'),parsed=parseRepairReport(wrapper,job,'arithmetic')
  render(<RepairReportDetails report={parsed} kind="reduction"/>)
  const region=screen.getByRole('region',{name:'Reduction witness comparison'})
  expect(within(region).getByText('4')).toBeInTheDocument();expect(within(region).getByText('2')).toBeInTheDocument()
  expect(screen.getByText(/minimality is not guaranteed/)).toBeInTheDocument()
  expect(screen.getAllByText('fixed-requirement · fixed-failure · first cycle 8')).toHaveLength(2)
  reducedResult.identity.checker_hash=other;expect(()=>parseRepairReport(wrapper,job,'arithmetic')).toThrow()
})
it.each([0.5,120.5,3600])('preserves valid fractional/subsecond execution budget %s',seconds=>{
  const raw=fixture('reduction','completed'),wrapper=report(raw)
  wrapper.report.original_plan.budget.wall_time_seconds=seconds
  expect(parseRepairReport(wrapper,parseRepairStatus(raw,'original-1','arithmetic'),'arithmetic').reduction?.reducedPlan.seconds).toBe(seconds)
})
it.each([0,-0.5,3600.1,Infinity,NaN])('rejects invalid execution budget %s',seconds=>{
  const raw=fixture('reduction','completed'),wrapper=report(raw)
  wrapper.report.original_plan.budget.wall_time_seconds=seconds
  expect(()=>parseRepairReport(wrapper,parseRepairStatus(raw,'original-1','arithmetic'),'arithmetic')).toThrow()
})
it.each(['plan-id','plan-rows','verdict','cycles','seconds','failure-value','failure-class'])('rejects detached reduced details with unchanged accepted case: %s',field=>{
  const f=reducedFixture(),job=parseRepairStatus(f.raw,'original-1','arithmetic')
  const wrapper={...f.wrapper,report:{...f.wrapper.report,reduced_experiment:structuredClone(f.reducedPlan),reduced_result:structuredClone(f.reducedResult)}}
  if(field==='plan-id')wrapper.report.reduced_experiment.experiment_id='different-plan'
  if(field==='plan-rows'&&'operations' in wrapper.report.reduced_experiment)wrapper.report.reduced_experiment.operations?.push({op:'idle',cycles:1})
  if(field==='verdict'){wrapper.report.reduced_result.verdict='pass_within_scope';wrapper.report.reduced_result.failure_signature=null}
  if(field==='cycles')wrapper.report.reduced_result.usage.cycles++
  if(field==='seconds')wrapper.report.reduced_result.usage.wall_time_seconds=99
  if(field==='failure-value')wrapper.report.reduced_result.failure_signature!.observed='invented'
  if(field==='failure-class')wrapper.report.reduced_result.failure_signature!.failure_class='another-failure'
  expect(()=>parseRepairReport(wrapper,job,'arithmetic')).toThrow()
})
it('allows accepted reduction first-cycle and value changes while preserving backend failure semantics',()=>{
  const f=reducedFixture();f.reducedResult.failure_signature!.first_cycle=3;f.reducedResult.failure_signature!.observed='reduced value';f.reducedResult.failure_signature!.signature_hash=other
  const parsed=parseRepairReport(f.wrapper,parseRepairStatus(f.raw,'original-1','arithmetic'),'arithmetic')
  expect(parsed.reduction?.reducedResult.failure?.cycle).toBe(3)
  expect(parsed.reduction?.reducedResult.failure?.observed).toBe('reduced value')
})
it('renders source bytes and curated configuration as distinct candidate changes',()=>{
  const f=validatedCandidate(),job=parseRepairStatus(f.raw,'original-1','arithmetic')
  render(<RepairReportDetails report={parseRepairReport(f.wrapper,job,'arithmetic')} kind="candidate"/>)
  expect(screen.getByRole('region',{name:'Candidate source identities'})).toBeInTheDocument()
  expect(screen.getByText(/Source bytes changed: false · curated configuration changed: true/)).toBeInTheDocument()
  f.validation.source_review.candidate_source.source_identity_sha256=other
  expect(()=>parseRepairReport(f.wrapper,job,'arithmetic')).toThrow()
})
it('links exact child campaigns without carrying an old repair or export selection',async()=>{
  history.replaceState(null,'','/?repair=job-1&export=old-export')
  vi.spyOn(globalThis,'fetch').mockImplementation(async input=>String(input)==='/api/health'?json(health):json(fixture()))
  mount();const link=await screen.findByRole('link',{name:'Open reproduce campaign campaign-1'})
  expect(link).toHaveAttribute('href','/?campaign=campaign-1')
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
it('does not restart terminal status polling on health toggles and still refreshes explicitly',async()=>{
  history.replaceState(null,'','/?repair=job-1')
  const done=fixture('reduction','completed');let healthReads=0,statusReads=0,release!:(r:Response)=>void
  vi.spyOn(globalThis,'fetch').mockImplementation(async input=>{
    if(String(input)==='/api/health'){healthReads++;return healthReads===1?json(health):new Promise<Response>(resolve=>{release=resolve})}
    if(String(input)==='/api/repairs/job-1'){statusReads++;return json(done)}
    throw new Error('Unexpected synthetic request')
  })
  mount();await screen.findByRole('button',{name:'Inspect settled repair report'});expect(statusReads).toBe(1)
  await userEvent.click(screen.getByRole('button',{name:'Refresh availability'}))
  await screen.findByText('Checking durable repair availability…')
  expect(screen.queryByRole('region',{name:'Repair child executions'})).not.toBeInTheDocument()
  await act(async()=>{release(json(health));await new Promise(resolve=>setTimeout(resolve,30))})
  await screen.findByRole('button',{name:'Inspect settled repair report'});expect(statusReads).toBe(1)
  await userEvent.click(screen.getByRole('button',{name:'Refresh repair job'}));await waitFor(()=>expect(statusReads).toBe(2))
})
