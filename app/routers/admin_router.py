"""
Admin CRUD routers. Each router is the FastAPI equivalent of an
ASP.NET Core Controller: groups related endpoints, injects a Session
via Depends() (same idea as constructor-injecting a DbContext).
"""
from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from database import get_session
from auth import require_role
from models import Showtime, ShowtimeSeat
from repositories import (
    CinemaRepository, HallRepository, SeatTypeRepository,
    SeatRepository, MovieRepository, ShowtimeRepository,
)
from schemas import (
    CinemaCreate, CinemaRead, CinemaUpdate,
    HallCreate, HallRead, HallUpdate,
    SeatTypeCreate, SeatTypeRead,
    SeatCreate, SeatRead,
    MovieCreate, MovieRead, MovieUpdate,
    ShowtimeCreate, ShowtimeRead,
)

router = APIRouter(
    prefix="/admin",
    tags=["admin"],
    dependencies=[Depends(require_role("admin", "theater_manager"))],
)


# ---------- Cinema ----------

@router.post("/cinemas", response_model=CinemaRead, status_code=status.HTTP_201_CREATED)
async def create_cinema(data: CinemaCreate, session: AsyncSession = Depends(get_session)):
    return await CinemaRepository(session).create(**data.model_dump())


@router.get("/cinemas", response_model=list[CinemaRead])
async def list_cinemas(session: AsyncSession = Depends(get_session)):
    return await CinemaRepository(session).get_all()


@router.get("/cinemas/{cinema_id}", response_model=CinemaRead)
async def get_cinema(cinema_id: int, session: AsyncSession = Depends(get_session)):
    cinema = await CinemaRepository(session).get(cinema_id)
    if cinema is None:
        raise HTTPException(status_code=404, detail="Cinema not found")
    return cinema


@router.patch("/cinemas/{cinema_id}", response_model=CinemaRead)
async def update_cinema(cinema_id: int, data: CinemaUpdate, session: AsyncSession = Depends(get_session)):
    repo = CinemaRepository(session)
    cinema = await repo.get(cinema_id)
    if cinema is None:
        raise HTTPException(status_code=404, detail="Cinema not found")
    return await repo.update(cinema, **data.model_dump())


