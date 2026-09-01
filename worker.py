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
from app.services.booking_service import (
    sweep_expired_bookings as sweep_expired_bookings_service,
)
from app.services.waiting_room import (
    waiting_room_key,
    admit_batch,
    BATCH_SIZE,
    ADMISSION_INTERVAL,
)

# arq worker settings
from arq import cron

def every_n_seconds(n: int) -> dict:
    """
    Build cron() kwargs that approximate "run every n seconds".

    arq's cron() is clock-based (like a crontab line), not interval-based —
    it wants specific second/minute marks, not a gap between runs. This
    translates a plain "every N seconds" into the set of clock marks that
    produces that cadence.
    """
    if n < 60:
        if 60 % n != 0:
            raise ValueError(f"{n} must evenly divide 60")
        return {"second": set(range(0, 60, n))}
    elif n % 60 == 0:
        minutes = n // 60
        if 60 % minutes != 0:
            raise ValueError(f"{minutes} must evenly divide 60")
        return {"minute": set(range(0, 60, minutes)), "second": 0}
    else:
        raise ValueError(f"{n} seconds isn't expressible as a simple cron interval")

async def sweep_expired_bookings(ctx) -> int:
    """Cancel bookings past their expires_at, releasing seats.

    Thin arq wrapper: opens a session and delegates the actual logic to
    booking_service.sweep_expired_bookings, so this stays the single
    source of truth instead of two copies drifting apart.
    """
    async with async_session() as session:
        return await sweep_expired_bookings_service(session)

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

class WorkerSettings:
    functions = [sweep_expired_bookings, sweep_waiting_room, refresh_occupancy_view]
    cron_jobs = [
        cron(sweep_expired_bookings),  # runs every minute (default: second=0)
        cron(sweep_waiting_room, **every_n_seconds(ADMISSION_INTERVAL)),
        cron(refresh_occupancy_view, **every_n_seconds(300)),
    ]
    on_startup = startup
    max_jobs = 4
