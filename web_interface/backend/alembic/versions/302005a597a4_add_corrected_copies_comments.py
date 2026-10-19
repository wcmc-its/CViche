"""add corrected_copies.comments

How many of the reviewer's own Word comments a corrected copy holds (#1937).
`changes` counts text edits only, so a copy carrying comments and no edits
read "0 changes" and looked lost. Rows stored before this migration read 0:
their comments were never counted.

Revision ID: 302005a597a4
Revises: 022cdb238947
Create Date: 2026-10-19
"""
from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = '302005a597a4'
down_revision: str | Sequence[str] | None = '022cdb238947'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column('corrected_copies',
                  sa.Column('comments', sa.Integer(), nullable=False, server_default=sa.text('0')))


def downgrade() -> None:
    op.drop_column('corrected_copies', 'comments')
