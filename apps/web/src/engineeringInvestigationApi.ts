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
export type DiscoveryMode = 'v1' | 'all'
export type NativeReportKind = 'riscv.recorded-report.v1' | 'firmware.recorded-report.v1'
export interface VersionSummary { id: string; investigationId: string; projectId: string; ordinal: number; contentHash: string; revisionId: string; revisionHash: string; manifestHash: string; createdAt: string; domains: string[]; nativeId: string; schemaVersion?: 'engineering.admission.v1' | 'engineering.admission.v2'; reportKind?: NativeReportKind }
export interface Investigation { id: string; projectId: string; createdAt: string; latest: VersionSummary }
export interface Page<T, C> { items: T[]; next: C | null }
function summary(v: unknown, projectId: string, investigationId?: string, mode: DiscoveryMode = 'v1'): VersionSummary {
  const o = obj(v); literal(o.project_id, projectId); if (investigationId) literal(o.investigation_id, investigationId)
  literal(o.scope, 'recorded_import'); literal(o.execution_status, 'not_run')
  const schemaVersion = one(o.schema_version, mode === 'all' ? ['engineering.admission.v1', 'engineering.admission.v2'] as const : ['engineering.admission.v1'] as const)
  const reportKind = schemaVersion === 'engineering.admission.v2' ? one(o.report_kind, nativeKinds) : undefined
  const parsedDomains = unique(list(o.domains, 6, v => one(v, domains)), v => v)
  if (reportKind && (parsedDomains.length !== 1 || parsedDomains[0] !== (reportKind === nativeKinds[0] ? 'riscv' : 'firmware'))) invalid()
  return { id: identifier(o.id), investigationId: identifier(o.investigation_id), projectId, ordinal: num(o.ordinal, Number.MAX_SAFE_INTEGER, 1), contentHash: hash(o.content_hash), revisionId: identifier(o.revision_id), revisionHash: hash(o.revision_hash), manifestHash: hash(o.manifest_hash), createdAt: date(o.created_at), domains: parsedDomains, nativeId: identifier(o.native_investigation_id), schemaVersion, ...(reportKind ? { reportKind } : {}) }
}
export function parseInvestigations(v: unknown, projectId: string, mode: DiscoveryMode = 'v1'): Page<Investigation, string> {
  const o = obj(v)
  return { items: unique(list(o.items, 50, v => { const row = obj(v); literal(row.project_id, projectId); const id = identifier(row.id); return { id, projectId, createdAt: date(row.created_at), latest: summary(row.latest_version, projectId, id, mode) } }), v => v.id), next: o.next_cursor === null ? null : identifier(o.next_cursor) }
}
export function parseVersions(v: unknown, projectId: string, investigationId: string, mode: DiscoveryMode = 'v1'): Page<VersionSummary, number> {
  const o = obj(v), items = unique(list(o.items, 50, v => summary(v, projectId, investigationId, mode)), v => v.id)
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
export function parseArtifacts(v: unknown, detail: InvestigationDetail | NativeInvestigationDetail): Artifact[] {
  const items = unique(list(obj(v).items, 256, v => {
    const o = obj(v); literal(o.media_type, 'application/octet-stream')
    return { ...reference(o), id: identifier(o.id), size: num(o.size_bytes, 1048576), roles: unique(list(o.roles, detail.reportKind ? 12 : 10, v => one(v, detail.reportKind ? nativeRoles : roles)), v => v) }
  }), a => a.id)
  unique(items, referenceKey)
  if (items.length !== detail.artifacts.length || items.some(a => !detail.artifacts.some(b => equalRef(a, b) && a.size === b.size && [...a.roles].sort().join() === [...b.roles].sort().join()))) invalid()
  return items
}
export const versionPath = (investigationId: string, versionId: string) => `/investigations/${encodeURIComponent(identifier(investigationId))}/versions/${encodeURIComponent(identifier(versionId))}`
export const artifactHref = (detail: VersionSummary, artifact: Artifact) => `/api${versionPath(detail.investigationId, detail.id)}/artifacts/${encodeURIComponent(identifier(artifact.id))}/download`

export async function investigationGet<T>(path: string, parse: (value: unknown) => T, signal?: AbortSignal): Promise<T> {
  const requestSignal = signal ? AbortSignal.any([signal, AbortSignal.timeout(10000)]) : AbortSignal.timeout(10000)
  let response: Response
  try { if (requestSignal.aborted) throw new Error(); response = await fetch(`/api${path}`, { method: 'GET', signal: requestSignal, cache: 'no-store', headers: { Accept: 'application/json' } }) }
  catch { throw new InvestigationError(0, 'The local investigation API is unavailable. Retry when it is reachable.') }
  if (requestSignal.aborted) { await response.body?.cancel().catch(() => undefined); invalid() }
  if (!response.ok) {
    await response.body?.cancel()
    throw new InvestigationError(response.status, response.status === 404 ? 'This investigation or version is missing or unavailable to this project. Refresh the list.' : response.status === 409 ? 'Stored investigation evidence is corrupt, changed or unavailable. No evidence was interpreted.' : `Investigation request failed (HTTP ${response.status}). Retry the request.`)
  }
  const reader = response.body?.getReader(); if (!reader) invalid()
  let size = 0, raw = ''; const decoder = new TextDecoder('utf-8', { fatal: true })
  try {
    while (true) { const { done, value } = await reader.read(); if (done) break; size += value.byteLength; if (size > MAX_RESPONSE_BYTES) invalid(); raw += decoder.decode(value, { stream: true }) }
    raw += decoder.decode()
    if (requestSignal.aborted) invalid()
    return parse(parseInvestigationJson(raw))
  } catch { await reader.cancel().catch(() => undefined); invalid() }
  finally { reader.releaseLock() }
}

// Validate object-key uniqueness before any capability, identity or policy is
// interpreted. Decode key escapes so "key" and "\u006bey" are the same key.
// This scanner retains no numeric values; the shared inspection parser remains
// responsible for preserving unsafe numeric lexemes and exact decimal strings.
function parseInvestigationJson(source: string): unknown {
  let cursor = 0
  const whitespace = () => { while (/[\x20\t\r\n]/.test(source[cursor] ?? '')) cursor++ }
  const stringToken = () => {
    const start = cursor
    if (source[cursor++] !== '"') invalid()
    while (cursor < source.length) {
      const character = source[cursor++]
      if (character === '\\') cursor++
      else if (character === '"') return source.slice(start, cursor)
    }
    return invalid()
  }
  const scalar = /(?:true|false|null|-?(?:0|[1-9]\d*)(?:\.\d+)?(?:[eE][+-]?\d+)?)/y
  function value(depth: number): void {
    if (depth > 64) invalid()
    whitespace()
    const opening = source[cursor]
    if (opening === '{' || opening === '[') {
      cursor++; whitespace()
      const closing = opening === '{' ? '}' : ']'
      const keys = new Set<string>()
      if (source[cursor] === closing) { cursor++; return }
      while (cursor < source.length) {
        if (opening === '{') {
          whitespace()
          const key: string = JSON.parse(stringToken())
          if (keys.has(key)) invalid()
          keys.add(key); whitespace()
          if (source[cursor++] !== ':') invalid()
        }
        value(depth + 1); whitespace()
        if (source[cursor] === closing) { cursor++; return }
        if (source[cursor++] !== ',') invalid()
      }
      invalid()
    } else if (opening === '"') stringToken()
    else {
      scalar.lastIndex = cursor
      const token = scalar.exec(source)
      if (!token) invalid()
      cursor = scalar.lastIndex
    }
  }
  value(0); whitespace()
  if (cursor !== source.length) invalid()
  // Validate the original full JSON grammar before numeric preservation.
  return parseInspectionJson(source)
}

const nativeKinds = ['riscv.recorded-report.v1', 'firmware.recorded-report.v1'] as const
const nativeRoles = ['project_revision','project_source','native_report','native_case','provenance_source','receipt','manifest','native_result','native_execution','native_check','native_trace','reference_trace','program','source_snapshot','configuration','replay_record','candidate_diff'] as const
const selectedRoles = nativeRoles.filter(role => !['project_revision','project_source','native_report','native_case','provenance_source'].includes(role))
function exact(v: unknown, keys: readonly string[]) { const o = obj(v); if (Object.keys(o).length !== keys.length || keys.some(k => !Object.hasOwn(o,k))) invalid(); return o }
function boolean(v: unknown): boolean { if (typeof v !== 'boolean') invalid(); return v }
function ref(v: unknown): Reference { exact(v,['artifact_id','revision_id','attempt_id','sha256']); return reference(v) }
const fingerprint = (v: unknown): string => JSON.stringify(v, (_key, value) => value && typeof value === 'object' && !Array.isArray(value) ? Object.fromEntries(Object.entries(value).sort(([a],[b]) => a.localeCompare(b))) : value)
function same(a: unknown, b: unknown) { if (fingerprint(a) !== fingerprint(b)) invalid() }
function u32(v: unknown) { return num(v,0xffffffff) }
function bigintText(v: unknown, maximum: bigint, minimum = 0n): string {
  // This curated UI supports safe integer metadata only. Reject preserved
  // unsafe lexemes as unsupported rather than confuse them with quoted numbers.
  // The original exact bytes remain downloadable; no value is rounded.
  const s = String(num(v))
  if (BigInt(s) < minimum || BigInt(s) > maximum) invalid()
  return s
}
function safePath(v: unknown): string {
  const s = text(v,512)
  if (s.startsWith('/') || s.includes('\\') || s.includes(':') || /[\x00-\x1f\x7f]/.test(s) || s.split('/').some(x => !x || x === '.' || x === '..')) invalid()
  return s
}
export function parseInvestigationCapabilities(raw: unknown): { mode: DiscoveryMode } {
  const o = obj(raw); literal(o.status,'ok'); literal(o.investigation_api,'1')
  if (o.investigation_admission_schemas === undefined && o.investigation_report_kinds === undefined) return { mode: 'v1' }
  const schemas = unique(list(o.investigation_admission_schemas,2,v => one(v,['engineering.admission.v1','engineering.admission.v2'])),v => v)
  const kinds = unique(list(o.investigation_report_kinds,2,v => one(v,nativeKinds)),v => v)
  if (schemas.length !== 2 || kinds.length !== 2) invalid()
  return { mode: 'all' }
}
const origins = {
  '40920450439': { commit: '44b8b5e40ce7841f076e4178df13f38ecdbd05fd', artifact: '12919420477' },
  '40929665294': { commit: '5565985bbe2bb2a0be869d366baa48c715057657', artifact: '12922737365' },
} as const
function origin(raw: unknown) {
  const o = exact(raw,['kind','repository','source_commit','run_id','run_attempt','artifact_id','extracted_receipt_ref','transport_digest'])
  literal(o.kind,'github_actions'); literal(o.repository,'desanv01/VerifForge'); num(o.run_attempt,1,1)
  const runId = bigintText(o.run_id,(1n<<63n)-1n,1n), artifactId = bigintText(o.artifact_id,(1n<<63n)-1n,1n)
  const pinned = origins[runId as keyof typeof origins]; if (!pinned || o.source_commit !== pinned.commit || artifactId !== pinned.artifact) invalid()
  const receipt = ref(o.extracted_receipt_ref); if (receipt.attemptId !== null) invalid()
  const digest = o.transport_digest === null ? null : exact(o.transport_digest,['sha256','verification'])
  return { kind: 'github_actions' as const, repository: 'desanv01/VerifForge', commit: pinned.commit, runId, artifactId, runAttempt: 1, receipt, digest: digest ? { sha256: hash(digest.sha256), verification: one(digest.verification,['github_reported','locally_verified']) } : null }
}
export type NativeOrigin = ReturnType<typeof origin>
const riscvCases = {
  control: ['attempt-ba01be2c331649828d66ffcca63365ba','rv32i-control-v1'],
  mutant: ['attempt-3507c622a1f143d894f819a0958c0f6a','rv32i-functional-register-mutant-v1'],
  'control-replay': ['attempt-e845135395204d61b526d707bae1ef2f','rv32i-control-v1'],
} as const
const firmwareCases = { control: 'fw-regmap-control-v1', bad: 'fw-regmap-offset-bug-v1', candidate: 'fw-regmap-offset-candidate-v1', 'bad-replay': 'fw-regmap-offset-bug-v1', 'candidate-replay': 'fw-regmap-offset-candidate-v1' } as const
function locator(raw: unknown) {
  const kind = one(obj(raw).kind,['producer_attempt','producer_case'])
  const o = exact(raw,kind === 'producer_attempt' ? ['kind','origin','case_label','native_attempt_id','wire_experiment_id','raw_result_ref'] : ['kind','origin','case_label','fixture_id','execution_ref'])
  const hosted = origin(o.origin)
  if (kind === 'producer_attempt') {
    const label = one(o.case_label,['control','mutant','control-replay']), [nativeId,experimentId] = riscvCases[label]
    literal(hosted.runId,'40920450439'); literal(o.native_attempt_id,nativeId); literal(o.wire_experiment_id,experimentId)
    const result = ref(o.raw_result_ref); literal(result.attemptId,nativeId)
    return { kind, origin: hosted, label, nativeId, experimentId, result } as const
  }
  const label = one(o.case_label,['control','bad','candidate','bad-replay','candidate-replay']), fixtureId = firmwareCases[label]
  literal(hosted.runId,'40929665294'); literal(o.fixture_id,fixtureId)
  const result = ref(o.execution_ref); if (result.attemptId !== null) invalid()
  return { kind, origin: hosted, label, fixtureId, result } as const
}
export type NativeLocator = ReturnType<typeof locator>
function scope(raw: unknown) { return obj(raw).kind === 'github_actions' ? origin(raw) : locator(raw) }
function evidenceIdentity(raw: unknown) {
  const o = exact(raw,['source_hash','checker_hash','contract_hash','experiment_hash','environment_hash'])
  return { source: hash(o.source_hash), checker: hash(o.checker_hash), contract: hash(o.contract_hash), experiment: hash(o.experiment_hash), environment: hash(o.environment_hash) }
}
function checkerIdentity(raw: unknown) { const o = exact(raw,['checker_id','version','sha256']); return { id: identifier(o.checker_id), version: identifier(o.version), sha256: hash(o.sha256) } }
const riscvInputs = ['plan','profile','source_manifest','checker_manifest','environment','raw_rvfi','raw_spike','normalized_pair','comparison','object','elf','binary'] as const
const firmwareInputs = ['preparation','observation','check','register_contract','selected_header','bad_base_header','source_manifest','tool_metadata'] as const
const programInputs = ['start_object','probe_object','main_object','elf','binary','map','disassembly','elf_attributes'] as const
const sourceInputs = ['main','start','probe','linker_script','wrapper','core_wrapper','core','peripheral'] as const
function refs<T extends string>(raw: unknown, fields: readonly T[]): Record<T,Reference> {
  const o = exact(raw,fields); return Object.fromEntries(fields.map(k => [k,ref(o[k])])) as Record<T,Reference>
}
function memory(raw: unknown) {
  if (raw === null) return null
  const o = exact(raw,['kind','address','width','byte_mask','value']), width = num(o.width,4,1), value = u32(o.value)
  if (![1,2,4].includes(width) || value >= 2 ** (8*width)) invalid()
  return { kind: one(o.kind,['read','write']), address: u32(o.address), width, byteMask: num(o.byte_mask,15,1), value }
}
function divergence(raw: unknown) {
  if (raw === null) return null
  const o = exact(raw,['order','cycle','requirement','field','expected','observed'])
  literal(o.requirement,'RISCV_ARCH_RETIREMENT')
  const field = one(o.field,['pc','instruction','rd','rd_value','trap','interrupt','memory']), order = bigintText(o.order,(1n<<64n)-1n), cycle = num(o.cycle,1000000,1)
  const expected = field === 'memory' ? memory(o.expected) : ['trap','interrupt'].includes(field) ? boolean(o.expected) : num(o.expected,field === 'rd' ? 31 : 0xffffffff)
  const observed = field === 'memory' ? memory(o.observed) : ['trap','interrupt'].includes(field) ? boolean(o.observed) : num(o.observed,field === 'rd' ? 31 : 0xffffffff)
  if (fingerprint(expected) === fingerprint(observed)) invalid()
  return { field, order, cycle, expected, observed }
}
function riscCase(raw: unknown) {
  const o = exact(raw,['schema_version','locator','inputs','identity','checker','configuration_hash','producer_execution_status','termination','checker_evidence_status','verdict','cycles','cycle_limit','retirement_limit','retired_count','reference_count','compared_count','first_divergence','producer_phases'])
  literal(o.schema_version,'riscv.recorded-attempt.v1')
  const located = locator(o.locator); if (located.kind !== 'producer_attempt') invalid()
  const input = refs(o.inputs,riscvInputs), identity = evidenceIdentity(o.identity), checker = checkerIdentity(o.checker)
  if (identity.checker !== checker.sha256 || input.checker_manifest.sha256 !== identity.checker || input.environment.sha256 !== identity.environment) invalid()
  const execution = one(o.producer_execution_status,['completed','bounded_incomplete','failed']), termination = one(o.termination,['completion_store_retired','trap','cycle_limit','retirement_limit']), evidence = one(o.checker_evidence_status,['valid','invalid','unsupported']), verdict = one(o.verdict,['pass_within_scope','design_mismatch','inconclusive'])
  const cycles = num(o.cycles,1000000,1), cycleLimit = num(o.cycle_limit,1000000,1), retirementLimit = num(o.retirement_limit,10000,1), retired = num(o.retired_count,10000), referenceCount = num(o.reference_count,10000), compared = num(o.compared_count,10000), first = divergence(o.first_divergence)
  const phases = unique(list(o.producer_phases,5,v => one(v,['assembler','compiler','objcopy','dut','reference'])),v => v)
  if (!phases.length || cycles > cycleLimit || retired > retirementLimit || compared > Math.min(retired,referenceCount) || (verdict === 'design_mismatch') !== !!first || (first && (BigInt(first.order)+1n !== BigInt(compared) || first.cycle > cycles)) || (evidence !== 'valid' && verdict !== 'inconclusive') || (verdict === 'pass_within_scope' && (!compared || execution !== 'completed' || termination !== 'completion_store_retired' || compared !== retired || compared !== referenceCount))) invalid()
  return { family: 'riscv' as const, locator: located, input, identity, checker, configurationHash: hash(o.configuration_hash), execution, termination, evidence, verdict, cycles, cycleLimit, retirementLimit, retired, referenceCount, compared, first, phases }
}
function mmioConflict(raw: unknown) {
  if (raw === null) return null
  const o = exact(raw,['mmio_index','bus_sequence','cycle','requirement','operation','expected_address','observed_address','write_strobes','active_lane_value','expected_probe','observed_probe'])
  literal(o.requirement,'FIRMWARE_STATUS_ADDRESS')
  const operation = one(o.operation,['read','write']), expectedAddress = u32(o.expected_address), observedAddress = u32(o.observed_address), strobes = num(o.write_strobes,15), value = u32(o.active_lane_value), expectedProbe = u32(o.expected_probe), observedProbe = u32(o.observed_probe)
  const addresses = [0x40000000,0x40000004,0x40000008,0x4000000c]
  let mask = 0n; for (let i=0;i<4;i++) if (strobes & (1<<i)) mask |= 255n << BigInt(8*i)
  if (!addresses.includes(expectedAddress) || !addresses.includes(observedAddress) || (operation === 'write' && (!strobes || (BigInt(value) & ~mask) !== 0n)) || (operation === 'read' && strobes !== 0) || (expectedAddress === observedAddress && expectedProbe === observedProbe)) invalid()
  return { index: num(o.mmio_index,8), busSequence: num(o.bus_sequence,65535), cycle: num(o.cycle,999999,5), operation, expectedAddress, observedAddress, strobes, value, expectedProbe, observedProbe }
}
const firmwarePhases = ['assemble_start','assemble_probe','compile_main','compiler','binary','disassembly','elf_headers','simulator'] as const
function firmwareCase(raw: unknown) {
  const o = exact(raw,['schema_version','locator','inputs','identity','checker','configuration_hash','profile_hash','semantic_hash','producer_completed','termination','checker_evidence_status','verdict','cycles','cycle_limit','retired_count','retirement_limit','transfer_count','mmio_count','first_conflict','mailbox','producer_phases','replay_of_execution_ref','reference_support'])
  literal(o.schema_version,'firmware.recorded-case.v1'); literal(o.reference_support,'independent_literal_mmio_checker_spike_unsupported')
  const located = locator(o.locator); if (located.kind !== 'producer_case') invalid()
  const inputs = exact(o.inputs,[...firmwareInputs,'program','sources'])
  const input = Object.fromEntries(firmwareInputs.map(k => [k,ref(inputs[k])])) as Record<typeof firmwareInputs[number],Reference>
  const program = refs(inputs.program,programInputs), sources = refs(inputs.sources,sourceInputs), identity = evidenceIdentity(o.identity), checker = checkerIdentity(o.checker)
  if (identity.checker !== checker.sha256) invalid()
  const completed = boolean(o.producer_completed), termination = one(o.termination,['completion_store_retired','trap','cycle_limit','retirement_limit','protocol_error','transfer_limit']), evidence = one(o.checker_evidence_status,['completed','bounded_incomplete','invalid_evidence']), verdict = one(o.verdict,['pass_within_scope','design_mismatch','inconclusive'])
  const cycles = num(o.cycles,1000000,1), cycleLimit = num(o.cycle_limit,1000000,1), retired = num(o.retired_count,10000), retirementLimit = num(o.retirement_limit,10000,1), transfers = num(o.transfer_count,65536), mmioCount = num(o.mmio_count,9), first = mmioConflict(o.first_conflict)
  const m = exact(o.mailbox,['observed','address','value','cycle']), observed = boolean(m.observed), address = num(m.address,0x8000ff00,0x8000ff00), value = m.value === null ? null : u32(m.value), cycle = m.cycle === null ? null : num(m.cycle,1000000,1)
  if (observed !== (value !== null && cycle !== null) || (!observed && (value !== null || cycle !== null)) || (cycle !== null && cycle > cycles)) invalid()
  const phases = unique(list(o.producer_phases,8,v => one(v,firmwarePhases)),v => v), replayOf = o.replay_of_execution_ref === null ? null : ref(o.replay_of_execution_ref), replay = located.label.endsWith('-replay')
  if (cycles > cycleLimit || retired > retirementLimit || mmioCount > transfers || (first && (verdict !== 'design_mismatch' || first.index >= mmioCount || first.busSequence >= transfers || first.cycle > cycles)) || (evidence !== 'completed' && verdict !== 'inconclusive') || (verdict === 'pass_within_scope' && (!completed || termination !== 'completion_store_retired' || mmioCount !== 9 || value !== 1 || first)) || replay !== !!replayOf || (replay ? phases.join() !== 'simulator' : phases.length !== 8)) invalid()
  return { family: 'firmware' as const, locator: located, input, program, sources, identity, checker, configurationHash: hash(o.configuration_hash), profileHash: hash(o.profile_hash), semanticHash: hash(o.semantic_hash), completed, termination, evidence, verdict, cycles, cycleLimit, retired, retirementLimit, transfers, mmioCount, first, mailbox: { observed, address, value, cycle }, phases, replayOf }
}
export type RiscvCaseView = ReturnType<typeof riscCase>
export type FirmwareCaseView = ReturnType<typeof firmwareCase>
export type NativeCaseView = RiscvCaseView | FirmwareCaseView

const summaryFields = ['id','investigation_id','project_id','ordinal','content_hash','revision_id','revision_hash','manifest_hash','schema_version','scope','execution_status','created_at','domains','native_investigation_id','report_kind']
const inputRoles: Record<string,string> = { plan:'configuration',profile:'configuration',source_manifest:'source_snapshot',checker_manifest:'configuration',environment:'configuration',raw_rvfi:'native_trace',raw_spike:'reference_trace',normalized_pair:'native_trace',comparison:'native_check',object:'program',elf:'program',binary:'program',preparation:'configuration',observation:'native_trace',check:'native_check',register_contract:'configuration',selected_header:'source_snapshot',bad_base_header:'source_snapshot',tool_metadata:'configuration' }
export function parseNativeDetail(raw: unknown, selected: VersionSummary) {
  const o = exact(raw,[...summaryFields,'envelope','project_revision','manifest','native_report']), s = summary(o,selected.projectId,selected.investigationId,'all')
  same(s,selected); if (s.schemaVersion !== 'engineering.admission.v2' || !s.reportKind) invalid()
  const e = exact(o.envelope,['schema_version','operation','new_execution','claim_scope','external_context_scope','validation','record','record_hash','artifacts','total_artifact_bytes'])
  literal(e.schema_version,'engineering.admission.v2'); literal(e.operation,'admission_only'); literal(e.new_execution,'not_run'); literal(e.claim_scope,'resolved_bytes_and_registered_native_checks_only'); literal(e.external_context_scope,'host_pinned_external_locator_not_local_execution_origin'); hash(e.record_hash)
  const proof = exact(e.validation,['validator_id','version','implementation_hash','implementation_hash_provenance','scope'])
  literal(proof.validator_id,'veriforge.native-tool-admission'); literal(proof.version,'1'); literal(proof.implementation_hash_provenance,'host_pinned_installed_source_hash'); literal(proof.scope,'exact_bytes_and_registered_pure_checkers_not_remote_attestation')
  const validatorHash = hash(proof.implementation_hash)
  if (new TextEncoder().encode(JSON.stringify(e)).length > 262144 || new TextEncoder().encode(JSON.stringify(o.native_report)).length > 1048576) invalid()
  const artifacts = unique(list(e.artifacts,256,value => {
    const a = exact(value,['reference','size_bytes','roles','canonical_record_hash']), parsedRoles = unique(list(a.roles,12,v => one(v,nativeRoles)),v => v)
    if (!parsedRoles.length) invalid()
    return { ...ref(a.reference), size: num(a.size_bytes,1048576), roles: parsedRoles, canonicalHash: a.canonical_record_hash === null ? null : hash(a.canonical_record_hash) }
  }),referenceKey)
  const totalBytes = num(e.total_artifact_bytes,16777216)
  if (artifacts.reduce((sum,a) => sum+a.size,0) !== totalBytes) invalid()
  function retained(reference: Reference, role?: string, canonical = false) {
    const a = artifacts.find(a => equalRef(a,reference))
    if (!a || (role && !a.roles.includes(role as typeof nativeRoles[number])) || (canonical && a.canonicalHash !== reference.sha256)) invalid()
    return a
  }
  const r = exact(e.record,['schema_version','investigation_id','project_id','revision_id','project_revision_ref','manifest_ref','report_kind','report_ref','native_records'])
  literal(r.schema_version,'engineering.investigation.v2'); literal(r.investigation_id,s.nativeId); literal(r.project_id,s.projectId); literal(r.revision_id,s.revisionId); literal(r.report_kind,s.reportKind)
  const revision = ref(r.project_revision_ref), manifest = ref(r.manifest_ref), reportRef = ref(r.report_ref)
  if (revision.sha256 !== s.revisionHash || manifest.sha256 !== s.manifestHash) invalid()
  retained(revision,'project_revision',true); retained(manifest,'manifest',true); retained(reportRef,'native_report',true)
  const p = exact(o.project_revision,['schema_version','project_id','revision_id','source_refs','configuration_hash'])
  literal(p.schema_version,'engineering.project-revision.v1'); literal(p.project_id,s.projectId); literal(p.revision_id,s.revisionId)
  const sources = unique(list(p.source_refs,64,ref),referenceKey), configurationHash = hash(p.configuration_hash)
  if (!sources.some(a => a.sha256 === configurationHash)) invalid(); sources.forEach(a => retained(a,'project_source'))
  const m = exact(o.manifest,['schema_version','manifest_id','revision_id','review_state','reviewed_by','adapters','facts','relations'])
  literal(m.schema_version,'engineering.manifest.v1'); literal(m.revision_id,s.revisionId); identifier(m.manifest_id)
  const manifestReview = one(m.review_state,['draft','reviewed']); if (m.reviewed_by !== null) identifier(m.reviewed_by); if (manifestReview === 'reviewed' && m.reviewed_by === null) invalid()
  if (list(m.adapters,0,obj).length || list(m.relations,0,obj).length) invalid()
  const report = exact(o.native_report,s.reportKind === nativeKinds[0] ? ['schema_version','family','operation','new_execution','manifest_ref','selection','cases','recompiled_control'] : ['schema_version','family','operation','new_execution','manifest_ref','selection','cases','candidate','replays'])
  literal(report.schema_version,s.reportKind); literal(report.family,s.reportKind === nativeKinds[0] ? 'riscv_retirement' : 'firmware_mmio'); literal(report.operation,'admission_only'); literal(report.new_execution,'not_run'); same(ref(report.manifest_ref),manifest)
  const selection = exact(report.selection,['origin','inventory','selected','omitted_paths']), hosted = origin(selection.origin)
  const inventory = unique(list(selection.inventory,4096,v => { const row = exact(v,['original_relative_path','sha256','size_bytes']); return { path: safePath(row.original_relative_path), sha256: hash(row.sha256), size: bigintText(row.size_bytes,(1n<<63n)-1n) } }),v => v.path)
  const bindings = unique(list(selection.selected,256,v => {
    const row = exact(v,['ref','scope','provenance','original_relative_path','original_artifact_id','role','media_type','size_bytes']), reference = ref(row.ref), located = scope(row.scope), provenance = one(row.provenance,['producer','adapter_generated']), originalPath = row.original_relative_path === null ? null : safePath(row.original_relative_path), originalId = row.original_artifact_id === null ? null : identifier(row.original_artifact_id), role = one(row.role,selectedRoles), size = num(row.size_bytes,1048576)
    const mediaType = one(row.media_type,['application/json','application/octet-stream','text/plain','text/x-diff','text/x-c','text/x-asm','application/xml'])
    same(located.kind === 'github_actions' ? located : located.origin,hosted)
    if (retained(reference,role).size !== size) invalid()
    if (provenance === 'adapter_generated') { if (located.kind !== 'github_actions' || originalPath !== null || originalId !== null || reference.attemptId !== null) invalid() }
    else {
      if (!originalPath || reference.attemptId !== (located.kind === 'producer_attempt' ? located.nativeId : null)) invalid()
      if (located.kind !== 'github_actions' && !originalPath.startsWith(located.label+'/') && originalPath !== located.label+'.check.json') invalid()
      const entry = inventory.find(a => a.path === originalPath); if (!entry || entry.sha256 !== reference.sha256 || entry.size !== String(size)) invalid()
    }
    return { reference, scope: located, provenance, originalPath, originalId, role, mediaType, size }
  }),v => referenceKey(v.reference))
  const omitted = unique(list(selection.omitted_paths,4096,safePath),v => v), included = unique(bindings.filter(b => b.provenance === 'producer'),b => b.originalPath!)
  if (!inventory.length || !bindings.length || bindings.reduce((sum,b) => sum+b.size,0)>16777216 || omitted.length+included.length !== inventory.length || omitted.some(path => !inventory.some(a => a.path===path) || included.some(a => a.originalPath===path))) invalid()
  function required(reference: Reference, located: NativeOrigin | NativeLocator, role?: string) {
    const b = bindings.find(b => equalRef(b.reference,reference)); if (!b || (role && b.role !== role)) invalid(); same(b.scope,located)
  }
  required(manifest,hosted,'manifest'); required(hosted.receipt,hosted,'receipt')
  const cases: NativeCaseView[] = s.reportKind === nativeKinds[0] ? list(report.cases,3,riscCase) : list(report.cases,5,firmwareCase)
  const expectedLabels = s.reportKind === nativeKinds[0] ? Object.keys(riscvCases) : Object.keys(firmwareCases)
  same(cases.map(c => c.locator.label).sort(),expectedLabels.sort())
  for (const c of cases) {
    same(c.locator.origin,hosted); required(c.locator.result,c.locator,c.family === 'riscv' ? 'native_result' : 'native_execution')
    for (const [name,reference] of Object.entries(c.input)) required(reference,c.family === 'firmware' && ['source_manifest','tool_metadata'].includes(name) ? hosted : c.locator,inputRoles[name])
    if (c.family === 'firmware') { Object.values(c.program).forEach(a => required(a,c.locator,'program')); Object.values(c.sources).forEach(a => required(a,c.locator,'source_snapshot')) }
  }
  if (bindings.some(b => b.scope.kind !== 'github_actions' && !cases.some(c => fingerprint(c.locator) === fingerprint(b.scope)))) invalid()
  const attachments = unique(list(r.native_records,5,v => exact(v,['fact_id','domain','native_schema_id','reference'])),a => identifier(a.fact_id)), facts = unique(list(m.facts,5,v => exact(v,['fact_id','domain','native_schema_id','native_record_hash','native_record','provenance','category','review_state','conditions'])),f => identifier(f.fact_id))
  if (attachments.length !== cases.length || facts.length !== cases.length) invalid()
  const factViews = facts.map((f,i) => {
    const a=attachments[i], c=cases[i], reference=ref(f.native_record), domain=c.family, schema=domain === 'riscv' ? 'riscv.recorded-attempt.v1' : 'firmware.recorded-case.v1'
    literal(a.fact_id,identifier(f.fact_id)); literal(a.domain,domain); literal(f.domain,domain); literal(a.native_schema_id,schema); literal(f.native_schema_id,schema); literal(f.category,'observation'); same(ref(a.reference),reference)
    if (hash(f.native_record_hash)!==reference.sha256) invalid(); retained(reference,'native_case',true)
    const provenance=exact(f.provenance,['state','sources','extraction','derived_from','uncertainties']); literal(provenance.state,'complete')
    const extraction=exact(provenance.extraction,['method','extractor_id','extractor_version','implementation_hash','tool']); literal(extraction.method,'native_parser'); literal(extraction.extractor_id,'veriforge.native-tool-admission'); literal(extraction.extractor_version,'1'); literal(extraction.implementation_hash,validatorHash)
    if (extraction.tool !== null) { const tool=exact(extraction.tool,['name','version']); identifier(tool.name);text(tool.version,256) }
    const refs=list(provenance.sources,32,v => {
      const src=exact(v,['artifact','location']), reference=ref(src.artifact), loc=obj(src.location)
      if (loc.kind==='lines') { exact(loc,['kind','start','end']); if(num(loc.end,Number.MAX_SAFE_INTEGER,1)<num(loc.start,Number.MAX_SAFE_INTEGER,1))invalid() }
      else if(loc.kind==='json_pointer') { exact(loc,['kind','pointer']); if(typeof loc.pointer!=='string'||loc.pointer.length>512||!/^(?:\/(?:[^~/]|~[01])*)*$/.test(loc.pointer))invalid() }
      else { exact(loc,['kind','object_id','field']);literal(loc.kind,'native_object');identifier(loc.object_id);identifier(loc.field) }
      retained(reference,'provenance_source');if(!sources.some(s=>equalRef(s,reference)))invalid();return reference
    })
    const primary=c.family==='riscv'?c.locator.result:c.input.check
    if(!refs.some(r=>equalRef(r,primary)))invalid()
    const parents=unique(list(provenance.derived_from,32,identifier),v=>v);if(parents.some(id=>!facts.some(f=>f.fact_id===id)))invalid()
    return { id: identifier(f.fact_id), reference, review:one(f.review_state,['reviewed','unreviewed']), limitations:countText(f.conditions)+countText(provenance.uncertainties) }
  })
  const nativeIds=cases.flatMap(c=>c.locator.kind==='producer_attempt'?[c.locator.nativeId]:[])
  if(artifacts.some(a=>a.attemptId!==null&&!nativeIds.includes(a.attemptId as typeof nativeIds[number])))invalid()
  const base={...s,kind:'native' as const,validatorHash,configurationHash,sources,manifestReview,artifacts,totalBytes,origin:hosted,bindings,omittedCount:omitted.length,inventoryCount:inventory.length,facts:factViews}
  if(s.reportKind===nativeKinds[0]) {
    const typed=cases as RiscvCaseView[], original=typed.find(c=>c.locator.label==='control')!, repeated=typed.find(c=>c.locator.label==='control-replay')!, pair=exact(report.recompiled_control,['kind','original','repeated','record_ref','outcome'])
    literal(pair.kind,'recompiled_control');same(locator(pair.original),original.locator);same(locator(pair.repeated),repeated.locator)
    const record=ref(pair.record_ref), outcome=one(pair.outcome,['byte_identical_recompiled_control','inconclusive']);required(record,hosted,'replay_record')
    if(outcome==='byte_identical_recompiled_control') {
      same(original.identity,repeated.identity);literal(original.configurationHash,repeated.configurationHash)
      for(const name of ['object','elf','binary','raw_rvfi','raw_spike','normalized_pair','comparison','environment'] as const)literal(original.input[name].sha256,repeated.input[name].sha256)
      if([original,repeated].some(c=>c.verdict!=='pass_within_scope'||!c.phases.includes('assembler')||!c.phases.includes('compiler')))invalid()
    }
    return {...base,family:'riscv' as const,cases:typed,recompiled:{record,outcome}}
  }
  const typed=cases as FirmwareCaseView[], bad=typed.find(c=>c.locator.label==='bad')!, fixed=typed.find(c=>c.locator.label==='candidate')!, candidate=exact(report.candidate,['kind','bad','candidate','diff_ref','original_bad_header_ref','candidate_header_ref','protected_checker_hash','protected_contract_hash','protected_profile_hash','covered_mmio_indices','outcome'])
  literal(candidate.kind,'curated_header_correction');same(locator(candidate.bad),bad.locator);same(locator(candidate.candidate),fixed.locator)
  const diff=ref(candidate.diff_ref), originalHeader=ref(candidate.original_bad_header_ref), candidateHeader=ref(candidate.candidate_header_ref), checkerHash=hash(candidate.protected_checker_hash), contractHash=hash(candidate.protected_contract_hash), profileHash=hash(candidate.protected_profile_hash), outcome=one(candidate.outcome,['scoped_curated_correction','inconclusive'])
  required(diff,fixed.locator,'candidate_diff');same(originalHeader,bad.input.selected_header);same(candidateHeader,fixed.input.selected_header);literal(originalHeader.sha256,fixed.input.bad_base_header.sha256)
  same(unique(list(candidate.covered_mmio_indices,9,v=>num(v,8)),String).sort((a,b)=>a-b),[0,1,2,3,4,5,6,7,8])
  for(const c of [bad,fixed]){literal(c.identity.checker,checkerHash);literal(c.identity.contract,contractHash);literal(c.profileHash,profileHash)}
  if(outcome==='scoped_curated_correction') {
    if(bad.verdict!=='design_mismatch'||fixed.verdict!=='pass_within_scope'||originalHeader.sha256===candidateHeader.sha256)invalid()
    for(const name of sourceInputs)literal(bad.sources[name].sha256,fixed.sources[name].sha256)
  }
  const replays=unique(list(report.replays,2,value=>{
    const replay=exact(value,['kind','original','repeated','record_ref','outcome']);literal(replay.kind,'saved_program_replay')
    const original=locator(replay.original), repeated=locator(replay.repeated)
    if(original.kind!=='producer_case'||!['bad','candidate'].includes(original.label))invalid()
    const a=typed.find(c=>c.locator.label===original.label)!, b=typed.find(c=>c.locator.label===original.label+'-replay')!
    same(original,a.locator);same(repeated,b.locator);same(b.replayOf,a.locator.result)
    const record=ref(replay.record_ref), outcome=one(replay.outcome,['semantic_saved_program_replay','inconclusive']);required(record,hosted,'replay_record')
    if(outcome==='semantic_saved_program_replay') {
      for(const name of ['preparation','register_contract','selected_header','bad_base_header','source_manifest','tool_metadata'] as const)literal(a.input[name].sha256,b.input[name].sha256)
      for(const name of programInputs)literal(a.program[name].sha256,b.program[name].sha256)
      for(const name of sourceInputs)literal(a.sources[name].sha256,b.sources[name].sha256)
      for(const name of ['identity','configurationHash','profileHash','semanticHash','verdict','first','mailbox','cycles','retired','transfers','mmioCount','termination','completed'] as const)same(a[name],b[name])
    }
    return {original:original.label,repeated:b.locator.label,record,outcome}
  }),r=>r.original)
  if(replays.length!==2)invalid()
  return {...base,family:'firmware' as const,cases:typed,candidate:{diff,originalHeader,candidateHeader,checkerHash,contractHash,profileHash,outcome},replays}
}
export type NativeInvestigationDetail = ReturnType<typeof parseNativeDetail>
