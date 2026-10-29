"""Independent resource/transport wiring: fake provider and disposable SQLite only."""
import asyncio
import inspect
from dataclasses import replace
from datetime import timedelta
from pathlib import Path
import runpy

from alembic import command
from alembic.config import Config
import pytest
from sqlalchemy import func,select

from veriforge.core import planner_transport as transport, planner_resources as resources, planner_cleanup as cleanup
from veriforge.core.catalog import seed_catalog
from veriforge.core.database import PlannerCleanup,PlannerRound,make_engine,session_factory
from veriforge.planner.durable_transport import TransportOutcome

ROOT=Path(__file__).resolve().parents[2]
BUILDERS=runpy.run_path(str(ROOT/'tests/core/test_planner_transport.py'))
queued,AT,Sessions,FakeProvider=map(BUILDERS.__getitem__,('queued','AT','Sessions','FakeMetaProvider'))
STAMP=AT+timedelta(milliseconds=200)


@pytest.fixture
def db(tmp_path):
    engine=make_engine('sqlite:///'+(tmp_path/'review.db').as_posix())
    config=Config(str(ROOT/'alembic.ini'))
    with engine.begin() as connection:
        config.attributes['connection']=connection;command.upgrade(config,'head')
    factory=session_factory(engine)
    with factory.begin() as session:seed_catalog(session)
    yield factory
    engine.dispose()


class RecordedAuthority:
    """Instrumentation only; every grant and phase uses real ResourceAuthority."""
    def __init__(self):
        self.real=transport.ResourceAuthority(resources.new_incarnation())
        self.grants=[];self.phases=[];self.before_validate=None;self.corrupt=None
    def claim(self,session,round_id,**kwargs):
        grant=self.real.claim(session,round_id,**kwargs)
        self.grants.append(grant)
        return self.corrupt(grant) if self.corrupt and grant else grant
    def validate(self,session,grant,**kwargs):
        self.phases.append(kwargs['phase'])
        if self.before_validate:self.before_validate(session,grant,kwargs['phase'])
        return self.real.validate(session,grant,**kwargs)


async def invoke(round_id,sessions,provider,authority,**options):
    return await transport.run_model_intent(round_id,sessions=sessions,provider=provider,authority=authority,
        now=lambda:STAMP,control=lambda:'RUNNING',poll_seconds=0.001,settlement_seconds=0.01,**options)


def forbid_finalizers(monkeypatch):
    # Resource reconciliation itself legitimately settles under its authority.
    # The forbidden path is a direct fallback from transport after refusal.
    for name in ('finish_model_round','fail_model_round'):
        original=getattr(transport.rounds,name)
        def guarded(*a,_original=original,**kw):
            if Path(inspect.currentframe().f_back.f_code.co_filename).resolve()!=Path(resources.__file__).resolve():
                pytest.fail('lost resource authority cannot fall back to raw round mutation')
            return _original(*a,**kw)
        monkeypatch.setattr(transport.rounds,name,guarded)


def test_resource_authority_is_mandatory_before_any_session_or_provider_access():
    with pytest.raises(TypeError,match='authority'):
        transport.run_model_intent('unclaimed',sessions=object(),provider=object(),
            now=lambda:STAMP,control=lambda:'RUNNING')


def test_real_atomic_claim_commits_cleanup_before_io_and_never_releases_slot(db):
    _,round_id=queued(db,key='independent-atomic')
    sessions=Sessions(db);authority=RecordedAuthority()
    async def effect(context,kwargs):
        assert sessions.active==0
        with db() as session:
            assert session.scalar(select(func.count()).select_from(PlannerCleanup))==1
            row=session.get(PlannerCleanup,round_id)
            assert row.local_state=='pending' and row.owner_token==authority.grants[0].cleanup_ticket.owner_token
            assert resources.safe_snapshot(session).state=='active'
        return BUILDERS['completion']()
    provider=FakeProvider(effect)
    result=asyncio.run(invoke(round_id,sessions,provider,authority))
    assert result.applied and provider.calls==1 and len(authority.grants)==1
    assert authority.phases[0]=='pre_io' and authority.phases[-1]=='completion'
    with db() as session:
        assert session.get(PlannerCleanup,round_id).local_state=='settled'
        assert resources.safe_snapshot(session).state=='active'  # Separate resource CAS owns release.
    assert not asyncio.run(invoke(round_id,sessions,provider,authority)).claimed and provider.calls==1


def test_postcommit_owner_loss_prevents_any_io_or_raw_finalization(db,monkeypatch):
    _,round_id=queued(db,key='independent-postcommit')
    sessions=Sessions(db);authority=RecordedAuthority();provider=FakeProvider()
    def lose():
        with db.begin() as session:
            assert resources.mark_owner_lost(session,authority.grants[0],role=resources.ROLE,at=STAMP)
    sessions.after_commit=lose
    forbid_finalizers(monkeypatch)
    result=asyncio.run(invoke(round_id,sessions,provider,authority))
    assert result.claimed and not result.applied and result.reason=='lease_revoked' and provider.calls==0
    assert authority.phases==['pre_io','completion']
    with db() as session:assert resources.safe_snapshot(session).state=='unknown'


