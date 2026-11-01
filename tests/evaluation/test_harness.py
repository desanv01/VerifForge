"""Synthetic protocol/storage tests only; no benchmark/native/provider execution."""
from datetime import datetime, timedelta, timezone
from dataclasses import replace
from pathlib import Path
import socket
import sqlite3
import subprocess

import pytest
from pydantic import ValidationError

from veriforge.evaluation import harness as h
from veriforge.contracts import ProjectRevision, canonical_hash
from veriforge.evaluation import records as r
from veriforge.planner.models import ExperimentProposal
from veriforge.rtl.runner import reviewed_contract

H = "a"*64
AT = datetime(2026, 10, 31, tzinfo=timezone.utc)


def artifact(path):
    return dict(artifact_id="private-reference", path=path, sha256=H, size_bytes=1, media_type="application/json")


class Clock:
    def __init__(self): self.at = AT
    def __call__(self): return self.at


def plan(seed=7):
    return ExperimentProposal(kind="experiment", seed=seed, objective_ids=("FIFO_BEHAVIOR",),
        operations=({"op": "reset"}, {"op": "write", "value": 1}, {"op": "read"}))


class FakeProvider:
    evidence_kind = "mock"
    model_identity_sha256 = H
    requested_model = "meta-evaluation-operator-pinned"
    generation_parameters_sha256 = H
    def __init__(self, *, unknown=False, invalid=False, fail=False, returned=None):
        self.calls, self.unknown, self.invalid, self.fail, self.returned = [], unknown, invalid, fail, returned

    def propose(self, operation_id, request, *, timeout_seconds):
        self.calls.append((operation_id, request, timeout_seconds))
        if self.fail: raise RuntimeError("SECRET fifo_reset_bug PRIVATE_WITNESS.json")
        raw = b'{"arbitrary_shell":"SECRET fifo_reset_bug"}' if self.invalid else h.encoded(plan(request.seed))
        return h.ProposalReceipt(operation_id=operation_id, request_sha256=h.digest(h.encoded(request)), evidence_kind="mock",
            state="completed", response=raw, returned_model=self.returned, model_version=None,
            usage=r.ModelUsage(state="unknown" if self.unknown else "known", input_tokens=None if self.unknown else 20,
                output_tokens=None if self.unknown else 30, billed_usd=None), elapsed_seconds=0.1)


class FakeRuntime:
    def __init__(self, *, mismatch=False, status="completed", evidence="mock", fail=False, over_cycles=False):
        self.calls, self.mismatch, self.status, self.evidence, self.fail, self.over_cycles = [], mismatch, status, evidence, fail, over_cycles
        self.evidence_kind = evidence

    def execute(self, operation_id, experiment, *, revision, contract):
        self.calls.append((operation_id, experiment))
        if self.fail: raise RuntimeError("SECRET fifo_reset_bug PRIVATE_WITNESS.json")
        # Synthetic observations, not simulator output. Deliberate mutation below
        # must be independently classified by the installed immutable checker.
        expected = h.oracle.expected_trace(h.oracle.expand(experiment), experiment.parameters.depth)
        rows = [dict(cycle=row["cycle"], data=row["data"], empty=row["empty"], full=row["full"]) for row in expected]
        if self.mismatch: rows[-1]["data"] ^= 1
        return h.ToolReceipt(operation_id=operation_id, experiment_sha256=canonical_hash(experiment), environment_sha256=H,
            source_sha256=revision.source_hash, contract_sha256=canonical_hash(contract),
            evidence_kind=self.evidence, status=self.status, rows=tuple(rows) if self.status == "completed" else (),
            cycles=1001 if self.over_cycles else len(rows) if self.status == "completed" else 0, elapsed_seconds=0.1)


@pytest.fixture(autouse=True)
def no_external_execution(monkeypatch):
    def forbidden(*args, **kwargs): pytest.fail("No external operation authorized")
    monkeypatch.setattr(subprocess, "run", forbidden)
    monkeypatch.setattr(subprocess, "Popen", forbidden)
    monkeypatch.setattr(socket, "create_connection", forbidden)
    monkeypatch.setattr(sqlite3, "connect", forbidden)


