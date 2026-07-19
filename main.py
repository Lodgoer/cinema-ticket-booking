from fastapi import FastAPI
from routers import router as admin_router
from auth_router import auth_router
import asyncpg
import redis

app = FastAPI()
app.include_router(admin_router)
app.include_router(auth_router)

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