"""ChargedEV API — EV charging marketplace backend."""
import asyncio
import base64
import hashlib
import io
import logging
import re
import secrets
import string
from contextlib import asynccontextmanager
from decimal import Decimal, ROUND_HALF_UP
from html import escape as html_escape
from datetime import datetime, timedelta, timezone
from typing import Optional
from urllib.parse import urlencode

import httpx
import qrcode
import resend
from fastapi import Depends, FastAPI, Form, HTTPException, Request, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, RedirectResponse, Response
from slowapi import Limiter, _rate_limit_exceeded_handler
from slowapi.errors import RateLimitExceeded
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from ocpp.routing import on
from ocpp.v16 import ChargePoint as OcppCp
from ocpp.v16 import call, call_result
from ocpp.v16.enums import Action, RegistrationStatus, RemoteStartStopStatus

from jose import JWTError, jwt as jose_jwt
from .auth import (
    create_access_token, get_current_user, hash_password,
    require_role, verify_password,
)
from .config import settings
from .db import get_session, init_db
from .models import (
    Booking, BookingStatus, HostPayout, HostSession, Listing, Message, PACKAGES_KWH, Review,
    OcppChargePoint, OcppSession, ShellySession, User, UserRole,
)
from . import payouts, sumup
from .schemas import (
    BookingCreate, BookingOut, ChargeStartContext, ForgotPasswordRequest, HostPayoutOut,
    HostSessionOut, ListingCreate, ListingOut, LoginRequest, MarkPaidIn, MessageIn, MessageOut,
    OcppChargePointOut, OcppRegisterRequest, PayoutAccountIn, PayoutAccountOut, PlatformStats,
    ProfileUpdate, RegisterRequest, ResetPasswordRequest, ReviewCreate, ReviewOut, SellerEarnings,
    ShellyConfigRequest, ShellyStatusOut, TokenResponse, UnreadCountOut, UserOut,
    WeeklyAvailabilitySchema,
)


# ── Shelly Cloud API client ───────────────────────────────────────────────────

class ShellyClient:
    """Thin wrapper around the Shelly Cloud HTTP API."""

    def __init__(self, server: str, auth_key: str, device_id: str):
        self._base = f"https://{server}"
        self._key = auth_key
        self._id = device_id

    def _params(self) -> dict:
        return {"id": self._id, "auth_key": self._key}

    async def relay(self, on: bool, channel: int = 0) -> bool:
        try:
            async with httpx.AsyncClient(timeout=10) as c:
                r = await c.post(
                    f"{self._base}/device/relay/control",
                    data={**self._params(), "channel": channel, "turn": "on" if on else "off"},
                )
                return r.json().get("isok", False)
        except Exception as exc:
            logger.warning("Shelly relay error: %s", exc)
            return False

    async def status(self) -> Optional[dict]:
        try:
            async with httpx.AsyncClient(timeout=10) as c:
                r = await c.post(f"{self._base}/device/status", data=self._params())
                d = r.json()
                return d.get("data", {}).get("device_status") if d.get("isok") else None
        except Exception as exc:
            logger.warning("Shelly status error: %s", exc)
            return None


def _shelly_energy_wh(ds: dict) -> float:
    """Extract cumulative energy in Wh from device status, handling Gen1/Gen2/Gen3."""
    for k, v in ds.items():
        if k.startswith("switch:") and isinstance(v, dict):
            total = v.get("aenergy", {}).get("total")
            if total is not None:
                return float(total)
    if "emdata:0" in ds:
        return float(ds["emdata:0"].get("total_act", 0))
    if "em:0" in ds:
        return float(ds["em:0"].get("total_act_energy", 0))
    meters = ds.get("meters", [])
    if meters:
        return float(meters[0].get("total", 0))
    return 0.0


def _shelly_power_w(ds: dict) -> float:
    for k, v in ds.items():
        if k.startswith("switch:") and isinstance(v, dict):
            return float(v.get("apower", 0))
        if k.startswith("em:") and isinstance(v, dict):
            return float(v.get("total_act_power", 0))
    meters = ds.get("meters", [])
    return float(meters[0].get("power", 0)) if meters else 0.0


def _shelly_relay_on(ds: dict) -> bool:
    for k, v in ds.items():
        if k.startswith("switch:") and isinstance(v, dict):
            return bool(v.get("output", False))
    relays = ds.get("relays", [])
    return bool(relays[0].get("ison", False)) if relays else False


async def _shelly_poll_loop() -> None:
    """Background task: poll active Shelly sessions and stop relay when target kWh reached."""
    from .db import async_session
    POLL_INTERVAL = 45  # seconds

    while True:
        await asyncio.sleep(POLL_INTERVAL)
        try:
            async with async_session() as sess:
                sessions = (await sess.execute(
                    select(ShellySession).where(ShellySession.status == "active")
                )).scalars().all()

            sem = asyncio.Semaphore(5)

            async def _poll_one(s):
                async with sem:
                    try:
                        # Fetch credentials fresh from the parent listing each tick
                        # (the session row no longer stores a copy — single source
                        # of truth, and a leaked sessions dump holds no secrets).
                        async with async_session() as _sess:
                            listing = await _sess.get(Listing, s.listing_id)
                        if not listing or not listing.shelly_auth_key:
                            logger.warning("Shelly session %d: parent listing missing creds — skipping", s.id)
                            return
                        auth_key = _decrypt_shelly_key(listing.shelly_auth_key)
                        client = ShellyClient(listing.shelly_server, auth_key, listing.shelly_device_id)
                        ds = await client.status()
                        if ds is None:
                            return
                        current_wh = _shelly_energy_wh(ds)
                        consumed_wh = current_wh - s.energy_start_wh
                        consumed_kwh = consumed_wh / 1000.0
                        logger.info(
                            "Shelly session %d: consumed=%.3f kWh target=%.1f kWh",
                            s.id, consumed_kwh, s.target_kwh,
                        )
                        if consumed_kwh >= s.target_kwh:
                            await client.relay(False)
                            async with async_session() as sess:
                                ss = await sess.get(ShellySession, s.id)
                                if ss and ss.status == "active":
                                    ss.status = "completed"
                                    ss.stopped_at = datetime.utcnow()
                                    booking = await sess.get(Booking, ss.booking_id)
                                    if booking and booking.status == BookingStatus.confirmed:
                                        booking.status = BookingStatus.completed
                                        booking.completed_at = datetime.utcnow()
                                    await sess.commit()
                                logger.info("Shelly session %d completed — relay off", s.id)
                    except Exception as exc:
                        logger.warning("Shelly poll error session %d: %s", s.id, exc)

            await asyncio.gather(*[_poll_one(s) for s in sessions])
        except Exception as exc:
            logger.warning("Shelly poll loop error: %s", exc)


# ── OCPP 1.6 Central System ───────────────────────────────────────────────────

# In-memory registry: charge_point_id → connected ChargePoint handler
_ocpp_connections: dict[str, "ChargedEVChargePoint"] = {}


class ChargedEVChargePoint(OcppCp):
    """OCPP 1.6 charge point handler — one instance per connected wallbox."""

    @on(Action.boot_notification)
    async def on_boot_notification(self, charge_point_vendor: str, charge_point_model: str, **kwargs):
        from .db import async_session
        async with async_session() as sess:
            cp = (await sess.execute(
                select(OcppChargePoint).where(OcppChargePoint.charge_point_id == self.id)
            )).scalar_one_or_none()
            if cp:
                cp.vendor = charge_point_vendor
                cp.model = charge_point_model
                cp.status = "available"
                cp.last_heartbeat = datetime.now(timezone.utc)
                await sess.commit()
        logger.info("OCPP BootNotification from %s (%s %s)", self.id, charge_point_vendor, charge_point_model)
        return call_result.BootNotification(
            current_time=datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S") + "Z",
            interval=300,
            status=RegistrationStatus.accepted,
        )

    @on(Action.heartbeat)
    async def on_heartbeat(self, **kwargs):
        from .db import async_session
        async with async_session() as sess:
            cp = (await sess.execute(
                select(OcppChargePoint).where(OcppChargePoint.charge_point_id == self.id)
            )).scalar_one_or_none()
            if cp:
                cp.last_heartbeat = datetime.now(timezone.utc)
                await sess.commit()
        return call_result.Heartbeat(
            current_time=datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S") + "Z"
        )

    @on(Action.status_notification)
    async def on_status_notification(self, connector_id: int, error_code: str, status: str, **kwargs):
        from .db import async_session
        async with async_session() as sess:
            cp = (await sess.execute(
                select(OcppChargePoint).where(OcppChargePoint.charge_point_id == self.id)
            )).scalar_one_or_none()
            if cp and connector_id in (0, 1):
                cp.status = status.lower()
                await sess.commit()
        logger.info("OCPP StatusNotification %s: connector=%d status=%s", self.id, connector_id, status)
        return call_result.StatusNotification()

    @on(Action.start_transaction)
    async def on_start_transaction(self, connector_id: int, id_tag: str, timestamp: str, meter_start: int, **kwargs):
        from .db import async_session
        transaction_id = int(secrets.randbelow(2 ** 31))
        async with async_session() as sess:
            # Host-session idTags are "host{host_id:06d}" — bind the txn_id to
            # the HostSession so we can RemoteStop it on 8h cap or Stop press.
            if id_tag.startswith("host"):
                try:
                    host_id = int(id_tag[4:])
                except ValueError:
                    host_id = None
                if host_id is not None:
                    host_sess = (await sess.execute(
                        select(HostSession)
                        .join(Listing, Listing.id == HostSession.listing_id)
                        .where(
                            HostSession.host_id == host_id,
                            HostSession.status == "active",
                            HostSession.transaction_id.is_(None),
                            Listing.ocpp_charge_point_id == self.id,
                        )
                    )).scalars().first()
                    if host_sess:
                        host_sess.transaction_id = transaction_id
                        host_sess.energy_start_wh = float(meter_start)
                        await sess.commit()
                        logger.info("OCPP StartTransaction txn=%d cpid=%s host_session=%d", transaction_id, self.id, host_sess.id)
                        return call_result.StartTransaction(
                            transaction_id=transaction_id,
                            id_tag_info={"status": "Accepted"},
                        )

            # Buyer booking idTags are "bk{booking_id:06d}" — standard flow.
            ocpp_sess = (await sess.execute(
                select(OcppSession).where(
                    OcppSession.charge_point_id == self.id,
                    OcppSession.id_tag == id_tag,
                    OcppSession.status == "starting",
                )
            )).scalar_one_or_none()
            if ocpp_sess:
                ocpp_sess.transaction_id = transaction_id
                ocpp_sess.energy_start_wh = float(meter_start)
                ocpp_sess.status = "active"
                await sess.commit()
                logger.info("OCPP StartTransaction txn=%d cpid=%s booking=%d", transaction_id, self.id, ocpp_sess.booking_id)
        return call_result.StartTransaction(
            transaction_id=transaction_id,
            id_tag_info={"status": "Accepted"},
        )

    @on(Action.stop_transaction)
    async def on_stop_transaction(self, transaction_id: int, timestamp: str, meter_stop: int, **kwargs):
        from .db import async_session
        async with async_session() as sess:
            # Host session?
            host_sess = (await sess.execute(
                select(HostSession).where(HostSession.transaction_id == transaction_id)
            )).scalar_one_or_none()
            if host_sess and host_sess.status in ("active", "error"):
                host_sess.energy_consumed_wh = max(0.0, float(meter_stop) - host_sess.energy_start_wh)
                host_sess.status = "completed"
                host_sess.stopped_at = datetime.utcnow()
                await sess.commit()
                logger.info("OCPP StopTransaction txn=%d host_session=%d consumed=%.3f kWh",
                            transaction_id, host_sess.id, host_sess.energy_consumed_wh / 1000.0)
                return call_result.StopTransaction()

            # Buyer session.
            ocpp_sess = (await sess.execute(
                select(OcppSession).where(OcppSession.transaction_id == transaction_id)
            )).scalar_one_or_none()
            if ocpp_sess and ocpp_sess.status == "active":
                consumed = (meter_stop - ocpp_sess.energy_start_wh) / 1000.0
                ocpp_sess.energy_consumed_wh = float(meter_stop - ocpp_sess.energy_start_wh)
                ocpp_sess.status = "completed"
                ocpp_sess.stopped_at = datetime.utcnow()
                booking = await sess.get(Booking, ocpp_sess.booking_id)
                if booking and booking.status == BookingStatus.confirmed:
                    booking.status = BookingStatus.completed
                    booking.completed_at = datetime.utcnow()
                await sess.commit()
                logger.info("OCPP StopTransaction txn=%d consumed=%.3f kWh", transaction_id, consumed)
        return call_result.StopTransaction()

    @on(Action.meter_values)
    async def on_meter_values(self, connector_id: int, meter_value: list, **kwargs):
        from .db import async_session
        # Extract Wh reading and check if target reached
        wh = None
        transaction_id = kwargs.get("transaction_id")
        for mv in meter_value:
            for sv in mv.get("sampled_value", []):
                if sv.get("measurand", "Energy.Active.Import.Register") == "Energy.Active.Import.Register":
                    try:
                        wh = float(sv.get("value", 0))
                        if sv.get("unit", "Wh") == "kWh":
                            wh *= 1000
                    except (ValueError, TypeError):
                        pass
        if wh is not None and transaction_id:
            async with async_session() as sess:
                ocpp_sess = (await sess.execute(
                    select(OcppSession).where(OcppSession.transaction_id == transaction_id)
                )).scalar_one_or_none()
                if ocpp_sess and ocpp_sess.status == "active":
                    consumed_kwh = (wh - ocpp_sess.energy_start_wh) / 1000.0
                    ocpp_sess.energy_consumed_wh = wh - ocpp_sess.energy_start_wh
                    await sess.commit()
                    logger.info("OCPP MeterValues txn=%d consumed=%.3f kWh target=%.1f", transaction_id, consumed_kwh, ocpp_sess.target_kwh)
                    if consumed_kwh >= ocpp_sess.target_kwh:
                        asyncio.create_task(self._stop_session(transaction_id))
        return call_result.MeterValues()

    async def _stop_session(self, transaction_id: int) -> None:
        try:
            req = call.RemoteStopTransaction(transaction_id=transaction_id)
            resp = await self.call(req)
            logger.info("OCPP RemoteStopTransaction txn=%d status=%s", transaction_id, resp.status)
        except Exception as exc:
            logger.error("OCPP RemoteStopTransaction error txn=%d: %s", transaction_id, exc)

    async def remote_start(self, id_tag: str, connector_id: int = 1) -> bool:
        try:
            req = call.RemoteStartTransaction(id_tag=id_tag, connector_id=connector_id)
            resp = await self.call(req)
            return resp.status == RemoteStartStopStatus.accepted
        except Exception as exc:
            logger.error("OCPP RemoteStartTransaction error cpid=%s: %s", self.id, exc)
            return False


async def _ocpp_start_session(booking: Booking, listing: Listing) -> None:
    """Send RemoteStartTransaction to the OCPP charge point. Fires as a background task."""
    from .db import async_session
    cp_handler = _ocpp_connections.get(listing.ocpp_charge_point_id)
    id_tag = f"bk{booking.id:06d}"
    # Create session record first
    async with async_session() as sess:
        existing = (await sess.execute(
            select(OcppSession).where(OcppSession.booking_id == booking.id)
        )).scalar_one_or_none()
        if not existing:
            sess.add(OcppSession(
                booking_id=booking.id,
                charge_point_id=listing.ocpp_charge_point_id,
                id_tag=id_tag,
                target_kwh=float(booking.package_kwh),
                status="starting" if cp_handler else "error",
            ))
            await sess.commit()
    if not cp_handler:
        logger.error("OCPP charge point %s not connected for booking %d", listing.ocpp_charge_point_id, booking.id)
        return
    ok = await cp_handler.remote_start(id_tag)
    if not ok:
        logger.error("OCPP RemoteStartTransaction rejected for booking %d", booking.id)
        async with async_session() as sess:
            ocpp_s = (await sess.execute(
                select(OcppSession).where(OcppSession.booking_id == booking.id)
            )).scalar_one_or_none()
            if ocpp_s:
                ocpp_s.status = "error"
                await sess.commit()
    else:
        logger.info("OCPP session started for booking %d (id_tag=%s)", booking.id, id_tag)


