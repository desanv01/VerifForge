"""Independent source/isolated SQLite review; no operational migration or execution."""
from datetime import datetime, timezone
import hashlib
import importlib.util
from io import StringIO
from pathlib import Path
import re

from alembic import command
from alembic.config import Config
import pytest
import sqlalchemy as sa
from sqlalchemy.exc import IntegrityError

from veriforge.adapters import get_adapter, registered_adapters
from veriforge.api.app import create_app
from veriforge.contracts import Experiment, canonical_hash
from veriforge.core import engineering_catalog as ec, families, service
from veriforge.core.catalog import seed_catalog
from veriforge.core.database import Campaign, Project, make_engine, session_factory

ROOT = Path(__file__).resolve().parents[2]


def cfg(connection):
    config = Config(str(ROOT / "alembic.ini")); config.attributes["connection"] = connection
    return config


@pytest.fixture
def db(tmp_path):
    engine = make_engine("sqlite:///"+(tmp_path/"review.db").as_posix())
    with engine.begin() as connection: command.upgrade(cfg(connection), "13engineeringprojects")
    sessions = session_factory(engine)
    with sessions.begin() as session: seed_catalog(session)
    yield engine, sessions
    engine.dispose()


def spec():
    return ec.EngineeringProjectSpec(project_id="independent-record", name="Retained board", domains=("kicad","system"))


def fifo_plan():
    return Experiment(experiment_id="review-legacy",fixture_id="fifo_correct",objective_ids=("FIFO_BEHAVIOR",),operations=({"op":"reset"},)).model_dump(mode="json")


def test_nine_reviewed_paths_match_frozen_owner_hashes():
    receipt=ROOT/".coordination/deliveries/U9-ENGINEERING-PROJECT-KIND.md"
    if not receipt.is_file(): pytest.skip("private frozen-review receipt not present")
    text=receipt.read_text()
    pairs=re.findall(r"- `([^`]+)`: `([a-f0-9]{64})`",text)
    assert len(pairs)==9
    for path,expected in pairs: assert hashlib.sha256((ROOT/path).read_bytes()).hexdigest()==expected,path


@pytest.mark.parametrize("kind,contract",[("rtl_fixture",None),("engineering_recorded","fifo-v1"),("software",None),(None,None)])
def test_sql_constraints_and_campaign_contract_remain_strict(db,kind,contract):
    with pytest.raises(IntegrityError):
        with db[0].begin() as connection:
            connection.execute(sa.text("INSERT INTO projects(id,owner,data,kind,contract_id) VALUES('bad','local','{}',:kind,:contract)"),dict(kind=kind,contract=contract))
    assert not next(c for c in sa.inspect(db[0]).get_columns("campaigns") if c["name"]=="contract_id")["nullable"]


def test_raw_legacy_insert_defaults_rtl_kind_and_preserves_metadata(db):
    with db[0].begin() as connection:
            connection.exec_driver_sql("INSERT INTO projects(id,owner,data,contract_id) VALUES('old','local','{\"id\":\"old\",\"marker\":17}','fifo-v1')")
    with db[1]() as session:
        wire=ec.project_view(session.get(Project,"old"))
        assert wire==dict(id="old",marker=17,kind="rtl_fixture",contract_id="fifo-v1")
        assert families.project_adapter(session.get(Project,"old")).adapter_id=="rtl.fifo.v1"


@pytest.mark.parametrize("column,value",[("id","renamed"),("owner","foreign"),("data",'{}'),("kind","rtl_fixture"),("contract_id","fifo-v1")])
def test_direct_sql_cannot_rewrite_recorded_identity(db,column,value):
    with db[1].begin() as session: ec.create_engineering_project(session,spec())
    with pytest.raises(IntegrityError):
        with db[0].begin() as connection:
            connection.execute(sa.text(f"UPDATE projects SET {column}=:value WHERE id='independent-record'"),dict(value=value))
    with db[1]() as session: assert ec.project_view(ec.get_engineering_project(session,spec().project_id,expected=spec()))["kind"]=="engineering_recorded"
    with pytest.raises(IntegrityError):
        with db[0].begin() as connection: connection.exec_driver_sql("DELETE FROM projects WHERE id='independent-record'")


