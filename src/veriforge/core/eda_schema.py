"""Uncached, read-only schema admission for explicitly enabled native EDA work.

Call only AFTER the trusted runtime's enabled/control check. Importing this module
does not inspect/connect; it never creates an engine, tables or a migration.
"""
from sqlalchemy import UniqueConstraint, inspect, text

from . import service
from .eda_repair_models import EdaRepairJob

REVISION = "15edarepairjobs"
_REFUSAL = "EDA schema unavailable or incompatible; explicit schema15 qualification required"


def _require(value):
    if not value:
        raise service.Conflict(_REFUSAL)


def _column_type_matches(expected, actual, dialect):
    # Render both types for the actual dialect: reflection subclasses such as
    # VARCHAR/TIMESTAMP match their ORM equivalents, while CHAR/JSONB, changed
    # widths and PostgreSQL timezone differences do not silently broaden shape.
    return expected.compile(dialect=dialect) == actual.compile(dialect=dialect)


def _foreign_keys(reflector, table):
    default = reflector.default_schema_name
    expected = sorted((tuple(constraint.column_keys), constraint.referred_table.schema or default,
                       constraint.referred_table.name, tuple(element.column.name for element in constraint.elements))
                      for constraint in table.foreign_key_constraints)
    actual = sorted((tuple(item["constrained_columns"]), item.get("referred_schema") or default,
                     item["referred_table"], tuple(item["referred_columns"]))
                    for item in reflector.get_foreign_keys(table.name))
    _require(actual == expected)


def require_eda_schema_session(session):
    """Check a supplied session without committing, rolling back or closing it.

    Model-derived exact columns, PK/unique keys and FK targets are checked. This
    is not a complete audit of administrator actions, SQL triggers or check SQL.
    """
    try:
        reflector = inspect(session.connection())
        table = EdaRepairJob.__table__
        _require(reflector.has_table("alembic_version") and reflector.has_table(table.name))
        result = session.execute(text("SELECT version_num FROM alembic_version"))
        try:
            rows = result.fetchmany(2)  # zero/multiple revisions are never guessed
            _require(len(rows) == 1 and len(rows[0]) == 1 and rows[0][0] == REVISION)
        finally:
            result.close()
        columns = reflector.get_columns(table.name)
        _require(len(columns) == len(table.columns))
        by_name = {column["name"]: column for column in columns}
        _require(len(by_name) == len(columns) and set(by_name) == set(table.columns.keys()))
        for expected in table.columns:
            actual = by_name[expected.name]
            _require(actual["nullable"] is expected.nullable and
                     _column_type_matches(expected.type, actual["type"], reflector.dialect))
        _require(tuple(reflector.get_pk_constraint(table.name)["constrained_columns"]) ==
                 tuple(column.name for column in table.primary_key.columns))
        expected_unique = sorted(tuple(sorted(column.name for column in constraint.columns))
                                 for constraint in table.constraints if isinstance(constraint, UniqueConstraint))
        actual_unique = sorted(tuple(sorted(item["column_names"]))
                               for item in reflector.get_unique_constraints(table.name))
        _require(actual_unique == expected_unique)
        _foreign_keys(reflector, table)
    except Exception:
        # Never expose driver messages, SQL, URLs, credentials or config values.
        raise service.Conflict(_REFUSAL) from None


def require_eda_schema(sessions):
    """Validate current schema with the existing factory on each enabled entry.

    Disabled callers must return before calling this function. No cache/fallback
    allows a previous good schema to authorize a later old/partial/unknown one.
    """
    try:
        with sessions() as session:
            require_eda_schema_session(session)
    except Exception:
        raise service.Conflict(_REFUSAL) from None
