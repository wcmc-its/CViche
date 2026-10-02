"""add notify_on_complete to run_batches (#1335)

Revision ID: d9f3b7e1a2c5
Revises: c4e8a2f6d9b1
Create Date: 2026-10-02
"""
from alembic import op
import sqlalchemy as sa

revision = 'd9f3b7e1a2c5'
down_revision = 'c4e8a2f6d9b1'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column('run_batches', sa.Column('notify_on_complete', sa.Boolean(), server_default=sa.false(), nullable=False))


def downgrade() -> None:
    op.drop_column('run_batches', 'notify_on_complete')