async def _attempt_booking_refund(booking_id: int, *, source: str) -> bool:
    """Idempotently refund an expired booking via SumUp and mark it cancelled.

    Pattern (same as joining_fee_refunded_at):
    1. FOR UPDATE on the booking row.
    2. Short-circuit if already refunded OR already attempted within 5 min.
    3. Stamp refund_attempted_at + commit (releases the lock) BEFORE calling SumUp.
    4. If SumUp refund succeeds: FOR UPDATE again, set refund_completed_at AND
       flip status=cancelled in the same transaction.
    5. If SumUp refund fails: revert refund_attempted_at so the sweep retries.
       Status stays `confirmed`-but-expired so it still matches the sweep filter.

    `source` is just a tag for the log line ("sweep" vs "buyer-start").
    Returns True on a successful refund, False otherwise (incl. "already done").
    """
    from .db import async_session
    now = datetime.utcnow()
    # Step 1 + 2 + 3: lock, guard, stamp, commit (release).
    async with async_session() as sess:
        b = (await sess.execute(
            select(Booking).where(Booking.id == booking_id).with_for_update()
        )).scalar_one_or_none()
        if not b:
            return False
        if b.refund_completed_at is not None:
            return False  # already refunded
        if (b.refund_attempted_at is not None
                and b.refund_attempted_at > now - timedelta(minutes=5)):
            return False  # another coroutine is actively trying
        if not b.stripe_session_id:
            # Nothing to refund (shouldn't happen for a paid booking, but defend).
            b.status = BookingStatus.cancelled
            await sess.commit()
            logger.warning("Booking %d had no stripe_session_id — marked cancelled without refund (source=%s)", b.id, source)
            return False
        tentative_at = now
        b.refund_attempted_at = tentative_at
        await sess.commit()

    # Step 4/5: fire the external calls OUTSIDE the lock so we don't hold a row
    # lock across a 15-second SumUp request.
    try:
        verif = await sumup.verify_checkout(b.stripe_session_id)
        txn_id = verif.get("transaction_id")
        if not txn_id:
            raise RuntimeError("SumUp verify returned no transaction_id")
        await sumup.refund(txn_id, amount_eur=Decimal(f"{b.total_eur:.2f}"))
    except Exception as exc:
        # Revert the stamp (only if it still matches ours — another retry may
        # have overwritten it) so the sweep picks the row up again.
        async with async_session() as sess:
            row = await sess.get(Booking, booking_id)
            if row and row.refund_attempted_at == tentative_at:
                row.refund_attempted_at = None
                await sess.commit()
        logger.error("Booking %d refund failed (source=%s): %s — will retry", booking_id, source, exc)
        return False

    # Success: terminal transition, status + completed_at together.
    async with async_session() as sess:
        row = (await sess.execute(
            select(Booking).where(Booking.id == booking_id).with_for_update()
        )).scalar_one_or_none()
        if row and row.refund_completed_at is None:
            row.refund_completed_at = datetime.utcnow()
            row.status = BookingStatus.cancelled
            await sess.commit()
            logger.info("Booking %d refunded + cancelled (source=%s)", booking_id, source)
    return True


async def _booking_expiry_sweep() -> None:
    """Every minute: (a) cancel+refund bookings past their 30-min start window,
    (b) stop host sessions past their 8-hour cap. Belt-and-braces for the
    in-request checks in buyer_start_session / host_session_start."""
    from .db import async_session
    POLL_INTERVAL = 60  # seconds

    while True:
        await asyncio.sleep(POLL_INTERVAL)
        try:
            now = datetime.utcnow()

            # (b) Host sessions needing relay-off:
            #   - status=active AND expires_at < now (8h cap hit)
            #   - status=error (relay-off previously failed, retry)
            async with async_session() as sess:
                stale_hs = (await sess.execute(
                    select(HostSession).where(
                        (HostSession.status == "active") & (HostSession.expires_at < now)
                        | (HostSession.status == "error"),
                    ).limit(20)
                )).scalars().all()
            for hs in stale_hs:
                try:
                    async with async_session() as sess2:
                        listing = await sess2.get(Listing, hs.listing_id)
                    relay_off_ok = True
                    if hs.kind == "shelly" and listing and listing.shelly_auth_key:
                        try:
                            auth_key = _decrypt_shelly_key(listing.shelly_auth_key)
                            client = ShellyClient(listing.shelly_server, auth_key, listing.shelly_device_id)
                            ds = await client.status()
                            if ds:
                                hs.energy_consumed_wh = max(0.0, _shelly_energy_wh(ds) - hs.energy_start_wh)
                            relay_off_ok = await client.relay(False)
                            # Verify by re-polling (A4): if still on, leave in error.
                            try:
                                verify = await client.status()
                                if verify is not None and _shelly_relay_on(verify):
                                    relay_off_ok = False
                            except Exception:
                                relay_off_ok = False
                        except Exception as exc:
                            logger.warning("Expiry sweep: Shelly stop failed for host session %d: %s", hs.id, exc)
                            relay_off_ok = False
                    elif hs.kind == "ocpp" and listing and listing.ocpp_charge_point_id:
                        # A5: issue RemoteStop using the captured transaction_id.
                        cp_handler = _ocpp_connections.get(listing.ocpp_charge_point_id)
                        if hs.transaction_id and cp_handler:
                            try:
                                await cp_handler._stop_session(hs.transaction_id)
                                logger.info("Expiry sweep: OCPP RemoteStop sent for host session %d (txn=%d)", hs.id, hs.transaction_id)
                            except Exception as exc:
                                logger.error("Expiry sweep: OCPP RemoteStop failed for host session %d: %s", hs.id, exc)
                                relay_off_ok = False
                        else:
                            # No txn_id (charger never acked StartTransaction) or
                            # the charger disconnected. Can't force-stop; leave
                            # in error so an admin can clean up manually.
                            relay_off_ok = False
                    async with async_session() as sess2:
                        row = await sess2.get(HostSession, hs.id)
                        if not row:
                            continue
                        row.energy_consumed_wh = hs.energy_consumed_wh
                        if relay_off_ok:
                            row.status = "completed"
                            row.stopped_at = now
                            await sess2.commit()
                            logger.info("Expiry sweep: host session %d stopped (relay confirmed off)", hs.id)
                        else:
                            row.status = "error"
                            await sess2.commit()
                            logger.warning("Expiry sweep: host session %d still on — will retry next tick", hs.id)
                except Exception as exc:
                    logger.error("Expiry sweep host-session error %d: %s", hs.id, exc)

            # (a) Expired unstarted bookings: refund via SumUp, THEN cancel.
            # Select rows that either have never been refunded, or whose last
            # refund attempt is more than 5 minutes old (transient SumUp outage
            # recovery). refund_completed_at is the terminal flag.
            retry_cutoff = now - timedelta(minutes=5)
            async with async_session() as sess:
                expired = (await sess.execute(
                    select(Booking).where(
                        Booking.status == BookingStatus.confirmed,
                        Booking.started_at.is_(None),
                        Booking.expires_at.isnot(None),
                        Booking.expires_at < now,
                        Booking.refund_completed_at.is_(None),
                        (Booking.refund_attempted_at.is_(None))
                        | (Booking.refund_attempted_at < retry_cutoff),
                    ).limit(20)
                )).scalars().all()
            for b in expired:
                await _attempt_booking_refund(b.id, source="sweep")
        except Exception as exc:
            logger.warning("Expiry sweep loop error: %s", exc)


@asynccontextmanager
async def lifespan(_: FastAPI):
    await init_db()
    asyncio.create_task(_shelly_poll_loop())
    asyncio.create_task(_booking_expiry_sweep())
    yield


resend.api_key = settings.resend_api_key
logger = logging.getLogger(__name__)

if not settings.secret_key:
    raise RuntimeError("SECRET_KEY env var is not set. Generate one with: openssl rand -hex 32")
if not settings.encryption_key:
    raise RuntimeError(
        "ENCRYPTION_KEY env var is not set. Generate one with: "
        "python -c 'from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())'"
    )


def real_ip(request: Request) -> str:
    """Extract real client IP for IP-based rate limiting.

    Each proxy in the chain APPENDS to x-forwarded-for; a client can prepend
    fake entries to the header but can't inject anything after the trusted
    edge's own value. So we read the LAST entry, not the first — that's what
    Railway's / Vercel's own edge put there.

    `x-real-ip` is dropped as an input because browsers can spoof it to the
    Next.js proxy; `x-forwarded-for` is cheap to defend by convention here.
    Rate limits on sensitive auth endpoints are additionally keyed by
    request body (email) so this IP question doesn't gate the main defence.
    """
    xff = request.headers.get("x-forwarded-for")
    if xff:
        parts = [p.strip() for p in xff.split(",") if p.strip()]
        if parts:
            return parts[-1]
    envoy = request.headers.get("x-envoy-external-address")
    if envoy:
        return envoy.split(",")[-1].strip()
    return request.client.host if request.client else "unknown"


limiter = Limiter(key_func=real_ip, default_limits=["60/minute"])


# Account-level lockout (defence against credential stuffing even when the
# source IP is spoofed). On each bad login we increment the User's counter
# and, once it crosses a threshold, set locked_until for a progressively
# longer window. We always return the same generic 401 so an attacker
# can't probe the lockout state.
_LOGIN_LOCKOUT_THRESHOLD = 5           # bad attempts before any lockout
_LOGIN_LOCKOUT_STEPS = [60, 300, 900, 3600]  # seconds: 1m, 5m, 15m, 1h, then 1h+


def _lockout_seconds_for(count: int) -> int:
    """Return the lockout duration (seconds) for the given failure count.
    Below the threshold → 0; above it, step through the ladder and stay at
    the max for subsequent fails."""
    if count < _LOGIN_LOCKOUT_THRESHOLD:
        return 0
    idx = min(count - _LOGIN_LOCKOUT_THRESHOLD, len(_LOGIN_LOCKOUT_STEPS) - 1)
    return _LOGIN_LOCKOUT_STEPS[idx]


async def geocode(address: str, city: str, country: str) -> tuple:
    """Returns (lat, lng) or (None, None) using OpenStreetMap Nominatim."""
    try:
        async with httpx.AsyncClient(timeout=5.0) as client:
            r = await client.get(
                "https://nominatim.openstreetmap.org/search",
                params={"q": f"{address}, {city}, {country}", "format": "json", "limit": 1},
                headers={"User-Agent": "ChargedEV/1.0 (chargedev.io)"},
            )
            data = r.json()
            if data:
                return float(data[0]["lat"]), float(data[0]["lon"])
    except Exception as exc:
        logger.warning("Geocoding failed: %s", exc)
    return None, None


def _qr_png_b64(url: str) -> str:
    """Return a base64-encoded PNG QR for `url`, matching the on-site visual.

    Palette matches the `/charge/success` page QR so buyers see the same mark
    at scan-time as in the email. Error-correction M balances robustness vs
    pixel density for the kWh-booking URL length.
    """
    qr = qrcode.QRCode(
        version=None,
        error_correction=qrcode.constants.ERROR_CORRECT_M,
        box_size=8,
        border=2,
    )
    qr.add_data(url)
    qr.make(fit=True)
    img = qr.make_image(fill_color="#F7FF2B", back_color="#0B0E13")
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return base64.b64encode(buf.getvalue()).decode("ascii")


async def send_pin_email(buyer_email: str, buyer_name: str, booking: Booking, listing: Listing) -> None:
    if not settings.resend_api_key:
        logger.warning("RESEND_API_KEY not set — skipping email")
        return
    # Hardcoded prod host via settings.frontend_url — a preview URL would 404
    # after deprovision; a lookalike would phish scanners. Same URL the on-site
    # QR sticker and /charge/success page encode, so one scan path for all.
    start_url = f"{settings.frontend_url}/charge/{listing.id}/start"
    qr_attachment = None
    try:
        qr_attachment = {
            "filename": "chargedev-qr.png",
            "content": _qr_png_b64(start_url),
            "content_type": "image/png",
            "content_id": "chargedev-qr",
        }
    except Exception as exc:
        logger.error("QR generation failed for booking %d: %s", booking.id, exc)
    try:
        payload = {
            "from": settings.email_from,
            "to": [buyer_email],
            "subject": f"Your ChargedEV PIN — {listing.title}",
            "html": f"""
            <div style="font-family:sans-serif;max-width:480px;margin:0 auto;padding:32px">
              <h2 style="color:#22C55E;margin-bottom:4px">Your charging PIN is ready ⚡</h2>
              <p style="color:#555">Payment confirmed. Scan the QR to open the start page, or enter the PIN below as a backup.</p>
              <div style="background:#0A0F1E;border-radius:12px;padding:24px;text-align:center;margin:24px 0">
                {('<p style="color:#9CA3AF;font-size:12px;text-transform:uppercase;letter-spacing:0.1em;margin:0 0 12px">Scan to start</p>'
                  '<img src="cid:chargedev-qr" alt="Scan to start charging" width="220" height="220" style="display:block;margin:0 auto 16px;border-radius:8px;max-width:100%;height:auto" />'
                  f'<p style="margin:0 0 16px"><a href="{start_url}" style="color:#F7FF2B;font-size:13px;text-decoration:underline">Or tap here to open on this device</a></p>'
                  '<div style="height:1px;background:#1F2937;margin:0 32px 16px"></div>') if qr_attachment else ''}
                <p style="color:#9CA3AF;font-size:12px;text-transform:uppercase;letter-spacing:0.1em;margin:0 0 8px">Backup PIN</p>
                <p style="color:#22C55E;font-size:40px;font-family:monospace;font-weight:700;letter-spacing:0.2em;margin:0">{booking.pin_code}</p>
              </div>
              <table style="width:100%;font-size:14px;color:#555;border-collapse:collapse">
                <tr><td style="padding:6px 0;border-bottom:1px solid #eee">Charger</td><td style="padding:6px 0;border-bottom:1px solid #eee;text-align:right;font-weight:600;color:#111">{html_escape(listing.title)}</td></tr>
                <tr><td style="padding:6px 0;border-bottom:1px solid #eee">Address</td><td style="padding:6px 0;border-bottom:1px solid #eee;text-align:right;color:#111">{html_escape(listing.address)}, {html_escape(listing.city)}</td></tr>
                <tr><td style="padding:6px 0;border-bottom:1px solid #eee">Package</td><td style="padding:6px 0;border-bottom:1px solid #eee;text-align:right;color:#111">{booking.package_kwh} kWh</td></tr>
                <tr><td style="padding:6px 0;border-bottom:1px solid #eee;color:#9CA3AF">incl. VAT 25.5%</td><td style="padding:6px 0;border-bottom:1px solid #eee;text-align:right;color:#9CA3AF">€{(booking.vat_eur or 0):.2f}</td></tr>
                <tr><td style="padding:6px 0">Total paid</td><td style="padding:6px 0;text-align:right;font-weight:700;color:#111">€{booking.total_eur:.2f}</td></tr>
              </table>
              <p style="color:#9CA3AF;font-size:12px;margin-top:24px">ChargedEV · chargedev.io</p>
            </div>
            """,
        }
        if qr_attachment:
            payload["attachments"] = [qr_attachment]
        resend.Emails.send(payload)
        logger.info("PIN email sent to %s", buyer_email)
    except Exception as exc:
        logger.error("Failed to send PIN email: %s", exc)


async def _shelly_start_session(booking: Booking, listing: Listing) -> None:
    """Turn on Shelly relay and create a session record. Fires as a background task.

    The ShellySession row no longer duplicates auth_key/server from the listing —
    the poll loop re-reads them from Listing each tick, so an auth-key rotation
    on the listing immediately applies to the in-flight session, and a leaked
    sessions table can't hand out every host's Shelly relay credentials.
    """
    from .db import async_session
    auth_key = _decrypt_shelly_key(listing.shelly_auth_key)
    client = ShellyClient(listing.shelly_server, auth_key, listing.shelly_device_id)
    ds = await client.status()
    if ds is None:
        logger.error("Shelly device offline for booking %d — cannot start session", booking.id)
        async with async_session() as sess:
            sess.add(ShellySession(
                booking_id=booking.id, listing_id=listing.id,
                device_id=listing.shelly_device_id, auth_key="", server="",
                target_kwh=float(booking.package_kwh),
                energy_start_wh=0.0, status="error",
            ))
            await sess.commit()
        return
    energy_start = _shelly_energy_wh(ds)
    ok = await client.relay(True)
    session_status = "active" if ok else "error"
    if not ok:
        logger.error("Shelly relay ON failed for booking %d — session marked error", booking.id)
    async with async_session() as sess:
        existing = (await sess.execute(
            select(ShellySession).where(ShellySession.booking_id == booking.id)
        )).scalar_one_or_none()
        if not existing:
            sess.add(ShellySession(
                booking_id=booking.id, listing_id=listing.id,
                device_id=listing.shelly_device_id, auth_key="", server="",
                target_kwh=float(booking.package_kwh),
                energy_start_wh=energy_start, status=session_status,
            ))
            await sess.commit()
    if ok:
        logger.info("Shelly session started for booking %d (start_wh=%.1f)", booking.id, energy_start)


