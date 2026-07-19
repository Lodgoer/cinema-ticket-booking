"""
Repository layer — sits between routers and raw SQLAlchemy queries.
A router calls e.g. `CinemaRepository(session).create(...)`, never
`session.execute(select(Cinema)...)` directly. Same idea as the Generic
Repository pattern used with EF Core.
"""
from typing import Generic, TypeVar
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from database import Base
from models import Cinema, Hall, SeatType, Seat, Movie, Showtime

ModelType = TypeVar("ModelType", bound=Base)


class BaseRepository(Generic[ModelType]):
    """Generic CRUD operations shared by every repository.

    Subclasses only need to set `model = <SQLAlchemy class>`.
    """
    model: type[ModelType]

    def __init__(self, session: AsyncSession):
        self.session = session

    async def get(self, id: int) -> ModelType | None:
        return await self.session.get(self.model, id)

    async def get_all(self) -> list[ModelType]:
        result = await self.session.execute(select(self.model))
        return list(result.scalars().all())

    async def create(self, **kwargs) -> ModelType:
        obj = self.model(**kwargs)
        self.session.add(obj)
        await self.session.commit()
        await self.session.refresh(obj)
        return obj

    async def update(self, obj: ModelType, **kwargs) -> ModelType:
        for key, value in kwargs.items():
            if value is not None:
                setattr(obj, key, value)
        await self.session.commit()
        await self.session.refresh(obj)
        return obj

    async def delete(self, obj: ModelType) -> None:
        await self.session.delete(obj)
        await self.session.commit()


class CinemaRepository(BaseRepository[Cinema]):
    model = Cinema


class HallRepository(BaseRepository[Hall]):
    model = Hall

    async def get_by_cinema(self, cinema_id: int) -> list[Hall]:
        result = await self.session.execute(
            select(Hall).where(Hall.cinema_id == cinema_id)
        )
        return list(result.scalars().all())


class SeatTypeRepository(BaseRepository[SeatType]):
    model = SeatType


class SeatRepository(BaseRepository[Seat]):
    model = Seat

    async def get_by_hall(self, hall_id: int) -> list[Seat]:
        result = await self.session.execute(
            select(Seat).where(Seat.hall_id == hall_id)
        )
        return list(result.scalars().all())


class MovieRepository(BaseRepository[Movie]):
    model = Movie


class ShowtimeRepository(BaseRepository[Showtime]):
    model = Showtime

    async def get_by_hall(self, hall_id: int) -> list[Showtime]:
        """Used for the overlap check when creating a new showtime."""
        result = await self.session.execute(
            select(Showtime).where(Showtime.hall_id == hall_id)
        )
        return list(result.scalars().all())
