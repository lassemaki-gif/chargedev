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
                    "ALTER TABLE users ADD COLUMN IF NOT EXISTS stripe_account_id VARCHAR(255)",
                    "ALTER TABLE bookings ADD COLUMN IF NOT EXISTS stripe_transfer_id VARCHAR(255)",
                    """CREATE TABLE IF NOT EXISTS reviews (
                        id SERIAL PRIMARY KEY,
                        booking_id INTEGER UNIQUE NOT NULL REFERENCES bookings(id),
                        listing_id INTEGER NOT NULL REFERENCES listings(id),
                        reviewer_id INTEGER NOT NULL REFERENCES users(id),
                        rating INTEGER NOT NULL,
                        comment TEXT,
                        created_at TIMESTAMP DEFAULT NOW()
                    )""",
                    "ALTER TABLE listings ADD COLUMN IF NOT EXISTS shelly_device_id VARCHAR(255)",
                    "ALTER TABLE listings ADD COLUMN IF NOT EXISTS shelly_auth_key VARCHAR(255)",
                    "ALTER TABLE listings ADD COLUMN IF NOT EXISTS shelly_server VARCHAR(255)",
                    """CREATE TABLE IF NOT EXISTS shelly_sessions (
                        id SERIAL PRIMARY KEY,
                        booking_id INTEGER UNIQUE NOT NULL REFERENCES bookings(id),
                        listing_id INTEGER NOT NULL REFERENCES listings(id),
                        device_id VARCHAR(255) NOT NULL,
                        auth_key VARCHAR(255) NOT NULL,
                        server VARCHAR(255) NOT NULL,
                        target_kwh FLOAT NOT NULL,
                        energy_start_wh FLOAT NOT NULL DEFAULT 0,
                        status VARCHAR(20) NOT NULL DEFAULT 'active',
                        started_at TIMESTAMP DEFAULT NOW(),
                        stopped_at TIMESTAMP
                    )""",
                    # NOTE: previously wiped iban on every boot while payouts ran through
                    # Mollie Connect. SumUp returns payouts to ChargedEV's account, so we
                    # need to collect each host's IBAN again for weekly SEPA batches.
                    "ALTER TABLE users ALTER COLUMN iban TYPE VARCHAR(255)",
                    "ALTER TABLE users ADD COLUMN IF NOT EXISTS payout_account_holder VARCHAR(120)",
                    "ALTER TABLE users ADD COLUMN IF NOT EXISTS joining_fee_transaction_id VARCHAR(64)",
                    "ALTER TABLE users ADD COLUMN IF NOT EXISTS joining_fee_refunded_at TIMESTAMP",
                    "ALTER TABLE users ADD COLUMN IF NOT EXISTS failed_login_count INTEGER NOT NULL DEFAULT 0",
                    "ALTER TABLE users ADD COLUMN IF NOT EXISTS locked_until TIMESTAMP",
                    """CREATE TABLE IF NOT EXISTS host_payouts (
                        id SERIAL PRIMARY KEY,
                        host_id INTEGER NOT NULL REFERENCES users(id),
                        period_start TIMESTAMP NOT NULL,
                        period_end TIMESTAMP NOT NULL,
                        net_eur FLOAT NOT NULL,
                        booking_ids_json TEXT NOT NULL,
                        iban_last4 VARCHAR(4),
                        account_holder VARCHAR(120),
                        status VARCHAR(20) NOT NULL DEFAULT 'pending',
                        sepa_reference VARCHAR(64),
                        created_at TIMESTAMP DEFAULT NOW(),
                        paid_at TIMESTAMP
                    )""",
                    "CREATE INDEX IF NOT EXISTS ix_host_payouts_host_id ON host_payouts (host_id)",
                    "CREATE INDEX IF NOT EXISTS ix_host_payouts_status ON host_payouts (status)",
                    # Belt-and-braces against the race in payouts.generate_batch:
                    # at most one 'pending' row per host at any time. The advisory
                    # lock in generate_batch serialises callers; this index is the
                    # fallback that rejects a second row even if the lock misfires.
                    "CREATE UNIQUE INDEX IF NOT EXISTS ux_host_payouts_pending_per_host "
                    "ON host_payouts (host_id) WHERE status = 'pending'",
                    "ALTER TABLE listings ADD COLUMN IF NOT EXISTS ocpp_charge_point_id VARCHAR(255)",
                    "CREATE UNIQUE INDEX IF NOT EXISTS ix_listings_ocpp_charge_point_id ON listings (ocpp_charge_point_id) WHERE ocpp_charge_point_id IS NOT NULL",
                    """CREATE TABLE IF NOT EXISTS ocpp_charge_points (
                        id SERIAL PRIMARY KEY,
                        listing_id INTEGER UNIQUE NOT NULL REFERENCES listings(id),
                        charge_point_id VARCHAR(255) UNIQUE NOT NULL,
                        vendor VARCHAR(255),
                        model VARCHAR(255),
                        status VARCHAR(50) NOT NULL DEFAULT 'offline',
                        last_heartbeat TIMESTAMP,
                        created_at TIMESTAMP DEFAULT NOW()
                    )""",
                    """CREATE TABLE IF NOT EXISTS ocpp_sessions (
                        id SERIAL PRIMARY KEY,
                        booking_id INTEGER UNIQUE NOT NULL REFERENCES bookings(id),
                        charge_point_id VARCHAR(255) NOT NULL,
                        transaction_id INTEGER,
                        id_tag VARCHAR(20) NOT NULL,
                        target_kwh FLOAT NOT NULL,
                        energy_start_wh FLOAT NOT NULL DEFAULT 0,
                        energy_consumed_wh FLOAT NOT NULL DEFAULT 0,
                        status VARCHAR(20) NOT NULL DEFAULT 'starting',
                        started_at TIMESTAMP DEFAULT NOW(),
                        stopped_at TIMESTAMP
                    )""",
                    "ALTER TABLE users ADD COLUMN IF NOT EXISTS email_verified BOOLEAN DEFAULT FALSE",
                    "ALTER TABLE users ADD COLUMN IF NOT EXISTS verification_token VARCHAR(64)",
                    "ALTER TABLE users ADD COLUMN IF NOT EXISTS mollie_profile_id VARCHAR(40)",
                    "ALTER TABLE bookings ADD COLUMN IF NOT EXISTS vat_eur FLOAT NOT NULL DEFAULT 0",
                    "ALTER TABLE ocpp_charge_points ADD COLUMN IF NOT EXISTS auth_token VARCHAR(64)",
                    "ALTER TABLE users ADD COLUMN IF NOT EXISTS webhook_token VARCHAR(64)",
                    "ALTER TABLE users ADD COLUMN IF NOT EXISTS password_reset_hash VARCHAR(128)",
                    "ALTER TABLE users ADD COLUMN IF NOT EXISTS password_reset_expires TIMESTAMP",
                    # NOTE: removed `UPDATE users SET email_verified = TRUE WHERE email_verified IS NULL`
                    # which backfilled every pre-existing row to verified on first boot — a one-shot
                    # migration that no longer needs to run on fresh deploys. New rows default to FALSE
                    # at the model level (models.py). Keeping it here would silently verify any future
                    # NULL rows (e.g. accounts migrated in from a backup).
                    """CREATE TABLE IF NOT EXISTS platform_mollie (
                        id INTEGER PRIMARY KEY DEFAULT 1,
                        access_token VARCHAR(2048),
                        refresh_token VARCHAR(2048),
                        nonce VARCHAR(64),
                        updated_at TIMESTAMP DEFAULT NOW()
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
