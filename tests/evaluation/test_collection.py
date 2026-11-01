"""Synthetic developer integration; no model, simulator, API or benchmark run."""
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import runpy
import socket
import sqlite3
import subprocess
import sys

import pytest
from pydantic import ValidationError

from veriforge.contracts import ProjectRevision, canonical_hash
from veriforge.evaluation import collection as c, harness as h, records as r
from veriforge.evaluation.cli import collect_main, reproduce_main
from veriforge.planner.models import ExperimentProposal
from veriforge.rtl.runner import reviewed_contract

H = "a" * 64


@pytest.fixture(autouse=True)
def no_external_execution(monkeypatch):
    def deny(*args, **kwargs):
        pytest.fail("No external execution authorized by synthetic collection tests")
    monkeypatch.setattr(subprocess, "run", deny)
    monkeypatch.setattr(subprocess, "Popen", deny)
    monkeypatch.setattr(socket, "create_connection", deny)
    monkeypatch.setattr(sqlite3, "connect", deny)


class SyntheticRuntime:
    evidence_kind = "mock"

    def execute(self, operation_id, experiment, *, revision, contract):
        rows = h.oracle.expected_trace(h.oracle.expand(experiment), experiment.parameters.depth)
        rows = [dict(cycle=x["cycle"], data=x["data"], empty=x["empty"], full=x["full"]) for x in rows]
        return h.ToolReceipt(operation_id=operation_id, experiment_sha256=canonical_hash(experiment),
                             environment_sha256=H, source_sha256=revision.source_hash, contract_sha256=canonical_hash(contract),
                             evidence_kind="mock", status="completed", rows=tuple(rows), cycles=experiment.cycle_count, elapsed_seconds=0.1)


class SyntheticProvider:
    evidence_kind = "mock"
    requested_model = "meta-evaluation-operator-pinned"
    model_identity_sha256 = H
    generation_parameters_sha256 = H

    def propose(self, operation_id, request, *, timeout_seconds):
        plan = ExperimentProposal(kind="experiment", seed=request.seed, objective_ids=("FIFO_BEHAVIOR",),
                                  operations=({"op": "reset"}, {"op": "write", "value": 1}, {"op": "read"}))
        return h.ProposalReceipt(operation_id=operation_id, request_sha256=h.digest(h.encoded(request)),
                                 evidence_kind="mock", state="completed", response=h.encoded(plan),
                                 returned_model=None, model_version=None,
                                 usage=r.ModelUsage(state="unknown", input_tokens=None, output_tokens=None, billed_usd=None),
                                 elapsed_seconds=0.1)


@pytest.fixture
def dataset(tmp_path):
    def make(strategy="directed_manual", *, terminal=True):
        root = tmp_path / (strategy + ("-terminal" if terminal else "-partial"))
        root.mkdir()
        trial_root = root / "original-trial"
        trial_root.mkdir()
        public = h.PublicCase(case_id="case-0001", parameters={"width": 8, "depth": 4})
        revision = ProjectRevision(project_id="synthetic-project", revision_id="synthetic-revision", source_hash=H,
                                   fixture_id="fifo_correct", sources=({"path": "fifo.sv", "sha256": H},))
        fake_ref = dict(artifact_id="truth", path="private-ground-truth.json", sha256=H, size_bytes=1, media_type="application/json")
        truth = r.GroundTruthRecord(record_id="synthetic-truth", evaluation_revision=H, case_id=public.case_id,
                                    classification="control", provenance=fake_ref, reviewed_by="synthetic-reviewer", review=fake_ref,
                                    witness=None, expected_repair=None)
        contract = reviewed_contract()
        plan = ExperimentProposal(kind="experiment", seed=7, objective_ids=("FIFO_BEHAVIOR",),
                                  operations=({"op": "reset"}, {"op": "write", "value": 1}, {"op": "read"}))
        registry = h.Registry((h.TrustedCase(public, revision, contract, truth, (plan,)),))
        limits = h.Limits(attempts=1, cycles=10, tool_seconds=1.0, artifact_bytes=262144, model_tokens=10000,
                          per_attempt_cycles=10, per_attempt_seconds=1.0, per_attempt_artifact_bytes=16384,
                          per_model_seconds=1.0, response_bytes=4096, feedback_output_tokens=128, end_to_end_seconds=60.0)
        study = h.Study(cohort_id="study-20261101-0001", cases=(public,), registry_sha256=registry.sha256,
                        information_policy_sha256=H, environment_sha256=H, strategy_sha256=h.digest(Path(h.__file__).read_bytes()),
                        seeds=(7,), repeats=1, strategies=h.STRATEGIES, limits=limits, model_identity_sha256=H,
                        generation_parameters_sha256=H, tool_evidence_kind="mock", model_evidence_kind="mock", visibility="disclosed_development")
        trial = next(t for t in h.matrix(study) if t.strategy == strategy)
        harness = h.Harness(trial_root, study, trial, registry, now=lambda: datetime(2026, 11, 1, tzinfo=timezone.utc))
        harness.create()
        for _ in range(6):
            outcome = harness.advance(expected_head=harness.journal.read()[1], runtime=SyntheticRuntime(), provider=SyntheticProvider())
            if not terminal or outcome != "progress":
                break
        request = c.CollectionRequest(study=study,
            cases=(c.CaseBinding(case_id=public.case_id, source_sha256=H, checker_sha256=contract.checker.sha256,
                                contract_sha256=canonical_hash(contract), classification="control", ground_truth_review_sha256=H),),
            release=c.ReleaseIdentity(source_commit="c" * 40, source_state="unknown", dirty_patch_sha256=None,
                                      versions=tuple(c.Version(name=n, version=None) for n in ("python", "verilator", "cocotb", "gcc", "runtime_image", "model"))),
            captures=(c.Capture(trial=trial, directory="original-trial", expected_head=harness.log.snapshot().current_head),),
            exclusions=tuple(c.Exclusion(trial=t, reason="not_run", explanation="Synthetic developer fixture; no actual run.", review_sha256=H)
                             for t in h.matrix(study) if t != trial))
        return root, harness, request
    return make


