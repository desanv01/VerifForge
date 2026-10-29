# Engineering investigations and native evidence

The native bridges, pure admission module and immutable persistence/read API have passed independent source review. The bridges compare firmware header/literal RTL address facts and board-reference XDC/KiCad connector facts. Investigation versions associate these records with an existing owned project and optional campaign evidence. They perform **byte extraction, admission and recorded-evidence retrieval**. They do not start tools, models or jobs, establish physical correctness, or turn imported evidence into a new execution.

Current scope is the existing curated-project ownership boundary. Operational migration/adoption, PostgreSQL runtime qualification, browser integration and additional native domains require their own acceptance. See the [engineering reference contracts](ENGINEERING_INTERFACES.md), [native bridge implementation](../../src/veriforge/system_investigation/bridge.py) and [admission module](../../src/veriforge/system_investigation/records.py).

## Interface

```python
SourceArtifact(reference=EvidenceReference(...), data=raw_bytes,
               origin="imported_artifact", reviewed_by=None, parents=())
RootArtifact(reference=EvidenceReference(...), data=actual_generator_input_bytes,
             origin="imported_artifact", reviewed_by=None)
compare_registers(header, rtl, RegisterIntent(...)) -> BridgeReport
compare_board(xdc, netlist, pcb, BoardIntent(...), board_reference=False) -> BridgeReport
compare_candidate(before_report, after_report) -> CandidateComparison
bundle_files(report) -> dict[str, bytes]
```

The source reference's SHA-256 must match the exact bytes before parsing. Inputs are nonempty bytes, at most 1MiB each. Review is absent by default. Only fixed existing firmware and KiCad parsers plus the literal XDC parser are reachable; there is no command, shell, uploaded Python, path loader, dynamic import, model call or tool dispatch. References are metadata, not filesystem read authority. These APIs read no user-specified path.

`RegisterIntent` explicitly binds logical binding ID, header macro, RTL signal and expected byte address; byte base and selector are mandatory policy inputs with fixed defaults. `BoardIntent` explicitly binds selected HDL port, FPGA package ball, component reference, symbol pin, footprint pad and **both exact net names**. One label cannot imply these relationships. Names retain hierarchy; `/JA1` and `JA1` do not become equal through normalization. Duplicate selections reject. Power and ground pins have no automatic FPGA-ball equivalents. Each intent has optional host reviewer and review-evidence reference; the application must authenticate these, not accept them from a planner or arbitrary client.

Native firmware extraction uses `firmware.facts.v1`: the existing bounded numeric header grammar and one-case literal RTL decode grammar. Unsupported preprocessing, unresolved macros, truncating/duplicate decodes or absent selected facts stay inconclusive. This does not validate reset, byte-strobe, access-policy, startup or MMIO behavior. Existing firmware fixtures are synthetic parser text, not compiled/executed firmware.

Native KiCad extraction uses `kicad-connectivity-v1`: XML export D/E and PCB format20241229. Source locations/actual hashes and native connector endpoint types are retained. Board pad assignment is not routed copper continuity. Duplicate pad endpoints, even if their nets agree, are outside this bridge's unambiguous subset. Native parses never establish MPN, pad geometry, cable orientation or electrical correctness.

## XDC reference versus project interpretation

`literal-xdc-package-pin-v1` parses only literal `set_property PACKAGE_PIN ... [get_ports {literal_port}]` or the fixed `-dict { PACKAGE_PIN ... IOSTANDARD ... }` shape. No Tcl is evaluated. Active-project mode ignores comment lines and rejects active commands outside the narrow literal subset, including `source`, variable substitution and unrelated Tcl scripts. It is deliberately not a general XDC evaluator.

With `board_reference=True`, only commented manufacturer pin templates are interpreted. Active lines are ignored entirely; global configuration cannot turn comments into effective constraints. Every such native observation says `commented_board_reference`. Duplicate selected declarations remain ambiguous. This mode describes a manufacturer reference map, not the effective port/ball assignment of a compiled project.

The pinned Digilent Nexys Video Rev.A master XDC contains those commented templates. Its reviewed 28,746-byte content has SHA-256 `2debf0afb537c0a9f8f2c1569aa99e0366629a354f2fbdf369bd5142670086cc`. The [literal parser](../../src/veriforge/system_investigation/xdc.py) keeps this reference interpretation explicit. Upstream changes require review rather than silent replacement.

## Provenance and verdict boundary

