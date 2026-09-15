"""
Redis connection setup, separate from database.py (Postgres) because
Redis here plays a fundamentally different role: it's not the source of
truth, it's a fast, TTL-based layer for transient state (seat holds).
"""
import redis.asyncio as redis

from app.settings import settings

redis_client = redis.from_url(settings.redis_url, decode_responses=True)
# decode_responses=True means values come back as Python str, not bytes —
# saves having to .decode() everywhere we read a key.


async def get_redis() -> redis.Redis:
    return redis_client

def hold_key(showtime_id: int, seat_id: int) -> str:
    return f"seat_hold:{showtime_id}:{seat_id}"