@pytest.fixture
def setup(tmp_path):
    public = h.PublicCase(case_id="case-0001", parameters={"width": 8, "depth": 4})
    revision = ProjectRevision(project_id="private-project", revision_id="private-revision", source_hash=H,
        fixture_id="fifo_reset_bug", sources=({"path": "fifo.sv", "sha256": H},))
    truth = r.GroundTruthRecord(record_id="private-truth", evaluation_revision=H, case_id=public.case_id,
        classification="seeded_defect", provenance=artifact("PRIVATE_LABELS.json"), reviewed_by="reviewer",
        review=artifact("PRIVATE_REVIEW.json"), witness=artifact("PRIVATE_WITNESS.json"), expected_repair=artifact("PRIVATE_REPAIR.json"))
    registry = h.Registry((h.TrustedCase(public, revision, reviewed_contract(), truth, (plan(),)),))
    limits = h.Limits(attempts=3, cycles=30, tool_seconds=3.0, artifact_bytes=262144, model_tokens=10000,
        per_attempt_cycles=10, per_attempt_seconds=1.0, per_attempt_artifact_bytes=16384, per_model_seconds=1.0,
        response_bytes=4096, feedback_output_tokens=128, end_to_end_seconds=60.0)
    study = h.Study(cohort_id="study-20261031-0001", cases=(public,), registry_sha256=registry.sha256, information_policy_sha256=H, environment_sha256=H,
        strategy_sha256=h.digest(Path(h.__file__).read_bytes()), seeds=(7,), repeats=1, strategies=h.STRATEGIES, limits=limits,
        model_identity_sha256=H, generation_parameters_sha256=H, tool_evidence_kind="mock", model_evidence_kind="mock", visibility="disclosed_development")
    clock = Clock()
    def make(strategy="directed_manual", *, selected_study=None, selected_registry=None, create=True, name="trial"):
        selected_study = selected_study or study
        selected_registry = selected_registry or registry
        trial = next(t for t in h.matrix(selected_study) if t.strategy == strategy)
        root = tmp_path/name
        root.mkdir(exist_ok=True)
        result = h.Harness(root, selected_study, trial, selected_registry, now=clock)
        if create: result.create()
        return result
    return make, study, registry, clock


def advance(harness, **kwargs):
    return harness.advance(expected_head=harness.journal.read()[1], **kwargs)


def records(harness):
    events, _, _ = harness.read()
    return tuple(entry.record for entry in harness.sync(events))


@pytest.mark.parametrize("strategy", h.STRATEGIES)
def test_four_strategies_are_typed_bounded_and_separate(setup, strategy):
    make, _, _, _ = setup
    harness = make(strategy)
    provider, runtime = FakeProvider(), FakeRuntime()
    for _ in range(10):
        status = advance(harness, provider=provider, runtime=runtime)
        if status != "progress": break
    else: pytest.fail("Unbounded campaign")
    rows = records(harness)
    assert isinstance(rows[-1], r.CampaignRecord)
    assert all(row.evidence_kind == "mock" for row in rows)
    assert rows[-1].budget.reserved.attempts <= 3 and rows[-1].budget.reserved.cycles <= 30
    assert len(runtime.calls) <= 3 and len(provider.calls) <= (1 if strategy == "meta_one_pass" else 3)
    assert rows[-1].budget.consumed.attempts == len(runtime.calls)
    for row in rows:
        if isinstance(row, r.AttemptRecord):
            assert row.attempt.usage.artifact_bytes == sum(ref.size_bytes for ref in row.attempt.artifacts)
            assert row.accepted_plan in row.attempt.artifacts
    if not strategy.startswith("meta_"):
        assert provider.calls == [] and rows[-1].budget.allocated.model_tokens == rows[-1].budget.consumed.model_tokens == 0
    if strategy == "meta_one_pass":
        assert len(provider.calls) == len(runtime.calls) == 1
        assert provider.calls[0][1].feedback == ()