def export(dataset, tmp_path):
    root, harness, request = dataset()
    target = tmp_path / "export"
    manifest, sha = c.export_collection(request, root, target)
    return root, harness, request, target, manifest, sha


def test_end_to_end_private_export_and_read_only_reproduction(dataset, tmp_path):
    root, harness, request, target, manifest, sha = export(dataset, tmp_path)
    result = c.validate_reproduction(target, expected_manifest_sha256=sha)
    assert result["status"] == "manifest_validation_passed" and result["execution"] == "not_run"
    assert result["clean_tool_reproduction"] == "not_established"
    assert manifest.request.study == request.study and manifest.request.cases == request.cases
    assert manifest.request.captures[0].directory == "trials/000"
    assert (target / "trials/000/measurements.jsonl").read_bytes() == harness.log.path.read_bytes()
    for source in root.glob("original-trial/blob-*.bin"):
        assert (target / "trials/000" / source.name).read_bytes() == source.read_bytes()
    report = json.loads((target / "collection.json").read_bytes())
    assert report["status"] == "collected_not_scored" and report["calibration"] == "uncalibrated_protocol_candidate"
    assert report["planned_trials"] == 4 and report["captured_trials"] == 1 and len(report["exclusions"]) == 3
    assert report["trials"][0]["evidence_kinds"] == ["mock"]
    assert set(report["missing_versions"]) == {"python", "verilator", "cocotb", "gcc", "runtime_image", "model"}
    assert "private-ground-truth" not in (target / "collection.json").read_text()
    assert not (root / "original-trial/measurements.jsonl.lock").exists()


@pytest.mark.parametrize("strategy", ["meta_one_pass", "meta_feedback"])
def test_unknown_model_usage_remains_null_and_mock(dataset, tmp_path, strategy):
    root, harness, request = dataset(strategy)
    manifest, files = c.capture_files(request, root)
    rows = [json.loads(line)["record"] for line in files["trials/000/measurements.jsonl"].splitlines()]
    model = next(row for row in rows if row["kind"] == "model_round")
    assert model["usage"] == dict(state="unknown", input_tokens=None, output_tokens=None, billed_usd=None)
    assert model["evidence_kind"] == "mock" and model["returned_model"] is None


def test_partial_trial_is_retained_without_terminal_or_score(dataset):
    root, _, request = dataset(terminal=False)
    _, files = c.capture_files(request, root)
    row = json.loads(files["collection.json"])["trials"][0]
    assert row["state"] == "incomplete" and row["campaign"] is None


@pytest.mark.parametrize("change", ["missing", "duplicate", "foreign", "binding", "version", "missing_version", "dirty_patch", "invented_score"])
def test_request_refuses_silent_denominator_or_identity_changes(dataset, change):
    _, _, request = dataset()
    value = request.model_dump(mode="json")
    if change == "missing": value["exclusions"].pop()
    elif change == "duplicate": value["exclusions"].append(value["exclusions"][0])
    elif change == "foreign": value["exclusions"][0]["trial"]["seed"] = 8
    elif change == "binding": value["cases"][0]["case_id"] = "case-0002"
    elif change == "version": value["release"]["versions"].append(value["release"]["versions"][0])
    elif change == "missing_version": value["release"]["versions"].pop()
    elif change == "invented_score": value["score"] = 1.0
    else: value["release"]["source_state"] = "dirty"
    with pytest.raises((ValidationError, c.CollectionError)):
        c.CollectionRequest.model_validate(value)


