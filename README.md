# cinema-ticket-booking

A ticket-booking backend for a cinema chain, built with FastAPI, PostgreSQL (async), and Redis. Guarantees that **two customers can never end up with the same seat**, even under heavy concurrent load — a fast optimistic layer (Redis) for the common case, backed by a hard structural guarantee (a Postgres constraint) that holds even if the fast layer fails.

**Stack:** FastAPI (async) · PostgreSQL 16 · Redis 7 · SQLAlchemy 2.0 (async) · Alembic · arq · Docker Compose

**ERD:** [docs/erd.pdf](docs/erd.pdf)

---

## Quick Start

```bash
# Clone the repo
git clone https://github.com/Lodgoer/cinema-ticket-booking.git
cd cinema-ticket-booking

# Create and activate a virtual environment
python -m venv venv
venv\Scripts\activate        # Windows
# source venv/bin/activate   # macOS/Linux

# Install dependencies
pip install -r requirements.txt

# Start infrastructure (Postgres + Redis)
docker compose up -d

# Run migrations
alembic upgrade head

# Refresh the materialized view (after first migration)
# In psql: REFRESH MATERIALIZED VIEW mv_occupancy_rate;

# Start the server
uvicorn main:app --reload

# In a separate terminal: start the background worker
python -m arq worker.WorkerSettings

# In a separate terminal: run tests
pytest tests/test_unit.py -v
pytest tests/test_concurrency.py -v   # requires the server running
```

Once running, interactive API docs are available at `http://127.0.0.1:8000/docs`.

**Prerequisites:** Docker Desktop (for Postgres and Redis containers) · Python 3.11+ · a VPN may be required to pull Docker images or push/pull from GitHub, depending on network restrictions in some regions.

---

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

---

## Deep Dive

The sections below cover the *why* behind the harder decisions in this project — useful if you want to understand the reasoning, not just the code.

### Overview

This project covers the full journey from an admin setting up cinemas, halls, and showtimes, through a customer selecting a seat, holding it, paying for it, and receiving a QR-coded ticket — plus a virtual waiting room for high-demand showtimes and a management reporting layer. The design deliberately mirrors how real systems (Ticketmaster, BookMyShow, Airbnb) solve the same double-booking problem.

### Architecture

| Layer | File(s) | Purpose |
|-------|---------|---------|
| **Routers** | `app/routers/admin_router.py`, `app/routers/auth_router.py`, `app/routers/hold_router.py`, `app/routers/booking_router.py`, `app/routers/waiting_room_router.py`, `app/routers/stats_router.py`, `app/routers/reports_router.py` | FastAPI endpoints — the HTTP boundary |
| **Services** | `app/services/booking_service.py`, `app/services/waiting_room.py` | Business logic — seat holds, booking lifecycle, waiting room admission |
| **Repositories** | `app/repositories.py` | Generic CRUD data access (cinemas, halls, seats, movies, showtimes, users) |
| **Models** | `app/models.py` | SQLAlchemy ORM models (14 tables) |
| **Schemas** | `app/schemas.py` | Pydantic input/output models |
| **Auth** | `app/auth.py` | JWT authentication + role-based authorization |
| **Payment** | `app/services/payment_provider.py` | Fake Stripe-like payment sandbox |
| **Worker** | `worker.py` | Background tasks via arq (booking sweep, waiting room admission, view refresh) |

### Key Design Decisions

#### Why `ShowtimeSeat` is separate from `Seat`

A `Seat` (row A, seat 1, VIP) is a physical fixture of a hall — it exists once and is reused across thousands of showtimes over its lifetime. Its *booking status*, however, is meaningless without reference to a specific showtime: seat A1 can be booked for the 5pm screening and free for the 8pm screening on the same day. Storing status directly on `Seat` would allow only one status per seat *ever*. `ShowtimeSeat` creates one row per (seat, showtime) pair, so each combination carries its own independent status.

#### Why `price_snapshot` instead of a live link to `seat_type.price`

