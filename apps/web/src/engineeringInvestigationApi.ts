// Read-only projection of the recorded-investigation GET API. Native payloads
// and free-form source text deliberately never enter the presentation model.
import { parseInspectionJson } from './json'

export const MAX_RESPONSE_BYTES = 4 * 1024 * 1024
const domains = ['rtl', 'riscv', 'firmware', 'vivado', 'kicad', 'system'] as const
const roles = ['project_revision', 'manifest', 'bridge_report', 'project_source', 'native_fact', 'provenance_source', 'relation_evidence', 'capability_evidence', 'attempt_result', 'attempt_artifact'] as const
export class InvestigationError extends Error {
  constructor(public status: number, message: string) { super(message) }
}
function invalid(): never { throw new InvestigationError(0, 'Investigation metadata is invalid or inconsistent. No evidence was interpreted.') }
function obj(v: unknown): Record<string, unknown> { if (!v || typeof v !== 'object' || Array.isArray(v)) invalid(); return v as Record<string, unknown> }
function text(v: unknown, max = 128): string { if (typeof v !== 'string' || !v.length || v.length > max) invalid(); return v }
export function identifier(v: unknown): string { const s = text(v); if (!/^[A-Za-z0-9][A-Za-z0-9_.:-]*$/.test(s)) invalid(); return s }
function hash(v: unknown): string { const s = text(v, 64); if (!/^[a-f0-9]{64}$/.test(s)) invalid(); return s }
function num(v: unknown, max = Number.MAX_SAFE_INTEGER, min = 0): number { if (typeof v !== 'number' || !Number.isSafeInteger(v) || v < min || v > max) invalid(); return v }
function one<T extends string>(v: unknown, values: readonly T[]): T { if (!values.includes(v as T)) invalid(); return v as T }
function literal(v: unknown, expected: string) { if (v !== expected) invalid() }
function list<T>(v: unknown, max: number, parse: (v: unknown) => T): T[] { if (!Array.isArray(v) || v.length > max) invalid(); return v.map(parse) }
function unique<T>(rows: T[], key: (v: T) => string): T[] { if (new Set(rows.map(key)).size !== rows.length) invalid(); return rows }
function countText(v: unknown): number { return list(v, 32, v => text(v, 1000)).length }
function date(v: unknown): string { const s = text(v, 64); if (!/^\d{4}-\d\d-\d\dT[\d:.]+(?:Z|[+-]\d\d:\d\d)$/.test(s) || !Number.isFinite(Date.parse(s))) invalid(); return s }
export interface Reference { artifactId: string; revisionId: string; attemptId: string | null; sha256: string }
function reference(v: unknown): Reference { const o = obj(v); return { artifactId: identifier(o.artifact_id), revisionId: identifier(o.revision_id), attemptId: o.attempt_id === null ? null : identifier(o.attempt_id), sha256: hash(o.sha256) } }
export const referenceKey = (r: Reference) => JSON.stringify([r.artifactId, r.revisionId, r.attemptId])
function equalRef(a: Reference, b: Reference) { return referenceKey(a) === referenceKey(b) && a.sha256 === b.sha256 }
export interface VersionSummary { id: string; investigationId: string; projectId: string; ordinal: number; contentHash: string; revisionId: string; revisionHash: string; manifestHash: string; createdAt: string; domains: string[]; nativeId: string }
export interface Investigation { id: string; projectId: string; createdAt: string; latest: VersionSummary }
export interface Page<T, C> { items: T[]; next: C | null }
function summary(v: unknown, projectId: string, investigationId?: string): VersionSummary {
  const o = obj(v); literal(o.project_id, projectId); if (investigationId) literal(o.investigation_id, investigationId)
  literal(o.scope, 'recorded_import'); literal(o.execution_status, 'not_run'); literal(o.schema_version, 'engineering.admission.v1')
  return { id: identifier(o.id), investigationId: identifier(o.investigation_id), projectId, ordinal: num(o.ordinal, Number.MAX_SAFE_INTEGER, 1), contentHash: hash(o.content_hash), revisionId: identifier(o.revision_id), revisionHash: hash(o.revision_hash), manifestHash: hash(o.manifest_hash), createdAt: date(o.created_at), domains: unique(list(o.domains, 6, v => one(v, domains)), v => v), nativeId: identifier(o.native_investigation_id) }
}
export function parseInvestigations(v: unknown, projectId: string): Page<Investigation, string> {
  const o = obj(v)
  return { items: unique(list(o.items, 50, v => { const row = obj(v); literal(row.project_id, projectId); const id = identifier(row.id); return { id, projectId, createdAt: date(row.created_at), latest: summary(row.latest_version, projectId, id) } }), v => v.id), next: o.next_cursor === null ? null : identifier(o.next_cursor) }
}
export function parseVersions(v: unknown, projectId: string, investigationId: string): Page<VersionSummary, number> {
  const o = obj(v), items = unique(list(o.items, 50, v => summary(v, projectId, investigationId)), v => v.id)
  if (items.some((row, i) => i > 0 && row.ordinal <= items[i - 1].ordinal)) invalid()
  return { items, next: o.next_cursor === null ? null : num(o.next_cursor, Number.MAX_SAFE_INTEGER, 1) }
}
export interface Artifact extends Reference { id: string; size: number; roles: string[] }
interface ArtifactIdentity extends Reference { size: number; roles: string[] }
function artifactIdentity(v: unknown): ArtifactIdentity { const o = obj(v); return { ...reference(o.reference), size: num(o.size_bytes, 1048576), roles: unique(list(o.roles, 10, v => one(v, roles)), v => v) } }
export interface Fact { id: string; domain: string; schema: string; category: string; review: string; provenance: string; method: string | null; implementationHash: string | null; sources: Reference[]; parents: string[]; limitations: number; reference: Reference }
export interface Relation { id: string; kind: string; facts: string[]; requirements: string[]; verdict: string; comparison: string; basis: string; dependence: string; limitations: number; evidence: Reference[]; checkerHash: string | null }
export interface UnresolvedComparison { bindingId: string; facts: string[] }
export interface ExecutionContext { campaignId: string; dbAttemptId: string; experimentRecordId: string; nativeAttemptId: string; wireExperimentId: string; result: Reference; origin: 'context_only' | 'retained_execution_bytes' }
export interface InvestigationDetail extends VersionSummary { configurationHash: string; sources: Reference[]; facts: Fact[]; relations: Relation[]; unresolvedComparisons: UnresolvedComparison[]; contexts: ExecutionContext[]; artifacts: ArtifactIdentity[]; totalBytes: number; manifestReview: string; bridgeFamily: string; bridgeCheckerHash: string }
export function parseDetail(v: unknown, selected: VersionSummary): InvestigationDetail {
  const o = obj(v), s = summary(o, selected.projectId, selected.investigationId)
  const identityFields = ['id', 'investigationId', 'projectId', 'ordinal', 'contentHash', 'revisionId', 'revisionHash', 'manifestHash', 'createdAt', 'nativeId'] as const
  if (identityFields.some(field => s[field] !== selected[field]) || [...s.domains].sort().join() !== [...selected.domains].sort().join()) invalid()
  const e = obj(o.envelope), r = obj(e.record), p = obj(o.project_revision), m = obj(o.manifest)
  literal(e.schema_version, 'engineering.admission.v1'); literal(e.operation, 'admission_only'); literal(e.new_execution, 'not_run')
  literal(e.claim_scope, 'resolved_bytes_and_typed_consistency_only'); literal(e.attempt_context_scope, 'host_pinned_association_not_execution_origin')
  literal(r.schema_version, 'engineering.investigation.v1'); literal(r.project_id, s.projectId); literal(r.revision_id, s.revisionId); literal(r.investigation_id, s.nativeId)
  literal(p.schema_version, 'engineering.project-revision.v1'); literal(p.project_id, s.projectId); literal(p.revision_id, s.revisionId)
  literal(m.schema_version, 'engineering.manifest.v1'); hash(e.record_hash)
  const revision = reference(r.project_revision_ref), manifest = reference(r.manifest_ref), bridge = reference(r.bridge_report_ref)
  if (revision.sha256 !== s.revisionHash || manifest.sha256 !== s.manifestHash) invalid()
  const sources = unique(list(p.source_refs, 64, reference), referenceKey), configurationHash = hash(p.configuration_hash)
  if (!sources.some(ref => ref.sha256 === configurationHash)) invalid()
  const artifacts = unique(list(e.artifacts, 256, artifactIdentity), referenceKey), totalBytes = num(e.total_artifact_bytes, 16777216)
  if (artifacts.reduce((sum, a) => sum + a.size, 0) !== totalBytes) invalid()
  function retained(ref: Reference) { if (!artifacts.some(a => equalRef(a, ref))) invalid() }
  ;[revision, manifest, bridge, ...sources].forEach(retained)
  const facts = unique(list(m.facts, 256, v => {
    const f = obj(v), provenance = obj(f.provenance), extraction = provenance.extraction === null ? null : obj(provenance.extraction)
    const ref = reference(f.native_record); if (hash(f.native_record_hash) !== ref.sha256) invalid(); retained(ref)
    const refs = list(provenance.sources, 32, v => reference(obj(v).artifact)); refs.forEach(retained)
    return { id: identifier(f.fact_id), domain: one(f.domain, domains), schema: identifier(f.native_schema_id), category: one(f.category, ['observation', 'requirement', 'hypothesis']), review: one(f.review_state, ['reviewed', 'unreviewed']), provenance: one(provenance.state, ['complete', 'ambiguous', 'missing']), method: extraction ? one(extraction.method, ['native_parser', 'tool_report', 'manual_review', 'model_proposal', 'derived']) : null, implementationHash: extraction ? hash(extraction.implementation_hash) : null, sources: refs, parents: list(provenance.derived_from, 32, identifier), limitations: countText(provenance.uncertainties) + countText(f.conditions), reference: ref }
  }), f => f.id)
  const factIds = new Set(facts.map(f => f.id)); if (!facts.length) invalid()
  if ([...new Set(facts.map(f => f.domain))].sort().join() !== [...s.domains].sort().join()) invalid()
  for (const fact of facts) if (fact.parents.some(id => !factIds.has(id))) invalid()
  const attachments = list(r.native_records, 128, obj)
  if (attachments.length !== facts.length || attachments.some((a, index) => a.fact_id !== facts[index].id || a.domain !== facts[index].domain || a.native_schema_id !== facts[index].schema || !equalRef(reference(a.reference), facts[index].reference))) invalid()
  const bridgeReport = obj(o.bridge_report)
  literal(bridgeReport.schema_version, 'system.bridge.v1'); literal(bridgeReport.execution_status, 'not_run')
  const bridgeFamily = one(bridgeReport.family, ['firmware_decode', 'board_connector']), bridgeCheckerHash = hash(bridgeReport.checker_hash)
  const comparisons = unique(list(bridgeReport.comparisons, 16, v => {
    const row = obj(v), bindingId = identifier(row.binding_id), ids = unique(list(row.fact_ids, 3, identifier), v => v)
    if (ids.some(id => !factIds.has(id))) invalid()
    const value = one(row.value_comparison, ['consistent', 'conflict', 'inconclusive']), verdict = one(row.verdict, ['consistent', 'conflict', 'inconclusive'])
    if (ids.length < 2 && (value !== 'inconclusive' || verdict !== 'inconclusive')) invalid()
    return { id: `relation-${bindingId}`, bindingId, facts: ids, value, verdict }
  }), row => row.id)
  const relations = unique(list(m.relations, 256, v => {
    const row = obj(v), refs = list(row.evidence, 64, reference); refs.forEach(retained)
    const ids = unique(list(row.fact_ids, 16, identifier), v => v), requirements = unique(list(row.requirement_fact_ids, 32, identifier), v => v)
    if (ids.length < 2 || [...ids, ...requirements].some(id => !factIds.has(id))) invalid()
    const comparison = comparisons.find(c => c.id === row.relation_id)
    if (!comparison || comparison.facts.join() !== ids.join() || comparison.verdict !== row.verdict) invalid()
    if (row.basis === 'independent_check' && (row.dependence !== 'independent' || !row.checker || !refs.length)) invalid()
    return { id: identifier(row.relation_id), kind: one(row.relation, ['maps_to', 'same_signal', 'same_address', 'same_clock', 'connects_to', 'satisfies']), facts: ids, requirements, verdict: one(row.verdict, ['consistent', 'conflict', 'inconclusive']), comparison: comparison.value, basis: one(row.basis, ['hypothesis', 'reviewed', 'independent_check']), dependence: one(row.dependence, ['independent', 'same_source', 'unknown']), limitations: countText(row.uncertainties), evidence: refs, checkerHash: row.checker === null ? null : hash(obj(row.checker).sha256) }
  }), r => r.id)
  if (comparisons.filter(comparison => comparison.facts.length >= 2).length !== relations.length) invalid()
  // Missing/unsupported extraction still yields a comparison, but cannot form
  // a relationship. Preserve that uncertainty without inventing endpoints.
  const unresolvedComparisons = comparisons.filter(comparison => comparison.facts.length < 2).map(({ bindingId, facts }) => ({ bindingId, facts }))
  const contexts = unique(list(o.execution_contexts, 32, v => {
    const row = obj(v), b = obj(row.binding), result = reference(b.result_ref); retained(result)
    const nativeAttemptId = identifier(b.native_attempt_id); if (result.attemptId !== nativeAttemptId) invalid()
    return { campaignId: identifier(b.campaign_id), dbAttemptId: identifier(b.db_attempt_id), experimentRecordId: identifier(b.experiment_record_id), nativeAttemptId, wireExperimentId: identifier(b.wire_experiment_id), result, origin: one(row.origin_at_admission, ['context_only', 'retained_execution_bytes'] as const) }
  }), c => c.dbAttemptId)
  const attempts = list(r.attempts, 32, obj)
  if (attempts.length !== contexts.length || attempts.some(a => !contexts.some(c => c.dbAttemptId === a.db_attempt_id && c.campaignId === a.campaign_id && c.nativeAttemptId === a.native_attempt_id && c.experimentRecordId === a.experiment_record_id && c.wireExperimentId === a.wire_experiment_id && equalRef(c.result, reference(a.result_ref))))) invalid()
  // Raw bridge/native payloads and free-form reasons never enter UI state.
  return { ...s, configurationHash, sources, artifacts, totalBytes, facts, relations, unresolvedComparisons, contexts, bridgeFamily, bridgeCheckerHash, manifestReview: one(m.review_state, ['draft', 'reviewed']) }
}
export function parseArtifacts(v: unknown, detail: InvestigationDetail): Artifact[] {
  const items = unique(list(obj(v).items, 256, v => {
    const o = obj(v); literal(o.media_type, 'application/octet-stream')
    return { ...reference(o), id: identifier(o.id), size: num(o.size_bytes, 1048576), roles: unique(list(o.roles, 10, v => one(v, roles)), v => v) }
  }), a => a.id)
  unique(items, referenceKey)
  if (items.length !== detail.artifacts.length || items.some(a => !detail.artifacts.some(b => equalRef(a, b) && a.size === b.size && [...a.roles].sort().join() === [...b.roles].sort().join()))) invalid()
  return items
}
export const versionPath = (investigationId: string, versionId: string) => `/investigations/${encodeURIComponent(identifier(investigationId))}/versions/${encodeURIComponent(identifier(versionId))}`
export const artifactHref = (detail: VersionSummary, artifact: Artifact) => `/api${versionPath(detail.investigationId, detail.id)}/artifacts/${encodeURIComponent(identifier(artifact.id))}/download`

