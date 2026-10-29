# Additive engineering contracts — engineering.manifest.v1

Import directly from `veriforge.contracts.engineering`. Legacy `contracts/__init__.py`, FIFO/arith/AXI models, AttemptResult and the registry are unchanged. These are bounded immutable wire references and consistency validators, not new execution adapters, an authenticity service or a replacement domain fact/value model.

## Capability and configuration identity

`AdapterIdentity` (`engineering.adapter.v1`) carries adapter ID/version, native schema ID, implementation SHA-256 and configuration SHA-256. The hashes identify a versioned native implementation/configuration; they cannot specify an import, shell command or tool recipe. Domain owners retain typed native configurations and calculate hashes using their documented serialization.

`AdapterCapabilities` requires three explicit `CapabilitySupport` records, exactly one each for `import`, `execute`, `repair`. Each independently records maturity (`planned`, `unsupported`, `experimental`, `demonstrated`), exact scope and immutable evidence references. Demonstrated support requires evidence; demonstrated execution/repair additionally needs an attempt reference. Imported logs cannot automatically promote Execute or Repair. A tool-ready UI card, successful build or schema acceptance is not capability evidence. Native acceptance checks, comparison cases and regression matrices remain native owners' responsibility; these references do not prove their contents passed.

## Native facts and provenance

`EvidenceReference` binds artifact ID, SHA-256, revision ID and optional native attempt ID. The identity namespace is `(artifact_id, revision_id, attempt_id)`; conflicting hashes in one manifest are rejected. IDs are references, never filesystem paths or download authority.

`NativeFactReference` identifies a domain (`rtl`, `riscv`, `firmware`, `vivado`, `kicad`, `system`), native schema and exact native-record artifact hash, category (`observation`, `requirement`, `hypothesis`), review state, provenance and conditions. The native record retains values, units, addresses, pin/net semantics, register access behavior, ambiguity and native outcome fields. Do not flatten these into an untyped shared value dictionary. Serialize/store a native fact record or immutable native record bundle with a documented native identifier; keep the native record retrievable by its evidence reference. This shared layer cannot interpret or check its domain semantics.

`FactProvenance` has explicit `complete`, `ambiguous`, or `missing` state. Complete requires source artifact identity/location and `ExtractionIdentity` (method, extractor ID/version, implementation hash); actual tool identity/version is mandatory for `tool_report` extraction. A native parser is versioned independently of the domain tool; manual/model/generated records must accurately identify their method. Do not invent a tool version or extraction hash: use missing/ambiguous provenance and explain uncertainty instead. Source locations are discriminated line ranges, JSON Pointers or native object/field identifiers; line numbers are strict positive integers.

`derived_from` is a bounded list of upstream fact IDs **actually used to derive this fact**. Every ancestry node must resolve in the manifest, with cycles rejected. A generated header and generated RTL from one manifest inherit that manifest's root, even when generated file hashes, extractor names and artifact labels differ. Merely mentioning the same requirement is not derivation: use relation `requirement_fact_ids` for shared intent/context, rather than falsely claiming the requirement generated independently extracted source observations.

## Reviewed system relationships

`SystemManifest` (`engineering.manifest.v1`) holds unique native fact references, typed relationships and optional capability records. Reviewed manifests require a reviewer ID. Each adapter version/configuration is explicit; multiple configurations may coexist without conflating their maturity evidence.

`FactRelation` links named facts by `maps_to`, `same_signal`, `same_address`, `same_clock`, `connects_to`, or `satisfies`. It keeps:

- Evidence basis: `hypothesis`, `reviewed`, `independent_check`.
- Domain verdict: `consistent`, `conflict`, `inconclusive`.
- Provenance dependence: `independent`, `same_source`, `unknown`.
- Optional checker identity, immutable supporting evidence and uncertainty.

These are separate dimensions, not execution status. An independent checker can legitimately return inconclusive. Inconclusive requires explicit uncertainty. Same-source comparison can be retained as reviewed/inconclusive without presenting it as independent validation. Labels matching is never sufficient to infer a relationship.

An `independent_check` must be validated inside a complete `SystemManifest`, not accepted from a standalone relation object. It requires reviewed manifest and observation endpoints, checker identity/evidence, complete ancestry and disjoint root source hashes across endpoints. Every fact in each observation's derivation ancestry must also be explicitly reviewed; a reviewed leaf cannot promote an unreviewed ancestor, even through multiple hops. Review eligibility is tracked separately from provenance completeness, preserving nonchecked unknown/inconclusive records. Requirements are separately reviewed context references; they do not count as independently extracted observations. Hypotheses/model proposals and incomplete/ambiguous ancestry cannot provide checked-observation independence. Missing endpoints and unresolved/cyclic derivation references reject even draft manifests, preventing silent ancestry truncation. Facts with incomplete provenance can remain in an explicit unknown/inconclusive relationship.

**Root lineage and independence are reviewed provenance assertions, not cryptographic proof of real independence.** Equal source-content hashes detect shared artifacts despite renamed IDs. Distinct hashes alone cannot establish independent derivation: a producer can omit a common ancestor or supply misleading provenance. Honest complete ancestry, authentic native evidence, review and appropriate independent extraction are required outside the schema. Schema validation only enforces consistency of the supplied graph. Reviewer IDs/checker/evidence hashes are also assertions until the application resolves and authenticates them. No claim becomes accepted because the UI displays it.

## Bounds and native bridge requirements

Manifests allow 256 facts/relations, 64 adapter configurations, 32 sources/parents per fact and 16 endpoints per relation. Records forbid extra fields, freeze values and use bounded tuples, identifiers, strict integers and SHA-256 strings. Standard Pydantic JSON round trips and legacy `canonical_hash` apply. A schema/version change is a new wire contract; no dynamic plugin loading is provided.

Native bridges should preserve original native serialization and separately reference source artifacts/locations, extractor code/version, actual tool report version and derivation inputs. Native parsers that only have partial provenance must report it as incomplete. Domain checkers supply checker identity, exact evidence and explicit verdict; the server validates the whole manifest and resolves authorization/bytes before claiming acceptance. Existing execution adapters, AttemptResult, tool/process budgets, review workflows, storage, signatures and API adoption remain separate work.

Focused validation: `.venv/Scripts/python.exe -m pytest tests/contracts/test_engineering.py -q` — 29 pure tests passed in 0.61s at source delivery. This is schema/lineage evidence only; no domain tool ran.
