"""Pure step acceptance with fabricated records. No provider, runner or clock calls."""

from decimal import Decimal
import hashlib
import json

import pytest
from pydantic import ValidationError

from veriforge.contracts import (
    ArtifactReference, AttemptResult, BehavioralContract, CheckerIdentity, EvidenceIdentity,
    Experiment, FifoParameters, ProjectRevision, Requirement, ResourceUsage, SourceFile, canonical_hash,
)
from veriforge.planner.models import (
    ExperimentProposal, Feedback, InvestigationLimits, ModelUsage, PlannerDecision, Pricing, ProviderCompletion,
)
from veriforge.planner.provider import encode_payload, request_payload
from veriforge.planner.steps import (
    PLANNER_CAPABILITIES, STEPS_VERSION, DUPLICATE_CORRECTION, INVALID_CORRECTION,
    PlannerStepError, admission_stop_reason, build_context, build_experiment,
    control_stop_reason, decision_stop_reason, execution_stop_reason, prepare_model_call, proposal_fingerprint,
    proposal_stop_reason, require_planner_capability, transient_retry_allowed,
    validate_completion, validate_result, validated_feedback,
)

HASH = "a" * 64
CONTRACT = BehavioralContract(review_status="reviewed",
    checker=CheckerIdentity(checker_id="fake-checker", version="1", sha256=HASH),
    requirements=(Requirement(requirement_id="ORDER", description="Retain FIFO order"),))
REVISION = ProjectRevision(project_id="test", revision_id="r1", source_hash=HASH,
    fixture_id="fifo_correct", sources=(SourceFile(path="fifo.sv", sha256=HASH),))
PROPOSAL = ExperimentProposal(kind="experiment", objective_ids=("ORDER",), seed=31,
    operations=({"op": "reset"}, {"op": "write", "value": 5}, {"op": "read"}))


def make_experiment(**changes):
    arguments = dict(experiment_id="server-owned-1", revision=REVISION, contract=CONTRACT,
                     limits=InvestigationLimits(), reserved_cycles=0, remaining_wall_seconds=90.0)
    arguments.update(changes)
    return build_experiment(PROPOSAL, **arguments)


def make_result(experiment=None, **changes):
    plan = experiment or make_experiment()
    arguments = dict(attempt_id="fabricated-attempt", experiment_id=plan.experiment_id,
        execution_status="completed", verdict="pass_within_scope",
        identity=EvidenceIdentity(source_hash=HASH, checker_hash=HASH,
            contract_hash=canonical_hash(CONTRACT), experiment_hash=canonical_hash(plan), environment_hash=HASH),
        requirement_coverage=("ORDER",), usage=ResourceUsage(cycles=3))
    arguments.update(changes)
    return AttemptResult(**arguments)


@pytest.mark.parametrize("adapter", ["rtl.arithmetic.v1", "rtl.axi_lite.v1", "vivado.v1", "unknown", None])
def test_execution_family_is_not_planner_support(adapter):
    with pytest.raises(PlannerStepError, match="planner_unsupported"):
        require_planner_capability(adapter)
    with pytest.raises(PlannerStepError, match="planner_unsupported"):
        build_experiment(PROPOSAL, experiment_id="server", revision=REVISION, contract=CONTRACT,
                         limits=InvestigationLimits(), reserved_cycles=0, remaining_wall_seconds=1,
                         adapter_id=adapter)


def test_fifo_capability_frozen_and_schema_identity_unchanged():
    assert tuple(PLANNER_CAPABILITIES) == ("rtl.fifo.v1",)
    capability = require_planner_capability("rtl.fifo.v1")
    assert capability.steps_version == STEPS_VERSION == "planner.steps.v1"
    assert capability.prompt_version == "vf-planner-1"
    assert capability.decision_schema_hash == hashlib.sha256(
        json.dumps(PlannerDecision.model_json_schema(), sort_keys=True).encode()).hexdigest()
    with pytest.raises(TypeError):
        PLANNER_CAPABILITIES["rtl.arithmetic.v1"] = capability
    with pytest.raises(ValidationError):
        capability.adapter_id = "rtl.arithmetic.v1"


