"""Database-owned state; no queue or process-global session is authoritative."""
from __future__ import annotations

import os
from datetime import datetime, timezone
from uuid import uuid4

from sqlalchemy import JSON, CheckConstraint, DateTime, ForeignKey, ForeignKeyConstraint, Index, Integer, LargeBinary, String, UniqueConstraint, create_engine, event, inspect
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, sessionmaker


def now() -> datetime:
    return datetime.now(timezone.utc)


def identifier() -> str:
    return str(uuid4())


class Base(DeclarativeBase):
    pass


class Project(Base):
    __tablename__ = "projects"
    id: Mapped[str] = mapped_column(String(128), primary_key=True)
    owner: Mapped[str] = mapped_column(String(128), default="local")
    data: Mapped[dict] = mapped_column(JSON)
    contract_id: Mapped[str] = mapped_column(ForeignKey("contracts.id"))


class Contract(Base):
    __tablename__ = "contracts"
    id: Mapped[str] = mapped_column(String(128), primary_key=True)
    data: Mapped[dict] = mapped_column(JSON)
    identity: Mapped[dict] = mapped_column(JSON)


class Campaign(Base):
    __tablename__ = "campaigns"
    __table_args__ = (UniqueConstraint("owner", "create_key"),)
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=identifier)
    owner: Mapped[str] = mapped_column(String(128), default="local")
    adapter_id: Mapped[str] = mapped_column(String(128), default="rtl.fifo.v1", server_default="rtl.fifo.v1")
    completion_policy: Mapped[str] = mapped_column(String(32), default="single", server_default="single")
    project_id: Mapped[str] = mapped_column(ForeignKey("projects.id"))
    contract_id: Mapped[str] = mapped_column(ForeignKey("contracts.id"))
    create_key: Mapped[str] = mapped_column(String(128))
    request_hash: Mapped[str] = mapped_column(String(64))
    plan: Mapped[dict] = mapped_column(JSON)
    identity: Mapped[dict] = mapped_column(JSON)
    execution_status: Mapped[str] = mapped_column(String(32), default="draft")
    verdict: Mapped[str | None] = mapped_column(String(32), nullable=True)
    version: Mapped[int] = mapped_column(Integer, default=0)
    event_sequence: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)


class Attempt(Base):
    __tablename__ = "attempts"
    __table_args__ = (Index("uq_attempt_campaign", "id", "campaign_id", unique=True),)
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=identifier)
    campaign_id: Mapped[str] = mapped_column(ForeignKey("campaigns.id"), index=True)
    lease: Mapped[str] = mapped_column(String(36), default=identifier)
    execution_status: Mapped[str] = mapped_column(String(32), default="queued")
    verdict: Mapped[str | None] = mapped_column(String(32), nullable=True)
    result: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    heartbeat_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class ExperimentRecord(Base):
    """Immutable submitted input; DB identity differs from adapter experiment_id."""
    __tablename__ = "experiments"
    __table_args__ = (
        UniqueConstraint("id", "campaign_id", name="uq_experiment_campaign"),
        UniqueConstraint("campaign_id", "ordinal", name="uq_experiment_ordinal"),
        UniqueConstraint("campaign_id", "admission_key", name="uq_experiment_admission"),
        UniqueConstraint("campaign_id", "wire_id", name="uq_experiment_wire_id"),
        CheckConstraint("ordinal > 0", name="ck_experiment_ordinal"),
        CheckConstraint("origin IN ('manual', 'replay', 'planner')", name="ck_experiment_origin"),
    )
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=identifier)
    campaign_id: Mapped[str] = mapped_column(ForeignKey("campaigns.id"))
    adapter_id: Mapped[str] = mapped_column(String(128))
    ordinal: Mapped[int] = mapped_column(Integer)
    admission_key: Mapped[str] = mapped_column(String(128))
    wire_id: Mapped[str] = mapped_column(String(128))
    origin: Mapped[str] = mapped_column(String(16))
    plan: Mapped[dict] = mapped_column(JSON)
    plan_hash: Mapped[str] = mapped_column(String(64))
    identity: Mapped[dict] = mapped_column(JSON)
    contract_snapshot: Mapped[dict] = mapped_column(JSON)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)


