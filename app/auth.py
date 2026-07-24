"""
Authentication and role-based authorization.

Rough ASP.NET Core equivalents:
- verify_password / hash_password  -> Identity's PasswordHasher
- create_access_token              -> what issues the JWT after login
- get_current_user (Depends)       -> [Authorize] — "is there a valid token?"
- require_role(...) (Depends)      -> [Authorize(Roles = "Admin")] — "and does
                                       this user have the right role?"
"""
from datetime import datetime, timedelta, timezone

from fastapi import Depends, HTTPException, status
from fastapi.security import OAuth2PasswordBearer
from jose import JWTError, jwt
from passlib.context import CryptContext
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from database import get_session
from models import AppUser

# In a real deployment this must come from an environment variable, never
# hardcoded — flagged here as a TODO for when .env / pydantic-settings gets
# wired in properly.
SECRET_KEY = "dev-secret-change-me"
ALGORITHM = "HS256"
ACCESS_TOKEN_EXPIRE_MINUTES = 60

pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")

# tokenUrl points at the login endpoint (added to routers.py below) — this is
# only used so Swagger's "Authorize" button knows where to send credentials.
oauth2_scheme = OAuth2PasswordBearer(tokenUrl="/auth/login")


def hash_password(plain_password: str) -> str:
    return pwd_context.hash(plain_password)


def verify_password(plain_password: str, hashed_password: str) -> bool:
    return pwd_context.verify(plain_password, hashed_password)


def create_access_token(data: dict) -> str:
    to_encode = data.copy()
    expire = datetime.now(timezone.utc) + timedelta(minutes=ACCESS_TOKEN_EXPIRE_MINUTES)
    to_encode["exp"] = expire
    return jwt.encode(to_encode, SECRET_KEY, algorithm=ALGORITHM)


async def get_current_user(
    token: str = Depends(oauth2_scheme),
    session: AsyncSession = Depends(get_session),
) -> AppUser:
    credentials_exception = HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Could not validate credentials",
        headers={"WWW-Authenticate": "Bearer"},
    )
    try:
        payload = jwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM])
        user_id: str | None = payload.get("sub")
        if user_id is None:
            raise credentials_exception
    except JWTError:
        raise credentials_exception

    result = await session.execute(select(AppUser).where(AppUser.id == int(user_id)))
    user = result.scalar_one_or_none()
    if user is None:
        raise credentials_exception
    return user


def require_role(*allowed_roles: str):
    """Returns a dependency that only lets the request through if the
    current user's role is one of `allowed_roles`.

    Usage: Depends(require_role("admin")) or Depends(require_role("admin", "theater_manager"))
    """
    async def role_checker(user: AppUser = Depends(get_current_user)) -> AppUser:
        if user.role not in allowed_roles:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=f"This action requires one of these roles: {', '.join(allowed_roles)}",
            )
        return user

    return role_checker
