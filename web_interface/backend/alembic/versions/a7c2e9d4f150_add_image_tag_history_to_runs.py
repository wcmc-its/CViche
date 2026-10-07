"""add image_tag_history to runs

Keeps every image a run executed on (#1239). ``runs.image_tag`` holds only the
latest executing image, so a run resumed or retried across a deploy showed the
resuming image alone. This JSON array (Text, like feedback.issue_locations)
gains the executing image's tag each time the run moves to "running" on an
image other than the last one recorded. Nullable with no default: NULL for
every run that predates this migration and for runs only ever claimed by an
image built without a tag.

Revision ID: a7c2e9d4f150
Revises: c4e8a2f61b37
Create Date: 2026-10-07
"""
from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = 'a7c2e9d4f150'
down_revision: str | Sequence[str] | None = 'c4e8a2f61b37'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column('runs', sa.Column('image_tag_history', sa.Text(), nullable=True))


def downgrade() -> None:
    op.drop_column('runs', 'image_tag_history')
