from pathlib import Path

import pytest

from veriforge.vivado.storage import (
    CACHE_CAP, ENTRY_CAP, FILE_CAP, RESULT_CAP, StorageTraversalLimit, StorageUsage, measure_storage,
)
from veriforge.vivado import storage


def test_only_fixed_cache_prefix_is_excluded_from_results(tmp_path):
    paths = {"2026.1/XilinxTclStore/tool.tcl": b"12345", "2026.1/other.bin": b"12",
             "home/vendor-cache.bin": b"123", "stdout.log": b"1234",
             "2026.1/XilinxTclStore-lookalike/item": b"123456"}
    for name, data in paths.items():
        path = tmp_path / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
    usage = measure_storage(tmp_path)
    assert usage.cache_bytes == 5 and usage.result_bytes == 15
    assert usage.files == 5 and usage.total_bytes == 20 and usage.limit_reason is None


@pytest.mark.parametrize("usage,reason", [
    (StorageUsage(CACHE_CAP + 1, 0, 1), "initialization_cache_limit"),
    (StorageUsage(0, RESULT_CAP + 1, 1), "result_directory_limit"),
    (StorageUsage(0, 0, FILE_CAP + 1), "file_count_limit"),
    (StorageUsage(CACHE_CAP, RESULT_CAP, FILE_CAP), None),
])
def test_separate_limits_fail_closed(usage, reason):
    assert usage.limit_reason == reason


def test_file_count_scan_is_bounded(tmp_path, monkeypatch):
    path = tmp_path / "item"
    path.write_bytes(b"x")
    # Exercise the independent regular-file bound without the traversal gate.
    monkeypatch.setattr(storage, "ENTRY_CAP", FILE_CAP + 1)
    monkeypatch.setattr(Path, "rglob", lambda self, pattern: (path for _ in range(FILE_CAP + 100)))
    usage = measure_storage(tmp_path)
    assert usage.files == FILE_CAP + 1
    assert usage.result_bytes == FILE_CAP and usage.limit_reason == "file_count_limit"


def test_non_directory_root_is_rejected(tmp_path):
    path = tmp_path / "regular-file"
    path.write_bytes(b"not a directory")
    with pytest.raises(ValueError, match="directory"):
        measure_storage(path)


def test_directory_entries_have_separate_finite_budget(tmp_path, monkeypatch):
    directory = tmp_path / "empty-dir"
    directory.mkdir()
    visits = []
    def entries(root, pattern):
        for index in range(ENTRY_CAP + 1):
            visits.append(index)
            yield directory
        pytest.fail("scan continued beyond entry budget")
    monkeypatch.setattr(Path, "rglob", entries)
    with pytest.raises(StorageTraversalLimit, match="entry_scan_limit") as failure:
        measure_storage(tmp_path)
    assert failure.value.reason == "entry_scan_limit" and len(visits) == ENTRY_CAP + 1


def test_entry_budget_boundary_is_inclusive_without_inventing_file_bytes(tmp_path, monkeypatch):
    directory = tmp_path / "empty-dir"
    directory.mkdir()
    monkeypatch.setattr(Path, "rglob", lambda root, pattern: (directory for _ in range(ENTRY_CAP)))
    result = measure_storage(tmp_path)
    assert result == StorageUsage(0, 0, 0) and result.limit_reason is None


def test_mixed_directory_and_file_entries_both_consume_scan_budget(tmp_path, monkeypatch):
    directory = tmp_path / "empty-dir"
    directory.mkdir()
    file = tmp_path / "file"
    file.write_bytes(b"123")
    monkeypatch.setattr(storage, "ENTRY_CAP", 2)
    monkeypatch.setattr(Path, "rglob", lambda root, pattern: iter((directory, file, directory)))
    with pytest.raises(StorageTraversalLimit, match="entry_scan_limit"):
        measure_storage(tmp_path)
