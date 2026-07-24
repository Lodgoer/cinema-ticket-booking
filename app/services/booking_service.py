"""
Booking service — the core business logic that ties holds, bookings,
payments, and tickets together.

Design notes (for interview defense):
- create_booking: converts Redis-held seats into a pending booking in Postgres.
  The transaction inserts booking_seat rows and flips showtime_seat status to
  'booked' atomically. If two users race on the same seat, the partial unique
  index (uq_active_booking_seat) ensures only one wins; the other gets a
  clean 409 instead of a raw 500.
- cancel_booking: uses SELECT ... FOR UPDATE to lock the booking row, making
  concurrent cancel requests for the same booking serializable.
- confirm_payment: flips booking to 'confirmed' and issues tickets.
- sweep_expired: periodic task (via arq) that finds bookings past expires_at
  and cancels them. This is the "sweep" side of the hold cleanup strategy;
  the "lazy check" improvement is documented but deferred.
"""
from datetime import datetime, timedelta, timezone
from decimal import Decimal

from sqlalchemy import select, update, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.models import (
    Booking, BookingSeat, ShowtimeSeat, Ticket, Payment,
)

BOOKING_HOLD_MINUTES = 10  # matches Redis TTL


async def create_booking(
    session: AsyncSession,
    user_id: int,
    showtime_id: int,
    seat_ids: list[int],
) -> Booking:
    """Convert held seats into a pending booking.

    Flow:
    1. Look up ShowtimeSeat rows for the requested seats
    2. Insert Booking + BookingSeat rows (status='active')
    3. Flip ShowtimeSeat.status to 'booked'
    4. If any step fails (e.g. partial unique index violation), rollback

    The key guarantee: the partial unique index on booking_seat
    (uq_active_booking_seat WHERE status = 'active') makes it structurally
    impossible for two active bookings to claim the same seat — even if Redis
    holds overlap or expire mid-transaction.
    """

    # Look up the showtime seats
    result = await session.execute(
        select(ShowtimeSeat).where(
            ShowtimeSeat.showtime_id == showtime_id,
            ShowtimeSeat.seat_id.in_(seat_ids),
        )
    )
    showtime_seats = list(result.scalars().all())

    if len(showtime_seats) != len(seat_ids):
        found_ids = {ss.seat_id for ss in showtime_seats}
        missing = [sid for sid in seat_ids if sid not in found_ids]
        raise ValueError(f"Seats not found for this showtime: {missing}")

    # Check none are already booked
    already_booked = [ss for ss in showtime_seats if ss.status == "booked"]
    if already_booked:
        booked_ids = [ss.seat_id for ss in already_booked]
        raise ValueError(f"Seats already booked: {booked_ids}")

    # Calculate total price from price_snapshots
    total_price = sum(ss.price_snapshot for ss in showtime_seats)

    # Create booking
    now = datetime.now(timezone.utc)
    booking = Booking(
        user_id=user_id,
        status="pending",
        total_price=total_price,
        expires_at=now + timedelta(minutes=BOOKING_HOLD_MINUTES),
    )
    session.add(booking)
    await session.flush()  # get booking.id

    # Create booking_seat rows and flip showtime_seat status
    for ss in showtime_seats:
        session.add(
            BookingSeat(
                booking_id=booking.id,
                showtime_seat_id=ss.id,
                status="active",
            )
        )
        ss.status = "booked"

    try:
        await session.commit()
    except IntegrityError:
        await session.rollback()
        raise ValueError("One or more seats were just taken by another customer")

    # Re-query with eager loading for the relationship
    result = await session.execute(
        select(Booking)
        .where(Booking.id == booking.id)
        .options(selectinload(Booking.booking_seats))
    )
    return result.scalar_one()


async def cancel_booking(
    session: AsyncSession,
    booking_id: int,
    user_id: int,
) -> Booking:
    """Cancel a pending or confirmed booking.

    Uses SELECT ... FOR UPDATE to lock the booking row, preventing two
    concurrent cancel requests from both succeeding (the second one would
    see status='cancelled' after the first commits, and skip).
    """
    result = await session.execute(
        select(Booking)
        .where(Booking.id == booking_id, Booking.user_id == user_id)
        .with_for_update()
    )
    booking = result.scalar_one_or_none()

    if booking is None:
        raise ValueError("Booking not found")
    if booking.status in ("cancelled", "expired"):
        return booking  # idempotent — already done

    # Deactivate booking seats
    await session.execute(
        update(BookingSeat)
        .where(
            BookingSeat.booking_id == booking_id,
            BookingSeat.status == "active",
        )
        .values(status="cancelled")
    )

    # Release showtime seats back to available
    await session.execute(
        update(ShowtimeSeat)
        .where(
            ShowtimeSeat.id.in_(
                select(BookingSeat.showtime_seat_id).where(
                    BookingSeat.booking_id == booking_id,
                    BookingSeat.status == "cancelled",
                )
            )
        )
        .values(status="available")
    )

    booking.status = "cancelled"
    await session.commit()

    # Re-query with eager loading
    result = await session.execute(
        select(Booking)
        .where(Booking.id == booking.id)
        .options(selectinload(Booking.booking_seats))
    )
    return result.scalar_one()


async def confirm_payment(
    session: AsyncSession,
    booking_id: int,
) -> Booking:
    """Mark booking as confirmed after successful payment.

    Issues a ticket (with QR code) for each active seat in the booking.
    """
    result = await session.execute(
        select(Booking).where(Booking.id == booking_id)
    )
    booking = result.scalar_one_or_none()

    if booking is None:
        raise ValueError("Booking not found")
    if booking.status != "pending":
        raise ValueError(f"Booking is {booking.status}, cannot confirm")

    # Get active booking seats
    result = await session.execute(
        select(BookingSeat).where(
            BookingSeat.booking_id == booking_id,
            BookingSeat.status == "active",
        )
    )
    active_seats = list(result.scalars().all())

    if not active_seats:
        raise ValueError("No active seats in this booking")

    # Issue tickets
    import uuid
    for bs in active_seats:
        ticket = Ticket(
            booking_seat_id=bs.id,
            qr_code=f"TKT-{uuid.uuid4().hex[:12].upper()}",
        )
        session.add(ticket)

    booking.status = "confirmed"
    await session.commit()

    # Re-query with eager loading
    result = await session.execute(
        select(Booking)
        .where(Booking.id == booking.id)
        .options(selectinload(Booking.booking_seats))
    )
    return result.scalar_one()


async def sweep_expired_bookings(session: AsyncSession) -> int:
    """Periodic task: cancel bookings past their expires_at.

    Called by the arq background worker every ~1 minute.
    Returns the number of bookings cancelled.
    """
    now = datetime.now(timezone.utc)

    # Find expired pending bookings
    result = await session.execute(
        select(Booking).where(
            Booking.status == "pending",
            Booking.expires_at < now,
        )
    )
    expired = list(result.scalars().all())

    for booking in expired:
        # Release seats
        await session.execute(
            update(BookingSeat)
            .where(
                BookingSeat.booking_id == booking.id,
                BookingSeat.status == "active",
            )
            .values(status="cancelled")
        )

        await session.execute(
            update(ShowtimeSeat)
            .where(
                ShowtimeSeat.id.in_(
                    select(BookingSeat.showtime_seat_id).where(
                        BookingSeat.booking_id == booking.id,
                        BookingSeat.status == "cancelled",
                    )
                )
            )
            .values(status="available")
        )

        booking.status = "expired"

    await session.commit()
    return len(expired)