async def listing_out(r: Listing, seller_name: str, session: AsyncSession) -> ListingOut:
    """Build ListingOut including avg rating."""
    reviews = (await session.execute(
        select(Review).where(Review.listing_id == r.id)
    )).scalars().all()
    avg = round(sum(rv.rating for rv in reviews) / len(reviews), 1) if reviews else None
    return ListingOut(
        **{c.key: getattr(r, c.key) for c in r.__table__.columns},
        seller_name=seller_name,
        avg_rating=avg,
        review_count=len(reviews),
        shelly_enabled=bool(r.shelly_device_id),
        ocpp_enabled=bool(r.ocpp_charge_point_id),
    )


async def send_host_booking_email(host_email: str, host_name: str, booking: Booking, listing: Listing, buyer: User) -> None:
    if not settings.resend_api_key:
        return
    try:
        resend.Emails.send({
            "from": settings.email_from,
            "to": [host_email],
            "subject": f"New booking — {listing.title}",
            "html": f"""
            <div style="font-family:sans-serif;max-width:480px;margin:0 auto;padding:32px">
              <h2 style="color:#22C55E;margin-bottom:4px">You have a new booking ⚡</h2>
              <p style="color:#555">Hi {html_escape(host_name)}, someone has booked your charger and payment is confirmed.</p>
              <div style="background:#0A0F1E;border-radius:12px;padding:24px;margin:24px 0">
                <p style="color:#9CA3AF;font-size:12px;text-transform:uppercase;letter-spacing:0.1em;margin:0 0 8px">Session PIN</p>
                <p style="color:#22C55E;font-size:40px;font-family:monospace;font-weight:700;letter-spacing:0.2em;margin:0">{booking.pin_code}</p>
                <p style="color:#9CA3AF;font-size:12px;margin:8px 0 0">Share this PIN with the driver when they arrive.</p>
              </div>
              <table style="width:100%;font-size:14px;color:#555;border-collapse:collapse">
                <tr><td style="padding:6px 0;border-bottom:1px solid #eee">Driver</td><td style="padding:6px 0;border-bottom:1px solid #eee;text-align:right;color:#111">{html_escape(buyer.full_name)}</td></tr>
                <tr><td style="padding:6px 0;border-bottom:1px solid #eee">Package</td><td style="padding:6px 0;border-bottom:1px solid #eee;text-align:right;color:#111">{booking.package_kwh} kWh</td></tr>
                <tr><td style="padding:6px 0;border-bottom:1px solid #eee">Your earnings</td><td style="padding:6px 0;border-bottom:1px solid #eee;text-align:right;font-weight:700;color:#22C55E">€{booking.seller_earnings_eur:.2f}</td></tr>
                <tr><td style="padding:6px 0">Charger</td><td style="padding:6px 0;text-align:right;color:#111">{html_escape(listing.address)}, {html_escape(listing.city)}</td></tr>
              </table>
              <p style="color:#555;font-size:14px;margin-top:20px">Once the session is done, mark it as complete in your <a href="https://chargedev.io/sell/dashboard" style="color:#22C55E">host dashboard</a>.</p>
              <p style="color:#9CA3AF;font-size:12px;margin-top:24px">ChargedEV · chargedev.io</p>
            </div>
            """,
        })
        logger.info("Host booking email sent to %s", host_email)
    except Exception as exc:
        logger.error("Failed to send host booking email: %s", exc)

async def send_password_reset_email(email: str, full_name: str, token: str) -> None:
    if not settings.resend_api_key:
        return
    reset_url = f"{settings.frontend_url}/reset-password?token={token}"
    try:
        resend.Emails.send({
            "from": settings.email_from,
            "to": [email],
            "subject": "Reset your ChargedEV password",
            "html": f"""
            <div style="font-family:sans-serif;max-width:480px;margin:0 auto;padding:32px">
              <h2 style="color:#22C55E;margin-bottom:4px">Reset your password 🔑</h2>
              <p style="color:#555">Hi {html_escape(full_name)}, click below to pick a new password for your ChargedEV account. The link expires in one hour and can only be used once.</p>
              <div style="margin:24px 0">
                <a href="{reset_url}" style="background:#22C55E;color:#0A0F1E;padding:12px 24px;border-radius:8px;text-decoration:none;font-weight:700;display:inline-block">Reset password</a>
              </div>
              <p style="color:#9CA3AF;font-size:12px">If you didn't ask to reset your password, ignore this email — your current password still works.</p>
              <p style="color:#9CA3AF;font-size:12px;margin-top:24px">ChargedEV · chargedev.io</p>
            </div>
            """,
        })
    except Exception as exc:
        logger.error("Failed to send password reset email: %s", _scrub(str(exc)))


async def send_verification_email(email: str, full_name: str, token: str) -> None:
    if not settings.resend_api_key:
        return
    verify_url = f"{settings.frontend_url}/verify-email?token={token}"
    try:
        resend.Emails.send({
            "from": settings.email_from,
            "to": [email],
            "subject": "Verify your ChargedEV email",
            "html": f"""
            <div style="font-family:sans-serif;max-width:480px;margin:0 auto;padding:32px">
              <h2 style="color:#22C55E;margin-bottom:4px">Verify your email ⚡</h2>
              <p style="color:#555">Hi {html_escape(full_name)}, click the link below to verify your ChargedEV account.</p>
              <div style="margin:24px 0">
                <a href="{verify_url}" style="background:#22C55E;color:#0A0F1E;padding:12px 24px;border-radius:8px;text-decoration:none;font-weight:700;display:inline-block">Verify email</a>
              </div>
              <p style="color:#9CA3AF;font-size:12px">Link expires in 24 hours. If you didn't create an account, ignore this email.</p>
              <p style="color:#9CA3AF;font-size:12px;margin-top:24px">ChargedEV · chargedev.io</p>
            </div>
            """,
        })
    except Exception as exc:
        logger.error("Failed to send verification email: %s", exc)


app = FastAPI(title="ChargedEV API", version="0.1.0", lifespan=lifespan)
app.state.limiter = limiter
app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)  # type: ignore[arg-type]

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.allowed_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


_SECRET_PARAM_RE = re.compile(
    r"(token|webhook_secret|access_token|refresh_token|client_secret|auth_key|code)=([^&\s'\"]+)",
    re.IGNORECASE,
)


def _scrub(text: str) -> str:
    """Redact secret-shaped query/body params before logging.

    httpx errors stringify as `Client error 'XXX' for url 'https://.../?token=…'`,
    which would otherwise land per-host webhook tokens, OAuth codes, and refresh
    tokens in Railway's log pipeline on any unhandled error path.
    """
    return _SECRET_PARAM_RE.sub(lambda m: f"{m.group(1)}=***", text or "")


@app.exception_handler(Exception)
async def global_exception_handler(request: Request, exc: Exception):
    logger.error("Unhandled error on %s: %s: %s", request.url.path, type(exc).__name__, _scrub(str(exc)), exc_info=True)
    origin = request.headers.get("origin", "")
    safe_origin = origin if origin in settings.allowed_origins else ""
    headers = {"Access-Control-Allow-Credentials": "true"}
    if safe_origin:
        headers["Access-Control-Allow-Origin"] = safe_origin
    return JSONResponse(
        status_code=500,
        content={"detail": "Internal server error"},
        headers=headers,
    )


def _calc_amounts(package_kwh: int, price_per_kwh: float) -> tuple[float, float, float, float]:
    """Return (total_eur_gross, vat_eur, fee_eur, earnings_eur) using Decimal to avoid float rounding.

    Finnish VAT at 25.5% is treated as included in the host's listed price. The
    platform (ChargedEV) is the deemed supplier and remits VAT to the Finnish
    tax authority; the 20% platform fee is taken from the net (ex-VAT) amount.
    """
    VAT_RATE = Decimal("0.255")
    total = (Decimal(str(package_kwh)) * Decimal(str(price_per_kwh))).quantize(Decimal("0.01"), ROUND_HALF_UP)
    vat = (total * VAT_RATE / (1 + VAT_RATE)).quantize(Decimal("0.01"), ROUND_HALF_UP)
    net = total - vat
    fee = (net * Decimal(str(settings.platform_fee_pct))).quantize(Decimal("0.01"), ROUND_HALF_UP)
    earnings = (net - fee).quantize(Decimal("0.01"), ROUND_HALF_UP)
    return float(total), float(vat), float(fee), float(earnings)


def _gen_pin() -> str:
    return "".join(secrets.choice(string.digits) for _ in range(6))


def _encrypt_token(token: str) -> str:
    """Fernet-encrypt a secret at rest. Used for Shelly auth keys (host IBANs
    have their own helper with permissive fallback on decrypt errors)."""
    if not token:
        return token
    from cryptography.fernet import Fernet
    return Fernet(settings.encryption_key.encode()).encrypt(token.encode()).decode()


def _decrypt_token(token: str) -> str:
    """Decrypt a Fernet-encrypted secret. Fails loudly on key rotation or
    ciphertext corruption so a stale value doesn't silently fail downstream."""
    if not token:
        return token
    from cryptography.fernet import Fernet, InvalidToken
    try:
        return Fernet(settings.encryption_key.encode()).decrypt(token.encode()).decode()
    except InvalidToken:
        logger.error("Secret decrypt failed — ENCRYPTION_KEY was likely rotated")
        raise HTTPException(500, "Server misconfiguration — contact support")


def _decrypt_shelly_key(key: str) -> str:
    """Decrypt a Fernet-encrypted Shelly auth_key, falling back to plaintext for legacy rows.

    Lenient fallback (unlike _decrypt_token) because an un-decryptable Shelly key
    just means the row predates this encryption pass — we want the active charging
    session to keep working, and the next write re-encrypts it.
    """
    if not key:
        return key
    from cryptography.fernet import Fernet, InvalidToken
    try:
        return Fernet(settings.encryption_key.encode()).decrypt(key.encode()).decode()
    except InvalidToken:
        return key  # legacy plaintext row — safe because the key still works for the Shelly API


_encrypt_shelly_key = _encrypt_token  # same at-rest primitive


def _booking_out(b: Booking, show_pin: bool = False) -> BookingOut:
    return BookingOut(
        id=b.id,
        listing_id=b.listing_id,
        listing_title=b.listing.title,
        listing_address=b.listing.address,
        buyer_id=b.buyer_id,
        buyer_name=b.buyer.full_name,
        package_kwh=b.package_kwh,
        price_per_kwh=b.price_per_kwh,
        total_eur=b.total_eur,
        vat_eur=b.vat_eur or 0,
        seller_earnings_eur=b.seller_earnings_eur,
        platform_fee_eur=b.platform_fee_eur,
        status=b.status,
        pin_code=b.pin_code if show_pin else None,
        paid_out=b.paid_out or False,
        paid_out_at=b.paid_out_at,
        scheduled_at=b.scheduled_at,
        completed_at=b.completed_at,
        notes=b.notes,
        created_at=b.created_at,
    )


async def _transfer_to_host(booking: Booking, session: AsyncSession) -> None:
    """Placeholder — payouts are processed manually via bank transfer."""
    logger.info("Booking %d: €%.2f earnings pending manual payout to host", booking.id, booking.seller_earnings_eur)


# ── Health ────────────────────────────────────────────────────────────────────

@app.get("/api/health")
async def health() -> dict:
    return {"ok": True}


# ── Auth ──────────────────────────────────────────────────────────────────────

@app.post("/api/auth/register", response_model=TokenResponse)
@limiter.limit("5/minute")
async def register(request: Request, body: RegisterRequest, session: AsyncSession = Depends(get_session)):
    if body.role not in ("buyer", "seller"):
        raise HTTPException(400, "role must be buyer or seller")
    existing = (await session.execute(select(User).where(User.email == body.email))).scalar_one_or_none()
    if existing:
        raise HTTPException(400, "Could not create account. If you already have one, please sign in.")
    user = User(
        email=body.email,
        hashed_password=hash_password(body.password),
        full_name=body.full_name,
        phone=body.phone,
        role=UserRole(body.role),
        joining_fee_paid=False if body.role == "seller" else None,
    )
    session.add(user)
    await session.commit()
    await session.refresh(user)
    # Generate verification token and send email. We store only the sha256 of
    # the token so a DB breach doesn't hand attackers usable verification links;
    # the raw token lives only in the user's inbox.
    verification_token = secrets.token_urlsafe(32)
    user.verification_token = hashlib.sha256(verification_token.encode()).hexdigest()
    user.verification_token_expires = datetime.utcnow() + timedelta(hours=24)
    await session.commit()
    asyncio.create_task(send_verification_email(user.email, user.full_name, verification_token))
    return TokenResponse(
        access_token=create_access_token(user.id, user.role),
        role=user.role,
        full_name=user.full_name,
    )


@app.post("/api/auth/login", response_model=TokenResponse)
@limiter.limit("10/minute")
async def login(request: Request, body: LoginRequest, session: AsyncSession = Depends(get_session)):
    user = (await session.execute(select(User).where(User.email == body.email))).scalar_one_or_none()
    now = datetime.utcnow()

    # Honour a prior account-level lockout. Return the same 401 string as a
    # bad-password response so the attacker can't tell locked from wrong.
    if user and user.locked_until and user.locked_until > now:
        logger.info("Login blocked (locked_until=%s) for user %d", user.locked_until, user.id)
        raise HTTPException(401, "Invalid email or password")

    if not user or not verify_password(body.password, user.hashed_password):
        if user is not None:
            user.failed_login_count = (user.failed_login_count or 0) + 1
            lockout_s = _lockout_seconds_for(user.failed_login_count)
            if lockout_s > 0:
                user.locked_until = now + timedelta(seconds=lockout_s)
                logger.info(
                    "Login lockout: user=%d count=%d locked_for=%ds",
                    user.id, user.failed_login_count, lockout_s,
                )
            await session.commit()
        raise HTTPException(401, "Invalid email or password")

    # Successful auth — clear the lockout state so legit users aren't stuck
    # after a stressful typo streak.
    if user.failed_login_count or user.locked_until:
        user.failed_login_count = 0
        user.locked_until = None
        await session.commit()

    return TokenResponse(
        access_token=create_access_token(user.id, user.role),
        role=user.role,
        full_name=user.full_name,
    )


@app.post("/api/auth/forgot-password")
@limiter.limit("3/hour")
async def forgot_password(request: Request, body: ForgotPasswordRequest, session: AsyncSession = Depends(get_session)):
    """Email a single-use password-reset link if the account exists.

    Always returns the same generic success response regardless of whether the
    email is on file — prevents account-enumeration via timing or response.
    """
    user = (await session.execute(select(User).where(User.email == body.email))).scalar_one_or_none()
    if user:
        token = secrets.token_urlsafe(32)
        user.password_reset_hash = hashlib.sha256(token.encode()).hexdigest()
        user.password_reset_expires = datetime.utcnow() + timedelta(hours=1)
        await session.commit()
        asyncio.create_task(send_password_reset_email(user.email, user.full_name, token))
    return {"ok": True}


@app.post("/api/auth/reset-password")
@limiter.limit("5/minute")
async def reset_password(request: Request, body: ResetPasswordRequest, session: AsyncSession = Depends(get_session)):
    """Consume a password-reset token: verify + TTL check, set new password, nullify token."""
    token_hash = hashlib.sha256(body.token.encode()).hexdigest()
    user = (await session.execute(
        select(User).where(User.password_reset_hash == token_hash)
    )).scalar_one_or_none()
    if not user or not user.password_reset_expires or user.password_reset_expires < datetime.utcnow():
        raise HTTPException(400, "Invalid or expired reset link")
    user.hashed_password = hash_password(body.new_password)
    user.password_reset_hash = None
    user.password_reset_expires = None
    # Invalidate every session minted before this reset (see auth.get_current_user).
    # A stolen JWT with a background refresh loop can no longer survive the reset.
    user.password_changed_at = datetime.utcnow()
    # Also clear any pending lockout so the user can log back in immediately
    # with their new password.
    user.failed_login_count = 0
    user.locked_until = None
    await session.commit()
    logger.info("Password reset completed for user %d", user.id)
    return {"ok": True}


@app.get("/api/auth/me", response_model=UserOut)
async def me(current_user: User = Depends(get_current_user)):
    return current_user


