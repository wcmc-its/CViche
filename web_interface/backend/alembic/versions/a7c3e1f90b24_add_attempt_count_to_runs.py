"""add attempt_count to runs

Tracks the number of pipeline execution attempts per run (1 = initial run),
incremented by the auto-retry feature (issue #145). NOT NULL with a server
default of 1 so existing rows backfill to 1 on MySQL when the column is added.

Revision ID: a7c3e1f90b24
Revises: 5a74eb0f9645
Create Date: 2026-06-12
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'a7c3e1f90b24'
down_revision: Union[str, Sequence[str], None] = '5a74eb0f9645'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column('runs',
        sa.Column('attempt_count', sa.Integer(), nullable=False, server_default='1'))


def downgrade() -> None:
    op.drop_column('runs', 'attempt_count')
