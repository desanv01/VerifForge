"""Bounded, uncached canonical control for explicitly configured service roles."""
from dataclasses import dataclass
import json
import os
from pathlib import Path
import stat


def _unique(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("Duplicate control key")
        result[key] = value
    return result


def _constant(value):
    raise ValueError("Invalid control constant")


@dataclass(frozen=True)
class ControlReader:
    path: Path
    max_bytes: int = 65_536

    def __post_init__(self):
        if type(self.max_bytes) is not int or not 1 <= self.max_bytes <= 65_536:
            raise ValueError("Invalid control byte bound")
        object.__setattr__(self, "path", Path(self.path))

    def __call__(self) -> str:
        descriptor = None
        try:
            target = self.path.stat()
            if not stat.S_ISREG(target.st_mode) or target.st_size > self.max_bytes:
                return "STOPPED"
            # Nonblocking open prevents a regular-file -> FIFO replacement race
            # from hanging on POSIX. Reject symlink/device targets where the OS
            # exposes that flag; verify the descriptor before any read on all OSes.
            flags = os.O_RDONLY | getattr(os, "O_NONBLOCK", 0) | getattr(os, "O_BINARY", 0)
            flags |= getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_CLOEXEC", 0)
            descriptor = os.open(self.path, flags)
            before = os.fstat(descriptor)
            if not stat.S_ISREG(before.st_mode) or before.st_size > self.max_bytes:
                return "STOPPED"
            raw = os.read(descriptor, self.max_bytes + 1)
            after = os.fstat(descriptor)
            # Atomic replacement is observed on each new open. In-place writes
            # or replacement during this read fail closed rather than cache old
            # RUNNING authorization; next poll opens the current canonical file.
            current = self.path.stat()
            identity = lambda value: (value.st_dev, value.st_ino, value.st_mode, value.st_size, value.st_mtime_ns)
            if (len(raw) != before.st_size or len(raw) > self.max_bytes or not stat.S_ISREG(after.st_mode) or
                    not stat.S_ISREG(current.st_mode) or identity(target) != identity(before) or
                    identity(before) != identity(after) or identity(after) != identity(current)):
                return "STOPPED"
            value = json.loads(raw.decode("utf-8"), object_pairs_hook=_unique, parse_constant=_constant)
            return "RUNNING" if type(value) is dict and value.get("control") == "RUNNING" else "STOPPED"
        except (OSError, ValueError, TypeError, AttributeError, RecursionError):
            return "STOPPED"
        finally:
            if descriptor is not None:
                try:
                    os.close(descriptor)
                except OSError:
                    return "STOPPED"
