"""add scanned_pages to runs

A PDF's image-only (scanned) pages, 1-based and comma-joined, recorded at
upload so the run page can warn that their text is missing from the output
(#1282). Nullable with no default: NULL for a docx, a PDF with no scanned
page, and every run that predates this migration.

Revision ID: c4e8a2f6d913
Revises: a7c2e5f9b134
Create Date: 2026-10-02
"""
from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'c4e8a2f6d913'
down_revision: str | Sequence[str] | None = 'a7c2e5f9b134'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column('runs', sa.Column('scanned_pages', sa.Text(), nullable=True))


def downgrade() -> None:
    op.drop_column('runs', 'scanned_pages')