def test_matrix_and_random_are_stable_across_reconstruction(setup):
    make, study, registry, _ = setup
    expanded = study.model_copy(update={"seeds": (7, 13), "repeats": 2})
    assert len(h.matrix(expanded)) == 16 and h.matrix(expanded) == h.matrix(expanded)
    one = make("constrained_random")
    two = make("constrained_random", create=False)
    assert one.proposal(0) == two.proposal(0)
    assert one.experiment(one.proposal(0), "stable").cycle_count == 10
    assert all(p.case_id == "case-0001" for p in h.matrix(expanded))
    with pytest.raises(TypeError): registry._cases["case-0002"] = registry.resolve("case-0001")


def test_hidden_map_and_truth_never_enter_provider_input_or_measurement_metadata(setup, tmp_path):
    make, study, registry, _ = setup
    harness, provider = make("meta_feedback"), FakeProvider()
    advance(harness, provider=provider)
    request = h.encoded(provider.calls[0][1])
    assert b"fifo_reset_bug" not in request and b"PRIVATE" not in request and b"seeded_defect" not in request
    truth = r.EvaluationLog(tmp_path, "ground-truth.jsonl", channel="ground_truth")
    truth.create()
    first = h.record_ground_truth(truth, study, registry, "case-0001")
    assert h.record_ground_truth(truth, study, registry, "case-0001") == first
    assert first.record.classification == "seeded_defect"
    metadata = harness.journal.path.read_bytes()+harness.log.path.read_bytes()
    assert b"fifo_reset_bug" not in metadata and b"PRIVATE" not in metadata and b"seeded_defect" not in metadata
    with pytest.raises(h.HarnessError, match="truth_channel_required"):
        h.record_ground_truth(harness.log, study, registry, "case-0001")


@pytest.mark.parametrize("strategy", ["meta_one_pass", "meta_feedback"])
def test_unknown_usage_is_null_reserved_and_not_automatically_retried(setup, strategy):
    make, _, _, _ = setup
    harness, provider, runtime = make(strategy), FakeProvider(unknown=True), FakeRuntime()
    assert advance(harness, provider=provider) == "progress"
    assert advance(harness, provider=provider, runtime=runtime) == "blocked"
    rows = records(harness)
    assert rows[0].usage.input_tokens is rows[0].usage.output_tokens is rows[0].usage.billed_usd is None
    assert rows[-1].budget.consumed.model_tokens is None and rows[-1].budget.reserved.model_tokens > 0
    assert len(provider.calls) == 1 and runtime.calls == []


def test_rejected_response_is_retained_with_fixed_reason_and_no_hidden_error(setup):
    make, _, _, _ = setup
    harness = make("meta_feedback")
    assert advance(harness, provider=FakeProvider(invalid=True)) == "progress"
    record = records(harness)[0]
    assert record.status == "rejected" and record.response is not None and record.rejection_reason == "typed_proposal_invalid"
    assert b"SECRET" not in harness.log.path.read_bytes() and b"fifo_reset_bug" not in harness.log.path.read_bytes()


def test_checker_independently_derives_mismatch_and_replay_uses_saved_stimulus(setup):
    make, _, _, _ = setup
    harness, runtime = make(), FakeRuntime(mismatch=True)
    assert advance(harness, runtime=runtime) == "progress"
    first = records(harness)[0]
    assert first.attempt.verdict == "design_mismatch" and first.attempt.failure_signature.first_cycle == 2
    assert advance(harness, runtime=runtime, replay_parent_id=first.record_id) == "progress"
    second = records(harness)[1]
    assert second.purpose == "replay" and second.parent_attempt.record_id == first.record_id
    assert runtime.calls[0][1].operations == runtime.calls[1][1].operations
    assert runtime.calls[0][1].seed == runtime.calls[1][1].seed
    assert runtime.calls[0][0] != runtime.calls[1][0]
    assert second.attempt.failure_signature == first.attempt.failure_signature