Reports contain the actual typed intent, native records and `engineering.manifest.v1` references; native values are not recreated from a manifest. Parsers independently consume each admitted native file's bytes. Each native record is canonically serialized into its referenced SHA-256, with extractor name/version and an implementation hash covering the bridge, its models/XDC parser, fixed firmware/KiCad extractors and accepted engineering schema. Host deployment must freeze those files during an invocation; source hashes do not authenticate arbitrary hot-patched Python processes.

`parents` means actual causal generator inputs, supplied with hash-matched bytes. It is not a list of documents merely mentioned by both sides. This finite interface accepts at most eight explicit flat roots per source. Nested source objects cannot masquerade as roots and silently lose their ancestry; deeper native ancestry needs an explicit reviewed extension. Each root becomes a fact in the manifest. Shared content hashes remain dependent despite different output filenames/hashes. Unreviewed ancestors are never promoted by reviewed descendants. Declared model-proposal source or parent blocks native extraction/checked observation entirely. No model text becomes checked evidence through a reviewer string.

Every comparison has two separate fields:

- `value_comparison`: consistent/conflict/inconclusive for the literal parsed values against explicit intent.
- `verdict`: checked relation eligibility and scoped result. It stays inconclusive if source/intent review is missing, roots overlap, inputs are ambiguous, or model origins are declared.

Only complete, reviewed, disjoint source histories plus reviewed intent can create an `independent_check` relation; the accepted engineering graph validates the full ancestry. Otherwise a hypothesis/unknown or same-source/inconclusive relationship preserves the uncertainty. Shared reviewed intent is requirement context, not automatically an observation root. `execution_status` is always `not_run`.

Reviewer IDs, source-origin tags, parent declarations and evidence references are **trusted host assertions requiring authentication**. Schema validation cannot discover omitted ancestry or establish authentic extraction, independence or execution from distinct hashes. Do not expose these constructors as a public upload-to-checked-result API. Reports/candidate comparisons must come from the trusted byte bridges, and storage must resolve/authorize artifact identities independently.

## Candidate and storage behavior

`compare_candidate` preserves exact before/after report hashes, same intent/checker identity, stable source-ID set and changed content hashes. Changed source bytes require a new revision or attempt identity, preserving immutable artifact semantics. It separately counts literal value conflicts before/after. `source_comparison_improved` requires an original checked conflict and all corrected checked relations consistent; unreviewed/dependent graphs remain `no_demonstrated_improvement`, even when literal conflicts drop to zero. This is never functional repair or physical signoff.

Generated native/comparison artifacts use content-hash revisions so different bytes cannot reuse an immutable reference. The manifest revision binds intent, implementation and exact fact identities. `BridgeReport` validates its typed intent/hash, native data/hash, graph/native correspondence, implementation identities, comparison/relationship agreement and evidence hashes. These checks enforce internal consistency, not authenticity of raw client reports.

Independent review found and closed two envelope gaps: removing relationships could previously leave a promoted comparison unbound, and source receipts could disagree with native provenance. Complete exact relationship coverage is now mandatory for each comparison with two or more facts. Observation roles/order, typed names/ports/pins, domains, literal values, exact requirement context and independent-check authority are validated before any noninconclusive verdict. Every source receipt's full immutable reference must match its role's native provenance; unsupported input is preserved as an input-identity fact instead of a floating receipt. Unbound/orphan native facts are rejected, including unrelated requirement/root records. These are consistency checks; an attacker who fabricates a fully coherent provenance history still cannot be authenticated by wire validation alone.

`bundle_files` returns fixed relative names and bytes for trusted storage: `report.json`, `manifest.json`, `intent.json`, `native/native-<fact-id>.json` and `comparisons/comparison-<binding-id>.json`. It writes/uploads nothing. Byte hashes correspond to native/comparison evidence references. Original native inputs and review documents remain external hash-bound dependencies; the bundle is not a portable complete replay export by itself. The storage owner must preserve those originals and resolve references, with proper authorization/path handling.

Limits: at most16 intent bindings, eight flat parent inputs per source, 128 native records, 32 source receipts, three observation endpoints per comparison, 4MiB returned bundle, and the stricter accepted engineering manifest bounds. IDs/unknown fields/integers remain typed and bounded. No data dictionary supplies domain semantics or executable plugins.

## Pure admission and serialization

Import explicitly from `veriforge.system_investigation.records`:

