// Synthetic v3 metadata + mocked HTTP only. No native process or design success.
import { createHash, webcrypto } from 'node:crypto'
import { cleanup, render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { afterEach, beforeEach, expect, it, vi } from 'vitest'
import { EdaRepairInvestigation } from './EdaRepairInvestigation'
import { EngineeringInvestigation } from './EngineeringInvestigation'
import { parseInvestigations } from './engineeringInvestigationApi'
const h='a'.repeat(64)
const canonical=(v:unknown)=>JSON.stringify(v,(_k,v)=>v&&typeof v==='object'&&!Array.isArray(v)?Object.fromEntries(Object.keys(v).sort().map(k=>[k,v[k]])):v)
const digest=(v:unknown)=>createHash('sha256').update(canonical(v)).digest('hex')
const ref=(name:string)=>({artifact_id:name,revision_id:'source-revision',attempt_id:null,sha256:h})
function fixture(id='original',investigation='a-original'){
  const requirements=ref('requirements'),configuration=ref('configuration'),receipt=ref('receipt'),schematic=ref('schematic'),board=ref('board'),report=ref('erc')
  const project={schema_version:'engineering.project-revision.v1',project_id:'project',revision_id:id,source_refs:[schematic,board],configuration_hash:h}
  const run={project,sources:[{name:'schematic.xml',reference:schematic},{name:'board.kicad_pcb',reference:board}],configuration,receipt,reports:[{name:'erc.json',reference:report,source_refs:[schematic]}]}
  const record={schema_version:'engineering.eda-investigation.v1',investigation_id:`native-${id}`,domain:'kicad',requirements,candidate:run,baseline:null}
  const envelope={schema_version:'engineering.admission.v3',record,record_hash:digest(record),checker_sha256:h,artifacts:[requirements,configuration,receipt,schematic,board,report].map(reference=>({reference,size_bytes:1})),total_artifact_bytes:6,provenance:'recorded_bytes_not_new_execution',execution_status:'not_observed',design_verdict:'inconclusive'}
  const summary={id,investigation_id:investigation,project_id:'project',ordinal:1,content_hash:digest(envelope),revision_id:id,revision_hash:digest(project),manifest_hash:h,schema_version:'engineering.admission.v3',scope:'recorded_import',execution_status:'not_run',created_at:'2026-11-03T00:00:00Z',domains:['kicad'],native_investigation_id:record.investigation_id}
  const detail={...summary,envelope,project_revision:project,execution_contexts:[]}
  const item={id:investigation,project_id:'project',created_at:summary.created_at,latest_version:summary}
  const selected=parseInvestigations({items:[item],next_cursor:null},'project','all').items[0].latest
  const artifacts={items:envelope.artifacts.map((a,i)=>({id:`blob-${i}`,...a.reference,size_bytes:1,media_type:'application/octet-stream',roles:['eda_evidence']}))}
  return {summary,detail,item,selected,artifacts}
}
const original=()=>fixture(),candidate=()=>fixture('candidate','b-candidate')
const json=(v:unknown,status=200)=>new Response(JSON.stringify(v),{status})
function job(status='completed'){return {job_id:'native-job',domain:'kicad',project_id:'project',original_version_id:'original',candidate_version_id:'candidate',status,cancel_requested:status==='cancelled',blockers:status==='blocked'?['unreviewed_mapping']:[],result:status==='completed'?{ticket_hash:h,configuration_hash:h,native_result_sha256:h,retained:true,settled:true,execution_status:'completed',design_verdict:'not_evaluated'}:null,design_verdict:'not_evaluated',physical_electrical_verdict:'inconclusive'}}
function http(override?:(url:string,init?:RequestInit)=>Response|Promise<Response>|undefined){
  const a=original(),b=candidate()
  return vi.spyOn(globalThis,'fetch').mockImplementation(async(input,init)=>{
    const url=String(input),custom=override?.(url,init);if(custom)return custom
    if(url==='/api/health')return json({status:'ok',investigation_api:'1',investigation_admission_schemas:['engineering.admission.v1','engineering.admission.v2','engineering.admission.v3'],investigation_report_kinds:['riscv.recorded-report.v1','firmware.recorded-report.v1','riscv.recorded-report.v2']})
    if(url.includes('/projects/project/investigations?'))return json({items:[a.item,b.item],next_cursor:null})
    for(const f of [a,b]){
      if(url.includes(`/investigations/${f.item.id}/versions?`))return json({items:[f.summary],next_cursor:null})
      if(url.endsWith(`/versions/${f.summary.id}/artifacts`))return json(f.artifacts)
      if(url.endsWith(`/versions/${f.summary.id}`))return json(f.detail)
    }
    if(url.startsWith('/api/eda-repairs/'))return json(job())
    return json({},404)
  })
}
function mount(integrated=false){const client=new QueryClient({defaultOptions:{queries:{retry:false,gcTime:0}}});return render(<QueryClientProvider client={client}>{integrated?<EngineeringInvestigation projectId="project"/>:<EdaRepairInvestigation original={original().selected}/>}</QueryClientProvider>)}
async function select(){await userEvent.selectOptions(await screen.findByLabelText('Candidate investigation'),'b-candidate');await userEvent.selectOptions(await screen.findByLabelText('Candidate version'),'candidate');await waitFor(()=>expect(screen.getByRole('button',{name:'Request native candidate execution'})).toBeEnabled())}
beforeEach(()=>{vi.stubGlobal('crypto',webcrypto);sessionStorage.clear()})
afterEach(()=>{cleanup();vi.restoreAllMocks();vi.unstubAllGlobals();sessionStorage.clear()})

it('requires explicit immutable selection and sends ID-only native request; receipt metadata never claims success',async()=>{
  const fetch=http();mount();expect(screen.getByRole('button',{name:'Request native candidate execution'})).toBeDisabled();await select()
  expect(fetch.mock.calls.every(([,init])=>init?.method==='GET')).toBe(true)
  await userEvent.click(screen.getByRole('button',{name:'Request native candidate execution'}))
  await screen.findByText(/^Execution state:/)
  const post=fetch.mock.calls.find(([url,init])=>String(url)==='/api/eda-repairs/kicad'&&init?.method==='POST')!
  expect(JSON.parse(post[1]!.body as string)).toEqual({project_id:'project',original_version_id:'original',candidate_version_id:'candidate'})
  await userEvent.click(screen.getByRole('button',{name:'Inspect native result metadata'}));await screen.findByText('Native receipt SHA-256')
  expect(screen.getByText(/They do not establish design success/)).toBeInTheDocument()
  expect(screen.queryByRole('link',{name:/download/i})).not.toBeInTheDocument()
})
it('keeps the exact key/body after an ambiguous submission and remount without automatic resubmission',async()=>{
  let fail=true
  const fetch=http((url,init)=>url==='/api/eda-repairs/kicad'&&init?.method==='POST'?fail?Promise.reject(new Error('network')):json(job()):undefined)
  const view=mount();await select();await userEvent.click(screen.getByRole('button',{name:'Request native candidate execution'}));await screen.findByRole('alert')
  const first=fetch.mock.calls.find(([,init])=>init?.method==='POST')![1]!
  view.unmount();mount();await screen.findByRole('button',{name:'Retry preserved native request'})
  expect(fetch.mock.calls.filter(([,init])=>init?.method==='POST')).toHaveLength(1)
  fail=false;await userEvent.click(screen.getByRole('button',{name:'Retry preserved native request'}));await screen.findByText(/^Execution state:/)
  const second=fetch.mock.calls.filter(([,init])=>init?.method==='POST')[1][1]!
  expect(second.body).toBe(first.body);expect(second.headers).toEqual(first.headers)
})
it('renders unavailable backend without fallback or duplicate submission',async()=>{
  const fetch=http((url,init)=>url==='/api/eda-repairs/kicad'&&init?.method==='POST'?new Response('PRIVATE SERVER PATH',{status:409}):undefined)
  mount();await select();await userEvent.click(screen.getByRole('button',{name:'Request native candidate execution'}));await screen.findByRole('alert')
  expect(screen.getByRole('alert')).toHaveTextContent('unavailable, disabled or conflicts')
  expect(document.body.textContent).not.toContain('PRIVATE SERVER PATH');expect(fetch.mock.calls.filter(([,init])=>init?.method==='POST')).toHaveLength(1)
  expect(screen.queryByRole('button',{name:'Cancel selected native job'})).not.toBeInTheDocument()
})
it('shows blockers distinctly and cancels only the returned selected job',async()=>{
  let cancelled=false
  const fetch=http((url,init)=>url.startsWith('/api/eda-repairs/')?(url.endsWith('/cancel')&&init?.method==='POST'?(cancelled=true,json({...job('cancelled'),blockers:['unreviewed_mapping']})):json(cancelled?{...job('cancelled'),blockers:['unreviewed_mapping']}:job('blocked'))):undefined)
  mount();await select();await userEvent.click(screen.getByRole('button',{name:'Request native candidate execution'}));await screen.findByRole('heading',{name:'Execution blockers'})
  expect(screen.getByText('unreviewed_mapping')).toBeInTheDocument()
  await userEvent.click(screen.getByRole('button',{name:'Cancel selected native job'}));await waitFor(()=>expect(screen.queryByRole('button',{name:'Cancel selected native job'})).not.toBeInTheDocument())
  expect(fetch.mock.calls.filter(([url,init])=>String(url).endsWith('/cancel')&&init?.method==='POST').map(([url])=>url)).toEqual(['/api/eda-repairs/jobs/native-job/cancel'])
})
it('hides a detached job response and never offers its cancellation',async()=>{
  http((url)=>url.startsWith('/api/eda-repairs/')?json({...job(),candidate_version_id:'other'}):undefined)
  mount();await select();await userEvent.click(screen.getByRole('button',{name:'Request native candidate execution'}));await screen.findByRole('alert')
  expect(screen.queryByText('Execution state:')).not.toBeInTheDocument();expect(screen.queryByRole('button',{name:'Cancel selected native job'})).not.toBeInTheDocument()
})
it('blocks corrupt saved request metadata without sending a mutation',async()=>{
  sessionStorage.setItem('veriforge-eda-request:project:original','broken');const fetch=http();mount();await screen.findByRole('alert')
  expect(screen.getByRole('button',{name:'Request native candidate execution'})).toBeDisabled();expect(fetch.mock.calls).toHaveLength(0)
})
it('connects the action only after the existing integrated EDA detail is validated',async()=>{
  const fetch=http();mount(true);await screen.findByRole('heading',{name:'Compare an admitted KiCad candidate'})
  expect(await screen.findByRole('region',{name:'Recorded EDA evidence'})).toBeInTheDocument()
  expect(fetch.mock.calls.every(([,init])=>init?.method==='GET')).toBe(true)
})
it('aborts an in-flight submission on unmount while preserving the exact retry journal',async()=>{
  let signal:AbortSignal|undefined
  http((url,init)=>url==='/api/eda-repairs/kicad'&&init?.method==='POST'?new Promise((_resolve,reject)=>{signal=init.signal as AbortSignal;signal.addEventListener('abort',()=>reject(new Error('aborted')),{once:true})}):undefined)
  const view=mount();await select();await userEvent.click(screen.getByRole('button',{name:'Request native candidate execution'}));await waitFor(()=>expect(signal).toBeDefined());view.unmount()
  expect(signal!.aborted).toBe(true);expect(sessionStorage.getItem('veriforge-eda-request:project:original')).toContain('candidate_version_id')
})
it('hides settled job and report metadata after a failed refresh',async()=>{
  let unavailable=false
  http((url)=>url==='/api/eda-repairs/jobs/native-job'&&unavailable?new Response('PRIVATE',{status:409}):undefined)
  mount();await select();await userEvent.click(screen.getByRole('button',{name:'Request native candidate execution'}));await screen.findByRole('button',{name:'Inspect native result metadata'})
  await userEvent.click(screen.getByRole('button',{name:'Inspect native result metadata'}));await screen.findByText('Native receipt SHA-256')
  unavailable=true;await userEvent.click(screen.getByRole('button',{name:'Refresh native job'}));await screen.findByRole('alert')
  expect(screen.queryByText('Native receipt SHA-256')).not.toBeInTheDocument()
  expect(screen.queryByRole('button',{name:'Inspect native result metadata'})).not.toBeInTheDocument()
  expect(screen.queryByRole('button',{name:'Start another native comparison'})).not.toBeInTheDocument()
})
it('rejects changed report metadata instead of displaying an unbound receipt',async()=>{
  http((url)=>url.endsWith('/native-job/report')?json({...job(),result:{...job().result!,native_result_sha256:'b'.repeat(64)}}):undefined)
  mount();await select();await userEvent.click(screen.getByRole('button',{name:'Request native candidate execution'}));await screen.findByRole('button',{name:'Inspect native result metadata'})
  await userEvent.click(screen.getByRole('button',{name:'Inspect native result metadata'}));await screen.findByRole('alert')
  expect(screen.queryByText('Native receipt SHA-256')).not.toBeInTheDocument()
})
it.each([false,true])('preserves quarantined request/key across report inspection, refresh and remount (retained result: %s)',async retained=>{
  const fetch=http(url=>url.startsWith('/api/eda-repairs/')?json({...job('quarantined'),result:retained?job().result:null}):undefined)
  const view=mount();await select();await userEvent.click(screen.getByRole('button',{name:'Request native candidate execution'}))
  await screen.findByText(/Quarantined work may have unknown native effects/)
  const preserved=sessionStorage.getItem('veriforge-eda-request:project:original')!
  expect(JSON.parse(preserved).jobId).toBe('native-job')
  expect(screen.queryByRole('button',{name:'Start another native comparison'})).not.toBeInTheDocument()
  await userEvent.click(screen.getByRole('button',{name:'Inspect native result metadata'}))
  if(retained)await screen.findByText('Native receipt SHA-256')
  else await screen.findByText('No retained native completion result is available.')
  expect(screen.queryByRole('button',{name:'Start another native comparison'})).not.toBeInTheDocument()
  await userEvent.click(screen.getByRole('button',{name:'Refresh native job'}));await screen.findByText(/Quarantined work may have unknown native effects/)
  expect(sessionStorage.getItem('veriforge-eda-request:project:original')).toBe(preserved)
  view.unmount();mount();await screen.findByText(/Quarantined work may have unknown native effects/)
  expect(sessionStorage.getItem('veriforge-eda-request:project:original')).toBe(preserved)
  expect(screen.queryByRole('button',{name:'Start another native comparison'})).not.toBeInTheDocument()
  expect(screen.queryByRole('button',{name:'Request native candidate execution'})).not.toBeInTheDocument()
  expect(fetch.mock.calls.filter(([,init])=>init?.method==='POST')).toHaveLength(1)
})
