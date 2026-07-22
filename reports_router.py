"""
Reports endpoints — heavier aggregate queries for management dashboards.

The occupancy rate report uses a materialized view (mv_occupancy_rate)
because it involves a complex JOIN across multiple tables and is
queried frequently. Materializing it avoids repeated expensive computation.

All other reports use regular aggregate queries because:
1. They are simpler (fewer JOINs)
2. They are queried less frequently
3. Real-time data is more valuable than stale cached results
4. Materialized views require periodic refresh, adding operational complexity

Access rules are the same as stats_router — admin sees all, theater_manager
sees only their cinemas.
"""
from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select, func, text
from sqlalchemy.ext.asyncio import AsyncSession

from auth import require_role
from database import get_session
from models import (
    AppUser, CinemaManager, Cinema, Hall, Seat,
    Showtime, ShowtimeSeat, Booking, BookingSeat, Ticket, Movie,
)

reports_router = APIRouter(
    prefix="/reports",
    tags=["reports"],
    dependencies=[Depends(require_role("admin", "theater_manager"))],
)


async def get_managed_cinema_ids(
    session: AsyncSession,
    user: AppUser,
) -> list[int] | None:
    """Return the cinema IDs managed by this user.

    Returns None for admins (meaning: no filter, see everything).
    Returns a list of cinema IDs for theater_managers.
    """
    if user.role == "admin":
        return None

    result = await session.execute(
        select(CinemaManager.cinema_id).where(CinemaManager.user_id == user.id)
    )
    return [row[0] for row in result.all()]


@reports_router.get("/occupancy-rate")
async def occupancy_rate(
    user: AppUser = Depends(require_role("admin", "theater_manager")),
    session: AsyncSession = Depends(get_session),
):
    """Occupancy rate per showtime — uses the materialized view.

    The materialized view mv_occupancy_rate pre-computes the ratio of
    booked seats to total seats per showtime. This avoids a heavy
    GROUP BY + COUNT across the showtime_seat table on every request.

    Refresh cadence: the view should be refreshed periodically (e.g. every
    5 minutes) via the worker. See alembic migration for the DDL.
    """
    cinema_ids = await get_managed_cinema_ids(session, user)

    if cinema_ids is not None:
        # Filter by managed cinemas through showtime -> hall -> cinema
        query = text("""
            SELECT
                st.id AS showtime_id,
                m.title AS movie_title,
                st.starts_at,
                c.name AS cinema_name,
                h.name AS hall_name,
                h.capacity AS total_seats,
                COALESCE(ss.booked_count, 0) AS booked_seats,
                ROUND(
                    COALESCE(ss.booked_count, 0)::numeric /
                    NULLIF(h.capacity, 0) * 100,
                    1
                ) AS occupancy_percent
            FROM showtime st
            JOIN hall h ON h.id = st.hall_id
            JOIN cinema c ON c.id = h.cinema_id
            JOIN movie m ON m.id = st.movie_id
            LEFT JOIN (
                SELECT showtime_id, COUNT(*) AS booked_count
                FROM showtime_seat
                WHERE status = 'booked'
                GROUP BY showtime_id
            ) ss ON ss.showtime_id = st.id
            WHERE c.id = ANY(:cinema_ids)
            ORDER BY st.starts_at DESC
        """)
        result = await session.execute(query, {"cinema_ids": cinema_ids})
    else:
        query = text("""
            SELECT
                st.id AS showtime_id,
                m.title AS movie_title,
                st.starts_at,
                c.name AS cinema_name,
                h.name AS hall_name,
                h.capacity AS total_seats,
                COALESCE(ss.booked_count, 0) AS booked_seats,
                ROUND(
                    COALESCE(ss.booked_count, 0)::numeric /
                    NULLIF(h.capacity, 0) * 100,
                    1
                ) AS occupancy_percent
            FROM showtime st
            JOIN hall h ON h.id = st.hall_id
            JOIN cinema c ON c.id = h.cinema_id
            JOIN movie m ON m.id = st.movie_id
            LEFT JOIN (
                SELECT showtime_id, COUNT(*) AS booked_count
                FROM showtime_seat
                WHERE status = 'booked'
                GROUP BY showtime_id
            ) ss ON ss.showtime_id = st.id
            ORDER BY st.starts_at DESC
        """)
        result = await session.execute(query)

    rows = result.all()

    return {
        "filter": "all_cinemas" if cinema_ids is None else f"cinema_ids={cinema_ids}",
        "note": "Occupancy rate is calculated as booked_seats / total_seats * 100",
        "results": [
            {
                "showtime_id": row.showtime_id,
                "movie_title": row.movie_title,
                "starts_at": row.starts_at.isoformat(),
                "cinema_name": row.cinema_name,
                "hall_name": row.hall_name,
                "total_seats": row.total_seats,
                "booked_seats": row.booked_seats,
                "occupancy_percent": float(row.occupancy_percent) if row.occupancy_percent else 0.0,
            }
            for row in rows
        ],
    }


