"""Native backend logic with fake OS services only. No Win32 calls/tool probes."""
import io
from dataclasses import replace
import pytest
from veriforge.kicad.native_backend import NativeBackend, Installation
from veriforge.kicad.repair_execution import commands
from .test_repair_execution import inputs, Fake
from veriforge.kicad.repair_execution import execute_candidate


class Process:
    def __init__(self,raw): self.stdout=io.BytesIO(raw); self.cleanup_receipt=None
    def poll(self): return 0
    def close_tree(self,*,grace):
        self.cleanup_receipt={'errors':[],'final_member_pids':[],'root_exit_status':0,
            'unexpected_members':[],'job_close_fallback_requested':False,'job_assignment_completed':True}


class Services:
    def __init__(self,raw=b'10.0.6',failure=None): self.raw=raw; self.failure=failure; self.calls=[]
    def verify_installation(self):
        if self.failure=='hash': raise RuntimeError('hash mismatch')
    def memory(self): return 0 if self.failure=='memory' else 3*1024**3
    def windows_directory(self): return 'C:/Windows'
    def launch(self,argv,env,cwd,guard):
        guard(); self.calls.append(argv)
        if self.failure=='launch': raise RuntimeError('uncertain launch')
        self.process=Process(self.raw); return self.process
    def peek(self,stream):
        n=len(stream.getvalue())-stream.tell(); return n,n==0


def run(tmp_path,services,checkpoint=lambda:None):
    (tmp_path/'reports').mkdir(); (tmp_path/'project-copy').mkdir()
    backend=NativeBackend(Installation('1'*64),_test_services=services,_sleep=lambda _:None)
    command=commands(tmp_path)[0]
    result=None
    try: result=backend.run(command,cwd=tmp_path/'project-copy',environment={},deadline=float('inf'),checkpoint=checkpoint)
    finally: cleanup=backend.cleanup(seconds=5)
    return backend,result,cleanup


def test_success_is_fake_with_exact_command_and_positive_cleanup(tmp_path):
    s=Services(); b,r,c=run(tmp_path,s)
    assert r['stdout']==b'10.0.6'; assert b.provenance=='fake-native-boundary'
    assert c['settled'] is True; assert c['owned_receipt']['job_assignment_completed']
    assert s.calls==[commands(tmp_path)[0].argv]
    assert (tmp_path/'reports/version.native-combined.log').read_bytes()==b'10.0.6'

@pytest.mark.parametrize('failure',['hash','memory'])
def test_bad_profile_or_headroom_never_launches(tmp_path,failure):
    s=Services(failure=failure)
    with pytest.raises(RuntimeError): run(tmp_path,s)
    assert not s.calls

def test_cancel_at_pre_resume_refuses_effect(tmp_path):
    s=Services(); count=[0]
    def check():
        count[0]+=1
        if count[0]>=5: raise InterruptedError('cancel')
    with pytest.raises(InterruptedError): run(tmp_path,s,check)
    assert not s.calls

def test_uncertain_launch_is_not_settled(tmp_path):
    (tmp_path/'reports').mkdir(); s=Services(failure='launch')
    b=NativeBackend(Installation('1'*64),_test_services=s)
    with pytest.raises(RuntimeError): b.run(commands(tmp_path)[0],cwd=tmp_path/'project-copy',environment={},deadline=float('inf'),checkpoint=lambda:None)
    assert b.cleanup(seconds=5)['settled'] is False

def test_overflow_retains_log_and_stops(tmp_path):
    with pytest.raises(RuntimeError,match='log cap'): run(tmp_path,Services(b'x'*(1024**2+1)))
    assert (tmp_path/'reports/version.native-combined.log').exists()

def test_arbitrary_command_cannot_launch(tmp_path):
    b=NativeBackend(Installation('1'*64),_test_services=Services())
    with pytest.raises(ValueError): b.run(replace(commands(tmp_path)[0],argv=('cmd.exe',)),cwd=tmp_path/'project-copy',environment={},deadline=999999999,checkpoint=lambda:None)

def test_fake_clocks_cannot_select_real_os_backend():
    with pytest.raises(ValueError,match='fake OS services'):
        NativeBackend(Installation('1'*64),_clock=lambda:0)

def test_expiry_during_memory_query_refuses_launch(tmp_path):
    t=[0]
    class Expiring(Services):
        def memory(self): t[0]=100; return 3*1024**3
    s=Expiring(); b=NativeBackend(Installation('1'*64),_test_services=s,_clock=lambda:t[0])
    with pytest.raises(TimeoutError):
        b.run(commands(tmp_path)[0],cwd=tmp_path/'project-copy',environment={},deadline=50,checkpoint=lambda:None)
    assert not s.calls; assert b.cleanup(seconds=5)['settled']

def test_native_backend_construction_and_import_are_inert(monkeypatch):
    import ctypes
    monkeypatch.setattr(ctypes,'WinDLL',lambda *a,**k:pytest.fail('native constructor queried'))
    b=NativeBackend(Installation('1'*64))
    assert b.provenance=='native'; assert b.services is None

def test_concrete_backend_to_candidate_projection_with_fake_os(tmp_path):
    class ExportServices(Services):
        def launch(self,argv,env,cwd,guard):
            guard()
            command=next(c for c in commands(cwd.parent) if c.argv==argv)
            result=Fake().run(command,cwd=cwd,environment=env,deadline=float('inf'),checkpoint=guard)
            self.calls.append(argv); self.process=Process(result['stdout'])
            return self.process
    services=ExportServices()
    backend=NativeBackend(Installation('1'*64),_test_services=services,_sleep=lambda _:None)
    result=execute_candidate(root=tmp_path/'attempt',operation_id='integration-1',backend=backend,
        control=lambda:True,reservation=lambda op:op=='integration-1',cancelled=lambda:False,**inputs())
    assert result['execution_status']=='fake_completed',result['errors']
    assert result['provenance']=='fake-native-boundary'
    assert result['native_execution']=='not_run'
    assert len(services.calls)==7
    assert result['investigation']['design_verdict']=='inconclusive'
    assert all(r['cleanup']['settled'] for r in result['records'])
