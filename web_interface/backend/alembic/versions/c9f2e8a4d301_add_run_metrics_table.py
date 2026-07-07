"""add run_metrics table

Revision ID: c9f2e8a4d301
Revises: 1928beb2174e
Create Date: 2026-07-07 18:20:00.000000

run_metrics was the one model table with no migration: it only ever existed
because init_db()'s metadata.create_all() ran at pod boot. #191 turns that
boot-time DDL off (CVICHE_INIT_DB=0), so Alembic must own this table too.
Long-lived environments already have it from init_db — the guard makes this
migration a no-op there; fresh environments get the DDL here.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'c9f2e8a4d301'
down_revision: Union[str, Sequence[str], None] = '1928beb2174e'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    inspector = sa.inspect(op.get_bind())
    if 'run_metrics' in inspector.get_table_names():
        return
    op.create_table(
        'run_metrics',
        sa.Column('id', sa.Integer(), autoincrement=True, nullable=False),
        sa.Column('run_id', sa.String(length=10), nullable=False),
        sa.Column('word_count', sa.Integer(), nullable=True),
        sa.Column('char_count', sa.Integer(), nullable=True),
        sa.Column('publication_count', sa.Integer(), nullable=True),
        sa.Column('sections_populated', sa.Integer(), nullable=True),
        sa.Column('sections_total', sa.Integer(), nullable=True),
        sa.Column('language', sa.String(length=10), nullable=True),
        sa.Column('computed_at', sa.DateTime(), server_default=sa.text('(CURRENT_TIMESTAMP)'), nullable=True),
        sa.ForeignKeyConstraint(['run_id'], ['runs.id'], ),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index(op.f('ix_run_metrics_run_id'), 'run_metrics', ['run_id'], unique=True)


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_index(op.f('ix_run_metrics_run_id'), table_name='run_metrics')
    op.drop_table('run_metrics')
