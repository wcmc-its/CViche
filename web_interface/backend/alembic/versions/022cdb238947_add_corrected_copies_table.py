"""add corrected_copies

One row per reviewer's corrected copy of a run (#1754 stores one per run and
uploader under corrected/). The runs list shows and filters on them, and
listing storage per run on every runs-list request is too slow, so the upload
records a row here. user_id is NULL for a copy stored before #1754, whose
uploader is unknown. Rows for copies uploaded before this migration come from
scripts/backfill_corrected_copies.py.

Revision ID: 022cdb238947
Revises: a7c2e9d4f150
Create Date: 2026-10-13
"""
from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = '022cdb238947'
down_revision: str | Sequence[str] | None = 'a7c2e9d4f150'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        'corrected_copies',
        sa.Column('id', sa.Integer(), autoincrement=True, nullable=False),
        sa.Column('run_id', sa.String(length=10), nullable=False),
        sa.Column('user_id', sa.Integer(), nullable=True),
        sa.Column('uploaded_at', sa.DateTime(), nullable=False),
        sa.Column('changes', sa.Integer(), nullable=False),
        sa.Column('storage_key', sa.String(length=255), nullable=False),
        sa.ForeignKeyConstraint(['run_id'], ['runs.id']),
        sa.ForeignKeyConstraint(['user_id'], ['users.id']),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('run_id', 'user_id', name='uq_corrected_copies_run_user'),
        sa.UniqueConstraint('storage_key'),
    )
    op.create_index(op.f('ix_corrected_copies_run_id'), 'corrected_copies', ['run_id'], unique=False)
    op.create_index(op.f('ix_corrected_copies_user_id'), 'corrected_copies', ['user_id'], unique=False)


def downgrade() -> None:
    # DROP TABLE takes the indexes with it. Dropping them first would fail on
    # MariaDB, which refuses to drop an index a foreign key still uses (1553).
    op.drop_table('corrected_copies')
