from fastapi import FastAPI

from routers import router as admin_router
from auth_router import auth_router
from hold_router import hold_router
from booking_router import booking_router
from waiting_room_router import waiting_room_router
from stats_router import stats_router
from reports_router import reports_router

import asyncpg
import redis

app = FastAPI()
app.include_router(admin_router)
app.include_router(auth_router)
app.include_router(hold_router)
app.include_router(booking_router)
app.include_router(waiting_room_router)
app.include_router(stats_router)
app.include_router(reports_router)

DATABASE_URL = "postgresql://postgres:mysecret@localhost:5432/cinema_db"
REDIS_URL = "redis://localhost:6379"

@app.get("/health")
async def health_check():
    result = {"postgres": "unknown", "redis": "unknown"}

    try:
        conn = await asyncpg.connect(DATABASE_URL)
        await conn.close()
        result["postgres"] = "connected"
    except Exception as e:
        result["postgres"] = f"error: {str(e)}"

    try:
        r = redis.Redis.from_url(REDIS_URL)
        r.ping()
        result["redis"] = "connected"
    except Exception as e:
        result["redis"] = f"error: {str(e)}"

    return result
