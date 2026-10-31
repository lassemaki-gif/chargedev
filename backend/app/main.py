"""ChargedEV API — EV charging marketplace backend."""
import asyncio
import hashlib
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
    Booking, BookingStatus, HostPayout, Listing, PACKAGES_KWH, Review,
    OcppChargePoint, OcppSession, ShellySession, User, UserRole,
)
from . import payouts, sumup
from .schemas import (
    BookingCreate, BookingOut, ForgotPasswordRequest, HostPayoutOut, ListingCreate,
    ListingOut, LoginRequest, MarkPaidIn, OcppChargePointOut, OcppRegisterRequest,
    PayoutAccountIn, PayoutAccountOut, PlatformStats, ProfileUpdate, RegisterRequest,
    ResetPasswordRequest, ReviewCreate, ReviewOut, SellerEarnings, ShellyConfigRequest,
    ShellyStatusOut, TokenResponse, UserOut, WeeklyAvailabilitySchema,
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


@asynccontextmanager
async def lifespan(_: FastAPI):
    await init_db()
    asyncio.create_task(_shelly_poll_loop())
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


async def send_pin_email(buyer_email: str, buyer_name: str, booking: Booking, listing: Listing) -> None:
    if not settings.resend_api_key:
        logger.warning("RESEND_API_KEY not set — skipping email")
        return
    try:
        resend.Emails.send({
            "from": settings.email_from,
            "to": [buyer_email],
            "subject": f"Your ChargedEV PIN — {listing.title}",
            "html": f"""
            <div style="font-family:sans-serif;max-width:480px;margin:0 auto;padding:32px">
              <h2 style="color:#22C55E;margin-bottom:4px">Your charging PIN is ready ⚡</h2>
              <p style="color:#555">Payment confirmed. Show this PIN to the host to start your session.</p>
              <div style="background:#0A0F1E;border-radius:12px;padding:32px;text-align:center;margin:24px 0">
                <p style="color:#9CA3AF;font-size:12px;text-transform:uppercase;letter-spacing:0.1em;margin:0 0 8px">Session PIN</p>
                <p style="color:#22C55E;font-size:48px;font-family:monospace;font-weight:700;letter-spacing:0.2em;margin:0">{booking.pin_code}</p>
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
        })
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
    # Generate verification token and send email
    verification_token = secrets.token_urlsafe(32)
    user.verification_token = verification_token
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
async def verify_email(token: str, session: AsyncSession = Depends(get_session)):
    user = (await session.execute(
        select(User).where(User.verification_token == token)
    )).scalar_one_or_none()
    if not user:
        raise HTTPException(400, "Invalid or expired verification token")
    user.email_verified = True
    user.verification_token = None
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
        payout_account_set=bool(current_user.iban and current_user.payout_account_holder),
    )


# ── Host payout account (SumUp era: manual SEPA batches to host IBANs) ────────

_IBAN_SHAPE_RE = re.compile(r"[A-Z]{2}[0-9]{2}[A-Z0-9]{11,30}$")
# Latin-1 Supplement + Latin Extended covers Finnish, Swedish, etc.;
# allows apostrophe, hyphen, period, and space for compound names.
_ACCOUNT_HOLDER_RE = re.compile(r"[A-Za-zÀ-ÿ'\.\- ]{2,120}$")


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


@app.get("/api/seller/payout-account", response_model=PayoutAccountOut)
async def get_payout_account(
    current_user: User = Depends(require_role("seller", "admin")),
):
    if not current_user.iban or not current_user.payout_account_holder:
        return PayoutAccountOut(is_set=False)
    try:
        iban = _decrypt_iban(current_user.iban)
    except Exception:
        logger.error("IBAN decrypt failed for user %d", current_user.id)
        return PayoutAccountOut(is_set=False)
    return PayoutAccountOut(
        iban_last4=iban[-4:] if len(iban) >= 4 else iban,
        account_holder=current_user.payout_account_holder,
        is_set=True,
    )


@app.put("/api/seller/payout-account", response_model=PayoutAccountOut)
async def set_payout_account(
    body: PayoutAccountIn,
    current_user: User = Depends(require_role("seller", "admin")),
    session: AsyncSession = Depends(get_session),
):
    iban = _normalize_iban(body.iban)
    if not _IBAN_SHAPE_RE.match(iban):
        raise HTTPException(400, "IBAN looks malformed — check the country prefix and length.")
    if not _iban_mod97_ok(iban):
        raise HTTPException(400, "IBAN checksum failed — please double-check the number.")
    holder = (body.account_holder or "").strip()
    if not _ACCOUNT_HOLDER_RE.match(holder):
        raise HTTPException(
            400,
            "Account holder name must be 2–120 characters, letters and standard punctuation only.",
        )
    current_user.iban = _encrypt_iban(iban)
    current_user.payout_account_holder = holder
    await session.commit()
    return PayoutAccountOut(
        iban_last4=iban[-4:],
        account_holder=holder,
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
    # Guarded by three conditions so retries + duplicate completes are safe:
    #   - joining_fee_paid is True (host actually paid it)
    #   - joining_fee_transaction_id is set (we have something to refund)
    #   - joining_fee_refunded_at is None (we haven't already refunded)
    # Any failure is logged and swallowed — the booking completion must not
    # roll back just because the refund API hiccupped. Admin can retry via
    # the SumUp dashboard if needed.
    if (
        current_user.joining_fee_paid is True
        and current_user.joining_fee_transaction_id
        and current_user.joining_fee_refunded_at is None
    ):
        try:
            await sumup.refund(current_user.joining_fee_transaction_id, amount_eur=Decimal("1.00"))
            current_user.joining_fee_refunded_at = datetime.utcnow()
            await session.commit()
            logger.info(
                "Joining fee refunded to user %d (txn=%s) on first completed booking %d",
                current_user.id, current_user.joining_fee_transaction_id, b.id,
            )
        except Exception as exc:
            logger.error(
                "Joining fee refund failed for user %d (txn=%s): %s — refund manually from SumUp dashboard",
                current_user.id, current_user.joining_fee_transaction_id, exc,
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
        status=p.status,
        sepa_reference=p.sepa_reference,
        created_at=p.created_at,
        paid_at=p.paid_at,
    )


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
            redirect_url=f"{settings.frontend_url}/sell/dashboard?joining_fee={current_user.id}",
            webhook_url=f"{settings.backend_url}/api/webhooks/sumup",
            customer_email=current_user.email,
        )
    except Exception as exc:
        logger.error("Joining fee checkout creation failed: %s", exc)
        raise HTTPException(502, "Could not create payment. Please try again.")
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
        return JSONResponse({"received": True})

    checkout_id = payload.get("id")
    if not checkout_id:
        return JSONResponse({"received": True})

    try:
        result = await sumup.verify_checkout(checkout_id)
    except Exception as exc:
        logger.error("SumUp webhook verify failed for %s: %s", checkout_id, exc)
        return JSONResponse({"received": True})

    if result.get("status") != "PAID":
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

    b.status = BookingStatus.confirmed
    b.pin_code = _gen_pin()
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
        if listing and listing.ocpp_charge_point_id:
            asyncio.create_task(_ocpp_start_session(b, listing))
        elif listing and listing.shelly_device_id and listing.shelly_auth_key and listing.shelly_server:
            asyncio.create_task(_shelly_start_session(b, listing))
    except Exception as exc:
        logger.error("SumUp webhook post-confirm actions failed for booking %d: %s", b.id, exc)

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
                b.status = BookingStatus.confirmed
                b.pin_code = _gen_pin()
                await session.commit()
                logger.info("Booking %d confirmed via verify endpoint", b.id)
                if listing:
                    await send_pin_email(current_user.email, current_user.full_name, b, listing)
                if host and listing:
                    await send_host_booking_email(host.email, host.full_name, b, listing, current_user)
                await _transfer_to_host(b, session)
                if listing and listing.ocpp_charge_point_id:
                    asyncio.create_task(_ocpp_start_session(b, listing))
                elif listing and listing.shelly_device_id and listing.shelly_auth_key and listing.shelly_server:
                    asyncio.create_task(_shelly_start_session(b, listing))
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
