"""Explicit local recorded import; default mode prepares without opening a DB.

The fixed case and input byte authority belong to prepare_corrected_case.
--commit requires an explicitly configured VERIFORGE_DATABASE_URL and an
idempotency key. This module never migrates, dispatches or executes input files.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path

from sqlalchemy import create_engine, event, inspect, text
from sqlalchemy.engine import make_url
from sqlalchemy.exc import SQLAlchemyError

from veriforge.system_investigation import prepare_import, records
from . import investigations, service
from .database import (Investigation, InvestigationArtifact, InvestigationExecutionLink,
                       InvestigationVersion, Project, session_factory)
from .engineering_catalog import EngineeringProjectSpec, create_engineering_project


SUPPORTED_REVISION = "13engineeringprojects"
PROJECT_SPEC = EngineeringProjectSpec(
    project_id=prepare_import.PROJECT_ID,
    name="Recorded carrier board investigation",
    description="Recorded KiCad connectivity and commented XDC reference; logical cable intent only. "
                "Shared and unreviewed ancestry remains inconclusive. No new tool execution.",
    domains=("kicad", "vivado", "system"),
)


class RecordedImportError(ValueError):
    """Only fixed diagnostic codes, never driver text, URLs, paths or bytes."""

    def __init__(self, stage, code):
        self.stage, self.code = stage, code
        super().__init__(f"{stage}: {code}")


def require_supported_schema(session):
    """Exact supported revision plus required table shape; no migration fallback.

    Future revisions require an intentional source/review update, rather than a
    lexical revision comparison. An active caller transaction is mandatory.
    """
    if not session.in_transaction():
        raise RecordedImportError("transaction", "caller_transaction_required")
    try:
        connection = session.connection()
        if connection.dialect.name not in {"sqlite", "postgresql"}:
            raise RecordedImportError("schema", "unsupported_database_dialect")
        schema = inspect(connection)
        if not schema.has_table("alembic_version") or connection.execute(
                text("SELECT version_num FROM alembic_version")).scalars().all() != [SUPPORTED_REVISION]:
            raise RecordedImportError("schema", "unsupported_schema_revision")
        for model in (Project, Investigation, InvestigationVersion, InvestigationArtifact, InvestigationExecutionLink):
            table = model.__table__
            if not schema.has_table(table.name):
                raise RecordedImportError("schema", "required_import_table_missing")
            columns = {column["name"]: column for column in schema.get_columns(table.name)}
            if not set(table.columns.keys()) <= columns.keys():
                raise RecordedImportError("schema", "required_import_column_missing")
        project_columns = {column["name"]: column for column in schema.get_columns("projects")}
        if not project_columns["contract_id"]["nullable"] or project_columns["kind"]["nullable"]:
            raise RecordedImportError("schema", "project_kind_schema_incompatible")
        if "ck_project_kind_contract" not in {row["name"] for row in schema.get_check_constraints("projects")}:
            raise RecordedImportError("schema", "project_kind_constraint_missing")
    except SQLAlchemyError:
        raise RecordedImportError("schema", "schema_unavailable") from None


def _digest(admitted):
    return hashlib.sha256(records.serialize_investigation(admitted)).hexdigest()


def _readback(session, summary, admitted):
    """Existing detail reader re-admits every exact stored blob before return."""
    view = investigations.version_view(session, summary["investigation_id"], summary["id"])
    if (view["envelope"] != admitted.envelope.model_dump(mode="json") or
            view["content_hash"] != _digest(admitted) or view["execution_contexts"] or view["ordinal"] != 1):
        raise RecordedImportError("readback", "stored_admission_disagrees")
    return {
        "project_id": PROJECT_SPEC.project_id,
        "db_investigation_id": summary["investigation_id"], "db_version_id": summary["id"],
        "native_investigation_id": admitted.envelope.record.investigation_id,
        "native_revision_id": admitted.manifest.revision_id,
        "content_sha256": view["content_hash"], "artifacts": len(admitted.artifacts),
        "total_bytes": admitted.envelope.total_artifact_bytes,
        "operation": "admission_only", "new_execution": "not_run",
        "graph_verdicts": sorted({r.verdict for r in admitted.manifest.relations}),
        "database": "pending_caller_commit",
    }


def _persist_prepared(session, admitted, *, idempotency_key, at):
    """Private trusted composition, not a JSON/admitted-wrapper write endpoint."""
    require_supported_schema(session)
    if type(idempotency_key) is not str or not 1 <= len(idempotency_key) <= 128:
        raise RecordedImportError("arguments", "bounded_idempotency_key_required")
    if not isinstance(at, datetime) or at.tzinfo is None or at.utcoffset() is None:
        raise RecordedImportError("arguments", "aware_service_time_required")
    if session.new or session.dirty or session.deleted:
        raise RecordedImportError("transaction", "clean_caller_session_required")
    # A sealed object came from the fixed trusted preparation boundary, never
    # from client JSON. Persistence still performs complete admission itself.
    try:
        records.serialize_investigation(admitted)
        record = admitted.envelope.record
        if (record.project_id != PROJECT_SPEC.project_id or record.revision_id != prepare_import.REVISION_ID or
                record.investigation_id != prepare_import.INVESTIGATION_ID or record.attempts):
            raise RecordedImportError("admission", "fixed_case_association_required")
        authority = records.create_admission_authority(project_id=record.project_id, revision_id=record.revision_id,
            project_revision_ref=record.project_revision_ref, manifest_ref=record.manifest_ref,
            bridge_report_ref=record.bridge_report_ref, attempts=())
        connection = session.connection()
        # Python sqlite legacy transaction mode otherwise releases a top-level
        # SAVEPOINT as a commit. This BEGIN belongs to the caller transaction.
        if connection.dialect.name == "sqlite" and not connection.connection.driver_connection.in_transaction:
            connection.exec_driver_sql("BEGIN")
        with session.begin_nested():
            create_engineering_project(session, PROJECT_SPEC)
            summary = investigations.persist_investigation(session, record, admitted.artifacts,
                authority=authority, idempotency_key=idempotency_key, at=at)
            return _readback(session, summary, admitted)
    except RecordedImportError:
        raise
    except service.Conflict:
        raise RecordedImportError("storage", "immutable_content_conflict") from None
    except service.NotFound:
        raise RecordedImportError("storage", "owned_association_unavailable") from None
    except SQLAlchemyError:
        raise RecordedImportError("storage", "database_operation_failed") from None
    except (ValueError, TypeError, AttributeError, KeyError):
        raise RecordedImportError("admission", "admission_or_readback_inconsistent") from None


def import_corrected_case(session, *, idempotency_key, at, bundle_root, xdc, netlist, board,
                          generator, logical_mapping, native_receipt):
    """Fixed local service; caller begins/commits/rolls back its transaction.

    No automatic retry or new-key fallback. Repeat the same key and exact inputs
    after an uncertain commit outcome. Never claim success until caller commit.
    """
    require_supported_schema(session)
    admitted = prepare_import.prepare_corrected_case(bundle_root=bundle_root, xdc=xdc, netlist=netlist,
        board=board, generator=generator, logical_mapping=logical_mapping, native_receipt=native_receipt)
    return _persist_prepared(session, admitted, idempotency_key=idempotency_key, at=at)


class _Parser(argparse.ArgumentParser):
    def error(self, message):
        # argparse's default error echoes operator-supplied values/paths.
        raise RecordedImportError("arguments", "invalid_arguments")


def _configured_engine():
    configured = os.environ.get("VERIFORGE_DATABASE_URL")
    if not configured:
        raise RecordedImportError("configuration", "explicit_database_configuration_required")
    try:
        url = make_url(configured)
        if url.drivername not in {"sqlite", "sqlite+pysqlite", "postgresql+psycopg"}:
            raise RecordedImportError("configuration", "unsupported_database_driver")
        # Refusal of an old/missing SQLite DB must not create an empty file.
        if url.get_backend_name() == "sqlite" and (not url.database or url.database == ":memory:" or
                not Path(url.database).is_file()):
            raise RecordedImportError("configuration", "existing_database_required")
        engine = create_engine(url, pool_pre_ping=True)
        if url.get_backend_name() == "sqlite":
            @event.listens_for(engine, "connect")
            def configure_foreign_keys(connection, _):
                connection.execute("PRAGMA foreign_keys=ON")
        return engine
    except RecordedImportError:
        raise
    except (SQLAlchemyError, ValueError, OSError):
        raise RecordedImportError("configuration", "invalid_database_configuration") from None


def main(argv=None):
    engine = None
    try:
        parser = _Parser(description=__doc__)
        parser.add_argument("--commit", action="store_true")
        parser.add_argument("--idempotency-key")
        parser.add_argument("--case", choices=["u7-corrected-v1"], required=True)
        parser.add_argument("--bundle-root", type=Path, required=True)
        for role in ("xdc", "netlist", "board", "generator", "logical-mapping", "native-receipt"):
            parser.add_argument("--" + role, type=Path, required=True)
        args = vars(parser.parse_args(argv))
        commit, key = args.pop("commit"), args.pop("idempotency_key")
        args.pop("case")
        if commit and (key is None or not 1 <= len(key) <= 128) or not commit and key is not None:
            raise RecordedImportError("arguments", "commit_requires_bounded_key_preparation_accepts_no_key")
        admitted = prepare_import.prepare_corrected_case(**args)
        if not commit:
            result = {"database": "not_opened", "operation": "admission_only", "new_execution": "not_run",
                "project_id": PROJECT_SPEC.project_id, "content_sha256": _digest(admitted),
                "artifacts": len(admitted.artifacts), "total_bytes": admitted.envelope.total_artifact_bytes}
        else:
            engine = _configured_engine()
            sessions = session_factory(engine)
            with sessions.begin() as session:
                result = _persist_prepared(session, admitted, idempotency_key=key, at=datetime.now(timezone.utc))
            # The transaction has actually committed before we emit this label.
            result["database"] = "committed"
        print(json.dumps({"status": "ok", **result}, sort_keys=True))
        return 0
    except prepare_import.PreparationError as exc:
        print(json.dumps({"status": "rejected", **exc.diagnostic}, sort_keys=True))
    except RecordedImportError as exc:
        print(json.dumps({"status": "rejected", "stage": exc.stage, "code": exc.code}, sort_keys=True))
    except SQLAlchemyError:
        # Includes commit failures; do not leak SQL parameters or connection URL.
        print(json.dumps({"status": "rejected", "stage": "storage", "code": "commit_or_connection_failed"}))
    finally:
        if engine is not None:
            engine.dispose()
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
