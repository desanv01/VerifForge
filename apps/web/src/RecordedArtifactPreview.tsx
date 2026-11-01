import { useEffect, useRef, useState } from 'react'
import { artifactHref } from './engineeringInvestigationApi'
import type { Artifact, VersionSummary } from './engineeringInvestigationApi'

export const TEXT_PREVIEW_LIMIT = 65536

/** Read only exact admitted bytes. Treat all content as text, never HTML or code. */
export function RecordedArtifactPreview({ detail, artifact, label = 'Preview recorded text' }: {
  detail: VersionSummary; artifact: Artifact; label?: string
}) {
  const life = useRef({ active: false, epoch: 0, controller: null as AbortController | null }).current
  const [state, setState] = useState<{ loading: boolean; text?: string; error?: string }>({ loading: false })
  useEffect(() => {
    life.active = true
    return () => { life.active = false; life.epoch++; life.controller?.abort() }
  }, [life])
  async function preview() {
    if (state.loading || artifact.size > TEXT_PREVIEW_LIMIT) return
    const epoch = ++life.epoch, controller = new AbortController()
    life.controller?.abort(); life.controller = controller
    const current = () => life.active && life.epoch === epoch && !controller.signal.aborted
    setState({ loading: true })
    try {
      const response = await fetch(artifactHref(detail, artifact), {
        method: 'GET', cache: 'no-store', signal: AbortSignal.any([controller.signal, AbortSignal.timeout(10000)]),
      })
      if (!response.ok || !response.body) throw new Error()
      const reader = response.body.getReader(), chunks: Uint8Array[] = []; let length = 0
      try {
        while (true) {
          const { done, value } = await reader.read(); if (done) break
          length += value.byteLength
          if (length > TEXT_PREVIEW_LIMIT || length > artifact.size) { await reader.cancel(); throw new Error() }
          chunks.push(value)
        }
      } finally { reader.releaseLock() }
      if (length !== artifact.size) throw new Error()
      const bytes = new Uint8Array(length); let offset = 0
      for (const chunk of chunks) { bytes.set(chunk, offset); offset += chunk.length }
      const digest = Array.from(new Uint8Array(await crypto.subtle.digest('SHA-256', bytes)), v => v.toString(16).padStart(2, '0')).join('')
      if (digest !== artifact.sha256) throw new Error()
      const text = new TextDecoder('utf-8', { fatal: true }).decode(bytes)
      if (current()) setState({ loading: false, text })
    } catch {
      if (current()) setState({ loading: false, error: 'Recorded text is unavailable or failed byte, hash or UTF-8 validation. Exact artifact download remains available.' })
    }
  }
  return <div className="recorded-preview">
    <button disabled={state.loading || artifact.size > TEXT_PREVIEW_LIMIT} onClick={() => void preview()}>{state.loading ? 'Checking recorded bytes…' : label}</button>
    <p className="muted">Plain-text preview only, at most 64 KiB. No source is executed or applied.</p>
    {artifact.size > TEXT_PREVIEW_LIMIT ? <p>Artifact exceeds the preview limit. Download exact bytes instead.</p> : null}
    {state.error ? <p role="alert">{state.error}</p> : null}
    {state.text !== undefined ? <pre tabIndex={0} aria-label="Verified recorded text">{state.text}</pre> : null}
  </div>
}

/** Changing the immutable identity always retires pending requests and old text. */
export function ArtifactPreview(props: Parameters<typeof RecordedArtifactPreview>[0]) {
  return <RecordedArtifactPreview key={`${props.detail.id}:${props.detail.contentHash}:${props.artifact.id}:${props.artifact.sha256}`} {...props} />
}
