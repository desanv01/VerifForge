"""EDA report byte-closure contracts. Wire data never grants host authority."""
from typing import Annotated, Literal
from pydantic import Field, StrictInt, model_validator
from veriforge.contracts import Identifier, Sha256, WireModel
from veriforge.contracts.engineering import EvidenceReference
from veriforge.system_investigation.records import EngineeringProjectRevision

class EdaSource(WireModel):
    name: Annotated[str, Field(pattern=r"^[A-Za-z0-9_.-]{1,128}$")]
    reference: EvidenceReference

class EdaRawReport(WireModel):
    name: Annotated[str, Field(pattern=r"^[a-z_]+(?:\.rpt|\.json)$")]
    reference: EvidenceReference
    source_refs: Annotated[tuple[EvidenceReference, ...], Field(min_length=1,max_length=64)]

class EdaRun(WireModel):
    project: EngineeringProjectRevision
    sources: Annotated[tuple[EdaSource, ...], Field(min_length=1,max_length=64)]
    configuration: EvidenceReference
    receipt: EvidenceReference
    reports: Annotated[tuple[EdaRawReport, ...], Field(min_length=1,max_length=16)]

    @model_validator(mode="after")
    def closure(self):
        if len({s.name for s in self.sources})!=len(self.sources) or len({r.name for r in self.reports})!=len(self.reports):
            raise ValueError("duplicate EDA source/report role")
        refs=[s.reference for s in self.sources]
        if len({r.model_dump_json() for r in refs})!=len(refs) or set(r.model_dump_json() for r in refs)!=set(r.model_dump_json() for r in self.project.source_refs):
            raise ValueError("exact project source closure required")
        if self.configuration.sha256!=self.project.configuration_hash:
            raise ValueError("project configuration identity differs")
        for report in self.reports:
            if len({r.model_dump_json() for r in report.source_refs})!=len(report.source_refs) or any(r not in refs for r in report.source_refs):
                raise ValueError("report source association differs")
        return self

class MappingAssertion(WireModel):
    reference: Identifier
    symbol_pin: Identifier
    footprint_pad: Identifier
    net: Annotated[str, Field(min_length=1,max_length=160)]
    manufacturer: Annotated[str, Field(min_length=1,max_length=160)]
    mpn: Annotated[str, Field(min_length=1,max_length=160)]
    footprint: Annotated[str, Field(min_length=1,max_length=160)]
    datasheet: EvidenceReference
    datasheet_location: Annotated[str, Field(min_length=1,max_length=256)]

class EdaRequirements(WireModel):
    schema_version: Literal["engineering.eda-requirements.v1"]="engineering.eda-requirements.v1"
    domain: Literal["vivado","kicad"]
    mappings: Annotated[tuple[MappingAssertion,...],Field(max_length=128)]=()
    part: str | None=None
    top: str | None=None
    clock_period_ps: Annotated[StrictInt,Field(gt=0,lt=10**15)] | None=None
    recipe: EvidenceReference | None=None

    @model_validator(mode="after")
    def supported(self):
        if self.domain=="vivado":
            if self.mappings or self.part!="xc7a200tsbg484-1" or self.top!="exercise3_nexys_video_top" or self.clock_period_ps is None or self.recipe is None:
                raise ValueError("unsupported Vivado requirement profile")
        elif any(v is not None for v in (self.part,self.top,self.clock_period_ps,self.recipe)):
            raise ValueError("KiCad cannot carry Vivado requirements")
        if len({(m.reference,m.symbol_pin) for m in self.mappings})!=len(self.mappings):
            raise ValueError("ambiguous mapping assertions")
        return self

class EdaConfiguration(WireModel):
    schema_version: Literal["engineering.eda-configuration.v1"]="engineering.eda-configuration.v1"
    domain: Literal["vivado","kicad"]
    profile: Literal["vivado.exercise3.v1","kicad10.configured.v1"]
    ignored_checks: dict[Literal["erc","drc"],tuple[Identifier,...]]=Field(default_factory=dict)
    included_severities: dict[Literal["erc","drc"],tuple[Literal["warning","error","exclusion"],...]]=Field(default_factory=dict)

    @model_validator(mode="after")
    def profile_matches(self):
        if self.profile!=("vivado.exercise3.v1" if self.domain=="vivado" else "kicad10.configured.v1"):
            raise ValueError("EDA configuration profile differs")
        if self.domain=="vivado" and (self.ignored_checks or self.included_severities):raise ValueError("unexpected Vivado rule configuration")
        if sum(map(len,self.ignored_checks.values()))>256:raise ValueError("ignored rule bound")
        return self

class EdaInvestigation(WireModel):
    schema_version: Literal["engineering.eda-investigation.v1"]="engineering.eda-investigation.v1"
    investigation_id: Identifier
    domain: Literal["vivado","kicad"]
    requirements: EvidenceReference
    candidate: EdaRun
    baseline: EdaRun | None=None

    @model_validator(mode="after")
    def candidate_lineage(self):
        if self.baseline:
            if self.baseline.project.project_id!=self.candidate.project.project_id or self.baseline.project.revision_id==self.candidate.project.revision_id:
                raise ValueError("distinct same-project baseline/candidate revisions required")
            if self.baseline.configuration!=self.candidate.configuration:
                raise ValueError("candidate configuration changed")
        return self

class EdaArtifactIdentity(WireModel):
    reference: EvidenceReference
    size_bytes: Annotated[StrictInt,Field(ge=0,le=1048576)]

class EdaAdmission(WireModel):
    schema_version: Literal["engineering.admission.v3"]="engineering.admission.v3"
    record: EdaInvestigation
    record_hash: Sha256
    checker_sha256: Sha256
    artifacts: Annotated[tuple[EdaArtifactIdentity,...],Field(max_length=192)]
    total_artifact_bytes: Annotated[StrictInt,Field(ge=0,le=16*1048576)]
    provenance: Literal["recorded_bytes_not_new_execution"]="recorded_bytes_not_new_execution"
    execution_status: Literal["not_observed"]="not_observed"
    design_verdict: Literal["inconclusive"]="inconclusive"
