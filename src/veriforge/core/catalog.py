"""Only repository-curated fixtures may enter the execution queue."""
from __future__ import annotations

import hashlib
from veriforge.contracts import canonical_hash

from .database import Contract, Project


def seed_catalog(session):
    # Contract identity is supplied by the protected RTL checker, never by a client.
    from veriforge.rtl import oracle, reviewed_contract

    contract_id = oracle.CONTRACT["id"]
    reviewed = reviewed_contract()
    identity = {"contract_hash": canonical_hash(reviewed), "checker_hash": reviewed.checker.sha256}
    contract = reviewed.model_dump(mode="json")
    if session.get(Contract, contract_id) is None:
        session.add(Contract(id=contract_id, data=contract, identity=identity))
        session.flush()
    elif session.get(Contract, contract_id).data != contract:
        raise RuntimeError("Reviewed contract changed under an existing version; create a new immutable version before executing")
    from veriforge.rtl.runner import FIXTURES, ROOT
    source_file_hash = hashlib.sha256((ROOT / "targets/fifo/fifo.sv").read_bytes()).hexdigest()
    for fixture in ("fifo_correct", "fifo_reset_bug"):
        if session.get(Project, fixture) is None:
            session.add(Project(id=fixture, contract_id=contract_id, data={
                "id": fixture, "name": fixture.replace("_", " ").title(),
                "family": "fifo", "support_level": "experimental", "contract_id": contract_id,
                "source_file_hash": source_file_hash, "variant": FIXTURES[fixture],
            }))
