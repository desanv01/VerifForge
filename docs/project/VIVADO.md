# Vivado investigation adapter — VF-060A

Accepted design-investigation support level: **import-only**. On October 29, readiness found
an installed Vivado 2026.1 in the current AMDDesignTools layout and a suitable
Nexys Video source candidate. Version-only output was captured; no project job
has run under this adapter. A separately granted bounded device query subsequently
passed (see below); synthesis/report, functional, timing, CDC and repair acceptance
remain unvalidated. The October 23 bounded inventory
covered older roots and found no launcher there or on PATH; it was not a full
disk inventory. No installation or license changes were made.

## Available interface

`veriforge.vivado` exposes immutable Python records and these local operations:

- `inventory()` inspects PATH and fixed-depth layouts beneath `C:/AMD`,
  `C:/Xilinx`, `C:/Program Files/AMD`, `C:/Program Files/Xilinx`, `D:/AMD`,
  `D:/Xilinx`, `C:/tools/Xilinx`, `C:/AMDDesignTools`, and `D:/AMDDesignTools`.
  It never starts a vendor process and never
  promotes a discovered executable to validated support. Present AMD folders
  contained only driver installer/CIM directories in the older-root inventory.
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

## October 29 readiness source — U6

Observed launcher: `C:/AMDDesignTools/2026.1/Vivado/bin/vivado.bat`. A version-only
invocation reported v2026.1, SW build 6511674, IP build 6504888 and SharedData
build 6501428, and returned exit 1. The first restricted invocation could not
enter the vendor launcher directory; narrowly reviewed escalation produced the
banner. This is version evidence, not a successful project/licensing check.
The normal `inventory()` function deliberately does not invoke the tool and
keeps its tool/device acceptance fields unverified.

On-disk Artix7 device directories include xc7a200t, xc7a100t and xc7a35t; the
xc7a200t_sbg484.bsd package metadata exists. These files are useful readiness
clues, not an accepted `get_parts` response or license/device usability proof.

`readiness.py` provides two enum-only trusted recipe sources, never an executor:

- `BatchRecipe.DEVICE_QUERY`: version and exact `get_parts` counts for
  xc7a200tsbg484-1, xc7a100tcsg324-1 and xc7a35tcpg236-1. Proposed 60-second cap.
- `BatchRecipe.EXERCISE3_POST_SYNTH`: approved Exercise3 HDL/XDC, top
  exercise3_nexys_video_top and part xc7a200tsbg484-1, two tool threads, followed
  by check_timing, timing summary including unconstrained paths, clocks, CDC,
  methodology and utilization. Proposed 300-second cap. It generates no bitstream
  and programs no hardware. Failed reports are explicitly unavailable, not clean.

`inspect_exercise3(local_source_directory)` reads only three fixed reviewed file
names, at most 1 MiB each, rejects containment/hash drift and returns immutable
source identities. Private HDL/XDC content is not bundled in the repository.
`plan_batch(launcher, snapshot_directory, recipe)` returns literal argv and script
hash without writing/copying/executing anything. Query planning requires an empty
attempt directory; synthesis planning requires exactly the three admitted files,
rejecting original work directories with existing scripts or generated outputs.
After separate main authorization, a trusted slot owner must copy to a fresh
private attempt, rehash every byte immediately before dispatch, write the exact
`render_batch_recipe(recipe)` text as `recipe.tcl`, and run only:

```text
<reviewed-vivado-launcher> -mode batch -source recipe.tcl -log vivado.log -journal vivado.jou
```

The cwd must be that private attempt. No XPR, project hooks, user Tcl, original
build script, upload or model-supplied command may be executed. The plan's caps
are metadata until a separately reviewed executor enforces time, process-tree,
output/disk and credential/init-hook isolation. No batch job was authorized in
the readiness slice. Original project/source locations are recorded privately.

