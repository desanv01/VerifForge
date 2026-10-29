"""Immutable experiments and additive legacy attempt bindings.

No existing attempt/campaign/evidence identifiers or payloads are rewritten.
"""
from copy import deepcopy
import hashlib
import json
from uuid import NAMESPACE_URL, uuid5

from alembic import op
import sqlalchemy as sa

revision = "8u2experiments"
down_revision = "7bf054adapter"
branch_labels = None
depends_on = None


def _hash(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def upgrade():
    connection = op.get_bind()
    if connection.dialect.name not in {"sqlite", "postgresql"}:
        raise ValueError("Experiment migration supports SQLite and PostgreSQL only")
    # Validate all legacy provenance before DDL: SQLite schema changes need not
    # roll back like PostgreSQL when an operator presents inconsistent history.
    source_metadata = sa.MetaData()
    campaigns_before = sa.Table("campaigns", source_metadata, autoload_with=connection)
    contracts_before = sa.Table("contracts", source_metadata, autoload_with=connection)
    pending = []
    for campaign in connection.execute(sa.select(campaigns_before)).mappings().all():
        if not isinstance(campaign["plan"].get("experiment_id"), str):
            raise ValueError("Legacy campaign has no valid executable experiment ID")
        snapshot = deepcopy(connection.scalar(sa.select(contracts_before.c.data).where(contracts_before.c.id == campaign["contract_id"])))
        if "parameters" in campaign["plan"]:
            snapshot["parameters"] = deepcopy(campaign["plan"]["parameters"])
        if _hash(snapshot) != campaign["identity"].get("contract_hash"):
            raise ValueError("Legacy contract snapshot differs from stored identity; migration will not invent provenance")
        pending.append((dict(campaign), snapshot))
    op.add_column("campaigns", sa.Column("completion_policy", sa.String(32), nullable=False, server_default="single"))
    op.create_index("uq_attempt_campaign", "attempts", ["id", "campaign_id"], unique=True)
    op.create_table("experiments",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("campaign_id", sa.String(36), sa.ForeignKey("campaigns.id"), nullable=False),
        sa.Column("adapter_id", sa.String(128), nullable=False),
        sa.Column("ordinal", sa.Integer(), nullable=False),
        sa.Column("admission_key", sa.String(128), nullable=False),
        sa.Column("wire_id", sa.String(128), nullable=False),
        sa.Column("origin", sa.String(16), nullable=False),
        sa.Column("plan", sa.JSON(), nullable=False),
        sa.Column("plan_hash", sa.String(64), nullable=False),
        sa.Column("identity", sa.JSON(), nullable=False),
        sa.Column("contract_snapshot", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("id", "campaign_id", name="uq_experiment_campaign"),
        sa.UniqueConstraint("campaign_id", "ordinal", name="uq_experiment_ordinal"),
        sa.UniqueConstraint("campaign_id", "admission_key", name="uq_experiment_admission"),
        sa.UniqueConstraint("campaign_id", "wire_id", name="uq_experiment_wire_id"),
        sa.CheckConstraint("ordinal > 0", name="ck_experiment_ordinal"),
        sa.CheckConstraint("origin IN ('manual', 'replay', 'planner')", name="ck_experiment_origin"))
    op.create_table("attempt_experiments",
        sa.Column("attempt_id", sa.String(36), primary_key=True),
        sa.Column("campaign_id", sa.String(36), nullable=False),
        sa.Column("experiment_id", sa.String(36), nullable=False),
        sa.ForeignKeyConstraint(["attempt_id", "campaign_id"], ["attempts.id", "attempts.campaign_id"]),
        sa.ForeignKeyConstraint(["experiment_id", "campaign_id"], ["experiments.id", "experiments.campaign_id"]))
    metadata = sa.MetaData()
    attempts = sa.Table("attempts", metadata, autoload_with=connection)
    experiments = sa.Table("experiments", metadata, autoload_with=connection)
    bindings = sa.Table("attempt_experiments", metadata, autoload_with=connection)
    replay_links = sa.Table("replay_links", metadata, autoload_with=connection)
    replay_campaigns = set(connection.scalars(sa.select(replay_links.c.campaign_id)))
    for campaign, snapshot in pending:
        experiment_id = str(uuid5(NAMESPACE_URL, "veriforge:legacy-experiment:" + campaign["id"]))
        connection.execute(experiments.insert().values(id=experiment_id, campaign_id=campaign["id"], adapter_id=campaign["adapter_id"], ordinal=1,
            admission_key="initial", wire_id=campaign["plan"]["experiment_id"], origin="replay" if campaign["id"] in replay_campaigns else "manual", plan=campaign["plan"], plan_hash=_hash(campaign["plan"]),
            identity=campaign["identity"], contract_snapshot=snapshot, created_at=campaign["created_at"]))
        for attempt_id in connection.scalars(sa.select(attempts.c.id).where(attempts.c.campaign_id == campaign["id"])):
            connection.execute(bindings.insert().values(attempt_id=attempt_id, campaign_id=campaign["id"], experiment_id=experiment_id))
    # ORM guards also protect metadata.create_all callers. These database guards
    # additionally reject direct SQL rewrites in migrated SQLite/PostgreSQL.
    if connection.dialect.name == "sqlite":
        for table in ("experiments", "attempt_experiments"):
            for action in ("UPDATE", "DELETE"):
                op.execute(f"CREATE TRIGGER immutable_{table}_{action.lower()} BEFORE {action} ON {table} BEGIN SELECT RAISE(ABORT, 'Immutable experiment record'); END")
    elif connection.dialect.name == "postgresql":
        op.execute("CREATE FUNCTION veriforge_immutable_experiment() RETURNS trigger LANGUAGE plpgsql AS $$ BEGIN RAISE EXCEPTION 'Immutable experiment record'; END; $$")
        for table in ("experiments", "attempt_experiments"):
            op.execute(f"CREATE TRIGGER immutable_{table} BEFORE UPDATE OR DELETE ON {table} FOR EACH ROW EXECUTE FUNCTION veriforge_immutable_experiment()")
    else:
        raise ValueError("Experiment migration supports SQLite and PostgreSQL only")


def downgrade():
    connection = op.get_bind()
    # Do not drop real multi-experiment history into the old single-plan schema.
    count = connection.scalar(sa.text("SELECT COUNT(*) FROM experiments WHERE ordinal > 1"))
    sequential = connection.scalar(sa.text("SELECT COUNT(*) FROM campaigns WHERE completion_policy <> 'single'"))
    if count or sequential:
        raise ValueError("Cannot downgrade sequential or multi-experiment history")
    op.drop_table("attempt_experiments")
    op.drop_table("experiments")
    if connection.dialect.name == "postgresql":
        op.execute("DROP FUNCTION veriforge_immutable_experiment()")
    op.drop_index("uq_attempt_campaign", table_name="attempts")
    op.drop_column("campaigns", "completion_policy")
