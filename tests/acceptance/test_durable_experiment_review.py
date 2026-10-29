"""Adversarial durable-binding review. Isolated SQLite and fabricated evidence only."""
from copy import deepcopy
import hashlib
import io
import json
import zipfile
from alembic import command
from alembic.config import Config
import pytest
from sqlalchemy import MetaData, Table, select, text
from sqlalchemy.exc import IntegrityError
from veriforge.adapters import get_adapter, registered_adapters
from veriforge.contracts import ArtifactReference, AttemptResult, Experiment, canonical_hash
from veriforge.core import artifacts, service
from veriforge.core.database import (Attempt, AttemptExperiment, Campaign, Dispatch, EvidenceExport,
    ExperimentRecord, Finding, make_engine, now, session_factory)
from veriforge.core.planner_experiments import admit_experiment, record_experiment, resolve_attempt_experiment, settle_sequence


def migrate(engine, revision="8u2experiments"):
    with engine.begin() as connection:
        config = Config("alembic.ini")
        config.attributes["connection"] = connection
        command.upgrade(config, revision)


def seed_historical_catalog(connection):
    # Keep revision7/8 schemas literal; current Project ORM requires kind.
    metadata = MetaData()
    contracts = Table("contracts", metadata, autoload_with=connection)
    projects = Table("projects", metadata, autoload_with=connection)
    assert "kind" not in projects.c
    seen = set()
    for descriptor in registered_adapters():
        reviewed = descriptor.reviewed_contract()
        if reviewed.contract_id not in seen:
            connection.execute(contracts.insert().values(id=reviewed.contract_id, data=reviewed.model_dump(mode="json"),
                identity={"contract_hash": canonical_hash(reviewed), "checker_hash": reviewed.checker.sha256}))
            seen.add(reviewed.contract_id)
        schema_version = descriptor.experiment_schema()["properties"]["schema_version"]["const"]
        for fixture in descriptor.fixture_ids:
            source = descriptor.fixture_source(fixture)
            connection.execute(projects.insert().values(id=fixture, owner="local", contract_id=reviewed.contract_id,
                data={"id": fixture, "name": fixture.replace("_", " ").title(), "family": descriptor.target,
                    "support_level": "experimental", "contract_id": reviewed.contract_id,
                    "source_file_hash": source["source_file_hash"], "variant": source["variant"],
                    "adapter_id": descriptor.adapter_id, "experiment_schema_version": schema_version,
                    "trace_schema": descriptor.trace_schema, "trace_artifact": descriptor.trace_artifact}))


def seed_historical_campaign(session, project_id, wire, key, *, completion_policy="sequential"):
    # Historical setup only, equivalent raw plan/identity/event/snapshot fields.
    descriptor = get_adapter("rtl.fifo.v1")
    experiment = descriptor.validate_experiment(wire)
    assert experiment.fixture_id == project_id
    request_hash = service.digest({"project_id": project_id, "plan": wire})
    if completion_policy != "single":
        request_hash = service.digest({"request_hash": request_hash, "completion_policy": completion_policy})
    parent = Campaign(project_id=project_id, contract_id=experiment.contract_id, adapter_id=descriptor.adapter_id,
        completion_policy=completion_policy, create_key=key, request_hash=request_hash,
        plan=wire, identity=descriptor.expected_identity(experiment))
    session.add(parent)
    session.flush()
    record_experiment(session, parent, wire, "initial", origin="manual", initial=True)
    service.emit(session, parent, "campaign.created")
    return parent


@pytest.fixture
def state(tmp_path, request):
    engine = make_engine("sqlite:///" + (tmp_path / "review.sqlite").as_posix())
    migrate(engine)
    sessions = session_factory(engine)
    with engine.begin() as connection: seed_historical_catalog(connection)
    if getattr(request, "param", None) is not None:
        # Only the current replay API integration requires Project.kind. The
        # historical backfill/immutability/FK cases stay on literal revision8.
        assert request.param == "13engineeringprojects"
        migrate(engine, request.param)
    yield engine, sessions
    engine.dispose()


def plan(name="seed", value=17):
    # Deliberately omit defaults: exact raw input must not become normalized JSON.
    return {"experiment_id": name, "fixture_id": "fifo_correct", "contract_id": "fifo-v1",
            "objective_ids": ["FIFO_BEHAVIOR"], "operations": [{"op": "reset"}, {"op": "write", "value": value}, {"op": "read"}]}


def create(sessions, key="root", policy="sequential"):
    with sessions.begin() as session:
        campaign = seed_historical_campaign(session, "fifo_correct", plan(), key, completion_policy=policy)
        service.start(session, campaign.id)
        attempt = session.scalar(select(Attempt).where(Attempt.campaign_id == campaign.id))
        return campaign.id, attempt.id


