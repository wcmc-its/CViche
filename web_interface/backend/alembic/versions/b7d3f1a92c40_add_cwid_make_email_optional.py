"""add cwid anchor to users; make email optional

Anchors SSO identity on CWID (unique, stable, present for every WCM identity)
so users without an ED `mail` attribute (e.g. external affiliates) can still
authenticate. email becomes preferred-but-optional. See issue #326.

Revision ID: b7d3f1a92c40
Revises: c9f2e8a4d301
Create Date: 2026-07-22
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'b7d3f1a92c40'
down_revision: Union[str, Sequence[str], None] = 'c9f2e8a4d301'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # batch_alter_table so the ALTER COLUMN (email nullable) works on SQLite too
    # (SQLite can't ALTER COLUMN in place; batch recreates the table). On MySQL
    # it emits plain ALTERs.
    with op.batch_alter_table('users') as batch_op:
        batch_op.add_column(sa.Column('cwid', sa.String(length=20), nullable=True))
        batch_op.alter_column(
            'email',
            existing_type=sa.String(length=255),
            nullable=True,
            existing_nullable=False,
        )
    op.create_index('ix_users_cwid', 'users', ['cwid'], unique=True)


def downgrade() -> None:
    op.drop_index('ix_users_cwid', table_name='users')
    with op.batch_alter_table('users') as batch_op:
        batch_op.alter_column(
            'email',
            existing_type=sa.String(length=255),
            nullable=False,
            existing_nullable=True,
        )
        batch_op.drop_column('cwid')