@app.post("/api/auth/logout")
async def logout():
    """Invalidate session by clearing the auth cookie (no server-side token store)."""
    response = JSONResponse({"ok": True})
    response.delete_cookie("ll_token", path="/", samesite="strict")
    return response


@app.get("/api/auth/verify-email")
@limiter.limit("30/hour")
async def verify_email(request: Request, token: str, session: AsyncSession = Depends(get_session)):
    token_hash = hashlib.sha256(token.encode()).hexdigest()
    user = (await session.execute(
        select(User).where(User.verification_token == token_hash)
    )).scalar_one_or_none()
    if (
        not user
        or not user.verification_token_expires
        or user.verification_token_expires < datetime.utcnow()
    ):
        raise HTTPException(400, "Invalid or expired verification token")
    user.email_verified = True
    user.verification_token = None
    user.verification_token_expires = None
    await session.commit()
    return {"ok": True, "email": user.email}


@app.post("/api/auth/refresh", response_model=TokenResponse)
async def refresh_token(
    current_user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
):
    """Issue a fresh access token for an authenticated user (extends session)."""
    if not current_user.is_active:
        raise HTTPException(403, "Account is suspended")
    token = create_access_token(current_user.id, current_user.role)
    return TokenResponse(
        access_token=token,
        role=current_user.role,
        full_name=current_user.full_name,
    )


# ── Listings (public) ─────────────────────────────────────────────────────────

@app.get("/api/listings", response_model=list[ListingOut])
async def list_listings(
    city: Optional[str] = None,
    session: AsyncSession = Depends(get_session),
):
    q = select(Listing).where(Listing.is_available == True)
    if city:
        q = q.where(Listing.city.ilike(f"%{city}%"))
    rows = (await session.execute(q.order_by(Listing.created_at.desc()))).scalars().all()
    result = []
    for r in rows:
        seller = await session.get(User, r.seller_id)
        result.append(await listing_out(r, seller.full_name if seller else "—", session))
    return result


@app.get("/api/listings/{listing_id}", response_model=ListingOut)
async def get_listing(listing_id: int, session: AsyncSession = Depends(get_session)):
    r = await session.get(Listing, listing_id)
    if not r:
        raise HTTPException(404, "Listing not found")
    seller = await session.get(User, r.seller_id)
    return await listing_out(r, seller.full_name if seller else "—", session)


# ── Seller endpoints ──────────────────────────────────────────────────────────

@app.post("/api/seller/listings", response_model=ListingOut)
async def create_listing(
    body: ListingCreate,
    current_user: User = Depends(require_role("seller", "admin")),
    session: AsyncSession = Depends(get_session),
):
    if current_user.joining_fee_paid is False:
        raise HTTPException(402, "Please pay the €1 host joining fee before creating a listing")
    lat, lng = await geocode(body.address, body.city, body.country)
    listing = Listing(seller_id=current_user.id, lat=lat, lng=lng, **body.model_dump())
    session.add(listing)
    await session.commit()
    await session.refresh(listing)
    return await listing_out(listing, current_user.full_name, session)


@app.put("/api/seller/profile", response_model=UserOut)
async def update_profile(
    body: ProfileUpdate,
    current_user: User = Depends(require_role("seller", "buyer", "admin")),
    session: AsyncSession = Depends(get_session),
):
    if body.full_name is not None:
        current_user.full_name = body.full_name
    if body.phone is not None:
        current_user.phone = body.phone
    await session.commit()
    await session.refresh(current_user)
    return current_user


@app.get("/api/seller/earnings", response_model=SellerEarnings)
async def seller_earnings(
    current_user: User = Depends(require_role("seller", "admin")),
    session: AsyncSession = Depends(get_session),
):
    my_listing_ids = [
        r.id for r in (await session.execute(
            select(Listing.id).where(Listing.seller_id == current_user.id)
        )).all()
    ]
    bookings = (await session.execute(
        select(Booking).where(
            Booking.listing_id.in_(my_listing_ids),
            Booking.status == BookingStatus.completed,
        )
    )).scalars().all()

    paid = sum(b.seller_earnings_eur for b in bookings if b.paid_out)
    pending = sum(b.seller_earnings_eur for b in bookings if not b.paid_out)

    return SellerEarnings(
        pending_eur=round(pending, 2),
        paid_out_eur=round(paid, 2),
        total_eur=round(pending + paid, 2),
        stripe_onboarded=False,
        stripe_account_id=None,
        joining_fee_paid=current_user.joining_fee_paid,
        payout_account_set=_host_payout_ready(current_user),
    )


# ── Host payout account (SumUp era: manual SEPA batches to host IBANs) ────────

_IBAN_SHAPE_RE = re.compile(r"[A-Z]{2}[0-9]{2}[A-Z0-9]{11,30}$")
# Latin-1 Supplement + Latin Extended covers Finnish, Swedish, etc.;
# allows apostrophe, hyphen, period, and space for compound names.
_ACCOUNT_HOLDER_RE = re.compile(r"[A-Za-zÀ-ÿ'\.\- ]{2,120}$")
# ISO-3166 alpha-2 (two uppercase letters). Checked exactly; we don't validate
# against the live country list — a typo'd country is caught at wire-time by
# the admin, not at save-time.
_COUNTRY_CODE_RE = re.compile(r"^[A-Z]{2}$")
# SEPA zone (as of 2026-11): 36 countries. Hosts in these use IBAN + SEPA batches.
# Everyone else uses payout_method='manual_intl' and is wired by the admin.
_SEPA_COUNTRIES = frozenset({
    "AT", "BE", "BG", "CH", "CY", "CZ", "DE", "DK", "EE", "ES", "FI", "FR",
    "GB", "GR", "HR", "HU", "IE", "IS", "IT", "LI", "LT", "LU", "LV", "MC",
    "MT", "NL", "NO", "PL", "PT", "RO", "SE", "SI", "SK", "SM", "VA", "AD",
})


def _normalize_iban(raw: str) -> str:
    """Strip whitespace and uppercase. Shape and mod-97 are validated separately."""
    return "".join(raw.split()).upper()


def _iban_mod97_ok(iban: str) -> bool:
    """Standard IBAN mod-97 checksum (ISO 13616). Returns True on pass."""
    # Move the first 4 chars to the end, convert letters A=10..Z=35 to digits,
    # then check the integer is 1 modulo 97.
    rearranged = iban[4:] + iban[:4]
    numeric = "".join(
        str(ord(c) - 55) if c.isalpha() else c for c in rearranged
    )
    try:
        return int(numeric) % 97 == 1
    except ValueError:
        return False


def _encrypt_iban(iban: str) -> str:
    """IBAN is at-rest sensitive — encrypt with the same Fernet key as other secrets."""
    from cryptography.fernet import Fernet
    if not settings.encryption_key:
        raise HTTPException(503, "Encryption key not configured")
    return Fernet(settings.encryption_key.encode()).encrypt(iban.encode()).decode()


def _decrypt_iban(ciphertext: str) -> str:
    from cryptography.fernet import Fernet, InvalidToken
    try:
        return Fernet(settings.encryption_key.encode()).decrypt(ciphertext.encode()).decode()
    except InvalidToken:
        # Legacy plaintext row (pre-SumUp cleanup wiped these, but a backup restore could bring one back).
        return ciphertext


def _host_payout_ready(u: User) -> bool:
    """True when the host has a complete payout destination on file.

    Shared by the A9 checkout gate, the seller earnings view, and the payouts
    pending-by-host preview — one place defines "payable".
    """
    if not u.payout_account_holder:
        return False
    method = u.payout_method or "sepa"
    if method == "sepa":
        return bool(u.iban)
    if method == "manual_intl":
        return bool(u.payout_details)
    return False


def _validate_payout_details(raw: str) -> str:
    """Normalise and sanity-check the free-text wire instructions for manual_intl hosts.

    This is intentionally liberal: we can't validate a US routing number *and*
    a Canadian transit number *and* every other country's quirks in one regex,
    and for a pilot the admin eyeballs each wire anyway. We just enforce:
      - length 20..3800 after normalising whitespace (room for Fernet overhead)
      - no control chars except TAB/LF
      - no CSV-formula leading chars (they'd leak into any export)
    """
    text = (raw or "").strip()
    if len(text) < 20:
        raise HTTPException(400, "Bank details look too short — include account number, bank name, and SWIFT/BIC.")
    if len(text) > 3800:
        raise HTTPException(400, "Bank details are too long (max 3800 chars).")
    for ch in text:
        if ord(ch) < 0x20 and ch not in ("\t", "\n"):
            raise HTTPException(400, "Bank details contain disallowed control characters.")
    if text[0] in ("=", "+", "@"):
        # Matches payouts._FORMULA_INJECTION_CHARS intent — reject at save-time
        # too so decryption never produces a formula-leading blob.
        raise HTTPException(400, "Bank details can't start with =, + or @.")
    return text


@app.get("/api/seller/payout-account", response_model=PayoutAccountOut)
async def get_payout_account(
    current_user: User = Depends(require_role("seller", "admin")),
):
    method = current_user.payout_method or "sepa"
    if method == "manual_intl":
        if not current_user.payout_details or not current_user.payout_account_holder:
            return PayoutAccountOut(is_set=False, payout_method="manual_intl")
        return PayoutAccountOut(
            account_holder=current_user.payout_account_holder,
            payout_method="manual_intl",
            payout_country=current_user.payout_country,
            payout_details_set=True,
            is_set=True,
        )
    # SEPA path
    if not current_user.iban or not current_user.payout_account_holder:
        return PayoutAccountOut(is_set=False, payout_method="sepa")
    try:
        iban = _decrypt_iban(current_user.iban)
    except Exception:
        logger.error("IBAN decrypt failed for user %d", current_user.id)
        return PayoutAccountOut(is_set=False, payout_method="sepa")
    return PayoutAccountOut(
        iban_last4=iban[-4:] if len(iban) >= 4 else iban,
        account_holder=current_user.payout_account_holder,
        payout_method="sepa",
        payout_country=current_user.payout_country,
        is_set=True,
    )


@app.put("/api/seller/payout-account", response_model=PayoutAccountOut)
async def set_payout_account(
    body: PayoutAccountIn,
    current_user: User = Depends(require_role("seller", "admin")),
    session: AsyncSession = Depends(get_session),
):
    method = (body.payout_method or "sepa").strip().lower()
    if method not in ("sepa", "manual_intl"):
        raise HTTPException(400, "payout_method must be 'sepa' or 'manual_intl'.")

    holder = (body.account_holder or "").strip()
    if not _ACCOUNT_HOLDER_RE.match(holder):
        raise HTTPException(
            400,
            "Account holder name must be 2–120 characters, letters and standard punctuation only.",
        )

    country = (body.payout_country or "").strip().upper() or None
    if country is not None and not _COUNTRY_CODE_RE.match(country):
        raise HTTPException(400, "payout_country must be a 2-letter ISO country code.")

    if method == "sepa":
        if not body.iban:
            raise HTTPException(400, "IBAN is required for SEPA payouts.")
        iban = _normalize_iban(body.iban)
        if not _IBAN_SHAPE_RE.match(iban):
            raise HTTPException(400, "IBAN looks malformed — check the country prefix and length.")
        if not _iban_mod97_ok(iban):
            raise HTTPException(400, "IBAN checksum failed — please double-check the number.")
        # Guardrail: if a country is specified, it has to be in SEPA.
        if country and country not in _SEPA_COUNTRIES:
            raise HTTPException(400, f"{country} is not a SEPA country — switch payout_method to 'manual_intl'.")
        if not country:
            country = iban[:2]  # IBAN starts with the country code
        current_user.iban = _encrypt_iban(iban)
        current_user.payout_details = None  # clear the other mode's blob
        current_user.payout_method = "sepa"
        current_user.payout_country = country
        current_user.payout_account_holder = holder
        await session.commit()
        return PayoutAccountOut(
            iban_last4=iban[-4:],
            account_holder=holder,
            payout_method="sepa",
            payout_country=country,
            is_set=True,
        )

    # manual_intl
    if not country:
        raise HTTPException(400, "payout_country is required for manual international payouts.")
    if country in _SEPA_COUNTRIES:
        raise HTTPException(400, f"{country} is in SEPA — use payout_method='sepa' with an IBAN instead.")
    if not body.payout_details:
        raise HTTPException(400, "Bank wire details are required for manual international payouts.")
    details = _validate_payout_details(body.payout_details)
    current_user.payout_details = _encrypt_iban(details)  # same Fernet envelope
    current_user.iban = None
    current_user.payout_method = "manual_intl"
    current_user.payout_country = country
    current_user.payout_account_holder = holder
    await session.commit()
    return PayoutAccountOut(
        account_holder=holder,
        payout_method="manual_intl",
        payout_country=country,
        payout_details_set=True,
        is_set=True,
    )


@app.get("/api/seller/listings", response_model=list[ListingOut])
async def my_listings(
    current_user: User = Depends(require_role("seller", "admin")),
    session: AsyncSession = Depends(get_session),
):
    rows = (await session.execute(
        select(Listing).where(Listing.seller_id == current_user.id)
    )).scalars().all()
    return [await listing_out(r, current_user.full_name, session) for r in rows]


@app.put("/api/seller/listings/{listing_id}", response_model=ListingOut)
async def update_listing(
    listing_id: int,
    body: ListingCreate,
    current_user: User = Depends(require_role("seller", "admin")),
    session: AsyncSession = Depends(get_session),
):
    listing = await session.get(Listing, listing_id)
    if not listing or listing.seller_id != current_user.id:
        raise HTTPException(404, "Listing not found")
    listing.title = body.title
    listing.description = body.description
    listing.address = body.address
    listing.city = body.city
    listing.country = body.country
    listing.charger_type = body.charger_type  # type: ignore[assignment]
    listing.max_power_kw = body.max_power_kw
    listing.price_per_kwh = body.price_per_kwh
    listing.instructions = body.instructions
    # Invalidate geocode so it refreshes on next geocode run
    listing.lat = None
    listing.lng = None
    await session.commit()
    return await listing_out(listing, current_user.full_name, session)


@app.put("/api/seller/listings/{listing_id}/toggle")
async def toggle_listing(
    listing_id: int,
    current_user: User = Depends(require_role("seller", "admin")),
    session: AsyncSession = Depends(get_session),
):
    listing = await session.get(Listing, listing_id)
    if not listing or listing.seller_id != current_user.id:
        raise HTTPException(404, "Listing not found")
    listing.is_available = not listing.is_available
    await session.commit()
    return {"is_available": listing.is_available}


@app.get("/api/seller/bookings", response_model=list[BookingOut])
async def seller_bookings(
    current_user: User = Depends(require_role("seller", "admin")),
    session: AsyncSession = Depends(get_session),
):
    my_listing_ids = [
        r.id for r in (await session.execute(
            select(Listing.id).where(Listing.seller_id == current_user.id)
        )).all()
    ]
    rows = (await session.execute(
        select(Booking).where(Booking.listing_id.in_(my_listing_ids)).order_by(Booking.created_at.desc())
    )).scalars().all()
    result = []
    for b in rows:
        b.listing = await session.get(Listing, b.listing_id)
        b.buyer = await session.get(User, b.buyer_id)
        result.append(_booking_out(b, show_pin=True))
    return result


@app.put("/api/seller/bookings/{booking_id}/complete")
async def complete_booking(
    booking_id: int,
    current_user: User = Depends(require_role("seller", "admin")),
    session: AsyncSession = Depends(get_session),
):
    b = await session.get(Booking, booking_id)
    if not b:
        raise HTTPException(404, "Booking not found")
    listing = await session.get(Listing, b.listing_id)
    if listing.seller_id != current_user.id:
        raise HTTPException(403, "Not your listing")
    if b.status not in (BookingStatus.confirmed, BookingStatus.active):
        raise HTTPException(400, f"Booking must be confirmed or active to complete (current: {b.status})")
    b.status = BookingStatus.completed
    b.completed_at = datetime.utcnow()
    await session.commit()

    # Automatic €1 joining-fee refund on this host's first completed booking.
    # Guarded against concurrent complete-booking calls with an optimistic lock:
    # we flip joining_fee_refunded_at and commit BEFORE calling SumUp, so a
    # second coroutine finds refunded_at already set and short-circuits. On a
    # refund API failure we revert the field so the next completion retries.
    if (
        current_user.joining_fee_paid is True
        and current_user.joining_fee_transaction_id
        and current_user.joining_fee_refunded_at is None
    ):
        txn_id = current_user.joining_fee_transaction_id
        # Re-fetch with FOR UPDATE so the losing racer sees the flipped
        # refunded_at and bails out, rather than racing on stale state.
        locked = (await session.execute(
            select(User).where(User.id == current_user.id).with_for_update()
        )).scalar_one_or_none()
        if locked and locked.joining_fee_refunded_at is None:
            tentative_at = datetime.utcnow()
            locked.joining_fee_refunded_at = tentative_at
            await session.commit()
            try:
                await sumup.refund(txn_id, amount_eur=Decimal("1.00"))
                logger.info(
                    "Joining fee refunded to user %d (txn=%s) on first completed booking %d",
                    current_user.id, txn_id, b.id,
                )
            except Exception as exc:
                # Revert so the next completion can retry. Fetch fresh in case
                # another request has touched the row in the meantime.
                revert = await session.get(User, current_user.id)
                if revert and revert.joining_fee_refunded_at == tentative_at:
                    revert.joining_fee_refunded_at = None
                    await session.commit()
                logger.error(
                    "Joining fee refund failed for user %d (txn=%s): %s — will retry on next completion",
                    current_user.id, txn_id, exc,
                )

    return {"status": "completed"}