def result_for(record, *, mismatch=False):
    values = {"attempt_id": "fabricated-result", "experiment_id": record.wire_id, "execution_status": "completed",
              "verdict": "design_mismatch" if mismatch else "pass_within_scope",
              "identity": {**record.identity, "environment_hash": "a" * 64},
              "uncertainties": ["Independent SQLite review fixture; no simulator execution"]}
    if mismatch:
        values["failure_signature"] = {"requirement_id": "FIFO_BEHAVIOR", "failure_class": "fifo.data", "first_cycle": 2,
            "expected": "17", "observed": "0", "signature_hash": "b" * 64}
    return AttemptResult(**values)


def finish(sessions, attempt_id, *, mismatch=False):
    with sessions.begin() as session:
        lease, _, _ = service.claim(session, attempt_id)
        record = resolve_attempt_experiment(session, session.get(Attempt, attempt_id))
        result = result_for(record, mismatch=mismatch)
        assert service.finalize(session, attempt_id, lease, result.model_dump(mode="json"))
        return result


@pytest.mark.parametrize("table,action,mode", [
    (table, action, mode) for table in ("experiments", "attempt_experiments")
    for action in ("update", "delete") for mode in ("orm", "sql")
])
def test_every_snapshot_and_binding_mutation_path_is_rejected(state, table, action, mode):
    _, attempt_id = create(state[1])
    with state[1]() as session:
        record = resolve_attempt_experiment(session, session.get(Attempt, attempt_id))
        key = record.id if table == "experiments" else attempt_id
    model = ExperimentRecord if table == "experiments" else AttemptExperiment
    if mode == "orm":
        with pytest.raises(ValueError, match="immutable"):
            with state[1].begin() as session:
                row = session.get(model, key)
                if action == "delete": session.delete(row)
                elif table == "experiments": row.origin = "replay"
                else: row.experiment_id = "rewritten"
    else:
        column = "id" if table == "experiments" else "attempt_id"
        statement = f"DELETE FROM {table} WHERE {column}=:key" if action == "delete" else f"UPDATE {table} SET {column}={column} WHERE {column}=:key"
        with pytest.raises(IntegrityError):
            with state[0].begin() as connection: connection.execute(text(statement), {"key": key})
    with state[1]() as session:
        assert resolve_attempt_experiment(session, session.get(Attempt, attempt_id)).id == record.id


@pytest.mark.parametrize("wrong_parent_field", ["attempt", "experiment"])
def test_cross_parent_associations_fail_composite_foreign_keys(state, wrong_parent_field):
    first, _ = create(state[1])
    second, second_attempt = create(state[1], "second-root")
    with state[1]() as session: other_record = resolve_attempt_experiment(session, session.get(Attempt, second_attempt)).id
    with pytest.raises(IntegrityError):
        with state[1].begin() as session:
            attempt = Attempt(campaign_id=first)
            session.add(attempt)
            session.flush()
            session.add(AttemptExperiment(attempt_id=attempt.id, campaign_id=second if wrong_parent_field == "attempt" else first, experiment_id=other_record))


def test_missing_binding_cannot_claim_or_finalize_even_infrastructure_result(state):
    campaign_id, _ = create(state[1])
    with state[1].begin() as session:
        orphan = Attempt(campaign_id=campaign_id)
        session.add(orphan)
        session.flush()
        orphan_id, lease = orphan.id, orphan.lease
    with pytest.raises(ValueError, match="binding"):
        with state[1].begin() as session: service.claim(session, orphan_id)
    with state[1].begin() as session:
        session.get(Campaign, campaign_id).execution_status = "running"
        session.get(Attempt, orphan_id).execution_status = "running"
    with pytest.raises(ValueError, match="binding"):
        with state[1].begin() as session: service.finalize(session, orphan_id, lease, {"error_type": "fixture"}, infrastructure_error=True)


