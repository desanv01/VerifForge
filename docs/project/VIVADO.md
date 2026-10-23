# Vivado investigation adapter — VF-060A

Support level: **import-only**. Inventory on October 23, 2026 found no Vivado
executable on PATH or in the bounded ordinary Windows install locations below.
No tool version, device support, license availability, synthesis recovery, HDL
functional behavior, timing, or equivalence has been validated. Phase 6's live
acceptance gate remains blocked. No installation or license changes were made.

## Available interface

`veriforge.vivado` exposes immutable Python records and these local operations:

- `inventory()` inspects PATH and fixed-depth layouts beneath `C:/AMD`,
  `C:/Xilinx`, `C:/Program Files/AMD`, `C:/Program Files/Xilinx`, `D:/AMD`,
  `D:/Xilinx`, and `C:/tools/Xilinx`. It never starts a vendor process and never
  promotes a discovered executable to validated support. Present AMD folders
  contained only driver installer/CIM directories in this inventory.
- `prepare_attempt(output_root, Recipe)` writes a unique curated fixture copy.
- `prepare_repair_pair(output_root, Recipe.WRONG_TOP or Recipe.MISSING_SOURCE)`
  writes independent baseline and candidate directories. Candidate linkage is
  explicit. Source bytes stay identical; only the approved recipe changes.
- `import_log(log_path, output_root)` reads at most 1 MiB and writes a unique
  directory containing `vivado.sanitized.log` and `observations.json`.

CLI examples from the repository root, using its provisioned Python 3.12:

```powershell
.venv/Scripts/python.exe -m veriforge.vivado inventory
.venv/Scripts/python.exe -m veriforge.vivado prepare wrong_top --output targets/vivado/.tmp-attempts
.venv/Scripts/python.exe -m veriforge.vivado import-log path/to/local/vivado.log --output targets/vivado/.tmp-imports
.venv/Scripts/python.exe -m pytest tests/vivado -q --basetemp=tests/vivado/.tmp-tests
```

These commands prepare or import only. No command executor or unrestricted Tcl
interface exists. The local operator selects input/output paths; they must not
be exposed directly as unauthenticated remote API inputs. No shared dependencies,
contracts, API, queue, or global configuration were changed in this scope. The
current shared wire contract is FIFO-specific; these adapter records are local
integration candidates, not a silently extended shared schema.

## Fixture and evidence boundaries

The deliberate project configuration errors use a small synchronous eight-bit
counter: reset clears count; each other clock edge increments modulo 256. The
`wrong_top` recipe selects a nonexistent top module. `missing_source` references
a nonexistent file. The candidate selects `vf_counter` and `counter.v`. This is
project repair preparation, not a discovered vendor defect or validated repair.

Each prepared directory contains `counter.v`, `recipe.tcl`, `manifest.json`, and
`prepared.json`. Hashes identify the source, exact recipe, and manifest. The
manifest records target part `xc7a35tcpg236-1` (availability unverified), intended
functional contract, required checks, parent attempt, and reviewed batch argv.
All execution and check statuses remain `not_run`/`prepared`. The 120-second
stage timeout is proposed metadata for a future runner; nothing currently
enforces it because nothing executes. Run any eventual approved recipe with its
own attempt directory as cwd so all generated files stay isolated.

Tcl emits version/stage markers and uses explicit failure exits after caught
source, synthesis, and utilization-report errors. It does not suppress tool
errors or relax design constraints. Recipe generation accepts only enum values;
no model-controlled paths, source, shell fragments, Tcl, top, or part enter it.

Imported reports preserve the original byte hash, byte count, sanitized hash,
parser version, reported version, stage observations, severity, stable message
IDs, and an error signature based on version/stage/IDs. Display is limited to
200 messages and 200 stage observations, with 2,000 characters per message.
Failure classification and signature still consider errors beyond those caps.
This signature groups diagnostics; it is not a proven root-cause identity or a
vendor crash signature, since imported process termination is unverified.

`execution_status=completed` means the **import** completed. Vendor execution is
`unverified_import`; build is `reported_passed`, `reported_failed`, or `unknown`;
functional status is always `not_run`. Only the controlled `VF_BUILD passed`
marker yields reported success, and errors override it. A blank, partial, or
timeout-only log remains unknown and cannot establish a design defect. Imported
markers are untrusted claims and cannot satisfy live acceptance.

Only sanitized text is copied into artifacts. Common key/token/password/bearer,
license-variable values, URL credentials, and Windows user path names are
redacted. Redaction is best-effort, not a guarantee for arbitrary secrets; review
before sharing. The original remains local and unchanged, with its hash recorded
for identity. Raw text and arbitrary environment variables never enter a model
call here. All parser tests use synthetic diagnostic strings, not real Vivado
execution evidence.

## Remaining live gate

A separately assigned runner integration must authenticate/lease work, permit
one active job, launch only verified immutable recipes, verify source/recipe
hashes before execution, scrub controller/model credentials from child jobs,
bound wall time and output/disk use, terminate process trees on cancellation,
record actual exit status/tool/environment/part support, capture logs/journals,
and preserve original project identity. No live runner claim is made here.
An independent protected HDL checker must exercise counter reset, increment and
wraparound; its identity and stimulus must accompany any functional result.
Build recovery, functional checks, timing, and equivalence require separate
reported outcomes. Install/device/license feasibility and actual synthesis
recipe compatibility must be checked before attempting the live gate.

## Official documentation verified

Reviewed current AMD 2026.1 documents (June 23, 2026 release) on October 23:

- [UG892: batch Tcl launch](https://docs.amd.com/r/en-US/ug892-vivado-design-flows-overview/Launching-the-Vivado-Tools-Using-a-Batch-Tcl-Script)
  documents `vivado -mode batch -source` on Windows and Linux.
- [UG904: synth_design](https://docs.amd.com/r/en-US/ug904-vivado-implementation/synth_design)
  describes source synthesis, top/part options and the in-memory design flow.
- [UG835: read_verilog](https://docs.amd.com/r/en-US/ug835-vivado-tcl-commands/read_verilog)
  documents explicit source loading and warns that `-quiet` can ignore errors;
  these recipes do not use it.

Documentation review establishes API intent, not installed-tool compatibility.
