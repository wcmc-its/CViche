"""add runs.cv_owner_name and users.department

Two nullable columns for the admin "all runs" view:

- runs.cv_owner_name: the CV owner's name as inferred by stage 4, persisted
  when stage 4 completes so the admin runs list can filter and group by
  faculty member without opening each run's *_fields.json. Indexed because the
  filter-options endpoint GROUP BYs it. NULL until stage 4 completes, when no
  owner was inferred, or for runs that predate this column (see
  scripts/backfill_cv_owner_name.py).

- users.department: the signed-in user's department from the Enterprise
  Directory, refreshed at SAML login. NULL for simple-auth users and when ED
  carries no department.

Revision ID: a7c3e91d5b24
Revises: 622ffee3ec76
Create Date: 2026-09-30
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'a7c3e91d5b24'
down_revision: Union[str, Sequence[str], None] = '622ffee3ec76'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column('runs', sa.Column('cv_owner_name', sa.String(length=255), nullable=True))
    op.create_index('ix_runs_cv_owner_name', 'runs', ['cv_owner_name'])
    op.add_column('users', sa.Column('department', sa.String(length=255), nullable=True))


def downgrade() -> None:
    op.drop_column('users', 'department')
    op.drop_index('ix_runs_cv_owner_name', table_name='runs')
    op.drop_column('runs', 'cv_owner_name')
