"""
Auth endpoints: register a new user, log in to get a JWT.
Kept in its own router/file since it's a distinct concern from admin CRUD.
"""
from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.security import OAuth2PasswordRequestForm
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth import hash_password, verify_password, create_access_token
from app.database import get_session
from app.repositories import AppUserRepository
from app.schemas import UserCreate, UserRead, Token

auth_router = APIRouter(prefix="/auth", tags=["auth"])


@auth_router.post("/register", response_model=UserRead, status_code=status.HTTP_201_CREATED)
async def register(data: UserCreate, session: AsyncSession = Depends(get_session)):
    repo = AppUserRepository(session)

    existing = await repo.get_by_email(data.email)
    if existing is not None:
        raise HTTPException(status_code=400, detail="Email already registered")

    user = await repo.create(
        name=data.name,
        email=data.email,
        password_hash=hash_password(data.password),
        role=data.role,
    )
    return user


@auth_router.post("/login", response_model=Token)
async def login(
    form_data: OAuth2PasswordRequestForm = Depends(),
    session: AsyncSession = Depends(get_session),
):
    # OAuth2PasswordRequestForm gives us `.username` and `.password` fields
    # (that's the OAuth2 spec's naming) — we treat `.username` as the email.
    repo = AppUserRepository(session)
    user = await repo.get_by_email(form_data.username)

    if user is None or not verify_password(form_data.password, user.password_hash):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Incorrect email or password",
            headers={"WWW-Authenticate": "Bearer"},
        )

    token = create_access_token(data={"sub": str(user.id)})
    return Token(access_token=token)
