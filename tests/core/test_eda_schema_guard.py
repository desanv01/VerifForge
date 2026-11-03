"""Fake inspector/session only: no database, engine, network or native work."""
from copy import deepcopy
from types import SimpleNamespace

import pytest
from sqlalchemy import CHAR, Integer, String, UniqueConstraint
from sqlalchemy.dialects import postgresql

from veriforge.core import eda_schema as guard, service


class Inspector:
    default_schema_name = "public"
    dialect = postgresql.dialect()

    def __init__(self):
        table = guard.EdaRepairJob.__table__
        self.tables = {"alembic_version", table.name}
        self.columns = [{"name": column.name, "type": deepcopy(column.type), "nullable": column.nullable}
                        for column in table.columns]
        self.primary = {"constrained_columns": [column.name for column in table.primary_key.columns]}
        self.unique = [{"column_names": [column.name for column in item.columns]}
                       for item in table.constraints if isinstance(item, UniqueConstraint)]
        self.foreign = [{"constrained_columns": list(item.column_keys), "referred_schema": None,
            "referred_table": item.referred_table.name, "referred_columns": [e.column.name for e in item.elements]}
            for item in table.foreign_key_constraints]
    def has_table(self, name): return name in self.tables
    def get_columns(self, name): return self.columns
    def get_pk_constraint(self, name): return self.primary
    def get_unique_constraints(self, name): return self.unique
    def get_foreign_keys(self, name): return self.foreign


class Session:
    def __init__(self, revisions=None):
        self.rows = [(guard.REVISION,)] if revisions is None else revisions
        self.events = []
        self.error = None
    def __enter__(self): self.events.append("enter"); return self
    def __exit__(self, *_): self.events.append("session-close")
    def connection(self): self.events.append("connection"); return self
    def execute(self, query):
        self.events.append(str(query))
        if self.error: raise self.error
        return self
    def fetchmany(self, count):
        assert count == 2
        return self.rows[:count]
    def close(self): self.events.append("result-close")


@pytest.fixture
def state(monkeypatch):
    inspector, session = Inspector(), Session()
    monkeypatch.setattr(guard, "inspect", lambda connection: inspector)
    return inspector, session


def test_exact15_and_default_schema_fk_spelling_pass(state):
    inspector, session = state
    for fk in inspector.foreign: fk["referred_schema"] = "public"
    guard.require_eda_schema(lambda:session)
    assert session.events[-2:] == ["result-close", "session-close"]
    assert session.events.count("SELECT version_num FROM alembic_version") == 1


@pytest.mark.parametrize("rows", [[], [("14u3repairjobs",)], [("16future",)],
    [(guard.REVISION,), (guard.REVISION,)], [(None,)], [(guard.REVISION,"extra")]])
def test_old_missing_unknown_multiple_or_malformed_revision_refuses(state, rows):
    _, session = state
    session.rows = rows
    with pytest.raises(service.Conflict): guard.require_eda_schema(lambda:session)
    assert "result-close" in session.events and session.events[-1] == "session-close"


@pytest.mark.parametrize("table", ["alembic_version", "eda_repair_jobs"])
def test_missing_table_never_queries_revision(state, table):
    inspector, session = state
    inspector.tables.remove(table)
    with pytest.raises(service.Conflict): guard.require_eda_schema(lambda:session)
    assert not any(item.startswith("SELECT") for item in session.events)


@pytest.mark.parametrize("change", ["missing", "extra", "duplicate", "type", "width", "fixed-char", "jsonb", "nullable", "timezone", "pk", "unique", "fk", "fk-schema"])
def test_partial15_model_shape_refuses(state, change):
    inspector, session = state
    if change == "missing": inspector.columns.pop()
    elif change == "extra": inspector.columns.append(dict(inspector.columns[0], name="extra"))
    elif change == "duplicate": inspector.columns[-1] = dict(inspector.columns[0])
    elif change == "type": inspector.columns[0]["type"] = Integer()
    elif change == "width": inspector.columns[0]["type"] = String(1)
    elif change == "fixed-char": inspector.columns[0]["type"] = CHAR(36)
    elif change == "jsonb": next(c for c in inspector.columns if c["name"] == "ticket")["type"] = postgresql.JSONB()
    elif change == "nullable": inspector.columns[0]["nullable"] = True
    elif change == "timezone":
        next(c for c in inspector.columns if c["name"] == "deadline_at")["type"].timezone = False
    elif change == "pk": inspector.primary["constrained_columns"] = []
    elif change == "unique": inspector.unique = []
    elif change == "fk": inspector.foreign[0]["referred_columns"] = ["wrong"]
    else: inspector.foreign[0]["referred_schema"] = "unapproved"
    with pytest.raises(service.Conflict): guard.require_eda_schema(lambda:session)


def test_no_success_cache_after_schema_changes(state):
    _, session = state
    guard.require_eda_schema(lambda:session)
    session.rows = [("14u3repairjobs",)]
    with pytest.raises(service.Conflict): guard.require_eda_schema(lambda:session)
    assert session.events.count("SELECT version_num FROM alembic_version") == 2


@pytest.mark.parametrize("where", ["factory", "execute", "inspect", "result-close", "session-close"])
def test_raw_errors_are_projected_safely(state, monkeypatch, where):
    _, session = state
    private = "synthetic-sql-config-password-value"
    def fail(*_): raise RuntimeError(private)
    factory = lambda:session
    if where == "factory": factory = fail
    elif where == "execute": session.error = RuntimeError(private)
    elif where == "inspect": monkeypatch.setattr(guard, "inspect", fail)
    elif where == "result-close": monkeypatch.setattr(session, "close", fail)
    else: monkeypatch.setattr(Session, "__exit__", fail)
    with pytest.raises(service.Conflict) as caught: guard.require_eda_schema(factory)
    assert private not in str(caught.value) and caught.value.__suppress_context__


def test_disabled_caller_has_no_query_or_factory_use():
    from veriforge.core.eda_repair_jobs import EdaRepairPolicy
    from veriforge.core.eda_repair_runtime import EdaRepairRuntime
    # Exercise the real existing disabled gate in the prescribed integration
    # order; production wiring is main-owned and not modified by this task.
    calls = []
    def forbidden_factory():
        calls.append("factory")
        pytest.fail("disabled runtime attempted database access")
    runtime = EdaRepairRuntime(forbidden_factory, None, lambda:EdaRepairPolicy(), lambda:"RUNNING")
    with pytest.raises(service.Conflict, match="disabled"):
        runtime.enabled()
        guard.require_eda_schema(runtime.sessions)
    assert calls == []


def test_session_helper_does_not_close_or_commit_the_callers_session(state):
    _, session = state
    guard.require_eda_schema_session(session)
    assert "session-close" not in session.events and session.events[-1] == "result-close"