class AttemptExperiment(Base):
    __tablename__ = "attempt_experiments"
    __table_args__ = (
        ForeignKeyConstraint(["attempt_id", "campaign_id"], ["attempts.id", "attempts.campaign_id"]),
        ForeignKeyConstraint(["experiment_id", "campaign_id"], ["experiments.id", "experiments.campaign_id"]),
    )
    attempt_id: Mapped[str] = mapped_column(String(36), primary_key=True)
    campaign_id: Mapped[str] = mapped_column(String(36), nullable=False)
    experiment_id: Mapped[str] = mapped_column(String(36), nullable=False)


def _immutable_record(mapper, connection, target):
    raise ValueError("Experiment records and attempt bindings are immutable")


for _record_type in (ExperimentRecord, AttemptExperiment):
    event.listen(_record_type, "before_update", _immutable_record)
    event.listen(_record_type, "before_delete", _immutable_record)


class Dispatch(Base):
    __tablename__ = "dispatches"
    attempt_id: Mapped[str] = mapped_column(ForeignKey("attempts.id"), primary_key=True)
    sent_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    error: Mapped[str | None] = mapped_column(String(256), nullable=True)


class RunEvent(Base):
    __tablename__ = "run_events"
    __table_args__ = (UniqueConstraint("campaign_id", "sequence"),)
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=identifier)
    campaign_id: Mapped[str] = mapped_column(ForeignKey("campaigns.id"), index=True)
    sequence: Mapped[int] = mapped_column(Integer)
    kind: Mapped[str] = mapped_column(String(64))
    payload: Mapped[dict] = mapped_column(JSON)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)


class Finding(Base):
    __tablename__ = "findings"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=identifier)
    campaign_id: Mapped[str] = mapped_column(ForeignKey("campaigns.id"), index=True)
    attempt_id: Mapped[str] = mapped_column(ForeignKey("attempts.id"), unique=True)
    data: Mapped[dict] = mapped_column(JSON)


class ReplayLink(Base):
    __tablename__ = "replay_links"
    campaign_id: Mapped[str] = mapped_column(ForeignKey("campaigns.id"), primary_key=True)
    source_attempt_id: Mapped[str] = mapped_column(ForeignKey("attempts.id"))
    owner: Mapped[str] = mapped_column(String(128), default="local")
    request_key: Mapped[str] = mapped_column(String(128))
    __table_args__ = (UniqueConstraint("owner", "request_key"),)


class EvidenceExport(Base):
    __tablename__ = "evidence_exports"
    __table_args__ = (UniqueConstraint("owner", "request_key"),)
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=identifier)
    owner: Mapped[str] = mapped_column(String(128), default="local")
    attempt_id: Mapped[str] = mapped_column(ForeignKey("attempts.id"))
    request_key: Mapped[str] = mapped_column(String(128))
    status: Mapped[str] = mapped_column(String(32), default="queued")
    lease: Mapped[str] = mapped_column(String(36), default=identifier)
    sent_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    sha256: Mapped[str | None] = mapped_column(String(64), nullable=True)
    size_bytes: Mapped[int | None] = mapped_column(Integer, nullable=True)
    error_type: Mapped[str | None] = mapped_column(String(128), nullable=True)


class PlannerState(Base):
    __tablename__ = "planner_states"
    __table_args__ = (
        CheckConstraint("phase IN ('ready', 'round_queued', 'model_running', 'waiting_execution', 'settled', 'cancelled', 'interrupted')"),
        CheckConstraint("strategy IN ('mock_planner', 'meta')"),
        CheckConstraint("generation >= 0 AND invalid_count >= 0 AND duplicate_count >= 0 AND transient_count >= 0"),
    )
    campaign_id: Mapped[str] = mapped_column(ForeignKey("campaigns.id"), primary_key=True)
    policy: Mapped[dict] = mapped_column(JSON)
    binding: Mapped[dict] = mapped_column(JSON)
    pricing: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    requested_model: Mapped[str] = mapped_column(String(128))
    strategy: Mapped[str] = mapped_column(String(32))
    steps_version: Mapped[str] = mapped_column(String(64))
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    deadline_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    phase: Mapped[str] = mapped_column(String(32), default="ready")
    generation: Mapped[int] = mapped_column(Integer, default=0)
    invalid_count: Mapped[int] = mapped_column(Integer, default=0)
    duplicate_count: Mapped[int] = mapped_column(Integer, default=0)
    transient_count: Mapped[int] = mapped_column(Integer, default=0)
    correction: Mapped[str | None] = mapped_column(String(512), nullable=True)
    stop_reason: Mapped[str | None] = mapped_column(String(64), nullable=True)
    not_before: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class PlannerRound(Base):
    __tablename__ = "planner_rounds"
    __table_args__ = (UniqueConstraint("campaign_id", "ordinal"), UniqueConstraint("id", "campaign_id"),
        ForeignKeyConstraint(["experiment_id", "campaign_id"], ["experiments.id", "experiments.campaign_id"]),
        CheckConstraint("ordinal > 0 AND generation >= 0"),
        CheckConstraint("status IN ('queued', 'running', 'accepted', 'invalid', 'duplicate', 'failed', 'stopped', 'uncertain', 'cancelled')"))
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=identifier)
    campaign_id: Mapped[str] = mapped_column(ForeignKey("planner_states.campaign_id"))
    ordinal: Mapped[int] = mapped_column(Integer)
    generation: Mapped[int] = mapped_column(Integer)
    lease: Mapped[str] = mapped_column(String(36), default=identifier)
    status: Mapped[str] = mapped_column(String(32), default="queued")
    heartbeat_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    fingerprint: Mapped[str | None] = mapped_column(String(64), nullable=True)
    experiment_id: Mapped[str | None] = mapped_column(String(36), nullable=True)