```python
EngineeringProjectRevision(project_id, revision_id, source_refs, configuration_hash)
EngineeringInvestigation(
    investigation_id, project_id, revision_id,
    project_revision_ref, manifest_ref, bridge_report_ref,
    native_records, attempts=(),
)
CampaignAttemptEvidence(
    campaign_id, db_attempt_id, experiment_record_id,
    native_attempt_id, wire_experiment_id, result_ref,
)
ArtifactBytes(reference, data=exact_bytes)
admit_investigation(record, artifacts, *, authority) -> AdmittedInvestigation
serialize_investigation(admitted) -> bytes
read_investigation(envelope_bytes, artifacts, *, authority) -> AdmittedInvestigation
```

These signatures are abbreviated field descriptions, not positional constructor examples. `EngineeringProjectRevision` describes an associated engineering source snapshot; it neither coerces the FIFO revision schema nor asserts that a board and an RTL target are the same design. Its configuration hash must match retained source bytes and is never evaluated as executable configuration.

Trusted service code creates `TrustedAdmissionAuthority` from already-authorized project, revision, manifest, bridge and attempt references. There is no authority-from-client-JSON path. Direct construction of an admitted object, or merely passing `isinstance`, cannot authorize persistence. Admission resolves every source/native/ancestor/configuration/relation/capability/result reference, revalidates the complete typed bridge graph and checks full identities, hashes and actual byte sizes. It rejects orphan, duplicate and conflicting supplied identities. Existing native formats remain authoritative; unregistered native schemas are unsupported.

All exact byte buffers remain private in the admitted result. The safe serialized envelope includes full references, raw hashes, sizes, roles and applicable canonical record hashes, with `admission_only`, `new_execution=not_run` and `host_pinned_association_not_execution_origin`. Reading requires complete re-admission with service authority and all original bytes. Raw artifact SHA-256 remains distinct from typed canonical identity; canonical-native references must satisfy both without rewriting the original reference.

Bounds are 0–1MiB per retained artifact, 16MiB total, 256 artifacts and 262144 envelope bytes. Empty diagnostic logs are preserved when their hashes and sizes agree. Native bridge input bounds remain stricter where specified above. Admission performs no database, filesystem, network or execution operation and does not promote maturity, verdict or provenance from a client assertion.

## Immutable persistence and migration prerequisite

The [persistence service](../../src/veriforge/core/investigations.py) attaches a stable server-ID investigation to an existing owned curated project. Append-only versions preserve the exact admission envelope and every admitted artifact byte. Native source/configuration revisions remain distinct from the project's execution snapshots. Same idempotency key and content returns the existing version; changed content conflicts. One caller-owned transaction stores the version, contextual links and blobs or rolls back. Internal writes use `persist_investigation`; there is no public investigation POST or execution endpoint.

The four additive tables are `investigations`, `investigation_versions`, `investigation_artifacts` and `investigation_execution_links`. Updates/deletes of retained history are fenced. Artifact download IDs are opaque server IDs, never native labels or filesystem paths. Downgrade refuses retained investigation history.

Schema prerequisite: the accepted experiment/round/cleanup chain must reach [`10u2cleanup`](../../migrations/versions/10u2cleanup_durable_local_cleanup.py) before [`11u9investigations`](../../migrations/versions/11u9investigations_durable_investigations.py). Revision11 explicitly depends on revision10 and adds the investigation tables plus project-scoped campaign association constraints. This dependency is a source/migration contract; it does not claim that a retained or production database has already been upgraded. Operational migration remains separately controlled.

Persistence and HTTP behavior have isolated SQLite/TestClient source-test coverage. PostgreSQL quota/immutability DDL has been inspected, but live PostgreSQL migration, trigger/concurrency behavior and restart recovery are not established by that evidence. This local source boundary is not public deployment or multi-tenant execution acceptance.

### DB context versus native execution identity

`campaign_id`, `db_attempt_id` and `experiment_record_id` identify database context. `native_attempt_id` and `wire_experiment_id` identify the unchanged native result and typed executable plan. EvidenceReference.attempt_id remains in the native namespace. Persistence resolves the current owned campaign/project/DB attempt and immutable experiment, then checks the stored result, identity and wire ID. It never rewrites native result bytes or IDs to equal database IDs; DB foreign keys use only the explicit DB fields.

Each link records `origin_at_admission`:

- `context_only`: an authorized database association, without retained original execution-byte proof.
- `retained_execution_bytes`: the original result-file bytes/hash and every result artifact matched a trusted existing attempt snapshot at admission.

