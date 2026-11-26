"""organizer paid-event terms acceptance

Revision ID: 0003
Revises: 0002
Create Date: 2026-11-26 11:00:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0003"
down_revision: str | Sequence[str] | None = "0002"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "organizer_profile", sa.Column("paid_terms_version", sa.String(length=32), nullable=True)
    )
    op.add_column(
        "organizer_profile",
        sa.Column("paid_terms_accepted_at", sa.DateTime(timezone=True), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("organizer_profile", "paid_terms_accepted_at")
    op.drop_column("organizer_profile", "paid_terms_version")