def test_context_window_cycles_correction_and_identity_do_not_mutate():
    limits = InvestigationLimits(max_feedback_items=2, max_total_cycles=100, max_cycles_per_experiment=50)
    feedback = tuple(Feedback(experiment_id=str(i), execution_status="completed", verdict="pass_within_scope",
                             coverage=("ORDER",), failure_signature=None, artifact_ids=()) for i in range(5))
    context = build_context(CONTRACT, limits, reserved_cycles=80, feedback=feedback, correction=INVALID_CORRECTION)
    assert context.remaining_cycles == context.max_cycles_per_experiment == 20
    assert context.feedback == feedback[-2:] and len(feedback) == 5
    assert context.contract == CONTRACT and context.correction == INVALID_CORRECTION
    assert "fifo_correct" not in context.model_dump_json()
    with pytest.raises(PlannerStepError, match="correction_invalid"):
        build_context(CONTRACT, limits, reserved_cycles=0, feedback=(), correction="raw invalid model text")


def test_request_reservation_keeps_exact_payload_and_no_ledger_side_effects():
    limits = InvestigationLimits()
    context = build_context(CONTRACT, limits, reserved_cycles=0, feedback=())
    admission = prepare_model_call(context, "fake", limits, reserved_tokens=0, reserved_cost_usd=None, pricing=None)
    expected = encode_payload(request_payload(context, "fake", limits.max_output_tokens))
    assert admission.request_body == expected
    assert admission.request_hash == hashlib.sha256(expected).hexdigest()
    assert admission.input_allowance == len(expected) + 1024
    assert admission.reserved_tokens == admission.input_allowance + limits.max_output_tokens
    assert admission.reserved_cost_usd is None
    assert "request_body" not in admission.model_dump()  # Explicit private snapshot access only.
    assert "request_body" not in repr(admission)
    tight = limits.model_copy(update={"max_model_tokens": admission.reserved_tokens * 2 - 1})
    with pytest.raises(PlannerStepError, match="model_token_limit"):
        prepare_model_call(context, "fake", tight, reserved_tokens=admission.reserved_tokens,
                           reserved_cost_usd=None, pricing=None)
    again = prepare_model_call(context, "fake", limits, reserved_tokens=0, reserved_cost_usd=None, pricing=None)
    assert again == admission  # No hidden counter or reservation mutation.


def test_admission_cannot_bypass_bounded_context_constructor():
    limits = InvestigationLimits()
    context = build_context(CONTRACT, limits, reserved_cycles=0, feedback=())
    tampered = context.model_copy(update={"max_cycles_per_experiment": limits.max_cycles_per_experiment + 1})
    with pytest.raises(PlannerStepError, match="planner_context_invalid"):
        prepare_model_call(tampered, "fake", limits, reserved_tokens=0, reserved_cost_usd=None, pricing=None)


def test_price_unknown_or_supplied_rates_with_prior_reservations():
    limits = InvestigationLimits(max_cost_usd=Decimal("0.1"))
    context = build_context(CONTRACT, limits, reserved_cycles=0, feedback=())
    with pytest.raises(PlannerStepError, match="price_unknown"):
        prepare_model_call(context, "fake", limits, reserved_tokens=0, reserved_cost_usd=None, pricing=None)
    pricing = Pricing(input_usd_per_million=Decimal(1), output_usd_per_million=Decimal(2))
    reservation = prepare_model_call(context, "fake", limits, reserved_tokens=0,
                                     reserved_cost_usd=Decimal(0), pricing=pricing)
    assert reservation.reserved_cost_usd == pricing.cost(reservation.input_allowance, limits.max_output_tokens)
    with pytest.raises(PlannerStepError, match="cost_limit"):
        prepare_model_call(context, "fake", limits, reserved_tokens=0,
                           reserved_cost_usd=Decimal("0.1"), pricing=pricing)


