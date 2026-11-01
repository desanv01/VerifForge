"""Pure EDA admission/projection; trusted service supplies authority and bytes.

No filesystem resolution, tool invocation, review authentication service or store.
Factories below are host-only; never feed public request claims into them.
"""
from dataclasses import dataclass
from hashlib import sha256
import json
from pathlib import Path

from veriforge.contracts import canonical_hash
from veriforge.contracts.eda_tools import EdaAdmission, EdaArtifactIdentity, EdaInvestigation, EdaRequirements, EdaConfiguration
from veriforge.contracts.engineering import EvidenceReference
from .records import ArtifactBytes

_SEAL=object()

def implementation_hash():
    from veriforge.contracts import eda_tools
    from veriforge.kicad import investigation as ki, facts as kf, reports as kr
    from veriforge.vivado import investigation as vi, readiness as vr
    modules=(eda_tools,ki,kf,kr,vi,vr)
    pins={m.__name__:sha256(Path(m.__file__).read_bytes()).hexdigest() for m in modules}
    pins[__name__]=sha256(Path(__file__).read_bytes()).hexdigest()
    return sha256(encode(pins)).hexdigest()

def encode(value):
    if hasattr(value,"model_dump"): value=value.model_dump(mode="json")
    return json.dumps(value,sort_keys=True,separators=(",",":"),ensure_ascii=False,allow_nan=False).encode()

def _json(raw):
    def unique(pairs):
        out={}
        for k,v in pairs:
            if k in out: raise ValueError("duplicate EDA JSON key")
            out[k]=v
        return out
    return json.loads(raw,object_pairs_hook=unique,parse_constant=lambda _:(_ for _ in ()).throw(ValueError("nonfinite EDA JSON")))

@dataclass(frozen=True,init=False)
class EdaAuthority:
    record_hash: str
    reviewed_by: str
    _seal: object
    def __new__(cls,*args,**kwargs):
        raise TypeError("trusted EDA service factory required")

def authorize_eda(record: EdaInvestigation, *, reviewed_by: str):
    """Host pins already reviewed artifact associations; no client auth endpoint."""
    from pydantic import TypeAdapter
    from veriforge.contracts import Identifier
    reviewed_by=TypeAdapter(Identifier).validate_python(reviewed_by)
    record=EdaInvestigation.model_validate_json(record.model_dump_json())
    value=object.__new__(EdaAuthority)
    for key,item in dict(record_hash=canonical_hash(record),reviewed_by=reviewed_by,_seal=_SEAL).items():object.__setattr__(value,key,item)
    return value

def _authority(record,authority):
    if type(authority) is not EdaAuthority or getattr(authority,"_seal",None) is not _SEAL or authority.record_hash!=canonical_hash(record):
        raise ValueError("trusted EDA record authority differs")

def _key(ref):return ref.artifact_id,ref.revision_id,ref.attempt_id

def admit_eda(record: EdaInvestigation, artifacts: tuple[ArtifactBytes,...], *, authority: EdaAuthority):
    record=EdaInvestigation.model_validate_json(record.model_dump_json())
    _authority(record,authority)
    if type(artifacts) is not tuple or not 1<=len(artifacts)<=192:raise ValueError("bounded EDA artifact tuple required")
    supplied={}; total=0
    for item in artifacts:
        if not isinstance(item,ArtifactBytes) or type(item.data) is not bytes:raise ValueError("typed EDA bytes required")
        item=ArtifactBytes(reference=EvidenceReference.model_validate_json(item.reference.model_dump_json()),data=item.data)
        total+=len(item.data)
        if len(item.data)>1048576 or total>16*1048576 or _key(item.reference) in supplied:raise ValueError("EDA artifact bound or duplicate identity")
        supplied[_key(item.reference)]=item
    required={}
    def take(ref):
        key=_key(ref)
        if key in required and required[key]!=ref:raise ValueError("conflicting EDA artifact identity")
        required[key]=ref
        if key not in supplied or supplied[key].reference!=ref:raise ValueError("missing or swapped EDA artifact")
        return supplied[key].data
    req=EdaRequirements.model_validate(_json(take(record.requirements)))
    if req.domain!=record.domain:raise ValueError("EDA requirements domain differs")
    if req.recipe:take(req.recipe)
    for mapping in req.mappings:take(mapping.datasheet)
    for run in (record.candidate,record.baseline):
        if run is None:continue
        config=EdaConfiguration.model_validate(_json(take(run.configuration)))
        if config.domain!=record.domain:raise ValueError("EDA configuration domain differs")
        if type(_json(take(run.receipt))) is not dict:raise ValueError("recorded receipt object required")
        for source in run.sources:take(source.reference)
        for report in run.reports:take(report.reference)
    if set(required)!=set(supplied):raise ValueError("orphan EDA artifact")
    # Parsing/projection must succeed before any envelope can be persisted.
    _projection(record,req,take,authority.reviewed_by)
    return EdaAdmission(record=record,record_hash=canonical_hash(record),checker_sha256=implementation_hash(),artifacts=tuple(
        EdaArtifactIdentity(reference=required[k],size_bytes=len(supplied[k].data)) for k in sorted(required,key=lambda k:tuple(x or "" for x in k))),total_artifact_bytes=total)

