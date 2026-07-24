from datetime import datetime
from decimal import Decimal
from sqlalchemy import String, Numeric, Integer, DateTime, ForeignKey, UniqueConstraint, Index, CheckConstraint, text
from sqlalchemy.orm import Mapped, mapped_column, relationship
from app.database import Base


class Cinema(Base):
    __tablename__ = "cinema"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(255))
    city: Mapped[str] = mapped_column(String(100))
    address: Mapped[str | None] = mapped_column(String(500))
    created_at: Mapped[datetime] = mapped_column(server_default=text("now()"))

    halls: Mapped[list["Hall"]] = relationship(back_populates="cinema")


class Hall(Base):
    __tablename__ = "hall"
    __table_args__ = (UniqueConstraint("cinema_id", "name"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    cinema_id: Mapped[int] = mapped_column(ForeignKey("cinema.id", ondelete="CASCADE"))
    name: Mapped[str] = mapped_column(String(100))
    capacity: Mapped[int] = mapped_column(Integer)

    cinema: Mapped["Cinema"] = relationship(back_populates="halls")
    seats: Mapped[list["Seat"]] = relationship(back_populates="hall")


class SeatType(Base):
    __tablename__ = "seat_type"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(50), unique=True)
    price: Mapped[Decimal] = mapped_column(Numeric(10, 2))


class Seat(Base):
    __tablename__ = "seat"
    __table_args__ = (UniqueConstraint("hall_id", "row_label", "seat_number"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    hall_id: Mapped[int] = mapped_column(ForeignKey("hall.id", ondelete="CASCADE"))
    row_label: Mapped[str] = mapped_column(String(5))
    seat_number: Mapped[int] = mapped_column(Integer)
    seat_type_id: Mapped[int] = mapped_column(ForeignKey("seat_type.id"))

    hall: Mapped["Hall"] = relationship(back_populates="seats")
    seat_type: Mapped["SeatType"] = relationship()


class Movie(Base):
    __tablename__ = "movie"

    id: Mapped[int] = mapped_column(primary_key=True)
    title: Mapped[str] = mapped_column(String(255))
    duration_minutes: Mapped[int] = mapped_column(Integer)
    genre: Mapped[str | None] = mapped_column(String(100))
    release_year: Mapped[int | None] = mapped_column(Integer)


class Showtime(Base):
    __tablename__ = "showtime"
    __table_args__ = (
        Index("idx_showtime_hall_start", "hall_id", "starts_at"),
        CheckConstraint("ends_at > starts_at"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    movie_id: Mapped[int] = mapped_column(ForeignKey("movie.id"))
    hall_id: Mapped[int] = mapped_column(ForeignKey("hall.id"))
    starts_at: Mapped[datetime]
    ends_at: Mapped[datetime]

    movie: Mapped["Movie"] = relationship()
    hall: Mapped["Hall"] = relationship()


class AppUser(Base):
    __tablename__ = "app_user"
    __table_args__ = (
        CheckConstraint("role IN ('customer', 'theater_manager', 'admin')"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(255))
    email: Mapped[str] = mapped_column(String(255), unique=True)
    password_hash: Mapped[str] = mapped_column(String(255))
    role: Mapped[str] = mapped_column(String(20))
    created_at: Mapped[datetime] = mapped_column(server_default=text("now()"))


class CinemaManager(Base):
    __tablename__ = "cinema_manager"
    __table_args__ = (UniqueConstraint("user_id", "cinema_id"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("app_user.id", ondelete="CASCADE"))
    cinema_id: Mapped[int] = mapped_column(ForeignKey("cinema.id", ondelete="CASCADE"))

    user: Mapped["AppUser"] = relationship()
    cinema: Mapped["Cinema"] = relationship()


class ShowtimeSeat(Base):
    __tablename__ = "showtime_seat"
    __table_args__ = (
        UniqueConstraint("showtime_id", "seat_id"),
        Index("idx_showtime_seat_showtime", "showtime_id"),
        CheckConstraint("status IN ('available', 'booked')"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    showtime_id: Mapped[int] = mapped_column(ForeignKey("showtime.id", ondelete="CASCADE"))
    seat_id: Mapped[int] = mapped_column(ForeignKey("seat.id"))
    status: Mapped[str] = mapped_column(String(20), server_default="available")
    price_snapshot: Mapped[Decimal] = mapped_column(Numeric(10, 2))

    showtime: Mapped["Showtime"] = relationship()
    seat: Mapped["Seat"] = relationship()


class Discount(Base):
    __tablename__ = "discount"
    __table_args__ = (CheckConstraint("valid_to > valid_from"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    code: Mapped[str] = mapped_column(String(50), unique=True)
    type: Mapped[str] = mapped_column(String(20))
    value: Mapped[Decimal] = mapped_column(Numeric(10, 2))
    valid_from: Mapped[datetime]
    valid_to: Mapped[datetime]
    max_uses: Mapped[int | None] = mapped_column(Integer)
    used_count: Mapped[int] = mapped_column(Integer, server_default="0")


class Booking(Base):
    __tablename__ = "booking"
    __table_args__ = (
        Index("idx_booking_user", "user_id"),
        CheckConstraint("status IN ('pending', 'confirmed', 'cancelled', 'expired')"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("app_user.id"))
    discount_id: Mapped[int | None] = mapped_column(ForeignKey("discount.id"))
    status: Mapped[str] = mapped_column(String(20), server_default="pending")
    total_price: Mapped[Decimal] = mapped_column(Numeric(10, 2))
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(server_default=text("now()"))

    user: Mapped["AppUser"] = relationship()
    discount: Mapped["Discount | None"] = relationship()
    booking_seats: Mapped[list["BookingSeat"]] = relationship(back_populates="booking")


class BookingSeat(Base):
    __tablename__ = "booking_seat"
    __table_args__ = (
        Index("idx_booking_seat_booking", "booking_id"),
        CheckConstraint("status IN ('active', 'cancelled')"),
        Index(
            "uq_active_booking_seat",
            "showtime_seat_id",
            unique=True,
            postgresql_where=text("status = 'active'"),
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    booking_id: Mapped[int] = mapped_column(ForeignKey("booking.id", ondelete="CASCADE"))
    showtime_seat_id: Mapped[int] = mapped_column(ForeignKey("showtime_seat.id"))
    status: Mapped[str] = mapped_column(String(20), server_default="active")

    booking: Mapped["Booking"] = relationship(back_populates="booking_seats")
    showtime_seat: Mapped["ShowtimeSeat"] = relationship()


class Payment(Base):
    __tablename__ = "payment"
    __table_args__ = (
        Index("idx_payment_booking", "booking_id"),
        CheckConstraint("status IN ('pending', 'processing', 'succeeded', 'failed', 'refunded')"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    booking_id: Mapped[int] = mapped_column(ForeignKey("booking.id", ondelete="CASCADE"))
    amount: Mapped[Decimal] = mapped_column(Numeric(10, 2))
    status: Mapped[str] = mapped_column(String(20), server_default="pending")
    idempotency_key: Mapped[str] = mapped_column(String(255), unique=True)
    provider_ref: Mapped[str | None] = mapped_column(String(255))
    created_at: Mapped[datetime] = mapped_column(server_default=text("now()"))

    booking: Mapped["Booking"] = relationship()


class Ticket(Base):
    __tablename__ = "ticket"

    id: Mapped[int] = mapped_column(primary_key=True)
    booking_seat_id: Mapped[int] = mapped_column(
        ForeignKey("booking_seat.id", ondelete="CASCADE"), unique=True
    )
    qr_code: Mapped[str] = mapped_column(String(255), unique=True)
    issued_at: Mapped[datetime] = mapped_column(server_default=text("now()"))

    booking_seat: Mapped["BookingSeat"] = relationship()
