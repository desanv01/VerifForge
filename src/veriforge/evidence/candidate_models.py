"""Precise candidate recommendation provenance, separate from source materialization."""
from typing import Annotated, Literal

from pydantic import Field, StrictBool, StrictInt, model_validator

from veriforge.contracts import Identifier, Sha256, WireModel
from veriforge.adapters import ValidatedExperiment

SafeLabel = Annotated[str, Field(pattern=r"^[A-Za-z0-9][A-Za-z0-9_./:-]{0,127}$")]


class ModelRecommendation(WireModel):
    provider: SafeLabel
    model: SafeLabel
    model_call_id: Identifier
    prompt_sha256: Sha256
    response_sha256: Sha256


class CandidateProvenance(WireModel):
    schema_version: Literal["candidate.provenance.v1"] = "candidate.provenance.v1"
    recommendation_origin: Literal["curated_catalog", "external_recommendation", "model_recommendation"] = "curated_catalog"
    materialization: Literal["curated_fixture_selection"] = "curated_fixture_selection"
    authorship_claim: Literal["curated_source_not_generated_patch"] = "curated_source_not_generated_patch"
    assertion_basis: Literal["controller_supplied_record"] = "controller_supplied_record"
    proposal_id: Identifier | None = None
    proposal_sha256: Sha256 | None = None
    external_reference_id: Identifier | None = None
    model_call: ModelRecommendation | None = None

    @model_validator(mode="after")
    def precise_origin(self):
        if self.recommendation_origin == "curated_catalog":
            if any(value is not None for value in (self.proposal_id, self.proposal_sha256,
                    self.external_reference_id, self.model_call)):
                raise ValueError("catalog selection cannot claim an external/model proposal")
        else:
            if self.proposal_id is None or self.proposal_sha256 is None:
                raise ValueError("external/model recommendation requires exact proposal identity")
            if self.recommendation_origin == "model_recommendation":
                if self.model_call is None or self.external_reference_id is not None:
                    raise ValueError("model recommendation requires model call identity only")
            elif self.external_reference_id is None or self.model_call is not None:
                raise ValueError("external recommendation requires an external reference only")
        return self


class CandidateReviewLimits(WireModel):
    max_source_bytes: Annotated[StrictInt, Field(ge=1, le=1_048_576)] = 262_144
    max_source_lines: Annotated[StrictInt, Field(ge=1, le=10_000)] = 4096
    max_diff_bytes: Annotated[StrictInt, Field(ge=1, le=4_194_304)] = 1_048_576


class ReviewedSource(WireModel):
    path: SafeLabel
    file_sha256: Sha256
    source_identity_sha256: Sha256
    size_bytes: Annotated[StrictInt, Field(ge=0)]
    fixture_id: Identifier
    curated_variant: Annotated[StrictInt, Field(ge=0)]


class CandidateSourceReview(WireModel):
    schema_version: Literal["candidate.source-review.v1"] = "candidate.source-review.v1"
    review_scope: Literal["source_and_curated_configuration_only"] = "source_and_curated_configuration_only"
    adapter_id: Identifier
    original_attempt_id: Identifier
    candidate_attempt_id: Identifier
    original_experiment_sha256: Sha256
    candidate_experiment_sha256: Sha256
    checker_sha256: Sha256
    contract_sha256: Sha256
    original_source: ReviewedSource
    candidate_source: ReviewedSource
    source_bytes_changed: StrictBool
    configuration_changed: StrictBool
    unified_source_diff: Annotated[str, Field(max_length=4_194_304)]
    unified_configuration_diff: Annotated[str, Field(max_length=4_194_304)]
    provenance: CandidateProvenance

    @model_validator(mode="after")
    def consistent_change_claims(self):
        source_changed = self.original_source.file_sha256 != self.candidate_source.file_sha256
        configuration_changed = (self.original_source.fixture_id != self.candidate_source.fixture_id
            or self.original_source.curated_variant != self.candidate_source.curated_variant)
        if self.source_bytes_changed != source_changed or bool(self.unified_source_diff) != source_changed:
            raise ValueError("source change flag/diff contradict recorded source byte hashes")
        if not source_changed and self.original_source.size_bytes != self.candidate_source.size_bytes:
            raise ValueError("identical source bytes cannot have different recorded sizes")
        if self.configuration_changed != configuration_changed or bool(self.unified_configuration_diff) != configuration_changed:
            raise ValueError("configuration change flag/diff contradict recorded fixture/variant identities")
        return self


class FamilyValidationCase(WireModel):
    role: Literal["witness", "neighbor", "regression"]
    label: SafeLabel
    # Resolve the union by its target; no input-supplied imports or runners.
    experiment: Annotated[ValidatedExperiment, Field(discriminator="target")]


class FamilyValidationSuite(WireModel):
    schema_version: Literal["family.validation-suite.v1"] = "family.validation-suite.v1"
    adapter_id: Identifier
    checker_sha256: Sha256
    contract_sha256: Sha256
    cases: Annotated[tuple[FamilyValidationCase, ...], Field(min_length=3, max_length=101)]

    @model_validator(mode="after")
    def exact_family_cases(self):
        from .families import descriptor_for, stimulus_identity
        first = self.cases[0].experiment
        descriptor = descriptor_for(first)
        if descriptor.adapter_id not in {"rtl.arithmetic.v1", "rtl.axi_lite.v1"} or self.adapter_id != descriptor.adapter_id:
            raise ValueError("repair suite requires a supported arithmetic or AXI family")
        if self.cases[0].role != "witness" or any(case.role == "witness" for case in self.cases[1:]):
            raise ValueError("repair suite requires exactly one leading witness")
        if not {"neighbor", "regression"} <= {case.role for case in self.cases}:
            raise ValueError("repair suite requires neighboring and regression checks")
        expected = descriptor.expected_identity(first)
        if (self.checker_sha256, self.contract_sha256) != (expected["checker_hash"], expected["contract_hash"]):
            raise ValueError("repair suite must retain the installed immutable checks")
        seen, labels = set(), set()
        for case in self.cases:
            plan = descriptor.validate_experiment(case.experiment)
            if (plan.parameters, plan.fixture_id, plan.contract_id, plan.objective_ids, plan.budget) != (
                    first.parameters, first.fixture_id, first.contract_id, first.objective_ids, first.budget):
                raise ValueError("repair cases cannot change the original scope or budgets")
            key = stimulus_identity(plan)
            if key in seen or case.label in labels:
                raise ValueError("repair suite cannot count duplicate stimulus or labels")
            seen.add(key)
            labels.add(case.label)
        return self
