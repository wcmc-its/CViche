"""add input_format and input_format_score to runs

Records whether the uploaded CV was written in the WCM faculty CV template
("wcm") or another format ("other"), so quality scores can be compared between
the two. input_format_score is the count of template signals behind the call.
Both nullable with no default: NULL for runs that predate this migration until
scripts/backfill_input_format.py fills them in.

Revision ID: e8b3a7d1c452
Revises: d6a1c9e4b708
Create Date: 2026-10-02
"""
from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'e8b3a7d1c452'
down_revision: str | Sequence[str] | None = 'd6a1c9e4b708'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column('runs', sa.Column('input_format', sa.String(length=10), nullable=True))
    op.add_column('runs', sa.Column('input_format_score', sa.Integer(), nullable=True))
    op.create_index(op.f('ix_runs_input_format'), 'runs', ['input_format'], unique=False)


def downgrade() -> None:
    op.drop_index(op.f('ix_runs_input_format'), table_name='runs')
    op.drop_column('runs', 'input_format_score')
    op.drop_column('runs', 'input_format')
