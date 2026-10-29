// Synthetic wire fixtures only. No admission, database, native tool or provider runs.
import { StrictMode } from 'react'
import { createHash, webcrypto } from 'node:crypto'
import { act, cleanup, render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { afterEach, expect, it, vi } from 'vitest'
import { EngineeringInvestigation } from './EngineeringInvestigation'
import { investigationGet, parseArtifacts, parseInvestigationCapabilities, parseInvestigations, parseNativeDetail, parseVersions, MAX_RESPONSE_BYTES } from './engineeringInvestigationApi'

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
function mock(f=fixture(), custom?:(url:string,init?:RequestInit)=>unknown|Promise<unknown>) {
  return vi.spyOn(globalThis,'fetch').mockImplementation(async(input,init)=>{
    const url=String(input), value=await custom?.(url,init);if(value instanceof Response)return value;if(value!==undefined)return json(value)
    if(url==='/api/health')return json(health)
    if(url.includes('/investigations?'))return json({items:url.includes('admission_schema=all')?[f.item]:[],next_cursor:null})
    if(url.includes('/versions?'))return json({items:[f.summary],next_cursor:null})
    if(url.endsWith('/artifacts'))return json(f.downloads)
    if(url.endsWith('/download'))return new Response(f.diffText)
    if(url.endsWith(f.summary.id))return json(f.detail)
    return json({},404)
  })
}
function mount(project='native-project',client=new QueryClient({defaultOptions:{queries:{retry:false,gcTime:0}}})) {
  const tree=(projectId:string)=><StrictMode><QueryClientProvider client={client}><EngineeringInvestigation projectId={projectId}/></QueryClientProvider></StrictMode>
  const view=render(tree(project));return {...view,client,change:(id:string)=>view.rerender(tree(id))}
}
afterEach(()=>{cleanup();vi.restoreAllMocks();vi.unstubAllGlobals()})

it.each(['riscv','firmware'] as const)('parses complete %s detail and exact artifact closure',family=>{
  const f=fixture(family), detail=parseNativeDetail(f.detail,f.selected), artifacts=parseArtifacts(f.downloads,detail)
  expect(detail.family).toBe(family);expect(detail.cases).toHaveLength(family==='riscv'?3:5);expect(artifacts).toHaveLength(f.downloads.items.length)
  expect(parseVersions({items:[f.summary],next_cursor:null},f.project,f.summary.investigation_id,'all').items[0]).toEqual(f.selected)
  expect(()=>parseInvestigations({items:[f.item],next_cursor:null},f.project)).toThrow()
})
it('shows RISC-V first divergence and recompiled control without producer campaign links',async()=>{
  const f=fixture(),calls=mock(f);mount();await screen.findByRole('region',{name:'Recorded native evidence'})
  await userEvent.selectOptions(screen.getByLabelText('Recorded native case'),'mutant')
  expect(screen.getByRole('table',{name:'First architectural divergence'})).toHaveTextContent('0x00000011')
  expect(screen.getByText('Recompiled control comparison')).toBeInTheDocument();expect(screen.getByText(/PicoRV32 RV32I/)).toBeInTheDocument()
  expect(screen.getByText(/Not established by this record/)).toBeInTheDocument()
  expect([...document.querySelectorAll('a')].every(a=>!a.href.includes('campaign'))).toBe(true)
  expect(calls.mock.calls.every(([,init])=>init?.method==='GET')).toBe(true)
  expect(calls.mock.calls.some(([url])=>String(url).includes('admission_schema=all'))).toBe(true)
})
it('shows firmware MMIO, protected curated correction and saved-program replay distinctly',async()=>{
  mock(fixture('firmware'));mount();await screen.findByText('Curated header correction')
  await userEvent.selectOptions(screen.getByLabelText('Recorded native case'),'bad')
  expect(screen.getByRole('table',{name:'First MMIO conflict'})).toHaveTextContent('0x40000004')
  expect(screen.getByText(/Spike is unsupported/)).toBeInTheDocument();expect(screen.getByText('Saved-program replay')).toBeInTheDocument()
  await userEvent.selectOptions(screen.getByLabelText('Recorded native case'),'bad-replay')
  expect(screen.getByText('Recorded producer phases: simulator.')).toBeInTheDocument()
  expect(screen.queryByText('Producer native attempt')).not.toBeInTheDocument()
})
it.each([
  {status:'ok',investigation_api:'1'}, {...health,investigation_admission_schemas:['engineering.admission.v2']},
  {...health,investigation_report_kinds:['riscv.recorded-report.v1','unknown']}, {...health,investigation_report_kinds:['riscv.recorded-report.v1','riscv.recorded-report.v1']},
])('keeps v1 default discovery without complete closed capabilities %#',async value=>{
  const calls=mock(fixture(),url=>url==='/api/health'?value:undefined),view=mount()
  await waitFor(()=>expect(view.client.isFetching()).toBe(0))
  expect(calls.mock.calls.some(([url])=>String(url).includes('admission_schema=all'))).toBe(false)
  expect(screen.queryByRole('region',{name:'Recorded native evidence'})).not.toBeInTheDocument()
})
it('waits for fresh health and discards cached native capability after a new mount',async()=>{
  let resolve!:(r:Response)=>void
  const calls=mock(fixture(),url=>url==='/api/health'?new Promise<Response>(done=>{resolve=done}):undefined)
  const view=mount();await waitFor(()=>expect(resolve).toBeTypeOf('function'))
  expect(calls.mock.calls.some(([url])=>String(url).includes('admission_schema=all'))).toBe(false)
  await act(async()=>resolve(json(health)));await screen.findByText('RISC-V architectural investigation')
  view.unmount();mount('native-project',view.client)
  expect(screen.queryByText('RISC-V architectural investigation')).not.toBeInTheDocument()
})
it('revokes native detail and downloads on a failed health refresh',async()=>{
  let fail=false;mock(fixture(),url=>url==='/api/health'&&fail?json({},503):undefined);mount()
  await screen.findByText('RISC-V architectural investigation');fail=true
  await userEvent.click(screen.getByRole('button',{name:'Check native view availability'}))
  await waitFor(()=>expect(screen.queryByText('RISC-V architectural investigation')).not.toBeInTheDocument())
  expect(document.querySelectorAll('a[download]')).toHaveLength(0)
})
it('hides stale native records on artifact409 and retries explicitly',async()=>{
  let fail=false;mock(fixture(),url=>url.endsWith('/artifacts')&&fail?json({},409):undefined);mount()
  await screen.findByText('RISC-V architectural investigation');fail=true
  await userEvent.click(screen.getByRole('button',{name:'Refresh investigations'}));await screen.findByRole('alert')
  expect(screen.queryByText('RISC-V architectural investigation')).not.toBeInTheDocument()
  expect(document.querySelectorAll('a[download]')).toHaveLength(0)
  fail=false;await userEvent.click(screen.getByRole('button',{name:'Retry investigation request'}));await screen.findByText('RISC-V architectural investigation')
})
it('rejects contradictory discovery cursors without fetching another page',async()=>{
  const f=fixture(),calls=mock(f,url=>url.includes('/investigations?')&&url.includes('all')?{items:[f.item],next_cursor:f.item.id}:undefined)
  mount();await screen.findByRole('alert')
  expect(screen.queryByText('RISC-V architectural investigation')).not.toBeInTheDocument()
  expect(calls.mock.calls.some(([url])=>String(url).includes(`after=${f.item.id}`))).toBe(false)
})
it('rejects mixed native history rather than swapping the selected family',async()=>{
  const f=fixture(),foreign=fixture('firmware')
  mock(f,url=>url.includes('/versions?')?{items:[{...foreign.summary,investigation_id:f.summary.investigation_id}],next_cursor:null}:undefined)
  mount();await screen.findByRole('alert');expect(screen.queryByRole('region',{name:'Recorded native evidence'})).not.toBeInTheDocument()
})
it('preserves the explicitly selected immutable version during refresh',async()=>{
  const f=fixture(),latest={...f.summary,id:'version-riscv-2',ordinal:2,content_hash:badHash},detail={...f.detail,...latest}
  mock(f,url=>url.includes('/investigations?')&&url.includes('all')?{items:[{...f.item,latest_version:latest}],next_cursor:null}:url.includes('/versions?')?{items:[f.summary,latest],next_cursor:null}:url.endsWith(latest.id)?detail:undefined)
  mount();await screen.findByText('Immutable native version 2')
  await userEvent.selectOptions(screen.getByLabelText('Investigation version'),f.summary.id)
  await screen.findByText('Immutable native version 1')
  await userEvent.click(screen.getByRole('button',{name:'Refresh investigations'}));await screen.findByText('Immutable native version 1')
  expect(screen.getByLabelText('Investigation version')).toHaveValue(f.summary.id)
})
it('discards a late native detail GET after a project switch even if fetch ignores abort',async()=>{
  const first=fixture(), second=fixture('firmware','second-project');let resolve!:(r:Response)=>void
  mock(first,url=>url.endsWith(first.summary.id)?new Promise<Response>(done=>{resolve=done}):url.includes('second-project/investigations?')?{items:url.includes('all')?[second.item]:[],next_cursor:null}:url.includes(second.summary.investigation_id)?url.includes('/versions?')?{items:[second.summary],next_cursor:null}:url.endsWith('/artifacts')?second.downloads:second.detail:undefined)
  const view=mount();await waitFor(()=>expect(resolve).toBeTypeOf('function'));view.change('second-project')
  await screen.findByText('Firmware MMIO investigation');await act(async()=>resolve(json(first.detail)))
  expect(screen.queryByText('RISC-V architectural investigation')).not.toBeInTheDocument()
})
it.each([
  (d:Obj)=>d.project_id='foreign', (d:Obj)=>d.envelope.validation.scope='trusted',
  (d:Obj)=>d.execution_contexts=[], (d:Obj)=>d.native_report.cases[0].locator.native_attempt_id='local-attempt',
  (d:Obj)=>d.native_report.cases[0].identity.checker_hash=badHash,
  (d:Obj)=>d.native_report.cases[1].compared_count=9,
  (d:Obj)=>d.native_report.cases[1].first_divergence.expected=18,
  (d:Obj)=>d.native_report.selection.selected[2].size_bytes=100,
  (d:Obj)=>d.native_report.selection.omitted_paths=['control/result.json'],
  (d:Obj)=>d.envelope.artifacts[0].canonical_record_hash=badHash,
  (d:Obj)=>d.manifest.facts[0].provenance.extraction.implementation_hash=badHash,
  (d:Obj)=>d.native_report.selection.origin.run_id='40920450439',
  (d:Obj)=>d.native_report.selection.inventory[0].size_bytes='9007199254740993',
])('rejects inconsistent native identity, closure or RISC-V semantics %#',mutate=>{
  const f=fixture();mutate(f.detail);expect(()=>parseNativeDetail(f.detail,f.selected)).toThrow()
})
it.each([
  (d:Obj)=>d.native_report.candidate.protected_checker_hash=badHash,
  (d:Obj)=>d.native_report.candidate.covered_mmio_indices=[0,1,2],
  (d:Obj)=>d.native_report.cases[1].first_conflict.write_strobes=0,
  (d:Obj)=>d.native_report.cases[2].mailbox.observed=false,
  (d:Obj)=>d.native_report.cases[3].producer_phases=['compiler','simulator'],
  (d:Obj)=>d.native_report.cases[4].semantic_hash=badHash,
  (d:Obj)=>d.native_report.replays[0].original=d.native_report.cases[0].locator,
])('rejects contradictory firmware candidate/replay metadata %#',mutate=>{
  const f=fixture('firmware');mutate(f.detail);expect(()=>parseNativeDetail(f.detail,f.selected)).toThrow()
})
it.each(['{"investigation_api":"0","investigation_api":"1"}','{"nested":{"id":"a","\\u0069d":"b"}}'])('rejects duplicate keys before interpreting any capability or identity %s',async body=>{
  vi.spyOn(globalThis,'fetch').mockResolvedValue(new Response(body));const parse=vi.fn((v:unknown)=>v)
  await expect(investigationGet('/health',parse)).rejects.toThrow();expect(parse).not.toHaveBeenCalled()
})
it('rejects oversized or deeply nested responses without interpreting data',async()=>{
  const calls=vi.spyOn(globalThis,'fetch').mockResolvedValue(new Response(' '.repeat(MAX_RESPONSE_BYTES+1)))
  await expect(investigationGet('/health',parseInvestigationCapabilities)).rejects.toThrow()
  calls.mockResolvedValue(new Response('['.repeat(66)+'0'+']'.repeat(66)))
  await expect(investigationGet('/health',v=>v)).rejects.toThrow()
})
it('rejects an aborted request even when the transport resolves it late',async()=>{
  let resolve!:(r:Response)=>void;vi.spyOn(globalThis,'fetch').mockImplementation(()=>new Promise<Response>(done=>{resolve=done}))
  const controller=new AbortController(),parse=vi.fn((v:unknown)=>v),pending=investigationGet('/health',parse,controller.signal)
  controller.abort();resolve(json(health));await expect(pending).rejects.toThrow();expect(parse).not.toHaveBeenCalled()
})
it('previews only explicit hash-bound UTF-8 diff bytes and escapes HTML-looking source',async()=>{
  vi.stubGlobal('crypto',webcrypto)
  const text='<script>window.PWNED=true</script>\n+STATUS=4\n',f=fixture('firmware','native-project',text),calls=mock(f);mount()
  await screen.findByText('Curated header correction');expect(calls.mock.calls.some(([url])=>String(url).endsWith('/download'))).toBe(false)
  await userEvent.click(screen.getByRole('button',{name:'Preview recorded header diff'}))
  await waitFor(()=>expect(document.querySelector('pre')).toHaveTextContent('<script>window.PWNED=true</script>'))
  expect(document.querySelector('script')).toBeNull();expect((window as Obj).PWNED).toBeUndefined()
})
it('withholds diff text on a hash mismatch and after a case switch',async()=>{
  vi.stubGlobal('crypto',webcrypto)
  const f=fixture('firmware');let delayed=false,resolve!:(r:Response)=>void
  mock(f,url=>url.endsWith('/download')?delayed?new Promise<Response>(done=>{resolve=done}):new Response('WRONG'):undefined);mount()
  await screen.findByText('Curated header correction');await userEvent.click(screen.getByRole('button',{name:'Preview recorded header diff'}));await screen.findByRole('alert')
  expect(document.querySelector('pre')).toBeNull();delayed=true
  await userEvent.click(screen.getByRole('button',{name:'Preview recorded header diff'}));await waitFor(()=>expect(resolve).toBeTypeOf('function'))
  await userEvent.selectOptions(screen.getByLabelText('Recorded native case'),'bad')
  await act(async()=>{resolve(new Response(f.diffText));await new Promise(done=>setTimeout(done,20))})
  expect(document.querySelector('pre')).toBeNull()
})
it('does not fetch a diff larger than 64 KiB',async()=>{
  const f=fixture('firmware','native-project','x'.repeat(65537)),calls=mock(f);mount();await screen.findByText('Curated header correction')
  expect(screen.getByRole('button',{name:'Preview recorded header diff'})).toBeDisabled()
  expect(calls.mock.calls.some(([url])=>String(url).endsWith('/download'))).toBe(false)
})
it('rejects matching-hash bytes that are not valid UTF-8',async()=>{
  vi.stubGlobal('crypto',webcrypto)
  const f=fixture('firmware'),bytes=new Uint8Array([0xc3,0x28]),digest=createHash('sha256').update(bytes).digest('hex')
  f.detail.native_report.candidate.diff_ref.sha256=digest
  const envelope=f.detail.envelope.artifacts.find((a:Obj)=>a.reference.artifact_id==='candidate-diff');envelope.reference.sha256=digest;f.detail.envelope.total_artifact_bytes+=2-envelope.size_bytes;envelope.size_bytes=2
  const binding=f.detail.native_report.selection.selected.find((a:Obj)=>a.ref.artifact_id==='candidate-diff');binding.size_bytes=2
  const entry=f.detail.native_report.selection.inventory.find((a:Obj)=>a.original_relative_path==='candidate/header.diff');entry.sha256=digest;entry.size_bytes=2
  const download=f.downloads.items.find(a=>a.artifact_id==='candidate-diff')!;download.sha256=digest;download.size_bytes=2
  mock(f,url=>url.endsWith('/download')?new Response(bytes):undefined);mount();await screen.findByText('Curated header correction')
  await userEvent.click(screen.getByRole('button',{name:'Preview recorded header diff'}));await screen.findByRole('alert');expect(document.querySelector('pre')).toBeNull()
})
