from fastapi import FastAPI

from app.routers.admin_router import router as admin_router
from app.routers.auth_router import auth_router
from app.routers.hold_router import hold_router
from app.routers.booking_router import booking_router
from app.routers.waiting_room_router import waiting_room_router
from app.routers.stats_router import stats_router
from app.routers.reports_router import reports_router

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