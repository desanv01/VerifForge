"""Independent synthetic evaluation-ledger review; no measured dataset."""
from datetime import datetime, timezone
import socket
import sqlite3
import subprocess

import pytest
from pydantic import ValidationError

from veriforge.evaluation import records as r

H = "1" * 64


def artifact(name="plan.json"):
    return dict(artifact_id="review-artifact", path=name, sha256=H, size_bytes=10, media_type="application/json")


def base(name):
    unknown = {field: None for field in r.Amounts.model_fields}
    return dict(record_id=name, occurred_at=datetime(2026, 10, 31, tzinfo=timezone.utc), evidence_kind="recorded",
        scope=dict(evaluation_revision=H, campaign_id="review-campaign", case_id="neutral-case", domain="rtl",
            profile="fifo", strategy="meta_feedback", strategy_sha256=H, replicate=0, seed=None,
            source_sha256=H, checker_sha256=H, contract_sha256=H, environment_sha256=H,
            information_policy_sha256=H, visibility="disclosed_development", cache_state="unknown"),
        budget=dict(profile_sha256=H, allocated=unknown.copy(), reserved=unknown.copy(), consumed=unknown.copy()),
        timings={field: None for field in r.Timings.model_fields})


def attempt(name="review-attempt", **changes):
    data = dict(**base(name), experiment_record_id="review-experiment", accepted_plan=artifact(), purpose="detection",
        attempt=dict(attempt_id=name, experiment_id="review-experiment", execution_status="completed", verdict="timeout",
            identity=dict(source_hash=H, checker_hash=H, contract_hash=H, experiment_hash=H, environment_hash=H)))
    data.update(changes)
    return r.AttemptRecord.model_validate(data)


def round_record(name="review-round", **changes):
    data = dict(**base(name), model_round_id=name, provider="meta", requested_model="review-request",
        returned_model=None, model_version=None, prompt_sha256=H, response_schema_sha256=H,
        generation_parameters_sha256=H, provider_seed=None, status="completed", response=artifact("response.json"),
        accepted_plan=None, rejection_reason=None,
        usage=dict(state="unknown", input_tokens=None, output_tokens=None, billed_usd=None))
    data.update(changes)
    return r.ModelRoundRecord.model_validate(data)


def reference(entry):
    return dict(record_id=entry.record.record_id, entry_sha256=entry.sha256)


@pytest.fixture(autouse=True)
def forbid_external_operations(monkeypatch):
    def forbidden(*args, **kwargs):
        pytest.fail("Unexpected process/network/database operation")
    monkeypatch.setattr(subprocess, "run", forbidden)
    monkeypatch.setattr(subprocess, "Popen", forbidden)
    monkeypatch.setattr(socket, "create_connection", forbidden)
    monkeypatch.setattr(sqlite3, "connect", forbidden)


@pytest.fixture
def log(tmp_path):
    ledger = r.EvaluationLog(tmp_path, "independent.jsonl")
    ledger.create()
    return ledger


def test_unknown_provider_accounting_and_timeout_are_preserved(log):
    one = log.append(attempt(), expected_head=None)
    two = log.append(round_record(feedback=(reference(one),)), expected_head=one.sha256)
    saved = log.read(expected_head=two.sha256)
    assert saved[0].record.attempt.execution_status == "completed"
    assert saved[0].record.attempt.verdict == "timeout"
    assert saved[0].record.attempt.failure_signature is None
    assert saved[1].record.returned_model is None and saved[1].record.model_version is None
    assert saved[1].record.usage.model_dump() == dict(state="unknown", input_tokens=None, output_tokens=None, billed_usd=None)


def test_rejected_proposal_requires_retained_response_reference():
    with pytest.raises(ValidationError):
        round_record(status="rejected", response=None, rejection_reason="Outside supported legal operations")


@pytest.mark.parametrize("field,value", [("source_sha256", "2"*64), ("information_policy_sha256", "2"*64),
    ("environment_sha256", "2"*64), ("seed", 42), ("replicate", 1), ("cache_state", "cold")])
def test_frozen_campaign_scope_cannot_drift(log, field, value):
    first = log.append(attempt(), expected_head=None)
    prior = log.path.read_bytes()
    scope = base("next")["scope"]
    scope[field] = value
    with pytest.raises(r.EvaluationLogError, match="frozen_scope_changed"):
        log.append(round_record(scope=scope), expected_head=first.sha256)
    assert log.path.read_bytes() == prior


@pytest.mark.parametrize("component", ["profile_sha256", "allocated"])
def test_frozen_budget_is_distinct_from_unknown_consumption(log, component):
    first = log.append(attempt(), expected_head=None)
    budget = base("next")["budget"]
    if component == "allocated":
        budget[component]["attempts"] = 3
    else:
        budget[component] = "2"*64
    with pytest.raises(r.EvaluationLogError, match="frozen_scope_changed"):
        log.append(round_record(budget=budget), expected_head=first.sha256)


@pytest.mark.parametrize("kind", ["actual", "mock", "recorded"])
def test_homogeneous_campaign_cannot_invent_other_provenance(log, kind):
    first = log.append(attempt(evidence_kind=kind), expected_head=None)
    data = base("campaign-summary")
    data["evidence_kind"] = "mixed"
    campaign = r.CampaignRecord(**data, attempts=(reference(first),), model_rounds=(), stop_reason="completed",
        right_censored=False, review=dict(status="pending", reason="Synthetic only"))
    with pytest.raises(r.EvaluationLogError, match="campaign_evidence_kind_differs"):
        log.append(campaign, expected_head=first.sha256)


