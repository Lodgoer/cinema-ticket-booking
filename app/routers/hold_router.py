"""
Seat-hold endpoints — the customer-facing counterpart to the admin CRUD.

Design recap (matches the schema.sql comments from Day 1):
- Redis owns the *temporary* 'held' state via a TTL key
  `seat_hold:{showtime_id}:{seat_id}` -> user_id, with `SET NX EX`.
- Postgres only ever sees 'available' / 'booked' on showtime_seat — never
  'held'. So the seat map endpoint below has to merge both sources to show
  the customer the full picture: booked (from Postgres) + held (from Redis)
  + everything else implicitly available.
"""
from fastapi import APIRouter, Depends, HTTPException, status
from redis.asyncio import Redis
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth import get_current_user
from app.database import get_session
from app.models import AppUser, ShowtimeSeat
from app.redis_client import get_redis
from app.services.waiting_room import is_admitted, waiting_room_key

hold_router = APIRouter(prefix="/showtimes/{showtime_id}", tags=["seat-hold"])

HOLD_TTL_SECONDS = 600  # 10 minutes


def hold_key(showtime_id: int, seat_id: int) -> str:
    return f"seat_hold:{showtime_id}:{seat_id}"


@hold_router.post("/seats/{seat_id}/hold", status_code=status.HTTP_200_OK)
async def hold_seat(
    showtime_id: int,
    seat_id: int,
    user: AppUser = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
    r: Redis = Depends(get_redis),
):
    # Waiting room check: if the waiting room is active for this showtime
    # (i.e. the queue ZSET has any members or admitted tokens exist), the user
    # must hold a valid admission token before they can hold a seat.
    queue_exists = await r.exists(waiting_room_key(showtime_id))
    if queue_exists:
        admitted = await is_admitted(r, showtime_id, user.id)
        if not admitted:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="You must be admitted through the waiting room before holding seats. "
                       "POST /waiting-room/{showtime_id}/join to enter the queue.",
            )

    # Postgres check first: a seat that's already 'booked' (paid for) should
    # never even reach the Redis hold step — no point holding a sold seat.
    result = await session.execute(
        select(ShowtimeSeat).where(
            ShowtimeSeat.showtime_id == showtime_id,
            ShowtimeSeat.seat_id == seat_id,
        )
    )
    showtime_seat = result.scalar_one_or_none()
    if showtime_seat is None:
        raise HTTPException(status_code=404, detail="Seat not found for this showtime")
    if showtime_seat.status == "booked":
        raise HTTPException(status_code=409, detail="Seat is already booked")

    # Redis check: the actual race-condition guard.
    acquired = await r.set(
        hold_key(showtime_id, seat_id),
        str(user.id),
        nx=True,
        ex=HOLD_TTL_SECONDS,
    )
    if not acquired:
        raise HTTPException(status_code=409, detail="Seat is currently held by someone else")

    return {"showtime_id": showtime_id, "seat_id": seat_id, "held_by": user.id, "ttl_seconds": HOLD_TTL_SECONDS}


@hold_router.delete("/seats/{seat_id}/hold", status_code=status.HTTP_204_NO_CONTENT)
async def release_seat_hold(
    showtime_id: int,
    seat_id: int,
    user: AppUser = Depends(get_current_user),
    r: Redis = Depends(get_redis),
):
    key = hold_key(showtime_id, seat_id)
    held_by = await r.get(key)

    if held_by is None:
        # Nothing to release — treat as success either way (idempotent delete).
        return

    if held_by != str(user.id) and user.role not in ("admin", "theater_manager"):
        raise HTTPException(status_code=403, detail="You don't hold this seat")

    await r.delete(key)


@hold_router.get("/seats", status_code=status.HTTP_200_OK)
async def get_seat_map(
    showtime_id: int,
    session: AsyncSession = Depends(get_session),
    r: Redis = Depends(get_redis),
):
    """Merges Postgres (available/booked) with Redis (held) into one view."""
    result = await session.execute(
        select(ShowtimeSeat).where(ShowtimeSeat.showtime_id == showtime_id)
    )
    showtime_seats = result.scalars().all()

    seat_map = []
    for ss in showtime_seats:
        effective_status = ss.status  # 'available' or 'booked' from Postgres
        if effective_status == "available":
            held_by = await r.get(hold_key(showtime_id, ss.seat_id))
            if held_by is not None:
                effective_status = "held"

        seat_map.append(
            {
                "seat_id": ss.seat_id,
                "status": effective_status,
                "price": str(ss.price_snapshot),
            }
        )

    return seat_map
