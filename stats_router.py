"""
Statistics and reporting endpoints — role-based access to cinema metrics.

Access rules:
- admin: sees statistics across ALL cinemas
- theater_manager: sees statistics ONLY for cinemas they manage (via cinema_manager table)
- customer: no access to statistics

All queries are aggregate SQL — no materialized views except for
occupancy_rate (which is in reports_router.py).
"""
from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select, func, case, and_, or_
from sqlalchemy.ext.asyncio import AsyncSession

from auth import require_role
from database import get_session
from models import (
    AppUser, CinemaManager, Cinema, Hall, Seat,
    Showtime, ShowtimeSeat, Booking, BookingSeat, Payment, Movie,
)

stats_router = APIRouter(
    prefix="/stats",
    tags=["statistics"],
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
        return None  # no filter

    result = await session.execute(
        select(CinemaManager.cinema_id).where(CinemaManager.user_id == user.id)
    )
    return [row[0] for row in result.all()]


def cinema_filter_clause(cinema_ids: list[int] | None):
    """Build a WHERE clause that filters by cinema IDs (or returns None for admins)."""
    if cinema_ids is None:
        return None
    return Hall.cinema_id.in_(cinema_ids)


@stats_router.get("/sales-by-movie")
async def sales_by_movie(
    user: AppUser = Depends(require_role("admin", "theater_manager")),
    session: AsyncSession = Depends(get_session),
):
    """Total tickets sold and revenue grouped by movie.

    Highest priority report metric.
    """
    cinema_ids = await get_managed_cinema_ids(session, user)

    query = (
        select(
            Movie.id.label("movie_id"),
            Movie.title.label("movie_title"),
            func.count(Ticket.id).label("tickets_sold"),
            func.coalesce(func.sum(Booking.total_price), 0).label("total_revenue"),
        )
        .join(Showtime, Showtime.movie_id == Movie.id)
        .join(Hall, Hall.id == Showtime.hall_id)
        .join(ShowtimeSeat, ShowtimeSeat.showtime_id == Showtime.id)
        .join(BookingSeat, BookingSeat.showtime_seat_id == ShowtimeSeat.id)
        .join(Booking, Booking.id == BookingSeat.booking_id)
        .join(Ticket, Ticket.booking_seat_id == BookingSeat.id)
        .where(Booking.status == "confirmed")
    )

    if cinema_ids is not None:
        query = query.where(Hall.cinema_id.in_(cinema_ids))

    query = query.group_by(Movie.id, Movie.title).order_by(func.count(Ticket.id).desc())

    result = await session.execute(query)
    rows = result.all()

    return {
        "filter": "all_cinemas" if cinema_ids is None else f"cinema_ids={cinema_ids}",
        "results": [
            {
                "movie_id": row.movie_id,
                "movie_title": row.movie_title,
                "tickets_sold": row.tickets_sold,
                "total_revenue": float(row.total_revenue),
            }
            for row in rows
        ],
    }


@stats_router.get("/sales-by-cinema")
async def sales_by_cinema(
    user: AppUser = Depends(require_role("admin", "theater_manager")),
    session: AsyncSession = Depends(get_session),
):
    """Total tickets sold and revenue grouped by cinema."""
    cinema_ids = await get_managed_cinema_ids(session, user)

    query = (
        select(
            Cinema.id.label("cinema_id"),
            Cinema.name.label("cinema_name"),
            func.count(Ticket.id).label("tickets_sold"),
            func.coalesce(func.sum(Booking.total_price), 0).label("total_revenue"),
        )
        .join(Hall, Hall.cinema_id == Cinema.id)
        .join(Showtime, Showtime.hall_id == Hall.id)
        .join(ShowtimeSeat, ShowtimeSeat.showtime_id == Showtime.id)
        .join(BookingSeat, BookingSeat.showtime_seat_id == ShowtimeSeat.id)
        .join(Booking, Booking.id == BookingSeat.booking_id)
        .join(Ticket, Ticket.booking_seat_id == BookingSeat.id)
        .where(Booking.status == "confirmed")
    )

    if cinema_ids is not None:
        query = query.where(Cinema.id.in_(cinema_ids))

    query = query.group_by(Cinema.id, Cinema.name).order_by(func.count(Ticket.id).desc())

    result = await session.execute(query)
    rows = result.all()

    return {
        "filter": "all_cinemas" if cinema_ids is None else f"cinema_ids={cinema_ids}",
        "results": [
            {
                "cinema_id": row.cinema_id,
                "cinema_name": row.cinema_name,
                "tickets_sold": row.tickets_sold,
                "total_revenue": float(row.total_revenue),
            }
            for row in rows
        ],
    }


@stats_router.get("/sales-by-showtime")
async def sales_by_showtime(
    user: AppUser = Depends(require_role("admin", "theater_manager")),
    session: AsyncSession = Depends(get_session),
):
    """Total tickets sold and revenue grouped by showtime."""
    cinema_ids = await get_managed_cinema_ids(session, user)

    query = (
        select(
            Showtime.id.label("showtime_id"),
            Movie.title.label("movie_title"),
            Showtime.starts_at.label("starts_at"),
            Cinema.name.label("cinema_name"),
            Hall.name.label("hall_name"),
            func.count(Ticket.id).label("tickets_sold"),
            func.coalesce(func.sum(Booking.total_price), 0).label("total_revenue"),
        )
        .join(Movie, Movie.id == Showtime.movie_id)
        .join(Hall, Hall.id == Showtime.hall_id)
        .join(Cinema, Cinema.id == Hall.cinema_id)
        .join(ShowtimeSeat, ShowtimeSeat.showtime_id == Showtime.id)
        .join(BookingSeat, BookingSeat.showtime_seat_id == ShowtimeSeat.id)
        .join(Booking, Booking.id == BookingSeat.booking_id)
        .join(Ticket, Ticket.booking_seat_id == BookingSeat.id)
        .where(Booking.status == "confirmed")
    )

    if cinema_ids is not None:
        query = query.where(Cinema.id.in_(cinema_ids))

    query = query.group_by(
        Showtime.id, Movie.title, Showtime.starts_at, Cinema.name, Hall.name
    ).order_by(Showtime.starts_at.desc())

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
                "hall_name": row.hall_name,
                "tickets_sold": row.tickets_sold,
                "total_revenue": float(row.total_revenue),
            }
            for row in rows
        ],
    }


