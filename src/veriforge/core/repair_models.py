"""Additive durable repair state; execution stays in ordinary attempts/dispatch."""
from datetime import datetime
from sqlalchemy import (JSON, CheckConstraint, DateTime, ForeignKey, ForeignKeyConstraint,
                        Index, Integer, String, UniqueConstraint, event, inspect, text)
from sqlalchemy.orm import Mapped, mapped_column

from .database import Base, identifier, now


class RepairJob(Base):
    __tablename__ = "repair_jobs"
    __table_args__ = (
        UniqueConstraint("owner", "create_key"),
        CheckConstraint("kind IN ('candidate','reduction')"),
        CheckConstraint("status IN ('active','completed','cancelled','interrupted','incomplete')"),
        CheckConstraint("next_ordinal > 0 AND reserved_cycles >= 0 AND reserved_bytes >= 0"),
    )
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=identifier)
    owner: Mapped[str] = mapped_column(String(128), default="local")
    create_key: Mapped[str] = mapped_column(String(128))
    request_hash: Mapped[str] = mapped_column(String(64))
    kind: Mapped[str] = mapped_column(String(16))
    original_attempt_id: Mapped[str] = mapped_column(ForeignKey("attempts.id"))
    original_plan: Mapped[dict] = mapped_column(JSON)
    original_result: Mapped[dict] = mapped_column(JSON)
    original_result_hash: Mapped[str] = mapped_column(String(64))
    candidate_fixture_id: Mapped[str | None] = mapped_column(String(128), nullable=True)
    suite: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    suite_hash: Mapped[str | None] = mapped_column(String(64), nullable=True)
    provenance: Mapped[dict] = mapped_column(JSON)
    policy: Mapped[dict] = mapped_column(JSON)
    policy_hash: Mapped[str] = mapped_column(String(64))
    max_attempts: Mapped[int] = mapped_column(Integer)
    deadline_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    status: Mapped[str] = mapped_column(String(16), default="active")
    reason: Mapped[str | None] = mapped_column(String(128), nullable=True)
    active_case_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    best_case_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    next_ordinal: Mapped[int] = mapped_column(Integer, default=1)
    reserved_cycles: Mapped[int] = mapped_column(Integer, default=0)
    reserved_bytes: Mapped[int] = mapped_column(Integer, default=0)
    version: Mapped[int] = mapped_column(Integer, default=0)
    cleanup_status: Mapped[str] = mapped_column(String(64), default="not_started")
    report: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    report_hash: Mapped[str | None] = mapped_column(String(64), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)


class RepairCase(Base):
    __tablename__ = "repair_cases"
    __table_args__ = (
        UniqueConstraint("job_id", "ordinal"), UniqueConstraint("job_id", "plan_hash", "role"),
        UniqueConstraint("campaign_id"), UniqueConstraint("experiment_id"), UniqueConstraint("attempt_id"),
        Index("uq_repair_active_child", "job_id", unique=True,
              sqlite_where=text("status = 'queued'"), postgresql_where=text("status = 'queued'")),
        ForeignKeyConstraint(["experiment_id", "campaign_id"], ["experiments.id", "experiments.campaign_id"]),
        ForeignKeyConstraint(["attempt_id", "campaign_id"], ["attempts.id", "attempts.campaign_id"]),
        CheckConstraint("ordinal > 0"),
        CheckConstraint("role IN ('reproduce','witness','neighbor','regression','reduction')"),
        CheckConstraint("status IN ('planned','queued','completed','cancelled','interrupted')"),
        CheckConstraint("status != 'queued' OR (attempt_id IS NOT NULL AND execution_lease IS NOT NULL)"),
        CheckConstraint("(attempt_id IS NULL AND execution_lease IS NULL) OR (attempt_id IS NOT NULL AND execution_lease IS NOT NULL)"),
    )
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=identifier)
    job_id: Mapped[str] = mapped_column(ForeignKey("repair_jobs.id"), index=True)
    ordinal: Mapped[int] = mapped_column(Integer)
    role: Mapped[str] = mapped_column(String(16))
    label: Mapped[str] = mapped_column(String(128))
    plan: Mapped[dict] = mapped_column(JSON)
    plan_hash: Mapped[str] = mapped_column(String(64))
    campaign_id: Mapped[str] = mapped_column(ForeignKey("campaigns.id"))
    experiment_id: Mapped[str] = mapped_column(String(36))
    attempt_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    execution_lease: Mapped[str | None] = mapped_column(String(36), nullable=True)
    status: Mapped[str] = mapped_column(String(16), default="planned")
    result: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    result_hash: Mapped[str | None] = mapped_column(String(64), nullable=True)
    accepted_progress: Mapped[bool] = mapped_column(default=False)


def _freeze(mapper, connection, target):
    fixed = ("owner", "create_key", "request_hash", "kind", "original_attempt_id", "original_plan",
        "original_result", "original_result_hash", "candidate_fixture_id", "suite", "suite_hash", "provenance",
        "policy", "policy_hash", "max_attempts", "deadline_at", "created_at") if isinstance(target, RepairJob) else (
        "job_id", "ordinal", "role", "label", "plan", "plan_hash", "campaign_id", "experiment_id")
    state = inspect(target)
    if isinstance(target, RepairJob):
        history = state.attrs.status.history
        if history.has_changes() and history.deleted and history.deleted[0] != "active":
            raise ValueError("Settled repair jobs cannot resume")
    if any(state.attrs[name].history.has_changes() for name in fixed):
        raise ValueError("Repair admission and case identities are immutable")
    once = ("report", "report_hash") if isinstance(target, RepairJob) else (
        "attempt_id", "execution_lease", "result", "result_hash")
    for name in once:
        history = state.attrs[name].history
        if history.has_changes() and history.deleted and history.deleted[0] is not None:
            raise ValueError("Repair execution identity cannot be replaced")


for _model in (RepairJob, RepairCase):
    event.listen(_model, "before_update", _freeze)


def _no_delete(mapper, connection, target):
    raise ValueError("Repair history is retained")


for _model in (RepairJob, RepairCase):
    event.listen(_model, "before_delete", _no_delete)