class PlannerModelCall(Base):
    __tablename__ = "planner_model_calls"
    round_id: Mapped[str] = mapped_column(ForeignKey("planner_rounds.id"), primary_key=True)
    requested_model: Mapped[str] = mapped_column(String(128))
    request_body: Mapped[bytes] = mapped_column(LargeBinary)
    request_hash: Mapped[str] = mapped_column(String(64))
    schema_hash: Mapped[str] = mapped_column(String(64))
    input_allowance: Mapped[int] = mapped_column(Integer)
    context: Mapped[dict] = mapped_column(JSON)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    deadline_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    outcome: Mapped[str | None] = mapped_column(String(64), nullable=True)
    usage: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    returned_model: Mapped[str | None] = mapped_column(String(128), nullable=True)
    actual_cost_usd: Mapped[str | None] = mapped_column(String(128), nullable=True)


class PlannerCleanup(Base):
    """Local task ownership; never establishes a remote call outcome."""
    __tablename__ = "planner_cleanup"
    __table_args__ = (
        ForeignKeyConstraint(["round_id", "campaign_id"], ["planner_rounds.id", "planner_rounds.campaign_id"]),
        CheckConstraint("generation >= 0"),
        CheckConstraint("local_state IN ('pending', 'unknown', 'settled')"),
        CheckConstraint("(local_state = 'pending' AND owner_lost_at IS NULL AND settled_at IS NULL AND settlement_proof IS NULL) OR (local_state = 'unknown' AND owner_lost_at IS NOT NULL AND settled_at IS NULL AND settlement_proof IS NULL) OR (local_state = 'settled' AND settled_at IS NOT NULL AND settlement_proof IS NOT NULL AND settlement_proof = 'cooperative_tasks_settled')"),
        CheckConstraint("(owner_lost_at IS NULL OR owner_lost_at >= registered_at) AND (settled_at IS NULL OR (settled_at >= registered_at AND (owner_lost_at IS NULL OR settled_at >= owner_lost_at)))"))
    round_id: Mapped[str] = mapped_column(ForeignKey("planner_model_calls.round_id"), primary_key=True)
    campaign_id: Mapped[str] = mapped_column(String(36))
    generation: Mapped[int] = mapped_column(Integer)
    execution_lease: Mapped[str] = mapped_column(String(36))
    request_hash: Mapped[str] = mapped_column(String(64))
    owner_token: Mapped[str] = mapped_column(String(36), default=identifier)
    registered_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    local_state: Mapped[str] = mapped_column(String(16), default="pending")
    owner_lost_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    settled_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    settlement_proof: Mapped[str | None] = mapped_column(String(32), nullable=True)


class PlannerReservation(Base):
    __tablename__ = "planner_reservations"
    __table_args__ = (UniqueConstraint("campaign_id", "admission_key"),
        ForeignKeyConstraint(["round_id", "campaign_id"], ["planner_rounds.id", "planner_rounds.campaign_id"]),
        ForeignKeyConstraint(["attempt_id", "campaign_id"], ["attempts.id", "attempts.campaign_id"]),
        CheckConstraint("reserved_tokens >= 0 AND reserved_cycles >= 0"))
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=identifier)
    campaign_id: Mapped[str] = mapped_column(ForeignKey("planner_states.campaign_id"))
    admission_key: Mapped[str] = mapped_column(String(128))
    round_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    attempt_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    reserved_tokens: Mapped[int] = mapped_column(Integer, default=0)
    reserved_cycles: Mapped[int] = mapped_column(Integer, default=0)
    reserved_cost_usd: Mapped[str | None] = mapped_column(String(128), nullable=True)
    reserved_seconds: Mapped[str] = mapped_column(String(128), default="0")


