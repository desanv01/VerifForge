"""Public snapshot and owned journal-lock contracts; synthetic local data only."""
from dataclasses import FrozenInstanceError
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import socket
import sqlite3
import subprocess
from threading import Thread
from types import SimpleNamespace

import pytest
from pydantic import ValidationError

from veriforge.evaluation import EvaluationLog, EvaluationLogError, EvaluationSnapshot
from veriforge.evaluation import harness, records, storage

H = "a"*64


def measurement(name):
    empty = {key: None for key in records.Amounts.model_fields}
    return records.AttemptRecord(record_id=name, occurred_at=datetime(2026, 10, 31, tzinfo=timezone.utc),
        scope=records.Scope(evaluation_revision=H, campaign_id="snapshot-campaign", case_id="case-0001", domain="rtl", profile="fifo-v1",
            strategy="directed_manual", strategy_sha256=H, replicate=0, seed=7, source_sha256=H, checker_sha256=H,
            contract_sha256=H, environment_sha256=H, information_policy_sha256=H, visibility="disclosed_development", cache_state="unknown"),
        evidence_kind="mock", budget=records.Budget(profile_sha256=H, allocated=empty, reserved=empty, consumed=empty),
        timings=records.Timings(**{key: None for key in records.Timings.model_fields}), experiment_record_id="experiment-"+name,
        accepted_plan=dict(artifact_id="plan", path="plan.json", sha256=H, size_bytes=2, media_type="application/json"),
        purpose="detection", attempt=dict(attempt_id="attempt-"+name, experiment_id="experiment-"+name,
            execution_status="completed", verdict="timeout"))


@pytest.fixture(autouse=True)
def no_external_operations(monkeypatch):
    def forbidden(*args, **kwargs): pytest.fail("No external operation authorized")
    monkeypatch.setattr(subprocess, "run", forbidden)
    monkeypatch.setattr(subprocess, "Popen", forbidden)
    monkeypatch.setattr(socket, "create_connection", forbidden)
    monkeypatch.setattr(sqlite3, "connect", forbidden)


@pytest.fixture
def log(tmp_path):
    result = EvaluationLog(tmp_path, "snapshot.jsonl")
    result.create()
    return result


def test_empty_snapshot_is_public_immutable_and_releases_lock(log):
    snapshot = log.snapshot()
    assert isinstance(snapshot, EvaluationSnapshot)
    assert snapshot.entries == () and snapshot.current_head is None and not log.lock.exists()
    with pytest.raises(FrozenInstanceError): snapshot.current_head = "b"*64
    with pytest.raises(FrozenInstanceError): snapshot.entries = ()


def test_snapshot_retains_canonical_entry_bytes_and_known_head_read(log):
    record = measurement("one")
    entry = log.append(record, expected_head=None)
    body = dict(schema_version="evaluation.entry.v1", channel="measurements", sequence=0, previous_sha256=None,
        record=record.model_dump(mode="json"))
    canonical = lambda value: json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False).encode("utf-8")
    expected = canonical({**body, "sha256": hashlib.sha256(canonical(body)).hexdigest()})+b"\n"
    assert log.path.read_bytes() == expected
    snapshot = log.snapshot()
    assert snapshot.entries == log.read(expected_head=entry.sha256) == (entry,)
    assert snapshot.current_head == entry.sha256 and log.path.read_bytes() == expected
    with pytest.raises(ValidationError): snapshot.entries[0].record.record_id = "changed"
    with pytest.raises(TypeError): snapshot.entries[0] = entry


def test_snapshot_is_a_value_not_a_write_lease(log):
    old = log.snapshot()
    first = log.append(measurement("one"), expected_head=old.current_head)
    with pytest.raises(EvaluationLogError, match="head_changed"):
        log.append(measurement("two"), expected_head=old.current_head)
    assert old.entries == () and old.current_head is None
    fresh = log.snapshot()
    assert fresh.current_head == first.sha256
    second = log.append(measurement("two"), expected_head=fresh.current_head)
    assert fresh.entries == (first,) and fresh.current_head == first.sha256
    assert log.snapshot().entries == (first, second)


@pytest.mark.parametrize("damage", ["partial", "record", "previous", "sequence", "noncanonical", "duplicate_key"])
def test_snapshot_validates_complete_chain_and_preserves_corruption(log, damage):
    first = log.append(measurement("one"), expected_head=None)
    log.append(measurement("two"), expected_head=first.sha256)
    lines = log.path.read_bytes().splitlines(keepends=True)
    if damage == "partial": lines[-1] = lines[-1][:-7]
    elif damage == "noncanonical": lines[-1] = b" "+lines[-1]
    elif damage == "duplicate_key": lines[-1] = lines[-1].replace(b"{", b'{"sequence":0,', 1)
    else:
        value = json.loads(lines[-1])
        if damage == "record": value["record"]["record_id"] = "modified"
        elif damage == "previous": value["previous_sha256"] = "b"*64
        else: value["sequence"] = 10
        lines[-1] = storage.canonical_json(value)+b"\n"
    raw = b"".join(lines)
    log.path.write_bytes(raw)
    with pytest.raises(EvaluationLogError): log.snapshot()
    assert log.path.read_bytes() == raw and not log.lock.exists()


