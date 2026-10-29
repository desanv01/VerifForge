# Optional local planner configuration

Status: source configuration and isolated tests only. Coordinator review of the
root overlay, actual local adoption, PostgreSQL/queue execution, and live Meta
verification are pending. This guide does not authorize starting services or
requesting credentials. A successful configuration check is not model readiness,
simulator execution, or a design verdict.

The base `compose.yaml` retains its manual API entrypoint, loopback host port and
named data/artifact volumes. `compose.planner.yaml` is an explicit additional
file, not an automatically loaded override. Its new dedicated worker also needs
the `planner` profile. Every role defaults to planner disabled unless
`VERIFORGE_PLANNER_ENABLED` is exactly `1`. Starting the profile alone does not
enable provider construction.

## Server policy and controls

`veriforge.api.runtime:create_configured_app` is the Uvicorn factory. Configuration
reads only these three planner settings, without loading any `.env` file:

| Setting | Meaning |
| --- | --- |
| `VERIFORGE_PLANNER_ENABLED` | Exact `1` opts in; every other value disables. |
| `VERIFORGE_CONTROL_PATH` | Absolute canonical control JSON path. |
| `VERIFORGE_PLANNER_POLICY_PATH` | Absolute trusted server policy JSON path. |

API configuration never reads provider credentials or the provider model
environment setting. Existing database/artifact configuration belongs to the
existing app factory. No model runs in the API, reconciler, or RTL worker.

The policy uses the existing `PlannerPolicy` schema: `enabled`, `strategy`
(`meta`), `requested_model`, `limits`, `pricing`, and
`max_seed_artifact_bytes`. Model identity, limits and pricing come exclusively
from this trusted file, never an HTTP request. This example is a shape example;
its model identifier must be replaced with a separately verified model:

```json
{
  "enabled": true,
  "strategy": "meta",
  "requested_model": "operator-verified-model-id",
  "limits": {
    "max_rounds": 3,
    "max_total_cycles": 10000,
    "max_cycles_per_experiment": 1000,
    "max_wall_seconds": 60,
    "max_execution_seconds": 10,
    "max_model_seconds": 10,
    "max_output_tokens": 1024,
    "max_model_tokens": 10000
  },
  "pricing": null,
  "max_seed_artifact_bytes": 10485760
}
```

Omitted limits retain the bounded defaults in `InvestigationLimits`. Unknown
pricing means unknown cost. To add `limits.max_cost_usd`, supply explicit,
separately verified `pricing.input_usd_per_million` and
`pricing.output_usd_per_million` as decimal strings or JSON numbers. No current
pricing or live model availability is asserted by this example.

Policy files must be absolute regular files, UTF-8 JSON, at most 65,536 bytes.
Duplicate keys at any depth, unknown fields, coercible invalid types, malformed
JSON, non-finite numbers, unsupported bounds, unreadable files and concurrent
file changes disable configuration. Both the environment opt-in and policy
`enabled: true` are required. Failures expose no policy contents or credential
details. A policy change requires a role restart so API and dedicated role load
the same trusted revision; coordinate restarts and do not edit policy mid-run.

Canonical control is uncached. Only a valid `"control": "RUNNING"` permits work;
missing, invalid, duplicated, oversized, paused or unreadable control fails
closed. A valid loaded policy may still be reported as configured in API health
while canonical control pauses admission. Health does not prove live readiness.

## Overlay review and later adoption

The overlay requires two explicit absolute host directories through
`VERIFORGE_CONTROL_DIRECTORY` and `VERIFORGE_PLANNER_POLICY_DIRECTORY`.
The former must be the directory containing the actual canonical
`current-state.json`; the latter contains the trusted `planner-policy.json`.
Do not make a copied control snapshot. Directory mounts are read-only with
`create_host_path: false`; missing host directories fail rather than being
created. Mounting the directory keeps subsequent atomic replacement of the
control file visible. A single-file bind would risk retaining an old inode.

The control directory is mounted in API, reconciler, RTL and dedicated planner
roles. The policy directory is mounted only in API and dedicated planner.
These directory mounts expose their directory contents to those local roles;
keep unrelated material out of a policy directory. No private control/evidence
files are copied into the image or committed by this configuration. There is no
Docker socket mount. Existing named volumes and loopback host binding are
preserved by Compose's merge rules.

The dedicated command is fixed Python without a shell. It loads the same
configuration and calls the accepted runtime only when both opt-ins are valid.
Its deferred provider factory uses the existing `MetaConfig.from_env()` key
mechanism only after the runtime's enable/control/resource gates, then fixes
the provider model to the validated server policy model. An environment model
value cannot select a different requested model. `MODEL_API_KEY` is scoped only
to `planner-worker` in the overlay; API, reconciler and RTL receive no model
credential setting or env-file inheritance. Do not add a shared env-file or
model key to the base app environment. Do not put credentials in policy JSON,
logs, reports, screenshots, or simulator jobs.

After coordinator source review and separate authorization for local adoption,
the intended file order is `compose.yaml` then `compose.planner.yaml`; the
dedicated profile is `planner`. Review a merged Compose configuration without
emitting interpolated secrets before any start. No Compose invocation, image
build, PostgreSQL/RQ operation, provider request or native tool run was performed
to deliver this overlay. The existing runtime CLI remains an explicitly enabled
lower-level entrypoint; the overlay deliberately uses the typed disabled-default
loader rather than hard-coding `--enable-live`.

## Qualification and remaining gates

`tests/api/test_runtime_configuration.py` covers strict bounded files, three-key
configuration access, disabled defaults, static overlay role separation and
fixed-command behavior with fake factories, and API admission with temporary
SQLite plus atomic control replacement. It does not validate Docker's actual
merged runtime, bind behavior on this host, PostgreSQL concurrency, RQ worker
lifecycle, Meta access, or simulator behavior. Existing durable state and
independent resource ownership checks remain authoritative for execution.

References: [Compose merge rules](https://docs.docker.com/compose/how-tos/multiple-compose-files/merge/),
[Compose service configuration](https://docs.docker.com/reference/compose-file/services/),
and the repository's accepted `PlannerPolicy`, `RuntimeConfiguration`,
`ControlReader` and dedicated `planner_runtime.serve` seams.
