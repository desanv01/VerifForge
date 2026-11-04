"""Additive Windows limits adapter. Import is inert; native constructor is gated.

No native constructor/query/create/probe is executed by source tests.
"""
import ctypes
from ctypes import wintypes as W
import hashlib
from pathlib import Path
import types

HELPER_SHA='0d29dc5cd783648a6a01e2b415b5225d2a2f66997c673fc9bd8a29515de7d54b'
_path=Path(__file__).resolve().parent/'owned_process.py'
with _path.open('rb',buffering=0) as _stream:
    _raw=_stream.read(1048576+1)
if hashlib.sha256(_raw).hexdigest()!=HELPER_SHA:
    raise RuntimeError('retained_owned_helper_differs')
owned=types.ModuleType('retained_registration_owned_process')
owned.__file__=str(_path)
exec(compile(_raw,str(_path),'exec'),owned.__dict__)
need=owned.require
MIB=1048576
MEMORY_LIMIT=512*MIB
FLAGS=0x2000|0x8|0x10|0x100|0x200

class MemoryStatus(ctypes.Structure):
    _fields_=[('length',W.DWORD),('load',W.DWORD)]+[(name,ctypes.c_ulonglong) for name in
        ('total','available','page_total','page_available','virtual_total','virtual_available','extended')]

# Declared here for static review; binding/calls occur only in the gated native
# constructor, never at import/default file-only inspection.
SIGNATURES={
 'GetCurrentProcess':([],W.HANDLE),
 'GetProcessAffinityMask':([W.HANDLE,ctypes.POINTER(ctypes.c_size_t),ctypes.POINTER(ctypes.c_size_t)],W.BOOL),
 'GetActiveProcessorGroupCount':([],W.WORD),
 'GetProcessGroupAffinity':([W.HANDLE,ctypes.POINTER(W.WORD),ctypes.POINTER(W.WORD)],W.BOOL),
 'GlobalMemoryStatusEx':([ctypes.POINTER(MemoryStatus)],W.BOOL),
 'PeekNamedPipe':([W.HANDLE,ctypes.c_void_p,W.DWORD,ctypes.POINTER(W.DWORD),ctypes.POINTER(W.DWORD),ctypes.POINTER(W.DWORD)],W.BOOL),
 'GetWindowsDirectoryW':([W.LPWSTR,W.UINT],W.UINT),
}

def select_cpu(process_mask,system_mask):
    need(type(process_mask) is int and type(system_mask) is int and
         0<process_mask<=system_mask<2**64 and process_mask&system_mask==process_mask,'host_affinity_unavailable')
    return process_mask & -process_mask

