"""Focused independent VF054D negatives; synthetic SQLite/file evidence only."""
import hashlib
import json

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import select

from veriforge.adapters import get_adapter
from veriforge.arithmetic import ArithmeticExperiment
from veriforge.contracts import ArtifactReference, AttemptResult, TraceSample
from veriforge.core import artifacts, service
from veriforge.core.catalog import seed_catalog
from veriforge.core.database import Attempt, Campaign, ReplayLink, make_engine, session_factory


@pytest.fixture
def store(tmp_path):
    engine = make_engine("sqlite:///" + (tmp_path / "review.db").as_posix())
    config = Config("alembic.ini")
    with engine.begin() as connection:
        config.attributes["connection"] = connection
        command.upgrade(config, "head")
    sessions = session_factory(engine)
    with sessions.begin() as session:
        seed_catalog(session)
    root = tmp_path / "evidence"

    def create(key="original"):
        experiment = ArithmeticExperiment(experiment_id="review-" + key, operations=(
            {"op": "reset"}, {"op": "multiply", "a": 255, "b": 255}, {"op": "idle", "cycles": 2}))
        with sessions.begin() as session:
            row = service.create_campaign(session, experiment.fixture_id, experiment.model_dump(mode="json"),
                                          key, adapter_id="rtl.arithmetic.v1")
            service.start(session, row.id)
            attempt = session.scalar(select(Attempt).where(Attempt.campaign_id == row.id))
            lease, _, _ = service.claim(session, attempt.id)
            directory = root / attempt.id
            directory.mkdir(parents=True)
            expected = [dict(cycle=0, operation_index=0, reset=True, valid=False, product=0),
                        dict(cycle=1, operation_index=1, reset=False, valid=False, product=None),
                        dict(cycle=2, operation_index=2, reset=False, valid=False, product=None),
                        dict(cycle=3, operation_index=2, reset=False, valid=True, product=65025)]
            observed = [dict(cycle=i, valid=i == 3, product=65025 if i == 3 else 0) for i in range(4)]
            contents = {"experiment.json": experiment.model_dump_json().encode(),
                        "stimulus.json": json.dumps(get_adapter("rtl.arithmetic.v1").expand_stimulus(experiment)).encode(),
                        "arithmetic-trace.json": json.dumps(dict(schema_version="arithmetic.trace.v1", expected=expected, observed=observed)).encode()}
            refs = []
            for index, (name, data) in enumerate(contents.items()):
                (directory / name).write_bytes(data)
                refs.append(ArtifactReference(artifact_id=f"review-{index}", path=name, size_bytes=len(data),
                                              sha256=hashlib.sha256(data).hexdigest(), media_type="application/json"))
            result = AttemptResult(attempt_id="adapter-record-is-not-storage-authority", experiment_id=experiment.experiment_id,
                execution_status="completed", verdict="pass_within_scope", identity={**row.identity, "environment_hash": "a" * 64},
                artifacts=tuple(refs), usage={"cycles": 4}, uncertainties=("Synthetic review record; no HDL executed",))
            (directory / "result.json").write_text(result.model_dump_json())
            assert service.finalize(session, attempt.id, lease, result.model_dump(mode="json"))
            return dict(attempt_id=attempt.id, campaign_id=row.id, directory=directory, experiment=experiment, result=result)

    yield dict(root=root, sessions=sessions, create=create)
    engine.dispose()


def replace_result(store, source, result):
    (source["directory"] / "result.json").write_text(result.model_dump_json())
    with store["sessions"].begin() as session:
        session.get(Attempt, source["attempt_id"]).result = result.model_dump(mode="json")


def replace_file(store, source, filename, payload):
    data = json.dumps(payload).encode()
    (source["directory"] / filename).write_bytes(data)
    refs = tuple(ref.model_copy(update={"size_bytes": len(data), "sha256": hashlib.sha256(data).hexdigest()})
                 if ref.path == filename else ref for ref in source["result"].artifacts)
    result = source["result"].model_copy(update={"artifacts": refs})
    replace_result(store, source, result)
    return result


@pytest.mark.parametrize("field", ["source_hash", "checker_hash", "contract_hash", "experiment_hash"])
def test_disk_and_db_agreement_cannot_override_frozen_campaign_identity(store, field):
    source = store["create"]()
    result = source["result"].model_copy(update={"identity": source["result"].identity.model_copy(update={field: "0" * 64})})
    replace_result(store, source, result)
    with store["sessions"]() as session:
        with pytest.raises(service.Conflict, match="Evidence is unavailable"):
            artifacts.attempt_snapshot(session, source["attempt_id"], root=store["root"], checked_required=True)


