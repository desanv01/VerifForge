"""Independent pure review. Fabricated provider/runner records are never live evidence."""
from decimal import Decimal
import hashlib

import pytest

from veriforge.contracts import (ArtifactReference, AttemptResult, BehavioralContract, CheckerIdentity,
    EvidenceIdentity, ProjectRevision, Requirement, ResourceUsage, SourceFile, canonical_hash)
from veriforge.planner.controller import InvestigationController
from veriforge.planner.models import (ExperimentProposal, InvestigationLimits, ModelUsage,
    PlannerDecision, Pricing, ProviderCompletion)
from veriforge.planner.steps import (PlannerStepError, build_context, build_experiment,
    prepare_model_call, validate_completion, validated_feedback)

SOURCE = "1" * 64
CHECKER = "2" * 64
CONTRACT = BehavioralContract(review_status="reviewed",
    checker=CheckerIdentity(checker_id="review-only", version="1", sha256=CHECKER),
    requirements=(Requirement(requirement_id="FIFO_REVIEW", description="Synthetic reviewed objective"),))
REVISION = ProjectRevision(project_id="review", revision_id="review-rev", source_hash=SOURCE,
    fixture_id="fifo_correct", sources=(SourceFile(path="review.sv", sha256=SOURCE),))
LIMITS = InvestigationLimits(max_rounds=2, max_total_cycles=12, max_cycles_per_experiment=6)


def proposal(seed=1):
    return ExperimentProposal(kind="experiment", objective_ids=("FIFO_REVIEW",), seed=seed,
                              operations=({"op": "reset"}, {"op": "write", "value": seed}))


def plan(seed=1):
    return build_experiment(proposal(seed), experiment_id="same-server-id", revision=REVISION,
        contract=CONTRACT, limits=LIMITS, reserved_cycles=0, remaining_wall_seconds=10.0)


def result(experiment, **changes):
    values = dict(attempt_id="fabricated-review-attempt", experiment_id=experiment.experiment_id,
        execution_status="completed", verdict="pass_within_scope",
        identity=EvidenceIdentity(source_hash=SOURCE, checker_hash=CHECKER,
            contract_hash=canonical_hash(CONTRACT), experiment_hash=canonical_hash(experiment),
            environment_hash="3" * 64), usage=ResourceUsage(cycles=experiment.cycle_count))
    values.update(changes)
    return AttemptResult(**values)


def complete(seed=1, *, model="mock-requested-model"):
    return ProviderCompletion(model=model, usage=ModelUsage(input_tokens=1, output_tokens=1),
        finish_reason="stop", content=PlannerDecision(proposal=proposal(seed)).model_dump_json())


class ImmediateProvider:
    model = "mock-requested-model"

    def __init__(self, effect=None):
        self.calls = 0
        self.effect = effect
        self.contexts = []

    async def complete(self, context, **kwargs):
        self.calls += 1
        self.contexts.append(context)
        if self.effect:
            self.effect(self)
        return complete(self.calls, model=self.model)


def controller(monkeypatch, provider, executor=None, **kwargs):
    async def fabricated_executor(experiment):
        return result(experiment)
    # Immediate fake operations have no suspension; no OS event loop, I/O or jobs.
    # This reviews controller boundaries; real _bounded cleanup is reviewed statically.
    async def direct(self, operation, timeout):
        assert timeout > 0
        return await operation
    monkeypatch.setattr(InvestigationController, "_bounded", direct)
    return InvestigationController(provider, executor or fabricated_executor, revision=REVISION,
                                    contract=CONTRACT, limits=kwargs.pop("limits", LIMITS), **kwargs)


def settle(coroutine):
    with pytest.raises(StopIteration) as stopped:
        coroutine.send(None)
    return stopped.value.value


@pytest.mark.parametrize("field", ["source_hash", "checker_hash", "contract_hash", "experiment_hash"])
def test_each_forged_provenance_component_stops_before_feedback(field):
    experiment = plan()
    record = result(experiment)
    forged = record.model_copy(update={"identity": record.identity.model_copy(update={field: "f" * 64})})
    with pytest.raises(PlannerStepError, match="result_identity_mismatch"):
        validated_feedback(forged, experiment=experiment, revision=REVISION, contract=CONTRACT)


def test_same_experiment_id_does_not_allow_replaying_a_different_plan():
    original, changed = plan(1), plan(2)
    assert original.experiment_id == changed.experiment_id
    with pytest.raises(PlannerStepError, match="result_identity_mismatch"):
        validated_feedback(result(original), experiment=changed, revision=REVISION, contract=CONTRACT)


def test_request_body_hash_and_exact_token_boundary_with_utf8_contract():
    contract = CONTRACT.model_copy(update={"assumptions": ("Register label \u03b1 has reviewed meaning",)})
    context = build_context(contract, LIMITS, reserved_cycles=0, feedback=())
    admitted = prepare_model_call(context, "mock", LIMITS, reserved_tokens=0, reserved_cost_usd=None, pricing=None)
    assert admitted.request_hash == hashlib.sha256(admitted.request_body).hexdigest()
    assert b"\xce\xb1" in admitted.request_body
    exact = LIMITS.model_copy(update={"max_model_tokens": admitted.reserved_tokens + 7})
    assert prepare_model_call(context, "mock", exact, reserved_tokens=7,
        reserved_cost_usd=None, pricing=None).reserved_tokens == admitted.reserved_tokens
    with pytest.raises(PlannerStepError, match="model_token_limit"):
        prepare_model_call(context, "mock", exact, reserved_tokens=8, reserved_cost_usd=None, pricing=None)


