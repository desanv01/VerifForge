# Evidence workflows — VF-040A / VF-054B

The evidence package implements local, controller-invoked workflows for the shared
FIFO, unsigned arithmetic-pipeline and bounded AXI-Lite contracts. It accepts typed experiments and trusted Python runner callbacks,
never shell strings, uploaded code, patch application, or model-generated commands.
Candidate execution is limited to a separately selected curated fixture revision.

## Public callable agreement

Import from `veriforge.evidence`. Paths are controller-selected local `pathlib.Path`
values. A runner has the shared signature
`runner(experiment: ValidatedExperiment, output_dir: Path) -> AttemptResult`, where
`ValidatedExperiment` is the registry's `Experiment | ArithmeticExperiment | AxiLiteExperiment`. The callback
must enforce the plan's wall-time, cycle, artifact and isolation limits, use a fresh
attempt directory, and persist exact experiment/result/artifact records. Production
callers must supply a trusted adapter; unit-test scripted callbacks are not simulator
evidence. The evidence module itself cannot attest that a supplied callback ran HDL.

```python
load_attempt(attempt_dir, *, limits=ExportLimits()) -> tuple[ValidatedExperiment, AttemptResult]
replay_attempt(attempt_dir, output_dir, *, runner=None) -> ReplayRecord
export_bundle(attempt_dir, destination, *, limits=ExportLimits()) -> Path
portable_artifact_bytes(attempt_dir, artifact_id, *, limits=ExportLimits()) -> bytes
reduce_failure(attempt_dir, output_dir, *, runner, max_attempts=30) -> ReductionRecord
validate_candidate(
    attempt_dir, output_dir, *, candidate_fixture_id,
    candidate_runner, original_runner, neighboring_plans, regression_plans, provenance=None,
) -> CandidateValidation
compare_candidate_sources(original_attempt_dir, candidate_attempt_dir, *,
                          provenance=None, limits=CandidateReviewLimits()) -> CandidateSourceReview
```

Records are frozen Pydantic wire models with `model_dump(mode="json")` and
`model_dump_json()`. Invalid identities, unsafe/missing artifacts, unsuitable
inputs, excessive limits and existing output paths raise `ValueError` or an
appropriate filesystem exception. Simulator outcomes such as a harness error or
timeout remain ordinary `AttemptResult` values; they never become design failures.

`load_attempt` checks the exact stored experiment's canonical identity and result
experiment ID, then validates every referenced artifact's path containment, size
and SHA-256, rejecting duplicate names/IDs. Default bounds: 100 artifact files,
20 MiB total artifact bytes, 20 MiB for each top-level JSON read. Symbolic links
resolving outside the attempt directory are rejected. A hash provides consistency,
not authenticity against an attacker who can replace every record and hash.
API/storage authorization and immutable server-side provenance remain required.
The trusted registry selects the experiment model and installed fixture set. Legacy
FIFO experiment JSON still loads without an adapter field. Arithmetic retains its
`arithmetic.v1` schema and `rtl.arithmetic.v1` adapter. Unknown adapters, conflicting
targets/schemas and uninstalled fixtures fail validation before any runner call.
Reduction/candidate record experiment fields use a union discriminated by `target`,
so JSON round trips retain each family's typed operations. Existing FIFO callable
arguments and serialized experiment content are unchanged.

Checked arithmetic results require the hash-verified `arithmetic-trace.json`
sidecar with schema `arithmetic.trace.v1`; FIFO-oriented shared `AttemptResult.trace`
must remain empty. The envelope validator rejects extra/unknown fields, non-boolean
valid/reset bits, non-integer or oversized products, misordered cycle/operation
indices, inconsistent cycle counts and missing rows. Defined expected outputs need
a non-null product. This validates evidence structure, not a new mathematical
oracle verdict. The immutable arithmetic checker remains responsible for valid
timing, products, reset priority and unspecified invalid-cycle data semantics.

