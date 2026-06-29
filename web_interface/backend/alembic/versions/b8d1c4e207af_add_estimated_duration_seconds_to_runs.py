"""add estimated_duration_seconds to runs

Persists an input-scaled wall-clock estimate (whole seconds) computed from the
document at upload time. The client stall watchdog uses it to scale its "taking
longer than expected" threshold to the actual CV instead of a fixed constant, so
a legitimately large CV no longer false-positives as "may be stuck". NULL for
runs created before the column exists (watchdog falls back to a default).

Revision ID: b8d1c4e207af
Revises: a7c3e1f90b24
Create Date: 2026-06-26
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'b8d1c4e207af'
down_revision: Union[str, Sequence[str], None] = 'a7c3e1f90b24'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column('runs',
        sa.Column('estimated_duration_seconds', sa.Integer(), nullable=True))


def downgrade() -> None:
    op.drop_column('runs', 'estimated_duration_seconds')
