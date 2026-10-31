"""add run_batches.submitted_notified_at

The Teams "batch submitted" card now goes out with a batch's first run, not
at POST /batches (#1202), and whichever upload sets this column first is the
one that posts it. Every batch stored before this migration already had its
card posted at creation (a one-file batch posts none), so they are backfilled
with their created_at: an upload into one of them must not post a second card.

Revision ID: 31d01b4bd243
Revises: 302005a597a4
Create Date: 2026-10-30
"""
from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = '31d01b4bd243'
down_revision: str | Sequence[str] | None = '302005a597a4'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column('run_batches', sa.Column('submitted_notified_at', sa.DateTime(), nullable=True))
    op.execute("UPDATE run_batches SET submitted_notified_at = created_at")


def downgrade() -> None:
    op.drop_column('run_batches', 'submitted_notified_at')
