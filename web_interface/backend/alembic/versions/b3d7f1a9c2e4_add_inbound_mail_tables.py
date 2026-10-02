"""add inbound_messages and inbound_files (#1298)

Email CV intake: one row per raw message SES wrote to S3 (unique on the key,
so a re-poll never reads it twice) and one row per CV held in a user's inbox
until they submit it.

Revision ID: b3d7f1a9c2e4
Revises: c4e8a2f6d913
Create Date: 2026-10-02
"""
from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'b3d7f1a9c2e4'
down_revision: str | Sequence[str] | None = 'c4e8a2f6d913'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        'inbound_messages',
        sa.Column('id', sa.Integer(), autoincrement=True, nullable=False),
        sa.Column('s3_key', sa.String(length=512), nullable=False),
        sa.Column('message_id', sa.String(length=255), nullable=True),
        sa.Column('from_addr', sa.String(length=255), nullable=True),
        sa.Column('user_id', sa.Integer(), nullable=True),
        sa.Column('received_at', sa.DateTime(), server_default=sa.text('(CURRENT_TIMESTAMP)'), nullable=False),
        sa.Column('status', sa.String(length=20), nullable=False),
        sa.Column('reject_reason', sa.String(length=120), nullable=True),
        sa.Column('file_count', sa.Integer(), nullable=False),
        sa.ForeignKeyConstraint(['user_id'], ['users.id']),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('s3_key'),
    )
    op.create_index(op.f('ix_inbound_messages_user_id'), 'inbound_messages', ['user_id'], unique=False)
    op.create_table(
        'inbound_files',
        sa.Column('id', sa.Integer(), autoincrement=True, nullable=False),
        sa.Column('inbound_message_id', sa.Integer(), nullable=False),
        sa.Column('user_id', sa.Integer(), nullable=False),
        sa.Column('filename', sa.String(length=255), nullable=False),
        sa.Column('size_bytes', sa.Integer(), nullable=False),
        sa.Column('sha256', sa.String(length=64), nullable=False),
        sa.Column('storage_key', sa.String(length=255), nullable=False),
        sa.Column('status', sa.String(length=20), nullable=False),
        sa.Column('run_id', sa.String(length=10), nullable=True),
        sa.Column('created_at', sa.DateTime(), server_default=sa.text('(CURRENT_TIMESTAMP)'), nullable=False),
        sa.ForeignKeyConstraint(['inbound_message_id'], ['inbound_messages.id']),
        sa.ForeignKeyConstraint(['user_id'], ['users.id']),
        sa.ForeignKeyConstraint(['run_id'], ['runs.id']),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index(op.f('ix_inbound_files_inbound_message_id'), 'inbound_files', ['inbound_message_id'], unique=False)
    op.create_index(op.f('ix_inbound_files_user_id'), 'inbound_files', ['user_id'], unique=False)


def downgrade() -> None:
    op.drop_index(op.f('ix_inbound_files_user_id'), table_name='inbound_files')
    op.drop_index(op.f('ix_inbound_files_inbound_message_id'), table_name='inbound_files')
    op.drop_table('inbound_files')
    op.drop_index(op.f('ix_inbound_messages_user_id'), table_name='inbound_messages')
    op.drop_table('inbound_messages')
