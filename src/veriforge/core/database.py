"""Database-owned state; no queue or process-global session is authoritative."""
from __future__ import annotations

import os
from datetime import datetime, timezone
from uuid import uuid4

from sqlalchemy import JSON, DateTime, ForeignKey, Integer, String, UniqueConstraint, create_engine, event
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
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=identifier)
    campaign_id: Mapped[str] = mapped_column(ForeignKey("campaigns.id"), index=True)
    lease: Mapped[str] = mapped_column(String(36), default=identifier)
    execution_status: Mapped[str] = mapped_column(String(32), default="queued")
    verdict: Mapped[str | None] = mapped_column(String(32), nullable=True)
    result: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    heartbeat_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


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