@pytest.mark.parametrize("mutation", ["reset_int", "valid_int", "operand_float", "index_float"])
def test_canonical_stimulus_equality_keeps_bool_int_float_distinct_for_source_and_fresh_replay(store, mutation):
    source = store["create"]()
    stimulus = json.loads((source["directory"] / "stimulus.json").read_bytes())
    expected = get_adapter("rtl.arithmetic.v1").expand_stimulus(source["experiment"])
    if mutation == "reset_int": stimulus[0]["reset"] = 1
    if mutation == "valid_int": stimulus[1]["valid"] = 1
    if mutation == "operand_float": stimulus[1]["a"] = 255.0
    if mutation == "index_float": stimulus[0]["operation_index"] = 0.0
    assert stimulus == expected  # Python equality alone would accept each mutation.
    result = replace_file(store, source, "stimulus.json", stimulus)
    with pytest.raises(service.Conflict):
        with store["sessions"].begin() as session:
            artifacts.replay_campaign(session, source["attempt_id"], "must-not-queue", root=store["root"])
    with pytest.raises(ValueError, match="Stored stimulus differs"):
        artifacts.verify_replay_output(source["directory"], source["experiment"], result)
    with store["sessions"]() as session:
        assert list(session.scalars(select(ReplayLink))) == []


@pytest.mark.parametrize("mutation", ["missing", "fifo_trace", "wrong_schema", "non_boolean"])
def test_family_sidecar_cannot_be_missing_or_coerced_to_fifo(store, mutation):
    source = store["create"]()
    if mutation == "missing":
        result = source["result"].model_copy(update={"artifacts": tuple(ref for ref in source["result"].artifacts if ref.path != "arithmetic-trace.json")})
        replace_result(store, source, result)
    elif mutation == "fifo_trace":
        replace_result(store, source, source["result"].model_copy(update={"trace": (TraceSample(cycle=0, operation_index=0),)}))
    else:
        sidecar = json.loads((source["directory"] / "arithmetic-trace.json").read_bytes())
        if mutation == "wrong_schema": sidecar["schema_version"] = "axi_lite.trace.v1"
        else: sidecar["observed"][0]["valid"] = 0
        replace_file(store, source, "arithmetic-trace.json", sidecar)
    with store["sessions"]() as session:
        with pytest.raises(service.Conflict):
            artifacts.artifact_list(session, source["attempt_id"], root=store["root"])


def test_replay_lineage_is_exact_idempotent_source_specific_and_does_not_mutate_parent(store):
    source = store["create"]()
    other = store["create"]("other")
    with store["sessions"]() as session:
        before = service.campaign_view(session, service.owned(session, source["campaign_id"]))
    with store["sessions"].begin() as session:
        replay = artifacts.replay_campaign(session, source["attempt_id"], "lineage", root=store["root"])
        replay_id = replay.id
        assert replay_id not in (source["campaign_id"], other["campaign_id"])
        assert replay.adapter_id == "rtl.arithmetic.v1" and replay.execution_status == "queued"
        assert replay.plan == before["plan"] and replay.identity == before["identity"]
    with store["sessions"].begin() as session:
        assert artifacts.replay_campaign(session, source["attempt_id"], "lineage", root=store["root"]).id == replay_id
    with pytest.raises(service.Conflict, match="another attempt"):
        with store["sessions"].begin() as session:
            artifacts.replay_campaign(session, other["attempt_id"], "lineage", root=store["root"])
    with store["sessions"]() as session:
        assert service.campaign_view(session, service.owned(session, source["campaign_id"])) == before
        assert session.get(ReplayLink, replay_id).source_attempt_id == source["attempt_id"]
        assert len(list(session.scalars(select(ReplayLink)))) == 1


def test_unaccepted_adapter_and_replay_source_owner_fail_closed(store):
    source = store["create"]()
    with store["sessions"].begin() as session:
        session.get(Campaign, source["campaign_id"]).adapter_id = "rtl.unaccepted.v1"
    with store["sessions"]() as session:
        with pytest.raises(service.Conflict, match="unsupported"):
            artifacts.owned_attempt(session, source["attempt_id"])
    with store["sessions"].begin() as session:
        row = session.get(Campaign, source["campaign_id"])
        row.adapter_id, row.owner = "rtl.arithmetic.v1", "foreign-owner"
    with store["sessions"]() as session:
        with pytest.raises(service.NotFound):
            artifacts.replay_campaign(session, source["attempt_id"], "foreign", root=store["root"])
