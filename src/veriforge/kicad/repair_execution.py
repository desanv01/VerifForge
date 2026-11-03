"""One-shot reference candidate orchestration with trusted fixed native backend.

Trusted host supplies immutable byte bundles/review records and fresh callbacks.
No arbitrary command, shell, uploaded script, renderer or native process API.
"""
from dataclasses import asdict, dataclass
from hashlib import sha256
from pathlib import Path, PurePosixPath
from typing import Callable, Protocol
import json
import time
import csv

from .facts import MappingRequirement, parse_board
from .investigation import bind_report, investigate_candidate, ViewEvidence

FILES = frozenset({'carrier.kicad_sch','carrier.kicad_pcb','carrier.kicad_pro',
    'VerifForge.kicad_sym','fp-lib-table','sym-lib-table',
    'VerifForge.pretty/Header2x6_2.54_Unreviewed.kicad_mod'})
CLI = 'C:/Program Files/KiCad/10.0/bin/kicad-cli.exe'
IGNORED = {'erc':frozenset({'single_global_label','four_way_junction','simulation_model_issue','footprint_filter'}),
    'drc':frozenset({'missing_courtyard','track_not_centered_on_via','tuning_profile_track_geometries','footprint_filters_mismatch','footprint_type_mismatch'})}
LIMITS = {'wall_seconds':240,'stage_seconds':30,'cleanup_seconds':5,'active_processes':1,
          'logical_processors':1,'commit_bytes':512*1024**2,'available_memory_floor':2*1024**3,
          'artifact_bytes':32*1024**2,'log_bytes':1024**2,'sample_seconds':0.1}
# Reserve 2MiB for initial receipts/logs; later outputs retain the aggregate cap.
STAGING_BYTES = 30*1024**2
STAGING_FILES = 64


def digest(raw): return sha256(raw).hexdigest()
def encode(value): return (json.dumps(value,sort_keys=True,indent=2,allow_nan=False)+'\n').encode()


@dataclass(frozen=True)
class Bundle:
    files: tuple[tuple[str, bytes], ...]
    expected: tuple[tuple[str, str], ...]

    def checked(self):
        files, expected = dict(self.files), dict(self.expected)
        if len(files)!=len(self.files) or len(expected)!=len(self.expected) or set(files)!=FILES or set(expected)!=FILES:
            raise ValueError('exact seven-file reference bundle required')
        for name,raw in files.items():
            if not isinstance(raw,bytes) or not 0<len(raw)<=8*1024**2 or digest(raw)!=expected[name]:
                raise ValueError('source identity mismatch: '+name)
        return files


@dataclass(frozen=True)
class Review:
    requirements: tuple[MappingRequirement, ...]
    expected_sha256: str

    def checked(self):
        if digest(encode([asdict(r) for r in self.requirements]))!=self.expected_sha256:
            raise ValueError('protected review identity mismatch')
        if len(self.requirements)!=24 or {(r.reference,r.symbol_pin) for r in self.requirements}!={(ref,str(n)) for ref in ('J1','J2') for n in range(1,13)}:
            raise ValueError('complete reviewed 24-pin reference contract required')
        return self.requirements


@dataclass(frozen=True)
class Command:
    name: str
    argv: tuple[str,...]
    timeout_seconds: int