@pytest.mark.parametrize("status", ["build_error", "harness_error", "timeout", "unsupported"])
def test_non_design_outcomes_remain_distinct(setup, status):
    make, _, _, _ = setup
    harness, runtime = make(), FakeRuntime(status=status)
    advance(harness, runtime=runtime)
    outcome = records(harness)[0].attempt
    assert outcome.execution_status == "completed" and outcome.verdict == status and outcome.failure_signature is None
    terminal = advance(harness, runtime=runtime)
    assert terminal == ("unsupported" if status == "unsupported" else "infrastructure_failure")


def test_aggregate_reservation_blocks_before_tool_invocation(setup):
    make, study, _, _ = setup
    limited = study.model_copy(update={"limits": study.limits.model_copy(update={"cycles": 9})})
    harness, runtime = make(selected_study=limited), FakeRuntime()
    assert advance(harness, runtime=runtime) == "budget_exhausted" and runtime.calls == []
    assert records(harness)[-1].right_censored


@pytest.mark.parametrize("resource,value", [("attempts", 1), ("cycles", 10), ("tool_seconds", 1.0), ("artifact_bytes", 16384)])
def test_each_aggregate_tool_resource_caps_later_admission(setup, resource, value):
    make, study, _, _ = setup
    limited = study.model_copy(update={"limits": study.limits.model_copy(update={resource: value})})
    harness, runtime = make("constrained_random", selected_study=limited), FakeRuntime()
    assert advance(harness, runtime=runtime) == "progress"
    assert advance(harness, runtime=runtime) == "budget_exhausted" and len(runtime.calls) == 1


def test_end_to_end_expiry_is_inclusive_and_dispatch_cap_must_fit(setup):
    make, _, _, clock = setup
    harness, runtime = make(), FakeRuntime()
    clock.at += timedelta(seconds=59.5)
    assert advance(harness, runtime=runtime) == "budget_exhausted" and runtime.calls == []


def test_mixed_provider_and_recorded_tool_evidence_are_not_all_actual(setup):
    make, study, _, _ = setup
    harness = make("meta_one_pass", selected_study=study.model_copy(update={"tool_evidence_kind": "recorded"}))
    advance(harness, provider=FakeProvider())
    advance(harness, runtime=FakeRuntime(evidence="recorded"))
    advance(harness)
    assert records(harness)[-1].evidence_kind == "mixed"


@pytest.mark.parametrize("stage", ["tool", "model"])
def test_ambiguous_external_effect_never_automatically_redispatches(setup, stage):
    make, _, _, _ = setup
    harness = make("meta_feedback" if stage == "model" else "directed_manual")
    provider, runtime = FakeProvider(fail=True), FakeRuntime(fail=True)
    with pytest.raises(h.HarnessError, match="^effect_unsettled_review_required$"):
        advance(harness, provider=provider, runtime=runtime)
    recreated = make("meta_feedback" if stage == "model" else "directed_manual", create=False)
    with pytest.raises(h.HarnessError, match="^recovery_required_no_automatic_redispatch$"):
        advance(recreated, provider=provider, runtime=runtime)
    assert len(provider.calls)+len(runtime.calls) == 1
    assert records(recreated) == ()
    assert b"SECRET" not in recreated.journal.path.read_bytes()


@pytest.mark.parametrize("after_append", [False, True])
def test_persisted_receipt_recovers_ledger_without_duplicate_effect_or_rows(setup, monkeypatch, after_append):
    make, _, _, _ = setup
    harness, runtime = make(), FakeRuntime()
    append = harness.log.append
    def interrupted(record, **kwargs):
        if after_append: append(record, **kwargs)
        raise OSError("synthetic storage interruption")
    monkeypatch.setattr(harness.log, "append", interrupted)
    with pytest.raises(h.HarnessError, match="ledger_sync_required"):
        advance(harness, runtime=runtime)
    recreated = make(create=False)
    assert advance(recreated, runtime=runtime) == "completed"
    assert len(runtime.calls) == 1
    assert len([row for row in records(recreated) if isinstance(row, r.AttemptRecord)]) == 1
    assert advance(recreated, runtime=runtime) == "completed" and len(runtime.calls) == 1


