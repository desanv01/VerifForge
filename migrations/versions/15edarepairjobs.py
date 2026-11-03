"""Add native EDA job intent; no existing rows or evidence are rewritten."""
from alembic import op
import sqlalchemy as sa

revision = '15edarepairjobs'
down_revision = '14u3repairjobs'
branch_labels = None
depends_on = None


def upgrade():
    op.create_table('eda_repair_jobs',
        sa.Column('id', sa.String(36), nullable=False, primary_key=True),
        sa.Column('owner', sa.String(128), nullable=False),
        sa.Column('create_key', sa.String(128), nullable=False),
        sa.Column('request_hash', sa.String(64), nullable=False),
        sa.Column('project_id', sa.String(128), nullable=False),
        sa.Column('original_version_id', sa.String(36), nullable=False),
        sa.Column('candidate_version_id', sa.String(36), nullable=False),
        sa.Column('original_hash', sa.String(64), nullable=False),
        sa.Column('candidate_hash', sa.String(64), nullable=False),
        sa.Column('domain', sa.String(16), nullable=False),
        sa.Column('configuration_hash', sa.String(64), nullable=False),
        sa.Column('policy_hash', sa.String(64), nullable=False),
        sa.Column('ticket', sa.JSON(), nullable=True),
        sa.Column('ticket_hash', sa.String(64), nullable=True),
        sa.Column('status', sa.String(16), nullable=False),
        sa.Column('dispatch_pending', sa.Boolean(), nullable=False),
        sa.Column('claim_token', sa.String(36), nullable=True),
        sa.Column('cancel_requested', sa.Boolean(), nullable=False),
        sa.Column('result', sa.JSON(), nullable=True),
        sa.Column('deadline_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint('owner', 'create_key'),
        sa.CheckConstraint("domain IN ('vivado','kicad')"),
        sa.CheckConstraint("status IN ('preparing','blocked','queued','claimed','completed','failed','cancelled','quarantined')"),
        sa.ForeignKeyConstraint(['project_id'], ['projects.id']),
        sa.ForeignKeyConstraint(['original_version_id'], ['investigation_versions.id']),
        sa.ForeignKeyConstraint(['candidate_version_id'], ['investigation_versions.id']),
    )


def downgrade():
    raise RuntimeError('Native EDA job/evidence history requires an explicit preservation review before removal')
