# cinema-ticket-booking

A ticket-booking backend for a cinema chain, built with FastAPI, PostgreSQL (async), and Redis.

## Architecture

| Layer | File(s) | Purpose |
|-------|---------|---------|
| **Routers** | `routers.py`, `auth_router.py`, `hold_router.py`, `booking_router.py`, `waiting_room_router.py`, `stats_router.py`, `reports_router.py` | FastAPI endpoints — the HTTP boundary |
| **Services** | `booking_service.py`, `waiting_room.py` | Business logic — seat holds, booking lifecycle, waiting room admission |
| **Repositories** | `repositories.py` | Generic CRUD data access (cinemas, halls, seats, movies, showtimes, users) |
| **Models** | `models.py` | SQLAlchemy ORM models (14 tables) |
| **Schemas** | `schemas.py` | Pydantic input/output models |
| **Auth** | `auth.py` | JWT authentication + role-based authorization |
| **Payment** | `payment_provider.py` | Fake Stripe-like payment sandbox |
| **Worker** | `worker.py` | Background tasks via arq (booking sweep, waiting room admission, view refresh) |

## Key Design Decisions

### Dual-layer seat protection (Redis + Postgres)
- **Redis** handles the fast, optimistic hold (`SET NX EX 600`) — prevents most concurrent double-booking at microsecond speed.
- **Postgres** has a partial unique index (`uq_active_booking_seat WHERE status = 'active'`) — catches any edge case where Redis and Postgres state diverge. This makes double-booking *structurally impossible*.
- The booking endpoint catches `IntegrityError` and translates it to a clean `409 Conflict`.

### Waiting Room
- **Redis Sorted Set** (`waiting_room:{showtime_id}`) with join timestamp as score — gives FIFO ordering for free.
- **Batch admission**: N users are admitted every X seconds (configurable `BATCH_SIZE` and `ADMISSION_INTERVAL`).
- **Admission token**: admitted users get a short-lived Redis key (`waiting_room_token:{showtime_id}:{user_id}`) valid for **120 seconds** — just long enough to hold seats. The existing 600-second seat-hold TTL takes over once a seat is held.
- The waiting room is **optional per showtime** — if no one has joined, seat holds work as before.

### Statistics & Reports — Role-Based Access
- **Admin** users see statistics across ALL cinemas.
- **Theater manager** users see statistics ONLY for cinemas they manage (via the `cinema_manager` junction table).
- **Customer** users have no access to statistics.

### Materialized View: Occupancy Rate

**Why a materialized view for occupancy rate but not for other reports?**

| Report | Implementation | Reason |
|--------|---------------|--------|
| **Occupancy rate** | Materialized view (`mv_occupancy_rate`) | Complex JOIN across showtime + hall + cinema + showtime_seat; queried frequently; benefits from pre-computation |
| Sales by movie/cinema | Regular aggregate query | Simpler query, less frequent, real-time data preferred |
| Revenue over time | Regular aggregate query | Time-series data is more valuable fresh |
| Peak hours | Regular aggregate query | Lightweight aggregation, no complex JOINs |

The materialized view is refreshed every **5 minutes** by the background worker (`REFRESH MATERIALIZED VIEW CONCURRENTLY`). The `CONCURRENTLY` flag ensures the view remains readable during refresh.

**Trade-off**: The occupancy data may be up to 5 minutes stale. This is acceptable for dashboard views where approximate numbers are fine. For real-time seat availability, the Redis hold map + Postgres status is the source of truth.

## API Endpoints

### Authentication
| Method | Path | Description |
|--------|------|-------------|
| POST | `/auth/register` | Register a new user |
| POST | `/auth/login` | Login, get JWT |

### Seat Hold (requires auth)
| Method | Path | Description |
|--------|------|-------------|
| POST | `/showtimes/{id}/seats/{id}/hold` | Hold a seat (600s TTL) |
| DELETE | `/showtimes/{id}/seats/{id}/hold` | Release a hold |
| GET | `/showtimes/{id}/seats` | Seat map (available/booked/held) |

### Waiting Room (requires auth)
| Method | Path | Description |
|--------|------|-------------|
| POST | `/waiting-room/{showtime_id}/join` | Join the queue |
| GET | `/waiting-room/{showtime_id}/status` | Queue position & admission status |
| POST | `/waiting-room/{showtime_id}/admit` | Trigger admission batch (admin/manager) |
| POST | `/waiting-room/{showtime_id}/leave` | Leave the queue |

### Bookings (requires auth)
| Method | Path | Description |
|--------|------|-------------|
| POST | `/bookings` | Create booking from held seats |
| GET | `/bookings/{id}` | Get booking details |
| POST | `/bookings/{id}/cancel` | Cancel a booking |
| POST | `/bookings/pay` | Process payment |
| GET | `/bookings/{id}/tickets` | Get issued tickets |

### Admin CRUD (requires admin/manager role)
| Method | Path | Description |
|--------|------|-------------|
| POST | `/admin/cinemas` | Create cinema |
| GET | `/admin/cinemas` | List cinemas |
| ... | ... | Full CRUD for cinemas, halls, seat types, seats, movies, showtimes |

### Statistics (requires admin/manager role)
| Method | Path | Description |
|--------|------|-------------|
| GET | `/stats/sales-by-movie` | Tickets sold & revenue by movie |
| GET | `/stats/sales-by-cinema` | Tickets sold & revenue by cinema |
| GET | `/stats/sales-by-showtime` | Tickets sold & revenue by showtime |
| GET | `/stats/revenue-over-time` | Daily revenue trend |
| GET | `/stats/peak-hours` | Most popular showtime hours |

### Reports (requires admin/manager role)
| Method | Path | Description |
|--------|------|-------------|
| GET | `/reports/occupancy-rate` | Occupancy rate per showtime (materialized view) |
| GET | `/reports/popular-showtimes` | Top showtimes by tickets sold |

## Running

```bash
# Start infrastructure
docker compose up -d

# Run migrations
alembic upgrade head

# Refresh the materialized view (after first migration)
# In psql: REFRESH MATERIALIZED VIEW mv_occupancy_rate;

# Start the server
uvicorn main:app --reload

# Start the background worker
python -m arq worker.WorkerSettings

# Run tests
pytest test_concurrency.py -v
pytest test_unit.py -v
```

## Configuration

Key settings in `waiting_room.py`:
- `BATCH_SIZE = 10` — users admitted per batch
- `ADMISSION_INTERVAL = 5` — seconds between admission batches
- `WAITING_ROOM_TOKEN_TTL_SECONDS = 120` — admission token lifetime

Key settings in `booking_service.py`:
- `BOOKING_HOLD_MINUTES = 10` — booking expiry (matches Redis hold TTL)

Key settings in `hold_router.py`:
- `HOLD_TTL_SECONDS = 600` — seat hold TTL in Redis
