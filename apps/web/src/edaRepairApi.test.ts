// Synthetic contract and mocked transport checks; no native execution.
import { afterEach, expect, it, vi } from 'vitest'
import { compatibleEdaJob, edaBinding, edaPollInterval, edaReplaceable, edaRequest, parseEdaJob, readPendingEda, EDA_STATES } from './edaRepairApi'
import type { EdaBinding } from './edaRepairApi'
import type { VersionSummary } from './engineeringInvestigationApi'
const h='a'.repeat(64)
const original:VersionSummary={id:'original',projectId:'project',investigationId:'investigation',ordinal:1,contentHash:h,revisionId:'revision',revisionHash:h,manifestHash:h,createdAt:'2026-11-03T00:00:00Z',domains:['kicad'],nativeId:'native',schemaVersion:'engineering.admission.v3'}
const candidate={...original,id:'candidate',ordinal:2}
const binding:EdaBinding={domain:'kicad',request:{project_id:'project',original_version_id:'original',candidate_version_id:'candidate'}}
function wire(status:string='queued'){return {job_id:'job',domain:'kicad',...binding.request,status,cancel_requested:false,blockers:status==='blocked'?['unreviewed_mapping']:[],result:status==='completed'||status==='failed'?{ticket_hash:h,configuration_hash:h,native_result_sha256:h,retained:true,settled:true,execution_status:status,design_verdict:'not_evaluated'}:null,design_verdict:'not_evaluated',physical_electrical_verdict:'inconclusive'}}
afterEach(()=>{vi.restoreAllMocks();sessionStorage.clear()})
it.each(EDA_STATES)('parses the exact %s state without inventing a design verdict',state=>{expect(parseEdaJob(wire(state),binding,'job').status).toBe(state)})
it.each(['completed','failed','cancelled'])('preserves strict %s completion as quarantined evidence without replacement permission',execution=>{
  const raw={...wire('quarantined'),result:{...wire('completed').result!,execution_status:execution}}
  const parsed=parseEdaJob(raw,binding,'job')
  expect(parsed.result?.execution_status).toBe(execution)
  expect(parsed.design_verdict).toBe('not_evaluated')
  expect(parsed.physical_electrical_verdict).toBe('inconclusive')
  expect(edaReplaceable(parsed)).toBe(false)
  expect(()=>parseEdaJob({...raw,result:{...raw.result,settled:false}},binding)).toThrow()
  expect(()=>parseEdaJob({...raw,result:{...raw.result,design_verdict:'pass_within_scope'}},binding)).toThrow()
})
it.each(EDA_STATES)('separates stopped polling from replacement permission for %s',state=>{
  expect(edaReplaceable(parseEdaJob(wire(state),binding))).toBe(['blocked','completed','failed','cancelled'].includes(state))
})
it.each(['missing_io_requirements','unreviewed_mapping','unsupported_configuration'])('preserves blocker %s',blocker=>{expect(parseEdaJob({...wire('blocked'),blockers:[blocker]},binding).blockers).toEqual([blocker])})
it.each(['job','project','original','candidate','domain','extra','result-extra','settled','verdict','physical','blocker','missing-result'])('rejects detached or unsupported response: %s',field=>{
  const raw=wire('completed') as Record<string,unknown>
  if(field==='job')raw.job_id='other'
  if(field==='project')raw.project_id='other'
  if(field==='original')raw.original_version_id='other'
  if(field==='candidate')raw.candidate_version_id='other'
  if(field==='domain')raw.domain='vivado'
  if(field==='extra')raw.native_path='PRIVATE'
  if(field==='result-extra')Object.assign(raw.result!,{path:'PRIVATE'})
  if(field==='settled')Object.assign(raw.result!,{settled:false})
  if(field==='verdict')raw.design_verdict='pass_within_scope'
  if(field==='physical')raw.physical_electrical_verdict='pass'
  if(field==='blocker')raw.blockers=['unknown']
  if(field==='missing-result')raw.result=null
  expect(()=>parseEdaJob(raw,binding,'job')).toThrow()
})
it('binds only distinct same-project/domain v3 versions',()=>{
  expect(edaBinding(original,candidate)).toEqual(binding)
  for(const changed of [original,{...candidate,projectId:'other'},{...candidate,domains:['vivado']},{...candidate,schemaVersion:'engineering.admission.v1' as const}])expect(()=>edaBinding(original,changed)).toThrow()
})
it('preserves immutable results and prevents state/cancellation regression',()=>{
  const completed=parseEdaJob(wire('completed'),binding)
  expect(()=>compatibleEdaJob(completed,parseEdaJob(wire(),binding))).toThrow()
  const changed=wire('completed');changed.result!.native_result_sha256='b'.repeat(64)
  expect(()=>compatibleEdaJob(completed,parseEdaJob(changed,binding))).toThrow()
  const cancelled=parseEdaJob({...wire('cancelled'),cancel_requested:true,blockers:['unreviewed_mapping']},binding)
  expect(compatibleEdaJob(parseEdaJob(wire('blocked'),binding),cancelled)).toEqual(cancelled)
  expect(()=>compatibleEdaJob(cancelled,parseEdaJob(wire('cancelled'),binding))).toThrow()
  expect(()=>compatibleEdaJob(parseEdaJob(wire('blocked'),binding),parseEdaJob({...wire('blocked'),blockers:['unsupported_configuration']},binding))).toThrow()
})
it('bounds polling and stops terminal jobs',()=>{
  expect(edaPollInterval(parseEdaJob(wire(),binding),Date.now()+1000)).toBe(1500)
  expect(edaPollInterval(parseEdaJob(wire(),binding),Date.now()-1)).toBe(false)
  expect(edaPollInterval(parseEdaJob(wire('blocked'),binding),Date.now()+1000)).toBe(false)
})
it('fails closed on corrupt journal or changed original binding',()=>{
  sessionStorage.setItem('pending',JSON.stringify({binding,key:'key-1',jobId:null}))
  expect(readPendingEda('pending',original)?.key).toBe('key-1')
  expect(()=>readPendingEda('pending',{...original,id:'other'})).toThrow()
  sessionStorage.setItem('pending','broken');expect(()=>readPendingEda('pending',original)).toThrow()
})
it('posts only IDs and the preserved key to the actual /api route',async()=>{
  const fetch=vi.spyOn(globalThis,'fetch').mockResolvedValue(new Response(JSON.stringify(wire())))
  await edaRequest('/eda-repairs/kicad',v=>parseEdaJob(v,binding),{method:'POST',body:binding.request,key:'same-key'})
  expect(fetch.mock.calls[0][0]).toBe('/api/eda-repairs/kicad')
  expect(fetch.mock.calls[0][1]?.body).toBe(JSON.stringify(binding.request))
  expect(fetch.mock.calls[0][1]?.headers).toMatchObject({'Idempotency-Key':'same-key'})
})
it.each([404,409,503])('reports disabled/missing/conflicting API %s without raw server content',async status=>{
  vi.spyOn(globalThis,'fetch').mockResolvedValue(new Response('PRIVATE',{status}))
  await expect(edaRequest('/eda-repairs/kicad',v=>v,{method:'POST',body:binding.request,key:'key'})).rejects.toThrow('unavailable, disabled or conflicts')
})
it('rejects oversize or malformed responses and aborted reads',async()=>{
  const fetch=vi.spyOn(globalThis,'fetch').mockResolvedValue(new Response(' '.repeat(65537)))
  await expect(edaRequest('/eda-repairs/jobs/job',v=>v)).rejects.toThrow('invalid')
  fetch.mockResolvedValue(new Response('{'))
  await expect(edaRequest('/eda-repairs/jobs/job',v=>v)).rejects.toThrow('invalid')
  const controller=new AbortController();controller.abort();fetch.mockResolvedValue(new Response(JSON.stringify(wire())))
  await expect(edaRequest('/eda-repairs/jobs/job',v=>v,{signal:controller.signal})).rejects.toThrow()
})
