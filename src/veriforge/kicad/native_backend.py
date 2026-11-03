"""Trusted fixed KiCad backend. Import/construction perform no native queries.

The host supplies a reviewed installation hash, not an HTTP-selected executable.
Retained Windows primitives own the child from suspended creation to settlement.
"""
from dataclasses import dataclass
from pathlib import Path
import hashlib
import os
import time
import types

from .repair_execution import CLI, LIMITS, commands

HELPERS = {'owned_process.py':'d3d33a80f7da4153634bdaa168b8393ffd04d97ae2125b2df3a97b48f45a7518',
           'resource_api.py':'f2899fc3cb44fe08299af76e575cdb5fed763b61c411cbc4546a2835f6625454'}


@dataclass(frozen=True)
class Installation:
    executable_sha256: str
    version: str = '10.0.6'

    def __post_init__(self):
        if not isinstance(self.executable_sha256,str) or len(self.executable_sha256)!=64 or any(c not in '0123456789abcdef' for c in self.executable_sha256) or self.version!='10.0.6':
            raise ValueError('reviewed KiCad10.0.6 installation SHA required')


class _WindowsServices:
    def __init__(self,installation,guard):
        if os.name!='nt': raise RuntimeError('Windows native profile required')
        self.installation=installation
        self.verify_installation(); guard()
        base=Path(__file__).parent
        helper_bytes={name:(base/name).read_bytes() for name in HELPERS}
        for name,pin in HELPERS.items():
            if hashlib.sha256(helper_bytes[name]).hexdigest()!=pin:
                raise RuntimeError('owned primitive changed')
        module=types.ModuleType('kicad_reviewed_limits'); module.__file__=str(base/'resource_api.py')
        exec(compile(helper_bytes['resource_api.py'],module.__file__,'exec'),module.__dict__)
        guard()
        self.api=module.LimitedWindowsApi()
        self.owned=module.owned
        guard()

    def verify_installation(self):
        p=Path(CLI)
        for part in (p,*p.parents):
            if part.is_symlink() or getattr(part.lstat(),'st_file_attributes',0)&0x400:
                raise RuntimeError('linked tool installation')
        if not p.is_file() or p.stat().st_size>128*1024**2:
            raise RuntimeError('unsupported installation')
        if hashlib.sha256(p.read_bytes()).hexdigest()!=self.installation.executable_sha256:
            raise RuntimeError('installation identity differs')

    def memory(self): return self.api.available_memory()
    def windows_directory(self): return self.api.windows_directory()
    def launch(self,argv,env,cwd,guard):
        self.api.pre_resume_guard=guard
        return self.owned.OwnedProcess(argv,env=env,cwd=str(cwd),_api=self.api)
    def peek(self,stream): return self.api.peek_pipe(stream)


class NativeBackend:
    """Only host composition constructs this backend; no executable/flag override.

    The private test seam forces fake provenance even if all callbacks pass.
    """
    def __init__(self,installation:Installation,*,_test_services=None,_clock=time.monotonic,_sleep=time.sleep):
        if type(installation) is not Installation: raise ValueError('installation profile required')
        if _test_services is None and (_clock is not time.monotonic or _sleep is not time.sleep):
            raise ValueError('test clocks require fake OS services')
        self.installation=installation
        self.provenance='native' if (_test_services is None and _clock is time.monotonic and _sleep is time.sleep) else 'fake-native-boundary'
        self.services=_test_services; self.clock=_clock; self.sleep=_sleep
        self.process=None; self.cleanup_record=None; self.stream=None; self.log=None
        self.launch_attempted=False
        self.log_eof=False

    def run(self,command,*,cwd,environment,deadline,checkpoint):
        if self.process is not None: raise RuntimeError('previous stage not cleaned')
        root=cwd.parent
        if command not in commands(root): raise ValueError('not a fixed reference command')
        deadline=min(deadline,self.clock()+command.timeout_seconds)
        self.cleanup_record=None
        self.launch_attempted=False
        self.log_eof=False
        def guard():
            checkpoint()
            if self.clock()>=deadline: raise TimeoutError('native stage deadline')
        guard()
        try:
            if self.services is None: self.services=_WindowsServices(self.installation,guard)
            self.services.verify_installation(); guard()
            if self.services.memory()<LIMITS['available_memory_floor']: raise RuntimeError('2GiB memory floor')
            guard()
            windows=self.services.windows_directory(); guard()
            env=dict(SystemRoot=windows,WINDIR=windows,PATH=str(Path(CLI).parent),
                TEMP=str(root/'temp'),TMP=str(root/'temp'),KICAD_CONFIG_HOME=str(root/'config'),
                APPDATA=str(root/'config'),LOCALAPPDATA=str(root/'config'),USERPROFILE=str(root/'config'))
            self.log=(root/'reports'/f'{command.name}.native-combined.log').open('xb',buffering=0)
            def resume_guard():
                self.services.verify_installation(); guard()
            self.launch_attempted=True
            self.process=self.services.launch(command.argv,env,cwd,resume_guard)
            self.stream=self.process.stdout
            captured=bytearray(); eof=False
            while True:
                guard()
                if self.services.memory()<LIMITS['available_memory_floor']: raise RuntimeError('2GiB memory floor')
                guard()
                count,eof=self.services.peek(self.stream)
                if type(count) is not int or count<0 or type(eof) is not bool or (eof and count):
                    raise RuntimeError('contradictory pipe accounting')
                if count:
                    if len(captured)+count>LIMITS['log_bytes']: raise RuntimeError('combined log cap')
                    raw=self.stream.read(min(count,4096))
                    if not raw: raise RuntimeError('short pipe read')
                    captured.extend(raw)
                    if self.log.write(raw)!=len(raw): raise RuntimeError('combined log short write')
                status=self.process.poll()
                if status is not None and eof:
                    self.log_eof=True
                    break
                self.sleep(LIMITS['sample_seconds'])
            guard()
            return {'returncode':status,'stdout':bytes(captured),'stderr':b'',
                    'provenance':self.provenance,'tool_sha256':self.installation.executable_sha256,
                    'log_format':'combined_stdout_stderr',
                    'version_expected':self.installation.version}
        except BaseException as exc:
            # Preserve constructor failure cleanup (suspended process may exist).
            self.cleanup_record=getattr(exc,'cleanup_receipt',None)
            raise

    def cleanup(self,*,seconds):
        errors=[]; receipt=self.cleanup_record
        try:
            if self.process is not None:
                self.process.close_tree(grace=min(seconds,5))
                receipt=self.process.cleanup_receipt
        except BaseException as exc: errors.append(str(exc))
        finally:
            for stream in (self.stream,self.log):
                if stream is not None:
                    try: stream.close()
                    except BaseException as exc: errors.append(str(exc))
            self.process=self.stream=self.log=None
        # No child creation is a settled failed preflight, not native success.
        settled=not errors and ((receipt is None and not self.launch_attempted) or (receipt is not None and receipt.get('errors')==[] and
            receipt.get('final_member_pids')==[] and type(receipt.get('root_exit_status')) is int and
            receipt.get('unexpected_members')==[] and receipt.get('job_assignment_completed') is True and
            receipt.get('job_close_fallback_requested') is False))
        return {'settled':settled,'owned_receipt':receipt,'errors':errors,'provenance':self.provenance,
                'log_eof_observed':self.log_eof}
