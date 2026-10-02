"""add image_tag to runs

Records which container image executed a run (#1239). Stamped when a run moves
to "running" (the worker's claim, a resume, a retry) from the CVICHE_IMAGE_TAG
env var the Dockerfile bakes in. Nullable with no default: NULL for every run
that predates this migration and for images built without a tag.

Revision ID: d6a1c9e4b708
Revises: c3d8e1f5a297
Create Date: 2026-10-02
"""
from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'd6a1c9e4b708'
down_revision: str | Sequence[str] | None = 'c3d8e1f5a297'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column('runs', sa.Column('image_tag', sa.String(length=128), nullable=True))


def downgrade() -> None:
    op.drop_column('runs', 'image_tag')