@pytest.mark.parametrize("change,outcome", [
    ({"usage": None}, "model_usage_unknown"),
    ({"usage": ModelUsage(input_tokens=101, output_tokens=1)}, "model_usage_limit_violated"),
    ({"model": "different"}, "model_identity_mismatch"),
    ({"finish_reason": "length"}, "invalid_proposal"),
    ({"content": "secret-like-invalid-reply"}, "invalid_proposal"),
    ({"content": '{"proposal":{"kind":"experiment","command":"shell"}}'}, "invalid_proposal"),
])
def test_completion_codes_never_echo_invalid_reply(change, outcome):
    arguments = dict(model="fake", usage=ModelUsage(input_tokens=1, output_tokens=1),
                     finish_reason="stop", content=PlannerDecision(proposal=PROPOSAL).model_dump_json())
    arguments.update(change)
    result = validate_completion(ProviderCompletion(**arguments), requested_model="fake", input_allowance=100,
                                 limits=InvestigationLimits())
    assert result.outcome == outcome and result.decision is None
    assert "secret-like-invalid-reply" not in result.model_dump_json()


def test_legal_experiment_binding_seed_width_budget_and_fingerprint():
    plan = make_experiment(limits=InvestigationLimits(max_total_cycles=30, max_cycles_per_experiment=20),
                           reserved_cycles=23, remaining_wall_seconds=4.5)
    assert plan.experiment_id == "server-owned-1" and plan.fixture_id == REVISION.fixture_id
    assert plan.parameters == CONTRACT.parameters and plan.seed == 31
    assert plan.budget.max_cycles == 7 and plan.budget.wall_time_seconds == 4.5
    assert plan.operations == PROPOSAL.operations
    assert proposal_fingerprint(PROPOSAL) == canonical_hash(PROPOSAL)
    assert proposal_fingerprint(PROPOSAL.model_copy(update={"seed": 32})) != proposal_fingerprint(PROPOSAL)
    changed = CONTRACT.model_copy(update={"parameters": FifoParameters(width=2)})
    with pytest.raises(PlannerStepError, match="invalid_proposal"):
        make_experiment(contract=changed)


@pytest.mark.parametrize("changes", [
    {"objective_ids": ("UNREVIEWED",)}, {"operations": ({"op": "read"},)},
    {"operations": ({"op": "reset", "cycles": 10001},)},
])
def test_invalid_legal_space_rejected_after_structural_decoding(changes):
    data = PROPOSAL.model_dump(mode="json")
    data.update(changes)
    proposed = ExperimentProposal.model_validate(data)
    with pytest.raises(PlannerStepError, match="invalid_proposal"):
        build_experiment(proposed, experiment_id="server", revision=REVISION, contract=CONTRACT,
                         limits=InvestigationLimits(), reserved_cycles=0, remaining_wall_seconds=10)


@pytest.mark.parametrize("field", ["source_hash", "checker_hash", "contract_hash", "experiment_hash"])
def test_feedback_rejects_each_provenance_mismatch(field):
    result = make_result()
    changed = result.model_copy(update={"identity": result.identity.model_copy(update={field: "b" * 64})})
    with pytest.raises(PlannerStepError, match="result_identity_mismatch"):
        validated_feedback(changed, experiment=make_experiment(), revision=REVISION, contract=CONTRACT)


@pytest.mark.parametrize("changes,code", [
    ({"experiment_id": "other"}, "result_identity_mismatch"),
    ({"requirement_coverage": ("UNKNOWN",)}, "result_requirement_mismatch"),
    ({"usage": ResourceUsage(cycles=10001)}, "executor_cycle_limit_violated"),
])
def test_feedback_cannot_bypass_result_requirements_cycles_or_id(changes, code):
    with pytest.raises(PlannerStepError, match=code):
        validated_feedback(make_result(**changes), experiment=make_experiment(), revision=REVISION, contract=CONTRACT)


