"""add provider column to llm_usage

Revision ID: c5e9a3f01b72
Revises: 1b8fabfe276a
Create Date: 2026-03-30
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'c5e9a3f01b72'
down_revision: Union[str, Sequence[str], None] = '1b8fabfe276a'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column('llm_usage',
        sa.Column('provider', sa.String(50), server_default='openai', nullable=True))


def downgrade() -> None:
    op.drop_column('llm_usage', 'provider')
