"""durable_investigations

Revision ID: 11u9investigations
Revises: 10u2cleanup
"""
from alembic import op
import sqlalchemy as sa


revision = '11u9investigations'
down_revision = '10u2cleanup'
branch_labels = None
depends_on = None

def upgrade():
    op.create_index('uq_campaign_project', 'campaigns', ['id', 'project_id'], unique=True)
    op.create_table('investigations',
    sa.Column('id', sa.String(length=36), nullable=False),
    sa.Column('owner', sa.String(length=128), nullable=False),
    sa.Column('project_id', sa.String(length=128), nullable=False),
    sa.Column('create_key', sa.String(length=128), nullable=False),
    sa.Column('request_hash', sa.String(length=64), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
    sa.ForeignKeyConstraint(['project_id'], ['projects.id'], ),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('id', 'project_id'),
    sa.UniqueConstraint('owner', 'create_key')
    )
    op.create_table('investigation_versions',
    sa.Column('id', sa.String(length=36), nullable=False),
    sa.Column('investigation_id', sa.String(length=36), nullable=False),
    sa.Column('project_id', sa.String(length=128), nullable=False),
    sa.Column('ordinal', sa.Integer(), nullable=False),
    sa.Column('admission_key', sa.String(length=128), nullable=False),
    sa.Column('schema_version', sa.String(length=128), nullable=False),
    sa.Column('revision_id', sa.String(length=128), nullable=False),
    sa.Column('revision_hash', sa.String(length=64), nullable=False),
    sa.Column('manifest_hash', sa.String(length=64), nullable=False),
    sa.Column('content_hash', sa.String(length=64), nullable=False),
    sa.Column('content', sa.LargeBinary(), nullable=False),
    sa.Column('scope', sa.String(length=32), nullable=False),
    sa.Column('execution_status', sa.String(length=16), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
    sa.CheckConstraint("ordinal > 0 AND scope = 'recorded_import' AND execution_status = 'not_run'"),
    sa.CheckConstraint('length(content) > 0 AND length(content) <= 262144'),
    sa.ForeignKeyConstraint(['investigation_id', 'project_id'], ['investigations.id', 'investigations.project_id'], ),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('id', 'project_id'),
    sa.UniqueConstraint('investigation_id', 'admission_key'),
    sa.UniqueConstraint('investigation_id', 'ordinal')
    )
    op.create_table('investigation_artifacts',
    sa.Column('id', sa.String(length=36), nullable=False),
    sa.Column('version_id', sa.String(length=36), nullable=False),
    sa.Column('artifact_id', sa.String(length=128), nullable=False),
    sa.Column('native_revision_id', sa.String(length=128), nullable=False),
    sa.Column('native_attempt_id', sa.String(length=128), nullable=True),
    sa.Column('identity_key', sa.String(length=64), nullable=False),
    sa.Column('sha256', sa.String(length=64), nullable=False),
    sa.Column('size_bytes', sa.Integer(), nullable=False),
    sa.Column('media_type', sa.String(length=128), nullable=False),
    sa.Column('role', sa.String(length=512), nullable=False),
    sa.Column('data', sa.LargeBinary(), nullable=False),
    sa.CheckConstraint('size_bytes >= 0 AND size_bytes <= 1048576 AND length(data) = size_bytes'),
    sa.ForeignKeyConstraint(['version_id'], ['investigation_versions.id'], ),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('version_id', 'identity_key')
    )
    op.create_table('investigation_execution_links',
    sa.Column('id', sa.String(length=36), nullable=False),
    sa.Column('version_id', sa.String(length=36), nullable=False),
    sa.Column('project_id', sa.String(length=128), nullable=False),
    sa.Column('campaign_id', sa.String(length=36), nullable=False),
    sa.Column('attempt_id', sa.String(length=36), nullable=True),
    sa.Column('experiment_id', sa.String(length=36), nullable=True),
    sa.Column('result_hash', sa.String(length=64), nullable=True),
    sa.Column('association_hash', sa.String(length=64), nullable=False),
    sa.Column('content', sa.LargeBinary(), nullable=False),
    sa.CheckConstraint('(attempt_id IS NULL AND experiment_id IS NULL AND result_hash IS NULL) OR (attempt_id IS NOT NULL AND experiment_id IS NOT NULL AND result_hash IS NOT NULL)'),
    sa.CheckConstraint('length(content) > 0 AND length(content) <= 1048576'),
    sa.ForeignKeyConstraint(['attempt_id', 'campaign_id'], ['attempts.id', 'attempts.campaign_id'], ),
    sa.ForeignKeyConstraint(['campaign_id', 'project_id'], ['campaigns.id', 'campaigns.project_id'], ),
    sa.ForeignKeyConstraint(['experiment_id', 'campaign_id'], ['experiments.id', 'experiments.campaign_id'], ),
    sa.ForeignKeyConstraint(['version_id', 'project_id'], ['investigation_versions.id', 'investigation_versions.project_id'], ),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('version_id', 'association_hash')
    )
    tables = ('investigations', 'investigation_versions', 'investigation_execution_links', 'investigation_artifacts')
    dialect = op.get_bind().dialect.name
    quota = "(SELECT COUNT(*) FROM investigation_artifacts WHERE version_id = NEW.version_id) >= 256 OR COALESCE((SELECT SUM(size_bytes) FROM investigation_artifacts WHERE version_id = NEW.version_id), 0) + NEW.size_bytes > 16777216"
    if dialect == 'sqlite':
        for table in tables:
            for operation in ('UPDATE', 'DELETE'):
                op.execute(f"CREATE TRIGGER immutable_{table}_{operation.lower()} BEFORE {operation} ON {table} BEGIN SELECT RAISE(ABORT, 'Immutable investigation history'); END")
        op.execute(f"CREATE TRIGGER investigation_artifact_quota BEFORE INSERT ON investigation_artifacts WHEN {quota} BEGIN SELECT RAISE(ABORT, 'Investigation artifact quota exceeded'); END")
    elif dialect == 'postgresql':
        op.execute("CREATE FUNCTION veriforge_investigation_immutable() RETURNS trigger LANGUAGE plpgsql AS $$ BEGIN RAISE EXCEPTION 'Immutable investigation history'; END; $$")
        for table in tables:
            op.execute(f"CREATE TRIGGER immutable_{table} BEFORE UPDATE OR DELETE ON {table} FOR EACH ROW EXECUTE FUNCTION veriforge_investigation_immutable()")
        op.execute(f"CREATE FUNCTION veriforge_investigation_quota() RETURNS trigger LANGUAGE plpgsql AS $$ BEGIN PERFORM 1 FROM investigation_versions WHERE id = NEW.version_id FOR UPDATE; IF {quota} THEN RAISE EXCEPTION 'Investigation artifact quota exceeded'; END IF; RETURN NEW; END; $$")
        op.execute("CREATE TRIGGER investigation_artifact_quota BEFORE INSERT ON investigation_artifacts FOR EACH ROW EXECUTE FUNCTION veriforge_investigation_quota()")
    else:
        raise ValueError('Investigations support SQLite and PostgreSQL only')

def downgrade():
    connection = op.get_bind()
    if any(connection.scalar(sa.text(f'SELECT COUNT(*) FROM {table}')) for table in
           ('investigations', 'investigation_versions', 'investigation_execution_links', 'investigation_artifacts')):
        raise ValueError('Cannot downgrade retained investigation history')
    op.drop_table('investigation_execution_links')
    op.drop_table('investigation_artifacts')
    op.drop_table('investigation_versions')
    op.drop_table('investigations')
    op.drop_index('uq_campaign_project', table_name='campaigns')
    if connection.dialect.name == 'postgresql':
        op.execute('DROP FUNCTION veriforge_investigation_quota()')
        op.execute('DROP FUNCTION veriforge_investigation_immutable()')