def test_completion_owner_loss_cannot_apply_success(db,monkeypatch):
    _,round_id=queued(db,key='independent-final-fence');sessions=Sessions(db);authority=RecordedAuthority()
    async def effect(context,kwargs):
        with db.begin() as session:
            assert resources.mark_owner_lost(session,authority.grants[0],role=resources.ROLE,at=STAMP)
        return BUILDERS['completion']()
    provider=FakeProvider(effect);forbid_finalizers(monkeypatch)
    result=asyncio.run(invoke(round_id,sessions,provider,authority))
    assert not result.applied and provider.calls==1 and authority.phases[-1]=='completion'
    with db() as session:assert resources.safe_snapshot(session).state=='unknown'


@pytest.mark.parametrize('phase,allowed',[('pre_io',False),('running',True),('completion',True)])
def test_exact_settled_cleanup_allows_finish_but_never_fresh_io(db,phase,allowed):
    _,round_id=queued(db,key='independent-phase-'+phase);authority=RecordedAuthority()
    with db.begin() as session:grant=authority.claim(session,round_id,at=STAMP,control='RUNNING')
    with db.begin() as session:
        assert cleanup.acknowledge_settled(session,grant.cleanup_ticket,proof='cooperative_tasks_settled',at=STAMP)
    with db.begin() as session:
        assert authority.validate(session,grant,at=STAMP,control='RUNNING',phase=phase) is allowed


def test_registration_refusal_rolls_back_real_claim_and_never_dispatches(db,monkeypatch):
    _,round_id=queued(db,key='independent-registration');authority=RecordedAuthority();provider=FakeProvider()
    monkeypatch.setattr(cleanup,'record_pending',lambda *a,**kw:None)
    forbid_finalizers(monkeypatch)
    result=asyncio.run(invoke(round_id,Sessions(db),provider,authority))
    assert not result.claimed and result.reason=='not_claimed' and provider.calls==0
    with db() as session:
        assert session.get(PlannerRound,round_id).status=='queued'
        assert session.get(PlannerCleanup,round_id) is None
        assert resources.safe_snapshot(session).state=='idle'


@pytest.mark.parametrize('field',['fence','generation','ticket'])
def test_invalid_grant_rolls_back_entire_claim_transaction(db,field):
    _,round_id=queued(db,key='independent-invalid-'+field);authority=RecordedAuthority();provider=FakeProvider()
    if field=='fence':authority.corrupt=lambda g:replace(g,fence=True)
    elif field=='generation':authority.corrupt=lambda g:replace(g,lease=replace(g.lease,generation=True))
    else:authority.corrupt=lambda g:replace(g,cleanup_ticket=replace(g.cleanup_ticket,request_hash='0'*64))
    with pytest.raises(ValueError):asyncio.run(invoke(round_id,Sessions(db),provider,authority))
    assert provider.calls==0
    with db() as session:
        assert session.get(PlannerCleanup,round_id) is None
        assert session.get(PlannerRound,round_id).status=='queued'
        assert resources.safe_snapshot(session).state=='idle'


def test_early_reconstruction_failure_cannot_bypass_completion_fence(db,monkeypatch):
    _,round_id=queued(db,key='independent-reconstruction');authority=RecordedAuthority();provider=FakeProvider()
    def invalid(*a,**kw):raise ValueError('synthetic immutable reconstruction failure')
    def lose(session,grant,phase):
        assert phase=='completion'
        assert resources.mark_owner_lost(session,grant,role=resources.ROLE,at=STAMP)
    monkeypatch.setattr(transport,'reconstruct_admitted_request',invalid)
    authority.before_validate=lose;forbid_finalizers(monkeypatch)
    result=asyncio.run(invoke(round_id,Sessions(db),provider,authority))
    assert result.claimed and not result.applied and result.reason=='admission_invalid' and provider.calls==0
    assert authority.phases==['completion']


def test_observed_inner_task_controls_late_cleanup_without_resource_reentry(db,monkeypatch):
    _,round_id=queued(db,key='independent-observed-child');authority=RecordedAuthority();sessions=Sessions(db)
    async def scenario():
        release=asyncio.Event();children=[]
        async def wrapper(request,**kwargs):
            child=asyncio.create_task(release.wait());children.append(child);kwargs['task_observer'](child)
            return TransportOutcome(call_id=request.call_id,generation=request.generation,
                request_hash=request.admission.request_hash,requested_model=request.requested_model,
                state='uncertain',code='model_timeout',invocation_started=True,remote_outcome_unknown=True,
                cleanup_pending=True,elapsed_seconds=0.001)
        monkeypatch.setattr(transport,'invoke_admitted',wrapper)
        try:
            result=await invoke(round_id,sessions,FakeProvider(),authority)
            assert result.cleanup_pending and result.remote_outcome_unknown and not children[0].done()
            with db() as session:assert session.get(PlannerCleanup,round_id).local_state=='pending'
            before=list(authority.phases)
            def forbidden(*a,**kw):pytest.fail('cleanup callback may not acquire global resource authority')
            monkeypatch.setattr(resources,'_lock',forbidden)
            release.set();await children[0];await asyncio.sleep(0)
            assert authority.phases==before
            with db() as session:
                assert session.get(PlannerCleanup,round_id).local_state=='settled'
                assert resources.safe_snapshot(session).state=='active'
            assert result.cleanup_pending
        finally:
            release.set();await asyncio.gather(*children)
    asyncio.run(scenario())
