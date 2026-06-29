"""merge estimated_duration + strip_template heads

#197 (b8d1c4e207af, estimated_duration_seconds) and #200 (e2b6c4d8a915,
strip_template_instructions) were both written off a7c3e1f90b24 and merged to
dev independently, leaving the Alembic tree with two heads. `alembic upgrade
head` then aborts with "Multiple head revisions are present", which crashed the
db-migration init container and silently blocked every dev rollout after
2026-06-26 (pods fell back to the last-healthy image). This is a no-op merge
that reunites the two heads; it changes no schema.

Revision ID: 1928beb2174e
Revises: b8d1c4e207af, e2b6c4d8a915
Create Date: 2026-06-29
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '1928beb2174e'
down_revision: Union[str, Sequence[str], None] = ('b8d1c4e207af', 'e2b6c4d8a915')
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    pass


def downgrade() -> None:
    pass