## Replay

Replay consumes the stored exact experiment, including seed, operations and budget;
it calls no planner/model. With no callback, it selects the trusted descriptor,
compares its `expected_identity(experiment)` source/checker/contract/experiment
hashes to the original, and verifies the stored hash-checked stimulus equals
`expand_stimulus(experiment)` before resolving the descriptor's native runner.
The registry never accepts an input-supplied import path or command. A supplied
callback supports the isolated container adapter. Its returned
source/checker/contract and exact experiment identities must match the original.
Environment/tool versions remain in both result records; a different environment is
not silently represented as the original. A passing replay returns
`same_semantic_failure=False`. Missing identity is rejected, including unsupported
attempts that never established a checked design identity.
Historical load/export preserve stored identity and do not require it to match the
currently installed source; native execution explicitly requires that match.

## Reduction

Reduction requires an original completed design mismatch or property failure.
It tries chunk deletion, individual deletion, reset/idle cycle reduction to one,
and scalar simplification to zero (FIFO `value`; arithmetic `a` and `b`). Every
candidate is fully revalidated by its original trusted descriptor, preserving
reset-before-traffic, widths, cycle limits and arithmetic final drain/cancellation.
Candidate plans retain the original fixture, target, parameters, seed, budget and
objectives. The maximum number of runner calls is configurable from 1 to 1000;
the original plan's runner wall-time limit applies to each call. No additional
unbounded retry loop exists. Output goes into a fresh reduction directory.

Acceptance requires the exact submitted experiment hash, original source/checker/
contract identities, completed execution, the same design-verdict category,
requirement ID and failure class. The first failing cycle and expected/observed
values may move as stimulus shrinks. A harness error, timeout, different requirement,
different class or changed checker cannot be accepted. Every attempted plan and
result appears in `reduction.json`, alongside the original and best accepted input.
The original evidence is retained. There is no claim of a globally smallest proof,
nor an assertion that an unchanged result was successfully reduced. The explicit
`improved` field distinguishes an accepted simplification from an unchanged input.

## Candidate validation

Validation does not execute arbitrary patches or create a repair. It selects a
different fixture ID from the schema's curated registry and requires the original
failure to reproduce under the original exact input/identity before candidate
execution. Both neighboring and regression suites must be nonempty, with at most
100 combined plans; their original fixture, contract, target and parameters must
match the original witness, including adapter family. The candidate plans change
only fixture ID, which must be installed in that same descriptor.

The candidate witness, neighbors and regressions must all complete and pass within
scope. Every result must bind to its exact submitted plan, preserve original checker
and contract hashes, and report one consistent candidate source hash differing from
the original. Status is `original_not_reproduced`, `failed_under_checks`, or
`validated_under_checks`. The record persists original/replay results, exact
candidate experiments and all checks in `validation.json`. Neighboring/regression
plan counts are retained, including when reproduction fails before the candidate
runs. The execution basis is
explicitly `supplied_runner_evidence`; actual HDL execution is established only by
the integrated runner's own evidence. Passing these finite checks does not prove
equivalence or arbitrary-input correctness. Callers must choose meaningful distinct
neighboring/regression plans; duplicate suite inputs cannot increase coverage.

### Inspectable source review and precise recommendation provenance

`compare_candidate_sources` reads two already recorded attempts without writing or
executing either. It requires an original checked failure and the exact original
witness on a different installed fixture in the same adapter. Contract, checker,
parameters, operations, seed and budget cannot change. It verifies every original
artifact using `load_attempt`, then binds copied source bytes to the source identity
using the trusted curated variant and recorded parameters. Copied checker bytes and
the copied reviewed typed contract must match immutable checker/contract hashes.
This additional snapshot binding belongs to source review; the established historical
`load_attempt` consistency boundary is unchanged.

