"""add source_sha256 to runs

The sha256 of the uploaded file, so the upload endpoint can ask for
confirmation before re-processing a file any user already ran (#1286).
Nullable and indexed: NULL for runs that predate this migration until
scripts/backfill_input_format.py fills it from the archived input.

Revision ID: a7c2e5f9b134
Revises: e8b3a7d1c452
Create Date: 2026-10-02
"""
from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'a7c2e5f9b134'
down_revision: str | Sequence[str] | None = 'e8b3a7d1c452'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column('runs', sa.Column('source_sha256', sa.String(length=64), nullable=True))
    op.create_index(op.f('ix_runs_source_sha256'), 'runs', ['source_sha256'], unique=False)


def downgrade() -> None:
    op.drop_index(op.f('ix_runs_source_sha256'), table_name='runs')
    op.drop_column('runs', 'source_sha256')
