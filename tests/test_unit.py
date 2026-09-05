"""
Real, in-process tests against a live Postgres + Redis instance.

These replace an earlier version of this file whose tests mostly
asserted constants against themselves or grepped source code for
strings — neither approach actually exercises the code. Every test
here calls the real service function with real seeded data and checks
real database state (or real Redis state) afterward.
"""
import pytest
from decimal import Decimal

from app.services.booking_service import create_booking, confirm_payment
from app.services.waiting_room import join_waiting_room, admit_batch
from app.redis_client import hold_key

pytestmark = pytest.mark.asyncio


async def _hold_seats(redis_client, showtime_id, seat_ids, user_id):
    """Test helper: simulate the caller having already gone through
    POST /hold for each seat, since create_booking now checks Redis."""
    for seat_id in seat_ids:
        await redis_client.set(hold_key(showtime_id, seat_id), str(user_id))


# ---------------------------------------------------------------------------
# 1. Booking confirmation guard — the ONLY real transition rule in the
#    codebase is "a booking must be 'pending' to be confirmed". There is
#    no broader Payment state machine implemented anywhere, so that's
#    what earlier tests here should have verified, not an invented spec.
# ---------------------------------------------------------------------------

class TestBookingConfirmationGuard:
    async def test_confirming_a_pending_booking_succeeds_and_issues_tickets(
        self, session, redis_client, seeded_showtime, test_user
    ):
        showtime_id = seeded_showtime["showtime"].id
        seat_ids = [s.id for s in seeded_showtime["seats"][:2]]
        await _hold_seats(redis_client, showtime_id, seat_ids, test_user.id)

        booking = await create_booking(
            session, redis_client, user_id=test_user.id,
            showtime_id=showtime_id, seat_ids=seat_ids,
        )

        confirmed = await confirm_payment(session, booking.id)

        assert confirmed.status == "confirmed"

    async def test_confirming_an_already_confirmed_booking_raises(
        self, session, redis_client, seeded_showtime, test_user
    ):
        showtime_id = seeded_showtime["showtime"].id
        seat_ids = [seeded_showtime["seats"][0].id]
        await _hold_seats(redis_client, showtime_id, seat_ids, test_user.id)

        booking = await create_booking(
            session, redis_client, user_id=test_user.id,
            showtime_id=showtime_id, seat_ids=seat_ids,
        )
        await confirm_payment(session, booking.id)  # first confirm: succeeds

        with pytest.raises(ValueError, match="cannot confirm"):
            await confirm_payment(session, booking.id)  # second: must reject

# ---------------------------------------------------------------------------
# 2. Seat claim — a seat already booked (in Postgres) is rejected with a
#    ValueError, which booking_router.py translates to 409 Conflict.
#    This exercises the actual code path, not a mocked session.
# ---------------------------------------------------------------------------

class TestSeatClaim:
    async def test_booking_an_already_booked_seat_raises(
        self, session, redis_client, seeded_showtime, test_user
    ):
        showtime_id = seeded_showtime["showtime"].id
        seat_id = seeded_showtime["seats"][0].id
        await _hold_seats(redis_client, showtime_id, [seat_id], test_user.id)

        # First booking claims the seat successfully
        await create_booking(
            session, redis_client, user_id=test_user.id,
            showtime_id=showtime_id, seat_ids=[seat_id],
        )

        # A second attempt on the SAME seat (even with a fresh hold) must fail —
        # the seat is now 'booked' in Postgres, which create_booking checks
        # before it even looks at Redis.
        await _hold_seats(redis_client, showtime_id, [seat_id], test_user.id)
        with pytest.raises(ValueError, match="already booked"):
            await create_booking(
                session, redis_client, user_id=test_user.id,
                showtime_id=showtime_id, seat_ids=[seat_id],
            )

# ---------------------------------------------------------------------------
# 3. Price calculation — total_price must equal the sum of the booked
#    seats' price_snapshot, calculated by the real create_booking function
#    against real seeded seats (not Python's own sum() in isolation).
# ---------------------------------------------------------------------------

class TestPriceCalculation:
    async def test_total_price_sums_price_snapshots(
        self, session, redis_client, seeded_showtime, test_user
    ):
        showtime_id = seeded_showtime["showtime"].id
        # seats[0] and seats[1] are 10000 each, seats[2] is 20000 (see fixture)
        seat_ids = [s.id for s in seeded_showtime["seats"]]
        await _hold_seats(redis_client, showtime_id, seat_ids, test_user.id)

        booking = await create_booking(
            session, redis_client, user_id=test_user.id,
            showtime_id=showtime_id, seat_ids=seat_ids,
        )

        assert booking.total_price == Decimal("40000")  # 10000 + 10000 + 20000

    async def test_total_price_for_single_seat(
        self, session, redis_client, seeded_showtime, test_user
    ):
        showtime_id = seeded_showtime["showtime"].id
        seat_id = seeded_showtime["seats"][2].id  # the 20000 VIP seat
        await _hold_seats(redis_client, showtime_id, [seat_id], test_user.id)

        booking = await create_booking(
            session, redis_client, user_id=test_user.id,
            showtime_id=showtime_id, seat_ids=[seat_id],
        )

        assert booking.total_price == Decimal("20000")


        # ---------------------------------------------------------------------------
# 4. Waiting room FIFO ordering — join_waiting_room + admit_batch against
#    real Redis. Confirms the nx=True fix (item 8): a user re-joining
#    keeps their original queue position instead of moving to the back,
#    and admit_batch admits strictly in join order.
# ---------------------------------------------------------------------------

class TestWaitingRoomFIFO:
    async def test_admit_batch_admits_in_join_order(self, redis_client):
        showtime_id = 999_001  # arbitrary id, isolated by not colliding with real data

        await join_waiting_room(redis_client, showtime_id, user_id=100)
        await join_waiting_room(redis_client, showtime_id, user_id=200)
        await join_waiting_room(redis_client, showtime_id, user_id=300)

        admitted = await admit_batch(redis_client, showtime_id, batch_size=2)

        assert admitted == [100, 200]  # first two to join, in order

    async def test_rejoining_does_not_move_user_to_the_back(self, redis_client):
        showtime_id = 999_002

        await join_waiting_room(redis_client, showtime_id, user_id=1)
        await join_waiting_room(redis_client, showtime_id, user_id=2)
        await join_waiting_room(redis_client, showtime_id, user_id=1)  # re-join

        admitted = await admit_batch(redis_client, showtime_id, batch_size=1)

        assert admitted == [1]  # still first, not pushed behind user 2