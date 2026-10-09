"""add feedback_verdicts

A reviewer's verdict on each group of doctor findings the run's Fix list
showed (#1587): Fixed, Not a problem or Can't tell, per lint and message
shape, with the group's finding count. A child table of ``feedback``, so the
feedback columns stay as they were. No CV text.

Revision ID: b5d1f7a3c920
Revises: a7c2e9d4f150
Create Date: 2026-10-09
"""
from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = 'b5d1f7a3c920'
down_revision: str | Sequence[str] | None = 'a7c2e9d4f150'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        'feedback_verdicts',
        sa.Column('id', sa.Integer(), autoincrement=True, nullable=False),
        sa.Column('feedback_id', sa.Integer(), nullable=False),
        sa.Column('run_id', sa.String(length=10), nullable=False),
        sa.Column('lint', sa.String(length=64), nullable=False),
        sa.Column('shape', sa.String(length=64), nullable=True),
        sa.Column('finding_count', sa.Integer(), nullable=False),
        sa.Column('verdict', sa.String(length=20), nullable=False),
        sa.Column('created_at', sa.DateTime(), server_default=sa.text('(CURRENT_TIMESTAMP)'), nullable=True),
        sa.ForeignKeyConstraint(['feedback_id'], ['feedback.id']),
        sa.ForeignKeyConstraint(['run_id'], ['runs.id']),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index(op.f('ix_feedback_verdicts_feedback_id'), 'feedback_verdicts', ['feedback_id'])
    op.create_index(op.f('ix_feedback_verdicts_run_id'), 'feedback_verdicts', ['run_id'])


def downgrade() -> None:
    # DROP TABLE takes its indexes with it; MariaDB refuses DROP INDEX on an
    # index a foreign key needs (see c3d8e1f5a297's downgrade test).
    op.drop_table('feedback_verdicts')
