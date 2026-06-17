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
            # Step 1: create tables
            async with engine.begin() as conn:
                await conn.run_sync(Base.metadata.create_all)
                is_pg = conn.dialect.name == "postgresql"
            logger.info("Tables OK.")

            # Steps 2-4: incremental column migrations, each in its own tx
            if is_pg:
                migrations = [
                    "ALTER TABLE bookings ADD COLUMN IF NOT EXISTS stripe_session_id VARCHAR(255)",
                    "ALTER TABLE bookings ALTER COLUMN pin_code DROP NOT NULL",
                    (
                        "CREATE UNIQUE INDEX IF NOT EXISTS ix_bookings_stripe_session_id "
                        "ON bookings (stripe_session_id) WHERE stripe_session_id IS NOT NULL"
                    ),
                    "ALTER TABLE users ADD COLUMN IF NOT EXISTS iban VARCHAR(34)",
                    "ALTER TABLE bookings ADD COLUMN IF NOT EXISTS paid_out BOOLEAN DEFAULT FALSE",
                    "ALTER TABLE bookings ADD COLUMN IF NOT EXISTS paid_out_at TIMESTAMP",
                    "ALTER TABLE listings ADD COLUMN IF NOT EXISTS availability_json TEXT",
                    """CREATE TABLE IF NOT EXISTS reviews (
                        id SERIAL PRIMARY KEY,
                        booking_id INTEGER UNIQUE NOT NULL REFERENCES bookings(id),
                        listing_id INTEGER NOT NULL REFERENCES listings(id),
                        reviewer_id INTEGER NOT NULL REFERENCES users(id),
                        rating INTEGER NOT NULL,
                        comment TEXT,
                        created_at TIMESTAMP DEFAULT NOW()
                    )""",
                ]
                for sql in migrations:
                    try:
                        async with engine.begin() as conn:
                            await conn.execute(text(sql))
                        logger.info("Migration OK: %.60s", sql)
                    except Exception as m_exc:
                        logger.warning("Migration skipped (%.60s): %s", sql, m_exc)

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
