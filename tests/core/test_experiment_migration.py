"""Legacy migration and persisted authority checks against isolated SQLite only."""

from alembic import command
from alembic.config import Config
import pytest
from sqlalchemy import MetaData, Table, inspect, select, text

from veriforge.adapters import get_adapter, registered_adapters
from veriforge.arithmetic import ArithmeticExperiment
from veriforge.contracts import Experiment, canonical_hash
from veriforge.core import service
from veriforge.core.database import Attempt, AttemptExperiment, Campaign, ExperimentRecord, make_engine, now, session_factory


def config_for(connection):
    config = Config("alembic.ini")
    config.attributes["connection"] = connection
    return config


def seed_historical_catalog(connection):
    # This fixture deliberately remains at revision7. Reflect its actual tables;
    # today's Project ORM includes kind, which does not exist in that schema.
    metadata = MetaData()
    contracts = Table('contracts', metadata, autoload_with=connection)
    projects = Table('projects', metadata, autoload_with=connection)
    seen = set()
    for descriptor in registered_adapters():
        reviewed = descriptor.reviewed_contract()
        if reviewed.contract_id not in seen:
            connection.execute(contracts.insert().values(id=reviewed.contract_id, data=reviewed.model_dump(mode='json'),
                identity={'contract_hash': canonical_hash(reviewed), 'checker_hash': reviewed.checker.sha256}))
            seen.add(reviewed.contract_id)
        schema_version = descriptor.experiment_schema()['properties']['schema_version']['const']
        for fixture in descriptor.fixture_ids:
            source = descriptor.fixture_source(fixture)
            connection.execute(projects.insert().values(id=fixture, owner='local', contract_id=reviewed.contract_id,
                data={'id': fixture, 'name': fixture.replace('_', ' ').title(), 'family': descriptor.target,
                    'support_level': 'experimental', 'contract_id': reviewed.contract_id,
                    'source_file_hash': source['source_file_hash'], 'variant': source['variant'],
                    'adapter_id': descriptor.adapter_id, 'experiment_schema_version': schema_version,
                    'trace_schema': descriptor.trace_schema, 'trace_artifact': descriptor.trace_artifact}))


@pytest.fixture
def legacy(tmp_path):
    engine = make_engine("sqlite:///" + (tmp_path / "legacy.db").as_posix())
    with engine.begin() as connection: command.upgrade(config_for(connection), "7bf054adapter")
    with engine.begin() as connection: seed_historical_catalog(connection)
    fifo = Experiment(experiment_id="old-fifo", fixture_id="fifo_correct", objective_ids=("FIFO_BEHAVIOR",), parameters={"width": 3, "depth": 2}, operations=({"op": "reset"}, {"op": "write", "value": 7}, {"op": "read"}))
    arithmetic = ArithmeticExperiment(experiment_id="old-arithmetic", operations=({"op": "reset"}, {"op": "multiply", "a": 255, "b": 255}, {"op": "idle", "cycles": 2}))
    payloads = []
    with engine.begin() as connection:
        metadata = MetaData()
        tables = {name: Table(name, metadata, autoload_with=connection) for name in ("campaigns", "attempts", "findings", "dispatches", "replay_links")}
        stamp = now()
        for index, (experiment, adapter) in enumerate(((fifo, "rtl.fifo.v1"), (arithmetic, "rtl.arithmetic.v1"))):
            campaign_id = f"old-campaign-{index}"
            typed_plan = experiment.model_dump(mode="json")
            identity = get_adapter(adapter).expected_identity(experiment)
            payloads.append((campaign_id, typed_plan, identity))
            connection.execute(tables["campaigns"].insert().values(id=campaign_id, owner="local", adapter_id=adapter, project_id=experiment.fixture_id, contract_id=experiment.contract_id, create_key=f"old-key-{index}", request_hash=service.digest({"project_id": experiment.fixture_id, "plan": typed_plan}), plan=typed_plan, identity=identity, execution_status="completed", verdict="design_mismatch", version=9, event_sequence=9, created_at=stamp, updated_at=stamp))
            for repeat in range(2):
                attempt_id = f"old-attempt-{index}-{repeat}"
                connection.execute(tables["attempts"].insert().values(id=attempt_id, campaign_id=campaign_id, lease=f"old-lease-{index}-{repeat}", execution_status="completed", verdict="design_mismatch", result={"synthetic_legacy_marker": repeat}, created_at=stamp, finished_at=stamp))
                connection.execute(tables["dispatches"].insert().values(attempt_id=attempt_id, sent_at=stamp, error=None))
            connection.execute(tables["findings"].insert().values(id=f"old-finding-{index}", campaign_id=campaign_id, attempt_id=f"old-attempt-{index}-0", data={"legacy_plan_hash": service.digest(typed_plan), "untouched": True}))
        connection.execute(tables["replay_links"].insert().values(campaign_id="old-campaign-1", source_attempt_id="old-attempt-0-0", owner="local", request_key="old-replay"))
    yield engine, payloads
    engine.dispose()


