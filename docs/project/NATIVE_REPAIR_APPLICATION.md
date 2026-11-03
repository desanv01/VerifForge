# Native candidate application integration

This local integration compares two existing immutable engineering admission v3
versions from the same owned project and native domain. Source implementation
and fake-boundary checks do not establish actual Vivado/KiCad execution,
functional correctness, physical compatibility or an accepted repair.

## Application surface

`POST /api/eda-repairs/{vivado|kicad}` takes only `project_id`,
`original_version_id` and `candidate_version_id`, plus `Idempotency-Key`.
The client preserves that exact request/key before sending it. An uncertain
response must be reconciled using the same key; it is not permission to start
another native operation.

`GET /api/eda-repairs/jobs/{job_id}` and `/report` expose bounded job metadata.
`POST /api/eda-repairs/jobs/{job_id}/cancel` records cancellation. Reports retain
execution/evidence identities separately from design verdicts. Quarantine may
retain a settled completion after authority withdrawal; it remains quarantine,
and its recovery journal must not be replaced by a new comparison.

The existing dispatcher scans a bounded batch and queues only opaque job IDs.
Durable database claims prevent duplicate native execution. Expired, cancelled
or unauthorized jobs are fenced. Uncertain preparation or effects require explicit
recovery; the worker does not automatically steal a claim or rerun native work.

## Trusted installation

Every process starts without a native runtime. There is no public enablement,
installation, arbitrary command, file path or grant endpoint. Trusted application
bootstrap must call `install_eda_application` separately in the API, dispatcher
and job-worker processes, supplying their existing session factory, the same
reviewed catalog, a separate owned output root, explicit native host objects,
and fresh policy/control callbacks. A failed replacement clears the old runtime.

```python
from veriforge.core.eda_application import install_eda_application

install_eda_application(
    sessions,
    output_root=trusted_separate_output_root,
    catalog=independently_reviewed_native_catalog,
    vivado=trusted_vivado_host,  # None leaves this domain unavailable
    kicad=trusted_kicad_host,
    policy_reader=fresh_policy_reader,
    control=fresh_control,
)
```

These names denote trusted installation objects, not strings loaded from an API
request. Installation constructs bindings; it does not execute tools or migrate
the database. The host must already install the existing v3 review authorities.
The native catalog binds exact version/project/revision/content, requirements,
configuration, installation and retained provenance. Hash consistency alone does
not establish independent engineering review or native capture authenticity.

Vivado resolves the three supported source files through the immutable artifact
store, preserves missing I/O requirements as blockers, and uses the fixed reviewed
controller/profile. KiCad additionally requires independently reviewed association
of the seven raw native files with the stored exported netlist/board evidence;
the admission record alone cannot reconstruct those files. No association or
I/O specification is inferred or generated automatically.

## Schema and operational gates

Effectful entry points check enabled policy/control, then require exactly one
Alembic revision `15edarepairjobs` and the model's table/column/type/key/FK shape
before job access. Unknown, old or partial schemas refuse with a safe conflict.
No automatic migration or table creation occurs. Installed runtimes retain
schema-checked status, cancellation and explicit quarantine access after execution
authority is withdrawn, so retained evidence can still be inspected. A process
with no installed runtime refuses before database access.

Revision15 adds the native job table. Applying it to a real database, qualifying
current-data restore, supplying reviewed native associations and configuring
actual queue/resource/cleanup ownership are separate operational steps. Schema
inspection is not a complete audit of administrator changes or SQL triggers.
Actual native acceptance must preserve selected source/checker/requirements
identities, tool versions, results and settlement. The fixed KiCad ignored-rule
scope and Vivado I/O/function/board limitations still apply.