@stats_router.get("/revenue-over-time")
async def revenue_over_time(
    user: AppUser = Depends(require_role("admin", "theater_manager")),
    session: AsyncSession = Depends(get_session),
):
    """Daily revenue for confirmed bookings."""
    cinema_ids = await get_managed_cinema_ids(session, user)

    query = (
        select(
            func.date(Booking.created_at).label("date"),
            func.count(Ticket.id).label("tickets_sold"),
            func.coalesce(func.sum(Booking.total_price), 0).label("daily_revenue"),
        )
        .join(BookingSeat, BookingSeat.booking_id == Booking.id)
        .join(Ticket, Ticket.booking_seat_id == BookingSeat.id)
        .join(ShowtimeSeat, ShowtimeSeat.id == BookingSeat.showtime_seat_id)
        .join(Showtime, Showtime.id == ShowtimeSeat.showtime_id)
        .join(Hall, Hall.id == Showtime.hall_id)
        .where(Booking.status == "confirmed")
    )

    if cinema_ids is not None:
        query = query.where(Hall.cinema_id.in_(cinema_ids))

    query = query.group_by(func.date(Booking.created_at)).order_by(func.date(Booking.created_at))

    result = await session.execute(query)
    rows = result.all()

    return {
        "filter": "all_cinemas" if cinema_ids is None else f"cinema_ids={cinema_ids}",
        "results": [
            {
                "date": str(row.date),
                "tickets_sold": row.tickets_sold,
                "daily_revenue": float(row.daily_revenue),
            }
            for row in rows
        ],
    }


@stats_router.get("/peak-hours")
async def peak_hours(
    user: AppUser = Depends(require_role("admin", "theater_manager")),
    session: AsyncSession = Depends(get_session),
):
    """Most popular showtime hours based on ticket count."""
    cinema_ids = await get_managed_cinema_ids(session, user)

    query = (
        select(
            func.extract("hour", Showtime.starts_at).label("hour"),
            func.count(Ticket.id).label("tickets_sold"),
        )
        .join(Showtime, Showtime.id == ShowtimeSeat.showtime_id)
        .join(Hall, Hall.id == Showtime.hall_id)
        .join(BookingSeat, BookingSeat.showtime_seat_id == ShowtimeSeat.id)
        .join(Ticket, Ticket.booking_seat_id == BookingSeat.id)
        .join(Booking, Booking.id == BookingSeat.booking_id)
        .where(Booking.status == "confirmed")
    )

    if cinema_ids is not None:
        query = query.where(Hall.cinema_id.in_(cinema_ids))

    query = query.group_by(func.extract("hour", Showtime.starts_at)).order_by(
        func.count(Ticket.id).desc()
    )

    result = await session.execute(query)
    rows = result.all()

    return {
        "filter": "all_cinemas" if cinema_ids is None else f"cinema_ids={cinema_ids}",
        "results": [
            {
                "hour": int(row.hour),
                "tickets_sold": row.tickets_sold,
            }
            for row in rows
        ],
    }
