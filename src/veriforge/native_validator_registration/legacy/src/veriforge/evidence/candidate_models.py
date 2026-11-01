"""Precise candidate recommendation provenance, separate from source materialization."""
from typing import Annotated, Literal

from pydantic import Field, StrictBool, StrictInt, model_validator

from veriforge.contracts import Identifier, Sha256, WireModel

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
