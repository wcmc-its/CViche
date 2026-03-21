"""add feedback table

Revision ID: b3f7e2a1c804
Revises: 9a2d1943d509
Create Date: 2026-03-20 16:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'b3f7e2a1c804'
down_revision: Union[str, None] = '9a2d1943d509'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        'feedback',
        sa.Column('id', sa.Integer(), autoincrement=True, nullable=False),
        sa.Column('run_id', sa.String(length=10), nullable=False),
        sa.Column('user_id', sa.Integer(), nullable=False),
        sa.Column('reviewer_role', sa.String(length=50), nullable=False),
        sa.Column('overall_accuracy', sa.Integer(), nullable=True),
        sa.Column('overall_completeness', sa.Integer(), nullable=True),
        sa.Column('overall_usefulness', sa.Integer(), nullable=False),
        sa.Column('manual_conversion_effort', sa.String(length=50), nullable=False),
        sa.Column('correction_effort', sa.String(length=50), nullable=False),
        sa.Column('enrichment_quality', sa.Integer(), nullable=True),
        sa.Column('summary_generated', sa.Integer(), nullable=True),
        sa.Column('summary_quality', sa.Integer(), nullable=True),
        sa.Column('issue_missing_content', sa.String(length=20), nullable=True),
        sa.Column('issue_split_merged', sa.String(length=20), nullable=True),
        sa.Column('issue_wrong_section', sa.String(length=20), nullable=True),
        sa.Column('issue_inaccurate', sa.String(length=20), nullable=True),
        sa.Column('issue_ai_enrichment', sa.String(length=20), nullable=True),
        sa.Column('issue_formatting', sa.String(length=20), nullable=True),
        sa.Column('issue_locations', sa.Text(), nullable=True),
        sa.Column('biggest_issue', sa.Text(), nullable=True),
        sa.Column('likelihood_to_recommend', sa.Integer(), nullable=False),
        sa.Column('submitted_at', sa.DateTime(), server_default=sa.text('(CURRENT_TIMESTAMP)'), nullable=True),
        sa.ForeignKeyConstraint(['run_id'], ['runs.id'], ),
        sa.ForeignKeyConstraint(['user_id'], ['users.id'], ),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('run_id', 'user_id', name='uq_feedback_run_user'),
    )
    op.create_index(op.f('ix_feedback_run_id'), 'feedback', ['run_id'], unique=False)
    op.create_index(op.f('ix_feedback_user_id'), 'feedback', ['user_id'], unique=False)


def downgrade() -> None:
    op.drop_index(op.f('ix_feedback_user_id'), table_name='feedback')
    op.drop_index(op.f('ix_feedback_run_id'), table_name='feedback')
    op.drop_table('feedback')