def test_changed_protocol_or_registry_cannot_resume(setup):
    make, study, _, _ = setup
    harness = make()
    changed = study.model_copy(update={"information_policy_sha256": "b"*64})
    recreated = make(selected_study=changed, create=False)
    with pytest.raises(h.HarnessError, match="freeze_changed"):
        advance(recreated, runtime=FakeRuntime())
    assert records(harness) == ()


def test_adversarial_response_metadata_cannot_disclose_private_fixture(setup):
    make, _, _, _ = setup
    harness = make("meta_feedback")
    with pytest.raises(h.HarnessError, match="effect_unsettled_review_required"):
        advance(harness, provider=FakeProvider(returned="fifo_reset_bug"))
    assert b"fifo_reset_bug" not in harness.journal.path.read_bytes()+harness.log.path.read_bytes()


def test_budget_violation_leaves_reservation_and_requires_review(setup):
    make, _, _, _ = setup
    harness = make()
    with pytest.raises(h.HarnessError, match="effect_unsettled_review_required"):
        advance(harness, runtime=FakeRuntime(over_cycles=True))
    events, _, pending = harness.read()
    assert pending and harness.reserved(events).attempts == 1 and harness.reserved(events).cycles == 10


@pytest.mark.parametrize("damage", ["truncated", "stale_head", "lock", "blob", "observations"])
def test_storage_faults_stop_before_new_effect(setup, damage):
    make, _, _, _ = setup
    harness, runtime = make(), FakeRuntime()
    advance(harness, runtime=runtime)
    head = harness.journal.read()[1]
    if damage == "truncated": harness.journal.path.write_bytes(harness.journal.path.read_bytes()[:-1])
    elif damage == "lock": harness.journal.lock_path.write_bytes(b"existing writer")
    elif damage in ("blob", "observations"):
        record = records(harness)[0]
        ref = record.accepted_plan if damage == "blob" else record.attempt.artifacts[-1]
        (harness.blobs.root/ref.path).write_bytes(b"corrupt")
    else: head = "0"*64
    with pytest.raises((h.HarnessError, r.EvaluationLogError)):
        harness.advance(expected_head=head, runtime=runtime)
    assert len(runtime.calls) == 1


def test_scope_requires_heldout_audit_and_rejects_unknown_fields(setup):
    _, study, _, _ = setup
    data = study.model_dump()
    data["visibility"] = "held_out"
    with pytest.raises(ValidationError): h.Study.model_validate(data)
    with pytest.raises(ValidationError): h.PublicCase(case_id="fifo_reset_bug", parameters={})
    with pytest.raises(ValidationError): h.PlannerInput(case=study.cases[0], seed=7, max_cycles=10, max_output_tokens=100, hidden_label="secret")


@pytest.mark.parametrize("classification", ["equivalent_mutant", "invalid", "unresolved"])
def test_private_exclusion_categories_never_dispatch_or_disclose_label(setup, classification):
    make, study, registry, _ = setup
    case = registry.resolve("case-0001")
    changed_registry = h.Registry((replace(case, truth=case.truth.model_copy(update={"classification": classification})),))
    changed = study.model_copy(update={"registry_sha256": changed_registry.sha256})
    harness, runtime = make(selected_study=changed, selected_registry=changed_registry), FakeRuntime()
    assert advance(harness, runtime=runtime) == "unsupported" and runtime.calls == []
    summary = records(harness)[0]
    assert summary.review.status == "excluded" and summary.review.reason == "not_eligible"
    assert classification.encode() not in harness.log.path.read_bytes()+harness.journal.path.read_bytes()