def test_exact_decimal_cost_boundary_and_unknown_price_fail_closed():
    context = build_context(CONTRACT, LIMITS, reserved_cycles=0, feedback=())
    pricing = Pricing(input_usd_per_million=Decimal("0.123"), output_usd_per_million=Decimal("0.456"))
    admitted = prepare_model_call(context, "mock", LIMITS, reserved_tokens=0,
                                 reserved_cost_usd=Decimal(0), pricing=pricing)
    cost = admitted.reserved_cost_usd
    limited = LIMITS.model_copy(update={"max_cost_usd": cost + Decimal("0.01")})
    assert prepare_model_call(context, "mock", limited, reserved_tokens=0,
        reserved_cost_usd=Decimal("0.01"), pricing=pricing).reserved_cost_usd == cost
    with pytest.raises(PlannerStepError, match="cost_limit"):
        prepare_model_call(context, "mock", limited, reserved_tokens=0,
                           reserved_cost_usd=Decimal("0.0100000001"), pricing=pricing)
    with pytest.raises(PlannerStepError, match="price_unknown"):
        prepare_model_call(context, "mock", limited, reserved_tokens=0, reserved_cost_usd=None, pricing=None)


def test_nonfinite_reserved_cost_rejected():
    context = build_context(CONTRACT, LIMITS, reserved_cycles=0, feedback=())
    pricing = Pricing(input_usd_per_million=Decimal(1), output_usd_per_million=Decimal(1))
    for bad in (Decimal("NaN"), Decimal("Infinity"), Decimal("-0.01")):
        with pytest.raises(PlannerStepError, match="planner_state_invalid"):
            prepare_model_call(context, "mock", LIMITS, reserved_tokens=0, reserved_cost_usd=bad, pricing=pricing)


def test_completed_non_design_error_without_identity_is_not_functional_feedback():
    experiment = plan()
    record = result(experiment, verdict="harness_error", identity=None)
    feedback = validated_feedback(record, experiment=experiment, revision=REVISION, contract=CONTRACT)
    assert feedback.verdict == "harness_error" and feedback.failure_signature is None


def test_db_artifact_mapping_cannot_swap_missing_or_extra_runner_ids():
    experiment = plan()
    artifact = ArtifactReference(artifact_id="runner-id", path="trace.json", sha256="4" * 64,
                                 size_bytes=1, media_type="application/json")
    record = result(experiment, artifacts=(artifact,))
    for mapping in ({"runner-id": "db-id", "extra": "db-other"}, {"stale-id": "db-id"}):
        with pytest.raises(PlannerStepError, match="feedback_artifact_mismatch"):
            validated_feedback(record, experiment=experiment, revision=REVISION,
                               contract=CONTRACT, artifact_ids=mapping)


def test_boundary_cancel_after_provider_keeps_reservation_and_never_executes(monkeypatch):
    provider = ImmediateProvider()
    subject = controller(monkeypatch, provider)
    provider.effect = lambda _: subject.cancel()
    report = settle(subject.run())
    assert report.stop_reason == "cancelled" and report.experiments == report.results == ()
    assert len(report.model_calls) == 1 and report.reserved_model_tokens > 0
    assert report.reserved_cycles == 0


def test_boundary_stop_after_executor_preserves_exact_result_and_no_next_round(monkeypatch):
    provider = ImmediateProvider()
    async def executor(experiment):
        subject.stop()
        return result(experiment)
    subject = controller(monkeypatch, provider, executor)
    report = settle(subject.run())
    assert report.stop_reason == "stopped" and len(report.results) == len(report.experiments) == 1
    assert provider.calls == 1 and report.reserved_cycles == LIMITS.max_cycles_per_experiment
    assert report.results[0].identity.experiment_hash == canonical_hash(report.experiments[0])


def test_provenance_rejection_halts_without_accepting_untrusted_result(monkeypatch):
    async def executor(experiment):
        return result(experiment, experiment_id="stale-previous-round")
    subject = controller(monkeypatch, ImmediateProvider(), executor)
    report = settle(subject.run())
    assert report.stop_reason == "result_identity_mismatch" and not report.results
    assert len(report.experiments) == 1 and report.reserved_cycles == 6


def test_fully_reserved_cycles_are_not_refunded_after_short_pass(monkeypatch):
    provider = ImmediateProvider()
    limits = LIMITS.model_copy(update={"max_total_cycles": 6})
    report = settle(controller(monkeypatch, provider, limits=limits).run())
    assert report.stop_reason == "cycle_limit" and provider.calls == 1
    assert report.reserved_cycles == 6 and report.results[0].usage.cycles == 2


def test_pause_before_provider_and_single_use_controller(monkeypatch):
    provider = ImmediateProvider()
    subject = controller(monkeypatch, provider, control=lambda: "PAUSED")
    report = settle(subject.run())
    assert report.stop_reason == "paused" and provider.calls == report.reserved_model_tokens == 0
    with pytest.raises(RuntimeError, match="single-use"):
        subject.run().send(None)


def test_returned_model_is_bound_to_admitted_model_not_mutable_provider_property(monkeypatch):
    provider = ImmediateProvider(effect=lambda source: setattr(source, "model", "different-model"))
    report = settle(controller(monkeypatch, provider).run())
    assert report.model_calls[0].model == "mock-requested-model"
    assert report.stop_reason == "model_identity_mismatch"
    assert not report.experiments and not report.results
