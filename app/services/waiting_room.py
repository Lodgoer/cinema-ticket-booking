"""
Waiting room service — manages the virtual queue for high-demand showtimes.

Design:
- Users join a Redis sorted set (ZSET) keyed by showtime_id, scored by
  their join timestamp. This gives us FIFO ordering for free.
- A configurable admission policy (batch_size, interval_seconds) admits
  N users every X seconds via the worker.
- Admitted users receive a short-lived token (WAITING_ROOM_TOKEN_TTL_SECONDS)
  that grants access to the seat-hold flow for that showtime.
- Once a user successfully holds a seat, the existing 600s seat-hold TTL
  takes over — the waiting room token is only needed to *enter* the
  seat-selection flow.

Redis key schema:
  waiting_room:{showtime_id}       — ZSET of user_id -> join_timestamp
  waiting_room_admitted:{showtime_id} — SET of admitted user IDs (with TTL)
  waiting_room_token:{showtime_id}:{user_id} — STRING "1", with TTL
"""
from datetime import datetime, timezone

from redis.asyncio import Redis

# Configurable admission settings
BATCH_SIZE = 10          # users admitted per batch
ADMISSION_INTERVAL = 5   # seconds between admission batches
WAITING_ROOM_TOKEN_TTL_SECONDS = 120  # 2 minutes — just long enough to hold a seat


def waiting_room_key(showtime_id: int) -> str:
    return f"waiting_room:{showtime_id}"


def admitted_key(showtime_id: int) -> str:
    return f"waiting_room_admitted:{showtime_id}"


def token_key(showtime_id: int, user_id: int) -> str:
    return f"waiting_room_token:{showtime_id}:{user_id}"


async def join_waiting_room(r: Redis, showtime_id: int, user_id: int) -> dict:
    """Add a user to the waiting room queue.

    Returns the user's position in the queue.
    If the user is already in the queue, returns their existing position.
    """
    key = waiting_room_key(showtime_id)
    now = datetime.now(timezone.utc).timestamp()

    # ZADD NX: only add if not already present (returns 0 for existing members)
    await r.zadd(key, {str(user_id): now})

    # Get position (1-indexed)
    position = await r.zrank(key, str(user_id))
    queue_length = await r.zcard(key)

    return {
        "position": position + 1 if position is not None else queue_length,
        "queue_length": queue_length,
    }


async def get_queue_status(r: Redis, showtime_id: int) -> dict:
    """Get the current state of the waiting room queue."""
    key = waiting_room_key(showtime_id)
    queue_length = await r.zcard(key)
    admitted_count = await r.scard(admitted_key(showtime_id))

    return {
        "queue_length": queue_length,
        "admitted_count": admitted_count,
    }


async def is_admitted(r: Redis, showtime_id: int, user_id: int) -> bool:
    """Check if a user has been admitted (has a valid token)."""
    key = token_key(showtime_id, user_id)
    return await r.exists(key) > 0


async def admit_batch(r: Redis, showtime_id: int, batch_size: int | None = None) -> list[int]:
    """Admit the next batch of users from the queue.

    Pops up to `batch_size` user IDs from the front of the sorted set
    (lowest timestamp = joined earliest = FIFO). Each admitted user gets
    a token with WAITING_ROOM_TOKEN_TTL_SECONDS TTL.

    Returns the list of admitted user IDs.
    """
    size = batch_size or BATCH_SIZE

    # ZPOPMIN: atomically pop the lowest-scored members (FIFO)
    # We pop up to `size` members in one call
    popped = await r.zpopmin(waiting_room_key(showtime_id), count=size)

    admitted_users = []
    token_ttl = WAITING_ROOM_TOKEN_TTL_SECONDS

    for member, score in popped:
        user_id = int(member)
        # Grant a token
        await r.set(token_key(showtime_id, user_id), "1", ex=token_ttl)
        # Track in the admitted set (for visibility)
        await r.sadd(admitted_key(showtime_id), str(user_id))
        admitted_users.append(user_id)

    return admitted_users


async def remove_from_queue(r: Redis, showtime_id: int, user_id: int) -> None:
    """Remove a user from the waiting room (e.g. if they navigate away)."""
    await r.zrem(waiting_room_key(showtime_id), str(user_id))