class PlannerFeedback(Base):
    __tablename__ = "planner_feedback"
    __table_args__ = (
        ForeignKeyConstraint(["attempt_id", "campaign_id"], ["attempts.id", "attempts.campaign_id"]),
        ForeignKeyConstraint(["experiment_id", "campaign_id"], ["experiments.id", "experiments.campaign_id"]))
    attempt_id: Mapped[str] = mapped_column(String(36), primary_key=True)
    campaign_id: Mapped[str] = mapped_column(ForeignKey("planner_states.campaign_id"))
    experiment_id: Mapped[str] = mapped_column(String(36))
    result_hash: Mapped[str] = mapped_column(String(64))
    feedback: Mapped[dict] = mapped_column(JSON)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class PlannerIntent(Base):
    __tablename__ = "planner_intents"
    __table_args__ = (UniqueConstraint("campaign_id", "generation", "kind"),
        ForeignKeyConstraint(["round_id", "campaign_id"], ["planner_rounds.id", "planner_rounds.campaign_id"]),
        CheckConstraint("kind IN ('advance', 'model') AND generation >= 0"))
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=identifier)
    campaign_id: Mapped[str] = mapped_column(ForeignKey("planner_states.campaign_id"))
    generation: Mapped[int] = mapped_column(Integer)
    kind: Mapped[str] = mapped_column(String(16))
    round_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    sent_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


for _record_type in (PlannerReservation, PlannerFeedback):
    event.listen(_record_type, "before_update", _immutable_record)
    event.listen(_record_type, "before_delete", _immutable_record)


def _frozen_fields(mapper, connection, target):
    fields = {
        PlannerState: ("campaign_id", "policy", "binding", "pricing", "requested_model", "strategy", "steps_version", "started_at", "deadline_at"),
        PlannerModelCall: ("round_id", "requested_model", "request_body", "request_hash", "schema_hash", "input_allowance", "context"),
        PlannerRound: ("id", "campaign_id", "ordinal", "generation"),
        PlannerIntent: ("id", "campaign_id", "generation", "kind", "round_id"),
        PlannerCleanup: ("round_id", "campaign_id", "generation", "execution_lease", "request_hash", "owner_token", "registered_at"),
    }[type(target)]
    if any(inspect(target).attrs[name].history.has_changes() for name in fields):
        raise ValueError("Admitted planner identity is immutable")


for _record_type in (PlannerState, PlannerModelCall, PlannerRound, PlannerIntent, PlannerCleanup):
    event.listen(_record_type, "before_update", _frozen_fields)
    event.listen(_record_type, "before_delete", _immutable_record)


@event.listens_for(PlannerCleanup, "before_update")
def _cleanup_transition(mapper, connection, target):
    attrs = inspect(target).attrs
    previous = attrs.local_state.history.deleted
    if previous and (previous[0] == "settled" or previous[0] == "unknown" and target.local_state == "pending"):
        raise ValueError("Cleanup state cannot move backwards")
    lost = attrs.owner_lost_at.history
    if lost.has_changes() and lost.deleted and lost.deleted[0] is not None:
        raise ValueError("Recorded owner loss is immutable")
    if target.local_state == "settled" and not attrs.local_state.history.has_changes() and any(
        attrs[name].history.has_changes() for name in ("settled_at", "settlement_proof", "owner_lost_at")):
        raise ValueError("Cleanup settlement is immutable")


def database_url() -> str:
    return os.getenv("VERIFORGE_DATABASE_URL", "sqlite:///./veriforge.db")


def make_engine(url: str | None = None):
    url = url or database_url()
    engine = create_engine(url, connect_args={"check_same_thread": False, "timeout": 30} if url.startswith("sqlite") else {}, pool_pre_ping=True)
    if url.startswith("sqlite"):
        @event.listens_for(engine, "connect")
        def configure_sqlite(connection, _):
            connection.execute("PRAGMA foreign_keys=ON")
            connection.execute("PRAGMA journal_mode=WAL")
    return engine


def session_factory(engine):
    return sessionmaker(engine, expire_on_commit=False)
