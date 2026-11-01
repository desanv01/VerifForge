"""Additive durable repair state; existing evidence and campaign tables remain unchanged."""
from alembic import op
import sqlalchemy as sa

revision = "14u3repairjobs"
down_revision = "13engineeringprojects"
branch_labels = None
depends_on = None

def upgrade():
    op.create_table('repair_jobs',
        sa.Column('id', sa.String(36), nullable=False, primary_key=True),
        sa.Column('owner', sa.String(128), nullable=False, primary_key=False),
        sa.Column('create_key', sa.String(128), nullable=False, primary_key=False),
        sa.Column('request_hash', sa.String(64), nullable=False, primary_key=False),
        sa.Column('kind', sa.String(16), nullable=False, primary_key=False),
        sa.Column('original_attempt_id', sa.String(36), nullable=False, primary_key=False),
        sa.Column('original_plan', sa.JSON(), nullable=False, primary_key=False),
        sa.Column('original_result', sa.JSON(), nullable=False, primary_key=False),
        sa.Column('original_result_hash', sa.String(64), nullable=False, primary_key=False),
        sa.Column('candidate_fixture_id', sa.String(128), nullable=True, primary_key=False),
        sa.Column('suite', sa.JSON(), nullable=True, primary_key=False),
        sa.Column('suite_hash', sa.String(64), nullable=True, primary_key=False),
        sa.Column('provenance', sa.JSON(), nullable=False, primary_key=False),
        sa.Column('policy', sa.JSON(), nullable=False, primary_key=False),
        sa.Column('policy_hash', sa.String(64), nullable=False, primary_key=False),
        sa.Column('max_attempts', sa.Integer(), nullable=False, primary_key=False),
        sa.Column('deadline_at', sa.DateTime(timezone=True), nullable=False, primary_key=False),
        sa.Column('status', sa.String(16), nullable=False, primary_key=False),
        sa.Column('reason', sa.String(128), nullable=True, primary_key=False),
        sa.Column('active_case_id', sa.String(36), nullable=True, primary_key=False),
        sa.Column('best_case_id', sa.String(36), nullable=True, primary_key=False),
        sa.Column('next_ordinal', sa.Integer(), nullable=False, primary_key=False),
        sa.Column('reserved_cycles', sa.Integer(), nullable=False, primary_key=False),
        sa.Column('reserved_bytes', sa.Integer(), nullable=False, primary_key=False),
        sa.Column('version', sa.Integer(), nullable=False, primary_key=False),
        sa.Column('cleanup_status', sa.String(64), nullable=False, primary_key=False),
        sa.Column('report', sa.JSON(), nullable=True, primary_key=False),
        sa.Column('report_hash', sa.String(64), nullable=True, primary_key=False),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False, primary_key=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False, primary_key=False),
        sa.CheckConstraint("kind IN ('candidate','reduction')"),
        sa.CheckConstraint("status IN ('active','completed','cancelled','interrupted','incomplete')"),
        sa.CheckConstraint('next_ordinal > 0 AND reserved_cycles >= 0 AND reserved_bytes >= 0'),
        sa.ForeignKeyConstraint(['original_attempt_id'], ['attempts.id']),
        sa.UniqueConstraint('owner', 'create_key'),
    )
    op.create_table('repair_cases',
        sa.Column('id', sa.String(36), nullable=False, primary_key=True),
        sa.Column('job_id', sa.String(36), nullable=False, primary_key=False),
        sa.Column('ordinal', sa.Integer(), nullable=False, primary_key=False),
        sa.Column('role', sa.String(16), nullable=False, primary_key=False),
        sa.Column('label', sa.String(128), nullable=False, primary_key=False),
        sa.Column('plan', sa.JSON(), nullable=False, primary_key=False),
        sa.Column('plan_hash', sa.String(64), nullable=False, primary_key=False),
        sa.Column('campaign_id', sa.String(36), nullable=False, primary_key=False),
        sa.Column('experiment_id', sa.String(36), nullable=False, primary_key=False),
        sa.Column('attempt_id', sa.String(36), nullable=True, primary_key=False),
        sa.Column('execution_lease', sa.String(36), nullable=True, primary_key=False),
        sa.Column('status', sa.String(16), nullable=False, primary_key=False),
        sa.Column('result', sa.JSON(), nullable=True, primary_key=False),
        sa.Column('result_hash', sa.String(64), nullable=True, primary_key=False),
        sa.Column('accepted_progress', sa.Boolean(), nullable=False, primary_key=False),
        sa.CheckConstraint('ordinal > 0'),
        sa.CheckConstraint("role IN ('reproduce','witness','neighbor','regression','reduction')"),
        sa.CheckConstraint("status IN ('planned','queued','completed','cancelled','interrupted')"),
        sa.CheckConstraint("status != 'queued' OR (attempt_id IS NOT NULL AND execution_lease IS NOT NULL)"),
        sa.CheckConstraint('(attempt_id IS NULL AND execution_lease IS NULL) OR (attempt_id IS NOT NULL AND execution_lease IS NOT NULL)'),
        sa.ForeignKeyConstraint(['campaign_id'], ['campaigns.id']),
        sa.ForeignKeyConstraint(['job_id'], ['repair_jobs.id']),
        sa.ForeignKeyConstraint(['experiment_id', 'campaign_id'], ['experiments.id', 'experiments.campaign_id']),
        sa.ForeignKeyConstraint(['attempt_id', 'campaign_id'], ['attempts.id', 'attempts.campaign_id']),
        sa.UniqueConstraint('attempt_id'),
        sa.UniqueConstraint('campaign_id'),
        sa.UniqueConstraint('experiment_id'),
        sa.UniqueConstraint('job_id', 'ordinal'),
        sa.UniqueConstraint('job_id', 'plan_hash', 'role'),
    )
    op.create_index('ix_repair_cases_job_id', 'repair_cases', ['job_id'], unique=False)
    op.create_index('uq_repair_active_child', 'repair_cases', ['job_id'], unique=True, sqlite_where=sa.text("status = 'queued'"), postgresql_where=sa.text("status = 'queued'"))

def downgrade():
    raise RuntimeError("Repair execution/evidence history requires an explicit preservation review before removal")
