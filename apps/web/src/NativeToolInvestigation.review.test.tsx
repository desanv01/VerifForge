// Independent assertions against frozen native UI. Synthetic wire fixture builder
// reused from the owner test as data context; no owner tests are imported/run.
import { StrictMode } from 'react'
import { createHash, webcrypto } from 'node:crypto'
import { act, cleanup, render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { afterEach, expect, it, vi } from 'vitest'
import { EngineeringInvestigation } from './EngineeringInvestigation'
import { investigationGet, parseArtifacts, parseInvestigationCapabilities, parseInvestigations, parseNativeDetail, MAX_RESPONSE_BYTES } from './engineeringInvestigationApi'

const h = 'a'.repeat(64), badHash = 'b'.repeat(64), goodHash = 'c'.repeat(64)
const health = {status:'ok',investigation_api:'1',investigation_admission_schemas:['engineering.admission.v1','engineering.admission.v2'],investigation_report_kinds:['riscv.recorded-report.v1','firmware.recorded-report.v1']}
const json = (value:unknown,status=200)=>new Response(JSON.stringify(value),{status})
type Ref = {artifact_id:string;revision_id:string;attempt_id:string|null;sha256:string}
type Obj = Record<string, any> // Test wire builder deliberately permits corrupting arbitrary fields.
const nativeIds: Record<string,[string,string]> = {control:['attempt-ba01be2c331649828d66ffcca63365ba','rv32i-control-v1'],mutant:['attempt-3507c622a1f143d894f819a0958c0f6a','rv32i-functional-register-mutant-v1'],'control-replay':['attempt-e845135395204d61b526d707bae1ef2f','rv32i-control-v1']}
const fwFixtures:Record<string,string>={control:'fw-regmap-control-v1',bad:'fw-regmap-offset-bug-v1',candidate:'fw-regmap-offset-candidate-v1','bad-replay':'fw-regmap-offset-bug-v1','candidate-replay':'fw-regmap-offset-candidate-v1'}
const inputRoles:Record<string,string>={plan:'configuration',profile:'configuration',source_manifest:'source_snapshot',checker_manifest:'configuration',environment:'configuration',raw_rvfi:'native_trace',raw_spike:'reference_trace',normalized_pair:'native_trace',comparison:'native_check',object:'program',elf:'program',binary:'program',preparation:'configuration',observation:'native_trace',check:'native_check',register_contract:'configuration',selected_header:'source_snapshot',bad_base_header:'source_snapshot',tool_metadata:'configuration'}
function fixture(family:'riscv'|'firmware'='riscv', project='native-project', diffText='-#define STATUS 8\n+#define STATUS 4\n') {
  const artifacts:Obj[]=[], bindings:Obj[]=[], inventory:Obj[]=[], sources:Ref[]=[], facts:Obj[]=[], attachments:Obj[]=[]
  function add(name:string,role:string,attempt:string|null=null,sha256=h,size=1,canonical=false):Ref {
    const reference={artifact_id:name,revision_id:'revision-1',attempt_id:attempt,sha256}
    artifacts.push({reference,size_bytes:size,roles:[role],canonical_record_hash:canonical?sha256:null});return reference
  }
  const revision=add('project-revision','project_revision',null,h,1,true), manifest=add('manifest','manifest',null,h,1,true), reportRef=add('report','native_report',null,h,1,true), receipt=add('receipt','receipt')
  const origin:Obj={kind:'github_actions',repository:'desanv01/VerifForge',source_commit:family==='riscv'?'44b8b5e40ce7841f076e4178df13f38ecdbd05fd':'5565985bbe2bb2a0be869d366baa48c715057657',run_id:family==='riscv'?40920450439:40929665294,run_attempt:1,artifact_id:family==='riscv'?12919420477:12922737365,extracted_receipt_ref:receipt,transport_digest:null}
  function bind(reference:Ref,scope:Obj,role:string,path:string|null,size=1,media='application/json') {
    bindings.push({ref:reference,scope,provenance:path===null?'adapter_generated':'producer',original_relative_path:path,original_artifact_id:null,role,media_type:media,size_bytes:size})
    if(path!==null)inventory.push({original_relative_path:path,sha256:reference.sha256,size_bytes:size})
  }
  bind(manifest,origin,'manifest',null);bind(receipt,origin,'receipt','receipt.json')
  function input(name:string,loc:Obj,role:string,sha256=h):Ref {
    const hosted=loc.kind==='github_actions', label=hosted?'shared':loc.case_label, id=`${label}-${name}`, old=bindings.find(b=>b.ref.artifact_id===id)
    if(old)return old.ref
    const reference=add(id,role,loc.kind==='producer_attempt'?loc.native_attempt_id:null,sha256)
    bind(reference,loc,role,hosted?`${name}.json`:`${label}/${name}.json`);return reference
  }
  const labels=family==='riscv'?['control','mutant','control-replay']:['control','bad','candidate','bad-replay','candidate-replay']
  const cases:Obj[]=labels.map(label=>{
    const raw=add(`${label}-result`,family==='riscv'?'native_result':'native_execution',family==='riscv'?nativeIds[label][0]:null)
    const loc:Obj=family==='riscv'?{kind:'producer_attempt',origin,case_label:label,native_attempt_id:nativeIds[label][0],wire_experiment_id:nativeIds[label][1],raw_result_ref:raw}:{kind:'producer_case',origin,case_label:label,fixture_id:fwFixtures[label],execution_ref:raw}
    bind(raw,loc,family==='riscv'?'native_result':'native_execution',`${label}/result.json`)
    const bad=label==='mutant'||label==='bad'||label==='bad-replay', replay=label.endsWith('-replay')
    const identity={source_hash:h,checker_hash:h,contract_hash:h,experiment_hash:h,environment_hash:h}, checker={checker_id:'synthetic-checker',version:'1',sha256:h}
    let item:Obj
    if(family==='riscv') {
      const names=['plan','profile','source_manifest','checker_manifest','environment','raw_rvfi','raw_spike','normalized_pair','comparison','object','elf','binary']
      item={schema_version:'riscv.recorded-attempt.v1',locator:loc,inputs:Object.fromEntries(names.map(name=>[name,input(name,loc,inputRoles[name])])),identity,checker,configuration_hash:h,producer_execution_status:'completed',termination:'completion_store_retired',checker_evidence_status:'valid',verdict:bad?'design_mismatch':'pass_within_scope',cycles:20,cycle_limit:100,retirement_limit:32,retired_count:10,reference_count:10,compared_count:bad?3:10,first_divergence:bad?{order:2,cycle:8,requirement:'RISCV_ARCH_RETIREMENT',field:'rd_value',expected:17,observed:18}:null,producer_phases:['assembler','compiler','objcopy','dut','reference']}
    } else {
      const names=['preparation','observation','check','register_contract','selected_header','bad_base_header','source_manifest','tool_metadata']
      const inputs:Obj=Object.fromEntries(names.map(name=>[name,input(name,['source_manifest','tool_metadata'].includes(name)?origin:loc,inputRoles[name],name==='selected_header'?(bad?badHash:goodHash):name==='bad_base_header'?badHash:h)]))
      inputs.program=Object.fromEntries(['start_object','probe_object','main_object','elf','binary','map','disassembly','elf_attributes'].map(name=>[name,input(`program-${name}`,loc,'program')]))
      inputs.sources=Object.fromEntries(['main','start','probe','linker_script','wrapper','core_wrapper','core','peripheral'].map(name=>[name,input(`source-${name}`,loc,'source_snapshot')]))
      item={schema_version:'firmware.recorded-case.v1',locator:loc,inputs,identity,checker,configuration_hash:h,profile_hash:h,semantic_hash:bad?badHash:goodHash,producer_completed:true,termination:'completion_store_retired',checker_evidence_status:'completed',verdict:bad?'design_mismatch':'pass_within_scope',cycles:20,cycle_limit:100,retired_count:10,retirement_limit:32,transfer_count:20,mmio_count:bad?4:9,first_conflict:bad?{mmio_index:2,bus_sequence:3,cycle:8,requirement:'FIRMWARE_STATUS_ADDRESS',operation:'write',expected_address:0x40000004,observed_address:0x40000008,write_strobes:15,active_lane_value:0x55667788,expected_probe:0x55667788,observed_probe:0}:null,mailbox:{observed:true,address:0x8000ff00,value:bad?0:1,cycle:19},producer_phases:replay?['simulator']:['assemble_start','assemble_probe','compile_main','compiler','binary','disassembly','elf_headers','simulator'],replay_of_execution_ref:null,reference_support:'independent_literal_mmio_checker_spike_unsupported'}
    }
    const primary:Ref=family==='riscv'?raw:item.inputs.check, row=artifacts.find(a=>a.reference.artifact_id===primary.artifact_id)!
    row.roles.push('project_source','provenance_source');sources.push(primary)
    const reference=add(`${label}-case`,'native_case',family==='riscv'?nativeIds[label][0]:null,h,1,true), factId=`fact-${label}`, schema=family==='riscv'?'riscv.recorded-attempt.v1':'firmware.recorded-case.v1'
    attachments.push({fact_id:factId,domain:family,native_schema_id:schema,reference})
    facts.push({fact_id:factId,domain:family,native_schema_id:schema,native_record_hash:h,native_record:reference,provenance:{state:'complete',sources:[{artifact:primary,location:{kind:'json_pointer',pointer:''}}],extraction:{method:'native_parser',extractor_id:'veriforge.native-tool-admission',extractor_version:'1',implementation_hash:h,tool:null},derived_from:[],uncertainties:[]},category:'observation',review_state:'reviewed',conditions:[]})
    return item
  })
  const report:Obj={schema_version:`${family}.recorded-report.v1`,family:family==='riscv'?'riscv_retirement':'firmware_mmio',operation:'admission_only',new_execution:'not_run',manifest_ref:manifest,selection:{origin,inventory,selected:bindings,omitted_paths:[]},cases}
  if(family==='riscv') report.recompiled_control={kind:'recompiled_control',original:cases[0].locator,repeated:cases[2].locator,record_ref:input('recompiled',origin,'replay_record'),outcome:'byte_identical_recompiled_control'}
  else {
    const bytes=new TextEncoder().encode(diffText), hash=createHash('sha256').update(bytes).digest('hex'), diff=add('candidate-diff','candidate_diff',null,hash,bytes.length)
    bind(diff,cases[2].locator,'candidate_diff','candidate/header.diff',bytes.length,'text/x-diff')
    report.candidate={kind:'curated_header_correction',bad:cases[1].locator,candidate:cases[2].locator,diff_ref:diff,original_bad_header_ref:cases[1].inputs.selected_header,candidate_header_ref:cases[2].inputs.selected_header,protected_checker_hash:h,protected_contract_hash:h,protected_profile_hash:h,covered_mmio_indices:[0,1,2,3,4,5,6,7,8],outcome:'scoped_curated_correction'}
    report.replays=[['bad',1,3],['candidate',2,4]].map(([name,a,b])=>{
      const original=cases[a as number],repeated=cases[b as number];repeated.replay_of_execution_ref=original.locator.execution_ref
      return {kind:'saved_program_replay',original:original.locator,repeated:repeated.locator,record_ref:input(`replay-${name}`,origin,'replay_record'),outcome:'semantic_saved_program_replay'}
    })
  }
  const summary={id:`version-${family}`,investigation_id:`investigation-${family}`,project_id:project,ordinal:1,content_hash:h,revision_id:'revision-1',revision_hash:h,manifest_hash:h,schema_version:'engineering.admission.v2',scope:'recorded_import',execution_status:'not_run',created_at:'2026-10-30T00:00:00Z',domains:[family],native_investigation_id:`native-${family}`,report_kind:report.schema_version}
  const detail:Obj={...summary,envelope:{schema_version:'engineering.admission.v2',operation:'admission_only',new_execution:'not_run',claim_scope:'resolved_bytes_and_registered_native_checks_only',external_context_scope:'host_pinned_external_locator_not_local_execution_origin',validation:{validator_id:'veriforge.native-tool-admission',version:'1',implementation_hash:h,implementation_hash_provenance:'host_pinned_installed_source_hash',scope:'exact_bytes_and_registered_pure_checkers_not_remote_attestation'},record:{schema_version:'engineering.investigation.v2',investigation_id:summary.native_investigation_id,project_id:project,revision_id:'revision-1',project_revision_ref:revision,manifest_ref:manifest,report_kind:report.schema_version,report_ref:reportRef,native_records:attachments},record_hash:h,artifacts,total_artifact_bytes:artifacts.reduce((sum,a)=>sum+a.size_bytes,0)},project_revision:{schema_version:'engineering.project-revision.v1',project_id:project,revision_id:'revision-1',source_refs:sources,configuration_hash:h},manifest:{schema_version:'engineering.manifest.v1',manifest_id:'manifest-1',revision_id:'revision-1',review_state:'reviewed',reviewed_by:'synthetic-reviewer',adapters:[],facts,relations:[]},native_report:report}
  const item={id:summary.investigation_id,project_id:project,created_at:summary.created_at,latest_version:summary}, downloads={items:artifacts.map((a,i)=>({id:`blob-${i}`,...a.reference,size_bytes:a.size_bytes,roles:a.roles,media_type:'application/octet-stream'}))}
  const selected=parseInvestigations({items:[item],next_cursor:null},project,'all').items[0].latest
  return {project,summary,selected,detail,item,downloads,diffText}
}

const pending = <T,>() => { let resolve!: (value:T)=>void; const promise=new Promise<T>(done=>{resolve=done});return {promise,resolve} }
function transport(f=fixture(), custom?:(url:string,init?:RequestInit)=>unknown|Promise<unknown>) {
  return vi.spyOn(globalThis,'fetch').mockImplementation(async(input,init)=>{
    const url=String(input), value=await custom?.(url,init)
    if(value instanceof Response)return value
    if(value!==undefined)return json(value)
    if(url==='/api/health')return json(health)
    if(url.includes('/investigations?'))return json({items:url.includes('admission_schema=all')?[f.item]:[],next_cursor:null})
    if(url.includes('/versions?'))return json({items:[f.summary],next_cursor:null})
    if(url.endsWith('/artifacts'))return json(f.downloads)
    if(url.endsWith('/download'))return new Response(f.diffText)
    if(url.endsWith(f.summary.id))return json(f.detail)
    throw new Error(`Unmocked independent review URL: ${url}`)
  })
}
const cache=()=>new QueryClient({defaultOptions:{queries:{retry:false,gcTime:0}}})
function mount(project='native-project',client=cache()) {
  const tree=(id:string)=><StrictMode><QueryClientProvider client={client}><EngineeringInvestigation projectId={id}/></QueryClientProvider></StrictMode>
  const view=render(tree(project));return {...view,client,change:(id:string)=>view.rerender(tree(id))}
}
const nativeRegion=()=>screen.findByRole('region',{name:'Recorded native evidence'})
const downloads=(calls:ReturnType<typeof transport>)=>calls.mock.calls.filter(([url])=>String(url).endsWith('/download'))
afterEach(()=>{cleanup();vi.restoreAllMocks();vi.unstubAllGlobals()})

it('accepts reordered exact closed capability sets but keeps absent markers legacy-only',()=>{
  expect(parseInvestigationCapabilities({...health,investigation_admission_schemas:[...health.investigation_admission_schemas].reverse(),investigation_report_kinds:[...health.investigation_report_kinds].reverse()})).toEqual({mode:'all'})
  expect(parseInvestigationCapabilities({status:'ok',investigation_api:'1'})).toEqual({mode:'v1'})
})
it.each([
  {...health,investigation_admission_schemas:null}, {...health,investigation_report_kinds:undefined},
  {...health,investigation_admission_schemas:['engineering.admission.v1','engineering.admission.v1']},
  {...health,investigation_report_kinds:['firmware.recorded-report.v1']},
  {...health,investigation_admission_schemas:[...health.investigation_admission_schemas,'engineering.admission.v3']},
  {...health,investigation_api:1}, {...health,status:'ready'},
])('fails closed for an incomplete, unknown, or non-string capability set %#',body=>{
  expect(()=>parseInvestigationCapabilities(body)).toThrow()
})
it('does not request all-mode discovery while a new mount awaits its own health',async()=>{
  // Each StrictMode request needs its own consumable Response body.
  const waiting=pending<typeof health>(), f=fixture(), calls=transport(f,url=>url==='/api/health'?waiting.promise:undefined)
  const view=mount();await waitFor(()=>expect(calls.mock.calls.some(([url])=>url==='/api/health')).toBe(true))
  expect(calls.mock.calls.some(([url])=>String(url).includes('admission_schema=all'))).toBe(false)
  await act(async()=>{waiting.resolve(health);await waiting.promise})
  await nativeRegion();view.unmount()
  const again=pending<unknown>()
  calls.mockImplementation(async input=>String(input)==='/api/health'?json(await again.promise):json({items:[],next_cursor:null}))
  const before=calls.mock.calls.length;mount(f.project,view.client)
  await waitFor(()=>expect(calls.mock.calls.slice(before).some(([url])=>url==='/api/health')).toBe(true))
  expect(screen.queryByRole('region',{name:'Recorded native evidence'})).not.toBeInTheDocument()
  expect(calls.mock.calls.slice(before).some(([url])=>String(url).includes('admission_schema=all'))).toBe(false)
  await act(async()=>{again.resolve({status:'ok',investigation_api:'1'});await again.promise})
})
it('withholds native links immediately during health revalidation and discards a revoked capability',async()=>{
  const waiting=pending<Response>();let refresh=false
  transport(fixture(),url=>url==='/api/health'&&refresh?waiting.promise:undefined)
  mount();await nativeRegion();refresh=true
  await userEvent.click(screen.getByRole('button',{name:'Check native view availability'}))
  expect(screen.queryByRole('region',{name:'Recorded native evidence'})).not.toBeInTheDocument()
  expect(document.querySelector('a[download]')).toBeNull()
  await act(async()=>{waiting.resolve(json({status:'ok',investigation_api:'1'}));await waiting.promise})
  await screen.findByText('No investigations on this page')
  expect(screen.queryByText('RISC-V architectural investigation')).not.toBeInTheDocument()
})
it('does not issue artifact continuation after an old detail read resolves on a new project',async()=>{
  const f=fixture(), waiting=pending<Response>()
  const calls=transport(f,url=>url.endsWith(f.summary.id)?waiting.promise:url.includes('/projects/new-project/')?{items:[],next_cursor:null}:undefined)
  const view=mount();await waitFor(()=>expect(calls.mock.calls.some(([url])=>String(url).endsWith(f.summary.id))).toBe(true))
  view.change('new-project');await screen.findByText('No investigations on this page')
  await act(async()=>{waiting.resolve(json(f.detail));await waiting.promise})
  expect(calls.mock.calls.some(([url])=>String(url).endsWith('/artifacts'))).toBe(false)
  expect(screen.queryByRole('region',{name:'Recorded native evidence'})).not.toBeInTheDocument()
})
it('uses GETs only and keeps producer identifiers out of local campaign routes',async()=>{
  const calls=transport(fixture('firmware'));mount();await nativeRegion()
  await userEvent.selectOptions(screen.getByLabelText('Recorded native case'),'bad')
  await userEvent.selectOptions(screen.getByLabelText('Recorded native case'),'candidate-replay')
  expect(screen.getByText(/not local campaign or attempt associations/)).toBeInTheDocument()
  expect(screen.getByText(/Model-generated repair provenance is not established/)).toBeInTheDocument()
  expect(screen.getByText(/simulator-only phases/)).toBeInTheDocument()
  expect(calls.mock.calls.every(([,init])=>init?.method==='GET')).toBe(true)
  expect([...document.querySelectorAll('a')].every(a=>!a.href.includes('/campaigns/'))).toBe(true)
  expect(downloads(calls)).toHaveLength(0)
})

const malformed:[string,unknown][]=[
  ['envelope.new_execution','completed'], ['envelope.external_context_scope','local_execution_origin'],
  ['envelope.validation.implementation_hash_provenance','remote_attestation'],
  ['envelope.record.report_kind','unknown'], ['envelope.record.project_id','foreign'],
  ['project_revision.configuration_hash',badHash], ['manifest.adapters',[{adapter_id:'invented'}]],
  ['native_report.selection.origin.artifact_id',1], ['native_report.selection.origin.source_commit','0'.repeat(40)],
  ['native_report.selection.origin.run_attempt','1'], ['native_report.selection.origin.transport_digest',{sha256:h,verification:'remote_attested'}],
  ['native_report.selection.inventory.0.size_bytes','1'], ['native_report.selection.inventory.0.original_relative_path','../outside'],
  ['native_report.selection.selected.0.ref.sha256',badHash], ['native_report.selection.selected.0.ref.attempt_id','local-attempt'],
  ['native_report.selection.selected.0.media_type','text/html'], ['native_report.selection.selected.0.role','executable'],
  ['native_report.selection.selected.0.extra',true], ['envelope.total_artifact_bytes','1'],
  ['manifest.facts.0.provenance.sources.0.location',{kind:'json_pointer',pointer:'/bad~9escape'}],
  ['manifest.facts.0.provenance.sources.0.location',{kind:'lines',start:3,end:1}],
  ['manifest.facts.0.provenance.extraction.tool',{name:'tool',version:1}],
  ['manifest.facts.0.provenance.derived_from',['missing-fact']],
]
function change(object:Obj,path:string,value:unknown) { const keys=path.split('.'),last=keys.pop()!;let target=object;for(const key of keys)target=target[key];target[last]=value }
it.each(malformed)('rejects malformed actual-v2 metadata at %s',(path,value)=>{
  const f=fixture();change(f.detail,path,value);expect(()=>parseNativeDetail(f.detail,f.selected)).toThrow()
})
it.each([
  ['native_report.cases.1.first_divergence.order','2'], ['native_report.cases.1.first_divergence.cycle',21],
  ['native_report.cases.1.first_divergence.expected',18], ['native_report.cases.0.producer_execution_status','not_run'],
  ['native_report.cases.0.checker_evidence_status','unsupported'], ['native_report.cases.0.reference_count',9],
  ['native_report.recompiled_control.outcome','saved_program_replay'],
] as [string,unknown][])('rejects unsupported RISC-V interpretation at %s',(path,value)=>{
  const f=fixture();change(f.detail,path,value);expect(()=>parseNativeDetail(f.detail,f.selected)).toThrow()
})
it.each([
  ['native_report.cases.1.first_conflict.mmio_index','2'], ['native_report.cases.1.first_conflict.bus_sequence',0],
  ['native_report.cases.2.mailbox.value',0], ['native_report.cases.2.reference_support','spike'],
  ['native_report.candidate.protected_profile_hash',badHash], ['native_report.candidate.original_bad_header_ref.sha256',goodHash],
  ['native_report.replays.1.outcome','byte_identical_recompiled_control'],
] as [string,unknown][])('rejects unsupported firmware interpretation at %s',(path,value)=>{
  const f=fixture('firmware');change(f.detail,path,value);expect(()=>parseNativeDetail(f.detail,f.selected)).toThrow()
})
it.each(['riscv','firmware'] as const)('binds exact %s detail, artifacts and selected identity',family=>{
  const f=fixture(family),parsed=parseNativeDetail(f.detail,f.selected)
  expect(parsed.family).toBe(family);expect(parsed.origin.runId).toBe(family==='riscv'?'40920450439':'40929665294')
  expect(parsed.cases).toHaveLength(family==='riscv'?3:5)
  expect(()=>parseNativeDetail(f.detail,{...f.selected,contentHash:badHash})).toThrow()
  f.downloads.items[0].sha256=badHash
  expect(()=>parseArtifacts(f.downloads,parsed)).toThrow()
})
it.each([
  '{"outer":[{"flag":false,"\\u0066lag":true}]}',
  '{"id":"a","i\\u0064":"b"}', '{"__proto__":1,"__proto__":2}',
])('rejects decoded duplicate keys before invoking the projection: %s',async source=>{
  vi.spyOn(globalThis,'fetch').mockResolvedValue(new Response(source));const project=vi.fn((v:unknown)=>v)
  await expect(investigationGet('/health',project)).rejects.toThrow();expect(project).not.toHaveBeenCalled()
})
it('permits repeated names in distinct objects and preserves exact decimal strings',async()=>{
  vi.spyOn(globalThis,'fetch').mockResolvedValue(new Response('{"rows":[{"id":1},{"id":2}],"cost":"0.00000000000000000000123"}'))
  expect(await investigationGet('/read',v=>v)).toEqual({rows:[{id:1},{id:2}],cost:'0.00000000000000000000123'})
})
it('cancels an oversized chunked response before interpreting metadata',async()=>{
  const cancel=vi.fn(), project=vi.fn((v:unknown)=>v)
  vi.spyOn(globalThis,'fetch').mockResolvedValue(new Response(new ReadableStream({start(controller){controller.enqueue(new Uint8Array(MAX_RESPONSE_BYTES));controller.enqueue(new Uint8Array(1))},cancel})))
  await expect(investigationGet('/read',project)).rejects.toThrow();expect(project).not.toHaveBeenCalled();expect(cancel).toHaveBeenCalledOnce()
})
it.each([65,200])('rejects JSON nesting depth %s before projection',async depth=>{
  vi.spyOn(globalThis,'fetch').mockResolvedValue(new Response('['.repeat(depth)+'0'+']'.repeat(depth)))
  const project=vi.fn((v:unknown)=>v)
  await expect(investigationGet('/read',project)).rejects.toThrow();expect(project).not.toHaveBeenCalled()
})

it('previews escaped text only on click using an immutable blob route',async()=>{
  vi.stubGlobal('crypto',webcrypto)
  const text='<img src=x onerror="window.REVIEW=true">\n+STATUS=4',f=fixture('firmware','native-project',text),calls=transport(f)
  mount();await nativeRegion();expect(downloads(calls)).toHaveLength(0)
  await userEvent.click(screen.getByRole('button',{name:'Preview recorded header diff'}))
  await waitFor(()=>expect(document.querySelector('pre')).toHaveTextContent('<img src=x'))
  expect(document.querySelector('img')).toBeNull();expect((window as Obj).REVIEW).toBeUndefined()
  const [url,init]=downloads(calls)[0];expect(String(url)).toMatch(/^\/api\/investigations\/investigation-firmware\/versions\/version-firmware\/artifacts\/blob-\d+\/download$/)
  expect(init?.method).toBe('GET')
})
it('does not display old diff bytes after a project change even if cancellation is ignored',async()=>{
  vi.stubGlobal('crypto',webcrypto)
  const f=fixture('firmware'),waiting=pending<Response>()
  transport(f,url=>url.endsWith('/download')?waiting.promise:url.includes('/projects/new-project/')?{items:[],next_cursor:null}:undefined)
  const view=mount();await nativeRegion();await userEvent.click(screen.getByRole('button',{name:'Preview recorded header diff'}))
  view.change('new-project');await screen.findByText('No investigations on this page')
  await act(async()=>{waiting.resolve(new Response(f.diffText));await waiting.promise})
  expect(document.querySelector('pre')).toBeNull();expect(screen.queryByText('Curated header correction')).not.toBeInTheDocument()
})
it.each(['size','hash','utf8','overflow'] as const)('withholds diff text on %s validation failure',async corruption=>{
  vi.stubGlobal('crypto',webcrypto)
  const f=fixture('firmware')
  let bytes=corruption==='size'?new TextEncoder().encode(f.diffText+'x'):corruption==='hash'?new TextEncoder().encode('x'.repeat(new TextEncoder().encode(f.diffText).length)):new Uint8Array([0xff])
  if(corruption==='utf8'){
    const digest=createHash('sha256').update(bytes).digest('hex'),old=f.detail.native_report.candidate.diff_ref
    old.sha256=digest
    const artifact=f.detail.envelope.artifacts.find((a:Obj)=>a.reference.artifact_id==='candidate-diff');f.detail.envelope.total_artifact_bytes+=bytes.length-artifact.size_bytes;artifact.size_bytes=bytes.length
    f.detail.native_report.selection.selected.find((b:Obj)=>b.ref.artifact_id==='candidate-diff').size_bytes=bytes.length
    const inventory=f.detail.native_report.selection.inventory.find((a:Obj)=>a.original_relative_path==='candidate/header.diff');inventory.size_bytes=bytes.length;inventory.sha256=digest
    const download=f.downloads.items.find(a=>a.artifact_id==='candidate-diff')!;download.size_bytes=bytes.length;download.sha256=digest
  }
  const cancel=vi.fn()
  transport(f,url=>url.endsWith('/download')?corruption==='overflow'?new Response(new ReadableStream({start(c){c.enqueue(new Uint8Array(65536));c.enqueue(new Uint8Array(1))},cancel})):new Response(bytes):undefined)
  mount();await nativeRegion();await userEvent.click(screen.getByRole('button',{name:'Preview recorded header diff'}))
  await screen.findByRole('alert');expect(document.querySelector('pre')).toBeNull()
  if(corruption==='overflow')expect(cancel).toHaveBeenCalledOnce()
  expect(document.querySelector('a[download]')).not.toBeNull()
})