export async function investigationGet<T>(path: string, parse: (value: unknown) => T, signal?: AbortSignal): Promise<T> {
  let response: Response
  try { response = await fetch(`/api${path}`, { method: 'GET', signal: signal ? AbortSignal.any([signal, AbortSignal.timeout(10000)]) : AbortSignal.timeout(10000), cache: 'no-store', headers: { Accept: 'application/json' } }) }
  catch { throw new InvestigationError(0, 'The local investigation API is unavailable. Retry when it is reachable.') }
  if (!response.ok) {
    await response.body?.cancel()
    throw new InvestigationError(response.status, response.status === 404 ? 'This investigation or version is missing or unavailable to this project. Refresh the list.' : response.status === 409 ? 'Stored investigation evidence is corrupt, changed or unavailable. No evidence was interpreted.' : `Investigation request failed (HTTP ${response.status}). Retry the request.`)
  }
  const reader = response.body?.getReader(); if (!reader) invalid()
  let size = 0, raw = ''; const decoder = new TextDecoder('utf-8', { fatal: true })
  try {
    while (true) { const { done, value } = await reader.read(); if (done) break; size += value.byteLength; if (size > MAX_RESPONSE_BYTES) invalid(); raw += decoder.decode(value, { stream: true }) }
    raw += decoder.decode()
    return parse(parseInspectionJson(raw))
  } catch { await reader.cancel().catch(() => undefined); invalid() }
  finally { reader.releaseLock() }
}