The frozen `CandidateSourceReview` contains original/candidate attempt and experiment
identities, source-file byte hashes, source identities, fixture IDs and variants,
protected checker/contract hashes, and two unified diffs with safe relative labels:
HDL text and curated fixture/variant/parameter configuration. Identical source bytes
produce `source_bytes_changed=false` and an empty source diff; a variant change is
shown only in the configuration diff. Current FIFO/arithmetic examples use this
prepared variant selection. They must not be described as newly generated source
patches. The review itself is scoped to source/configuration; it is not execution,
functional proof or a replacement for witness/neighbor/regression checks.

`CandidateReviewLimits` bounds each source to256KiB/4096lines by default and the
combined diff to1MiB (hard configurable caps1MiB/10000lines/4MiB). Excess fails rather
than truncating the change. Source and review metadata use the existing strict UTF-8
and credential/local-path screening. Originals are reverified before returning and
never modified. No arbitrary source path, shell, Python, patch application or network
operation is accepted. Only the fixed family source artifact is inspected.

Optional `CandidateProvenance` distinguishes recommendation from materialization:

- `curated_catalog`: default prepared candidate selection; no external/model claim.
- `external_recommendation`: exact proposal ID/hash plus external reference ID.
- `model_recommendation`: exact proposal ID/hash plus `ModelRecommendation` containing
  provider, exact model label, model-call ID and prompt/response SHA-256 hashes.

Every supported provenance retains `materialization=curated_fixture_selection`,
`authorship_claim=curated_source_not_generated_patch`, and
`assertion_basis=controller_supplied_record`. These references record the caller's
assertion; this module does not contact a provider or authenticate a model call.
Incomplete or contradictory origin records are rejected. External/model-authored
source patch materialization and execution remain unsupported; calling a curated
selection a model-generated patch is rejected. No provider credential or raw prompt
is stored here.

New `validate_candidate` calls record recommendation provenance and automatically
attach a source review when original and witness candidate manifests contain copied
source/checker/contract snapshots. `source_review_sha256` binds the exact review to
the validation record; model validation ties it to original/witness attempts, exact
inputs, source and protected identities. Change flags and nonempty diff claims must
agree with file hashes and fixture/variant identities; equal file hashes also require
equal recorded lengths. Validation binds adapter, fixture, variant, relative source
name, file hash/length and derived source identity to the original and witness plans
and their artifact manifests. Rehashing a contradictory review does not repair these
internal inconsistencies. A complete but inconsistent snapshot yields
`source_review_status=invalid_source_snapshots` and `failed_under_checks`, even if
the supplied execution results pass. Missing legacy/test snapshots are explicitly
`missing_source_snapshots`, with no fabricated diff. If original reproduction fails,
the review is `not_executed`. Old serialized records remain readable with provenance
null and `legacy_unrecorded`, so historical checks gain no invented authorship.

These are evidence-layer callables only. Candidate API routes, durable storage/UI
adoption and live execution of newly provenance-bound validation remain separate
integration gates. Pure tests and read-only review of accepted real FIFO/arithmetic
snapshots establish source behavior, not a new simulator/model run.

## Portable ZIP and report

Export verifies all original artifact references, then writes a new ZIP exclusively
outside the original attempt directory. It never overwrites originals or an existing
export. The bounded archive contains `manifest.json`, exact typed `experiment.json`,
`REPORT.txt`, and selected verified artifacts under `artifacts/`: `stimulus.json`,
`contract.json`, `checker.py`, `observed.json`, and its family source (`fifo.sv` or
`multiplier.sv`). Arithmetic additionally includes `arithmetic-trace.json`.
These names come from the trusted descriptor's `portable_artifacts`, not submitted
experiment metadata. A FIFO attempt cannot export the arithmetic source/sidecar,
and an arithmetic attempt cannot export `fifo.sv`. Arbitrary artifact paths,
job descriptions, raw logs and environment diagnostics are excluded. The manifest
retains immutable hashes, actual tool versions, numerical trace and semantic failure
identity, omitting free-form expected/observed diagnostic strings and uncertainties.
The manifest includes `adapter_id` and a separate `family_trace` schema/artifact
reference when applicable. Arithmetic valid/product rows stay in their original
sidecar bytes; they are never relabeled as FIFO data/empty/full observations.
Each included artifact retains its original bytes and SHA-256. Included text is
screened for common credential markers and local machine paths; suspicious exports
fail before creation rather than rewriting evidence and invalidating its hash.

