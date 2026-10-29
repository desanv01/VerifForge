"""Authored synthetic CI bytes; optional recorded evidence never runs a tool."""
from dataclasses import replace
import hashlib
import json
import os
from pathlib import Path

import pytest

from veriforge.contracts.engineering import EvidenceReference
from veriforge.system_investigation import (BoardIntent, PinBinding, RootArtifact,
    SourceArtifact, bundle_files, compare_board)
from veriforge.system_investigation import prepare_import as preparation
from veriforge.system_investigation.records import serialize_investigation


def sha(raw):
    return hashlib.sha256(raw).hexdigest()


@pytest.fixture
def authored(tmp_path):
    """Eight pins and one common generator, never produced by KiCad/Vivado."""
    raw = {
        "xdc": "".join(f'# set_property PACKAGE_PIN A{i} [get_ports {{ja[{i-1}]}}]\n'
                        for i in range(1, 9)).encode(),
        "netlist": ('<export version="E"><design><source>synthetic</source></design>'
            '<components><comp ref="J1"><value>SYNTHETIC</value><footprint>Test:Eight</footprint>'
            '</comp></components><nets>' + ''.join(
                f'<net code="{i}" name="N{i}"><node ref="J1" pin="{i}"/></net>'
                for i in range(1, 9)) + '</nets></export>').encode(),
        "board": ('(kicad_pcb (version 20241229) (generator "synthetic") (net 0 "") ' +
            ''.join(f'(net {i} "N{i}") ' for i in range(1, 9)) +
            '(footprint "Test:Eight" (property "Reference" "J1") ' +
            '(property "Value" "SYNTHETIC") ' + ''.join(
                f'(pad "{i}" thru_hole circle (net {i} "N{i}")) ' for i in range(1, 9)) + '))').encode(),
        "generator": b'raise RuntimeError("NEVER_EXECUTE_SYNTHETIC_INPUT")\n',
        "logical_mapping": b'{"scope":"synthetic logical intent, no physical review"}',
        "native_receipt": b'{"scope":"synthetic receipt, not native tool execution"}',
    }
    ids = {"xdc": "official-master-xdc", "netlist": "carrier-netlist", "board": "carrier-board",
           "generator": "carrier-generator", "logical_mapping": "u7-logical-mapping"}
    refs = {role: EvidenceReference(artifact_id=name, sha256=sha(raw[role]), revision_id="synthetic-v1")
            for role, name in ids.items()}
    parent = RootArtifact(reference=refs["generator"], data=raw["generator"], origin="synthetic_fixture")
    sources = [SourceArtifact(reference=refs[role], data=raw[role], origin="synthetic_fixture",
                parents=(parent,) if role != "xdc" else ()) for role in ("xdc", "netlist", "board")]
    intent = BoardIntent(reviewed_by="synthetic-logical-intent-only", review_evidence=refs["logical_mapping"],
        bindings=tuple(PinBinding(binding_id=f"J1-{i}", port=f"ja[{i-1}]", package_pin=f"A{i}",
            reference="J1", symbol_pin=str(i), footprint_pad=str(i), schematic_net=f"N{i}",
            board_net=f"N{i}") for i in range(1, 9)))
    report = compare_board(*sources, intent, board_reference=True)
    files = bundle_files(report)
    root = tmp_path / "bundle"
    for name, data in files.items():
        path = root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
    paths = {}
    for role, data in raw.items():
        path = tmp_path / (role + ".source")
        path.write_bytes(data)
        paths[role] = path
    case = preparation._Case("authored-synthetic-case", sha(files["report.json"]), sha(files["manifest.json"]),
        sha(files["intent.json"]), report.checker_hash,
        tuple((role, ids[role], sha(raw[role])) for role in ids), sha(raw["native_receipt"]))
    return case, root, paths, report, files, raw


def test_complete_preparation_preserves_exact_bytes_ids_ancestry_and_is_deterministic(authored):
    case, root, paths, report, files, raw = authored
    before = {p: p.read_bytes() for p in root.rglob("*") if p.is_file()}
    admitted = preparation._prepare(case, root, paths)
    again = preparation._prepare(case, root, paths)
    assert serialize_investigation(admitted) == serialize_investigation(again)
    assert admitted.bridge_report == report and admitted.manifest == report.manifest
    assert len(admitted.artifacts) == 52
    assert admitted.envelope.record.project_id == "engineering-u7-carrier"
    assert admitted.envelope.record.attempts == () and admitted.attempt_results == ()
    assert {r.verdict for r in admitted.manifest.relations} == {"inconclusive"}
    assert {r.value_comparison for r in admitted.bridge_report.comparisons} == {"consistent"}
    by_id = {a.reference.artifact_id: a for a in admitted.artifacts}
    assert by_id["recorded-bridge-report"].data == files["report.json"]
    assert by_id["recorded-manifest"].data == files["manifest.json"]
    assert by_id["historical-native-receipt"].data == raw["native_receipt"]
    assert by_id["carrier-generator"].data == raw["generator"]
    assert before == {p: p.read_bytes() for p in root.rglob("*") if p.is_file()}
    envelope = serialize_investigation(admitted)
    assert str(root).encode() not in envelope and b"NEVER_EXECUTE_SYNTHETIC_INPUT" not in envelope


def test_public_entry_point_cannot_replace_fixed_reviewed_case_with_authored_report(authored):
    _, root, paths, *_ = authored
    with pytest.raises(preparation.PreparationError, match="report.json: raw_sha256_mismatch"):
        preparation.prepare_corrected_case(bundle_root=root, **paths)