def test_admission_intent_is_durable_before_injected_effect(setup):
    make, _, _, _ = setup
    harness, runtime = make(), FakeRuntime()
    def submit(call):
        # Inspect the bounded committed line as a fake runtime observation;
        # public readers intentionally cannot reenter the held exclusive lock.
        line = harness.journal.path.read_bytes().splitlines()[-1]
        event = h.ledger_json(line)["event"]
        assert event["kind"] == "intent" and event["operation_id"] == call.dispatch_id
        assert event["reserve"]["attempts"] == 1
        return runtime.execute(call.dispatch_id, call.experiment, revision=call.revision, contract=call.contract)
    adapter = h.ControlledRuntimeAdapter(evidence_kind="mock", submit=submit)
    assert advance(harness, runtime=adapter) == "progress"
    assert len(runtime.calls) == 1


def test_neutral_proposal_adapter_carries_frozen_policy_without_private_binding(setup):
    make, _, _, _ = setup
    harness, provider, calls = make("meta_one_pass"), FakeProvider(), []
    def invoke(call):
        calls.append(call)
        assert b"fifo_reset_bug" not in h.encoded(call) and b"PRIVATE" not in h.encoded(call)
        assert call.request_sha256 == h.digest(h.encoded(call.request))
        return provider.propose(call.call_id, call.request, timeout_seconds=call.timeout_seconds)
    adapter = h.ControlledProposalAdapter(evidence_kind="mock", requested_model=provider.requested_model,
        model_identity_sha256=H, generation_parameters_sha256=H, invoke=invoke)
    assert advance(harness, provider=adapter) == "progress" and len(calls) == 1


def test_duplicate_feedback_proposal_is_preserved_as_rejected_not_reexecuted(setup):
    make, _, _, _ = setup
    harness, provider, runtime = make("meta_feedback"), FakeProvider(), FakeRuntime()
    advance(harness, provider=provider)
    advance(harness, runtime=runtime)
    advance(harness, provider=provider)
    rows = records(harness)
    assert rows[-1].status == "rejected" and rows[-1].response and rows[-1].accepted_plan is None
    assert len(runtime.calls) == 1
    assert len(provider.calls[-1][1].feedback) == 1


def test_adapter_identity_mismatch_blocks_before_invocation(setup):
    make, _, _, _ = setup
    harness, provider = make("meta_feedback"), FakeProvider()
    provider.model_identity_sha256 = "b"*64
    with pytest.raises(h.HarnessError, match="adapter_identity_invalid"):
        advance(harness, provider=provider)
    assert provider.calls == [] and len(harness.read()[0]) == 1


@pytest.mark.parametrize("field", ["environment_sha256", "source_sha256", "contract_sha256", "experiment_sha256"])
def test_changed_runtime_identity_is_not_attached_to_frozen_scope(setup, field):
    make, _, _, _ = setup
    harness, base = make(), FakeRuntime()
    class Changed:
        evidence_kind = "mock"
        def execute(self, *args, **kwargs):
            return base.execute(*args, **kwargs).model_copy(update={field: "b"*64})
    with pytest.raises(h.HarnessError, match="effect_unsettled_review_required"):
        advance(harness, runtime=Changed())
    assert records(harness) == ()


def test_invalid_journal_errors_do_not_echo_injected_private_text(setup):
    make, _, _, _ = setup
    harness, runtime = make(), FakeRuntime()
    old_head = harness.journal.read()[1]
    value = h.ledger_json(harness.journal.path.read_bytes())
    value["event"]["study"]["invalid"] = "SECRET fifo_reset_bug PRIVATE_WITNESS.json"
    value["sha256"] = h.digest(h.encoded({key: value[key] for key in ("sequence", "previous", "event")}))
    harness.journal.path.write_bytes(h.encoded(value)+b"\n")
    with pytest.raises(h.HarnessError, match="^storage_or_protocol_invalid$"):
        harness.advance(expected_head=old_head, runtime=runtime)
    assert runtime.calls == []


