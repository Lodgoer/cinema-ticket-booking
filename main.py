from fastapi import FastAPI
import asyncpg
import redis

app = FastAPI()

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