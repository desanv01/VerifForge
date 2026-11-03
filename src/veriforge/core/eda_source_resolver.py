"""Trusted immutable-catalog resolver for the concrete A10 native drivers.

No public deserializer, environment discovery, native calls or default install.
Catalog capabilities are installed Python objects after independent host review;
hashing their associations preserves identity, it does not perform that review.
"""
from dataclasses import asdict, dataclass,field
from hashlib import sha256
from pathlib import Path, PurePosixPath
from types import MappingProxyType
from uuid import uuid4
from veriforge.contracts.eda_tools import EdaRequirements,EdaConfiguration
from veriforge.system_investigation import eda_records as eda
from . import eda_policy, investigations, service
from .eda_investigations import AdmittedEda
from .eda_repair_runtime import VivadoDriver, KiCadDriver

MAX_FILE=1048576
MAX_CATALOG_BYTES=64*MAX_FILE
_SEAL=object()


def _require(value):
    if not value:raise service.Conflict('Reviewed native source association unavailable or changed')


def _bytes(artifact):
    from veriforge.system_investigation.records import ArtifactBytes
    _require(type(artifact)is ArtifactBytes and type(artifact.data)is bytes and
             0<len(artifact.data)<=MAX_FILE and sha256(artifact.data).hexdigest()==artifact.reference.sha256)
    return artifact.data


def _pairs(values,allowed=None):
    _require(type(values)is tuple and 0<len(values)<=64)
    out={}
    for pair in values:
        # A frozen dataclass/outer tuple does not freeze mutable inner lists.
        # Reject them at installation so retained reviewed bytes cannot drift.
        _require(type(pair)is tuple and len(pair)==2)
        name,raw=pair
        p=PurePosixPath(name)
        _require(type(name)is str and not p.is_absolute() and p.as_posix()==name and
                 '..'not in p.parts and '\\'not in name and ':'not in name and name not in out and
                 type(raw)is bytes and 0<len(raw)<=MAX_FILE)
        out[name]=raw
    _require(allowed is None or set(out)==set(allowed))
    return out


@dataclass(frozen=True)
class NativeAssociation:
    """Host review INPUT, never a request/wire grant or automatic review.

    KiCad files bind the seven raw sources to the retained exported XML and
    board evidence. That non-derivable association must be independently reviewed.
    Vivado sources already live in v3; files/configuration are empty there.
    provenance retains the reviewed native capture/association evidence bytes.
    """
    version_id: str
    content_sha256: str
    project_id: str
    revision_id: str
    revision_sha256: str
    requirements: object
    configuration: object
    authority: object
    installation: object
    provenance: object=field(repr=False)
    files: tuple=field(default=(),repr=False)
    native_configuration: tuple=field(default=(),repr=False)
    exported_xml: object=None
    io_requirements: object=field(default=None,repr=False)


class ReviewedNativeCatalog:
    """Explicit capability from trusted startup AFTER independent review.

    There is intentionally no JSON loader or reviewer-string argument. The
    factory is a host trust boundary, not an authentication or review service.
    It never claims native capture authenticity merely because hashes match.
    """
    def __init__(self,*a,**kw):raise TypeError('Trusted host review installation required')
    def __setattr__(self,name,value):raise AttributeError('Immutable installed catalog')

    @classmethod
    def from_trusted_host_review(cls,associations):
        _require(type(associations)is tuple and 0<len(associations)<=256)
        entries={};total=0
        for item in associations:
            _require(type(item)is NativeAssociation and item.version_id not in entries and
                     type(item.authority)is eda.EdaAuthority)
            for pin in (item.content_sha256,item.revision_sha256):
                _require(type(pin)is str and len(pin)==64 and all(c in '0123456789abcdef'for c in pin))
            total+=len(_bytes(item.provenance))
            for pairs in (item.files,item.native_configuration):
                _require(type(pairs)is tuple)
                if pairs:total+=sum(map(len,_pairs(pairs).values()))
            if item.io_requirements is not None:total+=len(_bytes(item.io_requirements))
            _require(total<=MAX_CATALOG_BYTES)
            entries[item.version_id]=item
        value=object.__new__(cls)
        object.__setattr__(value,'_entries',MappingProxyType(entries));object.__setattr__(value,'_seal',_SEAL)
        return value


