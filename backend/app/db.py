import asyncio
import logging

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from .config import settings
from .models import Base

logger = logging.getLogger(__name__)

engine = create_async_engine(settings.async_database_url, echo=False)
async_session = async_sessionmaker(engine, expire_on_commit=False)


async def init_db(retries: int = 5, delay: float = 3.0) -> None:
    for attempt in range(1, retries + 1):
        try:
            async with engine.begin() as conn:
                await conn.run_sync(Base.metadata.create_all)
                # Incremental migrations — safe to run on every startup
                dialect = conn.dialect.name
                if dialect == "postgresql":
                    await conn.execute(text(
                        "ALTER TABLE bookings ADD COLUMN IF NOT EXISTS "
                        "stripe_session_id VARCHAR(255)"
                    ))
                    await conn.execute(text(
                        "ALTER TABLE bookings ALTER COLUMN pin_code DROP NOT NULL"
                    ))
                    # Index for fast session lookup (ignore if already exists)
                    await conn.execute(text(
                        "CREATE UNIQUE INDEX IF NOT EXISTS ix_bookings_stripe_session_id "
                        "ON bookings (stripe_session_id) WHERE stripe_session_id IS NOT NULL"
                    ))
            logger.info("Database ready.")
            return
        except Exception as exc:
            logger.warning("DB not ready (attempt %d/%d): %s", attempt, retries, exc)
            if attempt == retries:
                raise
            await asyncio.sleep(delay)


async def get_session():
    async with async_session() as session:
        yield session
