# Vivado investigation integration

Implemented additive APIs, exported from `veriforge.vivado`:

```python
investigate_vivado_reports(project, execution, reports, parent_investigation_sha256=None)
compare_vivado_candidate(baseline, candidate)
vivado_projection(investigation, comparison=None)
```

`project` is `VivadoProjectIdentity(part, top, clock_period_ns, sources,
recipe_sha256, functional_specification_sha256=None)`. `sources` is an immutable
tuple of the three `readiness.SourceIdentity(name, sha256, size_bytes)` records.
Supported project configuration is the admitted Exercise3 top on
xc7a200tsbg484-1, with explicit clock/source/XDC identities. A changed identity is
not automatically a reviewed supported hardware configuration.

`execution` is `VivadoExecution(status, exit_code, receipt_sha256)` supplied by a
trusted host that has resolved the native receipt and cleanup/resource outcome.
The adapter does not authenticate a receipt from its hash or launch a tool.
`reports` maps fixed `*.rpt` names to original bytes, at most1MiB each/8MiB total.
Unknown names reject. The report loader belongs to the host's existing authorized
artifact store, not an arbitrary client path. No client shell/plugin/Python load.

## Stable JSON shape

`vivado_projection` returns:

```text
schema_version: "vivado.projection.v1"
investigation:
  schema_version, investigation_sha256, parent_investigation_sha256: string|null
  project: {part, top, clock_period_ns, sources:[{name,sha256,size_bytes}],
            recipe_sha256, functional_specification_sha256:string|null}
  execution: {status, exit_code:int|null, receipt_sha256}
  design_phase: "synthesis"|"routed"|"mixed"|"unknown"
  tool_version: string|null                  # version+build, no host names
  parser_version, parser_sha256: string
  reports: [{name,sha256:string|null,size_bytes:int|null,availability,
             design_phase:string|null}]
  findings: [{code,severity,scope,evidence_reports:[fixed report names]}]
  missing_input_delays, missing_output_delays, methodology_missing_io: int|null
  internal_timing: {wns_ns,tns_ns,whs_ns,ths_ns,wpws_ns,tpws_ns} # or empty object
  fully_routed_nets, routing_errors, drc_reported_checks: int|null
  complete_report_set: bool
  provenance: "recorded_report_interpretation_not_new_execution"
  new_execution: false
  design_verdict: "not_evaluated"
  functional_status, cdc_status: "not_established"
  repair_status: "not_validated"
candidate_comparison: null | {
  schema_version, comparison_sha256, baseline_sha256, candidate_sha256,
  status, unchanged_requirements:bool, changed_sources:[fixed names],
  required_rechecks:[fixed report names], blockers:[fixed codes],
  external_validation_required:[fixed codes], repair_status, functional_status
}
```

All outputs are serializable with standard JSON. Nullable observations are unknown,
never zero. Raw logs, report text, local host/project paths, argv and credentials
are not projected. Headerless native route_status is `parsed_context_bound`:
its bytes/counts are associated with the version-scoped bundle/receipt, not
fabricated standalone design/tool header identity. It cannot establish phase by
itself. Other supported report headers bind exact command/design/device,
Vivado2026.1 version/build and synthesized versus routed phase. Clock report must
match the declared period. Mixed/unrecognized identities withhold metrics.

## Candidate and authority boundaries

Candidate records bind the baseline investigation hash as their parent. Comparison
rehashes immutable records, requires that parent, the same phase/tool/parser,
unchanged part/top/clock/XDC/specification identity, complete required reports,
non-regressing missing-I/O coverage, and reported route/DRC clearance where
applicable. Changed HDL identities and all affected report rechecks are exposed.
Synthesis-to-route is a different characterization phase, not a repaired timing
result. Same-phase negative-to-nonnegative timing can be labeled
`reported_internal_timing_improvement`; it is still recorded report evidence,
not an independently authenticated functional repair. No candidate action or
tool execution is triggered by serializing these records.

External I/O requirements, asynchronous ingress/reset/word coherence and an
independent functional check remain explicit required validations. Known-source
async findings come from the admitted source hashes; changed-source topology
stays unreviewed. Supplying a specification hash identifies a document but does
not execute or authenticate it. Favorable CDC text and configured DRC counts
cannot establish board correctness. Repair execution/validation capability is
not advertised as complete by this report-comparison module.

Main owns wiring into the existing version-scoped `engineering.projection.v1`
response: authorize project/version, resolve bounded original report bytes and
host-owned source/receipt identities, invoke these functions, bind/store the
projection hash and expose only version-authorized artifact IDs for downloads.
UI can render the nested payload, map finding report names to those IDs, and
display blockers/required rechecks without creating a new execution control.
This module grants no access, provenance authentication or independent-review
authority. Host must freeze parser/source versions during invocation; parser
hash identifies the loaded source file, not an arbitrary hot-patched process.

Focused tests use synthetic report bytes. Separate read-only basic integration
against retained native synthesis/routed reports is recorded in the worker
delivery; it is an import/interpretation operation, not a new native run. Existing
`import_log` and recorded-query/attempt APIs retain their contracts.
