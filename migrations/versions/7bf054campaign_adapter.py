"""Persist a versioned trusted campaign adapter; preserve legacy FIFO records."""
from alembic import op
import sqlalchemy as sa

revision = "7bf054adapter"
down_revision = "6bf022evidence"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("campaigns", sa.Column("adapter_id", sa.String(128), nullable=False, server_default="rtl.fifo.v1"))


def downgrade():
    op.drop_column("campaigns", "adapter_id")
