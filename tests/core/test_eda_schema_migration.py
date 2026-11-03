"""Fake additive EDA migration/model parity. No database or private bundle dependency."""
from copy import deepcopy
import hashlib
import importlib.util
import json
from pathlib import Path
import socket
import sqlite3
import subprocess
import sys
from types import SimpleNamespace

import pytest
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql,sqlite
from sqlalchemy.schema import CreateTable

ROOT=Path(__file__).resolve().parents[2]


def load(name,path):
    spec=importlib.util.spec_from_file_location(name,path);module=importlib.util.module_from_spec(spec)
    sys.modules[name]=module;spec.loader.exec_module(module);return module


@pytest.fixture(autouse=True)
def no_operations(monkeypatch):
    def forbidden(*a,**kw):pytest.fail('A13 only permits fake/compile/source checks, no DB/process/network')
    monkeypatch.setattr(sa,'create_engine',forbidden)
    monkeypatch.setattr(sqlite3,'connect',forbidden)
    monkeypatch.setattr(subprocess,'Popen',forbidden)
    monkeypatch.setattr(subprocess,'run',forbidden)
    monkeypatch.setattr(socket,'create_connection',forbidden)


def normalize(sql):return ' '.join(sql.replace('\r','').split())


def migration_table():
    migration=load('a13_fake_migration',ROOT/'migrations/versions/15edarepairjobs.py')
    metadata=sa.MetaData()
    sa.Table('projects',metadata,sa.Column('id',sa.String(128),primary_key=True))
    sa.Table('investigation_versions',metadata,sa.Column('id',sa.String(36),primary_key=True))
    tables=[]
    def create_table(name,*items,**kwargs):
        table=sa.Table(name,metadata,*items,**kwargs);tables.append(table);return table
    migration.op=SimpleNamespace(create_table=create_table)
    migration.upgrade()
    assert len(tables)==1 and tables[0].name=='eda_repair_jobs'
    return tables[0]


def test_one_additive_table_exact_model_and_frozen_ddl_parity():
    table=migration_table()  # only fake create_table sink
    from veriforge.core.eda_repair_models import EdaRepairJob
    model=EdaRepairJob.__table__
    from veriforge.core.database import Base
    assert len(Base.metadata.tables)==26
    assert set(table.columns.keys())==set(model.columns.keys()) and len(table.columns)==21
    for name in table.columns.keys():
        actual,expected=table.c[name],model.c[name]
        assert actual.nullable==expected.nullable and actual.primary_key==expected.primary_key
        assert str(actual.type.compile(dialect=postgresql.dialect()))==str(expected.type.compile(dialect=postgresql.dialect()))
        assert actual.server_default is expected.server_default is None
    def constraints(value):
        return {(type(c).__name__,tuple(col.name for col in c.columns),
            tuple(e.target_fullname for e in c.elements) if isinstance(c,sa.ForeignKeyConstraint) else
            str(c.sqltext) if isinstance(c,sa.CheckConstraint) else None) for c in value.constraints}
    assert constraints(table)==constraints(model)
    frozen=str(CreateTable(model).compile(dialect=postgresql.dialect()))+';'
    assert normalize(str(CreateTable(table).compile(dialect=postgresql.dialect()))+';')==normalize(frozen)
    assert 'CREATE TABLE eda_repair_jobs' in str(CreateTable(table).compile(dialect=sqlite.dialect()))
    assert not list(table.indexes) and len(table.foreign_keys)==3


def test_downgrade_unconditionally_refuses_without_touching_any_operation():
    migration=load('a13_preserve_migration',ROOT/'migrations/versions/15edarepairjobs.py')
    class NoOp:
        def __getattr__(self,key):pytest.fail('Downgrade attempted an operation')
    migration.op=NoOp()
    assert migration.revision=='15edarepairjobs' and migration.down_revision=='14u3repairjobs'
    with pytest.raises(RuntimeError,match='preservation review'):migration.downgrade()
