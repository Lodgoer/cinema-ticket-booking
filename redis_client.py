"""
Redis connection setup, separate from database.py (Postgres) because
Redis here plays a fundamentally different role: it's not the source of
truth, it's a fast, TTL-based layer for transient state (seat holds).
"""
import redis.asyncio as redis

REDIS_URL = "redis://localhost:6379"

redis_client = redis.from_url(REDIS_URL, decode_responses=True)
# decode_responses=True means values come back as Python str, not bytes —
# saves having to .decode() everywhere we read a key.


async def get_redis() -> redis.Redis:
    return redis_client
