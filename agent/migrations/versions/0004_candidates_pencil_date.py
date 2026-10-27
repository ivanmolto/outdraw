"""candidates.pencil_date: the night a pencil hold is for (the shortlist's holds calendar, T44).

Revision ID: 0004
Revises: 0003
Create Date: 2026-10-27
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0004"
down_revision: str | Sequence[str] | None = "0003"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("candidates", sa.Column("pencil_date", sa.Date()))


def downgrade() -> None:
    op.drop_column("candidates", "pencil_date")