class LimitedWindowsApi(owned.WindowsApi):
    """Exact retained launch/close semantics, extended before suspended launch."""
    def __init__(self):
        super().__init__()
        need(ctypes.sizeof(ctypes.c_void_p)==8 and ctypes.sizeof(owned.ExtendedLimits)==144,'unsupported_native_layout')
        for name,(args,result) in SIGNATURES.items():
            function=getattr(self.kernel,name)
            function.argtypes,function.restype=args,result
        self.active_job=None
        self.principal=None
        self.cpu_bit=None
        self.limit_receipt=None
        self.pre_resume_guard=None

    def affinity(self,process):
        process_mask,system_mask=ctypes.c_size_t(),ctypes.c_size_t()
        need(self.kernel.GetProcessAffinityMask(process,ctypes.byref(process_mask),ctypes.byref(system_mask)),
             'affinity_query_failed')
        return int(process_mask.value),int(system_mask.value)

    def host_cpu(self):
        # A multi-group host is outside this deliberately bounded adapter.
        need(self.kernel.GetActiveProcessorGroupCount()==1,'processor_groups_unsupported')
        process=self.kernel.GetCurrentProcess()
        count=W.WORD(1)
        groups=(W.WORD*1)()
        need(self.kernel.GetProcessGroupAffinity(process,ctypes.byref(count),groups) and
             count.value==1 and groups[0]==0,'process_group_unsupported')
        masks=self.affinity(process)
        return select_cpu(*masks),masks

    def verify_limits(self,job):
        confirmed=owned.ExtendedLimits()
        returned=W.DWORD()
        need(self.kernel.QueryInformationJobObject(job,9,ctypes.byref(confirmed),ctypes.sizeof(confirmed),ctypes.byref(returned)),
             'registration_job_limit_query_failed')
        need(returned.value==ctypes.sizeof(confirmed),'registration_job_limit_length')
        need(confirmed.basic.flags==FLAGS and confirmed.basic.active_limit==1 and
             confirmed.basic.affinity==self.cpu_bit and confirmed.process_memory==MEMORY_LIMIT and
             confirmed.job_memory==MEMORY_LIMIT,'registration_job_limits_differ')
        return {'flags':int(confirmed.basic.flags),'active_process_limit':1,'affinity_bit':self.cpu_bit,
                'process_commit_bytes':int(confirmed.process_memory),'job_commit_bytes':int(confirmed.job_memory),
                'kill_on_close':True,'breakaway':False,'source':'native_query_readback'}

    def create_job(self):
        bit,masks=self.host_cpu()
        job=self.kernel.CreateJobObjectW(None,None)
        need(job,'registration_job_create_failed')
        try:
            self.cpu_bit=bit
            limits=owned.ExtendedLimits()
            limits.basic.flags=FLAGS
            limits.basic.active_limit=1
            limits.basic.affinity=bit
            limits.process_memory=limits.job_memory=MEMORY_LIMIT
            need(self.kernel.SetInformationJobObject(job,9,ctypes.byref(limits),ctypes.sizeof(limits)),
                 'registration_job_limit_failed')
            self.limit_receipt=self.verify_limits(job)
            self.limit_receipt['host_process_affinity']=masks[0]
            self.limit_receipt['host_system_affinity']=masks[1]
            self.active_job=job
            return job
        except BaseException as primary:
            try:
                self.close(job)
            except BaseException:
                primary.suspended_cleanup_errors=['limited_job_handle_close_unproved']
            raise

    def create_suspended(self,argv,env,cwd):
        need(self.active_job is not None,'registration_job_missing')
        self.verify_limits(self.active_job)
        return super().create_suspended(argv,env,cwd)

    def create_suspended_split(self,argv,env,cwd):
        need(self.active_job is not None,'registration_job_missing')
        self.verify_limits(self.active_job)
        return super().create_suspended_split(argv,env,cwd)

    def assign(self,job,process):
        super().assign(job,process)
        self.principal=process
        self.verify_limits(job)
        process_mask,system_mask=self.affinity(process)
        need(process_mask==self.cpu_bit and process_mask&system_mask==process_mask,'assigned_affinity_differs')

    def resume(self,thread):
        need(self.active_job is not None and self.principal is not None,'unverified_suspended_principal')
        self.verify_limits(self.active_job)
        need(self.affinity(self.principal)[0]==self.cpu_bit,'pre_resume_affinity_differs')
        need(callable(self.pre_resume_guard),'pre_resume_grant_guard_missing')
        self.pre_resume_guard()
        need(self.available_memory()>=2048*MIB,'pre_resume_2gib_floor')
        self.pre_resume_guard()
        super().resume(thread)

    def available_memory(self):
        value=MemoryStatus()
        value.length=ctypes.sizeof(value)
        need(self.kernel.GlobalMemoryStatusEx(ctypes.byref(value)),'memory_headroom_unavailable')
        return int(value.available)

    def windows_directory(self):
        value=ctypes.create_unicode_buffer(32768)
        size=self.kernel.GetWindowsDirectoryW(value,len(value))
        need(0<size<len(value),'windows_directory_unavailable')
        return value.value

    def peek_pipe(self,stream):
        import msvcrt
        handle=msvcrt.get_osfhandle(stream.fileno())
        available=W.DWORD()
        if self.kernel.PeekNamedPipe(handle,None,0,None,ctypes.byref(available),None):
            return int(available.value),False
        need(ctypes.get_last_error()==109,'owned_pipe_peek_failed')  # ERROR_BROKEN_PIPE
        return 0,True
