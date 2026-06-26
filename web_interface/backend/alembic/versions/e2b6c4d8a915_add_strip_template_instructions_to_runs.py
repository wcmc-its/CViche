"""add strip_template_instructions to runs

Records whether the WCM template instruction scaffolding should be stripped
from the Stage 6 output for a run (1 = strip, the existing unconditional
behavior; 0 = keep the instruction text). NOT NULL with a server default of 1
so existing rows backfill to the current behavior on MySQL when the column is
added.

Revision ID: e2b6c4d8a915
Revises: a7c3e1f90b24
Create Date: 2026-06-26
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'e2b6c4d8a915'
down_revision: Union[str, Sequence[str], None] = 'a7c3e1f90b24'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column('runs',
        sa.Column('strip_template_instructions', sa.Integer(), nullable=False, server_default='1'))


def downgrade() -> None:
    op.drop_column('runs', 'strip_template_instructions')
