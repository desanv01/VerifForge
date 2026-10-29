# Curated adapter registry — VF-052 / VF-054C

`veriforge.adapters` provides a static registry for typed validation and runner
resolution and trusted metadata for family adoption. This registry delivery does
not change API/core, evidence, shared schemas, checkers, or HDL. Their owners adopt
the interface independently. No simulator runs are part of this registry delivery.

| Adapter ID | Experiment schema/model | Contract model | Installed fixtures |
|---|---|---|---|
| `rtl.fifo.v1` | `1` / shared `Experiment` | shared `BehavioralContract` | `fifo_correct`, `fifo_reset_bug` |
| `rtl.arithmetic.v1` | `arithmetic.v1` / `ArithmeticExperiment` | `ArithmeticContract` | `multiplier_correct`, `multiplier_latency_bug`, `multiplier_data_bug` |

Identifiers are exact and versioned. Unknown identifiers, module names, paths, and
aliases fail closed with `UnknownAdapterError`. Descriptors are frozen, their
fixture collections are tuples, and the lookup mapping is read-only. Imports and
backend functions are fixed in source; experiment input cannot select an import,
source file, command, or Python plugin.

## Interface

```python
from pathlib import Path
from threading import Event
from veriforge.adapters import get_adapter, registered_adapters

descriptor = get_adapter("rtl.fifo.v1")
schema = descriptor.experiment_schema()
contract_schema = descriptor.contract_schema()
experiment = descriptor.validate_experiment({
    "experiment_id": "example",
    "fixture_id": "fifo_correct",
    "objective_ids": ["FIFO_BEHAVIOR"],
    "operations": [{"op": "reset"}, {"op": "write", "value": 42}, {"op": "read"}],
})
contract = descriptor.contract_for(experiment)
# Explicit execution selection; this example is not an instruction to run now.
runner = descriptor.resolve_runner("native")  # or "container"
token = Event()
# result = runner(experiment, Path("trusted-unique-attempt"), cancel_event=token)
```

`validate_experiment` accepts a family-specific model or a mapping. Existing model
instances are serialized to Python values and revalidated, so `model_copy` or
`model_construct` cannot bypass strict width/boolean/cycle checks. A model from
another family is rejected rather than converted. Family validators retain their
reset, operand, draining, budget, and unknown-field rules.

Installed fixtures are narrower than the shared FIFO schema: `fifo_full_bug` and
`fifo_order_bug` are schema-known but unavailable, and raise
`UnsupportedFixtureError` at this boundary. A fresh `experiment_schema()` narrows
the fixture enum accordingly, without modifying the original shared model or its
schema. Parameter-dependent checks such as expanded cycle budgets remain semantic
model validation; JSON Schema alone does not perform them.

`reviewed_contract(parameters=None)` validates the descriptor's parameter model
and calls its target package's existing factory. `contract_for(experiment)` binds
the validated experiment parameters automatically. Consumers may use
`canonical_hash(contract)` without a registry-specific contract encoding. FIFO and
arithmetic contracts remain separate model/schema versions.

`resolve_runner(mode)` accepts only `native` or `container`; it does not infer host
capabilities, probe tools, pull images, or run a job. Its callable validates input
before invoking the fixed backend and forwards the same optional cancellation
token and controller-selected output directory. Tokens must provide `is_set()`;
`threading.Event` matches the existing runners. The wrapper does not set/clear the
token or manufacture cancellation outcomes. Existing backend process cleanup and
execution/verdict separation remain authoritative.

## Trusted identity and evidence helpers

The API and evidence owners agreed on one shared descriptor interface for family
adoption. These are installed-code helpers, never planner-supplied identity claims:

| Helper or property | Result |
|---|---|
| `fixture_source(fixture_id)` | Fresh dictionary with exactly `source_file_hash` and integer `variant`; installed fixture only |
| `expected_identity(experiment)` | Fresh dictionary with exactly `source_hash`, `checker_hash`, `contract_hash`, `experiment_hash` |
| `expand_stimulus(experiment)` | Fresh deterministic list of typed cycle dictionaries from the family's existing oracle |
| `source_artifact` | `fifo.sv` or `multiplier.sv` |
| `portable_artifacts` | Fixed tuple: stimulus, contract, checker, observations and own source; arithmetic also includes `arithmetic-trace.json` |

Fixed source paths and source-variant declarations live in an internal frozen
descriptor record. The public source snapshot contains no paths, commands or
imports. Caller mutation of a returned dictionary cannot change descriptor state.
`expected_identity` validates the family/fixture/parameters, reads current installed
source bytes, uses the reviewed checker/contract factory, and matches both existing
runners' source identity formula:

```text
source_hash = SHA256(compact_sorted_JSON({
  "source": source_file_hash, "variant": variant, "parameters": validated_parameters
}))
```

Contract and experiment hashes use shared `canonical_hash`; the checker hash is
the reviewed contract's actual checker SHA. There is deliberately no expected
environment/tool hash: only an actual execution can supply observed environment
identity. This method is an explicit current-installation preflight for execution
or replay, and does not run a tool or create an attempt directory.

Historical stored-input parsing and export must remain separate from current
installation preflight. `validate_experiment`, schema/portable metadata access,
and stored-model validation do not read active source files. An old archive can
remain readable when current source differs or is absent; a new replay still has
to reject a current identity mismatch. Evidence owners retain artifact hash/size,
path-containment, screening, and arithmetic sidecar-envelope checks. An allowlist
is a selection boundary, not evidence integrity or download authorization.

No new selector alias or implicit schema map was introduced. Callers resolve a
known descriptor and then use strict family validation. Existing descriptor fields
and runner call signatures remain compatible; new metadata fields have defaults
for code-created legacy descriptors, whose new unavailable helpers fail explicitly.

## Evidence and capability limits

FIFO transactions remain in `AttemptResult.trace`; there is no new FIFO trace wire
schema or artifact, so its descriptor has `trace_schema=None` and
`trace_artifact=None`. Arithmetic uses `arithmetic-trace.json` with schema
`arithmetic.trace.v1`; its common FIFO-shaped result trace remains empty. Measured
coverage and finding semantics belong to the target checker, not this registry.

These descriptors identify installed curated packages. Registration is not proof
of a working host toolchain or live arithmetic acceptance. Native execution needs
the existing supported Linux Verilator/cocotb environment; host container execution
needs the prebuilt project image and Docker daemon. FIFO live evidence is tracked
by VF-011/VF-021. See ARITHMETIC.md for the separately recorded VF-050B curated
arithmetic acceptance. It does not establish API/planner/evidence integration.
Those integrations remain separate owner assignments with their own acceptance;
the registry adds no new live capability claim.

No AXI-Lite/general-software adapter, automatic package discovery, external plugin
loader, arbitrary project import, or capability negotiation is implemented here.

## Validation

Registry tests use only validated schemas/contracts and explicitly labeled backend
stubs. They cover both families, exact lookup rejection, unsupported fixtures,
schema isolation, strict values/budgets, parameter-bound contracts, cross-family
rejection, explicit backend selection, cancellation forwarding, and rejection
before filesystem/backend effects. They do not claim simulator behavior.

```powershell
.venv/Scripts/python.exe -m pytest tests/adapters -q --basetemp=tests/adapters/.tmp-vf052-final
```

See [ARITHMETIC.md](ARITHMETIC.md), [INTERFACES.md](INTERFACES.md), and
[RTL_TOOLCHAIN.md](RTL_TOOLCHAIN.md) for the existing target interfaces and evidence
boundaries. No new dependency or shared schema version was introduced.
