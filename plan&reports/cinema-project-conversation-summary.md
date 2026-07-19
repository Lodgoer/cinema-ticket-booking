# Cinema Ticket Booking Project — Conversation Summary

A running log of the design discussion for a one-week portfolio project: an online cinema ticket booking backend, built to be documented on GitHub and defended in a job interview. The focus is database design, engineering trade-offs, and creativity (not just a working CRUD app).

Stack: **Python, FastAPI, PostgreSQL, Redis**, Docker Compose for local dev.

---

## 1. Project scope (as defined)

- Admin/cinema data manager: defines cinemas, halls, seat layouts
- Customer-facing booking flow with real-time seat selection
- Core engineering problems to solve, not just features:
  - **Consistency**: what happens when multiple people try to buy the same seat
  - **Queue management** for high-demand showtimes
  - **Payment** (sandbox) and financial reconciliation
  - **Ticket generation**
  - **Statistics** for cinema manager/admin
- Emphasis: database design quality and the reasoning behind technical decisions matter more than feature count.

---

## 2. Core data model

Key modeling decision: separate the **physical seat** from **the seat's status per showtime**.

- `Cinema` → `Hall` (screen) → `Seat` (physical: row, number, type — regular/VIP/couple)
- `Movie` → `Showtime` (movie + hall + start time)
- `ShowtimeSeat` (showtime_id, seat_id, status: available/held/booked, price) — this is the table under contention
- `User` (roles: customer, theater_manager, admin)
- `Booking` → `BookingSeat` (links a booking to the showtime seats chosen)
- `Payment` (state machine, idempotency key)
- `Ticket` (issued after payment success, QR code)

Critical constraint: `UNIQUE(showtime_id, seat_id)` on the booking-seat relationship — the database-level guarantee that no double-booking can ever occur, regardless of application logic bugs.

---

## 3. The core hard problem: concurrent seat selection

Two-layer approach discussed and diagrammed:

1. **Redis (soft hold)**: `SET seat_hold:{showtime_id}:{seat_id} user_id NX EX 600` — atomic, auto-expiring (10 min TTL) hold. Resolves ~all contention cheaply and immediately; abandoned holds clean themselves up via TTL, no cleanup job needed.
2. **Postgres (hard guarantee)**: final booking insert wrapped in a transaction relying on the `UNIQUE(showtime_id, seat_id)` constraint. This is the true source of truth — even if Redis fails or a hold expires mid-payment, the DB constraint makes double-booking structurally impossible.

This dual-layer design was flagged as the strongest interview talking point in the project.

---

## 4. Other engineering pieces discussed

- **Queue management**: for high-demand showtimes, a virtual waiting room using a Redis sorted set (score = arrival time), admitting N users per interval via short-lived tokens — protects the seat-hold system from being overwhelmed.
- **Payment (sandbox)**: state machine `pending → processing → succeeded/failed → refunded`, idempotency keys to prevent double charges, confirmation handled via a background worker (Celery or `arq`) rather than inline in the request.
- **Ticket generation**: QR code issued after payment success, handled asynchronously.
- **Statistics**: revenue and occupancy reporting for admins/managers using Postgres aggregate/window functions, optionally materialized views for heavier reports.

---

## 5. Architecture and stack mapping (from her .NET/EF Core background)

| .NET / EF Core | Python equivalent |
|---|---|
| ASP.NET Core Web API | FastAPI |
| SQL Server | PostgreSQL |
| Entity Framework Core | SQLAlchemy |
| EF Core Migrations | Alembic |
| — (no direct equivalent) | Redis |

Layering suggested to match her existing clean-architecture habits: `domain` → `application` (use cases) → `infrastructure` (repositories, Redis client, payment adapter) → `api` (routers).

---

## 6. Local environment setup

- Docker Desktop recommended over native installs — mainly because **Redis has no official Windows support** (would otherwise require WSL2 or Memurai), and because a `docker-compose.yml` in the repo is itself a portfolio plus (reproducible environment, no manual setup needed to run/test the project).
- `docker-compose.yml` sketch provided for Postgres + Redis; FastAPI app run locally via `uvicorn` for easier debugging.
- Python packages: `fastapi uvicorn[standard] sqlalchemy asyncpg alembic redis pydantic-settings`

---

## 7. Research methodology discussion

Agreed approach: use AI (Claude) for concept explanations, debugging, and reviewing decisions; use real web research for seeing how real companies solved the same problem (material citable in interviews). Resources found and recommended:

- Article on how Ticketmaster, BookMyShow, and Airbnb each solve double-booking differently (no universal single answer)
- GitHub repo `grokking-the-object-oriented-design-interview`, which includes a detailed cinema ticket booking system design case (use cases, concurrency handling via isolation levels/transactions)
- Article on Redis-based distributed locking for seat reservation across multiple servers, referencing Ticketmaster's real-world load challenges (e.g., high-demand on-sale events)

Suggested routine: short, specific searches (20–30 min blocks), notes captured as "company did X, because Y, trade-off was Z" — directly reusable in the README and interview prep.

---

## 8. Database design philosophy (latest discussion)

Recommendation: **design the schema database-first**, not code-first through the ORM.

- EF Core's code-first style can let a developer skip deep thinking about keys, normalization, indexes, and constraints, since the tool generates the SQL.
- Since database design is the skill she most wants to demonstrate, the plan is: draw the ERD → write raw `CREATE TABLE` DDL by hand (including PKs, FKs, the `UNIQUE(showtime_id, seat_id)` constraint, and relevant indexes) → *then* translate into SQLAlchemy models.
- Suggested deliverable: keep a `schema.sql` file in the repo alongside the SQLAlchemy models, showing command of both the raw SQL layer and the ORM layer.

---

## 9. One-week plan (full detail saved separately)

A complete day-by-day plan (Day 1–7) with morning/afternoon task breakdowns, research blocks, deliverables per day, and an interview-prep question list was created as a separate file: **`plan-yek-hafteh-cinema-project.md`**. Highlights:

- Day 1: environment setup + ERD + schema design
- Day 2: admin CRUD (cinemas, halls, seats, movies, showtimes)
- Day 3: focused Redis learning block + seat-hold implementation
- Day 4: final consistency guarantee + concurrency testing (the key interview-defensible test)
- Day 5: payment sandbox + background worker + ticket issuance
- Day 6: waiting-room queue + statistics endpoints
- Day 7: tests, documentation (with a "why these decisions" section), interview prep

---

## Open next steps

- Draw/finalize the ERD and write the raw SQL schema (Day 1 task)
- Then translate into SQLAlchemy models + first Alembic migration