def test_artifact_cap_is_checked_before_captured_blob_writes(setup):
    make, study, _, _ = setup
    changed = study.model_copy(update={"limits": study.limits.model_copy(update={"per_attempt_artifact_bytes": 1024})})
    harness, runtime = make(selected_study=changed), FakeRuntime()
    with pytest.raises(h.HarnessError, match="effect_unsettled_review_required"):
        advance(harness, runtime=runtime)
    assert list(harness.blobs.root.glob("blob-*.bin")) == []


def test_model_token_reservation_and_response_bytes_are_bounded(setup):
    make, study, _, _ = setup
    changed = study.model_copy(update={"limits": study.limits.model_copy(update={"model_tokens": 1700})})
    harness, provider = make("meta_feedback", selected_study=changed), FakeProvider()
    # Either the first bounded call fits or it is blocked before invocation;
    # further rounds may never exceed the frozen aggregate reservation.
    for _ in range(5):
        status = advance(harness, provider=provider, runtime=FakeRuntime())
        if status != "progress": break
    assert harness.reserved(harness.read()[0]).model_tokens <= 1700
    assert len(provider.calls) <= 1


def test_backward_resume_clock_stops_before_another_effect(setup):
    make, _, _, clock = setup
    harness, runtime = make("constrained_random"), FakeRuntime()
    clock.at += timedelta(seconds=10)
    advance(harness, runtime=runtime)
    clock.at -= timedelta(seconds=5)
    with pytest.raises(h.HarnessError, match="clock_invalid"):
        advance(harness, runtime=runtime)
    assert len(runtime.calls) == 1


def test_measurement_projection_rejects_unjournaled_rows(setup):
    make, _, _, _ = setup
    harness, runtime = make(), FakeRuntime()
    advance(harness, runtime=runtime)
    entry = harness.sync(harness.read()[0])[0]
    rogue_result = entry.record.attempt.model_copy(update={"attempt_id": "rogue-attempt"})
    rogue = entry.record.model_copy(update={"record_id": "rogue-record", "attempt": rogue_result})
    harness.log.append(rogue, expected_head=entry.sha256)
    with pytest.raises(h.HarnessError, match="ledger_projection_differs"):
        advance(harness, runtime=runtime)
    assert len(runtime.calls) == 1


def test_mock_and_actual_adapter_policies_cannot_silently_switch_mid_trial(setup):
    make, study, _, _ = setup
    harness, runtime = make("constrained_random"), FakeRuntime()
    advance(harness, runtime=runtime)
    with pytest.raises(h.HarnessError, match="adapter_identity_invalid"):
        advance(harness, runtime=FakeRuntime(evidence="actual"))
    assert len(runtime.calls) == 1
    assert canonical_hash(study) != canonical_hash(study.model_copy(update={"tool_evidence_kind": "actual"}))
    assert canonical_hash(study) != canonical_hash(study.model_copy(update={"cohort_id": "study-20261031-0002"}))


def test_known_returned_model_mismatch_cannot_feed_tool_execution(setup):
    make, _, _, _ = setup
    harness, provider = make("meta_feedback"), FakeProvider(returned="unexpected-other-model")
    with pytest.raises(h.HarnessError, match="effect_unsettled_review_required"):
        advance(harness, provider=provider)
    assert records(harness) == ()


def test_full_declared_seed_range_survives_frozen_scheduling(setup):
    make, study, _, _ = setup
    changed = study.model_copy(update={"seeds": (2**32-1,)})
    harness = make("constrained_random", selected_study=changed)
    assert h.matrix(changed)[0].seed == 2**32-1
    assert harness.proposal(0).seed == 2**32-1
    advance(harness, runtime=FakeRuntime())
    assert records(harness)[0].scope.seed == 2**32-1


def test_public_case_and_observation_collections_have_exact_bounds():
    with pytest.raises(ValidationError):
        h.PublicCase(case_id="case-0001", parameters={}, objective_ids=("FIFO_BEHAVIOR", "FIFO_BEHAVIOR"))
    assert h.ObservedRow(cycle=0, data=2**64-1, empty=False, full=False).data == 2**64-1
    with pytest.raises(ValidationError): h.ObservedRow(cycle=0, data=2**64, empty=False, full=False)