# ── Buyer endpoints ───────────────────────────────────────────────────────────

@app.post("/api/bookings")
async def create_booking(_: Request):
    # Deprecated: all bookings must go through /api/checkout → Stripe payment flow
    raise HTTPException(410, "Use POST /api/checkout to create bookings")


@app.get("/api/buyer/bookings", response_model=list[BookingOut])
async def buyer_bookings(
    current_user: User = Depends(require_role("buyer", "seller", "admin")),
    session: AsyncSession = Depends(get_session),
):
    rows = (await session.execute(
        select(Booking).where(Booking.buyer_id == current_user.id).order_by(Booking.created_at.desc())
    )).scalars().all()
    result = []
    for b in rows:
        b.listing = await session.get(Listing, b.listing_id)
        b.buyer = current_user
        result.append(_booking_out(b, show_pin=True))
    return result


# ── Admin endpoints ───────────────────────────────────────────────────────────

@app.get("/api/admin/stats", response_model=PlatformStats)
async def admin_stats(
    _: User = Depends(require_role("admin")),
    session: AsyncSession = Depends(get_session),
):
    total_users = (await session.execute(select(func.count(User.id)))).scalar_one()
    total_sellers = (await session.execute(select(func.count(User.id)).where(User.role == UserRole.seller))).scalar_one()
    total_buyers = (await session.execute(select(func.count(User.id)).where(User.role == UserRole.buyer))).scalar_one()
    total_listings = (await session.execute(select(func.count(Listing.id)))).scalar_one()
    active_listings = (await session.execute(select(func.count(Listing.id)).where(Listing.is_available == True))).scalar_one()
    total_bookings = (await session.execute(select(func.count(Booking.id)))).scalar_one()
    completed = (await session.execute(select(func.count(Booking.id)).where(Booking.status == BookingStatus.completed))).scalar_one()
    kwh = (await session.execute(
        select(func.sum(Booking.package_kwh)).where(Booking.status == BookingStatus.completed)
    )).scalar_one() or 0
    revenue = (await session.execute(
        select(func.sum(Booking.total_eur)).where(Booking.status == BookingStatus.completed)
    )).scalar_one() or 0
    platform = (await session.execute(
        select(func.sum(Booking.platform_fee_eur)).where(Booking.status == BookingStatus.completed)
    )).scalar_one() or 0

    return PlatformStats(
        total_users=total_users,
        total_sellers=total_sellers,
        total_buyers=total_buyers,
        total_listings=total_listings,
        active_listings=active_listings,
        total_bookings=total_bookings,
        completed_bookings=completed,
        total_kwh_delivered=float(kwh),
        total_revenue_eur=float(revenue),
        platform_earnings_eur=float(platform),
    )


@app.get("/api/admin/users", response_model=list[UserOut])
async def admin_users(
    _: User = Depends(require_role("admin")),
    session: AsyncSession = Depends(get_session),
):
    rows = (await session.execute(select(User).order_by(User.created_at.desc()))).scalars().all()
    return rows


@app.get("/api/admin/listings", response_model=list[ListingOut])
async def admin_listings(
    _: User = Depends(require_role("admin")),
    session: AsyncSession = Depends(get_session),
):
    rows = (await session.execute(select(Listing).order_by(Listing.created_at.desc()))).scalars().all()
    result = []
    for r in rows:
        seller = await session.get(User, r.seller_id)
        result.append(ListingOut(
            **{c.key: getattr(r, c.key) for c in r.__table__.columns},
            seller_name=seller.full_name if seller else "—",
        ))
    return result


@app.get("/api/admin/bookings", response_model=list[BookingOut])
async def admin_bookings(
    _: User = Depends(require_role("admin")),
    session: AsyncSession = Depends(get_session),
):
    rows = (await session.execute(select(Booking).order_by(Booking.created_at.desc()))).scalars().all()
    result = []
    for b in rows:
        b.listing = await session.get(Listing, b.listing_id)
        b.buyer = await session.get(User, b.buyer_id)
        result.append(_booking_out(b, show_pin=True))
    return result


@app.post("/api/admin/bookings/{booking_id}/retry-transfer")
async def admin_retry_transfer(
    booking_id: int,
    _: User = Depends(require_role("admin")),
    session: AsyncSession = Depends(get_session),
):
    """Mark a booking as manually paid out."""
    b = await session.get(Booking, booking_id)
    if not b:
        raise HTTPException(404, "Booking not found")
    if b.paid_out:
        return {"detail": "Already marked as paid out", "booking_id": b.id}
    b.paid_out = True
    b.paid_out_at = datetime.utcnow()
    await session.commit()
    return {"booking_id": b.id, "amount_eur": b.seller_earnings_eur, "paid_out": True}


@app.post("/api/admin/geocode-listings")
async def geocode_all_listings(
    _: User = Depends(require_role("admin")),
    session: AsyncSession = Depends(get_session),
):
    """Geocode all listings that are missing lat/lng."""
    rows = (await session.execute(
        select(Listing).where(Listing.lat == None)
    )).scalars().all()

    updated, failed = 0, 0
    for listing in rows:
        lat, lng = await geocode(listing.address, listing.city, listing.country)
        if lat and lng:
            listing.lat = lat
            listing.lng = lng
            updated += 1
        else:
            failed += 1
        import asyncio
        await asyncio.sleep(1)  # Nominatim rate limit: 1 req/sec

    await session.commit()
    return {"updated": updated, "failed": failed, "total": len(rows)}


@app.put("/api/admin/listings/{listing_id}/toggle")
async def admin_toggle_listing(
    listing_id: int,
    _: User = Depends(require_role("admin")),
    session: AsyncSession = Depends(get_session),
):
    listing = await session.get(Listing, listing_id)
    if not listing:
        raise HTTPException(404, "Listing not found")
    listing.is_available = not listing.is_available
    await session.commit()
    return {"is_available": listing.is_available}


@app.put("/api/admin/users/{user_id}/toggle")
async def toggle_user(
    user_id: int,
    _: User = Depends(require_role("admin")),
    session: AsyncSession = Depends(get_session),
):
    user = await session.get(User, user_id)
    if not user:
        raise HTTPException(404, "User not found")
    user.is_active = not user.is_active
    await session.commit()
    return {"is_active": user.is_active}


@app.put("/api/admin/bookings/{booking_id}/confirm")
async def admin_confirm_booking(
    booking_id: int,
    force: bool = False,
    _: User = Depends(require_role("admin")),
    session: AsyncSession = Depends(get_session),
):
    """Admin fallback: confirm a pending booking and mint a PIN.

    Verifies the SumUp checkout is actually paid before confirming, so a phished
    or XSS'd admin session can't hand out free charging. Pass ?force=true to
    override (logged as a warning for audit).
    """
    b = await session.get(Booking, booking_id)
    if not b:
        raise HTTPException(404, "Booking not found")
    if b.status != BookingStatus.pending:
        raise HTTPException(400, f"Booking is already {b.status}")

    if not force:
        if not b.stripe_session_id:
            raise HTTPException(400, "Booking has no SumUp checkout ID — pass ?force=true to override")
        try:
            result = await sumup.verify_checkout(b.stripe_session_id)
        except Exception as exc:
            logger.error("Admin confirm: SumUp fetch failed for booking %d: %s", b.id, exc)
            raise HTTPException(502, "Could not verify payment with SumUp")
        if result.get("status") != "PAID":
            raise HTTPException(409, f"SumUp checkout status is {result.get('status')} — not confirming")
    else:
        logger.warning("Admin FORCE-confirm booking %d — payment NOT verified", b.id)

    b.status = BookingStatus.confirmed
    b.pin_code = _gen_pin()
    await session.commit()
    return {"status": "confirmed", "pin_code": b.pin_code, "booking_id": b.id, "forced": force}


# ── Admin: host payout batches (SEPA) ─────────────────────────────────────────

@app.get("/api/admin/payouts/pending")
async def admin_payouts_pending(
    _: User = Depends(require_role("admin")),
    session: AsyncSession = Depends(get_session),
) -> list[dict]:
    """Preview of what each host is owed. Hosts without an IBAN still appear
    so the admin can see the amount being held and nudge them to add one."""
    rows = await payouts.pending_by_host(session)
    # Hide internal booking_ids array from the preview; batch generation reads
    # it fresh at commit time anyway.
    return [
        {k: v for k, v in r.items() if k != "booking_ids"}
        for r in rows
    ]


@app.post("/api/admin/payouts/generate")
async def admin_payouts_generate(
    _: User = Depends(require_role("admin")),
    session: AsyncSession = Depends(get_session),
) -> list[HostPayoutOut]:
    """Create a HostPayout row for every payable host (one per host, not per
    booking). Locks the included booking IDs so a second call doesn't
    double-batch them. Idempotent in effect: with nothing new to pay out it
    returns an empty list."""
    batch = await payouts.generate_batch(session, decrypt_iban=_decrypt_iban)
    return [await _host_payout_out(p, session) for p in batch]


@app.get("/api/admin/payouts")
async def admin_payouts_list(
    status: Optional[str] = None,
    _: User = Depends(require_role("admin")),
    session: AsyncSession = Depends(get_session),
) -> list[HostPayoutOut]:
    stmt = select(HostPayout).order_by(HostPayout.created_at.desc())
    if status:
        stmt = stmt.where(HostPayout.status == status)
    rows = (await session.execute(stmt)).scalars().all()
    return [await _host_payout_out(p, session) for p in rows]


@app.get("/api/admin/payouts/{payout_id}/csv")
async def admin_payouts_csv(
    payout_id: int,
    _: User = Depends(require_role("admin")),
    session: AsyncSession = Depends(get_session),
):
    """Download a single-row CSV for bank upload. Admin can fetch multiple
    and concatenate, or just upload one at a time for low volume."""
    p = await session.get(HostPayout, payout_id)
    if not p:
        raise HTTPException(404, "Payout batch not found")
    host = await session.get(User, p.host_id)
    csv_text = payouts.to_csv(
        [p],
        decrypt_iban=_decrypt_iban,
        host_lookup={p.host_id: host} if host else {},
    )
    return Response(
        content=csv_text,
        media_type="text/csv",
        headers={"Content-Disposition": f'attachment; filename="payout-{p.id}.csv"'},
    )


@app.get("/api/admin/payouts/csv")
async def admin_payouts_pending_csv(
    _: User = Depends(require_role("admin")),
    session: AsyncSession = Depends(get_session),
):
    """Multi-row CSV: every currently-pending batch in one file. Convenient
    for a weekly run where the admin generates and then downloads once."""
    rows = (await session.execute(
        select(HostPayout).where(HostPayout.status == "pending").order_by(HostPayout.id)
    )).scalars().all()
    if not rows:
        return Response(content="IBAN,Name,Amount,Currency,Reference\n", media_type="text/csv")
    host_ids = {r.host_id for r in rows}
    hosts = (await session.execute(select(User).where(User.id.in_(host_ids)))).scalars().all()
    host_lookup = {h.id: h for h in hosts}
    csv_text = payouts.to_csv(rows, decrypt_iban=_decrypt_iban, host_lookup=host_lookup)
    today = datetime.utcnow().strftime("%Y-%m-%d")
    return Response(
        content=csv_text,
        media_type="text/csv",
        headers={"Content-Disposition": f'attachment; filename="payouts-{today}.csv"'},
    )


@app.post("/api/admin/payouts/{payout_id}/mark-paid")
async def admin_payouts_mark_paid(
    payout_id: int,
    body: MarkPaidIn,
    _: User = Depends(require_role("admin")),
    session: AsyncSession = Depends(get_session),
) -> HostPayoutOut:
    p = await session.get(HostPayout, payout_id)
    if not p:
        raise HTTPException(404, "Payout batch not found")
    if p.status == "paid":
        return await _host_payout_out(p, session)
    p = await payouts.mark_batch_paid(session, p, sepa_reference=body.sepa_reference)
    return await _host_payout_out(p, session)


async def _host_payout_out(p: HostPayout, session: AsyncSession) -> HostPayoutOut:
    import json as _json
    try:
        booking_count = len(_json.loads(p.booking_ids_json or "[]"))
    except _json.JSONDecodeError:
        booking_count = 0
    host = await session.get(User, p.host_id)
    return HostPayoutOut(
        id=p.id,
        host_id=p.host_id,
        host_name=host.full_name if host else None,
        period_start=p.period_start,
        period_end=p.period_end,
        net_eur=p.net_eur,
        booking_count=booking_count,
        iban_last4=p.iban_last4,
        account_holder=p.account_holder,
        payout_method=(host.payout_method if host else None) or "sepa",
        payout_country=host.payout_country if host else None,
        status=p.status,
        sepa_reference=p.sepa_reference,
        created_at=p.created_at,
        paid_at=p.paid_at,
    )


@app.get("/api/admin/payouts/{payout_id}/wire-details")
async def admin_payout_wire_details(
    payout_id: int,
    _: User = Depends(require_role("admin")),
    session: AsyncSession = Depends(get_session),
) -> dict:
    """Reveal the decrypted wire instructions for a manual_intl payout batch.

    Deliberately narrow: only returns details for one batch at a time, and only
    for batches whose host is on payout_method='manual_intl'. The admin uses
    this to populate the fields in their business bank's international-wire
    form. Access is admin-only; every call is logged so there's an audit trail
    of who looked at wire details and when.
    """
    p = await session.get(HostPayout, payout_id)
    if not p:
        raise HTTPException(404, "Payout batch not found")
    host = await session.get(User, p.host_id)
    if not host:
        raise HTTPException(404, "Host not found")
    if (host.payout_method or "sepa") != "manual_intl":
        raise HTTPException(400, "This host is on SEPA — use the CSV download instead.")
    if not host.payout_details:
        raise HTTPException(404, "No wire details on file for this host.")
    try:
        details = _decrypt_iban(host.payout_details)
    except Exception:
        logger.error("payout_details decrypt failed for host %d (payout %d)", host.id, p.id)
        raise HTTPException(500, "Could not decrypt wire details.")
    logger.info(
        "admin wire-details reveal: payout=%d host=%d country=%s net=%.2f",
        p.id, host.id, host.payout_country, p.net_eur,
    )
    return {
        "payout_id": p.id,
        "host_id": host.id,
        "host_name": host.full_name,
        "account_holder": host.payout_account_holder,
        "payout_country": host.payout_country,
        "net_eur": p.net_eur,
        "currency": "EUR",
        "reference": f"ChargedEV payout #{p.id}",
        "wire_details": details,
    }


# ── Host joining fee ─────────────────────────────────────────────────────────

