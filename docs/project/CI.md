# GitHub CI preparation — VF-091

Status: workflow prepared on October 24, 2026. The first hosted run passed Python
and web checks but FIFO failed to write its evidence mount. The VF-091B UID/GID
correction below is statically validated; a passing hosted FIFO rerun is pending.
Main owns commits, pushes and branch protection. This is not deployment acceptance.

## Workflow and scope

`.github/workflows/ci.yml` uses one Ubuntu 24.04 lane per selected check, with no
version matrix. It runs on pull requests, pushes to `main`, and manual dispatch.
If the chosen default branch differs, main must adjust the push branch filter.
No schedules, release publishing, deployment, database services, or secrets are
configured. Contents permission is read-only; checkout credentials are not kept.
All actions are first-party `actions/*` pinned to verified full commit SHAs.

| Job | Selection | Work |
|---|---|---|
| Select checks | Every event | Git diff paths; no third-party filter action |
| Python checks | Python/source/tests/target/infra/lock changes | Python 3.12.13, uv 0.11.25, `uv sync --locked --dev`, existing tests with live suites excluded |
| Web | `apps/web/**` changes | Node 24, lock-keyed npm cache, `npm ci`, tests, typecheck, build |
| Real Linux FIFO smoke | FIFO/contracts/RTL tests/image/Python locks change | Existing approved image, real reference/mutant/three-replay check, actual build failure, timeout/cancel process cleanup |
| CI gate | Always | Requires selection success and every selected job to pass |

Changes to the workflow itself and manual dispatch select all checks. Initial
pushes or unavailable diff bases conservatively select all. Pull requests use a
merge-base diff; rename detection is disabled so moving a relevant file out of its
directory still selects its old path. UI-only changes select web checks and skip
the simulator build. Documentation-only changes complete the selector and gate.

The workflow is not filtered out at the trigger level. This lets the single
`CI gate` status settle on documentation/UI-only changes; after actual successful
hosted validation, main can choose this stable status for required checks. A job
failure or cancellation fails the gate. Individual skipped jobs do not establish
acceptance of their subsystem.

## What each result means

Python checks include schemas, independent oracles, controlled backend/process
tests, and SQLite-backed API tests. Integration directories, actual Docker evidence
workflow, gated arithmetic HDL cases, and the two native FIFO simulation test
nodes are explicitly excluded here. Ordinary Linux process cleanup tests still
run; no live provider or proprietary tool is invoked. As tests are added, their
owners must keep this no-live selection boundary accurate.

The separate FIFO job builds `infra/rtl/Dockerfile`, then requires a real Linux
Verilator executable, cocotb 2.0, and discoverable shared libpython before tests.
This prevents missing tools from silently skipping the smoke into green. It uses
the existing directed 120-second experiment budget and existing test nodes rather
than a synthetic expected result. The image pins the accepted Verilator base;
its Python subset is separate from the application's uv-locked environment.

FIFO execution disables networking, drops capabilities, applies read-only root
and source mounts, and limits CPU, memory, PIDs and executable temporary storage.
Both simulator preflight and test containers explicitly use the hosted runner's
numeric UID/GID, with a guard rejecting UID zero. New evidence directories use
umask 077 and must be owned/writable by that runner before launch. This lets the
non-root container write its dedicated evidence bind mount without changing
workspace permissions, using a root process, or making the directory world-writable.
An eight-minute outer timeout plus named-container cleanup bounds the whole smoke.
The image build is limited to eight minutes and the entire job to twenty. A build
or timeout failure does not claim a functional design defect. Cold image pulls
and unmeasured hosted-runner throughput may require later evidence-based tuning.

