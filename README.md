# VerifForge

An investigation workbench for digital hardware and engineering-tool failures: typed experiments, independent checking, reproducible evidence, and validated repair candidates.

Implementation is underway. This repository does not yet claim the complete release acceptance criteria. Follow [the master checklist](docs/project/MASTER_CHECKLIST.md) for current status and [the implementation gameplan](docs/project/IMPLEMENTATION_GAMEPLAN.md) for intended scope.

## Initial implementation

- Python 3.12, FastAPI, Pydantic, SQLAlchemy/Alembic, PostgreSQL and RQ/Valkey.
- React, Vite and TypeScript workbench in `apps/web`.
- Linux cocotb/Verilator worker with curated FIFO fixtures and an independent oracle.
- Typed Meta provider/controller with explicit unavailable states when credentials are missing.
- Replay, reduction and evidence export; Vivado diagnostics with live capability tracked separately.

The model proposes operations. The checker determines the verdict. A completed run can find a design mismatch, and a passing test establishes only the checked scope.

## Development

Install Python dependencies with `uv sync --locked`. On restricted Windows environments, use a writable project-local `UV_CACHE_DIR`. Shared schemas and callable boundaries are documented in [INTERFACES.md](docs/project/INTERFACES.md). API endpoints are documented in [API.md](docs/project/API.md).

Run targeted tests with `.venv/Scripts/python.exe -m pytest tests/contracts -q` on Windows, or `uv run pytest tests/contracts -q` on Linux. HTTP/async tests need local socket access. Simulator validation requires the Linux worker; Python mock tests are not hardware execution evidence.

Full runtime instructions will be added after integration is verified. Local services bind to localhost. Public deployment, arbitrary uploaded executable testbenches, and vendor-binary repairs are outside this initial implementation.

Never commit credentials, generated evidence, local databases, or private coordination state.
