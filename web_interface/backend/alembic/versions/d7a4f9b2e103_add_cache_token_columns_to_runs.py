"""add cache token columns to runs

Tracks the Bedrock prompt-caching split (input tokens served from / written
to cache) per run. These are subsets of input_tokens, not additions to it.

Revision ID: d7a4f9b2e103
Revises: c5e9a3f01b72
Create Date: 2026-05-19
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'd7a4f9b2e103'
down_revision: Union[str, Sequence[str], None] = 'c5e9a3f01b72'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column('runs',
        sa.Column('cache_read_tokens', sa.Integer(), server_default='0', nullable=True))
    op.add_column('runs',
        sa.Column('cache_write_tokens', sa.Integer(), server_default='0', nullable=True))


def downgrade() -> None:
    op.drop_column('runs', 'cache_write_tokens')
    op.drop_column('runs', 'cache_read_tokens')