@pytest.mark.parametrize("damage", ["wrong_hash", "wrong_type", "duplicate", "reused_round"])
def test_exact_lineage_and_round_identity(log, damage):
    first = log.append(attempt(), expected_head=None)
    second = log.append(round_record(), expected_head=first.sha256)
    if damage == "reused_round":
        candidate = round_record("another-row", model_round_id="review-round")
    else:
        ref = reference(second if damage == "wrong_type" else first)
        if damage == "wrong_hash":
            ref["entry_sha256"] = "2"*64
        candidate = round_record("new-round", feedback=(ref, ref) if damage == "duplicate" else (ref,))
    before = log.path.read_bytes()
    with pytest.raises(r.EvaluationLogError):
        log.append(candidate, expected_head=second.sha256)
    assert log.path.read_bytes() == before


def test_annotation_correction_does_not_replace_outcome_or_chain(log):
    first = log.append(attempt(), expected_head=None)
    before = log.path.read_bytes()
    correction = r.CorrectionRecord(record_id="review-annotation", target=reference(first), reviewed_by="independent-reviewer",
        review=artifact("review.json"), disposition=dict(status="excluded", reason="Development disclosure"),
        explanation="Synthetic source review only")
    second = log.append(correction, expected_head=first.sha256)
    assert log.path.read_bytes().startswith(before)
    assert second.previous_sha256 == first.sha256
    assert log.read(expected_head=second.sha256)[0] == first
    with pytest.raises(ValidationError):
        r.CorrectionRecord.model_validate({**correction.model_dump(), "replacement_verdict": "design_mismatch"})


def test_cooperating_writer_collision_keeps_lock_and_original(log):
    log.lock.write_bytes(b"other writer")
    with pytest.raises(r.EvaluationLogError, match="writer_locked"):
        log.create()
    with pytest.raises(r.EvaluationLogError, match="writer_locked"):
        log.read(expected_head=None)
    assert log.lock.read_bytes() == b"other writer" and log.path.read_bytes() == b""


def test_zero_write_preserves_prior_head(log, monkeypatch):
    first = log.append(attempt(), expected_head=None)
    before = log.path.read_bytes()
    monkeypatch.setattr(r.os, "write", lambda *args: 0)
    with pytest.raises(r.EvaluationLogError, match="append_incomplete"):
        log.append(round_record(), expected_head=first.sha256)
    assert log.path.read_bytes() == before
    assert log.read(expected_head=first.sha256) == (first,)


def test_retained_head_detects_complete_suffix_removal(log):
    first = log.append(attempt(), expected_head=None)
    log.path.write_bytes(b"")
    with pytest.raises(r.EvaluationLogError, match="head_changed"):
        log.read(expected_head=first.sha256)
    with pytest.raises(r.EvaluationLogError, match="head_changed"):
        log.append(round_record(), expected_head=first.sha256)
    assert log.path.read_bytes() == b""


def test_ground_truth_and_measurement_channels_have_no_export_bridge(tmp_path):
    private = r.EvaluationLog(tmp_path, "truth-review.jsonl", channel="ground_truth")
    private.create()
    with pytest.raises(r.EvaluationLogError, match="wrong_private_channel"):
        private.append(attempt(), expected_head=None)
    assert not any("export" in name.lower() or "planner" in name.lower() for name in dir(private))
    assert private.path.read_bytes() == b""


@pytest.mark.parametrize("name", ["../review.jsonl", "review/inner.jsonl", "review\\inner.jsonl", "log:secret.jsonl", "NUL.jsonl", "x"*129])
def test_private_filename_boundaries(tmp_path, name):
    with pytest.raises(r.EvaluationLogError):
        r.EvaluationLog(tmp_path, name)
    assert list(tmp_path.iterdir()) == []


@pytest.mark.parametrize("limit", ["MAX_LINE_BYTES", "MAX_LOG_BYTES", "MAX_RECORDS"])
def test_bounded_append_preserves_existing_prefix(log, monkeypatch, limit):
    first = log.append(attempt(), expected_head=None)
    before = log.path.read_bytes()
    monkeypatch.setattr(r, limit, 1)
    with pytest.raises(r.EvaluationLogError):
        log.append(round_record(), expected_head=first.sha256)
    assert log.path.read_bytes() == before


def test_partial_tail_is_not_repaired_or_overwritten(log):
    first = log.append(attempt(), expected_head=None)
    damaged = log.path.read_bytes() + b'{"incomplete":'
    log.path.write_bytes(damaged)
    with pytest.raises(r.EvaluationLogError, match="truncated_tail"):
        log.append(round_record(), expected_head=first.sha256)
    assert log.path.read_bytes() == damaged


def test_detected_mutation_before_append_open_is_preserved(log, monkeypatch):
    first = log.append(attempt(), expected_head=None)
    before = log.path.read_bytes()
    original_open = r.os.open

    def changed_open(path, flags, *args, **kwargs):
        if path == log.path and flags & r.os.O_APPEND:
            with log.path.open("ab") as stream:
                stream.write(b"EXTERNAL_MUTATION")
        return original_open(path, flags, *args, **kwargs)

    monkeypatch.setattr(r.os, "open", changed_open)
    with pytest.raises(r.EvaluationLogError, match="file_identity_changed"):
        log.append(round_record(), expected_head=first.sha256)
    assert log.path.read_bytes() == before + b"EXTERNAL_MUTATION"