def readback_eda(raw: bytes, artifacts: tuple[ArtifactBytes,...], *, authority: EdaAuthority):
    if type(raw) is not bytes or len(raw)>262144:raise ValueError("bounded EDA envelope required")
    envelope=EdaAdmission.model_validate(_json(raw))
    admitted=admit_eda(envelope.record,artifacts,authority=authority)
    if envelope!=admitted:raise ValueError("EDA envelope readback differs")
    return admitted

def project_eda(envelope: EdaAdmission, artifacts: tuple[ArtifactBytes,...], *, authority: EdaAuthority):
    envelope=readback_eda(encode(envelope),artifacts,authority=authority)
    data={_key(a.reference):a.data for a in artifacts}
    take=lambda ref:data[_key(ref)]
    req=EdaRequirements.model_validate(_json(take(envelope.record.requirements)))
    projection=_projection(envelope.record,req,take,authority.reviewed_by)
    return dict(schema_version="engineering.eda-projection.v1",record_hash=envelope.record_hash,
        checker_sha256=envelope.checker_sha256,
        domain=envelope.record.domain,provenance=envelope.provenance,execution_status="not_observed",design_verdict="inconclusive",
        requirements_sha256=envelope.record.requirements.sha256,projection=projection,
        limitations=["Host-pinned association is not execution/resource-settlement authentication.","Clean reports do not establish functional, physical or electrical correctness."])

def _projection(record,req,take,reviewer):
    if record.domain=="vivado":
        from veriforge.vivado.investigation import VivadoProjectIdentity,VivadoExecution,investigate_vivado_reports,vivado_projection,compare_vivado_candidate
        from veriforge.vivado.readiness import SourceIdentity
        def build(run,parent=None):
            refs=tuple(s.reference for s in run.sources)
            if any(set(r.model_dump_json() for r in report.source_refs)!=set(r.model_dump_json() for r in refs) for report in run.reports):raise ValueError("Vivado report source closure differs")
            project=VivadoProjectIdentity(req.part,req.top,req.clock_period_ps/1000,tuple(SourceIdentity(s.name,s.reference.sha256,len(take(s.reference))) for s in run.sources),req.recipe.sha256)
            return investigate_vivado_reports(project,VivadoExecution("not_run",None,run.receipt.sha256),{r.name:take(r.reference) for r in run.reports},parent_investigation_sha256=parent)
        before=build(record.baseline) if record.baseline else None
        candidate=build(record.candidate,before.investigation_sha256 if before else None)
        return vivado_projection(candidate,compare_vivado_candidate(before,candidate) if before else None)
    from veriforge.kicad.investigation import bind_report,investigate_candidate
    from veriforge.kicad.facts import MappingRequirement,parse_netlist,parse_board
    def inputs(run):
        config=EdaConfiguration.model_validate(_json(take(run.configuration)))
        sources={s.name:s.reference for s in run.sources}
        if set(sources)!={"schematic.xml","board.kicad_pcb"}:raise ValueError("exact KiCad source roles required")
        reports=[]
        for r in run.reports:
            if r.name not in ("erc.json","drc.json"):raise ValueError("unsupported KiCad report role")
            source=sources["schematic.xml" if r.name=="erc.json" else "board.kicad_pcb"]
            if r.source_refs!=(source,):raise ValueError("KiCad report source closure differs")
            bound=bind_report(take(r.reference),r.name[:3],source_sha256=source.sha256,configuration_sha256=run.configuration.sha256,lineage="host_reviewed_recorded_source_association")
            if tuple(config.ignored_checks.get(r.name[:3],()))!=bound.report.ignored_checks or tuple(config.included_severities.get(r.name[:3],()))!=bound.report.included_severities:
                raise ValueError("KiCad configured rule scope differs")
            reports.append(bound)
        return sources,tuple(reports)
    sources,reports=inputs(record.candidate)
    before=inputs(record.baseline)[1] if record.baseline else ()
    mappings=tuple(MappingRequirement(m.reference,m.symbol_pin,m.footprint_pad,m.net,m.manufacturer,m.mpn,m.footprint,m.datasheet.sha256,m.datasheet_location,reviewer,record.requirements.sha256) for m in req.mappings)
    xml,pcb=take(sources["schematic.xml"]),take(sources["board.kicad_pcb"])
    if not mappings:
        parse_netlist(xml);parse_board(pcb)
        from veriforge.kicad.investigation import compare_configured_checks
        return dict(domain="kicad",mapping={"status":"inconclusive","gap":"reviewed_mapping_assertions_missing"},
            configured_reports=[dict(kind=r.report.kind,sha256=r.report.raw_sha256,rule_status=r.report.rule_status,ignored_checks=r.report.ignored_checks) for r in reports],
            configured_comparisons=[compare_configured_checks(b,c) for b in before for c in reports if b.report.kind==c.report.kind],
            execution_status="not_observed",physical_electrical_verdict="inconclusive")
    return investigate_candidate(schematic_xml=xml,board_source=pcb,requirements=mappings,before_reports=before,candidate_reports=reports)