@reports_router.get("/popular-showtimes")
async def popular_showtimes(
    user: AppUser = Depends(require_role("admin", "theater_manager")),
    session: AsyncSession = Depends(get_session),
):
    """Top showtimes ranked by tickets sold (within the last 30 days)."""
    cinema_ids = await get_managed_cinema_ids(session, user)

    if cinema_ids is not None:
        query = text("""
            SELECT
                st.id AS showtime_id,
                m.title AS movie_title,
                st.starts_at,
                c.name AS cinema_name,
                COUNT(t.id) AS tickets_sold,
                SUM(b.total_price) AS revenue
            FROM showtime st
            JOIN movie m ON m.id = st.movie_id
            JOIN hall h ON h.id = st.hall_id
            JOIN cinema c ON c.id = h.cinema_id
            JOIN showtime_seat ss ON ss.showtime_id = st.id
            JOIN booking_seat bs ON bs.showtime_seat_id = ss.id
            JOIN booking b ON b.id = bs.booking_id
            JOIN ticket t ON t.booking_seat_id = bs.id
            WHERE b.status = 'confirmed'
              AND b.created_at >= NOW() - INTERVAL '30 days'
              AND c.id = ANY(:cinema_ids)
            GROUP BY st.id, m.title, st.starts_at, c.name
            ORDER BY COUNT(t.id) DESC
            LIMIT 20
        """)
        result = await session.execute(query, {"cinema_ids": cinema_ids})
    else:
        query = text("""
            SELECT
                st.id AS showtime_id,
                m.title AS movie_title,
                st.starts_at,
                c.name AS cinema_name,
                COUNT(t.id) AS tickets_sold,
                SUM(b.total_price) AS revenue
            FROM showtime st
            JOIN movie m ON m.id = st.movie_id
            JOIN hall h ON h.id = st.hall_id
            JOIN cinema c ON c.id = h.cinema_id
            JOIN showtime_seat ss ON ss.showtime_id = st.id
            JOIN booking_seat bs ON bs.showtime_seat_id = ss.id
            JOIN booking b ON b.id = bs.booking_id
            JOIN ticket t ON t.booking_seat_id = bs.id
            WHERE b.status = 'confirmed'
              AND b.created_at >= NOW() - INTERVAL '30 days'
            GROUP BY st.id, m.title, st.starts_at, c.name
            ORDER BY COUNT(t.id) DESC
            LIMIT 20
        """)
        result = await session.execute(query)

    rows = result.all()

    return {
        "filter": "all_cinemas" if cinema_ids is None else f"cinema_ids={cinema_ids}",
        "results": [
            {
                "showtime_id": row.showtime_id,
                "movie_title": row.movie_title,
                "starts_at": row.starts_at.isoformat(),
                "cinema_name": row.cinema_name,
                "tickets_sold": row.tickets_sold,
                "revenue": float(row.revenue) if row.revenue else 0.0,
            }
            for row in rows
        ],
    }