Only fixed allowlisted archive names are written; no archive extraction endpoint is
provided. Byte/file caps apply to the uncompressed archive content. ZIP input is not
accepted. Export is intentionally a portable evidence report, not a self-executing
environment: replay requires the matching installed curated runner/source/checker/
contract. Raw local diagnostics remain available in the immutable original attempt.
The curated artifact allowlist plus screening reduces leakage; it is not a general
secret-detection guarantee for arbitrary user-supplied source. Untrusted uploads are
outside this capability.

`portable_artifact_bytes` uses the same fixed portable artifact allowlist and content
screening as ZIP export. It verifies the whole stored attempt using `load_attempt`,
selects the artifact by its stored ID, then reads a bounded byte snapshot and checks
the snapshot's exact size/hash again before screening and returning those bytes.
Unknown IDs, excluded names, changed hashes, non-UTF-8 content and suspicious paths/
credentials raise `ValueError`. APIs can return the bytes directly instead of
reopening a path with `FileResponse`. The API must still authorize using its trusted
DB attempt mapping, compare the disk result to DB provenance, enforce its symlink
policy on ancestors and files, and avoid exposing local exception details. This
helper deliberately rejects raw logs, job files and environment diagnostics even
when they have valid original artifact hashes.

## Verification and current limits

`tests/evidence/test_workflow.py` uses explicitly labeled scripted runners for hash
tampering, size bounds, output preservation, identity changes, reduction legality,
failure-class preservation, harness/timeout rejection, and candidate regression
failure. These tests do not establish simulator support. On this Windows sandbox,
use `--basetemp=tests/evidence/.tmp-unit` to avoid blocked default temporary paths.

`tests/evidence/test_live_rtl.py` is opt-in with `VF_REAL_RTL=1`. It runs the prebuilt
Docker RTL image with 120-second per-plan budget, the real reset-mutant witness,
replay, two bounded reduction calls, safe export, and curated correct-fixture witness,
neighbor and regression checks. A skipped opt-in test is not real simulator evidence.
Set `VF_EVIDENCE_ORIGINAL` to an accepted immutable attempt directory to reuse its
exact witness instead of rerunning original discovery. The neighboring plan changes
write data and adds idle cycles; the regression fills the FIFO, exercises simultaneous
traffic at full, and drains past empty. All three inputs are distinct.
Actual command outcomes are recorded in the task delivery. No API endpoints or
persistence orchestration are added by this package; the API owner integrates these
hooks and stores records/authorization. The previously accepted FIFO live workflow
remains its historical gate; no repeat simulator run is claimed by VF-054B.

VF-054B adds `tests/evidence/test_families.py`: scripted arithmetic callbacks for
replay, legal reduction and candidate checks; strict sidecar rejection; discriminated
record round trips; a stubbed native preflight/dispatch test; and read-only load/export
of all three actual accepted VF-050B attempts independently audited by VF-057. The
saved source attempts are hash-snapshotted before/after these tests. A saved-artifact
test skips explicitly if those ignored local fixtures are absent. These reads and
exports do not execute arithmetic HDL, and the stubbed callback is not actual native
replay. **Live arithmetic replay/reduction/candidate validation remains unverified.**
API/core evidence routing remains the API owner's separate reviewed change; source
support here does not imply an endpoint or service upgrade is accepted. Native AXI
source adoption is documented below; arbitrary uploaded/generated patch execution
remains unsupported.

