"""Durable evidence export jobs and exact replay lineage.

Revision ID: 6bf022evidence
Revises: c67f9c9f19e5
"""
from alembic import op
import sqlalchemy as sa

revision = "6bf022evidence"
down_revision = "c67f9c9f19e5"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table("replay_links",
        sa.Column("campaign_id", sa.String(36), sa.ForeignKey("campaigns.id"), primary_key=True),
        sa.Column("source_attempt_id", sa.String(36), sa.ForeignKey("attempts.id"), nullable=False),
        sa.Column("owner", sa.String(128), nullable=False),
        sa.Column("request_key", sa.String(128), nullable=False),
        sa.UniqueConstraint("owner", "request_key"))
    op.create_table("evidence_exports",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("owner", sa.String(128), nullable=False),
        sa.Column("attempt_id", sa.String(36), sa.ForeignKey("attempts.id"), nullable=False),
        sa.Column("request_key", sa.String(128), nullable=False),
        sa.Column("status", sa.String(32), nullable=False),
        sa.Column("lease", sa.String(36), nullable=False),
        sa.Column("sent_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("sha256", sa.String(64), nullable=True),
        sa.Column("size_bytes", sa.Integer(), nullable=True),
        sa.Column("error_type", sa.String(128), nullable=True),
        sa.UniqueConstraint("owner", "request_key"))


def downgrade():
    op.drop_table("evidence_exports")
    op.drop_table("replay_links")
