"""
Pydantic schemas — the API's input/output "shape", separate from the
SQLAlchemy models (which are the database's shape). Same idea as DTOs /
ViewModels in ASP.NET Core: a client should never be able to set `id` or
`created_at` themselves, and a Read response can include fields a Create
request never provides.
"""
from datetime import datetime
from decimal import Decimal
from pydantic import BaseModel, ConfigDict


# ---------- Cinema ----------

class CinemaBase(BaseModel):
    name: str
    city: str
    address: str | None = None


class CinemaCreate(CinemaBase):
    pass


class CinemaUpdate(BaseModel):
    name: str | None = None
    city: str | None = None
    address: str | None = None


class CinemaRead(CinemaBase):
    model_config = ConfigDict(from_attributes=True)  # lets Pydantic read straight
                                                       # from a SQLAlchemy object,
                                                       # not just from a dict
    id: int
    created_at: datetime


# ---------- Hall ----------

class HallBase(BaseModel):
    name: str
    capacity: int


class HallCreate(HallBase):
    cinema_id: int


class HallUpdate(BaseModel):
    name: str | None = None
    capacity: int | None = None


class HallRead(HallBase):
    model_config = ConfigDict(from_attributes=True)
    id: int
    cinema_id: int


# ---------- SeatType ----------

class SeatTypeBase(BaseModel):
    name: str
    price: Decimal


class SeatTypeCreate(SeatTypeBase):
    pass


class SeatTypeRead(SeatTypeBase):
    model_config = ConfigDict(from_attributes=True)
    id: int


# ---------- Seat ----------

class SeatBase(BaseModel):
    row_label: str
    seat_number: int
    seat_type_id: int


class SeatCreate(SeatBase):
    hall_id: int


class SeatRead(SeatBase):
    model_config = ConfigDict(from_attributes=True)
    id: int
    hall_id: int


# ---------- Movie ----------

class MovieBase(BaseModel):
    title: str
    duration_minutes: int
    genre: str | None = None
    release_year: int | None = None


class MovieCreate(MovieBase):
    pass


class MovieUpdate(BaseModel):
    title: str | None = None
    duration_minutes: int | None = None
    genre: str | None = None
    release_year: int | None = None


class MovieRead(MovieBase):
    model_config = ConfigDict(from_attributes=True)
    id: int


# ---------- Showtime ----------

class ShowtimeBase(BaseModel):
    movie_id: int
    hall_id: int
    starts_at: datetime
    ends_at: datetime


class ShowtimeCreate(ShowtimeBase):
    pass


class ShowtimeRead(ShowtimeBase):
    model_config = ConfigDict(from_attributes=True)
    id: int
