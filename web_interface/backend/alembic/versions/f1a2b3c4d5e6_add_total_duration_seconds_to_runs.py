"""add total_duration_seconds to runs

Persists the authoritative pipeline execution time (whole seconds) per run, so
historical conversion-time metrics can be queried directly instead of recomputed
from started_at/completed_at every time. The orchestrator already computes this
value at completion; this column stores it. NULL for runs created before the
column exists.

Revision ID: f1a2b3c4d5e6
Revises: d7a4f9b2e103
Create Date: 2026-06-04
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'f1a2b3c4d5e6'
down_revision: Union[str, Sequence[str], None] = 'd7a4f9b2e103'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column('runs',
        sa.Column('total_duration_seconds', sa.Integer(), nullable=True))


def downgrade() -> None:
    op.drop_column('runs', 'total_duration_seconds')
