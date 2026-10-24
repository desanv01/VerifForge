# Evidence workflows — VF-040A

The evidence package implements local, controller-invoked workflows for the shared
FIFO contracts. It accepts typed experiments and trusted Python runner callbacks,
never shell strings, uploaded code, patch application, or model-generated commands.
Candidate execution is limited to a separately selected curated fixture revision.

## Public callable agreement

Import from `veriforge.evidence`. Paths are controller-selected local `pathlib.Path`
values. A runner has the shared signature
`runner(experiment: Experiment, output_dir: Path) -> AttemptResult`. The callback
must enforce the plan's wall-time, cycle, artifact and isolation limits, use a fresh
attempt directory, and persist exact experiment/result/artifact records. Production
callers must supply a trusted adapter; unit-test scripted callbacks are not simulator
evidence. The evidence module itself cannot attest that a supplied callback ran HDL.

```python
load_attempt(attempt_dir, *, limits=ExportLimits()) -> tuple[Experiment, AttemptResult]
replay_attempt(attempt_dir, output_dir, *, runner=None) -> ReplayRecord
export_bundle(attempt_dir, destination, *, limits=ExportLimits()) -> Path
portable_artifact_bytes(attempt_dir, artifact_id, *, limits=ExportLimits()) -> bytes
reduce_failure(attempt_dir, output_dir, *, runner, max_attempts=30) -> ReductionRecord
validate_candidate(
    attempt_dir, output_dir, *, candidate_fixture_id,
    candidate_runner, original_runner, neighboring_plans, regression_plans,
) -> CandidateValidation
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

## Replay

Replay consumes the stored exact experiment, including seed, operations and budget;
it calls no planner/model. With no callback, it delegates to the RTL adapter's
`replay`, which also checks currently installed checker/contract/source and expanded
stimulus. A supplied callback supports the isolated container adapter. Its returned
source/checker/contract and exact experiment identities must match the original.
Environment/tool versions remain in both result records; a different environment is
not silently represented as the original. A passing replay returns
`same_semantic_failure=False`. Missing identity is rejected, including unsupported
attempts that never established a checked design identity.

## Reduction

Reduction requires an original completed design mismatch or property failure.
It tries chunk deletion, individual deletion, reset/idle cycle reduction to one,
and value simplification to zero. Every candidate is fully revalidated using the
shared `Experiment` schema, so reset-before-traffic, widths and cycle limits hold.
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
match the original witness. The candidate plans change only fixture ID.

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

## Portable ZIP and report

Export verifies all original artifact references, then writes a new ZIP exclusively
outside the original attempt directory. It never overwrites originals or an existing
export. The bounded archive contains `manifest.json`, exact typed `experiment.json`,
`REPORT.txt`, and selected verified artifacts under `artifacts/`: `stimulus.json`,
`contract.json`, `fifo.sv`, `checker.py`, `observed.json`. Arbitrary artifact paths,
job descriptions, raw logs and environment diagnostics are excluded. The manifest
retains immutable hashes, actual tool versions, numerical trace and semantic failure
identity, omitting free-form expected/observed diagnostic strings and uncertainties.
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
hooks and stores records/authorization. Arithmetic, AXI-Lite and arbitrary patch
validation remain outside this FIFO milestone.
