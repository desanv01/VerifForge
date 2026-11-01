import { identifier, investigationGet } from './engineeringInvestigationApi'
import { parseInspectionJson } from './json'

export const REPAIR_POLL_WINDOW = 300000
export type RepairFamily = 'arithmetic' | 'axi_lite'
export type RepairRequest = {kind:'candidate';candidate_fixture_id:string} | {kind:'reduction';max_attempts:number}
export class RepairError extends Error { constructor(public status:number,message:string){super(message)} }
function invalid():never{throw new RepairError(0,'Repair metadata is invalid or does not match the selected attempt. No repair result was interpreted.')}
const obj=(v:unknown):Record<string,unknown>=>{if(!v||typeof v!=='object'||Array.isArray(v))invalid();return v as Record<string,unknown>}
const text=(v:unknown,max=128)=>{if(typeof v!=='string'||!v.length||v.length>max)invalid();return v}
const code=(v:unknown)=>{const s=text(v);if(!/^[a-z][a-z0-9_]{0,127}$/.test(s))invalid();return s}
const hash=(v:unknown)=>{const s=text(v,64);if(!/^[a-f0-9]{64}$/.test(s))invalid();return s}
const number=(v:unknown,max=Number.MAX_SAFE_INTEGER,min=0)=>{if(typeof v!=='number'||!Number.isSafeInteger(v)||v<min||v>max)invalid();return v}
const boolean=(v:unknown)=>{if(typeof v!=='boolean')invalid();return v}
const one=<T extends string>(v:unknown,allowed:readonly T[]):T=>{if(!allowed.includes(v as T))invalid();return v as T}
const list=<T>(v:unknown,parse:(v:unknown)=>T,max=101):T[]=>{if(!Array.isArray(v)||v.length>max)invalid();return v.map(parse)}
const nullable=<T>(v:unknown,parse:(v:unknown)=>T):T|null=>v===null?null:parse(v)
const date=(v:unknown)=>{const s=text(v,64);if(!/\dT.*(?:Z|[+-]\d\d:\d\d)$/.test(s)||!Number.isFinite(Date.parse(s)))invalid();return s}
const same=(a:unknown,b:unknown)=>JSON.stringify(a)===JSON.stringify(b)
const verdict=(v:unknown)=>one(v,['pass_within_scope','design_mismatch','property_failure','inconclusive','unsupported','timeout','build_error','harness_error','resource_limit'])
export function repairFamily(adapter:string|null|undefined):RepairFamily|null{return adapter==='rtl.arithmetic.v1'?'arithmetic':adapter==='rtl.axi_lite.v1'?'axi_lite':null}
export function parseRepairCapabilities(raw:unknown,family:RepairFamily){
  const o=obj(raw)
  if(o.repair_api!=='1')return {supported:false,admission:false}
  one(o.status,['ok']);const families=list(o.repair_families,v=>one(v,['arithmetic','axi_lite']),2)
  if(new Set(families).size!==families.length)invalid()
  return {supported:families.includes(family),admission:boolean(o.repair_admission_enabled)}
}
export function parseRepairRequest(raw:unknown):RepairRequest{
  const o=obj(raw)
  if(o.kind==='candidate'&&Object.keys(o).sort().join()===['candidate_fixture_id','kind'].join())return {kind:'candidate',candidate_fixture_id:identifier(o.candidate_fixture_id)}
  if(o.kind==='reduction'&&Object.keys(o).sort().join()===['kind','max_attempts'].join())return {kind:'reduction',max_attempts:number(o.max_attempts,100,1)}
  return invalid()
}
function provenance(raw:unknown){
  const p=obj(raw);one(p.schema_version,['candidate.provenance.v1']);one(p.materialization,['curated_fixture_selection']);one(p.authorship_claim,['curated_source_not_generated_patch']);one(p.assertion_basis,['controller_supplied_record'])
  const origin=one(p.recommendation_origin,['curated_catalog','external_recommendation','model_recommendation'])
  const proposal=nullable(p.proposal_id,identifier),proposalHash=nullable(p.proposal_sha256,hash),external=nullable(p.external_reference_id,identifier)
  const model=nullable(p.model_call,v=>{const m=obj(v);return {provider:text(m.provider),model:text(m.model),id:identifier(m.model_call_id),prompt:hash(m.prompt_sha256),response:hash(m.response_sha256)}})
  if(origin==='curated_catalog'&&(proposal||proposalHash||external||model)||origin!=='curated_catalog'&&(!proposal||!proposalHash)||origin==='external_recommendation'&&(!external||model)||origin==='model_recommendation'&&(!model||external))invalid()
  return {origin,proposal,proposalHash,external,model}
}
function identity(raw:unknown){const i=obj(raw);return {source:hash(i.source_hash),checker:hash(i.checker_hash),contract:hash(i.contract_hash),experiment:hash(i.experiment_hash),environment:hash(i.environment_hash)}}
function repairCase(raw:unknown,family:RepairFamily){
  const c=obj(raw),plan=obj(c.plan)
  one(plan.target,[family]);one(plan.adapter,[family==='arithmetic'?'rtl.arithmetic.v1':'rtl.axi_lite.v1'])
  const attempt=nullable(c.attempt_id,identifier),result=nullable(c.result,v=>{const r=obj(v);if(r.attempt_id!==attempt)invalid();return {identity:identity(r.identity)}})
  const row={id:identifier(c.case_id),ordinal:number(c.ordinal,101,1),role:one(c.role,['reproduce','witness','neighbor','regression','reduction']),label:text(c.label),fixture:identifier(plan.fixture_id),planHash:hash(c.plan_hash),campaign:identifier(c.campaign_id),experiment:identifier(c.experiment_id),attempt,state:one(c.status,['planned','queued','completed','cancelled','interrupted']),execution:one(c.execution_status,['not_run','queued','preparing','running','finalizing','completed','cancelled','interrupted','infrastructure_error']),verdict:nullable(c.design_verdict,verdict),resultHash:nullable(c.result_hash,hash),result,progress:boolean(c.accepted_progress)}
  if((c.execution_lease===null)!==(attempt===null)||row.state==='completed'&&(!result||!row.resultHash)||row.progress&&(row.role!=='reduction'||row.state!=='completed'))invalid()
  if(c.execution_lease!==null)identifier(c.execution_lease)
  return row
}
function cases(raw:unknown,family:RepairFamily,kind:'candidate'|'reduction'){
  const rows=list(raw,v=>repairCase(v,family))
  if(!rows.length||rows[0].role!=='reproduce'||rows.some((r,i)=>r.ordinal!==i+1||i>0&&(kind==='candidate'?!['witness','neighbor','regression'].includes(r.role):r.role!=='reduction'))||new Set(rows.map(r=>r.id)).size!==rows.length)invalid()
  return rows
}
export function parseRepairStatus(raw:unknown,attemptId:string,family:RepairFamily,jobId?:string){
  const o=obj(raw);if(o.original_attempt_id!==attemptId||jobId&&o.job_id!==jobId)invalid()
  const kind=one(o.kind,['candidate','reduction']),rows=cases(o.cases,family,kind),limits=obj(o.limits)
  const status={id:identifier(o.job_id),kind,state:one(o.execution_status,['active','completed','cancelled','interrupted','incomplete']),reason:nullable(o.reason,code),version:number(o.version),requestHash:hash(o.request_hash),policyHash:hash(o.policy_hash),original:attemptId,originalHash:hash(o.original_result_hash),candidate:nullable(o.candidate_fixture_id,identifier),suite:nullable(o.suite_hash,hash),provenance:provenance(o.provenance),active:nullable(o.active_case_id,identifier),best:nullable(o.best_case_id,identifier),deadline:date(o.deadline_at),cycles:number(o.reserved_cycles,1000000),bytes:number(o.reserved_artifact_bytes,1073741824),maximum:number(o.max_attempts,100,1),cleanup:one(o.cleanup_status,['not_started','worker_ownership_required','external_settlement_required','worker_finalized_external_cleanup_unverified']),cases:rows,reportReady:boolean(o.report_ready),reportHash:nullable(o.report_hash,hash),limits:{cases:number(limits.max_cases,101,3),cycles:number(limits.max_total_cycles,1000000,1),bytes:number(limits.max_reserved_artifact_bytes,1073741824,1024),seconds:number(limits.max_wall_seconds,3600,1)}}
  if(status.reportReady!==(status.reportHash!==null)||status.state==='active'&&status.reportReady||kind==='candidate'&&(!status.candidate||!status.suite)||kind==='reduction'&&(status.candidate||status.suite)||rows.length>status.limits.cases||status.active&&!rows.some(r=>r.id===status.active)||status.best&&!rows.some(r=>r.id===status.best&&r.progress))invalid()
  return status
}
export type RepairStatus=ReturnType<typeof parseRepairStatus>
export function repairPollInterval(job:RepairStatus|undefined,available:boolean,until:number,now=Date.now()):1500|false{return available&&job?.state==='active'&&now<Math.min(until,Date.parse(job.deadline))?1500:false}
export function compatibleStatus(prior:RepairStatus|null,next:RepairStatus){
  if(prior&&(next.version<prior.version||['id','kind','requestHash','policyHash','original','originalHash','candidate','suite','deadline','maximum'].some(k=>prior[k as keyof RepairStatus]!==next[k as keyof RepairStatus])||!same(prior.provenance,next.provenance)||prior.reportHash&&prior.reportHash!==next.reportHash||prior.state!=='active'&&next.state!==prior.state))invalid()
  if(prior&&prior.cases.some(old=>{const row=next.cases.find(c=>c.id===old.id);return !row||(['ordinal','role','fixture','planHash','campaign','experiment'] as const).some(k=>old[k]!==row[k])||old.attempt!==null&&old.attempt!==row.attempt||old.resultHash!==null&&old.resultHash!==row.resultHash||old.progress&&!row.progress}))invalid()
  return next
}
export async function repairGet<T>(path:string,parse:(raw:unknown)=>T,signal?:AbortSignal){return investigationGet(path,parse,signal)}
export async function repairPost<T>(path:string,body:RepairRequest|undefined,key:string,parse:(raw:unknown)=>T,signal?:AbortSignal):Promise<T>{
  let response:Response
  try{response=await fetch(`/api${path}`,{method:'POST',headers:{'Content-Type':'application/json','Idempotency-Key':key},body:body?JSON.stringify(body):undefined,cache:'no-store',signal:signal?AbortSignal.any([signal,AbortSignal.timeout(10000)]):AbortSignal.timeout(10000)})}catch{throw new RepairError(0,'Repair submission outcome is unknown. Retry this exact request with its preserved key.')}
  if(!response.ok){await response.body?.cancel().catch(()=>undefined);throw new RepairError(response.status,response.status===503?'Repair admission is held or disabled. No automatic retry.':`Repair request failed (HTTP ${response.status}). Retry the same request or inspect the saved job.`)}
  const reader=response.body?.getReader();if(!reader)invalid()
  let length=0,raw='';const decoder=new TextDecoder('utf-8',{fatal:true})
  try{while(true){const {done,value}=await reader.read();if(done)break;length+=value.byteLength;if(length>4*1024*1024)invalid();raw+=decoder.decode(value,{stream:true})}raw+=decoder.decode();if(signal?.aborted)invalid();return parse(parseInspectionJson(raw))}finally{await reader.cancel().catch(()=>undefined);reader.releaseLock()}
}
export function parseRepairReport(raw:unknown,job:RepairStatus,family:RepairFamily){
  const wrapper=obj(raw),r=obj(wrapper.report)
  if(!job.reportReady||hash(wrapper.sha256)!==job.reportHash||r.job_id!==job.id||r.original_attempt_id!==job.original||r.kind!==job.kind||r.execution_status!==job.state||r.request_hash!==job.requestHash||r.policy_hash!==job.policyHash||r.original_result_hash!==job.originalHash||r.candidate_fixture_id!==job.candidate||r.suite_hash!==job.suite||r.physical_cleanup_verified!==false||r.operational_acceptance!==false)invalid()
  one(r.schema_version,['repair.report.v1']);const origin=provenance(r.provenance);if(!same(origin,job.provenance))invalid()
  const rows=cases(r.cases,family,job.kind);if(rows.length!==job.cases.length||rows.some((c,i)=>!same(c,job.cases[i])))invalid()
  const original=identity(r.original_identity)
  let candidate=null as null|{status:string;neighbors:number;regressions:number;reviewHash:string|null;sourceDiff:string|null;configurationDiff:string|null;checker:string;contract:string}
  if(job.kind==='candidate'&&r.family_candidate_report!==undefined){
    const f=obj(r.family_candidate_report),v=obj(f.validation),suite=obj(f.suite)
    one(f.schema_version,['family.candidate-report.v1']);if(hash(f.suite_sha256)!==job.suite||v.candidate_fixture_id!==job.candidate||!same(provenance(v.provenance),origin))invalid()
    const checker=hash(suite.checker_sha256),contract=hash(suite.contract_sha256);if(checker!==original.checker||contract!==original.contract)invalid()
    const review=nullable(v.source_review,obj),reviewHash=nullable(v.source_review_sha256,hash)
    if(review){if(review.original_attempt_id!==job.original||hash(review.checker_sha256)!==checker||hash(review.contract_sha256)!==contract||!same(provenance(review.provenance),origin)||v.source_review_status!=='available'||!reviewHash)invalid()}
    const diff=(v:unknown)=>{if(typeof v!=='string'||v.length>4194304)invalid();return v.length<=65536?v:null}
    candidate={status:one(v.status,['original_not_reproduced','failed_under_checks','validated_under_checks']),neighbors:number(v.neighboring_plan_count,100),regressions:number(v.regression_plan_count,100),reviewHash,sourceDiff:review?diff(review.unified_source_diff):null,configurationDiff:review?diff(review.unified_configuration_diff):null,checker,contract}
    if(candidate.status!=='original_not_reproduced'){
      one(suite.schema_version,['family.validation-suite.v1']);one(suite.adapter_id,[family==='arithmetic'?'rtl.arithmetic.v1':'rtl.axi_lite.v1'])
      const children=rows.slice(1),witness=children.filter(c=>c.role==='witness'),neighbors=children.filter(c=>c.role==='neighbor'),regressions=children.filter(c=>c.role==='regression')
      const suiteCases=list(suite.cases,obj),experiments=list(v.candidate_experiments,obj)
      if(witness.length!==1||children[0]!==witness[0]||!neighbors.length||!regressions.length||neighbors.length!==candidate.neighbors||regressions.length!==candidate.regressions||suiteCases.length!==children.length||experiments.length!==children.length||children.some((c,i)=>suiteCases[i].role!==c.role||suiteCases[i].label!==c.label||experiments[i].fixture_id!==job.candidate||c.fixture!==job.candidate||!c.attempt||!c.result||!c.resultHash))invalid()
      if(obj(v.original_result).attempt_id!==job.original||obj(v.original_replay).attempt_id!==rows[0].attempt||obj(v.witness).attempt_id!==witness[0].attempt||review&&review.candidate_attempt_id!==witness[0].attempt)invalid()
      for(const [values,expected] of [[v.neighbors,neighbors],[v.regressions,regressions]] as const){const results=list(values,obj);if(results.length!==expected.length||results.some((r,i)=>r.attempt_id!==expected[i].attempt))invalid()}
    }
  }
  if(job.kind==='candidate'){one(r.claim,['validation_under_exact_recorded_checks']);if(job.state==='completed'&&['validated_under_checks','failed_under_checks'].includes(job.reason??'')&&candidate?.status!==job.reason)invalid()}
  else one(r.claim,['bounded_search_no_minimality_guarantee'])
  const improved=job.kind==='reduction'?boolean(r.improved):null
  if(improved!==null&&improved!==!!job.best||candidate?.status==='validated_under_checks'&&rows.slice(1).some(c=>c.verdict!=='pass_within_scope'||c.result?.identity.checker!==original.checker||c.result?.identity.contract!==original.contract))invalid()
  return {sha256:hash(wrapper.sha256),reason:nullable(r.reason,code),original,candidate,improved,cleanup:one(r.cleanup_status,['not_started','worker_ownership_required','external_settlement_required','worker_finalized_external_cleanup_unverified'])}
}

export interface PendingRepair {key:string;body:RepairRequest;jobId?:string}
export const repairStorageKey=(attempt:string,adapter:string)=>`verifforge.repair.v1:${identifier(attempt)}:${identifier(adapter)}`
export function readPending(key:string):PendingRepair|null{
  try{const raw=sessionStorage.getItem(key);if(!raw)return null;if(raw.length>2048)invalid();const o=obj(JSON.parse(raw));if(!['body,key','body,jobId,key'].includes(Object.keys(o).sort().join()))invalid();return {key:identifier(o.key),body:parseRepairRequest(o.body),...(o.jobId===undefined?{}:{jobId:identifier(o.jobId)})}}catch{throw new RepairError(0,'Saved repair request storage is unavailable or corrupt. No replacement request can be submitted safely.')}
}
export function persistPending(key:string,value:PendingRepair){sessionStorage.setItem(key,JSON.stringify(value))}