The latter is a retained-byte observation at a specific time, not a new run, tool-authenticity attestation or independent design verdict. Synthetic fixtures can satisfy this byte-retention condition. Imported JSON equivalence alone cannot establish it. Investigation versions always retain `scope=recorded_import` and `execution_status=not_run`; native uncertainty and actual execution status stay in the original domain evidence. Context changes or corrupted links fail closed on resolved reads.

## Read-only HTTP contract

The [investigation router](../../src/veriforge/api/investigations.py) exposes these GET routes under the existing ownership boundary:

| Route | Response |
| --- | --- |
| `/api/projects/{project_id}/investigations` | `{items: [investigation], next_cursor}`; each investigation has server `id`, `project_id`, `created_at`, `latest_version` |
| `/api/investigations/{investigation_id}` | The investigation and latest version summary |
| `/api/investigations/{investigation_id}/versions` | `{items: [version_summary], next_cursor}` |
| `/api/investigations/{investigation_id}/versions/{version_id}` | Summary plus `envelope`, typed `project_revision`, `manifest`, `bridge_report`, `execution_contexts` |
| `/api/investigations/{investigation_id}/versions/{version_id}/artifacts` | `{items: [artifact_metadata]}` with opaque server download `id`, native reference fields, SHA-256, size, media type and roles |
| `/api/investigations/{investigation_id}/versions/{version_id}/artifacts/{artifact_id}/download` | Exact admitted bytes as an attachment |

Project pagination uses an opaque `after` ID; version pagination uses a nonnegative ordinal. Both default to 20 items and allow 1–50. Cursors may be null at the end. Version summaries include server IDs, ordinal, content/revision/manifest hashes, native revision/schema IDs, domains, UTC creation time and the explicit recorded-import/not-run scope. The native investigation ID is separate from the server ID.

Version detail, artifact listing and downloads fully re-admit the stored byte graph and validate DB context. Root/list summaries verify envelope identity without scanning every blob; use detail for resolved evidence. Unknown, foreign or misbound IDs return 404, and changed/unavailable evidence returns a safe 409. Detail's content-hash ETag supports 304 only after full validation, so a matching ETag cannot conceal corrupt bytes. Downloads use `application/octet-stream`, digest-derived filenames, `nosniff` and `no-store`; source paths and bytes are absent from metadata lists.

There is no investigation write, tool dispatch, maturity promotion or new SSE stream. Existing campaign execution/events remain separate. A read or stored version cannot create a Finding or convert build success, imported reports, model hypotheses or inconclusive native comparisons into correctness.

## Recorded native comparison scope

Read-only native-file inspection compared original/corrected KiCad netlist and PCB bytes against a pinned master-XDC reference. It used reviewed **logical fixture/cable intent only**, not an assertion of a real cable or physically reviewed part. Only the eight JA signal paths on J1 were compared; remaining ground/supply and J2 mappings were not inferred.

Actual original inputs:

- Netlist `0ae9dc3dcc90048f0c3ef1c35289976f7e3a5bb8b54908bbd10bd08d5ff9ad44`.
- PCB `625a84fdd8b32f87109ac45e54bdc1215c9762d70f1d2b3a3cd3ad2af8d929b7`.

Actual corrected inputs:

- Netlist `0f96ffcdbb8ace1c7c17be454f01f9438934b8147a54a63d5b6e1905a3d225ea`.
- PCB `6491491d6030c7a7319775fac4ba7b9216aa5a7f6665a855bb3004ecb6b6667c`.

The recorded inspection observed eight exact net-name conflicts in the original files and zero in corrected files under the same intent/checker. It preserved the common carrier-generation root and left native source review unapproved; all relationship verdicts remain inconclusive. The candidate result reports counts8→0 with `no_demonstrated_improvement`. This is a source discrepancy/correction comparison, not independent whole-board or physical acceptance.

Separate parsing found four consistent literal register addresses in the existing synthetic firmware header/RTL fixtures, while retaining synthetic origin, unreviewed intent and inconclusive checked verdicts. Neither parsing nor storing these observations ran firmware, a CPU, FPGA, ERC/DRC, Vivado or a model. Any native tool-execution claim needs its own actual tool evidence.

The [bridge tests](../../tests/system_investigation/test_bridges.py), [admission tests](../../tests/system_investigation/test_records.py), [persistence tests](../../tests/core/test_investigations.py) and [HTTP tests](../../tests/api/test_investigation_routes.py) exercise the accepted source boundaries with explicitly labeled fixtures. They do not establish native authenticity, physical cable/MPN/electrical acceptance, live model use, browser integration or end-to-end cross-domain execution.
