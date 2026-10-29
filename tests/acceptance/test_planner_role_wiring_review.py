"""Independent role admission/control review; explicit config, SQLite/fake runners only."""
import ast
from datetime import timedelta
import hashlib
import json
import os
from pathlib import Path
import runpy
import sys
from types import SimpleNamespace

from alembic import command
from alembic.config import Config
import pytest

from veriforge.core import dispatch, worker, runtime_configuration as config, runtime_control
from veriforge.core.catalog import seed_catalog
from veriforge.core.database import Attempt, Finding, make_engine, session_factory
import sqlalchemy as sa

ROOT=Path(__file__).resolve().parents[2]
# Trusted synthetic helper definitions only; pytest fixtures in this namespace
# are NOT registered, so its autouse env manipulations are never installed.
HELPERS=runpy.run_path(str(ROOT/'tests/core/test_planner_role_wiring.py'))
prepare,recorded,clock=HELPERS['prepare'],HELPERS['recorded'],HELPERS['clock']


@pytest.fixture
def db(tmp_path):
    engine=make_engine('sqlite:///'+(tmp_path/'review.db').as_posix())
    settings=Config(str(ROOT/'alembic.ini'))
    with engine.begin() as connection:
        settings.attributes['connection']=connection;command.upgrade(settings,'head')
    sessions=session_factory(engine)
    with sessions.begin() as session:seed_catalog(session)
    yield sessions
    engine.dispose()


class Broker:
    def __init__(self):self.attempts=[]
    def enqueue(self,attempt):self.attempts.append(attempt)


def known_control(tmp_path,state='RUNNING'):
    path=tmp_path/'explicit-control.json';path.write_text(json.dumps({'control':state}))
    return path,config.RuntimeConfiguration(enabled=True,control_path=path)


def fake_runner(sessions,attempt,effect=None):
    def runner(experiment,output,*,cancel_event):
        if effect:effect(cancel_event)
        with sessions() as session:
            record=worker.service.resolve_attempt_experiment(session,session.get(Attempt,attempt))
            return recorded(record)
    return runner


def test_final_owner_freeze_and_seventeen_only_control_keyword_changes():
    expected={'src/veriforge/core/dispatch.py':'60151d8e6bac9ae767653373f845af98cbf9d6a3b8b4b5c0b9ad431bb044c384',
        'src/veriforge/core/worker.py':'3724ee525b07ab119bad4780c392e7586e41d2269a605b87773761f9b21d0e4f',
        'src/veriforge/core/runtime_configuration.py':'23040cd803091286636cf3498d159591fed9273b4df73c51c22af81f629a2bb7'}
    for path,digest in expected.items():assert hashlib.sha256((ROOT/path).read_bytes()).hexdigest()==digest
    # Accepted public harness baselines; no private coordination file is needed.
    # Removing only these explicit-control additions must restore the complete
    # original normalized AST (including every action/assertion/parameter).
    proof={
        'tests/core/test_planner_driver.py':{
            'after_sha256':'d053070c744eb3606aea230be0774b05a68ac7b99b48954b3e0b847e78648ce1',
            'injected_call_lines':(162,164,167,168,251,294,295,312,328,343,354,381,401),
            'only_added_control_keywords':13,
            'normalized_ast_sha256':'663787cf58d6e28b5915d9ac7bcc45bb532536497b1a8dc938c6479e341b40e0'},
        'tests/acceptance/test_planner_driver_review.py':{
            'after_sha256':'7402f19021f552b314f4bf0347456242c7e058b2b94cefd5a330647463a56437',
            'injected_call_lines':(176,224,241,301),
            'only_added_control_keywords':4,
            'normalized_ast_sha256':'3a87fa7087dd3af68ef633fd74b16e2cba164ed52d1676db6e389c440bd12cef'}}
    total=0
    for path,item in proof.items():
        raw=(ROOT/path).read_bytes();assert hashlib.sha256(raw).hexdigest()==item['after_sha256']
        tree=ast.parse(raw);count=0
        for node in ast.walk(tree):
            if not isinstance(node,ast.Call) or node.lineno not in item['injected_call_lines']:continue
            found=[kw for kw in node.keywords if kw.arg=='control']
            if not found:continue
            assert len(found)==1 and isinstance(found[0].value,ast.Lambda)
            assert not found[0].value.args.args and ast.literal_eval(found[0].value.body)=='RUNNING'
            node.keywords.remove(found[0]);count+=1
        assert count==item['only_added_control_keywords']
        assert hashlib.sha256(ast.dump(tree,include_attributes=False).encode()).hexdigest()==item['normalized_ast_sha256']
        total+=count
    assert total==17


