"""Independent foundation acceptance; no simulator, network, or model execution."""
import json

import pytest
from pydantic import ValidationError

from veriforge.contracts import BehavioralContract, Experiment, canonical_hash
from veriforge.core import service
from veriforge.core.catalog import seed_catalog
from veriforge.core.database import Base, Contract, make_engine, session_factory
from veriforge.rtl.oracle import expand, expected_trace


def plan(**changes):
    payload = {
        "experiment_id": "acceptance-1", "fixture_id": "fifo_correct",
        "objective_ids": ["FIFO_BEHAVIOR"], "parameters": {"depth": 2},
        "operations": [{"op": "reset"}, {"op": "write", "value": 17},
                       {"op": "write", "value": 42},
                       {"op": "cycle", "read": True, "write": True, "value": 99},
                       {"op": "read"}, {"op": "read"}],
    }
    payload.update(changes)
    return payload


def test_json_boundary_preserves_exact_stimulus_and_nested_immutability():
    payload = plan()
    accepted = Experiment.model_validate_json(json.dumps(payload))
    before = canonical_hash(accepted)
    payload["operations"][1]["value"] = 99
    assert accepted.operations[1].value == 17
    with pytest.raises(ValidationError):
        accepted.operations[1].value = 99
    assert canonical_hash(Experiment.model_validate_json(accepted.model_dump_json())) == before
    assert canonical_hash(Experiment.model_validate_json(json.dumps(payload))) != before


@pytest.mark.parametrize("budget", [{"max_cycles": 5}, {"wall_time_seconds": True},
                                   {"max_artifact_bytes": "1024"}])
def test_wire_resource_limits_reject_underbudget_or_coerced_requests(budget):
    with pytest.raises(ValidationError):
        Experiment.model_validate_json(json.dumps(plan(budget=budget)))


def test_full_fifo_rejects_replacement_and_preserves_second_item():
    rows = expected_trace(expand(Experiment.model_validate(plan())), 2)
    # Hand-derived witness: 99 is rejected because occupancy was full before the edge.
    assert [row["data"] for row in rows] == [0, 0, 0, 17, 42, 42]
    assert rows[3]["accepted_read"] and not rows[3]["accepted_write"]
    assert rows[4]["empty"] and not rows[5]["accepted_read"]


def test_reset_overrides_simultaneous_traffic_and_discards_queued_data():
    payload = plan(operations=[{"op": "reset"}, {"op": "write", "value": 17},
                              {"op": "cycle", "reset": True, "read": True,
                               "write": True, "value": 99}, {"op": "read"}])
    rows = expected_trace(expand(Experiment.model_validate(payload)), 2)
    assert [row["data"] for row in rows] == [0, 0, 0, 0]
    assert rows[2]["empty"] and not rows[2]["accepted_read"] and not rows[2]["accepted_write"]
    assert not rows[3]["accepted_read"]


@pytest.fixture
def session():
    engine = make_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    with session_factory(engine).begin() as active:
        seed_catalog(active)
        yield active
    engine.dispose()


@pytest.mark.parametrize("changed", [None, "source_hash", "checker_hash", "contract_hash", "experiment_hash"])
def test_controller_requires_matching_evidence_identity(session, changed):
    experiment = Experiment.model_validate(plan())
    campaign = service.create_campaign(session, "fifo_correct", experiment.model_dump(mode="json"), "acceptance-key")
    service.start(session, campaign.id)
    session.flush()
    from veriforge.core.database import Attempt
    from sqlalchemy import select
    attempt = session.scalar(select(Attempt).where(Attempt.campaign_id == campaign.id))
    lease, _, _ = service.claim(session, attempt.id)
    # Use the campaign's parameterized immutable snapshot, not default catalog parameters.
    identity = {**campaign.identity, "environment_hash": "b" * 64}
    if changed is not None:
        identity[changed] = "0" * 64
    result = {"attempt_id": attempt.id, "experiment_id": experiment.experiment_id,
              "execution_status": "completed", "verdict": "pass_within_scope", "identity": identity}
    if changed is None:
        assert service.finalize(session, attempt.id, lease, result)
    else:
        with pytest.raises((ValueError, service.Conflict)):
            service.finalize(session, attempt.id, lease, result)


def test_catalog_contract_can_be_consumed_as_shared_wire_contract(session):
    # GET /api/contracts/{id} returns this exact catalog record.
    record = session.get(Contract, "fifo-v1").data
    BehavioralContract.model_validate_json(json.dumps(record))
