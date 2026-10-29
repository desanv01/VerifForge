"""Global provider runtime fence; no automatic ownership expiry.

Revision ID: 12u2resources
Revises: 11u9investigations
"""
from datetime import datetime, timezone
from alembic import op
import sqlalchemy as sa

revision = "12u2resources"
down_revision = "11u9investigations"
branch_labels = None
depends_on = None


def upgrade():
    table = op.create_table("planner_runtime_slots",
        sa.Column("resource", sa.String(64), nullable=False),
        sa.Column("fence", sa.BigInteger(), nullable=False),
        sa.Column("state", sa.String(16), nullable=False),
        sa.Column("incarnation", sa.String(36), nullable=True),
        sa.Column("active_round_id", sa.String(36), nullable=True),
        sa.Column("campaign_id", sa.String(36), nullable=True),
        sa.Column("acquired_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("owner_lost_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("changed_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("resource"),
        sa.ForeignKeyConstraint(["active_round_id"], ["planner_cleanup.round_id"]),
        sa.ForeignKeyConstraint(["active_round_id", "campaign_id"], ["planner_rounds.id", "planner_rounds.campaign_id"]),
        sa.CheckConstraint("resource = 'local_planner_provider' AND fence >= 0"),
        sa.CheckConstraint("(state = 'idle' AND incarnation IS NULL AND active_round_id IS NULL AND campaign_id IS NULL AND acquired_at IS NULL AND owner_lost_at IS NULL) OR (state IN ('active','unknown') AND incarnation IS NOT NULL AND active_round_id IS NOT NULL AND campaign_id IS NOT NULL AND acquired_at IS NOT NULL AND ((state = 'active' AND owner_lost_at IS NULL) OR (state = 'unknown' AND owner_lost_at IS NOT NULL)))"),
        sa.CheckConstraint("(acquired_at IS NULL OR acquired_at <= changed_at) AND (owner_lost_at IS NULL OR (owner_lost_at >= acquired_at AND owner_lost_at <= changed_at))"))
    op.bulk_insert(table, [dict(resource="local_planner_provider", fence=0, state="idle", changed_at=datetime(1970, 1, 1, tzinfo=timezone.utc))])
    dialect = op.get_bind().dialect.name
    equal = "IS NOT DISTINCT FROM" if dialect == "postgresql" else "IS"
    same = " AND ".join(f"NEW.{field} {equal} OLD.{field}" for field in ("state", "incarnation", "active_round_id", "campaign_id", "acquired_at", "owner_lost_at", "changed_at"))
    association = " AND ".join(f"NEW.{field} {equal} OLD.{field}" for field in ("incarnation", "active_round_id", "campaign_id", "acquired_at"))
    settled = "EXISTS (SELECT 1 FROM planner_cleanup WHERE round_id = OLD.active_round_id AND local_state = 'settled')"
    legal = f"(NEW.resource = OLD.resource AND ((NEW.fence = OLD.fence AND {same}) OR (NEW.fence = OLD.fence + 1 AND NEW.changed_at >= OLD.changed_at AND ((OLD.state = 'idle' AND NEW.state = 'active' AND NEW.acquired_at = NEW.changed_at) OR (OLD.state = 'active' AND NEW.state = 'unknown' AND {association} AND NEW.owner_lost_at = NEW.changed_at) OR (OLD.state IN ('active','unknown') AND NEW.state = 'idle' AND {settled})))))"
    if dialect == "sqlite":
        op.execute("CREATE TRIGGER runtime_slot_no_delete BEFORE DELETE ON planner_runtime_slots BEGIN SELECT RAISE(ABORT, 'Immutable runtime fence history'); END")
        op.execute(f"CREATE TRIGGER runtime_slot_monotonic BEFORE UPDATE ON planner_runtime_slots WHEN NOT {legal} BEGIN SELECT RAISE(ABORT, 'Invalid runtime fence transition'); END")
    elif dialect == "postgresql":
        op.execute(f"CREATE FUNCTION veriforge_runtime_slot_guard() RETURNS trigger LANGUAGE plpgsql AS $$ BEGIN IF TG_OP = 'DELETE' THEN RAISE EXCEPTION 'Immutable runtime fence history'; END IF; IF NOT {legal} THEN RAISE EXCEPTION 'Invalid runtime fence transition'; END IF; RETURN NEW; END; $$")
        op.execute("CREATE TRIGGER runtime_slot_monotonic BEFORE UPDATE OR DELETE ON planner_runtime_slots FOR EACH ROW EXECUTE FUNCTION veriforge_runtime_slot_guard()")
    else:
        raise ValueError("Runtime resource supports SQLite and PostgreSQL only")


def downgrade():
    if op.get_bind().scalar(sa.text("SELECT COUNT(*) FROM planner_runtime_slots WHERE fence <> 0 OR state <> 'idle'")):
        raise ValueError("Cannot downgrade retained runtime fence history")
    op.drop_table("planner_runtime_slots")
    if op.get_bind().dialect.name == "postgresql":
        op.execute("DROP FUNCTION veriforge_runtime_slot_guard()")