def test_configuration_reads_exact_control_names_not_credentials_and_has_no_provider_import(tmp_path):
    path,_=known_control(tmp_path);seen=[]
    class ExplicitMap:
        def get(self,key):
            seen.append(key)
            return {'VERIFORGE_PLANNER_ENABLED':'1','VERIFORGE_CONTROL_PATH':str(path)}[key]
    assert config.RuntimeConfiguration.from_mapping(ExplicitMap()).control()=='RUNNING'
    assert seen==['VERIFORGE_PLANNER_ENABLED','VERIFORGE_CONTROL_PATH']
    tree=ast.parse(Path(config.__file__).read_bytes())
    imports=[node.module or '' for node in ast.walk(tree) if isinstance(node,ast.ImportFrom)]
    assert not any('provider' in name or 'redis' in name or 'rq'==name for name in imports)
    for node in tree.body:
        if isinstance(node,(ast.FunctionDef,ast.ClassDef)):continue
        assert not any(isinstance(child,ast.Attribute) and child.attr=='environ' for child in ast.walk(node))


@pytest.mark.parametrize('values',[{}, {'VERIFORGE_PLANNER_ENABLED':'true'}, {'VERIFORGE_PLANNER_ENABLED':True},
    {'VERIFORGE_PLANNER_ENABLED':'1'}, {'VERIFORGE_PLANNER_ENABLED':'1','VERIFORGE_CONTROL_PATH':'relative'},
    {'VERIFORGE_PLANNER_ENABLED':'1','VERIFORGE_CONTROL_PATH':17}])
def test_explicit_missing_invalid_configuration_is_stopped(values):
    reader=config.resolve_control(planner_owned=True,configuration=config.RuntimeConfiguration.from_mapping(values))
    assert reader()=='STOPPED'


def test_atomic_replacement_and_mutation_during_read_never_cache_running(tmp_path,monkeypatch):
    path,settings=known_control(tmp_path)
    assert settings.control()=='RUNNING'
    replacement=tmp_path/'next.json';replacement.write_text('{"control":"STOPPED"}');os.replace(replacement,path)
    assert settings.control()=='STOPPED'
    replacement.write_text('{"control":"RUNNING"}');os.replace(replacement,path)
    actual=runtime_control.os.read
    actual_stat=Path.stat;changed=False
    def change_after_read(fd,size):
        nonlocal changed
        raw=actual(fd,size)
        changed=True
        return raw
    def changed_identity(target,*args,**kwargs):
        value=actual_stat(target,*args,**kwargs)
        if target==path and changed:
            return SimpleNamespace(st_dev=value.st_dev,st_ino=value.st_ino+1,st_mode=value.st_mode,st_size=value.st_size,st_mtime_ns=value.st_mtime_ns)
        return value
    with monkeypatch.context() as scoped:
        scoped.setattr(runtime_control.os,'read',change_after_read)
        scoped.setattr(Path,'stat',changed_identity)
        assert settings.control()=='STOPPED'
    # Simulated in-read identity replacement above, real atomic replacement now
    # that the descriptor is closed (Windows need not permit replacement while open).
    replacement.write_text('{"control":"PAUSED"}');os.replace(replacement,path)
    assert settings.control()=='STOPPED'


