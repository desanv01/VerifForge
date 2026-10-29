"""Check the published subset itself; this never reruns engineering tools."""
import hashlib
import json
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]


def verify_record(base, relative, size, digest):
    path = base / relative
    assert not Path(relative).is_absolute()
    assert path.resolve().is_relative_to(base.resolve())
    raw = path.read_bytes()
    assert len(raw) == size, relative
    assert hashlib.sha256(raw).hexdigest() == digest, relative


@pytest.mark.parametrize("domain", ["riscv", "firmware"])
def test_published_native_raw_bytes(domain):
    base = ROOT / "docs/evidence" / domain / "2026-10-30"
    manifest = json.loads((base / "identity-manifest.json").read_bytes())
    records = manifest["file_records"]
    assert records
    assert len({r["path"] for r in records}) == len(records)
    for record in records:
        assert record["byte_preserved"] is True
        verify_record(base, record["path"], record["bytes"], record["sha256"])


def test_published_kicad_selected_source_and_evidence_bytes():
    manifest = json.loads((ROOT / "docs/evidence/kicad/2026-10-30/identity-manifest.json").read_bytes())
    assert manifest["schema_version"] == "veriforge.derived-kicad-provenance.v2"
    records = manifest["selected_publication_files"]
    assert len(records) == 65
    assert len({r["repository_path"] for r in records}) == len(records)
    for record in records:
        verify_record(ROOT, record["repository_path"], record["bytes"], record["sha256"])
    projections = manifest["public_projection_files"]
    assert {r["repository_path"] for r in projections} == {
        "docs/evidence/kicad/2026-10-30/README.md",
        "docs/evidence/kicad/2026-10-30/identity-manifest.json",
    }
    for record in projections:
        if record["repository_path"].endswith("/identity-manifest.json"):
            assert record["bytes"] is None and record["sha256"] is None
        else:
            verify_record(ROOT, record["repository_path"], record["bytes"], record["sha256"])


def test_evidence_paths_disable_git_line_ending_conversion():
    # These scoped attributes protect Windows checkouts and git archives too.
    attributes = (ROOT / ".gitattributes").read_text().splitlines()
    for rule in ("docs/evidence/** -text", "targets/kicad/** -text", "docs/project/KICAD.md -text"):
        assert rule in attributes