`showtime_seat.price_snapshot` copies the seat type's price at the moment a showtime is created, rather than joining to `seat_type.price` live at read time. This is deliberate: if the cinema later changes VIP pricing, tickets already sold at the old price must not silently change value. Snapshotting price at showtime-creation time keeps historical bookings and revenue reports accurate regardless of later price changes.

#### Why `held` lives only in Redis, never in Postgres

`showtime_seat.status` only ever takes the values `available` / `booked` — there is no `held` state in Postgres. A "held" seat (someone has it selected but hasn't paid) is a high-frequency, mostly-throwaway piece of state — most holds expire without ever converting to a booking. Writing every hold/release to Postgres would mean hammering the primary database on every seat click. Instead, holds live entirely in Redis as a TTL key (`SET NX EX 600`), and Postgres is only touched at the two moments that actually matter: a payment succeeding (`available` → `booked`) or a booking being cancelled/expiring (`booked` → `available`).

#### The partial unique index on `booking_seat` — and the bug it fixes

The first version of `booking_seat` used a plain `UNIQUE (showtime_seat_id)` constraint. This was a trap: cancelling a booking only flips `booking.status` — it never deletes the corresponding `booking_seat` row. With a plain `UNIQUE` constraint, the very first booking to ever claim a seat would lock it **forever**, even after being cancelled, because that old row (now irrelevant) still satisfied the uniqueness check and blocked any new claim on the same seat.

The fix: `booking_seat` carries its own `active` / `cancelled` status, and uniqueness is enforced only among `active` rows, via a **partial unique index**:

```sql
CREATE UNIQUE INDEX uq_active_booking_seat
    ON booking_seat (showtime_seat_id)
    WHERE status = 'active';
```

A cancelled row is excluded from the uniqueness check entirely — a new customer can claim the same seat with a brand-new `active` row, while the cancelled row remains as history. This is the single constraint the entire double-booking guarantee hinges on: it is enforced by Postgres itself, not application code, so it holds even under a race between two concurrent requests.

#### Why this is enforced in the database, not application code

A check like "is this seat free? if so, book it" written in application code is inherently non-atomic: between the check and the write, another request can slip in and make the same decision based on stale information (a classic race condition). A database constraint, by contrast, is evaluated atomically by Postgres itself as part of the `INSERT` — there is no window where two concurrent requests can both "pass" the check. This is why the seat-claim guarantee lives in the schema, not in a `if seat.is_free:` check in Python.

#### Why background sweep, not lazy check, for booking expiry

`booking.expires_at` marks when an unpaid booking should be released. Two strategies were considered:
- **Lazy check** — check expiry at read time, right when another customer tries to claim the seat. Zero extra cost, reacts instantly, but touches more endpoints and is more complex to implement correctly.
- **Sweep** (chosen) — a background worker (`worker.py`, via `arq`) scans for expired pending bookings every 60 seconds and releases them.

Sweep was chosen for simplicity within a one-week project scope. The trade-off — up to a 1-minute window where a seat appears unavailable even though its hold technically expired — was judged acceptable at this scale. Lazy check is documented as a future improvement. (Redis keyspace notifications were also considered and rejected: Redis Pub/Sub is fire-and-forget, so a disconnected consumer permanently loses expiry events — an unacceptable risk for a money-relevant operation like releasing a paid-for hold.)

#### Why `CinemaManager` for row-level access control

`theater_manager` users should only see data (statistics, reports) for cinemas they actually manage — not the entire chain. Rather than hardcoding cinema ownership onto the `AppUser` table (which would only allow one manager per cinema), a junction table `cinema_manager (user_id, cinema_id)` was introduced. This allows a many-to-many relationship (one manager can cover several cinemas; a cinema could have more than one manager) and keeps authorization logic in one place: every stats/reports endpoint calls `get_managed_cinema_ids()`, which returns `None` for admins (no filter) or a list of cinema IDs for managers (used in a `WHERE cinema_id IN (...)` filter).

#### Materialized View: Occupancy Rate

**Why a materialized view for occupancy rate but not for other reports?**

| Report | Implementation | Reason |
|--------|---------------|--------|
| **Occupancy rate** | Materialized view (`mv_occupancy_rate`) | Complex JOIN across showtime + hall + cinema + showtime_seat; queried frequently; benefits from pre-computation |
| Sales by movie/cinema | Regular aggregate query | Simpler query, less frequent, real-time data preferred |
| Revenue over time | Regular aggregate query | Time-series data is more valuable fresh |
| Peak hours | Regular aggregate query | Lightweight aggregation, no complex JOINs |

The materialized view is refreshed every **5 minutes** by the background worker (`REFRESH MATERIALIZED VIEW CONCURRENTLY`). The `CONCURRENTLY` flag ensures the view remains readable during refresh.

**Trade-off**: The occupancy data may be up to 5 minutes stale. This is acceptable for dashboard views where approximate numbers are fine. For real-time seat availability, the Redis hold map + Postgres status is the source of truth.

### How I Tested It

#### Concurrency: proving double-booking is structurally impossible

`tests/test_concurrency.py` simulates two users racing for the same seat with `asyncio.gather`, firing both hold requests simultaneously:

```python
hold_a, hold_b = await asyncio.gather(
    client.post(f"/showtimes/{id}/seats/{id}/hold", headers=headers_a),
    client.post(f"/showtimes/{id}/seats/{id}/hold", headers=headers_b),
)
assert results.count(200) == 1  # exactly one winner
```

This validates both protection layers together: Redis `SET NX` ensures only one hold succeeds under normal conditions, and the Postgres partial unique index is the structural backstop that would still catch a double-claim even if the Redis layer were somehow bypassed. A second test confirms that once a seat is `booked`, any further hold attempt is rejected with `409`.

Manual verification of the full claim → cancel → reclaim cycle was also run directly against Postgres (`psql`) during initial schema design: a seat was claimed, a second claim attempt correctly failed with `duplicate key value violates unique constraint "uq_active_booking_seat"`, the first booking was cancelled, and the seat was then successfully reclaimed — confirming the partial index behaves exactly as designed, not just in theory.

#### Unit tests

`tests/test_unit.py` (20 tests, all passing) covers:
- Payment state machine transitions (valid and invalid)
- `IntegrityError` → `409 Conflict` translation
- Price calculation from `price_snapshot` (including decimal precision)
- Waiting room FIFO ordering guarantees

#### End-to-end flow tested manually via Swagger (`/docs`)

`hold seat → create booking (pending) → pay → get tickets (QR code)`, confirmed at each step that the seat map correctly reflected `available` → `held` → `booked` transitions, combining live Redis state with Postgres state.

### Configuration

Key settings in `app/services/waiting_room.py`:
- `BATCH_SIZE = 10` — users admitted per batch
- `ADMISSION_INTERVAL = 5` — seconds between admission batches
- `WAITING_ROOM_TOKEN_TTL_SECONDS = 120` — admission token lifetime

Key settings in `app/services/booking_service.py`:
- `BOOKING_HOLD_MINUTES = 10` — booking expiry (matches Redis hold TTL)

Key settings in `app/routers/hold_router.py`:
- `HOLD_TTL_SECONDS = 600` — seat hold TTL in Redis

### Known Limitations

- `tests/test_concurrency.py` uses hardcoded `showtime_id=1` and `seat_id=1`, coupling tests to specific seeded data. Planned fix: isolated test fixtures per test run (see TODO in the test file).
- `app/auth.py`'s `SECRET_KEY` is currently hardcoded for local development and must move to an environment variable (`.env`) before any real deployment.
- Booking expiry uses a 60-second sweep rather than lazy/event-driven expiry (see "Why background sweep" above) — acceptable at this project's scale but noted as a scaling consideration..
- Some secret keys are hard coded.