def test_snapshot_observes_valid_prefix_but_external_head_detects_removed_suffix(log):
    first = log.append(measurement("one"), expected_head=None)
    last = log.append(measurement("two"), expected_head=first.sha256)
    log.path.write_bytes(log.path.read_bytes().splitlines(keepends=True)[0])
    snapshot = log.snapshot()
    assert snapshot.entries == (first,) and snapshot.current_head == first.sha256
    with pytest.raises(EvaluationLogError, match="head_changed"): log.read(expected_head=last.sha256)


def test_snapshot_acquires_writer_lock_before_opening_ledger(log, monkeypatch):
    real_open = records.os.open
    reads = []
    def guarded(path, flags, *args, **kwargs):
        if Path(path) == log.path:
            assert log.lock.exists()
            reads.append(path)
        return real_open(path, flags, *args, **kwargs)
    monkeypatch.setattr(records.os, "open", guarded)
    assert log.snapshot().entries == () and len(reads) == 1 and not log.lock.exists()


def test_snapshot_does_not_take_over_existing_writer_lock(log):
    log.lock.write_bytes(b"other writer")
    with pytest.raises(EvaluationLogError, match="writer_locked"): log.snapshot()
    assert log.lock.read_bytes() == b"other writer" and log.path.read_bytes() == b""


def test_snapshot_refuses_observed_mutation_during_open(log, monkeypatch):
    log.append(measurement("one"), expected_head=None)
    real_open = records.os.open
    def changed(path, flags, *args, **kwargs):
        if Path(path) == log.path:
            with log.path.open("ab") as stream: stream.write(b" ")
        return real_open(path, flags, *args, **kwargs)
    monkeypatch.setattr(records.os, "open", changed)
    with pytest.raises(EvaluationLogError, match="file_identity_changed"): log.snapshot()
    assert log.path.read_bytes().endswith(b" ")


def test_snapshot_respects_read_size_bound(log, monkeypatch):
    log.path.write_bytes(b"too large")
    monkeypatch.setattr(records, "MAX_LOG_BYTES", 1)
    with pytest.raises(EvaluationLogError, match="log_size_limit"): log.snapshot()
    assert log.path.read_bytes() == b"too large"


def test_canonical_storage_bytes_match_the_existing_ledger_convention():
    assert storage.canonical_json({"z": 0, "a": "µ"}) == b'{"a":"\xc2\xb5","z":0}'
    with pytest.raises(ValueError): storage.canonical_json({"bad": float("nan")})


@pytest.mark.parametrize("name", ["../bad.jsonl", "a/b.jsonl", "a\\b.jsonl", "file:ads.jsonl", "NUL.jsonl", "COM1.extra.jsonl", "x"*129, "plain.txt"])
def test_owned_journal_lock_has_bounded_filename_interface(tmp_path, name):
    with pytest.raises(storage.StorageError): storage.PrivateFileLock(tmp_path, name)
    assert list(tmp_path.iterdir()) == []


def test_owned_journal_session_is_scoped_and_typed(tmp_path):
    journal = harness.Journal(tmp_path)
    journal.create()
    assert journal.read() == ((), None)
    assert not hasattr(journal, "append")
    with journal.locked() as session:
        assert session.read() == ((), None)
        invalid = records.ModelUsage(state="unknown", input_tokens=None, output_tokens=None, billed_usd=None)
        with pytest.raises(harness.HarnessError, match="journal_event_invalid"): session.append(invalid)
        assert journal.path.read_bytes() == b""
    for operation in (session.read, session.create, lambda: session.append(invalid)):
        with pytest.raises(harness.HarnessError, match="journal_session_inactive"): operation()
    assert not journal.lock_path.exists() and journal.path.read_bytes() == b""


def test_session_refuses_cross_thread_use_and_second_reader_cannot_reenter(tmp_path):
    journal = harness.Journal(tmp_path)
    journal.create()
    failures = []
    with journal.locked() as session:
        def other_thread():
            try: session.read()
            except harness.HarnessError as error: failures.append(str(error))
            try: journal.read()
            except storage.StorageError as error: failures.append(str(error))
        worker = Thread(target=other_thread)
        worker.start()
        worker.join(timeout=2)
        assert not worker.is_alive()
    assert failures == ["journal_session_inactive", "writer_locked"]
    assert journal.read() == ((), None)


def test_lock_replacement_identity_failure_preserves_sidecar(tmp_path, monkeypatch):
    lock = storage.PrivateFileLock(tmp_path, "owned.jsonl")
    real_stat = Path.lstat
    replaced = False
    def observed(path, *args, **kwargs):
        info = real_stat(path, *args, **kwargs)
        if Path(path) == lock.lock_path and replaced:
            return SimpleNamespace(st_dev=info.st_dev, st_ino=info.st_ino+1)
        return info
    monkeypatch.setattr(Path, "lstat", observed)
    with pytest.raises(storage.StorageError, match="lock_identity_changed"):
        with lock.acquire(): replaced = True
    assert lock.lock_path.exists()


@pytest.mark.parametrize("kind", ["hardlink", "symlink"])
def test_owned_storage_refuses_linked_files_when_fixture_available(tmp_path, kind):
    source, linked = tmp_path/"original.bin", tmp_path/"linked.bin"
    source.write_bytes(b"synthetic")
    try:
        if kind == "hardlink": linked.hardlink_to(source)
        else: linked.symlink_to(source)
    except OSError:
        pytest.skip("Host cannot create the link fixture")
    with pytest.raises(storage.StorageError, match="linked_path|single_regular_file"):
        storage.regular_file(linked)
    assert source.read_bytes() == b"synthetic"
