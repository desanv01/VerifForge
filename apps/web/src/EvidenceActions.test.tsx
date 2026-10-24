import { cleanup, render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { EvidenceActions } from './EvidenceActions'

const artifacts = {items:[
  {artifact_id:'artifact-stimulus',path:'stimulus.json',media_type:'application/json',size_bytes:123,sha256:'a'.repeat(64),downloadable:true},
  {artifact_id:'artifact-log',path:'simulator.log',media_type:'text/plain',size_bytes:456,sha256:'b'.repeat(64),downloadable:false},
]}
const queued = {id:'export-1',attempt_id:'db-attempt-1',status:'queued',error_type:null,sha256:null,size_bytes:null}
function mount(onReplay = vi.fn(), hasIdentity = true) {
  const client = new QueryClient({defaultOptions:{queries:{retry:false},mutations:{retry:false}}})
  render(<QueryClientProvider client={client}><EvidenceActions attemptId="db-attempt-1" hasIdentity={hasIdentity} onReplay={onReplay}/></QueryClientProvider>)
  return onReplay
}
function mockApi(override?: (url: string, init?: RequestInit) => unknown) {
  return vi.spyOn(globalThis,'fetch').mockImplementation(async (input, init) => {
    const url = String(input)
    const custom = override?.(url,init)
    if (custom instanceof Response) return custom
    const value = custom ?? (url === '/api/health' ? {status:'ok',evidence_api:'1'} : url.endsWith('/artifacts') ? artifacts : url.endsWith('/replay') ? {id:'replay-campaign',execution_status:'queued',verdict:null} : queued)
    return new Response(JSON.stringify(value),{status:200,headers:{'Content-Type':'application/json'}})
  })
}
afterEach(() => {cleanup();vi.restoreAllMocks();vi.unstubAllGlobals();history.replaceState(null,'','/')})
describe('evidence controls', () => {
  it('keeps old-server controls unavailable without probing commands', async () => {
    const calls = mockApi(url => url === '/api/health' ? {status:'ok'} : undefined)
    mount()
    await screen.findByText(/has not advertised evidence API support/)
    expect(screen.getByRole('button',{name:'Replay exact stimulus'})).toBeDisabled()
    expect(screen.getByRole('button',{name:'Prepare evidence bundle'})).toBeDisabled()
    expect(calls.mock.calls.map(([url]) => url)).toEqual(['/api/health'])
  })
  it('shows portable allowlist downloads separately from metadata and queues exact replay using DB ID', async () => {
    const calls = mockApi(); const onReplay = mount(); const user = userEvent.setup()
    await screen.findByText('stimulus.json')
    expect(screen.getByRole('button',{name:'Download stimulus.json'})).toBeEnabled()
    expect(screen.queryByRole('button',{name:'Download simulator.log'})).not.toBeInTheDocument()
    expect(screen.getByText('Metadata only')).toBeInTheDocument()
    await user.click(screen.getByRole('button',{name:'Replay exact stimulus'}))
    await screen.findByRole('button',{name:'Replay exact stimulus'})
    expect(onReplay).toHaveBeenCalledWith(expect.objectContaining({id:'replay-campaign',execution_status:'queued',verdict:null}))
    const call = calls.mock.calls.find(([url]) => url === '/api/attempts/db-attempt-1/replay')!
    expect(call[1]?.headers).toHaveProperty('Idempotency-Key')
    expect(call[1]?.body).toBeUndefined()
  })
  it('restores completed durable export and offers download only after published hash and size', async () => {
    history.replaceState(null,'','/?export=export-1')
    mockApi(url => url === '/api/exports/export-1' ? {...queued,status:'completed',sha256:'c'.repeat(64),size_bytes:1024} : undefined)
    mount()
    await screen.findByRole('button',{name:'Download evidence bundle'})
    expect(screen.getByText('completed',{exact:true})).toBeInTheDocument()
    expect(screen.getByText('Verified bundle · 1,024 bytes')).toBeInTheDocument()
    expect(screen.queryByRole('button',{name:'Cancel export'})).not.toBeInTheDocument()
  })
  it('creates and cancels a queued export without treating it as a download', async () => {
    const calls = mockApi(url => url.endsWith('/cancel') ? {...queued,status:'cancelled'} : undefined)
    mount(); const user = userEvent.setup()
    await screen.findByText('stimulus.json')
    await user.click(screen.getByRole('button',{name:'Prepare evidence bundle'}))
    await screen.findByRole('button',{name:'Cancel export'})
    expect(location.search).toContain('export=export-1')
    expect(screen.queryByRole('button',{name:'Download evidence bundle'})).not.toBeInTheDocument()
    await user.click(screen.getByRole('button',{name:'Cancel export'}))
    await screen.findByText('cancelled',{exact:true})
    expect(screen.getByRole('button',{name:'Prepare new evidence bundle'})).toBeEnabled()
    expect(calls.mock.calls.find(([url]) => url === '/api/exports/export-1/cancel')?.[1]?.body).toBeUndefined()
  })
  it('shows a server download rejection inline without creating a download', async () => {
    const calls = mockApi(url => url.endsWith('/download') ? new Response(JSON.stringify({detail:'Evidence hash changed'}),{status:409}) : undefined)
    mount(); const user = userEvent.setup()
    await screen.findByText('stimulus.json')
    await user.click(screen.getByRole('button',{name:'Download stimulus.json'}))
    await screen.findByText('API 409: Evidence hash changed')
    expect(calls.mock.calls.some(([url]) => url === '/api/attempts/db-attempt-1/artifacts/artifact-stimulus/download')).toBe(true)
  })
  it('downloads verified bytes with a generated filename only after an explicit click', async () => {
    const NativeURL = URL
    const createURL = vi.fn().mockReturnValue('blob:verified-evidence')
    vi.stubGlobal('URL', class extends NativeURL {static createObjectURL = createURL; static revokeObjectURL = vi.fn()})
    const click = vi.spyOn(HTMLAnchorElement.prototype,'click').mockImplementation(() => undefined)
    mockApi(url => url.endsWith('/download') ? new Response('{"operations":[]}',{status:200}) : undefined)
    mount(); const user = userEvent.setup(); await screen.findByText('stimulus.json')
    expect(createURL).not.toHaveBeenCalled()
    await user.click(screen.getByRole('button',{name:'Download stimulus.json'}))
    await screen.findByText('Verified download requested.')
    expect(createURL).toHaveBeenCalledOnce()
    const anchor = click.mock.instances[0] as HTMLAnchorElement
    expect(anchor.download).toBe('artifact-artifact-stimulus')
    expect(anchor.href).toBe('blob:verified-evidence')
  })
  it('restores a failed export honestly and offers a fresh job request', async () => {
    history.replaceState(null,'','/?export=export-1')
    mockApi(url => url === '/api/exports/export-1' ? {...queued,status:'failed',error_type:'EvidenceIntegrityError'} : undefined)
    mount()
    await screen.findByText('Export could not complete: EvidenceIntegrityError. Original evidence is unchanged.')
    expect(screen.getByRole('button',{name:'Prepare new evidence bundle'})).toBeEnabled()
    expect(screen.queryByRole('button',{name:'Download evidence bundle'})).not.toBeInTheDocument()
  })
  it('does not replay attempts without a checked identity', async () => {
    mockApi(); mount(vi.fn(),false)
    await screen.findByText(/has no complete checked identity/)
    expect(screen.getByRole('button',{name:'Replay exact stimulus'})).toBeDisabled()
  })
  it('keeps an exact replay retry key after a lost response', async () => {
    const calls = mockApi(); const original = calls.getMockImplementation()!; let count = 0
    calls.mockImplementation(async (input,init) => {
      if (String(input).endsWith('/replay') && ++count === 1) throw new TypeError('network')
      return original(input,init)
    })
    mount(); const user = userEvent.setup(); await screen.findByText('stimulus.json')
    await user.click(screen.getByRole('button',{name:'Replay exact stimulus'}))
    await screen.findByText(/local API is unreachable/)
    await user.click(screen.getByRole('button',{name:'Replay exact stimulus'}))
    const replays = calls.mock.calls.filter(([url]) => String(url).endsWith('/replay'))
    expect(replays).toHaveLength(2); expect(replays[0][1]?.headers).toEqual(replays[1][1]?.headers)
  })
})
