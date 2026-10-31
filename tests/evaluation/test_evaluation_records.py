"""Synthetic measurement fixtures, no benchmark, model or engineering execution."""
from datetime import datetime, timezone
import json
from pathlib import Path

import pytest
from pydantic import ValidationError

from veriforge.evaluation import records as r

H = "a" * 64
AT = datetime(2026, 10, 31, tzinfo=timezone.utc)


def artifact(name="evidence.json"):
    return dict(artifact_id="artifact", path=name, sha256=H, size_bytes=12, media_type="application/json")


def scope():
    return dict(evaluation_revision=H, campaign_id="campaign", case_id="case-001", domain="riscv", profile="rv32i",
        strategy="meta_feedback", strategy_sha256=H, replicate=0, seed=None, source_sha256=H, checker_sha256=H,
        contract_sha256=H, environment_sha256=H, information_policy_sha256=H, visibility="disclosed_development", cache_state="unknown")


def measurement(record_id):
    unknown = {name: None for name in r.Amounts.model_fields}
    return dict(record_id=record_id, occurred_at=AT, scope=scope(), evidence_kind="mock",
        budget=dict(profile_sha256=H, allocated=unknown, reserved=unknown, consumed=unknown),
        timings={name: None for name in r.Timings.model_fields})


def attempt(record_id="attempt-record", attempt_id="attempt-1", **changes):
    data = dict(**measurement(record_id), experiment_record_id="experiment-record", accepted_plan=artifact("plan.json"),
        purpose="detection", attempt=dict(attempt_id=attempt_id, experiment_id="experiment", execution_status="completed",
        verdict="pass_within_scope", identity=dict(source_hash=H, checker_hash=H, contract_hash=H, experiment_hash=H, environment_hash=H)))
    data.update(changes)
    return r.AttemptRecord.model_validate(data)


def model(record_id="model-record", **changes):
    data = dict(**measurement(record_id), model_round_id="round-1", provider="meta", requested_model="meta-llama/example",
        returned_model=None, model_version=None, prompt_sha256=H, response_schema_sha256=H, generation_parameters_sha256=H,
        provider_seed=None, status="unknown", usage=dict(state="unknown", input_tokens=None, output_tokens=None, billed_usd=None),
        response=None, accepted_plan=None, rejection_reason=None)
    data.update(changes)
    return r.ModelRoundRecord.model_validate(data)


def reference(entry):
    return dict(record_id=entry.record.record_id, entry_sha256=entry.sha256)


@pytest.fixture
def log(tmp_path):
    value = r.EvaluationLog(tmp_path, "measurements.jsonl")
    value.create()
    return value


def test_campaign_links_immutable_attempt_and_feedback_round(log):
    first = log.append(attempt(), expected_head=None)
    second = log.append(model(feedback=(reference(first),)), expected_head=first.sha256)
    campaign = r.CampaignRecord(**measurement("campaign-record"), attempts=(reference(first),), model_rounds=(reference(second),),
        stop_reason="budget_exhausted", right_censored=True, review=dict(status="inconclusive", reason="Synthetic fixture only"))
    third = log.append(campaign, expected_head=second.sha256)
    records = log.read(expected_head=third.sha256)
    assert [entry.sequence for entry in records] == [0, 1, 2]
    assert records[0].previous_sha256 is None and records[2].previous_sha256 == second.sha256
    assert records[1].record.usage.input_tokens is None and records[1].record.usage.billed_usd is None
    assert records[0].record.evidence_kind == "mock"
    assert records[0].record.attempt.execution_status == "completed"
    assert records[0].record.attempt.verdict == "pass_within_scope"
    with pytest.raises(ValidationError):
        records[0].record.record_id = "overwrite"


def test_mixed_actual_tool_and_mock_model_cannot_be_labeled_all_actual(log):
    first = log.append(attempt(evidence_kind="actual"), expected_head=None)
    second = log.append(model(), expected_head=first.sha256)
    data = dict(**measurement("campaign-record"), attempts=(reference(first),), model_rounds=(reference(second),),
        stop_reason="completed", right_censored=False, review=dict(status="pending", reason="Synthetic test"))
    data["evidence_kind"] = "actual"
    with pytest.raises(r.EvaluationLogError, match="campaign_evidence_kind_differs"):
        log.append(r.CampaignRecord(**data), expected_head=second.sha256)
    data["evidence_kind"] = "mixed"
    final = log.append(r.CampaignRecord(**data), expected_head=second.sha256)
    assert log.read(expected_head=final.sha256)[-1].record.evidence_kind == "mixed"