@pytest.mark.parametrize("part", ["report", "manifest", "intent", "source", "native", "comparison", "receipt"])
@pytest.mark.parametrize("damage", ["missing", "changed"])
def test_missing_or_changed_required_inputs_are_identified_and_not_dropped(authored, part, damage):
    case, root, paths, _, files, _ = authored
    path = {"report": root / "report.json", "manifest": root / "manifest.json", "intent": root / "intent.json",
            "source": paths["board"], "native": next(root.glob("native/*.json")),
            "comparison": next(root.glob("comparisons/*.json")), "receipt": paths["native_receipt"]}[part]
    if damage == "missing":
        path.unlink()
    else:
        path.write_bytes(path.read_bytes() + b" ")
    with pytest.raises(preparation.PreparationError) as caught:
        preparation._prepare(case, root, paths)
    diagnostic = caught.value.diagnostic
    assert diagnostic["stage"] == "input"
    assert diagnostic["reason"] == ("missing_or_unreadable_file" if damage == "missing" else "raw_sha256_mismatch")
    assert diagnostic["item"] and str(path) not in str(caught.value)


def test_linked_bundle_or_external_source_is_rejected(authored, tmp_path):
    case, root, paths, *_ = authored
    linked = tmp_path / "linked"
    try:
        linked.symlink_to(root, target_is_directory=True)
    except OSError:
        pytest.skip("OS does not permit this test user to create symlinks")
    with pytest.raises(preparation.PreparationError, match="link_or_reparse_point"):
        preparation._prepare(case, linked, paths)
    source_link = tmp_path / "linked-source"
    source_link.symlink_to(paths["board"])
    with pytest.raises(preparation.PreparationError, match="link_or_reparse_point"):
        preparation._prepare(case, root, {**paths, "board": source_link})


def test_oversized_input_and_pathlike_native_names_rejected_before_read(authored):
    case, root, paths, *_ = authored
    paths["board"].write_bytes(b"x" * (preparation.records.MAX_ARTIFACT_BYTES + 1))
    with pytest.raises(preparation.PreparationError, match="board: artifact_size_limit"):
        preparation._prepare(case, root, paths)
    for unsafe in ("../outside", "a/b", "a\\b", "/absolute", "C:drive"):
        with pytest.raises(preparation.PreparationError, match="unsafe_artifact_filename"):
            preparation._bundle_path(root, "native", unsafe)


def test_typed_failure_reports_contract_field_without_raw_input(authored):
    case, root, paths, _, files, _ = authored
    report = json.loads(files["report.json"])
    report["execution_status"] = "PRIVATE_INVALID_VALUE"
    altered = json.dumps(report).encode()
    (root / "report.json").write_bytes(altered)
    with pytest.raises(preparation.PreparationError) as caught:
        preparation._prepare(replace(case, report_hash=sha(altered)), root, paths)
    assert caught.value.diagnostic["stage"] == "typed_contract"
    assert caught.value.diagnostic["reason"][0]["field"] == "execution_status"
    assert "PRIVATE_INVALID_VALUE" not in str(caught.value)


def test_rehashed_case_cannot_drop_fact_and_claim_complete_closure(authored):
    case, root, paths, _, files, _ = authored
    report = json.loads(files["report.json"])
    report["native_records"].pop()
    altered = json.dumps(report).encode()
    (root / "report.json").write_bytes(altered)
    with pytest.raises(preparation.PreparationError, match="typed_contract"):
        preparation._prepare(replace(case, report_hash=sha(altered)), root, paths)


def test_extra_files_are_never_executed_or_admitted(authored):
    case, root, paths, *_ = authored
    before = serialize_investigation(preparation._prepare(case, root, paths))
    (root / "payload.py").write_bytes(b'raise RuntimeError("DO_NOT_EXECUTE")')
    assert serialize_investigation(preparation._prepare(case, root, paths)) == before


def test_cli_rejects_without_private_paths_or_source_content(authored, capsys):
    _, root, paths, *_ = authored
    args = ["--case", "u7-corrected-v1", "--bundle-root", str(root)]
    for role, path in paths.items():
        args += ["--" + role.replace("_", "-"), str(path)]
    assert preparation.main(args) == 2
    out = capsys.readouterr().out
    assert json.loads(out) == {"status": "rejected", "stage": "input", "item": "report.json",
                               "reason": "raw_sha256_mismatch"}
    assert str(root) not in out


def test_optional_preserved_corrected_case():
    """Explicit opt-in JSON path configuration, absent in clean CI.

    VERIFORGE_RECORDED_IMPORT_PATHS contains keys bundle_root, xdc, netlist,
    board, generator, logical_mapping and native_receipt. Values locate existing
    files only; production function still uses the fixed reviewed hash catalog.
    """
    config = os.environ.get("VERIFORGE_RECORDED_IMPORT_PATHS")
    if config is None:
        pytest.skip("explicit private recorded-evidence paths not provided")
    admitted = preparation.prepare_corrected_case(**{key: Path(value) for key, value in json.loads(config).items()})
    assert len(admitted.artifacts) == 52
    assert admitted.bridge_report.checker_hash == preparation._CORRECTED.checker_hash
    assert admitted.envelope.record.attempts == ()
    assert {r.verdict for r in admitted.manifest.relations} == {"inconclusive"}
