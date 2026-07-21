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

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from database import async_session
from models import Booking, BookingSeat, ShowtimeSeat


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


async def startup(ctx):
    """Runs once when the worker starts."""
    ctx["started_at"] = datetime.now(timezone.utc).isoformat()


# arq worker settings
from arq import cron

class WorkerSettings:
    functions = [sweep_expired_bookings]
    cron_jobs = [
        cron(sweep_expired_bookings),  # runs every minute (default)
    ]
    on_startup = startup
    max_jobs = 4