def commands(root: Path) -> tuple[Command,...]:
    """Fixed adaptation of reference native preparation; no fabricated runner reuse."""
    sch=str(root/'project-copy/carrier.kicad_sch'); pcb=str(root/'project-copy/carrier.kicad_pcb'); out=root/'reports'
    rows=[('version',('version',)),
      ('erc',('sch','erc','--format','json','--units','mm','--severity-all','--exit-code-violations','--output',str(out/'erc.json'),sch)),
      ('drc',('pcb','drc','--format','json','--units','mm','--severity-all','--schematic-parity','--refill-zones','--exit-code-violations','--output',str(out/'drc.json'),pcb)),
      ('netlist',('sch','export','netlist','--format','kicadxml','--output',str(out/'netlist.xml'),sch)),
      ('bom',('sch','export','bom','--fields','Reference,Value,Manufacturer,MPN,Datasheet,Footprint,DNP','--labels','Reference,Value,Manufacturer,MPN,Datasheet,Footprint,DNP','--sort-field','Reference','--sort-asc','--output',str(out/'properties.csv'),sch)),
      ('schematic-svg',('sch','export','svg','--output',str(out/'schematic'),sch)),
      ('pcb-svg',('pcb','export','svg','--mode-multi','--layers','F.Cu,B.Cu,F.SilkS,F.Mask,F.Fab,F.CrtYd,Edge.Cuts','--output',str(out/'pcb'),pcb))]
    return tuple(Command(n,(CLI,*args),5 if n=='version' else 30) for n,args in rows)


class Backend(Protocol):
    provenance: str
    def run(self, command:Command, *, cwd:Path, environment:dict, deadline:float,
            checkpoint:Callable[[],None]) -> dict: ...
    def cleanup(self, *, seconds:int) -> dict: ...


def _regular_tree(root):
    result={}; total=0
    for p in root.rglob('*'):
        if p.is_symlink() or getattr(p.lstat(),'st_file_attributes',0)&0x400:
            raise ValueError('linked attempt artifact')
        if p.is_file():
            total+=p.stat().st_size
            if total>LIMITS['artifact_bytes'] or p.stat().st_size>8*1024**2:
                raise ValueError('artifact budget exceeded')
            result[p.relative_to(root).as_posix()]=digest(p.read_bytes())
    return result