@pytest.mark.parametrize("state", ["13engineeringprojects"], indirect=True)
def test_second_child_selected_evidence_survives_parent_seed_changes_and_exports(state, tmp_path):
    campaign_id, seed_attempt = create(state[1])
    finish(state[1], seed_attempt, mismatch=True)
    with state[1].begin() as session:
        leaf = admit_experiment(session, campaign_id, plan("selected-leaf", 99), "round-two")
        leaf_id = leaf.id
        leaf_attempt = session.scalar(select(AttemptExperiment.attempt_id).where(AttemptExperiment.experiment_id == leaf.id))
        lease, _, _ = service.claim(session, leaf_attempt)
        checked = result_for(leaf)
        experiment = Experiment.model_validate(leaf.plan)
    root = tmp_path / "evidence"
    directory = root / leaf_attempt
    directory.mkdir(parents=True)
    payloads = {"experiment.json": experiment.model_dump_json().encode(),
                "stimulus.json": json.dumps(get_adapter("rtl.fifo.v1").expand_stimulus(experiment)).encode()}
    refs = []
    for index, (name, payload) in enumerate(payloads.items()):
        (directory / name).write_bytes(payload)
        refs.append(ArtifactReference(artifact_id=f"synthetic-{index}", path=name, sha256=hashlib.sha256(payload).hexdigest(), size_bytes=len(payload), media_type="application/json"))
    checked = checked.model_copy(update={"artifacts": tuple(refs)})
    (directory / "result.json").write_bytes(checked.model_dump_json().encode())
    with state[1].begin() as session:
        assert service.finalize(session, leaf_attempt, lease, checked.model_dump(mode="json"))
        parent = session.get(Campaign, campaign_id)
        parent.plan = plan("mutable-root-copy", 3)
        parent.identity = {**parent.identity, "experiment_hash": "0" * 64}
        finding = session.scalar(select(Finding).where(Finding.attempt_id == seed_attempt))
        original_finding = deepcopy(finding.data)
        export = artifacts.request_export(session, leaf_attempt, "selected-leaf-export")
        export_id = export.id
    assert artifacts.execute_export(export_id, sessions=state[1], root=root) == {"accepted": True}
    with state[1].begin() as session:
        exported = artifacts.export_download(session, export_id, root=root)
        replay = artifacts.replay_campaign(session, leaf_attempt, "selected-leaf-replay", root=root)
        assert replay.plan == plan("selected-leaf", 99) and replay.identity == checked.identity.model_dump(exclude={"environment_hash"})
        replay_attempt = session.scalar(select(Attempt).where(Attempt.campaign_id == replay.id))
        assert resolve_attempt_experiment(session, replay_attempt).origin == "replay"
        parent = session.get(Campaign, campaign_id)
        assert parent.execution_status == "running" and parent.verdict is None
        assert session.scalar(select(Finding).where(Finding.attempt_id == seed_attempt)).data == original_finding
        settled = settle_sequence(session, campaign_id, reason="finding_observed")
        assert settled.verdict is None and settled.execution_status == "completed"
        assert resolve_attempt_experiment(session, session.get(Attempt, leaf_attempt)).id == leaf_id
    with zipfile.ZipFile(io.BytesIO(exported)) as archive:
        exported_plan = json.loads(archive.read("experiment.json"))
        assert exported_plan["experiment_id"] == "selected-leaf" and exported_plan["operations"][1]["value"] == 99


def test_raw_legacy_json_identity_dispatch_and_finding_survive_additive_backfill(tmp_path):
    engine = make_engine("sqlite:///" + (tmp_path / "legacy.sqlite").as_posix())
    migrate(engine, "7bf054adapter")
    sessions = session_factory(engine)
    with engine.begin() as connection: seed_historical_catalog(connection)
    raw_plan = plan("legacy-omitted-defaults", 5)
    raw_json = json.dumps(raw_plan, indent=3)
    identity = get_adapter("rtl.fifo.v1").expected_identity(Experiment.model_validate(raw_plan))
    stamp = now()
    with engine.begin() as connection:
        tables = {name: Table(name, MetaData(), autoload_with=connection) for name in ("campaigns", "attempts", "findings", "dispatches", "run_events")}
        connection.execute(tables["campaigns"].insert().values(id="legacy-root", owner="local", adapter_id="rtl.fifo.v1", project_id="fifo_correct", contract_id="fifo-v1", create_key="legacy-key", request_hash="f" * 64, plan=raw_plan, identity=identity, execution_status="completed", verdict="design_mismatch", version=7, event_sequence=1, created_at=stamp, updated_at=stamp))
        connection.execute(text("UPDATE campaigns SET plan=:raw WHERE id='legacy-root'"), {"raw": raw_json})
        connection.execute(tables["attempts"].insert().values(id="legacy-attempt", campaign_id="legacy-root", lease="legacy-lease", execution_status="completed", verdict="design_mismatch", result={"legacy_marker": True}, created_at=stamp, finished_at=stamp))
        connection.execute(tables["findings"].insert().values(id="legacy-finding", campaign_id="legacy-root", attempt_id="legacy-attempt", data={"stored_hash": "d" * 64, "untouched": True}))
        connection.execute(tables["dispatches"].insert().values(attempt_id="legacy-attempt", sent_at=stamp, error="retained-history"))
        connection.execute(tables["run_events"].insert().values(id="legacy-event", campaign_id="legacy-root", sequence=1, kind="attempt.completed", payload={"retained": True}, created_at=stamp))
    names = ("campaigns", "attempts", "findings", "dispatches", "run_events")
    with engine.connect() as connection:
        before = {name: list(connection.execute(text(f"SELECT * FROM {name} ORDER BY 1")).mappings()) for name in names}
    migrate(engine)
    with engine.connect() as connection:
        for name in names:
            after = list(connection.execute(text(f"SELECT * FROM {name} ORDER BY 1")).mappings())
            assert [dict(row) for row in before[name]] == [{key: row[key] for key in before[name][index]} for index, row in enumerate(after)]
        assert connection.scalar(text("SELECT plan FROM campaigns WHERE id='legacy-root'")) == raw_json
    with sessions() as session:
        row = resolve_attempt_experiment(session, session.get(Attempt, "legacy-attempt"))
        assert row.plan == raw_plan and "parameters" not in row.plan and "budget" not in row.plan
        assert row.identity == identity and row.plan_hash == service.digest(raw_plan)
        assert session.get(Campaign, "legacy-root").request_hash == "f" * 64
    engine.dispose()
