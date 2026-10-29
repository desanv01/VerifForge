"""Independent malicious synthetic preparation tests; no tools/DB/input execution."""
from dataclasses import replace
import hashlib
import json
from pathlib import Path
import runpy
import stat
from types import SimpleNamespace

import pytest

from veriforge.system_investigation import prepare_import as preparation, records

ROOT = Path(__file__).resolve().parents[2]
BUILDERS = runpy.run_path(str(ROOT / 'tests/system_investigation/test_recorded_import_preparation.py'))


@pytest.fixture
def authored(tmp_path):
    # Reuse only native synthetic-byte construction, not owner test assertions.
    return BUILDERS['authored'].__wrapped__(tmp_path)


def test_complete_graph_keeps_original_ids_ancestry_and_cannot_execute_or_write_db(authored, monkeypatch):
    import sqlite3
    import subprocess
    case, root, paths, original, files, raw = authored
    before = {path: path.read_bytes() for path in [*root.rglob('*.json'), *paths.values()]}
    def forbidden(*args, **kwargs):
        pytest.fail('Preparation attempted a process or database operation')
    monkeypatch.setattr(subprocess, 'Popen', forbidden)
    monkeypatch.setattr(sqlite3, 'connect', forbidden)
    admitted = preparation._prepare(case, root, paths)
    assert len(admitted.artifacts) == 52
    assert admitted.attempt_results == () and admitted.envelope.record.attempts == ()
    assert admitted.bridge_report == original and admitted.manifest == original.manifest
    assert len(admitted.manifest.facts) == 33
    assert all(rel.basis == 'hypothesis' and rel.dependence == 'same_source' and rel.verdict == 'inconclusive' for rel in admitted.manifest.relations)
    assert all(f.review_state == 'unreviewed' for f in admitted.manifest.facts if f.category == 'observation')
    assert [f.fact_id for f in admitted.manifest.facts] == [f.fact_id for f in original.manifest.facts]
    for artifact in admitted.artifacts:
        assert hashlib.sha256(artifact.data).hexdigest() == artifact.reference.sha256
    assert before == {path: path.read_bytes() for path in before}
    assert next(a.data for a in admitted.artifacts if a.reference.artifact_id == 'carrier-generator') == raw['generator']
    assert b'NEVER_EXECUTE_SYNTHETIC_INPUT' not in records.serialize_investigation(admitted)


def test_public_case_authority_cannot_be_replaced_by_synthetic_pins(authored):
    case, root, paths, *_ = authored
    with pytest.raises(preparation.PreparationError, match='raw_sha256_mismatch'):
        preparation.prepare_corrected_case(bundle_root=root, **paths)
    assert case.report_hash != preparation._CORRECTED.report_hash


@pytest.mark.parametrize('part', ['native', 'comparison', 'receipt'])
def test_truncated_or_missing_required_bytes_never_yield_partial_admission(authored, part):
    case, root, paths, *_ = authored
    path = paths['native_receipt'] if part == 'receipt' else next((root / ('native' if part == 'native' else 'comparisons')).glob('*.json'))
    original = path.read_bytes()
    path.write_bytes(original[:len(original) // 2])
    with pytest.raises(preparation.PreparationError) as caught:
        preparation._prepare(case, root, paths)
    assert caught.value.diagnostic['reason'] == 'raw_sha256_mismatch'
    assert str(path) not in str(caught.value)
    path.unlink()
    with pytest.raises(preparation.PreparationError, match='missing_or_unreadable_file'):
        preparation._prepare(case, root, paths)


def test_duplicate_json_keys_rejected_after_private_case_rehash(authored):
    case, root, paths, *_ = authored
    original = (root / 'report.json').read_bytes()
    malicious = b'{"execution_status":"not_run",' + original[1:]
    assert json.loads(malicious)['execution_status'] == 'not_run'
    (root / 'report.json').write_bytes(malicious)
    with pytest.raises(preparation.PreparationError, match='duplicate JSON key'):
        preparation._prepare(replace(case, report_hash=hashlib.sha256(malicious).hexdigest()), root, paths)


@pytest.mark.parametrize('bound,value', [('MAX_TOTAL_BYTES', 1), ('MAX_ARTIFACTS', 2)])
def test_aggregate_bounds_fail_before_returning_an_admission(authored, monkeypatch, bound, value):
    case, root, paths, *_ = authored
    monkeypatch.setattr(records, bound, value)
    with pytest.raises(preparation.PreparationError, match='aggregate_byte_or_count_limit'):
        preparation._prepare(case, root, paths)


@pytest.mark.parametrize('name', ['../outside', '/absolute', 'nested/file', 'nested\\file', 'C:drive', '..'])
def test_manifest_names_cannot_escape_bundle_root(tmp_path, name):
    with pytest.raises(preparation.PreparationError, match='unsafe_artifact_filename'):
        preparation._bundle_path(tmp_path, 'native', name)


@pytest.mark.parametrize('kind', ['symlink', 'reparse'])
def test_ancestor_link_reparse_detection_precedes_file_open(tmp_path, monkeypatch, kind):
    directory = tmp_path / 'ancestor'
    directory.mkdir()
    path = directory / 'private-input'
    path.write_bytes(b'fixed bytes')
    original = Path.lstat
    def altered(current, *args, **kwargs):
        if current == directory:
            return SimpleNamespace(st_mode=stat.S_IFLNK if kind == 'symlink' else stat.S_IFDIR,
                st_file_attributes=0x400 if kind == 'reparse' else 0)
        return original(current, *args, **kwargs)
    def forbidden(*args, **kwargs):
        pytest.fail('Linked ancestor reached file open')
    monkeypatch.setattr(Path, 'lstat', altered)
    monkeypatch.setattr(Path, 'open', forbidden)
    with pytest.raises(preparation.PreparationError, match='link_or_reparse_point'):
        preparation._read(path, 'logical-input', hashlib.sha256(b'fixed bytes').hexdigest())


def test_directory_and_oversized_inputs_fail_with_safe_labels(tmp_path):
    with pytest.raises(preparation.PreparationError, match='regular_file_required'):
        preparation._read(tmp_path, 'logical-input', 'a' * 64)
    path = tmp_path / 'PRIVATE_PATH_MARKER'
    path.write_bytes(b'x' * (records.MAX_ARTIFACT_BYTES + 1))
    with pytest.raises(preparation.PreparationError) as caught:
        preparation._read(path, 'logical-input', 'a' * 64)
    assert caught.value.diagnostic == {'stage': 'input', 'item': 'logical-input', 'reason': 'artifact_size_limit'}
    assert 'PRIVATE_PATH_MARKER' not in str(caught.value)