def test_reviewed_correction_appends_without_replacing_measurement(log):
    first = log.append(attempt(), expected_head=None)
    prefix = log.path.read_bytes()
    correction = r.CorrectionRecord(record_id="correction", target=reference(first), reviewed_by="reviewer",
        review=artifact("review.json"), disposition=dict(status="excluded", reason="Reclassified after review"),
        explanation="Synthetic demonstration; not held-out accuracy evidence")
    second = log.append(correction, expected_head=first.sha256)
    assert log.path.read_bytes().startswith(prefix)
    assert log.read(expected_head=second.sha256)[0] == first


def truth():
    return r.GroundTruthRecord(record_id="truth", evaluation_revision=H, case_id="case-001", classification="seeded_defect",
        provenance=artifact("PRIVATE_GROUND_TRUTH.json"), reviewed_by="reviewer", review=artifact("truth-review.json"),
        witness=artifact("PRIVATE_WITNESS.json"), expected_repair=artifact("PRIVATE_EXPECTED_REPAIR.json"))


def test_ground_truth_requires_separate_file_channel(log, tmp_path):
    with pytest.raises(r.EvaluationLogError, match="wrong_private_channel"):
        log.append(truth(), expected_head=None)
    separate = r.EvaluationLog(tmp_path, "ground-truth.jsonl", channel="ground_truth")
    separate.create()
    entry = separate.append(truth(), expected_head=None)
    assert separate.read(expected_head=entry.sha256)[0].record.classification == "seeded_defect"
    with pytest.raises(r.EvaluationLogError, match="wrong_private_channel"):
        separate.append(attempt(), expected_head=entry.sha256)
    assert b"PRIVATE" not in log.path.read_bytes()
    with pytest.raises(ValidationError):
        r.Scope(**scope(), ground_truth="PRIVATE_LABEL")
    with pytest.raises(r.EvaluationLogError, match="wrong_private_channel"):
        r.EvaluationLog(tmp_path, "ground-truth.jsonl").read(expected_head=entry.sha256)


@pytest.mark.parametrize("change", [dict(input_tokens=0), dict(output_tokens=1), dict(billed_usd=0.0)])
def test_unknown_usage_cannot_be_silently_counted_as_zero(change):
    data = dict(state="unknown", input_tokens=None, output_tokens=None, billed_usd=None)
    data.update(change)
    with pytest.raises(ValidationError):
        r.ModelUsage(**data)


@pytest.mark.parametrize("bad", [True, -1, 1.5])
def test_integer_accounting_is_strict(bad):
    data = dict(state="known", input_tokens=bad, output_tokens=1, billed_usd=None)
    with pytest.raises(ValidationError):
        r.ModelUsage(**data)


@pytest.mark.parametrize("bad", [float("inf"), float("nan"), -1.0])
def test_timing_is_finite_nonnegative(bad):
    data = {name: None for name in r.Timings.model_fields}
    data["model_seconds"] = bad
    with pytest.raises(ValidationError):
        r.Timings(**data)


@pytest.mark.parametrize("status,verdict", [("running", "pass_within_scope"), ("infrastructure_error", "design_mismatch"), ("completed", None)])
def test_execution_status_cannot_be_conflated_with_design_verdict(status, verdict):
    data = attempt().model_dump()
    data["attempt"].update(execution_status=status, verdict=verdict)
    with pytest.raises(ValidationError):
        r.AttemptRecord.model_validate(data)


def test_missing_identity_and_bad_plan_path_are_rejected():
    data = attempt().model_dump()
    data["attempt"]["identity"]["checker_hash"] = "b" * 64
    with pytest.raises(ValidationError, match="frozen scope"):
        r.AttemptRecord.model_validate(data)
    data = attempt().model_dump()
    data["accepted_plan"]["path"] = "../secret"
    with pytest.raises(ValidationError):
        r.AttemptRecord.model_validate(data)
    data = scope(); data["visibility"] = "held_out"
    with pytest.raises(ValidationError, match="context-audit"):
        r.Scope(**data)


