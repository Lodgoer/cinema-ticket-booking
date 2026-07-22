"""add materialized view for occupancy rate

Revision ID: a1b2c3d4e5f6
Revises: 0cc17b8400ad
Create Date: 2026-07-21 20:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'a1b2c3d4e5f6'
down_revision: Union[str, Sequence[str], None] = '0cc17b8400ad'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Create the materialized view for occupancy rate."""
    op.execute("""
        CREATE MATERIALIZED VIEW IF NOT EXISTS mv_occupancy_rate AS
        SELECT
            st.id AS showtime_id,
            m.title AS movie_title,
            st.starts_at,
            c.id AS cinema_id,
            c.name AS cinema_name,
            h.id AS hall_id,
            h.name AS hall_name,
            h.capacity AS total_seats,
            COUNT(ss.seat_id) FILTER (WHERE ss.status = 'booked') AS booked_seats,
            ROUND(
                COUNT(ss.seat_id) FILTER (WHERE ss.status = 'booked')::numeric /
                NULLIF(h.capacity, 0) * 100,
                1
            ) AS occupancy_percent
        FROM showtime st
        JOIN hall h ON h.id = st.hall_id
        JOIN cinema c ON c.id = h.cinema_id
        JOIN movie m ON m.id = st.movie_id
        LEFT JOIN showtime_seat ss ON ss.showtime_id = st.id
        GROUP BY st.id, m.title, st.starts_at, c.id, c.name, h.id, h.name, h.capacity
        WITH NO DATA
    """)

    # Unique index required for CONCURRENTLY refresh
    op.execute("""
        CREATE UNIQUE INDEX IF NOT EXISTS idx_mv_occupancy_rate_showtime
        ON mv_occupancy_rate (showtime_id)
    """)

    # Index for filtering by cinema
    op.execute("""
        CREATE INDEX IF NOT EXISTS idx_mv_occupancy_rate_cinema
        ON mv_occupancy_rate (cinema_id)
    """)


def downgrade() -> None:
    """Drop the materialized view."""
    op.execute("DROP MATERIALIZED VIEW IF EXISTS mv_occupancy_rate")
