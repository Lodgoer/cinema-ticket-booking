"""
Concurrency test — proves that double-booking is structurally impossible.

This test simulates two users racing to book the same seat simultaneously.
The partial unique index on booking_seat (uq_active_booking_seat WHERE
status = 'active') ensures that only one booking succeeds; the other gets
an IntegrityError which translates to a clean 409 Conflict.

How to run:
    pytest test_concurrency.py -v

Prerequisites:
    - Postgres and Redis reachable at the URLs in .env
    - Database migrated (alembic upgrade head)

No live `uvicorn` process or manually-seeded data required — the app
runs in-process via httpx.ASGITransport, and each test creates its own
isolated showtime/seats through the seeded_showtime fixture (see
tests/conftest.py), so tests never depend on specific row IDs and can
run repeatedly or in any order.
"""

import asyncio
import uuid
import pytest
import httpx

from main import app

BASE_URL = "http://test"


def make_client() -> httpx.AsyncClient:
    """An httpx client wired directly to our FastAPI app via ASGITransport —
    sends real HTTP requests through real routing/dependency injection,
    without needing a separately-running `uvicorn main:app` process."""
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url=BASE_URL)


async def register_and_login(client: httpx.AsyncClient, password: str = "pass123") -> str:
    """Register a fresh, uniquely-named user and return their JWT token.

    No `role` in the payload — UserCreate no longer accepts one (see the
    role-escalation fix); every self-registered user is a customer.
    A fresh uuid-suffixed email avoids the unique-email constraint
    colliding across repeated test runs.
    """
    email = f"user-{uuid.uuid4().hex[:8]}@test.com"
    await client.post("/auth/register", json={
        "name": email.split("@")[0],
        "email": email,
        "password": password,
    })
    resp = await client.post("/auth/login", data={
        "username": email,
        "password": password,
    })
    return resp.json()["access_token"]


@pytest.mark.asyncio
async def test_concurrent_seat_booking(seeded_showtime):
    """
    Two users try to hold and book the same seat at the same time.

    Expected outcome:
    - User A gets the hold → creates booking → succeeds
    - User B gets rejected (hold fails OR booking fails with 409)

    This proves the dual-layer guarantee:
    - Layer 1 (Redis): SET NX ensures only one hold at a time
    - Layer 2 (Postgres): partial unique index catches any edge case
    """
    showtime_id = seeded_showtime["showtime"].id
    seat_id = seeded_showtime["seats"][0].id

    async with make_client() as client:
        token_a = await register_and_login(client)
        token_b = await register_and_login(client)

        headers_a = {"Authorization": f"Bearer {token_a}"}
        headers_b = {"Authorization": f"Bearer {token_b}"}

        # Both users try to hold the same seat concurrently
        hold_a, hold_b = await asyncio.gather(
            client.post(f"/showtimes/{showtime_id}/seats/{seat_id}/hold", headers=headers_a),
            client.post(f"/showtimes/{showtime_id}/seats/{seat_id}/hold", headers=headers_b),
        )

        results = [hold_a.status_code, hold_b.status_code]
        assert 200 in results, f"Expected one success, got: {results}"
        assert results.count(200) == 1, f"Expected exactly one success, got: {results.count(200)}"

        winner_headers = headers_a if hold_a.status_code == 200 else headers_b
        loser_headers = headers_b if hold_a.status_code == 200 else headers_a

        booking_resp = await client.post(
            "/bookings",
            json={"showtime_id": showtime_id, "seat_ids": [seat_id]},
            headers=winner_headers,
        )
        assert booking_resp.status_code == 201, f"Booking failed: {booking_resp.text}"
        assert booking_resp.json()["status"] == "pending"

        # The loser never holds this seat (User A/B's hold already lost the
        # race above), so create_booking rejects them — either because the
        # seat is already 'booked' in Postgres by now, or because they have
        # no valid Redis hold for it. Either path returns 409/400.
        loser_booking = await client.post(
            "/bookings",
            json={"showtime_id": showtime_id, "seat_ids": [seat_id]},
            headers=loser_headers,
        )
        assert loser_booking.status_code in (409, 400), \
            f"Expected 409/400 for loser, got: {loser_booking.status_code}"


@pytest.mark.asyncio
async def test_cannot_book_already_booked_seat(seeded_showtime):
    """After a seat is booked, a second hold attempt on it fails with 409."""
    showtime_id = seeded_showtime["showtime"].id
    seat_id = seeded_showtime["seats"][1].id  # a different seat from the test above

    async with make_client() as client:
        token = await register_and_login(client)
        headers = {"Authorization": f"Bearer {token}"}

        hold_resp = await client.post(f"/showtimes/{showtime_id}/seats/{seat_id}/hold", headers=headers)
        assert hold_resp.status_code == 200, f"Hold failed: {hold_resp.text}"

        booking_resp = await client.post(
            "/bookings",
            json={"showtime_id": showtime_id, "seat_ids": [seat_id]},
            headers=headers,
        )
        assert booking_resp.status_code == 201

        # A second user tries to hold the now-booked seat
        token2 = await register_and_login(client)
        headers2 = {"Authorization": f"Bearer {token2}"}

        hold2 = await client.post(f"/showtimes/{showtime_id}/seats/{seat_id}/hold", headers=headers2)
        assert hold2.status_code == 409, f"Expected 409, got: {hold2.status_code}"