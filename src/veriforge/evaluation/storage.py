"""Owned storage primitives for local evaluation journals and captured blobs.

These guards coordinate cooperating local writers and refuse linked paths.
They do not establish directory ACLs, hostile-writer safety or crash recovery.
The ledger retains its original byte encoding and compare-and-append behavior.
"""
from contextlib import contextmanager
import json
import os
from pathlib import Path
import re
import stat


class StorageError(ValueError):
    """Fixed storage diagnostics without private path or file contents."""


def canonical_json(value):
    """Canonical UTF-8 metadata bytes; reject nonfinite JSON numbers."""
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False).encode("utf-8")


def file_identity(info):
    """Stable path/descriptor comparison, including Windows stat semantics."""
    return info.st_dev, info.st_ino, info.st_mode, info.st_size, info.st_mtime_ns


def regular_file(path):
    """Inspect every ancestor and require a single-link regular leaf file."""
    path = Path(path)
    for part in (*reversed(path.parents), path):
        info = part.lstat()
        if stat.S_ISLNK(info.st_mode) or getattr(info, "st_file_attributes", 0) & 0x400:
            raise StorageError("linked_path")
    info = path.stat()
    if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
        raise StorageError("single_regular_file_required")
    return info


class PrivateFileLock:
    """Exclusive sidecar for one bounded filename in an existing private root.

    Acquisition validates directory ancestors; release removes only the owned
    sidecar identity. Existing or replaced locks are preserved. No lock takeover,
    reentrancy, timeout or automatic destructive recovery is implemented.
    """

    def __init__(self, private_root, name):
        reserved = {"CON", "PRN", "AUX", "NUL", *(f"COM{i}" for i in range(1, 10)), *(f"LPT{i}" for i in range(1, 10))}
        if not isinstance(name, str) or len(name) > 128 or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]*\.jsonl", name) or name.split(".")[0].upper() in reserved:
            raise StorageError("invalid_lock_location")
        self.root = Path(private_root).absolute()
        self.path, self.lock_path = self.root/name, self.root/(name+".lock")

    @contextmanager
    def acquire(self):
        for part in (*reversed(self.root.parents), self.root):
            info = part.lstat()
            if not stat.S_ISDIR(info.st_mode) or stat.S_ISLNK(info.st_mode) or getattr(info, "st_file_attributes", 0) & 0x400:
                raise StorageError("private_directory_required")
        try:
            fd = os.open(self.lock_path, os.O_CREAT | os.O_EXCL | os.O_WRONLY | getattr(os, "O_BINARY", 0), 0o600)
        except FileExistsError:
            raise StorageError("writer_locked") from None
        owned = os.fstat(fd)
        try:
            yield
        finally:
            os.close(fd)
            current = self.lock_path.lstat()
            if (current.st_dev, current.st_ino) != (owned.st_dev, owned.st_ino):
                raise StorageError("lock_identity_changed")
            self.lock_path.unlink()
