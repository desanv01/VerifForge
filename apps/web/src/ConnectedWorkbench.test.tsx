// Synthetic presentation fixtures only; no native execution or admission.
import { webcrypto, createHash } from 'node:crypto'
import { act, cleanup, render, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, expect, it, vi } from 'vitest'
import { ArtifactPreview } from './RecordedArtifactPreview'
import { DomainEvidenceNavigator } from './DomainEvidenceNavigator'
import { WorkbenchShell } from './WorkbenchShell'
import type { Artifact, InvestigationDetail, VersionSummary } from './engineeringInvestigationApi'

const hash = 'a'.repeat(64)
const version: VersionSummary = { id: 'version-1', investigationId: 'investigation-1', projectId: 'project-1', ordinal: 1, contentHash: hash, revisionId: 'revision-1', revisionHash: hash, manifestHash: hash, createdAt: '2026-11-01T00:00:00Z', domains: ['kicad', 'vivado'], nativeId: 'native-1' }
function artifact(text: string): Artifact { return { id: 'blob-1', artifactId: 'source-1', revisionId: 'revision-1', attemptId: null, sha256: createHash('sha256').update(text).digest('hex'), size: new TextEncoder().encode(text).length, roles: ['native_fact'] } }
afterEach(() => { cleanup(); vi.restoreAllMocks(); vi.unstubAllGlobals(); localStorage.clear() })

it('previews exact bounded text on demand without interpreting HTML or guessing binary facts', async () => {
  vi.stubGlobal('crypto', webcrypto)
  const text = '<script>bad()</script>\naddress=0x40000004', file = artifact(text)
  const fetch = vi.spyOn(globalThis, 'fetch').mockResolvedValue(new Response(text))
  render(<ArtifactPreview detail={version} artifact={file}/>)
  expect(fetch).not.toHaveBeenCalled()
  await userEvent.click(screen.getByRole('button', { name: 'Preview recorded text' }))
  expect(await screen.findByLabelText('Verified recorded text')).toHaveTextContent('address=0x40000004')
  expect(document.querySelector('script')).toBeNull()
  expect(fetch.mock.calls[0][1]).toMatchObject({ method: 'GET', cache: 'no-store' })
})

it('retires preview content and ignores a late response on immutable version change', async () => {
  vi.stubGlobal('crypto', webcrypto)
  const text = 'original', file = artifact(text); let resolve!: (r: Response) => void
  vi.spyOn(globalThis, 'fetch').mockImplementation(() => new Promise<Response>(done => { resolve = done }))
  const view = render(<ArtifactPreview detail={version} artifact={file}/>)
  await userEvent.click(screen.getByRole('button', { name: 'Preview recorded text' }))
  view.rerender(<ArtifactPreview detail={{ ...version, id: 'version-2', contentHash: 'b'.repeat(64) }} artifact={file}/>)
  await act(async () => { resolve(new Response(text)); await new Promise(done => setTimeout(done, 10)) })
  expect(screen.queryByLabelText('Verified recorded text')).not.toBeInTheDocument()
  expect(screen.getByRole('button', { name: 'Preview recorded text' })).toBeEnabled()
})

it('withholds hash-mismatched or overlong text and preserves the exact download scope', async () => {
  vi.stubGlobal('crypto', webcrypto)
  vi.spyOn(globalThis, 'fetch').mockResolvedValue(new Response('wrong'))
  const view = render(<ArtifactPreview detail={version} artifact={artifact('right')}/>)
  await userEvent.click(screen.getByRole('button', { name: 'Preview recorded text' }))
  await screen.findByRole('alert'); expect(screen.queryByLabelText('Verified recorded text')).not.toBeInTheDocument()
  view.rerender(<ArtifactPreview detail={version} artifact={{ ...artifact('right'), id: 'huge', size: 65537 }}/>)
  expect(screen.getByRole('button', { name: 'Preview recorded text' })).toBeDisabled()
})

it('keeps domain selection through theme changes and separates literal conflicts from verdicts', async () => {
  const file = artifact('fact')
  const detail: InvestigationDetail = { ...version, configurationHash: hash, sources: [], facts: [{ id: 'pin-J1', domain: 'kicad', schema: 'pin.v1', category: 'observation', review: 'unreviewed', provenance: 'complete', method: null, implementationHash: null, sources: [], parents: [], limitations: 1, reference: file }], relations: [{ id: 'mapping-1', kind: 'maps_to', facts: ['pin-J1'], requirements: [], verdict: 'inconclusive', comparison: 'conflict', basis: 'reviewed', dependence: 'same_source', limitations: 1, evidence: [], checkerHash: null }], unresolvedComparisons: [], contexts: [], artifacts: [], totalBytes: 0, manifestReview: 'draft', bridgeFamily: 'board_connector', bridgeCheckerHash: hash }
  render(<WorkbenchShell project="Board"><DomainEvidenceNavigator detail={detail} artifacts={[file]} factAnchor={id => id}/></WorkbenchShell>)
  const region = screen.getByRole('region', { name: 'Connected relationship comparisons' })
  expect(within(region).getByText('conflict')).toBeInTheDocument(); expect(within(region).getByText('inconclusive')).toBeInTheDocument()
  const firmware = screen.getByRole('button', { name: /Firmware \/ MMIO/ })
  await userEvent.click(firmware)
  expect(screen.getByText(/No Firmware \/ MMIO facts are admitted/)).toBeInTheDocument()
  await userEvent.click(screen.getByRole('button', { name: 'Dark theme' }))
  expect(firmware).toHaveAttribute('aria-pressed', 'true')
  await userEvent.click(screen.getByRole('button', { name: /KiCad/ }))
  await waitFor(() => expect(screen.getByRole('link', { name: 'Download native fact pin-J1' })).toHaveAttribute('href', '/api/investigations/investigation-1/versions/version-1/artifacts/blob-1/download'))
})
