"""Application-owned native EDA request/intent state; additive migration required."""
from sqlalchemy import JSON,Boolean,CheckConstraint,DateTime,ForeignKey,Integer,String,UniqueConstraint,event,inspect
from sqlalchemy.orm import Mapped,mapped_column
from datetime import datetime
from .database import Base,identifier,now

class EdaRepairJob(Base):
    __tablename__='eda_repair_jobs'
    __table_args__=(UniqueConstraint('owner','create_key'),
        CheckConstraint("domain IN ('vivado','kicad')"),
        CheckConstraint("status IN ('preparing','blocked','queued','claimed','completed','failed','cancelled','quarantined')"))
    id:Mapped[str]=mapped_column(String(36),primary_key=True,default=identifier)
    owner:Mapped[str]=mapped_column(String(128),default='local')
    create_key:Mapped[str]=mapped_column(String(128))
    request_hash:Mapped[str]=mapped_column(String(64))
    project_id:Mapped[str]=mapped_column(ForeignKey('projects.id'))
    original_version_id:Mapped[str]=mapped_column(ForeignKey('investigation_versions.id'))
    candidate_version_id:Mapped[str]=mapped_column(ForeignKey('investigation_versions.id'))
    original_hash:Mapped[str]=mapped_column(String(64))
    candidate_hash:Mapped[str]=mapped_column(String(64))
    domain:Mapped[str]=mapped_column(String(16))
    configuration_hash:Mapped[str]=mapped_column(String(64))
    policy_hash:Mapped[str]=mapped_column(String(64))
    ticket:Mapped[dict|None]=mapped_column(JSON,nullable=True,active_history=True)
    ticket_hash:Mapped[str|None]=mapped_column(String(64),nullable=True,active_history=True)
    status:Mapped[str]=mapped_column(String(16),default='preparing')
    dispatch_pending:Mapped[bool]=mapped_column(Boolean,default=False)
    claim_token:Mapped[str|None]=mapped_column(String(36),nullable=True,active_history=True)
    cancel_requested:Mapped[bool]=mapped_column(Boolean,default=False)
    result:Mapped[dict|None]=mapped_column(JSON,nullable=True,active_history=True)
    deadline_at:Mapped[datetime]=mapped_column(DateTime(timezone=True))
    created_at:Mapped[datetime]=mapped_column(DateTime(timezone=True),default=now)

@event.listens_for(EdaRepairJob,'before_update')
def _immutable(mapper,connection,target):
    fixed=('owner','create_key','request_hash','project_id','original_version_id','candidate_version_id',
           'original_hash','candidate_hash','domain','configuration_hash','policy_hash','deadline_at')
    state=inspect(target)
    if any(state.attrs[n].history.has_changes() for n in fixed):raise ValueError('immutable EDA request changed')
    for name in ('ticket','ticket_hash','claim_token','result'):
        history=state.attrs[name].history
        if history.has_changes() and (not history.deleted or history.deleted[0] is not None):
            raise ValueError('immutable EDA ticket/claim/result changed')