@app.post("/api/seller/joining-fee")
async def create_joining_fee_payment(
    current_user: User = Depends(require_role("seller", "admin")),
):
    if current_user.joining_fee_paid is not False:
        return {"status": "already_paid"}
    try:
        # Suffix the reference so each click mints a fresh checkout. SumUp
        # treats a repeated reference as idempotent, so without this a second
        # attempt lands the user on a stale/expired checkout URL that goes
        # nowhere.
        ref = f"joining-fee-{current_user.id}-{secrets.token_hex(4)}"
        checkout = await sumup.create_checkout(
            checkout_reference=ref,
            amount_eur=Decimal("1.00"),
            description="ChargedEV host joining fee",
            # Just a return marker — the real checkout_id lives in the frontend's
            # sessionStorage (set before redirect) because SumUp won't let us
            # stamp the id into the redirect URL after creation. Passing user.id
            # here was a leftover bug: the frontend read it as a payment_id and
            # every verify call 502'd silently; the webhook saved the UX.
            redirect_url=f"{settings.frontend_url}/sell/dashboard?joining_fee_return=1",
            webhook_url=f"{settings.backend_url}/api/webhooks/sumup",
            customer_email=current_user.email,
        )
    except httpx.HTTPStatusError as exc:
        logger.error(
            "Joining fee checkout creation failed: user=%d ref=%s status=%s body=%s",
            current_user.id, ref, exc.response.status_code, _scrub(exc.response.text),
        )
        raise HTTPException(502, "Could not create payment. Please try again.")
    except Exception as exc:
        logger.error("Joining fee checkout creation failed: user=%d ref=%s err=%s",
                     current_user.id, ref, exc)
        raise HTTPException(502, "Could not create payment. Please try again.")
    logger.info(
        "Joining fee checkout created: user=%d ref=%s checkout_id=%s",
        current_user.id, ref, checkout.get("checkout_id"),
    )
    if not checkout.get("checkout_id") or not checkout.get("checkout_url"):
        raise HTTPException(502, "Payment provider returned an unexpected response.")
    return {"checkout_url": checkout["checkout_url"], "payment_id": checkout["checkout_id"]}


@app.get("/api/seller/joining-fee/verify")
async def verify_joining_fee(
    payment_id: str,
    current_user: User = Depends(require_role("seller", "admin")),
    session: AsyncSession = Depends(get_session),
):
    if current_user.joining_fee_paid:
        return {"paid": True}
    try:
        result = await sumup.verify_checkout(payment_id)
    except Exception as exc:
        logger.error("Joining fee verify failed: %s", exc)
        raise HTTPException(502, "Could not verify payment")
    # The checkout_reference is "joining-fee-{user_id}-{nonce}"; prefix-check
    # so a user can't verify a different user's checkout.
    expected_prefix = f"joining-fee-{current_user.id}-"
    actual_ref = (result.get("raw") or {}).get("checkout_reference") or ""
    if not actual_ref.startswith(expected_prefix):
        raise HTTPException(403, "Payment does not belong to this account")
    if result.get("status") == "PAID":
        current_user.joining_fee_paid = True
        # Capture the SumUp transaction id so we can later refund via SumUp API.
        txn_id = result.get("transaction_id")
        if txn_id and not current_user.joining_fee_transaction_id:
            current_user.joining_fee_transaction_id = txn_id
        await session.commit()
        logger.info("Joining fee paid by user %d (txn=%s)", current_user.id, txn_id)
    return {"paid": current_user.joining_fee_paid is True}


# ── SumUp checkout ────────────────────────────────────────────────────────────

@app.post("/api/checkout")
async def create_checkout(
    body: BookingCreate,
    current_user: User = Depends(require_role("buyer", "seller", "admin")),
    session: AsyncSession = Depends(get_session),
):
    if not current_user.email_verified:
        raise HTTPException(403, "Please verify your email address before booking")
    if body.package_kwh not in PACKAGES_KWH:
        raise HTTPException(422, f"Package must be one of {PACKAGES_KWH} kWh")
    listing = await session.get(Listing, body.listing_id)
    if not listing or not listing.is_available:
        raise HTTPException(404, "Listing not found or unavailable")

    # A9: block booking a charger whose host hasn't configured a payout
    # destination. SEPA hosts need iban+holder; manual_intl hosts need
    # payout_details+holder. Either way, without this the host's share would
    # sit in pending forever.
    host = await session.get(User, listing.seller_id)
    if not host or not _host_payout_ready(host):
        raise HTTPException(409, "This host hasn't finished payout setup yet. Please try another charger.")

    total, vat, fee, earnings = _calc_amounts(body.package_kwh, listing.price_per_kwh)

    # Create pending booking (no PIN yet — assigned after payment)
    booking = Booking(
        listing_id=listing.id,
        buyer_id=current_user.id,
        package_kwh=body.package_kwh,
        price_per_kwh=listing.price_per_kwh,
        total_eur=total,
        vat_eur=vat,
        seller_earnings_eur=earnings,
        platform_fee_eur=fee,
        status=BookingStatus.pending,
        notes=body.notes,
    )
    session.add(booking)
    await session.flush()  # get booking.id before commit

    # SumUp acquiring: the full gross settles to ChargedEV's merchant account.
    # VAT and the host's 80%-of-net earnings are routed by us afterwards —
    # VAT to Verohallinto, host share via weekly SEPA batches to their IBAN.
    host = await session.get(User, listing.seller_id)
    if not host:
        raise HTTPException(409, "This listing has no host. Please try another charger.")

    try:
        checkout = await sumup.create_checkout(
            checkout_reference=f"booking-{booking.id}",
            amount_eur=Decimal(f"{total:.2f}"),
            description=f"{body.package_kwh} kWh — {listing.title}",
            redirect_url=f"{settings.frontend_url}/charge/success?booking_id={booking.id}",
            webhook_url=f"{settings.backend_url}/api/webhooks/sumup",
            customer_email=current_user.email,
        )
    except HTTPException:
        raise
    except httpx.HTTPStatusError as exc:
        logger.error("SumUp checkout creation failed: status=%s body=%s",
                     exc.response.status_code, _scrub(exc.response.text))
        raise HTTPException(502, "Could not create payment. Please try again.")
    except Exception as exc:
        logger.error("SumUp checkout creation failed: %s", exc)
        raise HTTPException(502, "Could not create payment. Please try again.")

    if not checkout.get("checkout_id") or not checkout.get("checkout_url"):
        logger.error("SumUp checkout response missing fields: %s", checkout)
        raise HTTPException(502, "Payment provider returned an unexpected response.")

    booking.stripe_session_id = checkout["checkout_id"]  # reuse column for the SumUp checkout ID
    # Money lands in ChargedEV's account, not the host's — paid_out tracks the
    # weekly SEPA transfer to the host, which happens separately.
    booking.paid_out = False
    booking.paid_out_at = None
    await session.commit()

    return {"checkout_url": checkout["checkout_url"], "booking_id": booking.id}


# ── Shelly Premium host endpoints ─────────────────────────────────────────────

@app.post("/api/seller/shelly/{listing_id}")
@limiter.limit("10/minute")
async def shelly_configure(
    request: Request,
    listing_id: int,
    body: ShellyConfigRequest,
    current_user: User = Depends(require_role("seller", "admin")),
    session: AsyncSession = Depends(get_session),
):
    listing = await session.get(Listing, listing_id)
    if not listing or listing.seller_id != current_user.id:
        raise HTTPException(404, "Listing not found")
    # Validate by calling device status
    client = ShellyClient(body.server, body.auth_key, body.device_id)
    ds = await client.status()
    if ds is None:
        raise HTTPException(400, "Could not connect to Shelly device. Check device ID, auth key, and server.")
    listing.shelly_device_id = body.device_id
    listing.shelly_auth_key = _encrypt_shelly_key(body.auth_key)
    listing.shelly_server = body.server
    await session.commit()
    return ShellyStatusOut(
        connected=True,
        device_id=body.device_id,
        relay_on=_shelly_relay_on(ds),
        power_w=_shelly_power_w(ds),
        energy_total_wh=_shelly_energy_wh(ds),
    )


@app.get("/api/seller/shelly/{listing_id}", response_model=ShellyStatusOut)
async def shelly_status(
    listing_id: int,
    current_user: User = Depends(require_role("seller", "admin")),
    session: AsyncSession = Depends(get_session),
):
    listing = await session.get(Listing, listing_id)
    if not listing or listing.seller_id != current_user.id:
        raise HTTPException(404, "Listing not found")
    if not listing.shelly_device_id:
        return ShellyStatusOut(connected=False, error="No Shelly device configured")
    client = ShellyClient(listing.shelly_server, _decrypt_shelly_key(listing.shelly_auth_key), listing.shelly_device_id)
    ds = await client.status()
    if ds is None:
        return ShellyStatusOut(connected=False, device_id=listing.shelly_device_id, error="Device offline or unreachable")
    return ShellyStatusOut(
        connected=True,
        device_id=listing.shelly_device_id,
        relay_on=_shelly_relay_on(ds),
        power_w=_shelly_power_w(ds),
        energy_total_wh=_shelly_energy_wh(ds),
    )


@app.delete("/api/seller/shelly/{listing_id}")
async def shelly_disconnect(
    listing_id: int,
    current_user: User = Depends(require_role("seller", "admin")),
    session: AsyncSession = Depends(get_session),
):
    listing = await session.get(Listing, listing_id)
    if not listing or listing.seller_id != current_user.id:
        raise HTTPException(404, "Listing not found")
    listing.shelly_device_id = None
    listing.shelly_auth_key = None
    listing.shelly_server = None
    await session.commit()
    return {"ok": True}


# ── OCPP endpoints ────────────────────────────────────────────────────────────

@app.websocket("/ocpp/{charge_point_id}")
async def ocpp_websocket(websocket: WebSocket, charge_point_id: str):
    """WebSocket endpoint for OCPP 1.6 charge points to connect to.

    Requires `?token=<cp.auth_token>` on the URL. The charge_point_id itself is
    not a secret — it travels in UI responses and intermediary logs — so an
    unauthenticated socket would let anyone who learned the id hijack the
    wallbox and send fake StopTransaction / MeterValues RPCs.
    """
    token = websocket.query_params.get("token", "")
    from .db import async_session as _as
    async with _as() as sess:
        db_cp = (await sess.execute(
            select(OcppChargePoint).where(OcppChargePoint.charge_point_id == charge_point_id)
        )).scalar_one_or_none()
    if not db_cp:
        logger.warning("OCPP: rejected unknown charge point %s", charge_point_id)
        await websocket.close(code=4001, reason="Unknown charge point")
        return
    if not db_cp.auth_token:
        logger.warning("OCPP: charge point %s has no auth_token — host must re-register", charge_point_id)
        await websocket.close(code=4401, reason="Re-register required")
        return
    if not token or not secrets.compare_digest(token, db_cp.auth_token):
        logger.warning("OCPP: rejected %s — bad or missing token", charge_point_id)
        await websocket.close(code=4401, reason="Unauthorized")
        return
    if charge_point_id in _ocpp_connections:
        logger.warning("OCPP: charge point %s reconnecting — displacing existing connection", charge_point_id)

    await websocket.accept(subprotocol="ocpp1.6")
    cp = ChargedEVChargePoint(charge_point_id, websocket)
    _ocpp_connections[charge_point_id] = cp
    logger.info("OCPP charge point connected: %s", charge_point_id)
    # Update DB status to online
    async with _as() as sess:
        db_cp = (await sess.execute(
            select(OcppChargePoint).where(OcppChargePoint.charge_point_id == charge_point_id)
        )).scalar_one_or_none()
        if db_cp:
            db_cp.status = "available"
            await sess.commit()
    try:
        await cp.start()
    except WebSocketDisconnect:
        pass
    except Exception as exc:
        logger.warning("OCPP charge point %s disconnected: %s", charge_point_id, exc)
    finally:
        _ocpp_connections.pop(charge_point_id, None)
        logger.info("OCPP charge point disconnected: %s", charge_point_id)
        async with _as() as sess:
            db_cp = (await sess.execute(
                select(OcppChargePoint).where(OcppChargePoint.charge_point_id == charge_point_id)
            )).scalar_one_or_none()
            if db_cp:
                db_cp.status = "offline"
                await sess.commit()


@app.post("/api/seller/ocpp/{listing_id}", response_model=OcppChargePointOut)
async def ocpp_register(
    listing_id: int,
    body: OcppRegisterRequest,
    current_user: User = Depends(require_role("seller", "admin")),
    session: AsyncSession = Depends(get_session),
):
    """Register an OCPP charge point for a listing."""
    listing = await session.get(Listing, listing_id)
    if not listing or listing.seller_id != current_user.id:
        raise HTTPException(404, "Listing not found")
    # Check charge_point_id not already used by another listing
    existing = (await session.execute(
        select(OcppChargePoint).where(OcppChargePoint.charge_point_id == body.charge_point_id)
    )).scalar_one_or_none()
    if existing and existing.listing_id != listing_id:
        raise HTTPException(400, "This charge point ID is already registered to another listing")
    # Upsert and (re)mint a per-charge-point secret so the WebSocket endpoint
    # can distinguish the real wallbox from someone who merely learned its id.
    cp = existing or OcppChargePoint(listing_id=listing_id, charge_point_id=body.charge_point_id)
    cp.charge_point_id = body.charge_point_id
    cp.auth_token = secrets.token_urlsafe(32)
    if not existing:
        session.add(cp)
    listing.ocpp_charge_point_id = body.charge_point_id
    await session.commit()
    base = settings.backend_url.replace("https://", "wss://").replace("http://", "ws://")
    ws_url = f"{base}/ocpp/{body.charge_point_id}?token={cp.auth_token}"
    return OcppChargePointOut(
        charge_point_id=cp.charge_point_id,
        vendor=cp.vendor,
        model=cp.model,
        status=cp.status if cp.charge_point_id in _ocpp_connections else "offline",
        last_heartbeat=cp.last_heartbeat,
        ws_url=ws_url,
    )


@app.get("/api/seller/ocpp/{listing_id}", response_model=OcppChargePointOut)
async def ocpp_status(
    listing_id: int,
    current_user: User = Depends(require_role("seller", "admin")),
    session: AsyncSession = Depends(get_session),
):
    listing = await session.get(Listing, listing_id)
    if not listing or listing.seller_id != current_user.id:
        raise HTTPException(404, "Listing not found")
    if not listing.ocpp_charge_point_id:
        raise HTTPException(404, "No OCPP charge point registered")
    cp = (await session.execute(
        select(OcppChargePoint).where(OcppChargePoint.charge_point_id == listing.ocpp_charge_point_id)
    )).scalar_one_or_none()
    if not cp:
        raise HTTPException(404, "Charge point not found")
    base = settings.backend_url.replace("https://", "wss://").replace("http://", "ws://")
    # Only include the token in the URL if one has been minted — hosts whose
    # charge point predates the auth-token column must re-register to pair it.
    ws_url = f"{base}/ocpp/{cp.charge_point_id}"
    if cp.auth_token:
        ws_url += f"?token={cp.auth_token}"
    return OcppChargePointOut(
        charge_point_id=cp.charge_point_id,
        vendor=cp.vendor,
        model=cp.model,
        status=cp.status if cp.charge_point_id in _ocpp_connections else "offline",
        last_heartbeat=cp.last_heartbeat,
        ws_url=ws_url,
    )


@app.delete("/api/seller/ocpp/{listing_id}")
async def ocpp_unregister(
    listing_id: int,
    current_user: User = Depends(require_role("seller", "admin")),
    session: AsyncSession = Depends(get_session),
):
    listing = await session.get(Listing, listing_id)
    if not listing or listing.seller_id != current_user.id:
        raise HTTPException(404, "Listing not found")
    if listing.ocpp_charge_point_id:
        cp = (await session.execute(
            select(OcppChargePoint).where(OcppChargePoint.charge_point_id == listing.ocpp_charge_point_id)
        )).scalar_one_or_none()
        if cp:
            await session.delete(cp)
        listing.ocpp_charge_point_id = None
        await session.commit()
    return {"ok": True}


def _summarise_sumup_result(result: dict) -> dict:
    """Pull the diagnostic fields out of a SumUp verify_checkout result so we
    can log a flat one-liner without dumping the entire payload. Returns only
    what we need to understand why a checkout failed; no PANs, no CVVs — SumUp
    doesn't return those, but the helper stays strict in case the API changes.
    """
    raw = result.get("raw") or {}
    ref = raw.get("checkout_reference") or ""
    status = (raw.get("status") or result.get("status") or "").upper()
    # Each transaction attempt is appended to `transactions` by SumUp; a
    # failed capture leaves a FAILED entry here with the decline detail.
    txns = raw.get("transactions") or []
    last_txn = txns[-1] if txns else {}
    return {
        "status": status,
        "ref": ref,
        "txn_count": len(txns),
        "txn_status": (last_txn.get("status") or "").upper(),
        "simple_status": (last_txn.get("simple_status") or "").upper(),
        "payment_type": last_txn.get("payment_type"),
        "auth_code": last_txn.get("auth_code"),
        # SumUp sometimes surfaces an error_code / error_message on the
        # checkout itself when the hosted page reports a decline. These are
        # the fields support will ask about.
        "error_code": raw.get("error_code"),
        "error_message": raw.get("error_message"),
        "status_description": raw.get("status_description"),
    }