def test_catalog_rollback_idempotency_typed_authority_and_immutable_content(db):
    with pytest.raises(RuntimeError):
        with db[1].begin() as session:
            ec.create_engineering_project(session,spec()); raise RuntimeError("caller rolls back")
    with db[1]() as session: assert session.get(Project,spec().project_id) is None
    with db[1].begin() as session:
        first=ec.create_engineering_project(session,spec())
        assert ec.create_engineering_project(session,spec()).id==first.id
        with pytest.raises(service.Conflict): ec.create_engineering_project(session,spec().model_copy(update={"description":"different"}))
        with pytest.raises(service.Conflict): ec.create_engineering_project(session,spec().model_copy(update={"project_id":"fifo_correct"}))
        with pytest.raises(ValueError): ec.create_engineering_project(session,spec().model_dump())
    with pytest.raises(ValueError): ec.EngineeringProjectSpec(**spec().model_dump(),adapter_id="rtl.fifo.v1")
    with pytest.raises(ValueError): ec.EngineeringProjectSpec(**{**spec().model_dump(),"domains":("kicad","kicad")})


def test_recorded_rejection_precedes_request_hash_idempotency_and_fifo_resolution(db,monkeypatch):
    with db[1].begin() as session:
        legacy=service.create_campaign(session,"fifo_correct",fifo_plan(),"occupied-key")
        assert service.create_campaign(session,"fifo_correct",fifo_plan(),"occupied-key").id==legacy.id
        ec.create_engineering_project(session,spec())
    def forbidden(*args,**kw): pytest.fail("engineering classification must occur first")
    monkeypatch.setattr(service,"digest",forbidden)
    monkeypatch.setattr(service,"project_adapter",forbidden)
    monkeypatch.setattr(families,"get_adapter",forbidden)
    with db[1].begin() as session:
        for adapter in ("rtl.fifo.v1","rtl.arithmetic.v1","rtl.axi_lite.v1"):
            with pytest.raises(service.Conflict,match="do not support RTL"):
                service.create_campaign(session,spec().project_id,{},"occupied-key",adapter_id=adapter)
        with pytest.raises(service.Conflict,match="do not support RTL"): families.project_adapter(session.get(Project,spec().project_id))
        assert session.scalar(sa.select(sa.func.count()).select_from(Campaign))==1


def test_project_api_handlers_serialize_kind_without_executable_record_claims(db):
    with db[1].begin() as session: ec.create_engineering_project(session,spec())
    app=create_app(db[0],initialize_catalog=False)
    endpoint=lambda path:next(route.endpoint for route in app.routes if getattr(route,"path",None)==path and "GET" in route.methods)
    board=endpoint("/api/projects/{project_id}")(spec().project_id)
    assert board==dict(id=spec().project_id,name=spec().name,description="",domains=["kicad","system"],kind="engineering_recorded",contract_id=None)
    items={item["id"]:item for item in endpoint("/api/projects")()["items"]}
    assert items[spec().project_id]==board and items["fifo_correct"]["kind"]=="rtl_fixture"
    assert items["fifo_correct"]["contract_id"]=="fifo-v1" and items["fifo_correct"]["adapter_id"]=="rtl.fifo.v1"
    assert not any(getattr(route,"path",None)=="/api/projects" and "POST" in route.methods for route in app.routes)
    with pytest.raises(service.NotFound): endpoint("/api/projects/{project_id}")("missing")
    assert endpoint("/api/health")()["investigation_api"]=="1"
    with db[1].begin() as session:
        legacy=session.get(Project,"fifo_correct")
        legacy.data={**legacy.data,"kind":"engineering_recorded"}
        session.add(Project(id="other-owner",owner="foreign",kind="engineering_recorded",contract_id=None,data={}))
    with pytest.raises(service.Conflict): endpoint("/api/projects/{project_id}")("fifo_correct")
    with pytest.raises(service.NotFound): endpoint("/api/projects/{project_id}")("other-owner")


