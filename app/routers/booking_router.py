"""
Booking endpoints — the customer-facing booking + payment + ticket flow.

This ties together:
- Seat holds (from hold_router, in Redis)
- Bookings (in Postgres, with expiry)
- Payments (sandbox provider)
- Tickets (issued after payment)
"""
from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from auth import get_current_user
from database import get_session, async_session
from models import AppUser, Booking, BookingSeat, Ticket, Payment
from schemas import BookingCreate, BookingRead, PaymentCreate, PaymentRead, TicketRead
from booking_service import create_booking, cancel_booking, confirm_payment
from payment_provider import FakePaymentProvider

import uuid

booking_router = APIRouter(prefix="/bookings", tags=["bookings"])

payment_provider = FakePaymentProvider()


@booking_router.post("", response_model=BookingRead, status_code=status.HTTP_201_CREATED)
async def create_booking_endpoint(
    data: BookingCreate,
    user: AppUser = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
):
    """Convert held seats into a pending booking.

    The user must have valid Redis holds for all requested seats.
    The booking gets a 10-minute expiry (matching the Redis TTL).
    """
    try:
        booking = await create_booking(
            session=session,
            user_id=user.id,
            showtime_id=data.showtime_id,
            seat_ids=data.seat_ids,
        )
        return booking
    except ValueError as e:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=str(e),
        )


@booking_router.post("/{booking_id}/cancel", response_model=BookingRead)
async def cancel_booking_endpoint(
    booking_id: int,
    user: AppUser = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
):
    """Cancel a pending booking, releasing held seats.

    Uses SELECT ... FOR UPDATE to prevent race conditions on concurrent
    cancel requests for the same booking.
    """
    try:
        booking = await cancel_booking(
            session=session,
            booking_id=booking_id,
            user_id=user.id,
        )
        return booking
    except ValueError as e:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=str(e),
        )


@booking_router.get("/{booking_id}", response_model=BookingRead)
async def get_booking_endpoint(
    booking_id: int,
    user: AppUser = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
):
    """Get booking details including seat assignments and status."""
    from sqlalchemy import select
    result = await session.execute(
        select(Booking).where(
            Booking.id == booking_id,
            Booking.user_id == user.id,
        )
    )
    booking = result.scalar_one_or_none()
    if booking is None:
        raise HTTPException(status_code=404, detail="Booking not found")
    return booking


@booking_router.post("/pay", response_model=PaymentRead, status_code=status.HTTP_201_CREATED)
async def pay_for_booking(
    data: PaymentCreate,
    user: AppUser = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
):
    """Process payment for a pending booking.

    The idempotency_key prevents double-charging if the client retries.
    Payment is processed synchronously via the sandbox provider, then
    the booking is confirmed and tickets are issued.
    """
    # Check idempotency FIRST — if this key was already used, return
    # the existing payment regardless of booking status
    existing = await session.execute(
        select(Payment).where(Payment.idempotency_key == data.idempotency_key)
    )
    existing_payment = existing.scalar_one_or_none()
    if existing_payment:
        return existing_payment  # idempotent return

    # Verify booking belongs to user and is payable
    result = await session.execute(
        select(Booking).where(
            Booking.id == data.booking_id,
            Booking.user_id == user.id,
        )
    )
    booking = result.scalar_one_or_none()
    if booking is None:
        raise HTTPException(status_code=404, detail="Booking not found")
    if booking.status != "pending":
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"Booking is {booking.status}, cannot process payment",
        )

    # Create payment record
    payment = Payment(
        booking_id=booking.id,
        amount=booking.total_price,
        status="pending",
        idempotency_key=data.idempotency_key,
    )
    session.add(payment)
    await session.flush()

    # Call sandbox provider
    provider_result = await payment_provider.charge(
        amount=float(booking.total_price),
        idempotency_key=data.idempotency_key,
    )

    if provider_result["success"]:
        payment.status = "succeeded"
        payment.provider_ref = provider_result["provider_ref"]
        await session.commit()

        # Confirm booking and issue tickets
        try:
            await confirm_payment(session=session, booking_id=booking.id)
        except ValueError as e:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=str(e),
            )

        await session.refresh(payment)
        return payment
    else:
        payment.status = "failed"
        payment.provider_ref = provider_result.get("provider_ref")
        await session.commit()
        await session.refresh(payment)

        raise HTTPException(
            status_code=status.HTTP_402_PAYMENT_REQUIRED,
            detail=provider_result.get("error", "Payment failed"),
        )


@booking_router.get("/{booking_id}/tickets", response_model=list[TicketRead])
async def get_booking_tickets(
    booking_id: int,
    user: AppUser = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
):
    """Get tickets for a confirmed booking."""
    # Verify booking belongs to user and is confirmed
    result = await session.execute(
        select(Booking).where(
            Booking.id == booking_id,
            Booking.user_id == user.id,
        )
    )
    booking = result.scalar_one_or_none()
    if booking is None:
        raise HTTPException(status_code=404, detail="Booking not found")
    if booking.status != "confirmed":
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Booking is {booking.status}, tickets not yet available",
        )

    # Get tickets via booking seats
    result = await session.execute(
        select(Ticket).join(BookingSeat).where(
            BookingSeat.booking_id == booking_id,
            BookingSeat.status == "active",
        )
    )
    return list(result.scalars().all())
