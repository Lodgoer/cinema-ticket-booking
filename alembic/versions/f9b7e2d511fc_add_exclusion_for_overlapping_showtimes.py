"""add exclusion for overlapping showtimes

Revision ID: f9b7e2d511fc
Revises: b3edeb3dad3d
Create Date: 2026-07-19 11:04:28.598751

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'f9b7e2d511fc'
down_revision: Union[str, Sequence[str], None] = 'b3edeb3dad3d'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    # btree_gist lets a GiST exclusion index compare a plain equality column
    # (hall_id) alongside a range type (tstzrange) in the same constraint.
    op.execute("CREATE EXTENSION IF NOT EXISTS btree_gist")

    op.execute(
        """
        ALTER TABLE showtime
            ADD CONSTRAINT no_overlapping_showtimes
            EXCLUDE USING gist (
                hall_id WITH =,
                tstzrange(starts_at, ends_at) WITH &&
            )
        """
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.execute("ALTER TABLE showtime DROP CONSTRAINT no_overlapping_showtimes")
    op.execute("DROP EXTENSION IF EXISTS btree_gist")