def test_default_manual_paths_do_not_resolve_or_read_planner_configuration(db,tmp_path,monkeypatch):
    _,attempt=prepare(db,planner=False)
    def forbidden(*args,**kwargs):pytest.fail('manual default consulted planner config')
    monkeypatch.setattr(config.RuntimeConfiguration,'from_environment',classmethod(forbidden))
    monkeypatch.setattr(config.ControlReader,'__call__',forbidden)
    broker=Broker()
    assert dispatch.dispatch_pending(db,broker,clock=clock,configuration=object())['sent']==1
    assert broker.attempts==[attempt]
    assert worker.execute_attempt(attempt,sessions=db,runner=fake_runner(db,attempt),artifact_root=tmp_path,now=clock,configuration=object())['accepted']


def test_dedicated_fixed_rq_target_uses_no_real_broker(monkeypatch):
    queues={};connection=object()
    class Queue:
        def __init__(self,name,*,connection):self.connection=connection;self.calls=[];queues[name]=self
        def enqueue(self,*args,**kwargs):self.calls.append((args,kwargs))
    monkeypatch.setitem(sys.modules,'redis',SimpleNamespace(Redis=SimpleNamespace(from_url=lambda _:connection)))
    monkeypatch.setitem(sys.modules,'rq',SimpleNamespace(Queue=Queue))
    dispatcher=dispatch.RQDispatcher(redis_url='redis://explicit-fake')
    identifier='11111111-1111-4111-8111-111111111111';dispatcher.enqueue_planner(identifier)
    assert queues['veriforge-planner'].calls==[(("veriforge.core.planner_runtime.execute_intent",identifier),{'job_timeout':180,'result_ttl':0})]
    assert queues['veriforge'].calls==[]


def test_missing_configuration_blocks_planner_at_dispatch_and_claim(db,tmp_path):
    _,attempt=prepare(db);broker=Broker();disabled=config.RuntimeConfiguration()
    assert dispatch.dispatch_pending(db,broker,clock=clock,configuration=disabled)['sent']==0
    def forbidden(*args,**kwargs):pytest.fail('disabled planner invoked runner')
    assert worker.execute_attempt(attempt,sessions=db,runner=forbidden,artifact_root=tmp_path,now=clock,configuration=disabled)=={'accepted':False}
    assert not broker.attempts


def test_pausing_after_dispatch_prevents_claim_without_process_or_runner(db,tmp_path):
    _,attempt=prepare(db);path,settings=known_control(tmp_path)
    assert dispatch.dispatch_pending(db,Broker(),clock=clock,configuration=settings)['sent']==1
    path.write_text('{"control":"PAUSED"}')
    def forbidden(*args,**kwargs):pytest.fail('queued ID was mistaken for authorization')
    assert worker.execute_attempt(attempt,sessions=db,runner=forbidden,artifact_root=tmp_path,now=clock,configuration=settings)=={'accepted':False}


@pytest.mark.parametrize('planner',[False,True])
@pytest.mark.parametrize('runner_error',[False,True])
def test_stop_latched_through_resume_before_late_success_or_error(db,tmp_path,planner,runner_error):
    _,attempt=prepare(db,planner=planner);path,settings=known_control(tmp_path)
    def stop_then_resume(token):
        path.write_text('{"control":"STOPPED"}');assert token.is_set()
        path.write_text('{"control":"RUNNING"}');assert token.is_set()
        if runner_error:raise OSError('synthetic late error')
    kwargs={'configuration':settings} if planner else {'control':settings.control}
    output=worker.execute_attempt(attempt,sessions=db,runner=fake_runner(db,attempt,stop_then_resume),artifact_root=tmp_path,now=clock,**kwargs)
    assert not output['accepted']
    with db() as session:
        assert session.get(Attempt,attempt).result is None
        assert session.scalar(sa.select(sa.func.count()).select_from(Finding))==0