@dataclass(frozen=True)
class VivadoHost:
    installation: object
    owner: str
    profile: str
    hooks_factory: object


@dataclass(frozen=True)
class KiCadHost:
    installation: object
    reservation: object


def _unlinked(path):
    path=Path(path)
    _require(path.is_absolute() and not any(p.is_symlink()or p.is_junction()for p in (path,*path.parents)))
    return path


class ImmutableEdaResolver:
    def __init__(self,sessions,*,output_root,catalog=None,vivado=None,kicad=None):
        self.sessions=sessions;self.root=_unlinked(output_root)
        _require(self.root.is_dir())
        self.catalog=catalog;self.vivado=vivado;self.kicad=kicad

    def _read(self,session,row,domain):
        # Fresh session lookup/ownership/content/artifact/installed-v3-authority
        # validation, not trust in the detached rows A10 happened to pass in.
        current=investigations._version(session,row.investigation_id,row.id)
        _require(all(getattr(current,key)==getattr(row,key)for key in
                     ('id','investigation_id','project_id','content_hash','revision_id','revision_hash','manifest_hash','content')))
        _,admitted,_=investigations.projection_inputs(session,current.investigation_id,current.id)
        _require(type(admitted)is AdmittedEda)
        envelope=admitted.envelope;record=envelope.record;run=record.candidate
        _require(record.domain==domain and current.schema_version=='engineering.admission.v3')
        item=self.catalog._entries.get(current.id);_require(item is not None)
        _require(item.authority is eda_policy.authority_for(record) and
                 (item.content_sha256,item.project_id,item.revision_id,item.revision_sha256)==
                 (current.content_hash,current.project_id,current.revision_id,current.revision_hash) and
                 item.requirements==record.requirements and item.configuration==run.configuration)
        _bytes(item.provenance)
        supplied={eda._key(a.reference):a for a in admitted.artifacts}
        def take(ref):
            a=supplied.get(eda._key(ref));_require(a is not None and a.reference==ref)
            return _bytes(a)
        req=EdaRequirements.model_validate(eda._json(take(record.requirements)))
        return item,record,run,req,take

    def __call__(self,domain,original,candidate):
        _require(type(self.catalog)is ReviewedNativeCatalog and self.catalog._seal is _SEAL and
                 domain in ('vivado','kicad') and original.id!=candidate.id and
                 original.project_id==candidate.project_id)
        host=self.vivado if domain=='vivado'else self.kicad
        _require(host is not None)
        with self.sessions()as session:
            old=self._read(session,original,domain);new=self._read(session,candidate,domain)
        _require(old[1].requirements==new[1].requirements and old[2].configuration==new[2].configuration and
                 old[2].project.revision_id!=new[2].project.revision_id and
                 old[0].installation==host.installation and new[0].installation==host.installation)
        if domain=='kicad':return self._kicad(host,old,new)
        return self._vivado(host,old,new)

    def _vivado(self,host,old,new):
        from veriforge.vivado.investigation import (VivadoProjectIdentity,VivadoExecution,
            investigate_vivado_reports,IMPLEMENTATION_SHA256)
        from veriforge.vivado.readiness import SourceIdentity
        from veriforge.vivado.repair_execution import ControllerInstallation,RepairPins,NAMES,PROFILES
        from veriforge.vivado.repair_runtime import VivadoRepairRuntime
        _require(type(host)is VivadoHost and type(host.installation)is ControllerInstallation and
                 host.profile in PROFILES and callable(host.hooks_factory) and
                 type(host.owner)is str and host.owner.startswith('U6-') and len(host.owner)<=100 and
                 all(c.isalnum()or c=='-'for c in host.owner))
        _unlinked(host.installation.project_root);_require(host.installation.project_root.is_dir())
        groups=[];identities=[]
        for item,record,run,req,take in (old,new):
            _require(not item.files and not item.native_configuration and req.clock_period_ps==10000 and req.recipe is not None and
                     req.recipe.sha256==PROFILES[host.profile][2])
            files=_pairs(tuple((s.name,take(s.reference))for s in run.sources),NAMES)
            groups.append(files);identities.append(tuple(SourceIdentity(n,sha256(b).hexdigest(),len(b))for n,b in sorted(files.items())))
        item,record,run,req,take=old
        baseline=investigate_vivado_reports(VivadoProjectIdentity(req.part,req.top,req.clock_period_ps/1000,
            identities[0],req.recipe.sha256),VivadoExecution('not_run',None,run.receipt.sha256),
            {r.name:take(r.reference)for r in run.reports})
        _require(baseline.design_phase==host.profile)
        io=old[0].io_requirements
        _require(io==new[0].io_requirements)
        pins=RepairPins(host.profile,identities[0],identities[1],
                        None if io is None else sha256(_bytes(io)).hexdigest(),IMPLEMENTATION_SHA256)
        # Source/requirements/pins and real installed controller/recipe bytes are
        # checked before creating even a materialization directory.
        host.installation.controller(host.profile);host.installation.recipe(host.profile);host.installation.verify_launcher()
        _require(sum(len(b)for files in groups for b in files.values())+(0 if io is None else len(_bytes(io)))<=7*MAX_FILE)
        _unlinked(self.root);directory=self.root/('eda-source-'+uuid4().hex);directory.mkdir(mode=0o700)
        for label,files in zip(('original','candidate'),groups):
            target=directory/label;target.mkdir(mode=0o700)
            for name,raw in files.items():
                with (target/name).open('xb')as out:out.write(raw)
                _require((target/name).read_bytes()==raw)
        document=None
        if io is not None:
            document=directory/'requirements.bin';document.write_bytes(_bytes(io))
        # Native tickets survive API/worker re-resolution. Source snapshots are
        # fresh, but the trusted retained job root is stable across processes.
        runtime=VivadoRepairRuntime(self.root/'vivado-native',host.installation,host.owner,host.hooks_factory,requirements_document=document)
        return VivadoDriver(runtime,original=directory/'original',candidate=directory/'candidate',
            baseline=baseline,pins=pins,configuration_hash=run.configuration.sha256)

    def _kicad(self,host,old,new):
        from veriforge.kicad.repair_execution import Bundle,Review,FILES,IGNORED,STAGING_BYTES,STAGING_FILES,digest,encode
        from veriforge.kicad.facts import MappingRequirement
        from veriforge.kicad.native_backend import NativeBackend,Installation
        _require(type(host)is KiCadHost and type(host.installation)is Installation and callable(host.reservation))
        bundles=[]
        for item,record,run,req,take in (old,new):
            config=EdaConfiguration.model_validate(eda._json(take(run.configuration)))
            _require(set(config.ignored_checks)=={'erc','drc'} and set(config.included_severities)=={'erc','drc'} and
                     all(frozenset(config.ignored_checks[k])==IGNORED[k] and
                         set(config.included_severities[k])=={'warning','error','exclusion'}for k in IGNORED))
            files=_pairs(item.files,FILES);sources={s.name:s.reference for s in run.sources}
            _require(set(sources)=={'schematic.xml','board.kicad_pcb'} and
                     item.exported_xml==sources['schematic.xml'] and
                     files['carrier.kicad_pcb']==take(sources['board.kicad_pcb']))
            take(item.exported_xml)
            bundle=Bundle(tuple(files.items()),tuple((n,digest(b))for n,b in files.items()));bundle.checked();bundles.append(bundle)
        configs=_pairs(old[0].native_configuration)
        _require(old[0].native_configuration==new[0].native_configuration and all(n.endswith('.json')for n in configs))
        _require(all(type(eda._json(raw))is dict for raw in configs.values()))
        _require(sum(len(b)for bundle in bundles for _,b in bundle.files)+sum(map(len,configs.values()))<=STAGING_BYTES and
                 sum(len(b.files)for b in bundles)+len(configs)<=STAGING_FILES)
        item,record,run,req,take=old
        mappings=tuple(MappingRequirement(m.reference,m.symbol_pin,m.footprint_pad,m.net,m.manufacturer,m.mpn,m.footprint,
            m.datasheet.sha256,m.datasheet_location,item.authority.reviewed_by,record.requirements.sha256)for m in req.mappings)
        review=Review(mappings,digest(encode([asdict(r)for r in mappings])));review.checked()
        _unlinked(self.root)
        return KiCadDriver(root=self.root,original=bundles[0],candidate=bundles[1],review=review,
            configuration=tuple(configs.items()),configuration_hash=digest(encode({n:digest(b)for n,b in configs.items()})),
            backend=NativeBackend(host.installation),reservation=lambda operation:host.reservation(operation))
