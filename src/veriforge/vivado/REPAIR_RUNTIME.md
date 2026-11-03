# Fixed-profile candidate worker integration

`VivadoRepairRuntime` is a trusted host service for preparing, inspecting and
executing one candidate against retained original report evidence. Preparation
and inspection never invoke Vivado. The application must invoke `execute` only
in its engineering worker under current operational authority.

## Trusted startup

```python
from veriforge.vivado import ControllerInstallation, VivadoRepairRuntime

installation = ControllerInstallation(
    project_root=project_root,                 # absolute Path; canonical control lives here
    synthesis_controller=installed_synthesis,   # absolute Path; exact approved bytes
    routed_controller=installed_routed,         # optional absolute Path
    routed_recipe=installed_routed_recipe,     # optional absolute Path; exact fixed recipe
    launcher=installed_vivado_bat,              # optional absolute Path named vivado.bat
    launcher_sha256=reviewed_launcher_sha256,   # required when launcher is explicit
)
runtime = VivadoRepairRuntime(
    artifact_root=artifact_root,                # absolute, separate from input source trees
    installation=installation,
    owner=exact_main_reservation_owner,         # U6-* canonical engineering_tool owner
    hooks_factory=hooks_for_job,                # fresh RepairHooks for this opaque job ID
    requirements_document=requirements_path,    # absolute trusted document, or None
)
```

These are host configuration objects, not request payloads. Do not load paths,
hash admissions, launchers or Python from a browser, model or queue message.
The exact accepted synthesis/routed controller hashes remain compiled into
`repair_execution.PROFILES`; a configured path relocates the same bytes. The
private controller files are not distributed in public source. They must be
installed separately by the trusted host; absence or changed bytes fails closed.
Controllers need at least three parent levels because their unchanged import
initialization derives a legacy root. The adapter then binds the configured
ROOT, fresh ATTEMPT, OWNER, PLAN_HASH and optional pinned LAUNCHER before any
controller operation. Storage implementation identity is checked before loading
controller code and again against its imported storage module. Command/process
and cleanup primitives are unchanged.

The default compatibility adapter can still resolve the historical private
paths. Production startup should pass explicit `ControllerInstallation` rather
than rely on that historical layout. The installation identity hashes all path
bindings and the optional launcher hash, without projecting paths to clients.
The launcher hash identifies the batch file, not the complete tool installation;
exact tool/version, process ownership and cleanup still need qualification.

`hooks_for_job(job_id)` returns `RepairHooks(control_running, reservation_owned,
cancelled, available_physical_bytes, monotonic)`. The callbacks reread current
trusted controls and that job's durable cancellation state. They must not capture
a stale approval boolean. Native dispatch also rereads canonical RUNNING and the
exact exclusive engineering-tool owner during the controller's monitoring.
Startup requires 2GiB physical headroom; this is not a continuous quota.
Synthesis/routed deadlines remain300/600s. The existing bounded storage, stdout,
receipt, file and entry limits and owned Windows Job settlement remain in force.
No service method is permission to reserve resources or launch a vendor process.

## Main-owned API and queue wiring

1. Authenticate/authorize the project and exact original/candidate versions.
   Resolve their three immutable source files, retained `VivadoInvestigation`
   and trusted `RepairPins` from the host catalog/artifact store. A document hash
   identifies requirements; it does not authenticate their engineering validity.
2. Call `ticket = runtime.prepare(original, candidate, baseline, pins)` in the
   admission path. This copies separate snapshots and persists `job.json` with
   original/candidate plan hashes, baseline and installation binding. The actual
   requirements bytes must match the protected document hash when one is set.
3. Store a durable application mapping between the authorized project/versions,
   ticket.job_id, baseline hash and candidate-plan hash. Use the existing durable
   idempotency policy for repeated HTTP requests; calling prepare twice creates
   two distinct jobs. Return ticket/blockers. **Do not enqueue a blocked ticket.**
4. Queue opaque job_id, retained baseline identity and the originally admitted
   ticket.candidate_plan_sha256 from the trusted application mapping, independently
   of mutable job.json. Each worker process
   constructs its own matching runtime from trusted startup configuration and
   resolves the exact baseline, then calls
   `runtime.execute(job_id, baseline, expected_candidate_plan_sha256=retained_candidate_hash)`.
   An API installation does not install configuration in another process.
5. GET/status calls
   `runtime.inspect(job_id, baseline, expected_candidate_plan_sha256=retained_candidate_hash)`
   only after authorizing
   the durable application mapping. No arbitrary directory parameter is exposed.
   Persist/bind `result_sha256`, candidate projection/comparison and artifact
   identities in the application's normal evidence records. Version-authorized
   artifact downloads are a host concern; never return local filesystem paths.
6. Cancellation is stored in the existing application record and observed by
   hooks_for_job. This module does not create a second cancellation database.
   Main must wire durable queue, cancellation and evidence storage in its owned
   API/worker files; none is silently enabled by this service.

`runtime.inspect` returns `vivado.repair-job-projection.v1`: job/installation/
baseline/candidate-plan identities, execution_status, blockers, repair_status,
result and result_sha256. Result retains the existing
`vivado.repair-execution.v1` shape with candidate_plan_sha256 added, native receipt
identity, explicit runner provenance and the existing candidate projection.
No raw report/log text, argv, local installation/source paths or credentials are
returned. Preserve candidate comparison's scoped verdict rather than treating an
execution status as a design verdict.

Duplicate worker delivery returns an already persisted result without rerunning.
The expected candidate-plan hash is mandatory for inspection and execution.
Reload compares it before authority checks or effects. Rewriting source, pins,
plans and manifest together cannot replace the admitted candidate. The caller
must use its retained ticket identity, never take that expected value from job.json.

An operation claim without a final receipt is `quarantined`, including a crash
before tool startup; do not delete it to retry. Invalid/tampered artifacts raise
an integrity error and must not trigger automatic effects. Receipt persistence
failure returns a fixed quarantine projection without forwarding raw in-memory
data. Persisted results use a closed field/state schema: unknown fields and prose
are rejected. Bounded received report bytes are retained separately, re-parsed
through the unchanged checker, and compared against the canonical candidate
projection/comparison. Completion requires the admitted requirements/baseline,
native receipt, zero exit, settled cleanup, claim and complete report bindings;
it cannot coexist with a failure blocker or null candidate evidence. Host repair
job recovery must reconcile uncertain effects under a separate operational grant.

## Current requirements and acceptance

Current Exercise3 admission retains the original unchanged XDC and exact fixed
recipes. Its retained actual evidence has12 missing input delays,6 missing output
delays and20 TIMING-18 findings. Preparation exposes `missing_io_requirements`;
execution refuses dispatch even if a document hash is supplied. Neither a model
nor this implementation may invent delays, timing exceptions, functional expected
values or a source-derived specification to clear that blocker. A separately
reviewed requirements-bearing profile/baseline is needed; this task does not
widen XDC admission.

Before real use, qualify relocated controller/launcher binding and current API,
worker, durable cancellation and artifact mapping under a concrete main grant.
Focused tests cover filesystem snapshots/reload, fake protocol/report comparison,
pins, duplicate/uncertain delivery and receipt identity. Optional private tests
only import exact original controller bytes without invoking run_attempt or
NativeJob. None is new native execution. `repair_status` remains `not_validated`;
functional, CDC, reset, board and independent repair acceptance remain open.
