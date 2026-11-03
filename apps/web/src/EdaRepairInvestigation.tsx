import { useEffect, useId, useRef, useState } from 'react'
import { useQuery } from '@tanstack/react-query'
import { humanize } from './api'
import { investigationGet, parseInvestigations, parseVersions, parseEdaDetail, versionPath } from './engineeringInvestigationApi'
import type { Investigation, VersionSummary } from './engineeringInvestigationApi'
import { compatibleEdaJob, edaBinding, edaDomain, edaPollInterval, edaReplaceable, edaRequest, edaTerminal, EdaRepairError, EDA_POLL_WINDOW, readPendingEda } from './edaRepairApi'
import type { EdaJob, PendingEda } from './edaRepairApi'
import { parseEdaJob } from './edaRepairApi'

/** Existing selected detail establishes the original's immutable read binding. */
export function EdaRepairInvestigation({original}:{original:VersionSummary}) {
  return <CandidateSelection key={`${original.projectId}:${original.id}:${original.contentHash}`} original={original}/>
}
function CandidateSelection({original}:{original:VersionSummary}) {
  const uid=useId(),domain=edaDomain(original),storageKey=`veriforge-eda-request:${original.projectId}:${original.id}`
  const [initial]=useState(()=>{try{return {pending:readPendingEda(storageKey,original),error:''}}catch{return {pending:null,error:'Saved native request metadata is unreadable. Submission is blocked; preserve the saved entry for operator recovery.'}}})
  const [pending,setPending]=useState<PendingEda|null>(initial.pending),[localError,setLocalError]=useState(initial.error)
  const [pages,setPages]=useState(['']),[selected,setSelected]=useState(''),[candidate,setCandidate]=useState<VersionSummary|null>(null)
  const [busy,setBusy]=useState(false),[error,setError]=useState<Error|null>(null),[job,setJob]=useState<EdaJob|null>(null)
  const controller=useRef<AbortController|null>(null),mounted=useRef(true)
  useEffect(()=>{mounted.current=true;return()=>{mounted.current=false;controller.current?.abort()}},[])
  const after=pages.at(-1)!
  const query=useQuery({queryKey:['eda-candidates',original.projectId,original.id,uid,after],queryFn:({signal})=>investigationGet(`/projects/${encodeURIComponent(original.projectId)}/investigations?after=${encodeURIComponent(after)}&limit=20&admission_schema=all`,v=>{
    const page=parseInvestigations(v,original.projectId,'all')
    if(page.items.length>20||page.items.some((row,i)=>row.id<=(i?page.items[i-1].id:after))||page.next!==null&&(page.items.length!==20||page.next!==page.items.at(-1)?.id))throw new Error('Invalid candidate discovery pagination.')
    return page
  },signal),enabled:!pending&&!initial.error,retry:false,gcTime:0,refetchOnMount:'always'})
  const options=query.data?.items.filter(item=>item.latest.schemaVersion==='engineering.admission.v3'&&item.latest.domains.length===1&&item.latest.domains[0]===domain)??[]
  const investigation=options.find(item=>item.id===selected)
  function save(value: PendingEda){sessionStorage.setItem(storageKey,JSON.stringify(value));setPending(value)}
  async function submit(){
    if(busy||initial.error)return
    let exact=pending
    try{if(!exact){if(!candidate||query.isFetching||query.isError)return;exact={binding:edaBinding(original,candidate),key:crypto.randomUUID(),jobId:null};save(exact)}}catch{setLocalError('Cannot preserve the exact native request and key. No request was sent.');return}
    if(exact.jobId)return
    setBusy(true);setError(null);controller.current=new AbortController()
    try{const value=await edaRequest(`/eda-repairs/${domain}`,v=>parseEdaJob(v,exact!.binding),{method:'POST',body:exact.binding.request,key:exact.key,signal:controller.current.signal});if(!mounted.current)return;setJob(value);try{save({...exact,jobId:value.job_id})}catch{setPending({...exact,jobId:value.job_id});setLocalError(`Job ${value.job_id} was returned but could not be saved. Record this ID before closing.`)}}catch(e){if(mounted.current)setError(e as Error)}finally{if(mounted.current)setBusy(false)}
  }
  function clear(){try{sessionStorage.removeItem(storageKey);setPending(null);setJob(null);setCandidate(null);setError(null);setLocalError('')}catch{setLocalError('Saved request could not be cleared. No replacement request was sent.')}}
  return <section className="panel" aria-labelledby={`${uid}-heading`}>
    <span className="eyebrow">Native candidate execution</span><h3 id={`${uid}-heading`}>Compare an admitted {domain==='vivado'?'Vivado':'KiCad'} candidate</h3>
    <p className="notice">Select already-owned immutable versions. This action requests native execution through the installed trusted runtime; recorded admission alone establishes no execution readiness. Design verdict remains not evaluated; physical/electrical verdict remains inconclusive.</p>
    <dl className="ei-grid"><div><dt>Original version</dt><dd><code>{original.id}</code></dd></div><div><dt>Original envelope SHA-256</dt><dd><code>{original.contentHash}</code></dd></div><div><dt>Project / domain</dt><dd>{original.projectId} / {domain}</dd></div></dl>
    {!pending&&!initial.error?<>
      {query.isFetching||query.isPending?<p role="status">Loading candidate investigations…</p>:query.isError?<div role="alert"><p>{query.error.message}</p><button onClick={()=>void query.refetch()}>Refresh candidate discovery</button></div>:<><label htmlFor={`${uid}-investigation`}>Candidate investigation</label><select id={`${uid}-investigation`} value={selected} onChange={e=>{setSelected(e.target.value);setCandidate(null)}}><option value="">Select a {domain} investigation</option>{options.map(item=><option key={item.id} value={item.id}>{item.id}</option>)}</select>{!options.length?<p>No compatible {domain} investigations on this page.</p>:null}</>}
      <div className="actions"><button disabled={pages.length===1||query.isFetching} onClick={()=>{setPages(p=>p.slice(0,-1));setSelected('');setCandidate(null)}}>Previous candidate page</button><button disabled={!query.data?.next||pages.length>=20||query.isFetching||query.isError} onClick={()=>{setPages(p=>[...p,query.data!.next!]);setSelected('');setCandidate(null)}}>Next candidate page</button></div>
      {!query.isFetching&&!query.isError&&investigation?<CandidateVersions key={investigation.id} investigation={investigation} original={original} choose={setCandidate}/>:null}
      {candidate?<dl><div><dt>Candidate version</dt><dd><code>{candidate.id}</code></dd></div><div><dt>Candidate envelope SHA-256</dt><dd><code>{candidate.contentHash}</code></dd></div></dl>:null}
    </>:null}
    {pending?<p className="notice">Preserved request: original <code>{pending.binding.request.original_version_id}</code>, candidate <code>{pending.binding.request.candidate_version_id}</code>. The same body and key are retained. No automatic resubmission occurs; selection changes or closing this view do not cancel a durable job.</p>:null}
    {!pending?.jobId?<button disabled={busy||!!localError||(!pending&&!candidate)} onClick={()=>void submit()}>{busy?'Submitting preserved native request…':pending?'Retry preserved native request':'Request native candidate execution'}</button>:null}
    {error?<p role="alert">{error.message}</p>:null}{localError?<p role="alert">{localError}</p>:null}
    {pending?.jobId?<EdaJobView key={pending.jobId} pending={pending} initial={job} onNew={clear}/>:null}
  </section>
}
function CandidateVersions({investigation,original,choose}:{investigation:Investigation;original:VersionSummary;choose:(v:VersionSummary|null)=>void}) {
  const uid=useId(),[pages,setPages]=useState([0]),[selection,setSelection]=useState(''),after=pages.at(-1)!
  const query=useQuery({queryKey:['eda-candidate-versions',uid,investigation.id,after],queryFn:({signal})=>investigationGet(`/investigations/${encodeURIComponent(investigation.id)}/versions?after=${after}&limit=20&admission_schema=all`,v=>{const page=parseVersions(v,original.projectId,investigation.id,'all');if(page.items.length>20||page.items.some(v=>v.ordinal<=after||v.schemaVersion!=='engineering.admission.v3'||v.domains.join()!==original.domains.join())||page.next!==null&&(page.items.length!==20||page.next!==page.items.at(-1)?.ordinal))throw new Error('Invalid candidate version history.');return page},signal),retry:false,gcTime:0,refetchOnMount:'always'})
  const selected=query.data?.items.find(v=>v.id===selection&&v.id!==original.id)
  const detail=useQuery({queryKey:['eda-candidate-detail',uid,selected?.id,selected?.contentHash],queryFn:({signal})=>investigationGet(versionPath(selected!.investigationId,selected!.id),v=>parseEdaDetail(v,selected!),signal),enabled:!!selected&&!query.isFetching&&!query.isError,retry:false,gcTime:0,refetchOnMount:'always'})
  const ready=!!selected&&!query.isFetching&&!query.isError&&detail.isSuccess&&!detail.isFetching
  useEffect(()=>{choose(ready?selected!:null)},[ready,selected,choose])
  useEffect(()=>()=>choose(null),[choose])
  function page(next?:number){choose(null);setSelection('');setPages(p=>next===undefined?p.slice(0,-1):[...p,next])}
  return <div className="ei-record">
    {query.isPending||query.isFetching?<p role="status">Loading immutable candidate versions…</p>:query.isError?<p role="alert">{query.error.message}</p>:<><label htmlFor={`${uid}-version`}>Candidate version</label><select id={`${uid}-version`} value={selection} onChange={e=>{choose(null);setSelection(e.target.value)}}><option value="">Select a distinct immutable version</option>{query.data.items.filter(v=>v.id!==original.id).map(v=><option key={v.id} value={v.id}>Version {v.ordinal} · {v.id}</option>)}</select></>}
    <div className="actions"><button disabled={pages.length===1||query.isFetching} onClick={()=>page()}>Previous version page</button><button disabled={!query.data?.next||pages.length>=20||query.isFetching||query.isError} onClick={()=>page(query.data!.next!)}>Next version page</button><button disabled={query.isFetching} onClick={()=>{choose(null);void query.refetch()}}>Refresh candidate versions</button></div>
    {selected&&detail.isFetching?<p role="status">Validating candidate immutable evidence…</p>:null}{selected&&detail.isError?<p role="alert">{detail.error.message}</p>:null}
  </div>
}
function EdaJobView({pending,initial,onNew}:{pending:PendingEda;initial:EdaJob|null;onNew:()=>void}) {
  const uid=useId(),previous=useRef(initial),[until,setUntil]=useState(()=>Date.now()+EDA_POLL_WINDOW),[reportShown,setReportShown]=useState(false),[cancelBusy,setCancelBusy]=useState(false),[cancelError,setCancelError]=useState<Error|null>(null)
  const controller=useRef<AbortController|null>(null),mounted=useRef(true)
  useEffect(()=>{mounted.current=true;return()=>{mounted.current=false;controller.current?.abort()}},[])
  const path=`/eda-repairs/jobs/${encodeURIComponent(pending.jobId!)}`
  const query=useQuery({queryKey:['eda-job',uid,pending.jobId],queryFn:async({signal})=>{const next=await edaRequest(path,v=>compatibleEdaJob(previous.current,parseEdaJob(v,pending.binding,pending.jobId!)),{signal});previous.current=next;return next},retry:false,gcTime:0,staleTime:Infinity,refetchOnMount:'always',refetchInterval:q=>q.state.error?false:edaPollInterval(q.state.data,until)})
  const job=query.isSuccess&&!query.isFetching?query.data:undefined
  const report=useQuery({queryKey:['eda-job-report',uid,pending.jobId,job?.status,job?.cancel_requested,job?.result?.native_result_sha256],queryFn:({signal})=>edaRequest(`${path}/report`,v=>{const value=compatibleEdaJob(previous.current,parseEdaJob(v,pending.binding,pending.jobId!));if(JSON.stringify(value)!==JSON.stringify(job))throw new EdaRepairError(0,'Native result state changed. Refresh the selected job before inspecting its result.');return value},{signal}),enabled:reportShown&&!!job&&edaTerminal(job),retry:false,gcTime:0,staleTime:Infinity})
  async function cancel(){if(!job||edaTerminal(job)&&job.status!=='blocked'||cancelBusy)return;setCancelBusy(true);setCancelError(null);controller.current=new AbortController();try{const next=await edaRequest(`${path}/cancel`,v=>compatibleEdaJob(previous.current,parseEdaJob(v,pending.binding,pending.jobId!)),{method:'POST',signal:controller.current.signal});if(!mounted.current)return;previous.current=next;await query.refetch()}catch(e){if(mounted.current)setCancelError(e as Error)}finally{if(mounted.current)setCancelBusy(false)}}
  return <div className="ei-record"><h4>Native job <code>{pending.jobId}</code></h4><button disabled={query.isFetching||cancelBusy} onClick={()=>{setReportShown(false);setUntil(Date.now()+EDA_POLL_WINDOW);void query.refetch()}}>Refresh native job</button>
    {query.isFetching||query.isPending?<p role="status">Revalidating native job…</p>:null}{query.isError?<p role="alert">{query.error.message}</p>:null}
    {job?<><p role="status">Execution state: <strong>{humanize(job.status)}</strong> (<code>{job.status}</code>)</p><p>Design verdict: <strong>not_evaluated</strong>. Physical/electrical verdict: <strong>inconclusive</strong>.</p><p>Cancellation requested: {job.cancel_requested?'Yes':'No'}. Cancellation is not independent cleanup verification.</p>
      {job.blockers.length?<><h4>Execution blockers</h4><ul>{job.blockers.map(b=><li key={b}><code>{b}</code> · {humanize(b)}</li>)}</ul></>:null}
      {!edaTerminal(job)||job.status==='blocked'?<button disabled={cancelBusy||job.cancel_requested} onClick={()=>void cancel()}>Cancel selected native job</button>:null}
      {job.status==='quarantined'?<p className="notice">Quarantined work may have unknown native effects. This request and its exact key remain preserved; refresh this job or seek operator recovery before any replacement. Retained completion metadata does not resolve quarantine or establish acceptance.</p>:null}
      {!edaTerminal(job)?Date.now()>=until?<p role="status">Automatic polling stopped after five minutes. Refresh explicitly to inspect current state.</p>:null:<><button disabled={report.isFetching} onClick={()=>{if(reportShown)void report.refetch();else setReportShown(true)}}>Inspect native result metadata</button>{edaReplaceable(job)?<button onClick={()=>{if(edaReplaceable(job))onNew()}}>Start another native comparison</button>:null}</>}
    </>:null}{cancelError?<p role="alert">{cancelError.message}</p>:null}{job&&reportShown&&report.isFetching?<p role="status">Checking native result metadata…</p>:null}{job&&report.isError?<p role="alert">{report.error.message}</p>:null}
    {job&&reportShown&&report.isSuccess&&!report.isFetching?<div><h4>Retained native result metadata</h4><p>A receipt hash and retained/settled flags describe recorded execution metadata. They do not establish design success or physical/electrical acceptance.</p>{report.data.result?<dl className="ei-grid"><div><dt>Native receipt SHA-256</dt><dd><code>{report.data.result.native_result_sha256}</code></dd></div><div><dt>Ticket SHA-256</dt><dd><code>{report.data.result.ticket_hash}</code></dd></div><div><dt>Configuration SHA-256</dt><dd><code>{report.data.result.configuration_hash}</code></dd></div><div><dt>Recorded execution</dt><dd>{report.data.result.execution_status}</dd></div><div><dt>Retained / settled</dt><dd>True / True</dd></div></dl>:<p>No retained native completion result is available.</p>}</div>:null}
  </div>
}
