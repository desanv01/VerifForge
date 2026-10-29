"""Canonical recorded engineering projects, without executable RTL contracts.

Revision ID: 13engineeringprojects
Revises: 12u2resources
"""
from alembic import op
import sqlalchemy as sa

revision = '13engineeringprojects'
down_revision = '12u2resources'
branch_labels = None
depends_on = None
CHECK = "(kind = 'rtl_fixture' AND contract_id IS NOT NULL) OR (kind = 'engineering_recorded' AND contract_id IS NULL)"


def _sqlite_batch(upgrade):
    connection = op.get_bind()
    # SQLite's Python legacy transaction mode otherwise allows DDL/savepoints
    # outside the caller's actual transaction. Keep the entire rebuild atomic.
    if not connection.connection.driver_connection.in_transaction:
        connection.exec_driver_sql('BEGIN')
    prior = connection.exec_driver_sql('PRAGMA defer_foreign_keys').scalar()
    connection.exec_driver_sql('PRAGMA defer_foreign_keys=ON')
    with op.batch_alter_table('projects', recreate='always') as batch:
        if upgrade:
            batch.add_column(sa.Column('kind', sa.String(32), nullable=False, server_default='rtl_fixture'))
            batch.alter_column('contract_id', existing_type=sa.String(128), nullable=True)
            batch.create_check_constraint('ck_project_kind_contract', CHECK)
        else:
            batch.drop_constraint('ck_project_kind_contract', type_='check')
            batch.alter_column('contract_id', existing_type=sa.String(128), nullable=False)
            batch.drop_column('kind')
    # Recreating the same parent identity leaves SQLite's deferred DROP-table
    # counter set even when all FKs resolve. Validate the complete database
    # explicitly before restoring deferral; foreign_keys is NEVER disabled.
    if connection.exec_driver_sql('PRAGMA foreign_key_check').first() is not None:
        raise ValueError('Project migration foreign-key validation failed')
    connection.exec_driver_sql('PRAGMA defer_foreign_keys=OFF')
    if prior:
        connection.exec_driver_sql('PRAGMA defer_foreign_keys=ON')


def upgrade():
    dialect = op.get_bind().dialect.name
    if dialect == 'sqlite':
        _sqlite_batch(True)
        op.execute("CREATE TRIGGER project_kind_immutable BEFORE UPDATE OF kind ON projects WHEN NEW.kind IS NOT OLD.kind BEGIN SELECT RAISE(ABORT, 'Immutable project kind'); END")
        op.execute("CREATE TRIGGER engineering_project_immutable BEFORE UPDATE ON projects WHEN OLD.kind = 'engineering_recorded' AND (NEW.id IS NOT OLD.id OR NEW.owner IS NOT OLD.owner OR NEW.data IS NOT OLD.data OR NEW.contract_id IS NOT OLD.contract_id) BEGIN SELECT RAISE(ABORT, 'Immutable engineering project'); END")
        op.execute("CREATE TRIGGER engineering_project_no_delete BEFORE DELETE ON projects WHEN OLD.kind = 'engineering_recorded' BEGIN SELECT RAISE(ABORT, 'Immutable engineering project'); END")
    elif dialect == 'postgresql':
        op.add_column('projects', sa.Column('kind', sa.String(32), nullable=False, server_default='rtl_fixture'))
        op.alter_column('projects', 'contract_id', existing_type=sa.String(128), nullable=True)
        op.create_check_constraint('ck_project_kind_contract', 'projects', CHECK)
        op.execute("CREATE FUNCTION veriforge_project_kind_guard() RETURNS trigger LANGUAGE plpgsql AS $$ BEGIN IF TG_OP = 'DELETE' THEN IF OLD.kind = 'engineering_recorded' THEN RAISE EXCEPTION 'Immutable engineering project'; END IF; RETURN OLD; END IF; IF NEW.kind IS DISTINCT FROM OLD.kind OR (OLD.kind = 'engineering_recorded' AND (NEW.id IS DISTINCT FROM OLD.id OR NEW.owner IS DISTINCT FROM OLD.owner OR NEW.data::text IS DISTINCT FROM OLD.data::text OR NEW.contract_id IS DISTINCT FROM OLD.contract_id)) THEN RAISE EXCEPTION 'Immutable project kind or engineering metadata'; END IF; RETURN NEW; END; $$")
        op.execute("CREATE TRIGGER project_kind_guard BEFORE UPDATE OR DELETE ON projects FOR EACH ROW EXECUTE FUNCTION veriforge_project_kind_guard()")
    else:
        raise ValueError('Project kinds support SQLite and PostgreSQL only')


def downgrade():
    connection = op.get_bind()
    if connection.scalar(sa.text("SELECT COUNT(*) FROM projects WHERE kind <> 'rtl_fixture' OR contract_id IS NULL")):
        raise ValueError('Cannot downgrade retained engineering projects')
    if connection.dialect.name == 'sqlite':
        _sqlite_batch(False)
    else:
        op.execute('DROP TRIGGER project_kind_guard ON projects')
        op.execute('DROP FUNCTION veriforge_project_kind_guard()')
        op.drop_constraint('ck_project_kind_contract', 'projects', type_='check')
        op.alter_column('projects', 'contract_id', existing_type=sa.String(128), nullable=False)
        op.drop_column('projects', 'kind')
