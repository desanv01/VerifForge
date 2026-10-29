"""Restore receipt checker and synthetic checker tests; never invokes Docker."""
import argparse
import hashlib
import json
from pathlib import Path

import pytest

SCHEMA_COLUMNS = {
    "alembic_version": "version_num",
    "contracts": "id data identity",
    "projects": "id owner data contract_id",
    "campaigns": "id owner project_id contract_id create_key request_hash plan identity execution_status verdict version event_sequence created_at updated_at",
    "attempts": "id campaign_id lease execution_status verdict result heartbeat_at created_at finished_at",
    "run_events": "id campaign_id sequence kind payload created_at",
    "dispatches": "attempt_id sent_at error",
    "findings": "id campaign_id attempt_id data",
}
EXPECTED_FOREIGN_KEYS = {"projects": 1, "campaigns": 2, "attempts": 1, "run_events": 1, "dispatches": 1, "findings": 2}
EXPECTED_UNIQUES = {"campaigns": 1, "run_events": 1, "findings": 1}


def require(condition, message):
    if not condition:
        raise ValueError(message)


def row_fingerprint(table, rows):
    key = "attempt_id" if table == "dispatches" else "id"
    ordered = sorted(rows, key=lambda row: row[key])
    require(len({row[key] for row in ordered}) == len(ordered), "Duplicate restored row key")
    data = json.dumps(ordered, sort_keys=True, separators=(",", ":"), default=str).encode()
    return {"count": len(ordered), "sha256": hashlib.sha256(data).hexdigest()}


def compare_restored(snapshot, expected):
    """Compare canonical rows to VF024 pre-upgrade receipt, schema to c67 source."""
    require(expected["revision"] == snapshot["revision"] == "c67f9c9f19e5", "Wrong restored migration revision")
    require(set(snapshot["tables"]) == set(expected["tables"]) == set(SCHEMA_COLUMNS) - {"alembic_version"}, "Wrong restored table set")
    require(set(snapshot["columns"]) == set(SCHEMA_COLUMNS), "Unexpected/missing public schema table")
    for table, names in SCHEMA_COLUMNS.items():
        require(sorted(snapshot["columns"][table]) == sorted(names.split()), f"Column mismatch: {table}")
        constraints = snapshot["constraints"].get(table, {})
        require(constraints.get("PRIMARY KEY", 0) == 1, f"Missing primary key: {table}")
        require(constraints.get("FOREIGN KEY", 0) == EXPECTED_FOREIGN_KEYS.get(table, 0), f"Foreign key count: {table}")
        require(constraints.get("UNIQUE", 0) == EXPECTED_UNIQUES.get(table, 0), f"Unique count: {table}")
    require(snapshot["unvalidated_constraints"] == 0, "Unvalidated restored constraints")
    actual = {table: row_fingerprint(table, rows) for table, rows in snapshot["tables"].items()}
    require(actual == expected["tables"], "Restored row count/content fingerprint differs")
    return {"status": "isolated_database_restore_matches_pre_upgrade", "revision": snapshot["revision"], "tables": actual,
            "schema_check": "initial migration column sets and primary/foreign/unique counts; no physical schema identity claim",
            "scope": "database only; no API/worker/broker restart or artifact restore proved"}


def synthetic_snapshot():
    """Explicit synthetic records exercise the checker only, not PostgreSQL."""
    tables = {table: [] for table in SCHEMA_COLUMNS if table != "alembic_version"}
    tables["campaigns"] = [{"id": "synthetic-campaign", "plan": {"seed": 17}}]
    snapshot = {"revision": "c67f9c9f19e5", "tables": tables,
                "columns": {table: names.split() for table, names in SCHEMA_COLUMNS.items()},
                "constraints": {table: {"PRIMARY KEY": 1, "FOREIGN KEY": EXPECTED_FOREIGN_KEYS.get(table, 0), "UNIQUE": EXPECTED_UNIQUES.get(table, 0)} for table in SCHEMA_COLUMNS},
                "unvalidated_constraints": 0}
    expected = {"revision": snapshot["revision"], "tables": {t: row_fingerprint(t, rows) for t, rows in tables.items()}}
    return snapshot, expected


def test_restore_checker_accepts_matching_synthetic_receipt():
    snapshot, expected = synthetic_snapshot()
    assert compare_restored(snapshot, expected)["status"] == "isolated_database_restore_matches_pre_upgrade"


@pytest.mark.parametrize("change", ["row", "revision", "column", "foreign_key", "extra_table", "unvalidated"])
def test_restore_checker_rejects_wrong_data_or_schema(change):
    snapshot, expected = synthetic_snapshot()
    if change == "row":
        snapshot["tables"]["campaigns"][0]["plan"]["seed"] = 18
    elif change == "revision":
        snapshot["revision"] = "6bf022evidence"
    elif change == "column":
        snapshot["columns"]["campaigns"].remove("identity")
    elif change == "foreign_key":
        snapshot["constraints"]["projects"]["FOREIGN KEY"] = 0
    elif change == "extra_table":
        snapshot["columns"]["evidence_exports"] = ["id"]
    else:
        snapshot["unvalidated_constraints"] = 1
    with pytest.raises(ValueError):
        compare_restored(snapshot, expected)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Compare isolated PostgreSQL JSON snapshot; no Docker or network operations")
    parser.add_argument("--snapshot", type=Path, required=True)
    parser.add_argument("--expected", type=Path, required=True)
    parser.add_argument("--receipt", type=Path, required=True)
    args = parser.parse_args()
    for path in (args.snapshot, args.expected):
        if path.stat().st_size > 20_971_520:
            raise ValueError("Restore comparison input exceeds 20MiB bound")
    result = compare_restored(json.loads(args.snapshot.read_text(encoding="utf-8-sig")), json.loads(args.expected.read_text(encoding="utf-8-sig")))
    args.receipt.write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"status": result["status"], "tables_verified": len(result["tables"])}))
