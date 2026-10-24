# Contributing to VerifForge

VerifForge is under active development. Small, reviewable changes with reproducible evidence are welcome. Read the [README](README.md), [shared interfaces](docs/project/INTERFACES.md) and relevant component documentation before changing behavior.

## Discuss and report

Use the repository's issue templates for a reproducible bug or focused feature proposal. State the affected target family and contract, what you expected, what happened, and whether the result came from an actual tool or a mock. Report suspected vulnerabilities through [SECURITY.md](SECURITY.md), without posting sensitive evidence publicly.

Keep reports minimal. Remove API keys, passwords, license details, private HDL, local usernames and absolute private paths from attachments. Retain non-sensitive contract/source/checker hashes, tool versions and exact legal stimulus when they are needed to reproduce the problem.

## Set up source development

Use Python 3.12 and uv for the backend, and Node.js 24 with npm for the frontend. Install from the committed locks:

```sh
uv sync --locked --dev
uv run --no-sync pytest tests/contracts -q
```

```sh
cd apps/web
npm ci
npm test
npm run typecheck
npm run build
```

The [local runtime guide](docs/project/LOCAL_RUNTIME.md) covers PostgreSQL, Valkey/RQ, migrations and the Linux worker. Windows hosts use Linux containers for simulation. The [FIFO toolchain guide](docs/project/RTL_TOOLCHAIN.md) documents actual simulator prerequisites. HTTP/async tests require loopback socket access; controlled-process tests may create and terminate child processes. Choose suites relevant to your change and record the commands you actually ran.

## Preserve the investigation boundary

- Models propose typed operations; trusted adapters construct tool commands and independent checkers decide verdicts.
- Preserve the reviewed contract and checker during an investigation. An intentional semantics change needs an explicit new version and compatible consumers.
- Keep execution status separate from design verdict. Timeout, infrastructure failure and build success are not functional conclusions.
- Preserve exact stimulus, seed, source/checker/contract identity, tool versions, artifacts and failure signature. Never overwrite an earlier attempt to make a new one appear to pass.
- Use installed curated fixtures. Arbitrary shell commands, uploaded Python execution and unrelated adapters are outside the current execution boundary.
- Keep model/database/license credentials outside simulator subprocesses and evidence exports.

## Submit a pull request

Describe the concrete problem and resulting behavior. Include the changed scope, relevant contract implications, exact validation commands and outcomes, and remaining limitations. A test using a fake provider or scripted runner is unit evidence; label it accordingly. Only claim a simulator, model or Vivado result when that tool actually executed and its evidence is retained.

Use the pull request template to make these distinctions easy to review. Update documentation when behavior or support status changes. Keep generated evidence, waveforms, databases, dependency directories and real environment files out of the patch. Do not replace dependency lockfiles or shared interfaces incidentally.

The [CI workflow](.github/workflows/ci.yml) selects Python checks, web checks and real Linux FIFO smoke based on changed paths. Its scope does not establish live Meta, Vivado, arithmetic or full runtime qualification. Relevant checks should pass before merge; explain skipped or unavailable checks instead of presenting them as passes.

License selection is pending. This guide does not add a license, contributor agreement or permission grant.
