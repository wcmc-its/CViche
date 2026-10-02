"""add source and completion_notified_at to run_batches (#1298)

Revision ID: c4e8a2f6d9b1
Revises: b3d7f1a9c2e4
Create Date: 2026-10-02
"""
from alembic import op
import sqlalchemy as sa

revision = 'c4e8a2f6d9b1'
down_revision = 'b3d7f1a9c2e4'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column('run_batches', sa.Column('source', sa.String(length=10), server_default='web', nullable=False))
    op.add_column('run_batches', sa.Column('completion_notified_at', sa.DateTime(), nullable=True))


def downgrade() -> None:
    op.drop_column('run_batches', 'completion_notified_at')
    op.drop_column('run_batches', 'source')
