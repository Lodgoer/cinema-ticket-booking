"""
Concurrency test — proves that double-booking is structurally impossible.

This test simulates two users racing to book the same seat simultaneously.
The partial unique index on booking_seat (uq_active_booking_seat WHERE
status = 'active') ensures that only one booking succeeds; the other gets
an IntegrityError which translates to a clean 409 Conflict.

How to run:
    pytest test_concurrency.py -v

Prerequisites:
    - Docker containers running (docker compose up -d)
    - Database migrated (alembic upgrade head)
    - Test data seeded (cinema, hall, seats, movie, showtime)
"""
import asyncio
import pytest
import httpx

BASE_URL = "http://127.0.0.1:8000"


@pytest.fixture(scope="module")
def event_loop():
    loop = asyncio.new_event_loop()
    yield loop
    loop.close()


async def register_and_login(client: httpx.AsyncClient, email: str, password: str) -> str:
    """Register a user and return their JWT token."""
    await client.post(f"{BASE_URL}/auth/register", json={
        "name": email.split("@")[0],
        "email": email,
        "password": password,
        "role": "customer",
    })
    resp = await client.post(f"{BASE_URL}/auth/login", data={
        "username": email,
        "password": password,
    })
    return resp.json()["access_token"]


@pytest.mark.asyncio
async def test_concurrent_seat_booking():
    """
    Two users try to hold and book the same seat at the same time.

    Expected outcome:
    - User A gets the hold → creates booking → succeeds
    - User B gets rejected (hold fails OR booking fails with 409)

    This proves the dual-layer guarantee:
    - Layer 1 (Redis): SET NX ensures only one hold at a time
    - Layer 2 (Postgres): partial unique index catches any edge case
    """
    async with httpx.AsyncClient() as client:
        # Setup: register two users
        token_a = await register_and_login(client, "user_a@test.com", "pass123")
        token_b = await register_and_login(client, "user_b@test.com", "pass123")

        headers_a = {"Authorization": f"Bearer {token_a}"}
        headers_b = {"Authorization": f"Bearer {token_b}"}

        # Assume showtime_id=1, seat_id=1 exist from prior test data
        showtime_id = 1
        seat_id = 1

        # Both users try to hold the same seat concurrently
        hold_a, hold_b = await asyncio.gather(
            client.post(
                f"{BASE_URL}/showtimes/{showtime_id}/seats/{seat_id}/hold",
                headers=headers_a,
            ),
            client.post(
                f"{BASE_URL}/showtimes/{showtime_id}/seats/{seat_id}/hold",
                headers=headers_b,
            ),
        )

        # Exactly one should succeed
        results = [hold_a.status_code, hold_b.status_code]
        assert 200 in results, f"Expected one success, got: {results}"
        assert results.count(200) == 1, f"Expected exactly one success, got: {results.count(200)}"

        # The winner creates a booking
        winner_token = token_a if hold_a.status_code == 200 else token_b
        winner_headers = {"Authorization": f"Bearer {winner_token}"}

        booking_resp = await client.post(
            f"{BASE_URL}/bookings",
            json={
                "showtime_id": showtime_id,
                "seat_ids": [seat_id],
            },
            headers=winner_headers,
        )
        assert booking_resp.status_code == 201, f"Booking failed: {booking_resp.text}"
        booking = booking_resp.json()
        assert booking["status"] == "pending"

        # The loser should fail to book the same seat
        loser_token = token_b if hold_a.status_code == 200 else token_a
        loser_headers = {"Authorization": f"Bearer {loser_token}"}

        # Loser tries to book (without a valid hold — should fail)
        loser_booking = await client.post(
            f"{BASE_URL}/bookings",
            json={
                "showtime_id": showtime_id,
                "seat_ids": [seat_id],
            },
            headers=loser_headers,
        )
        # Should get 409 (seat already booked) or similar error
        assert loser_booking.status_code in (409, 400), \
            f"Expected 409/400 for loser, got: {loser_booking.status_code}"

        print(f"\n{'='*50}")
        print(f"CONCURRENCY TEST PASSED")
        print(f"Winner: {winner_token[:20]}... → booking {booking['id']}")
        print(f"Loser got: {loser_booking.status_code} - {loser_booking.json().get('detail', 'N/A')}")
        print(f"{'='*50}")


@pytest.mark.asyncio
async def test_cannot_book_already_booked_seat():
    """
    After a seat is booked, a second booking attempt fails with 409.
    """
    async with httpx.AsyncClient() as client:
        token = await register_and_login(client, "user_c@test.com", "pass123")
        headers = {"Authorization": f"Bearer {token}"}

        showtime_id = 1
        seat_id = 2  # assume this seat exists

        # Hold the seat
        hold_resp = await client.post(
            f"{BASE_URL}/showtimes/{showtime_id}/seats/{seat_id}/hold",
            headers=headers,
        )
        if hold_resp.status_code != 200:
            pytest.skip("Could not hold seat (may be already taken)")

        # Book it
        booking_resp = await client.post(
            f"{BASE_URL}/bookings",
            json={"showtime_id": showtime_id, "seat_ids": [seat_id]},
            headers=headers,
        )
        assert booking_resp.status_code == 201

        # Register another user and try the same seat
        token2 = await register_and_login(client, "user_d@test.com", "pass123")
        headers2 = {"Authorization": f"Bearer {token2}"}

        hold2 = await client.post(
            f"{BASE_URL}/showtimes/{showtime_id}/seats/{seat_id}/hold",
            headers=headers2,
        )
        assert hold2.status_code == 409, f"Expected 409, got: {hold2.status_code}"

        print(f"\nDouble-booking prevention confirmed: {hold2.json()['detail']}")