def test_null_model_identity_is_preserved_not_invented():
    recorded = model(status="completed", response=artifact())
    assert recorded.returned_model is None and recorded.model_version is None
    assert recorded.usage.state == "unknown"


@pytest.mark.parametrize("missing", ["response", "rejection_reason"])
def test_rejected_proposal_keeps_exact_response_and_reason(missing):
    fields = dict(status="rejected", response=artifact("rejected-proposal.json"), rejection_reason="Outside typed operation space")
    fields[missing] = None
    with pytest.raises(ValidationError, match="retained response and reason"):
        model(**fields)


def test_rejected_proposal_round_trips_retained_reference(log):
    rejected = model(status="rejected", response=artifact("rejected-proposal.json"), rejection_reason="Outside typed operation space")
    entry = log.append(rejected, expected_head=None)
    saved = log.read(expected_head=entry.sha256)[0].record
    assert saved.response == rejected.response and saved.rejection_reason == rejected.rejection_reason
    assert saved.accepted_plan is None and saved.usage.input_tokens is None


@pytest.mark.parametrize("status", ["failed", "unknown"])
def test_missing_transport_response_is_not_reclassified_as_rejected_proposal(status):
    recorded = model(status=status, response=None, rejection_reason=None)
    assert recorded.status == status and recorded.response is None
    assert recorded.usage.state == "unknown" and recorded.usage.billed_usd is None


def test_append_revalidates_constructed_model(log):
    invalid = attempt().model_copy(update={"evidence_kind": "live-by-assumption"})
    with pytest.raises(r.EvaluationLogError, match="invalid_record"):
        log.append(invalid, expected_head=None)
    assert log.path.read_bytes() == b""


def test_create_and_stale_head_cannot_overwrite(log):
    first = log.append(attempt(), expected_head=None)
    original = log.path.read_bytes()
    with pytest.raises(r.EvaluationLogError, match="log_already_exists"):
        log.create()
    with pytest.raises(r.EvaluationLogError, match="head_changed"):
        log.append(model(), expected_head=None)
    assert log.path.read_bytes() == original
    assert log.read(expected_head=first.sha256) == (first,)


@pytest.mark.parametrize("mode", ["record", "subject", "scope", "allocation", "reference", "cross_campaign"])
def test_duplicate_or_unbound_measurements_fail_closed(log, mode):
    first = log.append(attempt(), expected_head=None)
    original = log.path.read_bytes()
    if mode == "record": candidate = attempt()
    elif mode == "subject": candidate = attempt("other-record")
    elif mode == "scope":
        data = measurement("model-record"); data["scope"]["seed"] = 123
        candidate = model(scope=data["scope"])
    elif mode == "allocation":
        data = measurement("model-record"); data["budget"]["allocated"]["model_tokens"] = 999
        candidate = model(budget=data["budget"])
    elif mode == "reference": candidate = model(feedback=(dict(record_id="missing", entry_sha256=H),))
    else:
        data = scope(); data["campaign_id"] = "other-campaign"
        candidate = model(scope=data, feedback=(reference(first),))
    with pytest.raises(r.EvaluationLogError):
        log.append(candidate, expected_head=first.sha256)
    assert log.path.read_bytes() == original


@pytest.mark.parametrize("mode", ["hash", "previous", "sequence", "duplicate_key", "partial", "missing_last", "extra_blank"])
def test_chain_corruption_and_truncation_are_detected(log, mode):
    first = log.append(attempt(), expected_head=None)
    last = log.append(model(), expected_head=first.sha256)
    lines = log.path.read_bytes().splitlines(keepends=True)
    if mode in ("hash", "previous", "sequence"):
        row = json.loads(lines[1])
        if mode == "hash": row["record"]["model_round_id"] = "tampered"
        else:
            row["previous_sha256" if mode == "previous" else "sequence"] = "b"*64 if mode == "previous" else 5
            row["sha256"] = r._digest(r._canonical({k:v for k,v in row.items() if k != "sha256"}))
        lines[1] = r._canonical(row) + b"\n"
    elif mode == "duplicate_key": lines[1] = lines[1].replace(b'{', b'{"sequence":0,', 1)
    elif mode == "partial": lines[1] = lines[1][:-8]
    elif mode == "missing_last": lines = lines[:1]
    else: lines.append(b"\n")
    log.path.write_bytes(b"".join(lines))
    corrupt = log.path.read_bytes()
    with pytest.raises(r.EvaluationLogError):
        log.read(expected_head=last.sha256)
    with pytest.raises(r.EvaluationLogError):
        log.append(model("new"), expected_head=last.sha256)
    assert log.path.read_bytes() == corrupt