def reflected_seed(connection):
    metadata=sa.MetaData()
    contracts=sa.Table("contracts",metadata,autoload_with=connection)
    projects=sa.Table("projects",metadata,autoload_with=connection)
    assert "kind" not in projects.c
    seen=set()
    for descriptor in registered_adapters():
        reviewed=descriptor.reviewed_contract()
        if reviewed.contract_id not in seen:
            connection.execute(contracts.insert(),dict(id=reviewed.contract_id,data=reviewed.model_dump(mode="json"),identity=dict(contract_hash=canonical_hash(reviewed),checker_hash=reviewed.checker.sha256)))
            seen.add(reviewed.contract_id)
        for fixture in descriptor.fixture_ids:
            source=descriptor.fixture_source(fixture)
            data=dict(id=fixture,name=fixture.replace("_"," ").title(),family=descriptor.target,support_level="experimental",contract_id=reviewed.contract_id,
                source_file_hash=source["source_file_hash"],variant=source["variant"],adapter_id=descriptor.adapter_id,
                experiment_schema_version=descriptor.experiment_schema()["properties"]["schema_version"]["const"],trace_schema=descriptor.trace_schema,trace_artifact=descriptor.trace_artifact)
            connection.execute(projects.insert(),dict(id=fixture,owner="local",contract_id=reviewed.contract_id,data=data))


def test_sqlite_fk_preservation_and_explicit_outer_rollback(tmp_path):
    engine=make_engine("sqlite:///"+(tmp_path/"history.db").as_posix())
    try:
        with engine.begin() as connection:
            command.upgrade(cfg(connection),"12u2resources"); reflected_seed(connection)
            table=sa.Table("campaigns",sa.MetaData(),autoload_with=connection)
            connection.execute(table.insert(),dict(id="old",owner="local",project_id="fifo_correct",contract_id="fifo-v1",create_key="old",request_hash="a"*64,plan={"bytes":"retained"},identity={},execution_status="draft",version=0,event_sequence=0,created_at=datetime.now(timezone.utc),updated_at=datetime.now(timezone.utc)))
        with engine.connect() as connection:
            before={name:connection.exec_driver_sql("SELECT * FROM "+name+" ORDER BY id").all() for name in ("projects","campaigns","contracts")}
        with pytest.raises(RuntimeError):
            with engine.begin() as connection:
                command.upgrade(cfg(connection),"13engineeringprojects"); raise RuntimeError("rollback DDL")
        with engine.connect() as connection:
            assert "kind" not in {c["name"] for c in sa.inspect(connection).get_columns("projects")}
            assert connection.exec_driver_sql("SELECT version_num FROM alembic_version").scalar()=="12u2resources"
        with engine.begin() as connection:
            connection.exec_driver_sql("BEGIN"); connection.exec_driver_sql("PRAGMA defer_foreign_keys=ON")
            command.upgrade(cfg(connection),"13engineeringprojects")
            assert connection.exec_driver_sql("PRAGMA defer_foreign_keys").scalar()==1
        with engine.begin() as connection:
            for name,rows in before.items():
                columns=[c["name"] for c in sa.inspect(connection).get_columns(name) if c["name"]!="kind"]
                assert connection.exec_driver_sql("SELECT "+",".join(columns)+" FROM "+name+" ORDER BY id").all()==rows
            assert connection.exec_driver_sql("PRAGMA foreign_keys").scalar()==1
            assert connection.exec_driver_sql("PRAGMA foreign_key_check").all()==[]
            command.downgrade(cfg(connection),"12u2resources")
        with engine.connect() as connection:
            assert connection.exec_driver_sql("PRAGMA foreign_key_check").all()==[]
            assert connection.exec_driver_sql("SELECT * FROM projects ORDER BY id").all()==before["projects"]
    finally:engine.dispose()