@pytest.mark.parametrize("path", ["../outside", "/absolute", "C:/secret", "a\\b", "a//b", "NUL", "ok/COM1.txt", "a.", "a:stream"])
def test_nonportable_or_escaping_capture_paths_rejected(path):
    with pytest.raises(ValueError):
        c.Capture(trial=h.Trial(case_id="case-0001", strategy="directed_manual", seed=7, replicate=0), directory=path, expected_head=H)


@pytest.mark.parametrize("change", ["head", "source", "budget", "evidence"])
def test_frozen_ledger_and_evidence_cannot_be_relabelled(dataset, change):
    root, _, request = dataset()
    value = request.model_dump(mode="json")
    if change == "head": value["captures"][0]["expected_head"] = "b" * 64
    elif change == "source": value["cases"][0]["source_sha256"] = "b" * 64
    elif change == "budget": value["study"]["limits"]["cycles"] = 11
    else: value["study"]["tool_evidence_kind"] = "actual"
    altered = c.CollectionRequest.model_validate(value)
    with pytest.raises(ValueError):
        c.capture_files(altered, root)


@pytest.mark.parametrize("damage", ["blob", "missing_blob", "ledger", "manifest", "extra_file", "report"])
def test_reproduction_refuses_damaged_or_unlisted_bytes(dataset, tmp_path, damage):
    _, _, _, target, _, sha = export(dataset, tmp_path)
    blob = next((target / "trials/000").glob("blob-*.bin"))
    if damage == "blob": blob.write_bytes(b"changed")
    elif damage == "missing_blob": blob.unlink()
    elif damage == "ledger": (target / "trials/000/measurements.jsonl").write_bytes(b"partial")
    elif damage == "manifest": (target / "release-manifest.json").write_bytes(b"{}")
    elif damage == "extra_file": (target / "unlisted.txt").write_text("not part of the packet")
    else: (target / "collection.json").write_bytes(b"{}")
    with pytest.raises((ValueError, OSError)):
        c.validate_reproduction(target, expected_manifest_sha256=sha)


def test_linked_evidence_is_not_exported(dataset, tmp_path):
    root, _, request = dataset()
    blob = next((root / "original-trial").glob("blob-*.bin"))
    linked = root / "alias"
    try:
        os.link(blob, linked)
    except OSError:
        pytest.skip("Host cannot create hardlink fixture")
    with pytest.raises(ValueError, match="single_regular_file"):
        c.export_collection(request, root, tmp_path / "no-export")
    assert not (tmp_path / "no-export").exists()


def test_existing_output_is_preserved(dataset, tmp_path):
    root, _, request, target, _, _ = export(dataset, tmp_path)
    original = (target / "release-manifest.json").read_bytes()
    with pytest.raises(FileExistsError): c.export_collection(request, root, target)
    assert (target / "release-manifest.json").read_bytes() == original


def test_cli_entrypoints_and_fixed_private_diagnostics(dataset, tmp_path, capsys, monkeypatch):
    root, _, request = dataset()
    req = tmp_path / "request.json"
    req.write_bytes(h.encoded(request))
    target = tmp_path / "cli-export"
    monkeypatch.setattr(sys, "argv", ["collect_evaluation.py", "--request", str(req), "--root", str(root), "--output", str(target)])
    with pytest.raises(SystemExit) as done:
        runpy.run_path(str(Path(__file__).parents[2] / "scripts/collect_evaluation.py"), run_name="__main__")
    assert done.value.code == 0
    result = json.loads(capsys.readouterr().out)
    assert result["status"] == "collected_not_scored"
    assert reproduce_main(["--root", str(target), "--expected-manifest-sha256", result["manifest_sha256"]]) == 0
    assert json.loads(capsys.readouterr().out)["execution"] == "not_run"
    assert reproduce_main(["--root", str(target), "--expected-manifest-sha256", H]) == 1
    diagnostic = capsys.readouterr().out
    assert str(tmp_path) not in diagnostic and "not_run" in diagnostic
    req.write_text('{"study":{},"study":{}}')
    assert collect_main(["--request", str(req), "--root", str(root), "--output", str(tmp_path / "invalid")]) == 1
    assert str(tmp_path) not in capsys.readouterr().out