@router.delete("/cinemas/{cinema_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_cinema(cinema_id: int, session: AsyncSession = Depends(get_session)):
    repo = CinemaRepository(session)
    cinema = await repo.get(cinema_id)
    if cinema is None:
        raise HTTPException(status_code=404, detail="Cinema not found")
    await repo.delete(cinema)


# ---------- Hall ----------

@router.post("/halls", response_model=HallRead, status_code=status.HTTP_201_CREATED)
async def create_hall(data: HallCreate, session: AsyncSession = Depends(get_session)):
    return await HallRepository(session).create(**data.model_dump())


@router.get("/cinemas/{cinema_id}/halls", response_model=list[HallRead])
async def list_halls_for_cinema(cinema_id: int, session: AsyncSession = Depends(get_session)):
    return await HallRepository(session).get_by_cinema(cinema_id)


@router.get("/halls/{hall_id}", response_model=HallRead)
async def get_hall(hall_id: int, session: AsyncSession = Depends(get_session)):
    hall = await HallRepository(session).get(hall_id)
    if hall is None:
        raise HTTPException(status_code=404, detail="Hall not found")
    return hall


@router.patch("/halls/{hall_id}", response_model=HallRead)
async def update_hall(hall_id: int, data: HallUpdate, session: AsyncSession = Depends(get_session)):
    repo = HallRepository(session)
    hall = await repo.get(hall_id)
    if hall is None:
        raise HTTPException(status_code=404, detail="Hall not found")
    return await repo.update(hall, **data.model_dump())


@router.delete("/halls/{hall_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_hall(hall_id: int, session: AsyncSession = Depends(get_session)):
    repo = HallRepository(session)
    hall = await repo.get(hall_id)
    if hall is None:
        raise HTTPException(status_code=404, detail="Hall not found")
    await repo.delete(hall)


# ---------- Seat type ----------

@router.post("/seat-types", response_model=SeatTypeRead, status_code=status.HTTP_201_CREATED)
async def create_seat_type(data: SeatTypeCreate, session: AsyncSession = Depends(get_session)):
    return await SeatTypeRepository(session).create(**data.model_dump())


@router.get("/seat-types", response_model=list[SeatTypeRead])
async def list_seat_types(session: AsyncSession = Depends(get_session)):
    return await SeatTypeRepository(session).get_all()


# ---------- Seat (physical layout of a hall) ----------

@router.post("/seats", response_model=SeatRead, status_code=status.HTTP_201_CREATED)
async def create_seat(data: SeatCreate, session: AsyncSession = Depends(get_session)):
    return await SeatRepository(session).create(**data.model_dump())


@router.get("/halls/{hall_id}/seats", response_model=list[SeatRead])
async def list_seats_for_hall(hall_id: int, session: AsyncSession = Depends(get_session)):
    return await SeatRepository(session).get_by_hall(hall_id)


@router.delete("/seats/{seat_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_seat(seat_id: int, session: AsyncSession = Depends(get_session)):
    repo = SeatRepository(session)
    seat = await repo.get(seat_id)
    if seat is None:
        raise HTTPException(status_code=404, detail="Seat not found")
    await repo.delete(seat)


# ---------- Movie ----------

@router.post("/movies", response_model=MovieRead, status_code=status.HTTP_201_CREATED)
async def create_movie(data: MovieCreate, session: AsyncSession = Depends(get_session)):
    return await MovieRepository(session).create(**data.model_dump())


@router.get("/movies", response_model=list[MovieRead])
async def list_movies(session: AsyncSession = Depends(get_session)):
    return await MovieRepository(session).get_all()


@router.get("/movies/{movie_id}", response_model=MovieRead)
async def get_movie(movie_id: int, session: AsyncSession = Depends(get_session)):
    movie = await MovieRepository(session).get(movie_id)
    if movie is None:
        raise HTTPException(status_code=404, detail="Movie not found")
    return movie


@router.patch("/movies/{movie_id}", response_model=MovieRead)
async def update_movie(movie_id: int, data: MovieUpdate, session: AsyncSession = Depends(get_session)):
    repo = MovieRepository(session)
    movie = await repo.get(movie_id)
    if movie is None:
        raise HTTPException(status_code=404, detail="Movie not found")
    return await repo.update(movie, **data.model_dump())


@router.delete("/movies/{movie_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_movie(movie_id: int, session: AsyncSession = Depends(get_session)):
    repo = MovieRepository(session)
    movie = await repo.get(movie_id)
    if movie is None:
        raise HTTPException(status_code=404, detail="Movie not found")
    await repo.delete(movie)


# ---------- Showtime ----------
#
# Two things happen here beyond plain CRUD, both tied to decisions made
# in schema.sql:
#
# 1. Overlap check: the database itself refuses two overlapping showtimes
#    in the same hall (the `no_overlapping_showtimes` exclusion constraint
#    added today). We don't re-implement that check in Python — instead we
#    let Postgres reject it and translate the low-level IntegrityError into
#    a clean 409 Conflict for the API caller. Checking only in application
#    code would leave a race condition between two concurrent admins; the
#    DB constraint is the real guarantee, the friendly error is just UX.
#
# 2. Seat map generation: the moment a showtime is created, we create one
#    ShowtimeSeat row per physical Seat in that hall, all starting out
#    'available'. This is what makes the showtime immediately bookable.

@router.post("/showtimes", response_model=ShowtimeRead, status_code=status.HTTP_201_CREATED)
async def create_showtime(data: ShowtimeCreate, session: AsyncSession = Depends(get_session)):
    showtime_repo = ShowtimeRepository(session)
    seat_repo = SeatRepository(session)

    showtime = Showtime(**data.model_dump())
    session.add(showtime)
    try:
        await session.flush()  # sends the INSERT so the exclusion constraint
                                # gets checked now, without committing yet
    except IntegrityError:
        await session.rollback()
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="This hall already has an overlapping showtime at that time.",
        )

    seats = await seat_repo.get_by_hall(data.hall_id)
    seat_type_repo = SeatTypeRepository(session)
    seat_type_prices: dict[int, object] = {}

    for seat in seats:
        if seat.seat_type_id not in seat_type_prices:
            seat_type = await seat_type_repo.get(seat.seat_type_id)
            seat_type_prices[seat.seat_type_id] = seat_type.price

        session.add(
            ShowtimeSeat(
                showtime_id=showtime.id,
                seat_id=seat.id,
                status="available",
                price_snapshot=seat_type_prices[seat.seat_type_id],
            )
        )

    await session.commit()
    await session.refresh(showtime)
    return showtime


@router.get("/showtimes", response_model=list[ShowtimeRead])
async def list_showtimes(session: AsyncSession = Depends(get_session)):
    return await ShowtimeRepository(session).get_all()


@router.get("/showtimes/{showtime_id}", response_model=ShowtimeRead)
async def get_showtime(showtime_id: int, session: AsyncSession = Depends(get_session)):
    showtime = await ShowtimeRepository(session).get(showtime_id)
    if showtime is None:
        raise HTTPException(status_code=404, detail="Showtime not found")
    return showtime


@router.delete("/showtimes/{showtime_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_showtime(showtime_id: int, session: AsyncSession = Depends(get_session)):
    repo = ShowtimeRepository(session)
    showtime = await repo.get(showtime_id)
    if showtime is None:
        raise HTTPException(status_code=404, detail="Showtime not found")
    await repo.delete(showtime)
