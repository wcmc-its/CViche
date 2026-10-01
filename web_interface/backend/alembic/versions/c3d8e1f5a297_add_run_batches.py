"""add run_batches table and runs.batch_id

Batch upload (#1114). run_batches holds one row per batch a user submits:
its short letters-only id, the submitter, when, and how many valid files the
user selected (the batch view compares that with the runs that actually got
created). runs.batch_id is a nullable, indexed FK to it; NULL for every
single-upload run, including every run that predates this migration.

The runs change goes through batch_alter_table so the FK can be added and
dropped on SQLite too (test_db_indexes.py runs this chain on SQLite); on
MySQL it is plain ALTER TABLE statements.

Revision ID: c3d8e1f5a297
Revises: b5e8d2f6a143
Create Date: 2026-10-01
"""
from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'c3d8e1f5a297'
down_revision: str | Sequence[str] | None = 'b5e8d2f6a143'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

BATCH_FK_NAME = 'fk_runs_batch_id_run_batches'


def upgrade() -> None:
    op.create_table(
        'run_batches',
        sa.Column('id', sa.String(length=8), nullable=False),
        sa.Column('user_id', sa.Integer(), nullable=False),
        sa.Column('created_at', sa.DateTime(), server_default=sa.text('(CURRENT_TIMESTAMP)'), nullable=False),
        sa.Column('files_submitted', sa.Integer(), nullable=False),
        sa.ForeignKeyConstraint(['user_id'], ['users.id']),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index('ix_run_batches_user_id', 'run_batches', ['user_id'])
    with op.batch_alter_table('runs') as batch_op:
        batch_op.add_column(sa.Column('batch_id', sa.String(length=8), nullable=True))
        batch_op.create_index('ix_runs_batch_id', ['batch_id'])
        batch_op.create_foreign_key(BATCH_FK_NAME, 'run_batches', ['batch_id'], ['id'])


def downgrade() -> None:
    with op.batch_alter_table('runs') as batch_op:
        batch_op.drop_constraint(BATCH_FK_NAME, type_='foreignkey')
        batch_op.drop_index('ix_runs_batch_id')
        batch_op.drop_column('batch_id')
    # No drop_index('ix_run_batches_user_id') first: MariaDB refuses it
    # (1553, "needed in a foreign key constraint" -- the user_id FK uses it),
    # and DDL is not transactional there, so the runs side above would stay
    # downgraded. DROP TABLE drops the index with the table.
    op.drop_table('run_batches')