@app.post("/api/webhooks/sumup")
async def sumup_webhook(
    request: Request,
    session: AsyncSession = Depends(get_session),
):
    """SumUp notifies us on checkout status change — payload is {event_type, id}.

    No signature verification: SumUp's own guidance is to re-fetch the checkout
    from their API with the id, which gives us the authoritative status and
    makes a spoofed payload harmless (worst case: wasted API call).

    Fail silently on any error — SumUp retries at 1, 5, 20, 120 minutes, but a
    non-2xx response makes those retries happen, so we return 200 even when
    something downstream couldn't be done.
    """
    try:
        payload = await request.json()
    except Exception:
        logger.warning("SumUp webhook: non-JSON body")
        return JSONResponse({"received": True})

    checkout_id = payload.get("id")
    event_type = payload.get("event_type")
    if not checkout_id:
        logger.warning("SumUp webhook: payload missing checkout id; event_type=%s", event_type)
        return JSONResponse({"received": True})

    try:
        result = await sumup.verify_checkout(checkout_id)
    except Exception as exc:
        logger.error("SumUp webhook verify failed for %s: %s", checkout_id, exc)
        return JSONResponse({"received": True})

    summary = _summarise_sumup_result(result)
    # One line per webhook so grepping Railway logs for a checkout_id gives
    # the full picture. Logged for both success and failure so we can trace
    # the capture lifecycle without having to hunt in the SumUp dashboard.
    logger.info(
        "SumUp webhook: event=%s checkout_id=%s status=%s ref=%s "
        "txns=%d txn_status=%s simple=%s payment_type=%s auth=%s "
        "error_code=%s error_message=%s status_desc=%s",
        event_type, checkout_id,
        summary["status"], summary["ref"],
        summary["txn_count"], summary["txn_status"], summary["simple_status"],
        summary["payment_type"], summary["auth_code"],
        summary["error_code"], summary["error_message"], summary["status_description"],
    )

    if result.get("status") != "PAID":
        # Explicit non-PAID branch — the one-line summary above already has
        # the decline detail, but keep a marker log at WARNING level so the
        # Railway log level filter catches it even if INFO is suppressed.
        logger.warning(
            "SumUp webhook: non-PAID status for checkout %s — status=%s txn_status=%s error=%s",
            checkout_id, summary["status"], summary["txn_status"],
            summary["error_code"] or summary["error_message"] or summary["status_description"] or "none",
        )
        return JSONResponse({"received": True})

    ref = (result.get("raw") or {}).get("checkout_reference", "") or ""

    # Joining fee path: reference is "joining-fee-{user_id}-{nonce}", no booking row.
    if ref.startswith("joining-fee-"):
        parts = ref.split("-")
        # ["joining", "fee", user_id, nonce]
        if len(parts) < 3:
            return JSONResponse({"received": True})
        try:
            user_id = int(parts[2])
        except ValueError:
            return JSONResponse({"received": True})
        user = await session.get(User, user_id)
        if user and user.joining_fee_paid is False:
            user.joining_fee_paid = True
            txn_id = result.get("transaction_id")
            if txn_id and not user.joining_fee_transaction_id:
                user.joining_fee_transaction_id = txn_id
            await session.commit()
            logger.info("Joining fee confirmed via SumUp webhook for user %d (txn=%s)", user_id, txn_id)
        return JSONResponse({"received": True})

    # Booking path: reference is "booking-{booking_id}".
    if not ref.startswith("booking-"):
        return JSONResponse({"received": True})
    try:
        booking_id = int(ref.split("-")[-1])
    except (ValueError, IndexError):
        return JSONResponse({"received": True})

    b = (await session.execute(
        select(Booking).where(Booking.id == booking_id).with_for_update()
    )).scalar_one_or_none()
    if not b:
        logger.warning("SumUp webhook: booking %d not found", booking_id)
        return JSONResponse({"received": True})
    # Guard against race where webhook arrives for a booking whose stripe_session_id
    # was overwritten (shouldn't happen with our idempotent reference, but be safe).
    if b.stripe_session_id != checkout_id:
        logger.warning(
            "SumUp webhook: checkout_id mismatch for booking %d (stored=%s, got=%s)",
            booking_id, b.stripe_session_id, checkout_id,
        )
        return JSONResponse({"received": True})
    if b.status != BookingStatus.pending:
        return JSONResponse({"received": True})  # already confirmed by verify endpoint

    now = datetime.utcnow()
    b.status = BookingStatus.confirmed
    b.pin_code = _gen_pin()
    b.confirmed_at = now
    b.expires_at = now + timedelta(minutes=30)
    await session.commit()
    logger.info("Booking %d confirmed via SumUp webhook", b.id)

    listing = await session.get(Listing, b.listing_id)
    buyer = await session.get(User, b.buyer_id)
    host = await session.get(User, listing.seller_id) if listing else None
    try:
        if buyer and listing:
            await send_pin_email(buyer.email, buyer.full_name, b, listing)
            if host:
                await send_host_booking_email(host.email, host.full_name, b, listing, buyer)
        await _transfer_to_host(b, session)
    except Exception as exc:
        logger.error("SumUp webhook post-confirm actions failed for booking %d: %s", b.id, exc)
    # NOTE: the physical charging session is NOT started here. The buyer scans
    # the charger's QR code and taps "Start charging" on /charge/{id}/start.
    # That triggers OCPP RemoteStart / Shelly relay-on via a dedicated endpoint.

    return JSONResponse({"received": True})


@app.get("/api/checkout/verify/{booking_id}")
async def verify_checkout(
    booking_id: int,
    current_user: User = Depends(require_role("buyer", "seller", "admin")),
    session: AsyncSession = Depends(get_session),
):
    """Called by success page — verifies the SumUp checkout and confirms the booking immediately."""
    b = (await session.execute(
        select(Booking).where(Booking.id == booking_id).with_for_update()
    )).scalar_one_or_none()

    if not b or b.buyer_id != current_user.id:
        raise HTTPException(404, "Booking not found")

    # Already confirmed — just return it
    if b.status == BookingStatus.confirmed and b.pin_code:
        b.listing = await session.get(Listing, b.listing_id)
        b.buyer = current_user
        return _booking_out(b, show_pin=True)

    # Ask SumUp directly — no webhook wait required.
    if b.stripe_session_id:
        try:
            listing = await session.get(Listing, b.listing_id)
            host = await session.get(User, listing.seller_id) if listing else None
            result = await sumup.verify_checkout(b.stripe_session_id)
            if result.get("status") == "PAID" and b.status == BookingStatus.pending:
                now = datetime.utcnow()
                b.status = BookingStatus.confirmed
                b.pin_code = _gen_pin()
                b.confirmed_at = now
                b.expires_at = now + timedelta(minutes=30)
                await session.commit()
                logger.info("Booking %d confirmed via verify endpoint", b.id)
                if listing:
                    await send_pin_email(current_user.email, current_user.full_name, b, listing)
                if host and listing:
                    await send_host_booking_email(host.email, host.full_name, b, listing, current_user)
                await _transfer_to_host(b, session)
                # Physical session is NOT started here — buyer must press Start
                # on /charge/{id}/start after arriving at the charger.
        except Exception as exc:
            logger.error("SumUp verify error for booking %d: %s", booking_id, exc)

    b.listing = await session.get(Listing, b.listing_id)
    b.buyer = current_user
    return _booking_out(b, show_pin=True)


@app.get("/api/bookings/{booking_id}")
async def get_booking(
    booking_id: int,
    current_user: User = Depends(require_role("buyer", "seller", "admin")),
    session: AsyncSession = Depends(get_session),
):
    b = await session.get(Booking, booking_id)
    if not b or b.buyer_id != current_user.id:
        raise HTTPException(404, "Booking not found")
    b.listing = await session.get(Listing, b.listing_id)
    b.buyer = current_user
    return _booking_out(b, show_pin=True)


# ── Charging session control (buyer + host) ──────────────────────────────────

async def _any_booking_imminent(session: AsyncSession, listing_id: int, within_minutes: int = 60) -> bool:
    """True if a confirmed/active booking on this listing is live or starts
    within `within_minutes`. Used to block host sessions when a buyer booking
    is about to need the charger."""
    now = datetime.utcnow()
    horizon = now + timedelta(minutes=within_minutes)
    stmt = select(Booking).where(
        Booking.listing_id == listing_id,
        Booking.status.in_((BookingStatus.confirmed, BookingStatus.active)),
        # confirmed but not yet started, within the 30-min window OR within the look-ahead;
        # active bookings are always imminent (they're happening now).
        (Booking.started_at.is_(None) & (Booking.expires_at >= now) & (Booking.confirmed_at <= horizon))
        | (Booking.started_at.isnot(None) & (Booking.completed_at.is_(None))),
    ).limit(1)
    return (await session.execute(stmt)).scalar_one_or_none() is not None


@app.post("/api/charge/{booking_id}/start")
async def buyer_start_session(
    booking_id: int,
    current_user: User = Depends(require_role("buyer", "seller", "admin")),
    session: AsyncSession = Depends(get_session),
):
    """Buyer presses "Start charging" after arriving at the charger. Fires
    OCPP RemoteStart or Shelly relay-on. Enforces the 30-minute post-payment
    window; expired bookings auto-cancel + refund here."""
    b = (await session.execute(
        select(Booking).where(Booking.id == booking_id).with_for_update()
    )).scalar_one_or_none()
    if not b or b.buyer_id != current_user.id:
        raise HTTPException(404, "Booking not found")
    if b.status == BookingStatus.active or b.started_at:
        return {"status": "already_started"}
    if b.status != BookingStatus.confirmed:
        raise HTTPException(400, f"Cannot start — booking is {b.status}")

    now = datetime.utcnow()
    # Legacy bookings (confirmed before the explicit-start migration) got
    # confirmed_at + expires_at backfilled from created_at + 30 min, so this
    # covers them too. A booking whose columns somehow still ended up NULL
    # is treated as expired — safer than letting it start indefinitely.
    if (not b.expires_at) or now > b.expires_at:
        booking_id_for_refund = b.id
        # Release this transaction's row lock before the helper takes its own
        # FOR UPDATE and makes external calls.
        await session.commit()
        refunded = await _attempt_booking_refund(booking_id_for_refund, source="buyer-start")
        if refunded:
            raise HTTPException(410, "This booking expired (30-min start window). You've been refunded.")
        # Refund failed or deferred — give the buyer an honest message and let
        # the sweep retry. The booking is still status=confirmed-but-expired
        # until the refund actually succeeds, so no "cancelled" lie in the UI.
        raise HTTPException(
            410,
            "This booking expired. We've started a refund; it will arrive within 24h. "
            "If you don't see it by then, contact support@chargedev.io.",
        )

    listing = await session.get(Listing, b.listing_id)
    if not listing:
        raise HTTPException(500, "Listing not found")

    # A7: block if the host has an active session on this listing. We'd fire
    # a second OCPP RemoteStart or try to flip the Shelly relay (already on),
    # and when the host presses Stop the buyer's paid session loses power.
    host_active = (await session.execute(
        select(HostSession.id).where(
            HostSession.listing_id == b.listing_id,
            HostSession.status == "active",
        ).limit(1)
    )).scalar_one_or_none()
    if host_active:
        raise HTTPException(409, "The host is currently using this charger. Please try again in a moment.")

    b.started_at = now
    b.status = BookingStatus.active
    await session.commit()

    if listing.ocpp_charge_point_id:
        asyncio.create_task(_ocpp_start_session(b, listing))
    elif listing.shelly_device_id and listing.shelly_auth_key and listing.shelly_server:
        asyncio.create_task(_shelly_start_session(b, listing))
    else:
        logger.warning("Booking %d: listing has no metered hardware; session won't auto-stop", b.id)
    return {"status": "started"}


@app.get("/api/charge/{listing_id}/context", response_model=ChargeStartContext)
async def charge_start_context(
    listing_id: int,
    current_user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
):
    """Payload for the QR landing page — tells the frontend whether the viewer
    is the host, a buyer with an eligible booking, or neither."""
    listing = await session.get(Listing, listing_id)
    if not listing:
        raise HTTPException(404, "Listing not found")

    # Host branch
    if current_user.id == listing.seller_id:
        hs = (await session.execute(
            select(HostSession).where(
                HostSession.listing_id == listing_id,
                HostSession.status == "active",
            )
        )).scalar_one_or_none()
        return ChargeStartContext(
            listing_id=listing.id,
            listing_title=listing.title,
            listing_address=listing.address,
            role="host",
            host_session_active=hs is not None,
        )

    # Buyer branch: find the most recent eligible (confirmed, not expired) booking
    now = datetime.utcnow()
    b = (await session.execute(
        select(Booking)
        .where(
            Booking.listing_id == listing_id,
            Booking.buyer_id == current_user.id,
            Booking.status.in_((BookingStatus.confirmed, BookingStatus.active)),
        )
        .order_by(Booking.created_at.desc())
        .limit(1)
    )).scalar_one_or_none()
    if b and b.expires_at and b.expires_at > now:
        return ChargeStartContext(
            listing_id=listing.id,
            listing_title=listing.title,
            listing_address=listing.address,
            role="buyer",
            booking_id=b.id,
            booking_pin=b.pin_code,
            booking_expires_at=b.expires_at,
            booking_status=b.status.value if hasattr(b.status, "value") else str(b.status),
            booking_package_kwh=b.package_kwh,
        )

    return ChargeStartContext(
        listing_id=listing.id,
        listing_title=listing.title,
        listing_address=listing.address,
        role="none",
    )


# Host-session endpoints ─────────────────────────────────────────────────────

HOST_SESSION_MAX_HOURS = 8


@app.post("/api/seller/listings/{listing_id}/host-session/start", response_model=HostSessionOut)
async def host_session_start(
    listing_id: int,
    current_user: User = Depends(require_role("seller", "admin")),
    session: AsyncSession = Depends(get_session),
):
    listing = await session.get(Listing, listing_id)
    if not listing or listing.seller_id != current_user.id:
        raise HTTPException(404, "Listing not found")

    # A8: serialise concurrent "Charge my car" taps so two coroutines can't both
    # pass the "no existing session" check and both fire the relay. The partial
    # unique index on host_sessions (listing_id) WHERE status='active' is the
    # backstop; this advisory lock is the primary serialisation.
    try:
        await session.execute(
            text("SELECT pg_advisory_xact_lock(:k)"),
            {"k": (hash(f"host_session:{listing_id}") & 0x7FFFFFFFFFFFFFFF)},
        )
    except Exception:
        pass  # SQLite dev path: single-process anyway

    # Block if there's already a live host session (now serialised).
    existing = (await session.execute(
        select(HostSession).where(
            HostSession.listing_id == listing_id,
            HostSession.status == "active",
        ).with_for_update()
    )).scalar_one_or_none()
    if existing:
        return HostSessionOut.model_validate({
            "id": existing.id, "listing_id": existing.listing_id, "kind": existing.kind,
            "status": existing.status, "started_at": existing.started_at,
            "expires_at": existing.expires_at, "stopped_at": existing.stopped_at,
            "energy_consumed_wh": existing.energy_consumed_wh,
        })

    # A7: block if a buyer booking is actively charging (ShellySession or
    # OcppSession status='active'). Starting the host session would
    # overlap the paid session's metering and corrupt the energy accounting.
    buyer_active = (await session.execute(
        select(ShellySession.id)
        .join(Booking, Booking.id == ShellySession.booking_id)
        .where(ShellySession.status == "active", Booking.listing_id == listing_id)
        .limit(1)
    )).scalar_one_or_none()
    if not buyer_active:
        buyer_active = (await session.execute(
            select(OcppSession.id)
            .join(Booking, Booking.id == OcppSession.booking_id)
            .where(
                OcppSession.status.in_(("active", "starting")),
                Booking.listing_id == listing_id,
            )
            .limit(1)
        )).scalar_one_or_none()
    if buyer_active:
        raise HTTPException(409, "A buyer is currently charging at this listing.")

    # Block if a buyer booking is active or imminent (confirmed within window).
    if await _any_booking_imminent(session, listing_id):
        raise HTTPException(409, "A buyer booking is active or starting within the next 60 minutes.")

    now = datetime.utcnow()
    expires_at = now + timedelta(hours=HOST_SESSION_MAX_HOURS)

    # Choose kind + fire the physical start
    if listing.ocpp_charge_point_id:
        kind = "ocpp"
        energy_start_wh = 0.0
    elif listing.shelly_device_id and listing.shelly_auth_key and listing.shelly_server:
        kind = "shelly"
        try:
            auth_key = _decrypt_shelly_key(listing.shelly_auth_key)
            client = ShellyClient(listing.shelly_server, auth_key, listing.shelly_device_id)
            ds = await client.status()
            energy_start_wh = _shelly_energy_wh(ds) if ds else 0.0
            ok = await client.relay(True)
            if not ok:
                raise HTTPException(502, "Could not turn on charger (Shelly relay rejected).")
        except HTTPException:
            raise
        except Exception as exc:
            logger.error("Shelly host-session start failed for listing %d: %s", listing_id, exc)
            raise HTTPException(502, "Could not reach the charger.")
    else:
        raise HTTPException(400, "This listing has no connected metering hardware.")

    hs = HostSession(
        listing_id=listing_id,
        host_id=current_user.id,
        kind=kind,
        status="active",
        started_at=now,
        expires_at=expires_at,
        energy_start_wh=energy_start_wh,
    )
    session.add(hs)
    await session.commit()
    await session.refresh(hs)

    if kind == "ocpp":
        # Fire RemoteStartTransaction with a host-specific idTag
        cp_handler = _ocpp_connections.get(listing.ocpp_charge_point_id)
        if cp_handler:
            asyncio.create_task(cp_handler.remote_start(f"host{current_user.id:06d}"))

    return HostSessionOut.model_validate({
        "id": hs.id, "listing_id": hs.listing_id, "kind": hs.kind, "status": hs.status,
        "started_at": hs.started_at, "expires_at": hs.expires_at, "stopped_at": hs.stopped_at,
        "energy_consumed_wh": hs.energy_consumed_wh,
    })


