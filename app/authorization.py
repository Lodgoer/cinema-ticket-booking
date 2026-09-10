"""
Shared ownership checks for theater_manager-scoped resources.

A theater_manager may only mutate cinemas they're linked to via
CinemaManager. An admin bypasses this check entirely — admin means
"can touch anything", theater_manager means "can touch what I manage".
"""
from fastapi import HTTPException, status, Depends
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import AppUser, CinemaManager, Hall, Seat, Showtime
from app.auth import get_current_user
from app.database import get_session


async def check_cinema_access(
    user: AppUser,
    cinema_id: int,
    session: AsyncSession,
) -> None:
    if user.role == "admin":
        return

    result = await session.execute(
        select(CinemaManager).where(
            CinemaManager.user_id == user.id,
            CinemaManager.cinema_id == cinema_id,
        )
    )
    if result.scalar_one_or_none() is None:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="You don't manage this cinema",
        )


async def require_cinema_owner(
    cinema_id: int,
    user: AppUser = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
) -> None:
    """
    FastAPI dependency: use on any endpoint whose path includes
    {cinema_id}. FastAPI reads cinema_id straight from the URL and
    passes it here automatically — same as it does for the endpoint
    function itself.
    """
    await check_cinema_access(user, cinema_id, session)


async def require_hall_owner(
    hall_id: int,
    user: AppUser = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
) -> Hall:
    """
    FastAPI dependency: use on any endpoint whose path includes
    {hall_id}. Unlike require_cinema_owner, there's no cinema_id in the
    URL here — so this looks the Hall up first to find its cinema_id,
    then checks ownership.

    Returns the Hall object itself (not just None) so the endpoint can
    receive it directly via Depends(require_hall_owner) instead of
    querying for it a second time.
    """
    result = await session.execute(select(Hall).where(Hall.id == hall_id))
    hall = result.scalar_one_or_none()
    if hall is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Hall not found")

    await check_cinema_access(user, hall.cinema_id, session)
    return hall


async def _cinema_id_for_hall(hall_id: int, session: AsyncSession) -> int:
    """Shared lookup: resolve a hall_id to its cinema_id, or 404."""
    result = await session.execute(select(Hall).where(Hall.id == hall_id))
    hall = result.scalar_one_or_none()
    if hall is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Hall not found")
    return hall.cinema_id


async def require_seat_owner(
    seat_id: int,
    user: AppUser = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
) -> Seat:
    result = await session.execute(select(Seat).where(Seat.id == seat_id))
    seat = result.scalar_one_or_none()
    if seat is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Seat not found")

    cinema_id = await _cinema_id_for_hall(seat.hall_id, session)
    await check_cinema_access(user, cinema_id, session)
    return seat


async def require_showtime_owner(
    showtime_id: int,
    user: AppUser = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
) -> Showtime:
    result = await session.execute(select(Showtime).where(Showtime.id == showtime_id))
    showtime = result.scalar_one_or_none()
    if showtime is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Showtime not found")

    cinema_id = await _cinema_id_for_hall(showtime.hall_id, session)
    await check_cinema_access(user, cinema_id, session)
    return showtime


async def get_managed_cinema_ids(
    session: AsyncSession,
    user: AppUser,
) -> list[int] | None:
    """Return the cinema IDs managed by this user.

    Returns None for admins (meaning: no filter, see everything).
    Returns a list of cinema IDs for theater_managers.
    """
    if user.role == "admin":
        return None

    result = await session.execute(
        select(CinemaManager.cinema_id).where(CinemaManager.user_id == user.id)
    )
    return [row[0] for row in result.all()]