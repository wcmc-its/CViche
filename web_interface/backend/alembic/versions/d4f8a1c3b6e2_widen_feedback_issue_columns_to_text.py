"""widen feedback issue_* columns from String(20) to Text

The 6 `issue_*` columns on `feedback` were declared String(20) since the
table's creation, but the frontend sends full free-text sentences for each
(FeedbackForm.tsx's issue checkboxes each open a text input). Under MySQL
strict mode, any submission longer than 20 characters raises 1406 ("Data too
long for column") and the whole feedback row is lost (500, uncaught in
submit_feedback). Prod's table was already widened to TEXT by hand to recover
from this; this migration brings a fresh DB build in line with prod. See
issue #606.

Revision ID: d4f8a1c3b6e2
Revises: b7d3f1a92c40
Create Date: 2026-09-16
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'd4f8a1c3b6e2'
down_revision: Union[str, Sequence[str], None] = 'b7d3f1a92c40'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_ISSUE_COLUMNS = (
    'issue_missing_content',
    'issue_split_merged',
    'issue_wrong_section',
    'issue_inaccurate',
    'issue_ai_enrichment',
    'issue_formatting',
)


def upgrade() -> None:
    # batch_alter_table so the ALTER COLUMN works on SQLite too (SQLite can't
    # ALTER COLUMN in place; batch recreates the table). On MySQL it emits
    # plain ALTERs.
    with op.batch_alter_table('feedback') as batch_op:
        for column_name in _ISSUE_COLUMNS:
            batch_op.alter_column(
                column_name,
                existing_type=sa.String(length=20),
                type_=sa.Text(),
                existing_nullable=True,
            )


def downgrade() -> None:
    with op.batch_alter_table('feedback') as batch_op:
        for column_name in _ISSUE_COLUMNS:
            batch_op.alter_column(
                column_name,
                existing_type=sa.Text(),
                type_=sa.String(length=20),
                existing_nullable=True,
            )
