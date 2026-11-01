// Synthetic v3 wire metadata and mocked transport only; no tool/DB admission.
import { createHash, webcrypto } from 'node:crypto'
import { cleanup, render, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { afterEach, beforeEach, expect, it, vi } from 'vitest'
import { EngineeringInvestigation } from './EngineeringInvestigation'
import { parseArtifacts, parseEdaDetail, parseInvestigations, parseInvestigationCapabilities } from './engineeringInvestigationApi'
import { parseProjection } from './investigationProjectionApi'

const h='a'.repeat(64),bad='b'.repeat(64)
const canonical=(v:unknown)=>JSON.stringify(v,(_k,v)=>v&&typeof v==='object'&&!Array.isArray(v)?Object.fromEntries(Object.keys(v).sort().map(k=>[k,v[k]])):v)
const digest=(v:unknown)=>createHash('sha256').update(canonical(v)).digest('hex')
const ref=(name:string)=>({artifact_id:name,revision_id:'source-revision',attempt_id:null,sha256:h})
const health={status:'ok',investigation_api:'1',investigation_admission_schemas:['engineering.admission.v1','engineering.admission.v2','engineering.admission.v3'],investigation_report_kinds:['riscv.recorded-report.v1','firmware.recorded-report.v1','riscv.recorded-report.v2']}
function fixture() {
  const requirements=ref('requirements'),configuration=ref('configuration'),receipt=ref('receipt'),schematic=ref('schematic'),board=ref('board'),report=ref('erc')
  const project={schema_version:'engineering.project-revision.v1',project_id:'eda-project',revision_id:'candidate-1',source_refs:[schematic,board],configuration_hash:h}
  const candidate={project,sources:[{name:'schematic.xml',reference:schematic},{name:'board.kicad_pcb',reference:board}],configuration,receipt,reports:[{name:'erc.json',reference:report,source_refs:[schematic]}]}
  const record={schema_version:'engineering.eda-investigation.v1',investigation_id:'native-eda',domain:'kicad',requirements,candidate,baseline:null}
  const envelope={schema_version:'engineering.admission.v3',record,record_hash:digest(record),checker_sha256:h,artifacts:[requirements,configuration,receipt,schematic,board,report].map(reference=>({reference,size_bytes:1})),total_artifact_bytes:6,provenance:'recorded_bytes_not_new_execution',execution_status:'not_observed',design_verdict:'inconclusive'}
  const summary={id:'eda-version',investigation_id:'eda-investigation',project_id:project.project_id,ordinal:1,content_hash:digest(envelope),revision_id:project.revision_id,revision_hash:digest(project),manifest_hash:h,schema_version:'engineering.admission.v3',scope:'recorded_import',execution_status:'not_run',created_at:'2026-11-01T00:00:00Z',domains:['kicad'],native_investigation_id:'native-eda'}
  const detail={...summary,envelope,project_revision:project,execution_contexts:[]}
  const item={id:summary.investigation_id,project_id:summary.project_id,created_at:summary.created_at,latest_version:summary}
  const selected=parseInvestigations({items:[item],next_cursor:null},summary.project_id,'all').items[0].latest
  const downloads={items:envelope.artifacts.map((a,i)=>({id:`blob-${i}`,...a.reference,size_bytes:a.size_bytes,media_type:'application/octet-stream',roles:['eda_evidence']}))}
  const partial={domain:'kicad',mapping:{status:'inconclusive',gap:'reviewed_mapping_assertions_missing'},configured_reports:[{kind:'erc',sha256:h,rule_status:'configured_rules_clear',ignored_checks:['ignored-example']}],configured_comparisons:[],execution_status:'not_observed',physical_electrical_verdict:'inconclusive'}
  const projection={schema_version:'engineering.projection.v1',project_id:summary.project_id,investigation_id:summary.investigation_id,version_id:summary.id,content_hash:summary.content_hash,scope:'recorded_import',execution_status:'not_run',projections:[{domain:'kicad',schema_version:'kicad.investigation.v1',data:partial}],gaps:['Reviewed mapping assertions are not supplied.']}
  return {summary,detail,item,selected,downloads,partial,projection}
}
beforeEach(()=>vi.stubGlobal('crypto',webcrypto))
afterEach(()=>{cleanup();vi.restoreAllMocks();vi.unstubAllGlobals()})

it('supports the closed three-schema capability union and compatible old runtime declarations',()=>{
  expect(parseInvestigationCapabilities(health)).toEqual({mode:'all'})
  expect(parseInvestigationCapabilities({...health,investigation_admission_schemas:health.investigation_admission_schemas.slice(0,2)})).toEqual({mode:'all'})
  for(const schemas of [[...health.investigation_admission_schemas,'engineering.admission.v9'],['engineering.admission.v1','engineering.admission.v3'],['engineering.admission.v1','engineering.admission.v2','engineering.admission.v2']])expect(()=>parseInvestigationCapabilities({...health,investigation_admission_schemas:schemas})).toThrow()
})
it('parses independently hash-bound v3 detail and exact eda_evidence download identities',async()=>{
  const f=fixture(),detail=await parseEdaDetail(f.detail,f.selected)
  expect(detail.kind).toBe('eda');expect(detail.domain).toBe('kicad');expect(detail.baseline).toBeNull();expect(detail.candidate.sources).toHaveLength(2)
  expect(parseArtifacts(f.downloads,detail)).toHaveLength(6)
  f.downloads.items[0].roles=['native_fact'];expect(()=>parseArtifacts(f.downloads,detail)).toThrow()
})
it('retains a distinct same-project baseline under the exact unchanged configuration',async()=>{
  const f=fixture(),record=f.detail.envelope.record
  Object.assign(record,{baseline:{...record.candidate,project:{...record.candidate.project,revision_id:'baseline-1'}}})
  f.detail.envelope.record_hash=digest(record);f.detail.content_hash=digest(f.detail.envelope)
  const selected={...f.selected,contentHash:f.detail.content_hash}
  const detail=await parseEdaDetail(f.detail,selected)
  expect(detail.baseline?.revisionId).toBe('baseline-1')
  Object.assign(record,{baseline:record.candidate})
  await expect(parseEdaDetail(f.detail,selected)).rejects.toThrow()
})
it('parses the independent Vivado EDA detail branch with complete report source bindings',async()=>{
  const f=fixture(),record=f.detail.envelope.record,run=record.candidate,xdc=ref('xdc')
  record.domain='vivado';f.detail.domains=['vivado']
  run.sources=[{name:'exercise3.v',reference:run.sources[0].reference},{name:'exercise3_nexys_video_top.v',reference:run.sources[1].reference},{name:'nexys_video_exercise3.xdc',reference:xdc}]
  run.project.source_refs=run.sources.map(s=>s.reference);run.reports=[{name:'timing_summary.rpt',reference:run.reports[0].reference,source_refs:run.project.source_refs}]
  f.detail.envelope.artifacts.push({reference:xdc,size_bytes:1});f.detail.envelope.total_artifact_bytes++
  f.detail.envelope.record_hash=digest(record);f.detail.revision_hash=digest(run.project);f.detail.content_hash=digest(f.detail.envelope)
  const selected={...f.selected,domains:['vivado'],revisionHash:f.detail.revision_hash,contentHash:f.detail.content_hash}
  expect((await parseEdaDetail(f.detail,selected)).domain).toBe('vivado')
  run.reports[0].source_refs=run.project.source_refs.slice(1)
  await expect(parseEdaDetail(f.detail,selected)).rejects.toThrow()
})
it.each(['project','domain','report_kind','total','record_hash','configuration','report-source','execution-context','source-swap','old-content-hash'])('rejects v3 identity, closure or scope corruption: %s',async kind=>{
  const f=fixture()
  if(kind==='project')f.detail.project_id='another-project'
  if(kind==='domain')f.detail.envelope.record.domain='vivado'
  if(kind==='report_kind')Object.assign(f.detail,{report_kind:'riscv.recorded-report.v1'})
  if(kind==='total')f.detail.envelope.total_artifact_bytes++
  if(kind==='record_hash')f.detail.envelope.record_hash=bad
  if(kind==='configuration')f.detail.envelope.record.candidate.configuration.sha256=bad
  if(kind==='report-source')f.detail.envelope.record.candidate.reports[0].source_refs=[f.detail.envelope.record.candidate.sources[1].reference]
  if(kind==='execution-context')Object.assign(f.detail,{execution_contexts:[{attempt:'invented'}]})
  if(kind==='source-swap')f.detail.envelope.artifacts[0].reference.sha256=bad
  if(kind==='old-content-hash')f.detail.envelope.checker_sha256=bad
  await expect(parseEdaDetail(f.detail,f.selected)).rejects.toThrow()
})
it('rejects v3 discovery outside native discovery mode and non-EDA domains',()=>{
  const f=fixture();expect(()=>parseInvestigations({items:[f.item],next_cursor:null},f.summary.project_id)).toThrow()
  f.summary.domains=['firmware'];expect(()=>parseInvestigations({items:[f.item],next_cursor:null},f.summary.project_id,'all')).toThrow()
})
it('retains partial KiCad report evidence without filling missing mapping/view fields',()=>{
  const f=fixture(),parsed=parseProjection(f.projection,f.selected).projections[0]
  expect(parsed.domain).toBe('kicad')
  if(parsed.domain==='kicad'){expect(parsed.partial).toBe(true);expect('rows' in parsed).toBe(false);expect('schematicViews' in parsed).toBe(false)}
  f.partial.mapping.gap='unknown_gap';expect(()=>parseProjection(f.projection,f.selected)).toThrow()
})
it('rejects a projection from another EDA domain even when envelope IDs match',()=>{
  const f=fixture();f.projection.projections[0].domain='vivado';expect(()=>parseProjection(f.projection,f.selected)).toThrow()
})
it('connects v3 discovery/detail/artifacts and partial projection while hiding stale data on409',async()=>{
  const f=fixture();let corrupt=false
  const fetch=vi.spyOn(globalThis,'fetch').mockImplementation(async(input)=>{
    const path=String(input),json=(v:unknown)=>new Response(JSON.stringify(v))
    if(path==='/api/health')return json(health)
    if(path.startsWith('/api/projects/eda-project/investigations?'))return json({items:[f.item],next_cursor:null})
    if(path.startsWith('/api/investigations/eda-investigation/versions?'))return json({items:[f.summary],next_cursor:null})
    if(path.endsWith('/projection'))return json(f.projection)
    if(path.endsWith('/artifacts'))return json(f.downloads)
    if(path.endsWith('/eda-version'))return corrupt?new Response('PRIVATE',{status:409}):json(f.detail)
    throw new Error('Unexpected synthetic request')
  })
  const client=new QueryClient({defaultOptions:{queries:{retry:false}}})
  render(<QueryClientProvider client={client}><EngineeringInvestigation projectId="eda-project"/></QueryClientProvider>)
  const detail=await screen.findByRole('region',{name:'Recorded EDA evidence'})
  expect(within(detail).getByText('Not observed')).toBeInTheDocument()
  expect(within(detail).getByRole('link',{name:'Download requirements'})).toHaveAttribute('href','/api/investigations/eda-investigation/versions/eda-version/artifacts/blob-0/download')
  expect(fetch.mock.calls.every(([path])=>!String(path).endsWith('/download'))).toBe(true)
  await userEvent.click(screen.getByRole('button',{name:'Load connected domain views'}))
  await screen.findByRole('article',{name:'Partial KiCad report investigation'})
  expect(screen.queryByRole('region',{name:'KiCad mapping rows'})).not.toBeInTheDocument()
  corrupt=true;await userEvent.click(screen.getByRole('button',{name:'Refresh investigations'}))
  await screen.findByRole('alert')
  await waitFor(()=>expect(screen.queryByRole('region',{name:'Recorded EDA evidence'})).not.toBeInTheDocument())
  expect(screen.queryByRole('region',{name:'EDA artifact inspector'})).not.toBeInTheDocument()
  expect(document.body.textContent).not.toContain('PRIVATE')
})