def test_feedback_maps_only_exact_trusted_artifact_ids_and_requires_finalization():
    artifact = ArtifactReference(artifact_id="runner-0", path="trace.json", sha256=HASH, size_bytes=4,
                                 media_type="application/json")
    result = make_result(artifacts=(artifact,))
    feedback = validated_feedback(result, experiment=make_experiment(), revision=REVISION, contract=CONTRACT,
                                   artifact_ids={"runner-0": "db-artifact-uuid"})
    assert feedback.artifact_ids == ("db-artifact-uuid",)
    for mapping in ({}, {"runner-0": "../private/path"}, {"runner-0": "id\nraw-text"}, {"wrong-runner": "db-id"}):
        with pytest.raises(PlannerStepError, match="feedback_artifact_mismatch"):
            validated_feedback(result, experiment=make_experiment(), revision=REVISION, contract=CONTRACT,
                               artifact_ids=mapping)
    pending = AttemptResult(attempt_id="test", experiment_id=make_experiment().experiment_id,
                            execution_status="running")
    with pytest.raises(PlannerStepError, match="feedback_not_finalized"):
        validated_feedback(pending, experiment=make_experiment(), revision=REVISION, contract=CONTRACT)


def test_context_binding_of_result_cannot_be_substituted():
    plan = make_experiment()
    wrong = plan.model_copy(update={"fixture_id": "fifo_reset_bug"})
    result = make_result(wrong)
    with pytest.raises(PlannerStepError, match="result_identity_mismatch"):
        validate_result(result, experiment=wrong, revision=REVISION, contract=CONTRACT)


@pytest.mark.parametrize("rounds,cycles,seconds,reason", [
    (15, 0, 1, "round_limit"), (0, 0, 0, "wall_time_limit"),
    (0, 100000, 1, "cycle_limit"), (0, 0, 1, None),
])
def test_stateless_admission_stop_priorities(rounds, cycles, seconds, reason):
    assert admission_stop_reason(InvestigationLimits(), rounds_used=rounds, reserved_cycles=cycles,
                                  remaining_wall_seconds=seconds) == reason


def test_control_cumulative_corrections_retry_and_non_design_outcomes():
    limits = InvestigationLimits()
    assert control_stop_reason(control="PAUSED", cancelled=True, stopped=True) == "cancelled"
    assert control_stop_reason(control="PAUSED", cancelled=False, stopped=True) == "stopped"
    assert control_stop_reason(control="PAUSED", cancelled=False, stopped=False) == "paused"
    assert proposal_stop_reason(limits, invalid_proposals=2, duplicate_proposals=0) == "invalid_proposal_limit"
    assert proposal_stop_reason(limits, invalid_proposals=0, duplicate_proposals=2) == "no_progress"
    assert transient_retry_allowed(limits, transient_errors=1)
    assert not transient_retry_allowed(limits, transient_errors=2)
    timeout = make_result(verdict="timeout")
    assert execution_stop_reason(timeout, limits) == "execution_blocked"
    assert timeout.failure_signature is None
    assert execution_stop_reason(make_result(), limits) is None
    assert decision_stop_reason(PlannerDecision(proposal=PROPOSAL)) is None
    assert decision_stop_reason(PlannerDecision.model_validate_json(
        '{"proposal":{"kind":"stop","reason":"bounded objective finished"}}')) == "model_stopped"


@pytest.mark.parametrize("bad", [-1, True, 1.5])
def test_invalid_counter_state_never_admits(bad):
    with pytest.raises(PlannerStepError, match="planner_state_invalid"):
        admission_stop_reason(InvestigationLimits(), rounds_used=bad, reserved_cycles=0, remaining_wall_seconds=1)


def test_clock_is_explicit_finite_and_legacy_normalizer_is_not_admission():
    for bad in (float("nan"), float("inf")):
        with pytest.raises(PlannerStepError, match="planner_state_invalid"):
            make_experiment(remaining_wall_seconds=bad)
    # Legacy construction clamp preserved; caller must separately reject expired admission.
    assert make_experiment(remaining_wall_seconds=-1).budget.wall_time_seconds == 0.001
    assert admission_stop_reason(InvestigationLimits(), rounds_used=0, reserved_cycles=0,
                                 remaining_wall_seconds=-1) == "wall_time_limit"
