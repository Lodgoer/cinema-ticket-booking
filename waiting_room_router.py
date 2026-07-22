"""
Waiting room endpoints — customers join a virtual queue before
they can hold seats for high-demand showtimes.

Flow:
1. POST /waiting-room/{showtime_id}/join  → user joins the queue
2. GET  /waiting-room/{showtime_id}/status → position in queue
3. POST /waiting-room/{showtime_id}/admit  → admin triggers admission (or worker)
4. GET  /waiting-room/{showtime_id}/check  → checks if user is admitted

The waiting room is optional per-showtime. If no one has joined, seat holds
work as before (no token required). Once the waiting room is active for a
showtime, users must be admitted before they can hold seats.
"""
from fastapi import APIRouter, Depends, HTTPException, status
from redis.asyncio import Redis
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from auth import get_current_user, require_role
from database import get_session
from models import AppUser, Showtime
from redis_client import get_redis
from waiting_room import (
    join_waiting_room,
    get_queue_status,
    is_admitted,
    admit_batch,
    remove_from_queue,
    BATCH_SIZE,
    ADMISSION_INTERVAL,
    WAITING_ROOM_TOKEN_TTL_SECONDS,
)

waiting_room_router = APIRouter(
    prefix="/waiting-room/{showtime_id}",
    tags=["waiting-room"],
)


@waiting_room_router.post("/join", status_code=status.HTTP_200_OK)
async def join_queue(
    showtime_id: int,
    user: AppUser = Depends(get_current_user),
    r: Redis = Depends(get_redis),
    session: AsyncSession = Depends(get_session),
):
    """Join the waiting room for a showtime.

    Returns the user's position in the FIFO queue.
    """
    # Verify showtime exists
    result = await session.execute(select(Showtime).where(Showtime.id == showtime_id))
    showtime = result.scalar_one_or_none()
    if showtime is None:
        raise HTTPException(status_code=404, detail="Showtime not found")

    info = await join_waiting_room(r, showtime_id, user.id)

    return {
        "showtime_id": showtime_id,
        "user_id": user.id,
        "position": info["position"],
        "queue_length": info["queue_length"],
    }


@waiting_room_router.get("/status", status_code=status.HTTP_200_OK)
async def queue_status(
    showtime_id: int,
    user: AppUser = Depends(get_current_user),
    r: Redis = Depends(get_redis),
    session: AsyncSession = Depends(get_session),
):
    """Check queue status and the user's position / admission state."""
    # Verify showtime exists
    result = await session.execute(select(Showtime).where(Showtime.id == showtime_id))
    showtime = result.scalar_one_or_none()
    if showtime is None:
        raise HTTPException(status_code=404, detail="Showtime not found")

    queue_info = await get_queue_status(r, showtime_id)
    admitted = await is_admitted(r, showtime_id, user.id)

    return {
        "showtime_id": showtime_id,
        "queue_length": queue_info["queue_length"],
        "admitted_count": queue_info["admitted_count"],
        "you_are_admitted": admitted,
        "token_ttl_seconds": WAITING_ROOM_TOKEN_TTL_SECONDS,
    }


@waiting_room_router.post("/admit", status_code=status.HTTP_200_OK)
async def admit_next_batch(
    showtime_id: int,
    batch_size: int | None = None,
    user: AppUser = Depends(require_role("admin", "theater_manager")),
    r: Redis = Depends(get_redis),
    session: AsyncSession = Depends(get_session),
):
    """Admit the next batch of users from the queue.

    This is called by the admin or the background worker. In normal
    operation the worker calls this automatically; admins can also
    trigger it manually.
    """
    result = await session.execute(select(Showtime).where(Showtime.id == showtime_id))
    showtime = result.scalar_one_or_none()
    if showtime is None:
        raise HTTPException(status_code=404, detail="Showtime not found")

    admitted = await admit_batch(r, showtime_id, batch_size)

    return {
        "showtime_id": showtime_id,
        "admitted_user_ids": admitted,
        "count": len(admitted),
    }


@waiting_room_router.post("/leave", status_code=status.HTTP_204_NO_CONTENT)
async def leave_queue(
    showtime_id: int,
    user: AppUser = Depends(get_current_user),
    r: Redis = Depends(get_redis),
):
    """Remove yourself from the waiting room queue."""
    await remove_from_queue(r, showtime_id, user.id)
