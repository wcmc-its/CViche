"""add queued_at and resume_from_step to runs

Two nullable columns for the Valkey run queue (#701 rework), landing together
since both are always read/written by the same flip (run_service's
flip_to_queued / claim_queued):

- queued_at: when a run last entered "queued" via flip_to_queued. The
  worker's claim (queued -> running) re-stamps started_at, so started_at now
  means "began executing" only; the admin queue view's queued-age needs its
  own column instead of reading started_at off a run that has not started
  running yet. NULL means "never queued" (in-process dispatch, or a run
  created before this column existed).

- resume_from_step: the step number a resumed run should start from, set by
  retry_step's flip and NULLed by start_run's flip. Replaces carrying
  start_step on the Valkey work token itself -- a redelivered or stale token
  could otherwise resume an old, already-superseded step (mrj4001 review,
  runs.py point 3). The DB row is now the only source of truth for where to
  resume; the token is a pure wake-up. NULL means "start from the top".

Revision ID: 622ffee3ec76
Revises: d4f8a1c3b6e2
Create Date: 2026-09-23
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '622ffee3ec76'
down_revision: Union[str, Sequence[str], None] = 'd4f8a1c3b6e2'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column('runs', sa.Column('queued_at', sa.DateTime(), nullable=True))
    op.add_column('runs', sa.Column('resume_from_step', sa.Integer(), nullable=True))


def downgrade() -> None:
    op.drop_column('runs', 'resume_from_step')
    op.drop_column('runs', 'queued_at')