@pytest.mark.parametrize("name", ["../outside.jsonl", "/absolute.jsonl", "a/b.jsonl", "a\\b.jsonl", "file:stream.jsonl", "plain.txt", "x"*129,
    "CON.jsonl", "nul.extra.jsonl", "COM1.jsonl", "line\n.jsonl"])
def test_log_path_is_single_bounded_name(tmp_path, name):
    with pytest.raises(r.EvaluationLogError):
        r.EvaluationLog(tmp_path, name)


def test_writer_lock_is_not_automatically_removed(log):
    log.lock.write_bytes(b"existing lock from another writer or crash")
    with pytest.raises(r.EvaluationLogError, match="writer_locked"):
        log.append(attempt(), expected_head=None)
    assert log.lock.read_bytes() == b"existing lock from another writer or crash"
    assert log.path.read_bytes() == b""


@pytest.mark.parametrize("bound", ["MAX_LINE_BYTES", "MAX_LOG_BYTES", "MAX_RECORDS"])
def test_append_bounds_preserve_existing_file(log, monkeypatch, bound):
    monkeypatch.setattr(r, bound, 1)
    if bound == "MAX_RECORDS":
        first = log.append(attempt(), expected_head=None)
        head = first.sha256
    else: head = None
    original = log.path.read_bytes()
    with pytest.raises(r.EvaluationLogError, match="bound"):
        log.append(model(), expected_head=head)
    assert log.path.read_bytes() == original


def test_reader_rejects_oversize_before_open(log, monkeypatch):
    log.path.write_bytes(b"x"*32)
    monkeypatch.setattr(r, "MAX_LOG_BYTES", 16)
    with pytest.raises(r.EvaluationLogError, match="log_size_limit"):
        log.read(expected_head=None)


def test_partial_write_is_preserved_and_further_append_refused(log, monkeypatch):
    original_write = r.os.write
    writes = 0
    def fail(fd, data):
        nonlocal writes
        writes += 1
        if writes == 1: return original_write(fd, data[:17])
        raise OSError("synthetic disk error")
    monkeypatch.setattr(r.os, "write", fail)
    with pytest.raises(OSError):
        log.append(attempt(), expected_head=None)
    assert len(log.path.read_bytes()) == 17 and not log.lock.exists()
    monkeypatch.setattr(r.os, "write", original_write)
    with pytest.raises(r.EvaluationLogError, match="truncated_tail"):
        log.append(attempt(), expected_head=None)


def test_read_rejects_mutation_during_read(log, monkeypatch):
    first = log.append(attempt(), expected_head=None)
    real_regular = r._regular
    calls = 0
    def mutate(path):
        nonlocal calls
        calls += 1
        if calls == 2:
            with Path(path).open("ab") as output: output.write(b" ")
        return real_regular(path)
    monkeypatch.setattr(r, "_regular", mutate)
    with pytest.raises(r.EvaluationLogError, match="file_changed_during_read"):
        log.read(expected_head=first.sha256)


@pytest.mark.parametrize("timestamp", [123, True, "123", "2026-10-31T20:00:00", datetime(2026, 10, 31)])
def test_timestamp_cannot_be_unzoned_or_implicitly_numeric(timestamp):
    with pytest.raises(ValidationError):
        attempt(occurred_at=timestamp)


@pytest.mark.parametrize("kind", ["hardlink", "symlink"])
def test_linked_evidence_logs_are_rejected(tmp_path, kind):
    original = tmp_path / "original"
    original.write_bytes(b"")
    log = r.EvaluationLog(tmp_path, "linked.jsonl")
    try:
        if kind == "hardlink": log.path.hardlink_to(original)
        else: log.path.symlink_to(original)
    except OSError:
        pytest.skip("Host does not permit creation of link fixture")
    with pytest.raises(r.EvaluationLogError, match="linked_path|single_regular"):
        log.read(expected_head=None)
    assert original.read_bytes() == b""
