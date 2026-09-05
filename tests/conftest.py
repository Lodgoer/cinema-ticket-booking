"""
Shared pytest fixtures for real, in-process tests against a live
Postgres + Redis instance — no mocks, no source-code string matching.

Each test gets its own uniquely-named Cinema/Hall/Movie/Showtime/Seats
(via a random suffix) so tests never collide with each other, and each
fixture cleans up exactly what it created when the test finishes.

Cleanup ORDER matters here because several foreign keys are NOT
declared with ondelete=CASCADE (Showtime.hall_id, Booking.user_id,
BookingSeat.showtime_seat_id) — deleting a parent before its children
would raise a foreign-key violation. If a test uses both
`seeded_showtime` and `test_user`, request `test_user` LAST in the
function signature: pytest tears fixtures down in reverse of the order
they were set up, so test_user's booking cleanup (which touches
showtime_seat via booking_seat) runs before seeded_showtime deletes
the showtime_seat rows themselves.
"""
import uuid
from datetime import datetime, timezone, timedelta
from decimal import Decimal

import pytest_asyncio
import redis.asyncio as redis
from sqlalchemy import select, delete
from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession

from app.settings import settings
from app.models import (
    Cinema, Hall, Movie, Showtime, SeatType, Seat, ShowtimeSeat,
    AppUser, Booking,
)


@pytest_asyncio.fixture
async def session():
    engine = create_async_engine(settings.database_url)
    async with AsyncSession(engine, expire_on_commit=False) as s:
        yield s
    await engine.dispose()


@pytest_asyncio.fixture
async def redis_client():
    r = redis.from_url("redis://localhost:6379", decode_responses=True)
    yield r
    await r.aclose()


@pytest_asyncio.fixture
async def test_user(session):
    """A fresh customer user. Cleans up its own bookings first (which
    cascades to booking_seat and ticket), then itself."""
    unique = uuid.uuid4().hex[:8]
    user = AppUser(
        name=f"TestUser-{unique}",
        email=f"test-{unique}@example.com",
        password_hash="x",
        role="customer",
    )
    session.add(user)
    await session.commit()
    await session.refresh(user)

    yield user

    await session.execute(delete(Booking).where(Booking.user_id == user.id))
    await session.execute(delete(AppUser).where(AppUser.id == user.id))
    await session.commit()


@pytest_asyncio.fixture
async def seeded_showtime(session):
    """An isolated cinema/hall/movie/showtime with 3 seats — 2 at
    10000 and 1 at 20000 — all starting 'available'."""
    unique = uuid.uuid4().hex[:8]
    now = datetime.now(timezone.utc)

    cinema = Cinema(name=f"TestCinema-{unique}", city="Tehran", address="X")
    session.add(cinema)
    await session.flush()

    hall = Hall(cinema_id=cinema.id, name=f"Hall-{unique}", capacity=10)
    session.add(hall)
    await session.flush()

    movie = Movie(title=f"Movie-{unique}", duration_minutes=100)
    session.add(movie)
    await session.flush()

    showtime = Showtime(
        movie_id=movie.id, hall_id=hall.id,
        starts_at=now, ends_at=now + timedelta(hours=2),
    )
    session.add(showtime)
    await session.flush()

    seat_type_std = SeatType(name=f"Standard-{unique}", price=Decimal("10000"))
    seat_type_vip = SeatType(name=f"VIP-{unique}", price=Decimal("20000"))
    session.add_all([seat_type_std, seat_type_vip])
    await session.flush()

    seats = [
        Seat(hall_id=hall.id, row_label="A", seat_number=1, seat_type_id=seat_type_std.id),
        Seat(hall_id=hall.id, row_label="A", seat_number=2, seat_type_id=seat_type_std.id),
        Seat(hall_id=hall.id, row_label="A", seat_number=3, seat_type_id=seat_type_vip.id),
    ]
    session.add_all(seats)
    await session.flush()

    showtime_seats = [
        ShowtimeSeat(showtime_id=showtime.id, seat_id=seats[0].id, status="available", price_snapshot=Decimal("10000")),
        ShowtimeSeat(showtime_id=showtime.id, seat_id=seats[1].id, status="available", price_snapshot=Decimal("10000")),
        ShowtimeSeat(showtime_id=showtime.id, seat_id=seats[2].id, status="available", price_snapshot=Decimal("20000")),
    ]
    session.add_all(showtime_seats)
    await session.commit()
    for ss in showtime_seats:
        await session.refresh(ss)

    data = {
        "cinema": cinema,
        "hall": hall,
        "movie": movie,
        "showtime": showtime,
        "seats": seats,
        "showtime_seats": showtime_seats,
    }

    yield data

    # Cleanup, in dependency order (children before parents):
    await session.execute(delete(ShowtimeSeat).where(ShowtimeSeat.showtime_id == showtime.id))
    await session.execute(delete(Showtime).where(Showtime.id == showtime.id))
    await session.execute(delete(Cinema).where(Cinema.id == cinema.id))  # cascades -> Hall -> Seat
    await session.execute(delete(Movie).where(Movie.id == movie.id))
    await session.execute(delete(SeatType).where(SeatType.id.in_([seat_type_std.id, seat_type_vip.id])))
    await session.commit()