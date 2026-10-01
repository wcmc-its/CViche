"""add runs.quality_score, runs.quality_band, runs.quality_cap

Denormalised copy of the advisory quality score (runs/{id}/quality_score.json)
so the admin runs list can show and sort a Score column without reading one
cached file per row:

- runs.quality_score: the final 0-100 score (after any hard-fail cap).
- runs.quality_band: GREEN / YELLOW / RED.
- runs.quality_cap: the hard-fail cap value when one lowered the score, else
  NULL.

All NULL until the run's score is computed, and for runs that predate the
columns (see scripts/backfill_quality_score.py). quality_score is indexed for
the Score sort.

Revision ID: b5e8d2f6a143
Revises: a7c3e91d5b24
Create Date: 2026-09-30
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'b5e8d2f6a143'
down_revision: Union[str, Sequence[str], None] = 'a7c3e91d5b24'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column('runs', sa.Column('quality_score', sa.Integer(), nullable=True))
    op.add_column('runs', sa.Column('quality_band', sa.String(length=20), nullable=True))
    op.add_column('runs', sa.Column('quality_cap', sa.Integer(), nullable=True))
    op.create_index('ix_runs_quality_score', 'runs', ['quality_score'])


def downgrade() -> None:
    op.drop_index('ix_runs_quality_score', table_name='runs')
    op.drop_column('runs', 'quality_cap')
    op.drop_column('runs', 'quality_band')
    op.drop_column('runs', 'quality_score')
