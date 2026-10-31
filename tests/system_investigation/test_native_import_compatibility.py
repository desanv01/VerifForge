"""Installed producer compatibility is narrow; recorded evidence stays immutable."""
import hashlib
from pathlib import Path

import pytest

from veriforge.system_investigation import native_tool_import as importer


MANIFEST = "474e91456137cc12aecae1b28bd64fae43c8a701381041460bb52282685e3f23"
PAIRS = {
    "src/veriforge/riscv/runner.py": (
        "c392ae910b354b55319036266a45621a930a69eb045ae57774a0c798026499cf",
        "80524f263ebf569f0d5f2fc989f82dbc3993e8e7b2e24490c96563435e0045e5"),
    "src/veriforge/riscv/__main__.py": (
        "90f8727405f2c565fb704d88e483a29f0df1095f247e31f0889ae557816ecb57",
        "439e8c8bfdb54fca6ba271d6c57e685b94b3bf530277b0f69c372af6747c851d"),
}
ROOT = Path(__file__).resolve().parents[2]


@pytest.fixture
def producer_revision(monkeypatch):
    # Isolate portable policy tests from which accepted producer revision is
    # checked out. Real retained-bundle tests separately inspect installed bytes.
    actual_read, actual_sha = importer._read_regular, importer._sha
    markers = {name: ("synthetic installed producer " + name).encode() for name in PAIRS}
    by_path = {(ROOT / name).absolute(): marker for name, marker in markers.items()}
    by_marker = {markers[name]: pair[1] for name, pair in PAIRS.items()}
    monkeypatch.setattr(importer, "_read_regular", lambda path: by_path.get(Path(path).absolute()) or actual_read(path))
    monkeypatch.setattr(importer, "_sha", lambda data: by_marker.get(data) or actual_sha(data))
    return {name: pair[0] for name, pair in PAIRS.items()}


def test_only_exact_reviewed_producer_pairs_are_compatible(producer_revision):
    assert importer._installed(producer_revision, source_manifest_hash=MANIFEST) == importer._VALIDATOR_HASH
    assert importer._INSTALLED_SOURCE_POLICY == "native-import.installed-source.v1"


@pytest.mark.parametrize("manifest", [None, "0" * 64, "fc727ccb8d52b1fb1b76e1dddf95b647954a7f0779b95f31254addc36b131bb3"])
def test_other_or_absent_manifest_cannot_enable_override(producer_revision, manifest):
    with pytest.raises(importer.NativeImportError, match="installed_domain_version_differs"):
        importer._installed(producer_revision, source_manifest_hash=manifest)


@pytest.mark.parametrize("name", list(PAIRS))
def test_other_historical_producer_hash_cannot_enable_override(producer_revision, name):
    producer_revision[name] = "0" * 64
    with pytest.raises(importer.NativeImportError, match="installed_domain_version_differs"):
        importer._installed(producer_revision, source_manifest_hash=MANIFEST)


@pytest.mark.parametrize("name", list(PAIRS))
def test_unreviewed_installed_producer_fails(producer_revision, monkeypatch, name):
    real_read = importer._read_regular
    monkeypatch.setattr(importer, "_read_regular", lambda path:
        b"unreviewed producer change" if Path(path).absolute() == (ROOT / name).absolute() else real_read(path))
    with pytest.raises(importer.NativeImportError, match="installed_domain_version_differs"):
        importer._installed(producer_revision, source_manifest_hash=MANIFEST)


@pytest.mark.parametrize("name", ["checker.py", "profile.py", "contracts.py", "trace.py", "sim_job.py"])
def test_compatibility_never_waives_checker_or_execution_contract(producer_revision, monkeypatch, name):
    relative = "src/veriforge/riscv/" + name
    producer_revision[relative] = hashlib.sha256((ROOT / relative).read_bytes()).hexdigest()
    real_read = importer._read_regular
    monkeypatch.setattr(importer, "_read_regular", lambda path:
        b"changed immutable checker or execution contract" if Path(path).absolute() == (ROOT / relative).absolute() else real_read(path))
    with pytest.raises(importer.NativeImportError, match="installed_domain_version_differs"):
        importer._installed(producer_revision, source_manifest_hash=MANIFEST)


def test_new_saved_replay_origin_and_four_case_format_are_not_admitted(tmp_path):
    assert set(importer._CASES) == {"riscv-40920450439", "firmware-40929665294"}
    assert tuple(case[0] for case in importer._RV_CASES) == ("control", "mutant", "control-replay")
    with pytest.raises(importer.NativeImportError, match="unsupported_family"):
        importer.prepare_native_bundle(family="riscv-saved-replay", bundle_root=tmp_path,
            project_id="project", revision_id="revision")