def test_postgres_offline_ddl_keeps_campaign_required_and_installs_immutable_guards(monkeypatch):
    # Emit only this revision; no server/driver connection or operational DDL.
    monkeypatch.setenv("VERIFORGE_DATABASE_URL","postgresql://offline/unused")
    output=StringIO(); config=Config(str(ROOT/"alembic.ini"),output_buffer=output)
    command.upgrade(config,"12u2resources:13engineeringprojects",sql=True)
    ddl=output.getvalue()
    assert "ALTER TABLE projects ALTER COLUMN contract_id DROP NOT NULL" in ddl
    assert "ck_project_kind_contract" in ddl and "engineering_recorded" in ddl and "rtl_fixture" in ddl
    assert "CREATE TRIGGER project_kind_guard BEFORE UPDATE OR DELETE" in ddl
    assert "NEW.data::text IS DISTINCT FROM OLD.data::text" in ddl
    assert "ALTER TABLE campaigns" not in ddl


@pytest.mark.parametrize("exercise",[None,"test_round_admission_crash_rolls_back_feedback_ledger_and_outbox_together",
    "test_claim_before_commit_crash_does_not_mark_remote_call_started",
    "test_accepted_completion_crash_and_duplicate_have_one_admission",
    "test_committed_claim_recovery_never_retries_uncertain_remote_call",
    "test_exact_body_model_policy_and_forged_context_lease"])
def test_reflected_revision_nine_seed_can_avoid_current_project_orm(tmp_path,exercise):
    from veriforge.core import planner_rounds as rounds
    from veriforge.core.planner_experiments import record_experiment
    from veriforge.contracts import AttemptResult
    from veriforge.core.database import Attempt
    from veriforge.planner.models import InvestigationLimits
    engine=make_engine("sqlite:///"+(tmp_path/"rounds9.db").as_posix())
    try:
        with engine.begin() as connection:
            command.upgrade(cfg(connection),"9u2rounds"); reflected_seed(connection)
        sessions=session_factory(engine)
        plan=Experiment(experiment_id="review-seed",fixture_id="fifo_correct",objective_ids=("FIFO_BEHAVIOR",),budget={"max_cycles":8,"wall_time_seconds":2.0},operations=({"op":"reset"},{"op":"write","value":17},{"op":"read"}))
        wire=plan.model_dump(mode="json"); identity=get_adapter("rtl.fifo.v1").expected_identity(plan)
        with sessions.begin() as session:
            parent=Campaign(project_id=plan.fixture_id,contract_id=plan.contract_id,adapter_id="rtl.fifo.v1",completion_policy="sequential",create_key="review-seed",
                request_hash=service.digest({"request_hash":service.digest({"project_id":plan.fixture_id,"plan":wire}),"completion_policy":"sequential"}),plan=wire,identity=identity)
            session.add(parent);session.flush()
            record_experiment(session,parent,wire,"initial",origin="manual",initial=True)
            service.emit(session,parent,"campaign.created")
            service.start(session,parent.id)
            attempt=session.scalar(sa.select(Attempt).where(Attempt.campaign_id==parent.id))
            lease,_,_=service.claim(session,attempt.id)
            result=AttemptResult(attempt_id="synthetic-review",experiment_id=plan.experiment_id,execution_status="completed",verdict="pass_within_scope",identity={**identity,"environment_hash":"a"*64},usage={"cycles":3})
            service.finalize(session,attempt.id,lease,result.model_dump(mode="json"))
            rounds.attach_planner(session,parent.id,limits=InvestigationLimits(max_rounds=4,max_total_cycles=80,max_cycles_per_experiment=8,max_wall_seconds=60.0,max_execution_seconds=2.0,max_model_seconds=2.0),requested_model="review-model",at=datetime(2026,10,29,16,tzinfo=timezone.utc))
        with engine.connect() as connection:
            assert connection.exec_driver_sql("SELECT version_num FROM alembic_version").scalar()=="9u2rounds"
            assert "kind" not in {c["name"] for c in sa.inspect(connection).get_columns("projects")}
            assert connection.exec_driver_sql("PRAGMA foreign_key_check").all()==[]
        if exercise is not None:
            module_spec=importlib.util.spec_from_file_location("unchanged_planner_review",ROOT/"tests/acceptance/test_planner_rounds_review.py")
            module=importlib.util.module_from_spec(module_spec);module_spec.loader.exec_module(module)
            getattr(module,exercise)((engine,sessions,parent.id))
    finally:engine.dispose()