## Native bounded AXI-Lite evidence adoption

Source support now recognizes the reviewed `rtl.axi_lite.v1` descriptor and
`axi_lite.v1` experiment with exact `cycles`, not FIFO/arithmetic operations.
No bus observations are converted into FIFO fields. Native `observed.json` is
`axi_lite.trace.v1` with an observations array. Every row must explicitly contain
exactly cycle, aw_ready, w_ready, b_valid, b_resp, ar_ready, r_valid, r_data and r_resp.
The fixed `AxiLiteTrace`/`AxiLiteObservation` models enforce strict boolean signals,
integer cycles, 32-bit unsigned data and two-bit responses; full length/order must
match the stored exact experiment. Shared `AttemptResult.trace` stays empty.

The fixed module helper `veriforge.evidence.families.validate_family_trace(raw,
experiment,result,*,artifact_reader=None)` dispatches the registered arithmetic
or AXI schema, rejecting unknown schemas. `validate_axi_trace` provides the specific
AXI implementation. The loader supplies a bounded, path-contained reader that
rechecks every companion's exact manifest size/hash. No input-selected import,
command, uploaded checker, copied Python/schema execution or dynamic fallback exists.

AXI requires native `check.json` (`axi_lite.check.v1`) and copied checker.py,
checker-contracts.py, contract.json, environment.json, registers.sv and stimulus.json.
The checks bind copied oracle bytes to checker_hash, copied schema bytes to the
recorded environment contracts_hash, raw environment bytes/tool records to the
recorded environment identity, typed contract to canonical contract_hash/parameters,
raw source+trusted curated variant+parameters to source_hash, and exact typed cycles
to stored stimulus/experiment_hash. This does not import or rerun copied code and
does not require old snapshot bytes to equal the current installed implementation.
Authenticated producer/DB provenance is still required; consistency is not authority.

Native checked outcome must agree with result verdict, its failure requirement/class/
cycle/expected/observed and semantic signature hash. Entire executed observation
length and the checker's checked prefix are distinct: design failure checks through
first_failure_cycle+1, while executed usage retains the full original trace. Passing
requires a completely checked trace. Out-of-range prefixes, coerced booleans, missing
signal fields, duplicate JSON keys, malformed companions and contradictory identities
fail closed. This is recorded evidence/identity validation, not another mathematical
oracle verdict or a newly executed simulator result. Product-specific progress
requirements remain scoped by the unchanged contract, not universal AXI deadlines.

Portable names come from the trusted descriptor: common stimulus/contract/checker/
observations plus registers.sv, checker-contracts.py and check.json. No invented
axi-trace filename, generic FIFO sidecar, raw job/log or environment diagnostic is
exported. Native checker failure text is retained in check.json as bounded screened
semantic evidence, not an arbitrary execution log. Existing UTF-8, credential/path,
file count, byte, containment and duplicate-reference checks continue to apply.

Typed native replay preflight/dispatch and same-witness curated candidate/source
review utilities support the fixed AXI types through the registry union. Their
new AXI callback tests use labeled synthetic observations only. **AXI cycle reduction
is explicitly unsupported**: `reduce_failure` raises `NotImplementedError` before
creating an output directory or calling a runner. The operations-based reducer is
not applied to a cycle protocol. A future strategy needs its own acceptance for
handshake validity, bounded environments and same-semantic failure preservation.

`tests/evidence/test_axi_evidence.py` validates all seven previously accepted native
attempts read-only, exports them and checks all original file hashes unchanged.
It also tests rehashed malformed trace/check envelopes, oracle/schema/environment/
source/stimulus drift, typed stubbed native replay, synthetic candidate provenance
and unsupported reduction. No new HDL, model, Docker, build or runtime work ran.
Actual new AXI replay/candidate validation and backend/browser adoption remain
separate gates. API evidence capability must remain off until independent source
review and explicitly assigned backend adoption.