The selected local Exercise3 XDC has a 10 ns clock, preserved without relaxation
or added exceptions. It lacks I/O delay requirements; physical board controls
are external asynchronous inputs. Post-synthesis timing is an estimate, not
post-route closure. CDC reporting only analyzes endpoints with defined source
and destination clocks and appropriately constrained I/O; a single-clock empty
CDC report cannot prove external-input safety. Methodology rules depend on stage;
post-synthesis capture is not implementation signoff. Functional checks remain
not_run and are separate from successful synthesis/report capture.

[AMD CDC guidance](https://docs.amd.com/r/en-US/ug835-vivado-tcl-commands/report_cdc),
[check_timing](https://docs.amd.com/r/en-US/ug835-vivado-tcl-commands/check_timing),
[timing summary](https://docs.amd.com/r/en-US/ug835-vivado-tcl-commands/report_timing_summary),
and [methodology](https://docs.amd.com/r/en-US/ug835-vivado-tcl-commands/report_methodology)
were checked against 2026.1 documentation on October 29. The new default install
root is documented in [UG973 installer notes](https://docs.amd.com/r/en-US/ug973-vivado-release-notes-install-license/Installer).

The [official Digilent Nexys Video Rev A master XDC](https://github.com/Digilent/digilent-xdc/blob/master/Nexys-Video-Master.xdc)
confirms clk at FPGA package ball R4, LVCMOS33, 10 ns. Connector logical signals,
physical header pins and FPGA balls are distinct facts; consult the board source
and [Pmod specification](https://digilent.com/reference/_media/reference/pmod/pmod-interface-specification-1_3_0.pdf)
for a carrier mapping. No connected PicoRV32/carrier FPGA execution is claimed.

Official master-XDC bytes read on October 29: 28,746 bytes, SHA-256
`2debf0afb537c0a9f8f2c1569aa99e0366629a354f2fbdf369bd5142670086cc`.
This identifies the reviewed content even if the upstream master branch changes.
Proposed JA mapping for later connected-carrier review (all signal pins LVCMOS33):

| HDL port | FPGA ball | JA connector pin |
|---|---|---|
| ja[0] | AB22 | 1 |
| ja[1] | AB21 | 2 |
| ja[2] | AB20 | 3 |
| ja[3] | AB18 | 4 |
| ja[4] | Y21 | 7 |
| ja[5] | AA21 | 8 |
| ja[6] | AA20 | 9 |
| ja[7] | AA18 | 10 |

The official XDC schematic labels bind those signal pins; the Pmod specification
defines expanded-GPIO pins 5/11 as GND and 6/12 as VCC. Its 2x6 numbering is
nonstandard and mirrored between host and peripheral connector views. A KiCad
generic odd/even numbered footprint must not be assumed to implement this mapping.
These interface facts do not establish connector MPN, voltage/current limits for
an actual attached circuit, mating orientation or completed PCB correctness.

Current capability split: Import implemented/tested; Execute source preparation
only, not validated; Repair not validated. Sequence 4 remains incomplete.

### Granted device-query attempt, October 29

One separately granted fixed-query attempt terminated during Vivado startup on
the proposed 4 MiB attempt-directory budget. Startup populated a local vendor
Tcl-store cache before the script produced any part-count records. Wall time was
14.422 seconds, launcher exit 1; only 148 bytes reached stdout. The owned Windows
job was terminated and independently reported zero remaining active processes.
No synthesis or original user script ran, and no automatic retry followed.

The directory monitor observed 5,248,723 bytes at its next polling sample, then
stopped the tree; this was a failed budget gate with polling overshoot, not a
claim that all directory writes were held below 4 MiB. Raw evidence was retained
privately, including the unsuccessful wrapper's final assertion. This reveals
first-use cache overhead to account for in a separately reviewed isolation/budget
plan. A new query grant is required after that review. Installed-part usability
and Execute/Repair maturity remain unvalidated.

### Query retry succeeded — October 29

A separately granted retry of the unchanged device-only script exited 0 in
11.656 seconds. Vivado reported version 2026.1 and count 1 for each requested
part: xc7a200tsbg484-1, xc7a100tcsg324-1 and xc7a35tcpg236-1.
Actual stdout was 958 bytes; final storage was 5,245,311 cache bytes plus 42,554
result bytes (5,287,865 total, 191 files). No approved ceiling was crossed:
60 seconds, 60 MiB cache, 4 MiB results, 1 MiB stdout, 64 MiB total, 4096 files.
Owned Windows-job cleanup verified zero active descendants. An independent
read-only audit verified all 190 receipt-listed artifact hashes/lengths.

Successful startup used a 184-file subset of the installed store; the installed
2037-file inventory was a conservative growth bound, not a measured full-copy
requirement. Earlier incomplete-attempt inferences are superseded by this result.
This establishes tool/part-query readiness only. No synthesis, project report,
functional check, repair or programming ran, and no automatic synthesis grant
follows. Import remains the accepted design-investigation capability.

### First granted post-synthesis attempt — resource-limited, October 29

One fixed private-copy attempt admitted the three exact HDL/XDC hashes and
started synthesis on xc7a200tsbg484-1 with the unchanged 10 ns constraint and
two-process engine setting. It terminated at **115.719 seconds** on the approved
4 MiB non-cache output limit, before a synthesis-success marker or any of the
six requested reports appeared. This is a resource-limited attempt, not a
design mismatch or timing/CDC verdict. No retry, route, programming or patch ran.

The monitor observed 6,418,119 non-cache bytes at termination; the write-burst
overshoot is retained rather than described as an atomic cap. Largest outputs
were internal `.Xil/incrSyn` elaboration records, including two approximately
2.3 MB records, not completed timing reports. Final independent accounting after
receipt writing found 7,295,738 cache bytes and 6,478,989 non-cache bytes, 318
files total. Stdout was 8,614 bytes; total remained below 64 MiB, but the separate
result limit failed. Owned Windows-job cleanup verified zero active processes.
All 317 listed artifact hashes and three original/copied source identities were
rechecked unchanged. A new scratch/output budget needs separate review before
another synthesis attempt. Design Execute/Repair acceptance remains incomplete.

### Typed recorded-attempt inspection

`inspect_recorded_attempt(directory, BatchRecipe)` is an additive read-only API.
It imports fixed receipt, plan and stdout filenames, with a 1 MiB bound per file,
and inspects only six fixed report names. It does not launch Vivado or follow
receipt-provided artifact paths. Existing `import_log` contracts are unchanged.

The frozen `RecordedAttempt` can be converted with `dataclasses.asdict` for a
manifest/API consumer. It contains allowlisted version/selected-part facts,
source and XDC identities, the recorded 10 ns clock, recipe/receipt/plan/stdout
hashes, exit/wall/cleanup observations, storage budget and observed counters,
build status and individual report availability. It contains no private paths,
argv, raw log text, arbitrary diagnostic strings or environment values.

The selected part query maps to `recorded_available`, native execution
`completed`, and build `not_run`. The interrupted synthesis maps to native
`resource_limited`, build `incomplete`, all six reports `absent`, and design
verdict `not_evaluated`. Timeout and unfinished cleanup remain execution states.
The storage sample is the receipt's before-receipt observation, not a fresh
directory measurement. Excess counters without a limit status are rejected.

Other explicit report states include `incomplete`, `unavailable`, `missing` when
a captured marker lacks its file, and `unverified` for an unmatched hash/marker.
`captured` requires a matching terminal marker and bounded file hash; it still
has verdict `not_evaluated`. No timing or CDC correctness parser is implied.

All results retain provenance `recorded_import_not_new_execution` and
`new_execution=false`. Internal hash agreement is not receipt authentication.
Source identities establish recorded plan/receipt agreement, not a new read of
private RTL. Device readiness does not authorize a native job or establish
functional correctness, routed timing closure, CDC safety or accepted repair.
The current native retry is blocked by the two below-threshold memory readings;
this importer performs no memory polling or execution retry.

Tests use small allowlisted recorded extracts under
`tests/vivado/fixtures/recorded`; private originals and failed-attempt outputs
remain preserved. Main owns any later shared API/system-manifest wiring.