@app.post("/api/seller/listings/{listing_id}/host-session/stop", response_model=HostSessionOut)
async def host_session_stop(
    listing_id: int,
    current_user: User = Depends(require_role("seller", "admin")),
    session: AsyncSession = Depends(get_session),
):
    listing = await session.get(Listing, listing_id)
    if not listing or listing.seller_id != current_user.id:
        raise HTTPException(404, "Listing not found")

    hs = (await session.execute(
        select(HostSession).where(
            HostSession.listing_id == listing_id,
            HostSession.status == "active",
        ).with_for_update()
    )).scalar_one_or_none()
    if not hs:
        raise HTTPException(404, "No active host session on this listing")

    relay_confirmed_off = True
    if hs.kind == "shelly" and listing.shelly_auth_key:
        try:
            auth_key = _decrypt_shelly_key(listing.shelly_auth_key)
            client = ShellyClient(listing.shelly_server, auth_key, listing.shelly_device_id)
            ds = await client.status()
            current_wh = _shelly_energy_wh(ds) if ds else hs.energy_start_wh
            hs.energy_consumed_wh = max(0.0, current_wh - hs.energy_start_wh)
            ok = await client.relay(False)
            # A4: verify the relay actually turned off by re-polling status.
            # ShellyClient.relay returns False on exception, so we re-check
            # device state. If still on, mark the row 'error' so the sweep
            # retries rather than silently claiming completed.
            relay_confirmed_off = ok
            try:
                verify = await client.status()
                if verify is not None:
                    relay_confirmed_off = relay_confirmed_off and not _shelly_relay_on(verify)
            except Exception:
                relay_confirmed_off = False
            if not relay_confirmed_off:
                logger.error("Shelly host-session %d stop: relay still on after off command — marking error for sweep retry", hs.id)
        except Exception as exc:
            logger.error("Shelly host-session stop error: %s", exc)
            relay_confirmed_off = False
    elif hs.kind == "ocpp":
        # A5: issue a proper RemoteStopTransaction using the transaction_id
        # we captured in on_start_transaction. If the charger isn't reachable
        # or the txn_id is missing, leave the row in `error` so the sweep
        # keeps retrying — don't claim completed while energy is still flowing.
        cp_handler = _ocpp_connections.get(listing.ocpp_charge_point_id or "")
        if hs.transaction_id and cp_handler:
            try:
                await cp_handler._stop_session(hs.transaction_id)
                logger.info("OCPP RemoteStop sent for host session %d (txn=%d)", hs.id, hs.transaction_id)
                # Note: the authoritative stop + energy_consumed_wh will arrive
                # via on_stop_transaction. We optimistically mark completed here.
            except Exception as exc:
                logger.error("OCPP RemoteStop failed for host session %d: %s", hs.id, exc)
                relay_confirmed_off = False
        else:
            logger.warning(
                "Host session %d: no OCPP txn_id (%s) or charger not connected (%s) — leaving in error for sweep",
                hs.id, hs.transaction_id, bool(cp_handler),
            )
            relay_confirmed_off = False

    # Only transition to completed if the relay-off was confirmed. Otherwise
    # leave in error so the sweep tries again — DB saying "completed" while
    # the relay stays on would silently burn host electricity.
    hs.status = "completed" if relay_confirmed_off else "error"
    hs.stopped_at = datetime.utcnow()
    await session.commit()
    return HostSessionOut.model_validate({
        "id": hs.id, "listing_id": hs.listing_id, "kind": hs.kind, "status": hs.status,
        "started_at": hs.started_at, "expires_at": hs.expires_at, "stopped_at": hs.stopped_at,
        "energy_consumed_wh": hs.energy_consumed_wh,
    })


@app.get("/api/seller/listings/{listing_id}/host-session", response_model=Optional[HostSessionOut])
async def host_session_status(
    listing_id: int,
    current_user: User = Depends(require_role("seller", "admin")),
    session: AsyncSession = Depends(get_session),
):
    listing = await session.get(Listing, listing_id)
    if not listing or listing.seller_id != current_user.id:
        raise HTTPException(404, "Listing not found")
    hs = (await session.execute(
        select(HostSession).where(
            HostSession.listing_id == listing_id,
            HostSession.status == "active",
        )
    )).scalar_one_or_none()
    if not hs:
        return None
    return HostSessionOut.model_validate({
        "id": hs.id, "listing_id": hs.listing_id, "kind": hs.kind, "status": hs.status,
        "started_at": hs.started_at, "expires_at": hs.expires_at, "stopped_at": hs.stopped_at,
        "energy_consumed_wh": hs.energy_consumed_wh,
    })


# ── Reviews ───────────────────────────────────────────────────────────────────

@app.post("/api/reviews", response_model=ReviewOut)
async def create_review(
    body: ReviewCreate,
    current_user: User = Depends(require_role("buyer", "seller", "admin")),
    session: AsyncSession = Depends(get_session),
):
    b = await session.get(Booking, body.booking_id)
    if not b or b.buyer_id != current_user.id:
        raise HTTPException(404, "Booking not found")
    if b.status != BookingStatus.completed:
        raise HTTPException(422, "Can only review completed bookings")
    existing = (await session.execute(
        select(Review).where(Review.booking_id == body.booking_id)
    )).scalar_one_or_none()
    if existing:
        raise HTTPException(409, "You already reviewed this booking")
    review = Review(
        booking_id=body.booking_id,
        listing_id=b.listing_id,
        reviewer_id=current_user.id,
        rating=body.rating,
        comment=body.comment,
    )
    session.add(review)
    await session.commit()
    await session.refresh(review)
    return ReviewOut(
        **{c.key: getattr(review, c.key) for c in review.__table__.columns},
        reviewer_name=current_user.full_name,
    )


@app.get("/api/listings/{listing_id}/reviews", response_model=list[ReviewOut])
async def listing_reviews(listing_id: int, session: AsyncSession = Depends(get_session)):
    rows = (await session.execute(
        select(Review).where(Review.listing_id == listing_id).order_by(Review.created_at.desc())
    )).scalars().all()
    result = []
    for r in rows:
        reviewer = await session.get(User, r.reviewer_id)
        result.append(ReviewOut(
            **{c.key: getattr(r, c.key) for c in r.__table__.columns},
            reviewer_name=reviewer.full_name if reviewer else "—",
        ))
    return result


@app.get("/api/seller/reviews", response_model=list[ReviewOut])
async def seller_reviews(
    current_user: User = Depends(require_role("seller", "admin")),
    session: AsyncSession = Depends(get_session),
):
    listing_ids = [
        r.id for r in (await session.execute(
            select(Listing.id).where(Listing.seller_id == current_user.id)
        )).all()
    ]
    rows = (await session.execute(
        select(Review).where(Review.listing_id.in_(listing_ids)).order_by(Review.created_at.desc())
    )).scalars().all()
    result = []
    for r in rows:
        reviewer = await session.get(User, r.reviewer_id)
        result.append(ReviewOut(
            **{c.key: getattr(r, c.key) for c in r.__table__.columns},
            reviewer_name=reviewer.full_name if reviewer else "—",
        ))
    return result


# ── Availability ──────────────────────────────────────────────────────────────

@app.put("/api/seller/listings/{listing_id}/availability")
async def set_availability(
    listing_id: int,
    body: WeeklyAvailabilitySchema,
    current_user: User = Depends(require_role("seller", "admin")),
    session: AsyncSession = Depends(get_session),
):
    import json as _json
    listing = await session.get(Listing, listing_id)
    if not listing or listing.seller_id != current_user.id:
        raise HTTPException(404, "Listing not found")
    listing.availability_json = _json.dumps(body.model_dump(exclude_none=True))
    await session.commit()
    return {"ok": True}


# ── Notifications (polling) ───────────────────────────────────────────────────

@app.get("/api/seller/new-bookings")
async def new_bookings_since(
    since: float,
    current_user: User = Depends(require_role("seller", "admin")),
    session: AsyncSession = Depends(get_session),
):
    since_dt = datetime.fromtimestamp(since, tz=timezone.utc)
    listing_ids = [
        r.id for r in (await session.execute(
            select(Listing.id).where(Listing.seller_id == current_user.id)
        )).all()
    ]
    rows = (await session.execute(
        select(Booking).where(
            Booking.listing_id.in_(listing_ids),
            Booking.status == BookingStatus.confirmed,
            Booking.created_at > since_dt,
        )
    )).scalars().all()
    return {"count": len(rows), "bookings": [{"id": b.id, "listing_id": b.listing_id} for b in rows]}


# ── Booking messages (buyer ↔ host) ───────────────────────────────────────────

# Simple in-process rate limit: last-send timestamp per sender id.
# Lives beside the process; a restart resets it — fine for a pilot, upgrade to
# Redis if we ever go multi-replica.
_message_last_sent: dict[int, datetime] = {}


async def _booking_participants(session: AsyncSession, booking: Booking) -> tuple[Optional[User], Optional[User], Optional[Listing]]:
    """Return (buyer, host, listing) for a booking."""
    listing = await session.get(Listing, booking.listing_id)
    buyer = await session.get(User, booking.buyer_id)
    host = await session.get(User, listing.seller_id) if listing else None
    return buyer, host, listing


async def _assert_booking_participant(
    session: AsyncSession, booking_id: int, user: User,
) -> tuple[Booking, Listing]:
    """Allow buyer, host, and admin. 404 if the booking is missing, 403 otherwise."""
    b = await session.get(Booking, booking_id)
    if not b:
        raise HTTPException(404, "Booking not found")
    listing = await session.get(Listing, b.listing_id)
    if not listing:
        raise HTTPException(404, "Listing not found")
    is_buyer = b.buyer_id == user.id
    is_host = listing.seller_id == user.id
    is_admin = user.role == UserRole.admin
    if not (is_buyer or is_host or is_admin):
        raise HTTPException(403, "Not a participant in this booking")
    return b, listing


def _message_role(sender_id: int, booking: Booking, listing: Listing) -> str:
    if sender_id == booking.buyer_id:
        return "buyer"
    if sender_id == listing.seller_id:
        return "host"
    return "admin"


async def _message_out(m: Message, session: AsyncSession, booking: Booking, listing: Listing) -> MessageOut:
    sender = await session.get(User, m.sender_id)
    return MessageOut(
        id=m.id,
        booking_id=m.booking_id,
        sender_id=m.sender_id,
        sender_name=sender.full_name if sender else None,
        sender_role=_message_role(m.sender_id, booking, listing),
        body=m.body,
        created_at=m.created_at,
        read_at=m.read_at,
    )


async def send_message_notification_email(
    *, recipient_email: str, recipient_name: str, sender_name: str,
    booking_id: int, listing_title: str, body_preview: str,
) -> None:
    """Fire-and-forget email to the other party on a new message."""
    if not settings.resend_api_key:
        return
    deep_link = f"{settings.frontend_url}/bookings"
    try:
        resend.Emails.send({
            "from": settings.email_from,
            "to": [recipient_email],
            "subject": f"New message from {sender_name} · ChargedEV",
            "html": f"""
            <div style="font-family:sans-serif;max-width:480px;margin:0 auto;padding:32px">
              <h2 style="color:#22C55E;margin-bottom:4px">New message 💬</h2>
              <p style="color:#555">{html_escape(sender_name)} sent you a message about booking #{booking_id} — {html_escape(listing_title)}.</p>
              <div style="background:#F3F4F6;border-radius:12px;padding:16px;margin:16px 0;color:#111;white-space:pre-wrap">{html_escape(body_preview)}</div>
              <p><a href="{deep_link}" style="color:#F7FF2B;text-decoration:underline">Open your bookings to reply →</a></p>
              <p style="color:#9CA3AF;font-size:12px;margin-top:24px">ChargedEV · chargedev.io</p>
            </div>
            """,
        })
        logger.info("Message notification email sent to %s (booking %d)", recipient_email, booking_id)
    except Exception as exc:
        logger.error("Failed to send message notification: %s", exc)


@app.post("/api/bookings/{booking_id}/messages", response_model=MessageOut)
async def send_booking_message(
    booking_id: int,
    body: MessageIn,
    current_user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
):
    b, listing = await _assert_booking_participant(session, booking_id, current_user)

    # Admin may read but not send; dispute mediation is out-of-band.
    if current_user.role == UserRole.admin and current_user.id not in (b.buyer_id, listing.seller_id):
        raise HTTPException(403, "Admin may read this thread but not post to it.")

    text = (body.body or "").strip()
    if len(text) < 1:
        raise HTTPException(400, "Message body is required.")
    if len(text) > 2000:
        raise HTTPException(400, "Message is too long (max 2000 characters).")

    # 2-second cooldown per sender: catches double-click, light scripted spam.
    now = datetime.utcnow()
    last = _message_last_sent.get(current_user.id)
    if last and (now - last).total_seconds() < 2:
        raise HTTPException(429, "Please wait a moment before sending another message.")
    _message_last_sent[current_user.id] = now

    msg = Message(
        booking_id=booking_id,
        sender_id=current_user.id,
        body=text,
        created_at=now,
    )
    session.add(msg)
    await session.commit()
    await session.refresh(msg)

    # Fire the notification email async so send never blocks on Resend.
    try:
        buyer, host, _ = await _booking_participants(session, b)
        recipient = host if current_user.id == b.buyer_id else buyer
        if recipient and recipient.email and recipient.id != current_user.id:
            asyncio.create_task(send_message_notification_email(
                recipient_email=recipient.email,
                recipient_name=recipient.full_name,
                sender_name=current_user.full_name,
                booking_id=b.id,
                listing_title=listing.title,
                body_preview=text[:200],
            ))
    except Exception as exc:
        logger.error("Message notification dispatch failed: %s", exc)

    return await _message_out(msg, session, b, listing)


@app.get("/api/bookings/{booking_id}/messages", response_model=list[MessageOut])
async def list_booking_messages(
    booking_id: int,
    current_user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
):
    b, listing = await _assert_booking_participant(session, booking_id, current_user)
    rows = (await session.execute(
        select(Message).where(Message.booking_id == booking_id).order_by(Message.created_at)
    )).scalars().all()

    # Mark the counter-party's messages as read for this viewer. Admin reads
    # never mark anything read, so the unread badge stays accurate for buyer/host.
    if current_user.role != UserRole.admin:
        now = datetime.utcnow()
        changed = False
        for m in rows:
            if m.sender_id != current_user.id and m.read_at is None:
                m.read_at = now
                changed = True
        if changed:
            await session.commit()

    return [await _message_out(m, session, b, listing) for m in rows]


@app.get("/api/bookings/{booking_id}/messages/unread-count", response_model=UnreadCountOut)
async def booking_unread_count(
    booking_id: int,
    current_user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
):
    b, _ = await _assert_booking_participant(session, booking_id, current_user)
    unread = (await session.execute(
        select(func.count(Message.id)).where(
            Message.booking_id == booking_id,
            Message.sender_id != current_user.id,
            Message.read_at.is_(None),
        )
    )).scalar_one()
    return UnreadCountOut(booking_id=b.id, unread=int(unread))
