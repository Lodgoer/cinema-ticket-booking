"""
Background worker using arq — handles periodic tasks that don't belong
in the request/response cycle.

Tasks:
- sweep_expired_bookings: finds bookings past their expires_at and
  cancels them, releasing seats back to available. Runs every 60 seconds.

Design note (for interview):
The sweep approach was chosen over "lazy check" (checking expiry at read time)
for simplicity in a one-week project. Lazy check is documented as a future
improvement — it would avoid the 1-minute window where a seat appears
unavailable even though its hold expired. The trade-off is acceptable at
this scale; in production, lazy check or event-driven expiry would be preferred.
"""
import asyncio
from datetime import datetime, timezone

from sqlalchemy import select, update, text
from sqlalchemy.ext.asyncio import AsyncSession


from app.database import async_session
from app.models import Booking, BookingSeat, ShowtimeSeat
from app.redis_client import redis_client
from app.services.waiting_room import (
    waiting_room_key,
    admit_batch,
    BATCH_SIZE,
    ADMISSION_INTERVAL,
)

async def sweep_expired_bookings(ctx) -> int:
    """Cancel bookings past their expires_at, releasing seats.

    Returns the number of bookings cancelled.
    """
    async with async_session() as session:
        now = datetime.now(timezone.utc)

        # Find expired pending bookings
        result = await session.execute(
            select(Booking).where(
                Booking.status == "pending",
                Booking.expires_at < now,
            )
        )
        expired = list(result.scalars().all())

        if not expired:
            return 0

        for booking in expired:
            # Deactivate booking seats
            await session.execute(
                update(BookingSeat)
                .where(
                    BookingSeat.booking_id == booking.id,
                    BookingSeat.status == "active",
                )
                .values(status="cancelled")
            )

            # Release showtime seats back to available
            await session.execute(
                update(ShowtimeSeat)
                .where(
                    ShowtimeSeat.id.in_(
                        select(BookingSeat.showtime_seat_id).where(
                            BookingSeat.booking_id == booking.id,
                            BookingSeat.status == "cancelled",
                        )
                    )
                )
                .values(status="available")
            )

            booking.status = "expired"

        await session.commit()
        return len(expired)


async def sweep_waiting_room(ctx) -> int:
    """Admit the next batch of users from all active waiting rooms.

    Scans Redis for waiting_room:* keys, and for each showtime that has
    users in the queue, admits BATCH_SIZE users.

    Returns the total number of users admitted across all showtimes.
    """
    total_admitted = 0
    r = redis_client

    # Find all waiting room keys
    cursor = 0
    while True:
        cursor, keys = await r.scan(cursor=cursor, match="waiting_room:*", count=100)
        for key in keys:
            showtime_id = int(key.split(":")[-1])
            admitted = await admit_batch(r, showtime_id, BATCH_SIZE)
            total_admitted += len(admitted)
        if cursor == 0:
            break

    return total_admitted


async def refresh_occupancy_view(ctx) -> None:
    """Refresh the materialized view for occupancy rate.

    Uses CONCURRENTLY to avoid locking the view during reads.
    This runs periodically to keep the occupancy data fresh.
    """
    async with async_session() as session:
        await session.execute(text("REFRESH MATERIALIZED VIEW CONCURRENTLY mv_occupancy_rate"))
        await session.commit()


async def startup(ctx):
    """Runs once when the worker starts."""
    ctx["started_at"] = datetime.now(timezone.utc).isoformat()


# arq worker settings
from arq import cron

class WorkerSettings:
    functions = [sweep_expired_bookings, sweep_waiting_room, refresh_occupancy_view]
    cron_jobs = [
        cron(sweep_expired_bookings),  # runs every minute (default)
        cron(sweep_waiting_room, interval=ADMISSION_INTERVAL),  # admit batches every N seconds
        cron(refresh_occupancy_view, interval=300),  # refresh materialized view every 5 minutes
    ]
    on_startup = startup
    max_jobs = 4
