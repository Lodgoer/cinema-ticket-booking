from datetime import datetime
from sqlalchemy import BigInteger, DateTime
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker, AsyncSession
from sqlalchemy.orm import DeclarativeBase

DATABASE_URL = "postgresql+asyncpg://postgres:mysecret@localhost:5432/cinema_db"

engine = create_async_engine(DATABASE_URL, echo=True)

async_session = async_sessionmaker(engine, expire_on_commit=False)


class Base(DeclarativeBase):
    """Every model class inherits from this, same idea as DbContext's model base.

    type_annotation_map tells SQLAlchemy which SQL type to use by default for
    each Python type, so every `Mapped[int]` becomes BIGINT (matching schema.sql's
    BIGSERIAL) and every `Mapped[datetime]` becomes a timezone-aware TIMESTAMPTZ,
    without having to repeat mapped_column(BigInteger) on every single column.
    """
    type_annotation_map = {
        int: BigInteger,
        datetime: DateTime(timezone=True),
    }


async def get_session() -> AsyncSession:
    async with async_session() as session:
        yield session
