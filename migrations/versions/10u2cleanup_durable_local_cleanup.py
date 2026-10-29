"""Durable local provider cleanup; independent of remote outcomes.

Revision ID: 10u2cleanup
Revises: 9u2rounds
"""
from alembic import op
import sqlalchemy as sa

revision = "10u2cleanup"
down_revision = "9u2rounds"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table("planner_cleanup",
        sa.Column("round_id", sa.String(36), nullable=False),
        sa.Column("campaign_id", sa.String(36), nullable=False),
        sa.Column("generation", sa.Integer(), nullable=False),
        sa.Column("execution_lease", sa.String(36), nullable=False),
        sa.Column("request_hash", sa.String(64), nullable=False),
        sa.Column("owner_token", sa.String(36), nullable=False),
        sa.Column("registered_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("local_state", sa.String(16), nullable=False),
        sa.Column("owner_lost_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("settled_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("settlement_proof", sa.String(32), nullable=True),
        sa.PrimaryKeyConstraint("round_id"),
        sa.ForeignKeyConstraint(["round_id"], ["planner_model_calls.round_id"]),
        sa.ForeignKeyConstraint(["round_id", "campaign_id"], ["planner_rounds.id", "planner_rounds.campaign_id"]),
        sa.CheckConstraint("generation >= 0"),
        sa.CheckConstraint("local_state IN ('pending', 'unknown', 'settled')"),
        sa.CheckConstraint("(local_state = 'pending' AND owner_lost_at IS NULL AND settled_at IS NULL AND settlement_proof IS NULL) OR (local_state = 'unknown' AND owner_lost_at IS NOT NULL AND settled_at IS NULL AND settlement_proof IS NULL) OR (local_state = 'settled' AND settled_at IS NOT NULL AND settlement_proof IS NOT NULL AND settlement_proof = 'cooperative_tasks_settled')"),
        sa.CheckConstraint("(owner_lost_at IS NULL OR owner_lost_at >= registered_at) AND (settled_at IS NULL OR (settled_at >= registered_at AND (owner_lost_at IS NULL OR settled_at >= owner_lost_at)))"))
    dialect = op.get_bind().dialect.name
    identity = "round_id,campaign_id,generation,execution_lease,request_hash,owner_token,registered_at"
    invalid_transition = "OLD.local_state = 'settled' OR (OLD.local_state = 'unknown' AND NEW.local_state = 'pending') OR (OLD.owner_lost_at IS NOT NULL AND (NEW.owner_lost_at IS NULL OR NEW.owner_lost_at <> OLD.owner_lost_at))"
    if dialect == "sqlite":
        op.execute(f"CREATE TRIGGER immutable_planner_cleanup_identity BEFORE UPDATE OF {identity} ON planner_cleanup BEGIN SELECT RAISE(ABORT, 'Immutable cleanup identity'); END")
        op.execute("CREATE TRIGGER immutable_planner_cleanup_delete BEFORE DELETE ON planner_cleanup BEGIN SELECT RAISE(ABORT, 'Immutable cleanup history'); END")
        op.execute(f"CREATE TRIGGER monotonic_planner_cleanup BEFORE UPDATE ON planner_cleanup WHEN {invalid_transition} BEGIN SELECT RAISE(ABORT, 'Immutable cleanup settlement or owner loss'); END")
    elif dialect == "postgresql":
        op.execute("CREATE FUNCTION veriforge_cleanup_immutable() RETURNS trigger LANGUAGE plpgsql AS $$ BEGIN RAISE EXCEPTION 'Immutable cleanup history'; END; $$")
        op.execute(f"CREATE FUNCTION veriforge_cleanup_transition() RETURNS trigger LANGUAGE plpgsql AS $$ BEGIN IF {invalid_transition} THEN RAISE EXCEPTION 'Immutable cleanup settlement or owner loss'; END IF; RETURN NEW; END; $$")
        op.execute(f"CREATE TRIGGER immutable_planner_cleanup_identity BEFORE UPDATE OF {identity} ON planner_cleanup FOR EACH ROW EXECUTE FUNCTION veriforge_cleanup_immutable()")
        op.execute("CREATE TRIGGER immutable_planner_cleanup_delete BEFORE DELETE ON planner_cleanup FOR EACH ROW EXECUTE FUNCTION veriforge_cleanup_immutable()")
        op.execute("CREATE TRIGGER monotonic_planner_cleanup BEFORE UPDATE ON planner_cleanup FOR EACH ROW EXECUTE FUNCTION veriforge_cleanup_transition()")
    else:
        raise ValueError("Cleanup state supports SQLite and PostgreSQL only")


def downgrade():
    if op.get_bind().scalar(sa.text("SELECT COUNT(*) FROM planner_cleanup")):
        raise ValueError("Cannot downgrade retained cleanup ownership history")
    op.drop_table("planner_cleanup")
    if op.get_bind().dialect.name == "postgresql":
        op.execute("DROP FUNCTION veriforge_cleanup_transition()")
        op.execute("DROP FUNCTION veriforge_cleanup_immutable()")
