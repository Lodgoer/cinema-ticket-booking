-- Cinema chain: cinemas and halls

CREATE TABLE cinema (
    id          BIGSERIAL PRIMARY KEY,
    name        VARCHAR(255) NOT NULL,
    city        VARCHAR(100) NOT NULL,
    address     VARCHAR(500),
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE hall (
    id          BIGSERIAL PRIMARY KEY,
    cinema_id   BIGINT NOT NULL REFERENCES cinema(id) ON DELETE CASCADE,
    name        VARCHAR(100) NOT NULL,
    capacity    INT NOT NULL CHECK (capacity > 0),
    UNIQUE (cinema_id, name)
);

-- Seat types and physical seats

CREATE TABLE seat_type (
    id      BIGSERIAL PRIMARY KEY,
    name    VARCHAR(50) NOT NULL UNIQUE,        -- regular, vip, couple
    price   NUMERIC(10,2) NOT NULL CHECK (price >= 0)
);

CREATE TABLE seat (
    id              BIGSERIAL PRIMARY KEY,
    hall_id         BIGINT NOT NULL REFERENCES hall(id) ON DELETE CASCADE,
    row_label       VARCHAR(5) NOT NULL,
    seat_number     INT NOT NULL,
    seat_type_id    BIGINT NOT NULL REFERENCES seat_type(id),
    UNIQUE (hall_id, row_label, seat_number)     -- keeps two seats from ever
                                                   -- sharing the same spot in a hall
);

--Movies and showtimes

CREATE TABLE movie (
    id                  BIGSERIAL PRIMARY KEY,
    title               VARCHAR(255) NOT NULL,
    duration_minutes    INT NOT NULL CHECK (duration_minutes > 0),
    genre               VARCHAR(100),
    release_year        INT
);

CREATE TABLE showtime (
    id          BIGSERIAL PRIMARY KEY,
    movie_id    BIGINT NOT NULL REFERENCES movie(id),
    hall_id     BIGINT NOT NULL REFERENCES hall(id),
    starts_at   TIMESTAMPTZ NOT NULL,
    ends_at     TIMESTAMPTZ NOT NULL,             -- stored directly instead of being
                                                    -- calculated from movie.duration_minutes,
                                                    -- so if someone corrects a movie's runtime
                                                    -- later, past showtimes don't quietly shift
    CHECK (ends_at > starts_at)
);

CREATE INDEX idx_showtime_hall_start ON showtime (hall_id, starts_at);

-- Prevents two showtimes from overlapping in the same hall, enforced at
-- the database level. Added in a dedicated migration on Day 2 (moved up
-- from the original plan, since it was a natural fit while working
-- directly on the showtime table). The application layer (routers.py)
-- catches the resulting IntegrityError and returns a clean 409 Conflict
-- instead of letting the raw database error surface.
CREATE EXTENSION IF NOT EXISTS btree_gist;
ALTER TABLE showtime
    ADD CONSTRAINT no_overlapping_showtimes
    EXCLUDE USING gist (
        hall_id WITH =,
        tstzrange(starts_at, ends_at) WITH &&
    );

-- Users and per-cinema managers

CREATE TABLE app_user (
    id              BIGSERIAL PRIMARY KEY,
    name            VARCHAR(255) NOT NULL,
    email           VARCHAR(255) NOT NULL UNIQUE,
    password_hash   VARCHAR(255) NOT NULL,
    role            VARCHAR(20) NOT NULL CHECK (role IN ('customer', 'theater_manager', 'admin')),
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE cinema_manager (
    id          BIGSERIAL PRIMARY KEY,
    user_id     BIGINT NOT NULL REFERENCES app_user(id) ON DELETE CASCADE,
    cinema_id   BIGINT NOT NULL REFERENCES cinema(id) ON DELETE CASCADE,
    UNIQUE (user_id, cinema_id)          -- one manager can cover several cinemas,
                                          -- just not be added to the same one twice
);

-- The contested resource: seat status per showtime
-- A seat here is only ever 'available' or 'booked'. The temporary 'held'
-- state - someone has it selected but hasn't paid yet - lives in Redis with
-- a TTL, not in this table. Writing to Postgres on every seat click would
-- mean hammering the database for state that's mostly throwaway anyway.
-- Postgres only gets touched at the two moments that actually matter: a
-- payment succeeding (-> booked) and a booking getting cancelled (-> available).
CREATE TABLE showtime_seat (
    id              BIGSERIAL PRIMARY KEY,
    showtime_id     BIGINT NOT NULL REFERENCES showtime(id) ON DELETE CASCADE,
    seat_id         BIGINT NOT NULL REFERENCES seat(id),
    status          VARCHAR(20) NOT NULL DEFAULT 'available'
                        CHECK (status IN ('available', 'booked')),
    price_snapshot  NUMERIC(10,2) NOT NULL,      -- the seat_type price at the moment
                                                  -- this showtime was created, copied
                                                  -- over so a later price change doesn't
                                                  -- retroactively touch tickets already sold
    UNIQUE (showtime_id, seat_id)                -- guarantees exactly one row per
                                                  -- seat, per showtime
);

CREATE INDEX idx_showtime_seat_showtime ON showtime_seat (showtime_id);

-- Discounts

CREATE TABLE discount (
    id          BIGSERIAL PRIMARY KEY,
    code        VARCHAR(50) NOT NULL UNIQUE,
    type        VARCHAR(20) NOT NULL CHECK (type IN ('percentage', 'fixed_amount')),
    value       NUMERIC(10,2) NOT NULL CHECK (value > 0),
    valid_from  TIMESTAMPTZ NOT NULL,
    valid_to    TIMESTAMPTZ NOT NULL,
    max_uses    INT,
    used_count  INT NOT NULL DEFAULT 0,
    CHECK (valid_to > valid_from)
);

-- Bookings

CREATE TABLE booking (
    id              BIGSERIAL PRIMARY KEY,
    user_id         BIGINT NOT NULL REFERENCES app_user(id),
    discount_id     BIGINT REFERENCES discount(id),          -- optional, nullable: at
                                                               -- most one discount per booking
    status          VARCHAR(20) NOT NULL DEFAULT 'pending'
                        CHECK (status IN ('pending', 'confirmed', 'cancelled', 'expired')),
    total_price     NUMERIC(10,2) NOT NULL CHECK (total_price >= 0),
    expires_at      TIMESTAMPTZ,                            -- when the hold expires;
                                                              -- background worker sweeps these
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX idx_booking_user ON booking (user_id);

-- A plain UNIQUE(showtime_seat_id) constraint here would've been a trap: the
-- first booking to ever claim a seat would lock it forever, even after being
-- cancelled, since cancelling only flips the status and never deletes the
-- row. So instead, booking_seat carries its own active/cancelled status, and
-- uniqueness is only enforced among the active rows, through the partial
-- index below rather than a table-level UNIQUE. That keeps double-booking
-- impossible while still letting a seat be claimed again after a
-- cancellation, and keeps the history of what happened around too.
CREATE TABLE booking_seat (
    id                  BIGSERIAL PRIMARY KEY,
    booking_id          BIGINT NOT NULL REFERENCES booking(id) ON DELETE CASCADE,
    showtime_seat_id    BIGINT NOT NULL REFERENCES showtime_seat(id),
    status              VARCHAR(20) NOT NULL DEFAULT 'active'
                            CHECK (status IN ('active', 'cancelled'))
);

CREATE INDEX idx_booking_seat_booking ON booking_seat (booking_id);

-- This is really the constraint the whole schema hinges on: a given
-- showtime_seat can be claimed by at most one active booking_seat row at a
-- time. Together with UNIQUE(showtime_id, seat_id) on showtime_seat above,
-- this rules out double-booking at the database level rather than leaving
-- it to application code.
CREATE UNIQUE INDEX uq_active_booking_seat
    ON booking_seat (showtime_seat_id)
    WHERE status = 'active';

-- Payment and tickets

CREATE TABLE payment (
    id                  BIGSERIAL PRIMARY KEY,
    booking_id          BIGINT NOT NULL REFERENCES booking(id) ON DELETE CASCADE,
    amount              NUMERIC(10,2) NOT NULL CHECK (amount >= 0),
    status              VARCHAR(20) NOT NULL DEFAULT 'pending'
                            CHECK (status IN ('pending', 'processing', 'succeeded', 'failed', 'refunded')),
    idempotency_key     VARCHAR(255) NOT NULL UNIQUE,   -- stops a retried request from
                                                         -- charging the same card twice
    provider_ref        VARCHAR(255),
    created_at          TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE ticket (
    id                  BIGSERIAL PRIMARY KEY,
    booking_seat_id     BIGINT NOT NULL UNIQUE REFERENCES booking_seat(id) ON DELETE CASCADE,
    qr_code             VARCHAR(255) NOT NULL UNIQUE,
    issued_at           TIMESTAMPTZ NOT NULL DEFAULT now()
);


-- ============================================================
-- How a cancellation works.
-- Keeps booking / booking_seat / showtime_seat in sync with each other.
--
-- This was originally sketched here as pseudocode during Day 1 schema
-- design. It is now a real, tested implementation:
-- see `booking_service.py::cancel_booking()` (and `sweep_expired_bookings()`
-- for the same logic applied to bookings that expired instead of being
-- explicitly cancelled). The pseudocode below is kept as a quick reference
-- for the transaction shape.
-- ============================================================
--
-- BEGIN;
--
-- -- lock the booking row so two concurrent cancel requests can't race
-- SELECT status FROM booking WHERE id = :booking_id FOR UPDATE;
--
-- -- idempotency check happens in app code right after this read: if
-- -- status is already 'cancelled', stop here and do nothing further
--
-- -- cancel the booking itself
-- UPDATE booking SET status = 'cancelled' WHERE id = :booking_id;
--
-- -- deactivate this booking's seat rows
-- UPDATE booking_seat
-- SET status = 'cancelled'
-- WHERE booking_id = :booking_id AND status = 'active';
--
-- -- and release those seats back to available
-- UPDATE showtime_seat
-- SET status = 'available'
-- WHERE id IN (
--     SELECT showtime_seat_id FROM booking_seat WHERE booking_id = :booking_id
-- );
--
-- COMMIT;
--
-- All five statements commit or roll back together, so a crash between
-- steps 4 and 5 can never leave booking_seat cancelled while showtime_seat
-- is still sitting there marked 'booked'.
 