def table_snapshot(connection):
    # Raw stored JSON text, IDs, timestamps and references must remain unchanged.
    return {name: list(connection.execute(text(f"SELECT * FROM {name} ORDER BY 1")).mappings()) for name in ("campaigns", "attempts", "findings", "dispatches", "replay_links")}


def test_backfill_preserves_legacy_payload_bytes_hashes_references_and_attempt_ids(legacy):
    engine, payloads = legacy
    with engine.connect() as connection: before = table_snapshot(connection)
    with engine.begin() as connection: command.upgrade(config_for(connection), "head")
    with engine.connect() as connection:
        after = table_snapshot(connection)
        for name, rows in before.items():
            for old, new in zip(rows, after[name]):
                assert dict(old) == {key: new[key] for key in old}
        assert list(connection.execute(text("PRAGMA foreign_key_check"))) == []
    with session_factory(engine)() as session:
        for campaign_id, old_plan, old_identity in payloads:
            record = session.scalar(select(ExperimentRecord).where(ExperimentRecord.campaign_id == campaign_id))
            assert record.plan == old_plan and record.identity == old_identity
            assert record.plan_hash == service.digest(old_plan) and record.ordinal == 1
            assert record.contract_snapshot["parameters"] == old_plan["parameters"]
            assert session.get(Campaign, campaign_id).completion_policy == "single"
            links = list(session.scalars(select(AttemptExperiment).where(AttemptExperiment.campaign_id == campaign_id)))
            assert len(links) == 2 and {link.experiment_id for link in links} == {record.id}
            for link in links:
                assert service.resolve_attempt_experiment(session, session.get(Attempt, link.attempt_id)).id == record.id


def test_single_plan_downgrade_and_reupgrade_preserve_raw_history_and_stable_record_ids(legacy):
    engine, _ = legacy
    with engine.begin() as connection: command.upgrade(config_for(connection), "head")
    with session_factory(engine)() as session: original_ids = set(session.scalars(select(ExperimentRecord.id)))
    with engine.connect() as connection: before = table_snapshot(connection)
    with engine.begin() as connection:
        command.downgrade(config_for(connection), "7bf054adapter")
        command.upgrade(config_for(connection), "head")
    with engine.connect() as connection: assert table_snapshot(connection) == before
    with session_factory(engine)() as session: assert set(session.scalars(select(ExperimentRecord.id))) == original_ids


def test_invalid_legacy_contract_provenance_rejects_before_any_schema_change(legacy):
    engine, _ = legacy
    with engine.begin() as connection:
        campaigns = Table("campaigns", MetaData(), autoload_with=connection)
        payload = connection.scalar(select(campaigns.c.identity).limit(1))
        connection.execute(campaigns.update().values(identity={**payload, "contract_hash": "0" * 64}))
    with pytest.raises(ValueError, match="will not invent"):
        with engine.begin() as connection: command.upgrade(config_for(connection), "head")
    assert "experiments" not in inspect(engine).get_table_names()
    assert "completion_policy" not in {col["name"] for col in inspect(engine).get_columns("campaigns")}
