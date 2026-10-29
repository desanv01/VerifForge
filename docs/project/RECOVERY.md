# Recovery qualification

Issue [#5](https://github.com/desanv01/VerifForge/issues/5) includes controlled
worker/API/broker interruption, lease rejection, running-process cancellation,
and isolated backup restore. These are separate acceptance gates. As of
30 October 2026, the original October24 backup has passed an actual isolated
database restore and independent evidence review. This acceptance covers only
the historical `c67f9c9f19e5` revision and its seven data tables. Newer backups,
current-schema recovery and the wider interruption/restart gates remain open.

The initial VF024 `pg_restore --list` inspection established archive readability;
the later restore established the separate database-only result below. Existing
local services and valuable volumes must remain untouched by any qualification.

## Accepted historical restore

The separately granted restore completed at private, ignored evidence root
`infra/app/evidence/vf025a-d14ec0ad1fde4e11b2d8235ea17ac58c/`.
Retained receipt identities are:

| Receipt | SHA256 |
|---|---|
| qualification-receipt.json | `a81f84c17a1d2428f718fba523e6c2ad12022c12c632e73dc233298dacbc880e` |
| comparison-receipt.json | `a953d77353e2b5a192df3ed451d6a6cdc3b8b2df5d1960933ea1838f9c3398ee` |
| restored-snapshot.json | `32c70ef45e5ddc1e947fa9d1f37d418953a4f59328bf4ddd6676aac42fa4b0b0` |

Independent review accepted the restored revision, all seven canonical row
fingerprints and counts: campaigns 4, attempts 3, findings 1, run_events 12,
dispatches 3, contracts 1, projects 2. Initial column sets and primary/foreign/unique
constraint counts matched, with no unvalidated constraints. Saved isolation and
ownership evidence confirmed only the newly created resources were used and
removed: qualification passed, container stop confirmed, cleanup complete.
This is acceptance of the retained historical evidence, not a fresh resource
inventory or a new restore invocation. Raw snapshots contain private database
records and must remain unpublished; the hashes above do not authorize uploading
their contents.

## Fixed inputs and comparison

- Archive: ignored `infra/app/evidence/vf024-pre-upgrade-20261024T015821851Z/veriforge.dump`.
- Archive SHA256: `091d8e811bf1862995e7c45b3333e41cb4df94a0cde5c6d493ea2745df941a55`,
  20,993 bytes; accompanying backup receipt is checked too.
- Expected data: `infra/app/evidence/vf024-upgrade/pre-upgrade-state.json`.
  This is the pre-migration snapshot, not the later live database state.
- Destination image: exact already retained PostgreSQL16 image ID
  `sha256:32ebb7185baf2c9d38f91684417d140c0745aeff5082dfbb62edb330c2af6197`.
  Pulling a replacement image or building an image is not part of the helper.
- Expected revision: `c67f9c9f19e5`, seven data tables plus Alembic's table.
  Later `replay_links`/`evidence_exports` tables must be absent in this old backup.

`tests/integration/test_backup_restore.py` compares all seven restored table row
counts and canonical-JSON SHA256 values to the pre-upgrade receipt. Rows are
ordered by their immutable primary key. It also checks initial migration column
sets, primary/foreign/unique constraint counts and validation status. The old
receipt did not capture a complete catalog/schema fingerprint: this schema check
uses the initial migration's expected structure, and does not claim identical
physical storage, column type catalog, ACLs, ownership or every constraint's
definition. `pg_restore --single-transaction --exit-on-error` must succeed before
comparison. No app migrations, API, planner, simulator, broker or workers start.

## Isolation and finite limits

The historical `scripts/verify-backup-restore.ps1` defaults to plan-only output.
Live execution requires main's explicit isolated-operation grant and `-Execute`.
RUNNING control alone is not that grant.

This is a local Windows operator helper with fixed historical inputs, not a
self-contained public-checkout or current-schema recovery tool. Its execution
path reads the ignored `.coordination/current-state.json` control and requires
the private archive, backup receipt and pre-upgrade snapshot listed above, the
already-retained exact image, Docker and `.venv/Scripts/python.exe`. The default
plan returns before those execution checks or Docker operations. A public source
checkout does not supply these private inputs; do not publish controls, database
rows or dumps to make it executable. The synthetic comparator and fake cleanup
tests establish their own source-level behavior without those live inputs.

Each invocation generates fresh random names and a unique ownership label for
one container, one internal Docker network and one data volume. It attaches no
existing project network or volume, has no published ports, disables PostgreSQL
TCP listening and uses only the new container's Unix socket. The existing dump
is a read-only file bind; the mounted file is hashed before restore and the
original file is hashed after. No Docker socket, credentials, source tree,
artifact storage or live DB/broker URL is mounted or forwarded. The role is the
fixed local `veriforge` role in a new `restore_qualification` database; temporary
trust authentication is confined by the no-TCP/internal-network boundary.

Resource settings: one CPU,512MiB memory/no swap,128 PIDs,32MiB /tmp and16MiB
socket tmpfs, read-only root, no restart, shared_buffers32MiB/max_connections8.
The new named data volume is separate storage, without a portable hard disk
quota; only the fixed trusted20,993-byte archive is accepted (additional8MiB
input ceiling), and time limits bound execution. Do not generalize this to an
untrusted uploaded dump. Default operation deadline240s, configurable60–300s;
each command is capped at90s, including the Python comparison. Cleanup/logging
commands have10s each, at most90s beyond the main deadline. The Docker client
process is terminated on timeout, followed by a bounded stop of the owned server.

Initialization readiness waits for PID1 to become the final `postgres` process,
then checks the target database via Unix socket; it does not mistake the
entrypoint's temporary initialization server for the final ready instance.

## Source checks and separately granted execution

Source preparation checks, which do not touch Docker:

```powershell
.venv/Scripts/python.exe -m pytest tests/integration/test_backup_restore.py -q
./scripts/verify-backup-restore.ps1
```

The historical restore used `./scripts/verify-backup-restore.ps1 -Execute` after
review and a separate explicit grant. Any later invocation needs a new grant;
do not run it based solely on this document or repeat it to resolve stale status
wording. Do not run all integration tests; that would start unrelated RTL
acceptance cases. Do not point restore tools at the live project database.

Each actual step's stdout/stderr, isolation inspect, restored snapshot, comparison
receipt and final qualification receipt are retained in a fresh ignored
`infra/app/evidence/vf025a-<run-id>` directory. The restored snapshot contains
private database records and must not be published. Passed comparisons permit
stopping/removing only the exact newly created container, then its uniquely
labeled volume/network. Labels are checked before cleanup. There is no global
prune, wildcard deletion, live-volume removal, `pg_restore --clean`, migration
downgrade or restore-over-live path.

On failure, logs/receipts remain; diagnostic log capture has its own best-effort
try/catch so a log timeout cannot skip the label-checked stop. The receipt states
diagnostic failure, confirmed stop and cleanup-incomplete status independently.
The helper stops only its owned container and
retains its container/volume/network for bounded diagnosis. It does not retry
restoration or delete failed evidence. If the Docker API cannot confirm a
creation/cleanup result, planned names and failure are recorded; main must inspect
labels and grant any later cleanup. A cleanup failure is a failed qualification,
not a clean success.

The accepted historical DB restore does not prove artifact backup/restore,
application startup against the restored DB, worker/lease recovery, broker
recovery, running compiler cancellation, remote authentication, or a restored
end-to-end replay. The known old backup predates the new export and replay rows.

## Remaining recovery gates

- The newer 26,847-byte family pre-migration archive and 35,679-byte AXI-adoption
  archive were inspected and byte-verified, but have not been restore-tested.
  The latter captures `7bf054adapter` and nine data tables. Preservation of 110
  artifact/export files does not establish their restoration. Do not substitute
  either archive for this helper's fixed original dump.
- Current migration source reaches `13engineeringprojects`. Hosted empty-database
  migration/schema qualification is separate from restoring existing data at
  that revision. The c67 comparator intentionally rejects later revisions,
  tables and changed columns; it cannot qualify schema13 recovery unchanged.
  A newer restore needs a separately reviewed baseline and comparisons covering
  replay/export, experiment/planner state, investigations, resource fences,
  project-kind invariants and the associated artifact bytes.
- `scripts/runtime-state-receipt.py` is a trusted local diagnostic that prints
  the observed revision but fingerprints only the original seven tables. It is
  not a complete schema13 preservation or recovery baseline. The backup helper's
  archive creation/listing and limited row counts are also separate from restore
  acceptance.
- Coherent artifact restoration and application startup against a restored
  database, controlled API/worker/broker interruption, durable lease/outbox
  recovery, running-process cancellation and restored end-to-end replay remain
  separate acceptance gates. No general recovery or later-schema acceptance
  follows from the original seven-table result.

Official references: [PostgreSQL16 pg_restore](https://www.postgresql.org/docs/16/app-pgrestore.html)
and [Docker resource limits](https://docs.docker.com/engine/containers/resource_constraints/).