The [initial FIFO job](https://github.com/desanv01/VerifForge/actions/runs/40053182108/job/120750191039)
built the image and observed Verilator 5.042/cocotb 2.0.1, then failed with
`PermissionError: /evidence/results.xml` before functional checks. The image's
default identity did not own the host-created output directory. Explicit numeric
UID/GID mapping addresses that mount boundary; it does not weaken test selection
or the fail-closed aggregate gate. No local rebuild/full suite was used to claim
the correction passed; the next hosted revision must establish that result.

Meta calls, Vivado execution, arithmetic HDL, PostgreSQL/Valkey/RQ integration,
application migrations against a real service, and full browser/runtime acceptance
are **outside this CI gate** until separately accepted and explicitly integrated.
No provider key, license, production environment, or external service credential
is expected. Existing mocked provider/import-only checks do not close live gates.

## Results and publication limits

JUnit and curated FIFO evidence are generated under `ci-artifacts/` on the
ephemeral runner. There is no artifact-upload step; these files are not retained
after the job. Standard job status/test console output remains the CI record.

Future artifact retention should allowlist bounded JUnit results and curated FIFO
evidence, use a short retention period, and exclude provider credentials, private
project sources and imported tool logs. Review artifact contents and access before
enabling upload steps; the current workflow does not transfer these files.

## Publication path audit and main-owned prerequisites

The path-only audit used `git ls-files`, `git status --short`, and
`git ls-files --others --exclude-standard`. No credential values were read. This
does not substitute for a content/secret review of the final staged revision.

- Existing excludes cover `.env` and `.env.*` (except `.env.example`), private
  `.coordination/`, `.runtime/`, `.uv-cache/`, virtualenvs, dependency caches,
  generated distributions, databases, simulator build directories and waveforms.
  Target/infra-specific ignores also retain local evidence outside publication.
- Initial inspection found hundreds of untracked generated files under
  `tests/acceptance/.tmp-*`. Main was asked to add `**/.tmp*` and `ci-artifacts/`
  to root `.gitignore` and reported those exact edits complete. A subsequent
  path scan found zero untracked `.tmp` contents.
- The tracked-path scan matched `.env.example` only among env/private/generated
  patterns; that template is intentionally allowed. No runtime `.env`, private
  coordination directory, database, waveform, archive or log path was tracked.
- Intended frontend source plus `apps/web/package-lock.json`, registry/arithmetic
  packages and tests, and in-progress API/migration changes were still untracked
  during inspection. Main must stage a coherent reviewed source snapshot before
  expecting this workflow to resolve all modules and scripts. Do not blanket-add
  local generated evidence to satisfy missing-source errors.

No root dependency/lock edit is required by this workflow. It consumes the current
`pyproject.toml`/`uv.lock` and matching frontend dependency sections in
`package.json`/`package-lock.json`. No package installation was done for preparation.
Main should review the repository visibility and staged paths, run the first
actual CI, and configure required checks only after its behavior is observed.

## Static validation performed

Local host PyYAML parsed the workflow using `BaseLoader` so YAML 1.1 does not
reinterpret the GitHub `on` key as a boolean. Structural checks verified five
bounded jobs, dependency/gate links, read-only permissions, first-party 40-character
action pins, absent secrets/uploads/privileged triggers/schedules, and disabled
checkout credential persistence. Both embedded Python programs parsed/compiled.
The isolated path classifier passed seven routing scenarios: UI-only, shared
contracts, docs-only, arithmetic-only, Python lock, workflow edit, and force-all.
Seven isolated gate scenarios checked intentional skips, selected failures,
cancellation, unexpected selected skips, success, missing selector outputs and
selector failure.
Project and frontend lock/script metadata were parsed and compared statically.

No project tests, Node install, web build, Docker build/run, migration, Git commit,
remote push, workflow dispatch or simulator execution was performed in VF-091.
`actionlint` was unavailable locally; actual GitHub workflow validation is pending.

## Official documentation checked

- [GitHub workflow syntax](https://docs.github.com/en/actions/reference/workflows-and-actions/workflow-syntax)
- [Official uv GitHub guidance](https://docs.astral.sh/uv/guides/integration/github/) and [uv 0.11.25 release](https://github.com/astral-sh/uv/releases/tag/0.11.25)
- [Checkout action](https://github.com/actions/checkout) and [Python setup action](https://github.com/actions/setup-python)
- [Node setup action](https://github.com/actions/setup-node) and [v7.0.0 release](https://github.com/actions/setup-node/releases/tag/v7.0.0)
- [Docker run user selection](https://docs.docker.com/reference/cli/docker/container/run/#user) and [bind-mount permissions](https://docs.docker.com/engine/storage/bind-mounts/)

The chosen checkout/Python full pins are in uv's official current integration
examples. The Node full pin was resolved from its official release commit. These
sources establish workflow/action syntax; they do not establish a hosted green run.
