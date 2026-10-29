"""Separate initialization-cache and evidence accounting for bounded slot runners."""
from dataclasses import dataclass
from pathlib import Path

CACHE_RELATIVE = Path("2026.1/XilinxTclStore")
CACHE_CAP = 60 * 1024 * 1024
RESULT_CAP = 4 * 1024 * 1024
TOTAL_CAP = 64 * 1024 * 1024
FILE_CAP = 4096
ENTRY_CAP = 4096  # Separate traversal budget, including directories and links.


class StorageTraversalLimit(ValueError):
    """Incomplete accounting because the metadata traversal budget was exceeded."""

    reason = "entry_scan_limit"


@dataclass(frozen=True)
class StorageUsage:
    cache_bytes: int
    result_bytes: int
    files: int

    @property
    def total_bytes(self) -> int:
        return self.cache_bytes + self.result_bytes

    @property
    def limit_reason(self) -> str | None:
        if self.files > FILE_CAP:
            return "file_count_limit"
        if self.cache_bytes > CACHE_CAP:
            return "initialization_cache_limit"
        if self.result_bytes > RESULT_CAP:
            return "result_directory_limit"
        if self.total_bytes > TOTAL_CAP:
            return "total_directory_limit"
        return None


def measure_storage(attempt: Path) -> StorageUsage:
    """Classify only the observed fixed vendor cache prefix; everything else costs results.

    No file contents are read. Links/junctions are rejected. At most ENTRY_CAP
    yielded entries receive metadata inspection; the next entry raises an
    explicit traversal exception rather than returning incomplete byte counts.
    FILE_CAP separately counts only regular files. Polling is not an atomic quota.
    """
    root = Path(attempt).resolve(strict=True)
    if not root.is_dir():
        raise ValueError("attempt must be a directory")
    cache = result = files = entries = 0
    for path in root.rglob("*"):
        entries += 1
        if entries > ENTRY_CAP:
            raise StorageTraversalLimit("entry_scan_limit: storage accounting incomplete")
        if path.is_symlink() or path.is_junction():
            raise ValueError("linked path is not an owned isolated output")
        try:
            if not path.is_file():
                continue
            size = path.stat().st_size
        except FileNotFoundError:
            continue  # Vendor-owned temporary file disappeared between samples.
        files += 1
        if files > FILE_CAP:
            break
        relative = path.relative_to(root)
        if relative.is_relative_to(CACHE_RELATIVE):
            cache += size
        else:
            result += size
    return StorageUsage(cache, result, files)
