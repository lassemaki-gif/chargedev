"""ChargedEV API — EV charging marketplace backend."""
import asyncio
import logging
import secrets
import string
from contextlib import asynccontextmanager
from decimal import Decimal, ROUND_HALF_UP
from html import escape as html_escape
from datetime import datetime, timezone
from typing import Optional
from urllib.parse import urlencode

import httpx
import resend
from fastapi import Depends, FastAPI, Form, HTTPException, Request, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, RedirectResponse
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
    Booking, BookingStatus, Listing, PACKAGES_KWH, PlatformMollie, Review,
    OcppChargePoint, OcppSession, ShellySession, User, UserRole,
)
from .schemas import (
    BookingCreate, BookingOut, ListingCreate, ListingOut,
    LoginRequest, OcppChargePointOut, OcppRegisterRequest, PlatformStats,
    ProfileUpdate, RegisterRequest, ReviewCreate, ReviewOut, SellerEarnings,
    ShellyConfigRequest, ShellyStatusOut, TokenResponse, UserOut, WeeklyAvailabilitySchema,
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
                        client = ShellyClient(s.server, s.auth_key, s.device_id)
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


def real_ip(request: Request) -> str:
    """Extract real client IP, checking proxy headers in priority order."""
    for header in ("x-real-ip", "x-forwarded-for", "x-envoy-external-address"):
        ip = request.headers.get(header)
        if ip:
            return ip.split(",")[0].strip()
    return request.client.host if request.client else "unknown"


limiter = Limiter(key_func=real_ip, default_limits=["60/minute"])


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
    """Turn on Shelly relay and create a session record. Fires as a background task."""
    from .db import async_session
    client = ShellyClient(listing.shelly_server, listing.shelly_auth_key, listing.shelly_device_id)
    ds = await client.status()
    if ds is None:
        logger.error("Shelly device offline for booking %d — cannot start session", booking.id)
        async with async_session() as sess:
            sess.add(ShellySession(
                booking_id=booking.id, listing_id=listing.id,
                device_id=listing.shelly_device_id, auth_key=listing.shelly_auth_key,
                server=listing.shelly_server, target_kwh=float(booking.package_kwh),
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
                device_id=listing.shelly_device_id, auth_key=listing.shelly_auth_key,
                server=listing.shelly_server, target_kwh=float(booking.package_kwh),
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


@app.exception_handler(Exception)
async def global_exception_handler(request: Request, exc: Exception):
    logger.error("Unhandled error on %s: %s: %s", request.url.path, type(exc).__name__, exc, exc_info=True)
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
    """Encrypt a Mollie OAuth token at rest using Fernet symmetric encryption."""
    if not settings.mollie_token_key or not token:
        return token
    from cryptography.fernet import Fernet
    return Fernet(settings.mollie_token_key.encode()).encrypt(token.encode()).decode()


def _decrypt_token(token: str) -> str:
    """Decrypt a Fernet-encrypted Mollie OAuth token."""
    if not settings.mollie_token_key or not token:
        return token
    try:
        from cryptography.fernet import Fernet
        return Fernet(settings.mollie_token_key.encode()).decrypt(token.encode()).decode()
    except Exception:
        return token  # Return as-is if not encrypted (legacy plain tokens)


async def _mollie(method: str, path: str, idempotency_key: Optional[str] = None, **kwargs) -> dict:
    """Authenticated request to the Mollie REST API."""
    headers = {"Authorization": f"Bearer {settings.mollie_api_key}"}
    if idempotency_key:
        headers["Idempotency-Key"] = idempotency_key
    async with httpx.AsyncClient(timeout=15.0) as client:
        resp = await client.request(
            method,
            f"https://api.mollie.com/v2{path}",
            headers=headers,
            **kwargs,
        )
        resp.raise_for_status()
        return resp.json()


async def _refresh_mollie_token(user: User, session: AsyncSession) -> str:
    """Exchange the user's refresh token for a new access token; persist both."""
    if not user.mollie_refresh_token:
        raise HTTPException(401, "Mollie refresh token missing — reconnect required")
    refresh_token = _decrypt_token(user.mollie_refresh_token)
    async with httpx.AsyncClient(timeout=15.0) as client:
        resp = await client.post(
            "https://api.mollie.com/oauth2/tokens",
            auth=(settings.mollie_client_id, settings.mollie_client_secret),
            data={"grant_type": "refresh_token", "refresh_token": refresh_token},
        )
    if resp.status_code >= 400:
        logger.warning("Mollie refresh failed for user %d: %s %s", user.id, resp.status_code, resp.text)
        raise HTTPException(401, "Mollie refresh failed — reconnect required")
    tokens = resp.json()
    access_token = tokens["access_token"]
    new_refresh = tokens.get("refresh_token", refresh_token)
    user.mollie_access_token = _encrypt_token(access_token)
    user.mollie_refresh_token = _encrypt_token(new_refresh)
    await session.commit()
    logger.info("Mollie token refreshed for user %d", user.id)
    return access_token


async def _mollie_user_request(
    user: User, session: AsyncSession, method: str, path: str,
    idempotency_key: Optional[str] = None, **kwargs,
) -> httpx.Response:
    """Call Mollie with the seller's access token; refresh + retry on an invalid-token response.

    Mollie returns 400 "Invalid Authorization header" (not 401) for expired or
    invalid access tokens, so we retry on both.
    """
    if not user.mollie_access_token:
        raise HTTPException(401, "Mollie not connected")
    access_token = _decrypt_token(user.mollie_access_token)
    url = f"https://api.mollie.com/v2{path}"
    headers = {"Authorization": f"Bearer {access_token}"}
    if idempotency_key:
        headers["Idempotency-Key"] = idempotency_key
    async with httpx.AsyncClient(timeout=15.0) as client:
        resp = await client.request(method, url, headers=headers, **kwargs)
        if _is_mollie_auth_error(resp):
            access_token = await _refresh_mollie_token(user, session)
            headers["Authorization"] = f"Bearer {access_token}"
            resp = await client.request(method, url, headers=headers, **kwargs)
        resp.raise_for_status()
        return resp


async def _refresh_platform_mollie_token(row: PlatformMollie, session: AsyncSession) -> str:
    """Refresh the platform's own OAuth access token via its refresh token."""
    if not row.refresh_token:
        raise HTTPException(503, "Platform Mollie OAuth not configured")
    refresh_token = _decrypt_token(row.refresh_token)
    async with httpx.AsyncClient(timeout=15.0) as client:
        resp = await client.post(
            "https://api.mollie.com/oauth2/tokens",
            auth=(settings.mollie_client_id, settings.mollie_client_secret),
            data={"grant_type": "refresh_token", "refresh_token": refresh_token},
        )
    if resp.status_code >= 400:
        logger.warning("Platform Mollie refresh failed: %s %s", resp.status_code, resp.text)
        raise HTTPException(503, "Platform Mollie refresh failed")
    tokens = resp.json()
    access_token = tokens["access_token"]
    new_refresh = tokens.get("refresh_token", refresh_token)
    row.access_token = _encrypt_token(access_token)
    row.refresh_token = _encrypt_token(new_refresh)
    await session.commit()
    logger.info("Platform Mollie token refreshed")
    return access_token


async def _platform_mollie_request(
    session: AsyncSession, method: str, path: str, **kwargs,
) -> httpx.Response:
    """Call Mollie using the platform's own OAuth token; refresh + retry on auth error."""
    row = (await session.execute(select(PlatformMollie).where(PlatformMollie.id == 1))).scalar_one_or_none()
    if not row or not row.access_token:
        raise HTTPException(503, "Platform Mollie OAuth not configured. Run /api/admin/mollie/platform-connect.")
    access_token = _decrypt_token(row.access_token)
    url = f"https://api.mollie.com/v2{path}"
    async with httpx.AsyncClient(timeout=15.0) as client:
        resp = await client.request(
            method, url, headers={"Authorization": f"Bearer {access_token}"}, **kwargs,
        )
        if _is_mollie_auth_error(resp):
            access_token = await _refresh_platform_mollie_token(row, session)
            resp = await client.request(
                method, url, headers={"Authorization": f"Bearer {access_token}"}, **kwargs,
            )
        resp.raise_for_status()
        return resp


def _is_mollie_auth_error(resp: httpx.Response) -> bool:
    """True if a Mollie response signals an invalid/expired access token."""
    if resp.status_code == 401:
        return True
    if resp.status_code == 400:
        try:
            return "authorization" in (resp.json().get("detail", "")).lower()
        except Exception:
            return False
    return False


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
    if not user or not verify_password(body.password, user.hashed_password):
        raise HTTPException(401, "Invalid email or password")
    return TokenResponse(
        access_token=create_access_token(user.id, user.role),
        role=user.role,
        full_name=user.full_name,
    )


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
        mollie_connected=bool(current_user.stripe_account_id),
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
    _: User = Depends(require_role("admin")),
    session: AsyncSession = Depends(get_session),
):
    """Manually confirm a pending booking and generate a PIN (admin fallback)."""
    b = await session.get(Booking, booking_id)
    if not b:
        raise HTTPException(404, "Booking not found")
    if b.status != BookingStatus.pending:
        raise HTTPException(400, f"Booking is already {b.status}")
    b.status = BookingStatus.confirmed
    b.pin_code = _gen_pin()
    await session.commit()
    return {"status": "confirmed", "pin_code": b.pin_code, "booking_id": b.id}


# ── Admin: platform Mollie self-OAuth (one-time setup) ────────────────────────

PLATFORM_MOLLIE_SCOPE = "clients.write organizations.read"


@app.get("/api/admin/mollie/platform-connect")
async def platform_mollie_connect(
    _: User = Depends(require_role("admin")),
    session: AsyncSession = Depends(get_session),
):
    """Start the platform self-OAuth flow (one-time setup for Client Links API)."""
    row = (await session.execute(select(PlatformMollie).where(PlatformMollie.id == 1))).scalar_one_or_none()
    if row is None:
        row = PlatformMollie(id=1)
        session.add(row)
    nonce = secrets.token_hex(16)
    row.nonce = nonce
    await session.commit()

    state = jose_jwt.encode(
        {"platform": True, "nonce": nonce, "exp": datetime.utcnow().timestamp() + 600},
        settings.secret_key, algorithm="HS256",
    )
    # Mollie allows only one redirect URI per app; reuse the registered seller
    # callback, which dispatches on payload["platform"] in the state JWT.
    params = urlencode({
        "client_id": settings.mollie_client_id,
        "redirect_uri": f"{settings.backend_url}/api/seller/mollie/callback",
        "response_type": "code",
        "scope": PLATFORM_MOLLIE_SCOPE,
        "state": state,
    })
    return {"auth_url": f"https://my.mollie.com/oauth2/authorize?{params}"}


async def _handle_platform_oauth_callback(
    code: str, payload: dict, session: AsyncSession,
) -> RedirectResponse:
    """Platform self-OAuth leg of the shared callback — stores clients.write token."""
    state_nonce = payload.get("nonce")
    row = (await session.execute(select(PlatformMollie).where(PlatformMollie.id == 1))).scalar_one_or_none()
    if not row or not row.nonce or not secrets.compare_digest(row.nonce, state_nonce or ""):
        raise HTTPException(400, "Invalid or expired OAuth state")
    row.nonce = None

    async with httpx.AsyncClient(timeout=15.0) as client:
        token_resp = await client.post(
            "https://api.mollie.com/oauth2/tokens",
            auth=(settings.mollie_client_id, settings.mollie_client_secret),
            data={
                "grant_type": "authorization_code",
                "code": code,
                "redirect_uri": f"{settings.backend_url}/api/seller/mollie/callback",
            },
        )
        token_resp.raise_for_status()
        tokens = token_resp.json()

    row.access_token = _encrypt_token(tokens["access_token"])
    row.refresh_token = _encrypt_token(tokens.get("refresh_token", ""))
    await session.commit()
    logger.info("Platform Mollie OAuth connected")
    return RedirectResponse(f"{settings.frontend_url}/sell/dashboard?platform_mollie=connected")


@app.get("/api/admin/mollie/platform-status")
async def platform_mollie_status(
    _: User = Depends(require_role("admin")),
    session: AsyncSession = Depends(get_session),
):
    row = (await session.execute(select(PlatformMollie).where(PlatformMollie.id == 1))).scalar_one_or_none()
    return {"connected": bool(row and row.access_token)}


# ── Host joining fee ─────────────────────────────────────────────────────────

@app.post("/api/seller/joining-fee")
async def create_joining_fee_payment(
    current_user: User = Depends(require_role("seller", "admin")),
):
    if current_user.joining_fee_paid is not False:
        return {"status": "already_paid"}
    try:
        payment = await _mollie("POST", "/payments",
            idempotency_key=f"joining-fee-{current_user.id}",
            json={
                "amount": {"currency": "EUR", "value": "1.00"},
                "description": "ChargedEV host joining fee",
                "redirectUrl": f"{settings.frontend_url}/sell/dashboard",
                "webhookUrl": f"{settings.backend_url}/api/webhooks/mollie?webhook_secret={settings.mollie_webhook_secret}",
                "metadata": {"type": "joining_fee", "user_id": str(current_user.id)},
            })
    except Exception as exc:
        logger.error("Mollie joining fee payment creation failed: %s", exc)
        raise HTTPException(502, "Could not create payment. Please try again.")
    return {"checkout_url": payment["_links"]["checkout"]["href"], "payment_id": payment["id"]}


@app.get("/api/seller/joining-fee/verify")
async def verify_joining_fee(
    payment_id: str,
    current_user: User = Depends(require_role("seller", "admin")),
    session: AsyncSession = Depends(get_session),
):
    if current_user.joining_fee_paid:
        return {"paid": True}
    try:
        payment = await _mollie("GET", f"/payments/{payment_id}")
    except Exception as exc:
        logger.error("Mollie joining fee verify failed: %s", exc)
        raise HTTPException(502, "Could not verify payment")
    metadata = payment.get("metadata") or {}
    if str(metadata.get("user_id")) != str(current_user.id):
        raise HTTPException(403, "Payment does not belong to this account")
    if payment.get("status") == "paid":
        current_user.joining_fee_paid = True
        await session.commit()
        logger.info("Joining fee paid by user %d", current_user.id)
    return {"paid": current_user.joining_fee_paid is True}


# ── Mollie Connect (seller payout accounts) ───────────────────────────────────

MOLLIE_OAUTH_SCOPE = (
    "payments.read payments.write organizations.read profiles.read "
    "onboarding.read settlements.read balances.read"
)


def _split_name(full_name: str) -> tuple[str, str]:
    parts = (full_name or "").strip().split(None, 1)
    if len(parts) == 2:
        return parts[0], parts[1]
    return (parts[0] if parts else "User"), (parts[0] if parts else "User")


@app.get("/api/seller/mollie/connect")
async def mollie_connect_redirect(
    current_user: User = Depends(require_role("seller", "admin")),
    session: AsyncSession = Depends(get_session),
):
    """Return a Mollie onboarding URL for the seller to open.

    Mollie recommends co-branded onboarding via the Client Links API for new
    merchants (prefills signup data, reduces drop-off). We create a client link
    with the seller's details prefilled, then append our OAuth parameters so a
    single redirect handles both signup and consent. Falls back to the raw
    authorize URL if client-link creation fails.
    """
    # Generate single-use nonce to prevent OAuth callback injection (MC-1/MC-2)
    nonce = secrets.token_hex(16)
    current_user.mollie_oauth_nonce = nonce
    await session.commit()

    state = jose_jwt.encode(
        {"sub": str(current_user.id), "role": current_user.role, "nonce": nonce,
         "exp": datetime.utcnow().timestamp() + 600},
        settings.secret_key, algorithm="HS256",
    )
    oauth_params = {
        "client_id": settings.mollie_client_id,
        "redirect_uri": f"{settings.backend_url}/api/seller/mollie/callback",
        "response_type": "code",
        "scope": MOLLIE_OAUTH_SCOPE,
        "state": state,
    }

    given, family = _split_name(current_user.full_name)
    client_link_body = {
        "owner": {
            "email": current_user.email,
            "givenName": given,
            "familyName": family,
            "locale": "fi_FI",
        },
        "name": current_user.full_name or current_user.email.split("@")[0],
        "address": {"country": "FI"},
    }
    # Mollie's POST /v2/client-links requires a dashboard-created Advanced
    # Access Token (prefix `access_`) with the clients.write permission; OAuth
    # tokens are rejected with 403 regardless of requested scope.
    if settings.mollie_advanced_access_token:
        try:
            async with httpx.AsyncClient(timeout=15.0) as client:
                resp = await client.post(
                    "https://api.mollie.com/v2/client-links",
                    headers={"Authorization": f"Bearer {settings.mollie_advanced_access_token}"},
                    json=client_link_body,
                )
                resp.raise_for_status()
            link = resp.json()
            base_url = link["_links"]["clientLink"]["href"]
            return {"auth_url": f"{base_url}?{urlencode(oauth_params)}"}
        except httpx.HTTPStatusError as exc:
            logger.warning("Client Link creation failed (%s), falling back to raw OAuth: %s",
                           exc.response.status_code, exc.response.text)
        except Exception as exc:
            logger.warning("Client Link creation failed, falling back to raw OAuth: %s", exc)
    return {"auth_url": f"https://my.mollie.com/oauth2/authorize?{urlencode(oauth_params)}"}


@app.get("/api/seller/mollie/callback")
async def mollie_connect_callback(
    code: Optional[str] = None,
    state: Optional[str] = None,
    error: Optional[str] = None,
    session: AsyncSession = Depends(get_session),
):
    """Handle Mollie OAuth callback for both seller connect and platform self-OAuth.

    Mollie allows only one registered redirect URI per app, so both flows share
    this endpoint and dispatch based on `payload["platform"]` in the state JWT.
    """
    if error:
        logger.info("Mollie OAuth denied by user: %s", error)
        return RedirectResponse(f"{settings.frontend_url}/sell/dashboard?mollie=denied")
    if not code or not state:
        raise HTTPException(400, "Missing code or state")

    try:
        payload = jose_jwt.decode(state, settings.secret_key, algorithms=["HS256"])
    except (JWTError, KeyError, ValueError):
        raise HTTPException(400, "Invalid state parameter")

    # Dispatch: platform self-OAuth vs seller connect
    if payload.get("platform"):
        return await _handle_platform_oauth_callback(code, payload, session)

    try:
        user_id = int(payload["sub"])
        state_nonce = payload.get("nonce")
    except (KeyError, ValueError):
        raise HTTPException(400, "Invalid state parameter")

    # Verify and consume the single-use nonce (MC-1/MC-2)
    user = await session.get(User, user_id)
    if not user or not user.mollie_oauth_nonce or not secrets.compare_digest(user.mollie_oauth_nonce, state_nonce or ""):
        logger.warning("Mollie OAuth: invalid or reused nonce for user %d", user_id)
        raise HTTPException(400, "Invalid or expired OAuth state")
    user.mollie_oauth_nonce = None  # Consume nonce — single use only
    await session.commit()

    async with httpx.AsyncClient(timeout=15.0) as client:
        token_resp = await client.post(
            "https://api.mollie.com/oauth2/tokens",
            data={
                "grant_type": "authorization_code",
                "client_id": settings.mollie_client_id,
                "client_secret": settings.mollie_client_secret,
                "code": code,
                "redirect_uri": f"{settings.backend_url}/api/seller/mollie/callback",
            },
        )
        token_resp.raise_for_status()
        tokens = token_resp.json()
        access_token = tokens["access_token"]
        refresh_token = tokens.get("refresh_token", "")

        org_resp = await client.get(
            "https://api.mollie.com/v2/organizations/me",
            headers={"Authorization": f"Bearer {access_token}"},
        )
        org_resp.raise_for_status()
        org = org_resp.json()

        # OAuth-created payments need an explicit profileId. /profiles/me is
        # API-key-scoped (one key = one profile), so for OAuth we list all
        # profiles on the org and take the first (= the primary).
        profile_id: Optional[str] = None
        try:
            prof_resp = await client.get(
                "https://api.mollie.com/v2/profiles",
                headers={"Authorization": f"Bearer {access_token}"},
            )
            prof_resp.raise_for_status()
            profiles = prof_resp.json().get("_embedded", {}).get("profiles", [])
            if profiles:
                profile_id = profiles[0].get("id")
        except Exception as exc:
            logger.warning("Could not fetch Mollie profiles for user %d: %s", user_id, exc)

    user.stripe_account_id = org["id"]          # Mollie org ID
    user.mollie_access_token = _encrypt_token(access_token)   # MC-3: encrypted at rest
    user.mollie_refresh_token = _encrypt_token(refresh_token)
    user.mollie_profile_id = profile_id
    await session.commit()
    logger.info("Mollie Connect completed for user %d, org %s", user_id, org["id"])
    return RedirectResponse(f"{settings.frontend_url}/sell/dashboard?mollie=connected")


@app.get("/api/seller/mollie/status")
async def mollie_connect_status(current_user: User = Depends(require_role("seller", "admin"))):
    return {"connected": bool(current_user.stripe_account_id), "organization_id": current_user.stripe_account_id}


@app.get("/api/seller/mollie/onboarding")
async def mollie_onboarding_status(
    current_user: User = Depends(require_role("seller", "admin")),
    session: AsyncSession = Depends(get_session),
):
    """Check the connected seller's Mollie onboarding status using their access token."""
    if not current_user.mollie_access_token:
        return {"status": "not_connected", "can_receive_payments": False}
    try:
        resp = await _mollie_user_request(current_user, session, "GET", "/onboarding/me")
        data = resp.json()
        status = data.get("status", "unknown")
        return {
            "status": status,               # needs-data | in-progress | completed
            "can_receive_payments": status == "completed",
            "needs_data": data.get("canReceivePayments") is False,
        }
    except HTTPException:
        raise
    except Exception as exc:
        logger.error("Mollie onboarding check failed: %s", exc)
        return {"status": "unknown", "can_receive_payments": False}


@app.get("/api/seller/mollie/balance")
async def mollie_balance(
    current_user: User = Depends(require_role("seller", "admin")),
    session: AsyncSession = Depends(get_session),
):
    """Return the seller's primary Mollie balance (available + pending)."""
    if not current_user.mollie_access_token:
        return {"status": "not_connected"}
    try:
        resp = await _mollie_user_request(current_user, session, "GET", "/balances/primary")
        data = resp.json()
        return {
            "status": "ok",
            "currency": data.get("currency"),
            "available": data.get("availableAmount", {}).get("value"),
            "pending": data.get("pendingAmount", {}).get("value"),
            "balance_status": data.get("status"),
        }
    except HTTPException:
        raise
    except httpx.HTTPStatusError as exc:
        if exc.response.status_code == 403:
            return {"status": "scope_missing", "detail": "Reconnect Mollie to enable balance view"}
        logger.error("Mollie balance fetch failed: %s", exc.response.text)
        return {"status": "error"}
    except Exception as exc:
        logger.error("Mollie balance fetch failed: %s", exc)
        return {"status": "error"}


@app.get("/api/seller/mollie/settlements")
async def mollie_settlements(
    current_user: User = Depends(require_role("seller", "admin")),
    session: AsyncSession = Depends(get_session),
):
    """List the seller's recent Mollie settlements."""
    if not current_user.mollie_access_token:
        return {"status": "not_connected", "settlements": []}
    try:
        resp = await _mollie_user_request(current_user, session, "GET", "/settlements?limit=10")
        data = resp.json()
        items = data.get("_embedded", {}).get("settlements", [])
        return {
            "status": "ok",
            "settlements": [
                {
                    "id": s.get("id"),
                    "reference": s.get("reference"),
                    "created_at": s.get("createdAt"),
                    "settled_at": s.get("settledAt"),
                    "status": s.get("status"),
                    "amount": s.get("amount", {}).get("value"),
                    "currency": s.get("amount", {}).get("currency"),
                }
                for s in items
            ],
        }
    except HTTPException:
        raise
    except httpx.HTTPStatusError as exc:
        if exc.response.status_code == 403:
            return {"status": "scope_missing", "settlements": []}
        logger.error("Mollie settlements fetch failed: %s", exc.response.text)
        return {"status": "error", "settlements": []}
    except Exception as exc:
        logger.error("Mollie settlements fetch failed: %s", exc)
        return {"status": "error", "settlements": []}


@app.delete("/api/seller/mollie/disconnect")
async def mollie_disconnect(
    current_user: User = Depends(require_role("seller", "admin")),
    session: AsyncSession = Depends(get_session),
):
    current_user.stripe_account_id = None
    current_user.mollie_access_token = None
    current_user.mollie_refresh_token = None
    await session.commit()
    return {"ok": True}


# ── Payout account (legacy stubs) ─────────────────────────────────────────────

@app.post("/api/seller/stripe/onboard")
async def stripe_onboard(_: User = Depends(require_role("seller", "admin"))):
    raise HTTPException(503, "Use Mollie Connect instead.")


@app.get("/api/seller/stripe/status")
async def stripe_status(_: User = Depends(require_role("seller", "admin"))):
    return {"onboarded": False, "charges_enabled": False, "payouts_enabled": False, "account_id": None}


# ── Mollie checkout ───────────────────────────────────────────────────────────

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

    # Connect for Platforms: the host owns the payment on their own Mollie org;
    # we take a 20% application fee that lands in the platform balance.
    host = await session.get(User, listing.seller_id)
    if not host or not host.mollie_access_token:
        raise HTTPException(409, "This host hasn't finished payout setup yet. Please try another charger.")

    # Lazy backfill: hosts who connected before the profile cache landed
    # don't yet have mollie_profile_id — fetch it once and store it.
    if not host.mollie_profile_id:
        try:
            prof_resp = await _mollie_user_request(host, session, "GET", "/profiles")
            profiles = prof_resp.json().get("_embedded", {}).get("profiles", [])
            if not profiles:
                raise HTTPException(409, "This host hasn't created a payment profile in Mollie yet. Please try another charger.")
            host.mollie_profile_id = profiles[0].get("id")
            await session.commit()
        except HTTPException:
            raise
        except httpx.HTTPStatusError as exc:
            if exc.response.status_code == 403:
                logger.warning("Host %d: profiles.read scope missing — reconnect required", host.id)
                raise HTTPException(409, "This host needs to reconnect their Mollie account to grant profile access. Please try another charger.")
            logger.error("Could not fetch Mollie profiles for host %d: %s", host.id, exc.response.text)
            raise HTTPException(409, "Host's Mollie account is missing a payment profile. Please contact support.")
        except Exception as exc:
            logger.error("Could not fetch Mollie profiles for host %d: %s", host.id, exc)
            raise HTTPException(409, "Host's Mollie account is missing a payment profile. Please contact support.")

    payment_data: dict = {
        "amount": {"currency": "EUR", "value": f"{total:.2f}"},
        "description": f"{body.package_kwh} kWh — {listing.title}",
        "redirectUrl": f"{settings.frontend_url}/charge/success?booking_id={booking.id}",
        "webhookUrl": f"{settings.backend_url}/api/webhooks/mollie?webhook_secret={settings.mollie_webhook_secret}",
        "metadata": {"booking_id": str(booking.id)},
        "profileId": host.mollie_profile_id,
        "applicationFee": {
            "amount": {"currency": "EUR", "value": f"{fee:.2f}"},
            "description": "ChargedEV platform fee",
        },
    }

    idempotency_key = f"booking-{booking.id}"
    try:
        resp = await _mollie_user_request(
            host, session, "POST", "/payments",
            idempotency_key=idempotency_key, json=payment_data,
        )
        payment = resp.json()
    except HTTPException:
        raise
    except httpx.HTTPStatusError as exc:
        logger.error("Mollie payment creation failed: status=%s body=%s",
                     exc.response.status_code, exc.response.text)
        raise HTTPException(502, "Could not create payment. Please try again.")
    except Exception as exc:
        logger.error("Mollie payment creation failed: %s", exc)
        raise HTTPException(502, "Could not create payment. Please try again.")

    booking.stripe_session_id = payment["id"]  # reuse column for Mollie payment ID
    # Under applicationFee, Mollie credits the host's balance net of the fee at
    # capture time — no manual payout step needed.
    booking.paid_out = True
    booking.paid_out_at = datetime.utcnow()
    await session.commit()

    checkout_url = payment["_links"]["checkout"]["href"]
    return {"checkout_url": checkout_url, "booking_id": booking.id}


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
    listing.shelly_auth_key = body.auth_key
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
    client = ShellyClient(listing.shelly_server, listing.shelly_auth_key, listing.shelly_device_id)
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
    """WebSocket endpoint for OCPP 1.6 charge points to connect to."""
    from .db import async_session as _as
    # Verify charge point is registered before accepting the connection
    async with _as() as sess:
        db_cp = (await sess.execute(
            select(OcppChargePoint).where(OcppChargePoint.charge_point_id == charge_point_id)
        )).scalar_one_or_none()
    if not db_cp:
        logger.warning("OCPP: rejected unknown charge point %s", charge_point_id)
        await websocket.close(code=4001, reason="Unknown charge point")
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
    # Upsert
    cp = existing or OcppChargePoint(listing_id=listing_id, charge_point_id=body.charge_point_id)
    cp.charge_point_id = body.charge_point_id
    if not existing:
        session.add(cp)
    listing.ocpp_charge_point_id = body.charge_point_id
    await session.commit()
    ws_url = f"{settings.frontend_url.replace('https://', 'wss://').replace('http://', 'ws://')}/ocpp/{body.charge_point_id}".replace("chargedev.io", "chargedev-production.up.railway.app")
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
    ws_url = f"wss://chargedev-production.up.railway.app/ocpp/{cp.charge_point_id}"
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


@app.post("/api/webhooks/mollie")
async def mollie_webhook(
    request: Request,
    id: Optional[str] = Form(None),
    session: AsyncSession = Depends(get_session),
):
    """Mollie sends a form POST with the payment ID; we fetch the payment to verify status."""
    # Verify shared secret to prevent webhook spoofing
    incoming_secret = request.query_params.get("webhook_secret", "")
    expected_secret = settings.mollie_webhook_secret
    if not expected_secret or not secrets.compare_digest(incoming_secret, expected_secret):
        logger.warning("Mollie webhook: invalid or missing webhook_secret")
        raise HTTPException(401, "Invalid webhook secret")

    payment_id = id
    if not payment_id:
        # Some Mollie test calls send an empty body
        return JSONResponse({"received": True})

    logger.info("Mollie webhook received: payment_id=%s", payment_id)

    # Look up booking first so we know which token can read this payment:
    # booking payments live on the host's org (Connect for Platforms); joining
    # fees have no booking row and live on the platform.
    b = (await session.execute(
        select(Booking).where(Booking.stripe_session_id == payment_id).with_for_update()
    )).scalar_one_or_none()

    try:
        if b:
            listing = await session.get(Listing, b.listing_id)
            host = await session.get(User, listing.seller_id) if listing else None
            if not host or not host.mollie_access_token:
                logger.error("Booking %d: host has no Mollie connection — cannot verify payment", b.id)
                return JSONResponse({"received": True})
            resp = await _mollie_user_request(host, session, "GET", f"/payments/{payment_id}")
            payment = resp.json()
        else:
            payment = await _mollie("GET", f"/payments/{payment_id}")
    except Exception as exc:
        logger.error("Could not fetch Mollie payment %s: %s", payment_id, exc)
        return JSONResponse({"received": True})

    if payment.get("status") != "paid":
        logger.info("Mollie payment %s status=%s — ignoring", payment_id, payment.get("status"))
        return JSONResponse({"received": True})

    metadata = payment.get("metadata") or {}

    # Handle joining fee payments (no booking row — payment is on the platform)
    if metadata.get("type") == "joining_fee":
        user_id = int(metadata.get("user_id", 0))
        if user_id:
            host = await session.get(User, user_id)
            if host and not host.joining_fee_paid:
                host.joining_fee_paid = True
                await session.commit()
                logger.info("Joining fee confirmed via webhook for user %d", user_id)
        return JSONResponse({"received": True})

    # Fallback booking lookup via metadata (rare race: webhook before commit)
    if not b:
        booking_id = int(metadata.get("booking_id", 0))
        if booking_id:
            b = await session.get(Booking, booking_id)
            if b and b.stripe_session_id != payment_id:
                logger.warning("Booking %d payment ID mismatch — ignoring", booking_id)
                b = None

    try:
        if b and b.status == BookingStatus.pending:
            b.status = BookingStatus.confirmed
            b.pin_code = _gen_pin()
            await session.commit()
            logger.info("Booking %d confirmed via Mollie webhook", b.id)
            buyer = await session.get(User, b.buyer_id)
            listing = await session.get(Listing, b.listing_id)
            host = await session.get(User, listing.seller_id) if listing else None
            if buyer and listing:
                await send_pin_email(buyer.email, buyer.full_name, b, listing)
                if host:
                    await send_host_booking_email(host.email, host.full_name, b, listing, buyer)
            await _transfer_to_host(b, session)
            if listing and listing.ocpp_charge_point_id:
                asyncio.create_task(_ocpp_start_session(b, listing))
            elif listing and listing.shelly_device_id and listing.shelly_auth_key and listing.shelly_server:
                asyncio.create_task(_shelly_start_session(b, listing))
        else:
            logger.warning("Mollie webhook: booking not found or already processed for payment %s", payment_id)
    except Exception as exc:
        logger.error("Mollie webhook handler error: %s", exc, exc_info=True)

    return JSONResponse({"received": True})


@app.get("/api/checkout/verify/{booking_id}")
async def verify_checkout(
    booking_id: int,
    current_user: User = Depends(require_role("buyer", "seller", "admin")),
    session: AsyncSession = Depends(get_session),
):
    """Called by success page — verifies payment with Mollie and confirms booking immediately."""
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

    # Ask Mollie directly — don't wait for webhook. Payment lives on the host's
    # Mollie org (Connect for Platforms), so use their token to fetch it.
    if b.stripe_session_id:
        try:
            listing = await session.get(Listing, b.listing_id)
            host = await session.get(User, listing.seller_id) if listing else None
            if not host or not host.mollie_access_token:
                raise RuntimeError("Host has no Mollie connection")
            resp = await _mollie_user_request(host, session, "GET", f"/payments/{b.stripe_session_id}")
            payment = resp.json()
            if payment.get("status") == "paid" and b.status == BookingStatus.pending:
                b.status = BookingStatus.confirmed
                b.pin_code = _gen_pin()
                await session.commit()
                logger.info("Booking %d confirmed via verify endpoint", b.id)
                if listing:
                    await send_pin_email(current_user.email, current_user.full_name, b, listing)
                if host:
                    await send_host_booking_email(host.email, host.full_name, b, listing, current_user)
                await _transfer_to_host(b, session)
                if listing and listing.ocpp_charge_point_id:
                    asyncio.create_task(_ocpp_start_session(b, listing))
                elif listing and listing.shelly_device_id and listing.shelly_auth_key and listing.shelly_server:
                    asyncio.create_task(_shelly_start_session(b, listing))
        except Exception as exc:
            logger.error("Mollie verify error for booking %d: %s", booking_id, exc)

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