def execute_candidate(*, root:Path, operation_id:str, original:Bundle, candidate:Bundle,
                      configuration:tuple[tuple[str,bytes],...], configuration_sha256:str,
                      review:Review, backend:Backend, control:Callable[[],bool],
                      reservation:Callable[[str],bool], cancelled:Callable[[],bool],
                      before_reports=(), clock=time.monotonic) -> dict:
    """Create/retain a one-shot attempt, including failed and interrupted evidence.

    Native requires the concrete host-composed NativeBackend and fresh callbacks.
    Caller-claimed provenance alone cannot enable a native backend.
    Callbacks are fresh admission checks, not a continuous exclusion guarantee.
    """
    root=Path(root).absolute()
    for p in (root,*root.parents):
        if p.exists() and (p.is_symlink() or getattr(p.lstat(),'st_file_attributes',0)&0x400):
            raise ValueError('linked workspace')
    base, proposed=original.checked(),candidate.checked(); requirements=review.checked()
    configs=dict(configuration)
    if len(configs)!=len(configuration) or not configs or digest(encode({n:digest(b) for n,b in configs.items()}))!=configuration_sha256:
        raise ValueError('configured-rule identity mismatch')
    for name,raw in configs.items():
        path=PurePosixPath(name)
        if '\\' in name or ':' in name or path.is_absolute() or '..' in path.parts or not name.endswith('.json') or not isinstance(raw,bytes) or len(raw)>1024**2:
            raise ValueError('invalid isolated configuration')
    # Admission is over BOTH versions plus configuration, before even creating
    # an attempt/receipt. Per-file bounds alone cannot bound staging writes.
    staged_files=len(base)+len(proposed)+len(configs)
    staged_bytes=sum(len(raw) for files in (base,proposed,configs) for raw in files.values())
    if staged_files>STAGING_FILES or staged_bytes>STAGING_BYTES:
        raise ValueError('aggregate staging byte/file budget exceeded')
    if not operation_id or not operation_id.isascii() or not operation_id.replace('-','').replace('_','').isalnum():
        raise ValueError('invalid operation ID')
    start=clock(); deadline=start+240
    def admission():
        if cancelled(): raise InterruptedError('cancelled')
        if not control() or not reservation(operation_id): raise InterruptedError('control/reservation lost')
        if clock()>=deadline-5: raise TimeoutError('aggregate deadline')
    admission()
    root.mkdir(parents=True,exist_ok=False)
    receipt={'operation_id':operation_id,'provenance':backend.provenance,'execution_status':'preparing',
             'native_execution':'not_run','design_verdict':'inconclusive','physical_electrical_verdict':'inconclusive',
             'original':dict(original.expected),'candidate':dict(candidate.expected),'configuration_sha256':configuration_sha256,
             'staging':{'bytes':staged_bytes,'files':staged_files,'maximum_bytes':STAGING_BYTES,'maximum_files':STAGING_FILES},
             'review_sha256':review.expected_sha256,'limits':LIMITS,'records':[],'errors':[]}
    snapshots=0
    def save():
        nonlocal snapshots
        raw=encode(receipt)
        (root/f'receipt-{snapshots:03d}.json').write_bytes(raw)
        snapshots+=1
        (root/'receipt.json').write_bytes(raw)
    save()
    try:
        from .native_backend import NativeBackend
        native=type(backend) is NativeBackend and backend.provenance=='native'
        if native and clock is not time.monotonic:
            raise ValueError('native operation requires real monotonic clock')
        if backend.provenance not in ('fake','fake-native-boundary') and not native:
            receipt['execution_status']='held'; receipt['errors'].append('native profile/owned backend not admitted')
            return receipt
        for folder,files in (('original',base),('project-copy',proposed),('config',configs)):
            for name,raw in files.items():
                path=root/folder/name; path.parent.mkdir(parents=True,exist_ok=True); path.write_bytes(raw)
        (root/'reports').mkdir(); (root/'temp').mkdir()
        protected=_regular_tree(root)
        protected.pop('receipt.json',None)
        def checkpoint():
            admission()
            actual=_regular_tree(root)
            if any(actual.get(p)!=h for p,h in protected.items()): raise ValueError('protected workspace changed')
        # Board-side review is required before any command, independent of exports.
        board=parse_board(proposed['carrier.kicad_pcb'])
        for r in requirements:
            pads=[p for p in board.endpoints if (p.reference,p.number)==(r.reference,r.footprint_pad)]
            parts=[p for p in board.components if p.reference==r.reference]
            if not pads or any(p.net!=r.net for p in pads) or len(parts)!=1 or (parts[0].manufacturer,parts[0].mpn,parts[0].footprint)!=(r.manufacturer,r.mpn,r.footprint):
                raise ValueError('candidate violates protected part/pad/net review')
        for command in commands(root):
            record={'name':command.name,'argv':command.argv,'provenance':backend.provenance,'status':'started'}
            receipt['records'].append(record); save()
            try:
                checkpoint(); stage_deadline=min(clock()+command.timeout_seconds,deadline-5)
                if native: receipt['native_execution']='attempted_through_owned_backend'
                result=backend.run(command,cwd=root/'project-copy',environment={
                    'TEMP':str(root/'temp'),'TMP':str(root/'temp'),'KICAD_CONFIG_HOME':str(root/'config')},
                    deadline=stage_deadline,checkpoint=checkpoint)
                checkpoint()
                for key in ('stdout','stderr'):
                    raw=result.get(key,b'')
                    if not isinstance(raw,bytes) or len(raw)>LIMITS['log_bytes']: raise ValueError('log budget/type')
                    (root/'reports'/f'{command.name}.{key}.bin').write_bytes(raw)
                record.update(status='native_completed' if native else 'fake_completed',returncode=result['returncode'])
                record['log_format']=result.get('log_format','separate_stdout_stderr')
                if native:
                    receipt['native_execution']='observed_through_owned_backend'
                    record['tool_sha256']=result['tool_sha256']
                if clock()>stage_deadline: raise TimeoutError('stage deadline')
                if result['returncode'] not in ((0,5) if command.name in IGNORED else (0,)): raise ValueError('tool boundary failure')
                if command.name=='version' and result.get('stdout',b'').strip()!=b'10.0.6': raise ValueError('tool version differs')
                if command.name in IGNORED:
                    parsed=bind_report((root/'reports'/f'{command.name}.json').read_bytes(),command.name,
                        source_sha256=dict(candidate.expected)['carrier.kicad_pcb' if command.name=='drc' else 'carrier.kicad_sch'],
                        configuration_sha256=configuration_sha256,lineage=backend.provenance+'; common CAD ancestry').report
                    record['ignored_checks']=parsed.ignored_checks
                    if set(parsed.ignored_checks)!=IGNORED[command.name] or len(parsed.ignored_checks)!=len(IGNORED[command.name]): raise ValueError('ignored-rule scope changed')
                if command.name=='bom':
                    rows=list(csv.DictReader((root/'reports/properties.csv').read_text(encoding='utf-8-sig').splitlines()))
                    if {r.get('Reference') for r in rows}!={'J1','J2'} or len(rows)!=2:
                        raise ValueError('BOM reference coverage')
                    if any(r.get('Manufacturer')!='Samtec' or r.get('MPN')!='TSW-106-07-G-D' for r in rows):
                        raise ValueError('BOM exact part mismatch')
            except BaseException as exc:
                record.update(status='interrupted' if isinstance(exc,(KeyboardInterrupt,InterruptedError)) else 'failed',
                              error=type(exc).__name__+': '+str(exc))
                raise
            finally:
                cleanup=backend.cleanup(seconds=5); record['cleanup']=cleanup
                if cleanup.get('settled') is not True: raise ValueError('owned cleanup unproved')
                checkpoint(); save()
        xml=(root/'reports/netlist.xml').read_bytes()
        reports=tuple(bind_report((root/'reports'/f'{kind}.json').read_bytes(),kind,
            source_sha256=digest(xml) if kind=='erc' else digest(proposed['carrier.kicad_pcb']),
            configuration_sha256=configuration_sha256,lineage=backend.provenance+'; common CAD ancestry') for kind in ('erc','drc'))
        views=[]
        for p in sorted((root/'reports').rglob('*.svg')):
            kind='schematic' if 'schematic' in p.parts else None
            if kind is None:
                for token,role in (('F_Cu','pcb_copper'),('B_Cu','pcb_copper'),('F_SilkS','pcb_silkscreen'),
                    ('F_Mask','pcb_mask'),('F_CrtYd','pcb_courtyard'),('Edge_Cuts','pcb_edge')):
                    if token in p.stem.replace('.','_'): kind=role
            if kind is None: continue  # Retain unknown/F.Fab views raw; don't invent role.
            views.append(ViewEvidence('view_'+digest(p.relative_to(root).as_posix().encode())[:16],digest(p.read_bytes()),
                digest(xml) if kind=='schematic' else digest(proposed['carrier.kicad_pcb']),kind,'image/svg+xml',backend.provenance+' export'))
        receipt['investigation']=investigate_candidate(schematic_xml=xml,board_source=proposed['carrier.kicad_pcb'],
            requirements=requirements,before_reports=before_reports,candidate_reports=reports,views=tuple(views))
        checkpoint()
        receipt['execution_status']='native_completed' if native else 'fake_completed'
    except BaseException as exc:
        receipt['execution_status']='interrupted' if isinstance(exc,(InterruptedError,KeyboardInterrupt)) else 'failed'
        receipt['errors'].append(type(exc).__name__+': '+str(exc))
    finally:
        receipt['elapsed_seconds']=clock()-start
        try: receipt['artifact_hashes']=_regular_tree(root); receipt['artifact_hashes'].pop('receipt.json',None)
        except Exception as exc: receipt['errors'].append(str(exc)); receipt['execution_status']='failed'
        save()
    return receipt
