"""widen users.cwid to hold a partner user's scoped ePPN

A user from a partner IdP is anchored on their full scoped ePPN
("user@cornell.edu") rather than a bare CWID, which can exceed 20 chars.
See issue #1452.

Revision ID: c4e8a2f61b37
Revises: d9f3b7e1a2c5
Create Date: 2026-10-05
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'c4e8a2f61b37'
down_revision: Union[str, Sequence[str], None] = 'd9f3b7e1a2c5'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    with op.batch_alter_table('users') as batch_op:
        batch_op.alter_column(
            'cwid',
            existing_type=sa.String(length=20),
            type_=sa.String(length=255),
            existing_nullable=True,
        )


def downgrade() -> None:
    # Fails if a scoped ePPN longer than 20 chars has been stored -- delete
    # or shorten those rows first.
    with op.batch_alter_table('users') as batch_op:
        batch_op.alter_column(
            'cwid',
            existing_type=sa.String(length=255),
            type_=sa.String(length=20),
            existing_nullable=True,
        )
