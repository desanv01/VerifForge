"""Only repository-curated fixtures may enter the execution queue."""
from __future__ import annotations

from veriforge.adapters import registered_adapters
from veriforge.contracts import canonical_hash

from .database import Contract, Project


def seed_catalog(session):
    for descriptor in registered_adapters():
        reviewed = descriptor.reviewed_contract()
        contract_id = reviewed.contract_id
        identity = {"contract_hash": canonical_hash(reviewed), "checker_hash": reviewed.checker.sha256}
        contract = reviewed.model_dump(mode="json")
        if session.get(Contract, contract_id) is None:
            session.add(Contract(id=contract_id, data=contract, identity=identity))
            session.flush()
        elif session.get(Contract, contract_id).data != contract:
            raise RuntimeError("Reviewed contract changed under an existing version; create a new immutable version before executing")
        schema_version = descriptor.experiment_schema()["properties"]["schema_version"]["const"]
        for fixture in descriptor.fixture_ids:
            metadata = descriptor.fixture_source(fixture)
            added = {"adapter_id": descriptor.adapter_id, "experiment_schema_version": schema_version,
                     "trace_schema": descriptor.trace_schema, "trace_artifact": descriptor.trace_artifact}
            existing = session.get(Project, fixture)
            if existing is None:
                session.add(Project(id=fixture, contract_id=contract_id, data={
                    "id": fixture, "name": fixture.replace("_", " ").title(),
                    "family": descriptor.target, "support_level": "experimental", "contract_id": contract_id,
                    "source_file_hash": metadata["source_file_hash"], "variant": metadata["variant"], **added,
                }))
            else:
                # Enrich legacy catalog discovery without replacing immutable source data.
                existing.data = {**existing.data, **added}
