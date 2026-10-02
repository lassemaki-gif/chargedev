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

import httpx
import resend
import stripe as stripe_lib
from fastapi import Depends, FastAPI, HTTPException, Request, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from slowapi import Limiter, _rate_limit_exceeded_handler
from slowapi.errors import RateLimitExceeded
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from ocpp.routing import on
from ocpp.v16 import ChargePoint as OcppCp
from ocpp.v16 import call, call_result
from ocpp.v16.enums import Action, RegistrationStatus, RemoteStartStopStatus

from .auth import (
    create_access_token, get_current_user, hash_password,
    require_role, verify_password,
)
from .config import settings
from .db import get_session, init_db
from .models import (
    Booking, BookingStatus, Listing, PACKAGES_KWH, Review,
    OcppChargePoint, OcppSession, ShellySession, User, UserRole,
)
from .schemas import (
    BookingCreate, BookingOut, ListingCreate, ListingOut,
    LoginRequest, OcppChargePointOut, OcppRegisterRequest, PlatformStats,
    ProfileUpdate, RegisterRequest, ReviewCreate, ReviewOut, SellerEarnings,
    ShellyConfigRequest, ShellyStatusOut, StripeOnboardResponse,
    StripeStatusResponse, TokenResponse, UserOut, WeeklyAvailabilitySchema,
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
                                    ss.stopped_at = datetime.now(timezone.utc)
                                    booking = await sess.get(Booking, ss.booking_id)
                                    if booking and booking.status == BookingStatus.confirmed:
                                        booking.status = BookingStatus.completed
                                        booking.completed_at = datetime.now(timezone.utc)
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
                ocpp_sess.stopped_at = datetime.now(timezone.utc)
                booking = await sess.get(Booking, ocpp_sess.booking_id)
                if booking and booking.status == BookingStatus.confirmed:
                    booking.status = BookingStatus.completed
                    booking.completed_at = datetime.now(timezone.utc)
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


stripe_lib.api_key = settings.stripe_secret_key
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


limiter = Limiter(key_func=real_ip, default_limits=[])


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


def _calc_amounts(package_kwh: int, price_per_kwh: float) -> tuple[float, float, float]:
    """Return (total_eur, fee_eur, earnings_eur) using Decimal to avoid float rounding errors."""
    total = (Decimal(str(package_kwh)) * Decimal(str(price_per_kwh))).quantize(Decimal("0.01"), ROUND_HALF_UP)
    fee = (total * Decimal(str(settings.platform_fee_pct))).quantize(Decimal("0.01"), ROUND_HALF_UP)
    earnings = (total - fee).quantize(Decimal("0.01"), ROUND_HALF_UP)
    return float(total), float(fee), float(earnings)


def _gen_pin() -> str:
    return "".join(secrets.choice(string.digits) for _ in range(6))


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


async def _transfer_to_host(booking: Booking, stripe_account_id: str, session: AsyncSession) -> None:
    """Create an immediate Stripe Transfer of 80% to the host's connected account."""
    try:
        transfer = stripe_lib.Transfer.create(
            amount=int(booking.seller_earnings_eur * 100),  # cents
            currency="eur",
            destination=stripe_account_id,
            transfer_group=f"booking_{booking.id}",
            description=f"ChargedEV booking #{booking.id} — host earnings",
            idempotency_key=f"transfer_booking_{booking.id}",
        )
        booking.stripe_transfer_id = transfer.id
        booking.paid_out = True
        booking.paid_out_at = datetime.now(timezone.utc)
        await session.commit()
        logger.info("Transfer %s created for booking %d (€%.2f)", transfer.id, booking.id, booking.seller_earnings_eur)
    except Exception as exc:
        logger.error("Stripe transfer failed for booking %d: %s", booking.id, exc)


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

    stripe_onboarded = False
    if current_user.stripe_account_id:
        try:
            acct = stripe_lib.Account.retrieve(current_user.stripe_account_id)
            stripe_onboarded = acct.charges_enabled and acct.payouts_enabled
        except Exception:
            pass

    return SellerEarnings(
        pending_eur=round(pending, 2),
        paid_out_eur=round(paid, 2),
        total_eur=round(pending + paid, 2),
        stripe_onboarded=stripe_onboarded,
        stripe_account_id=current_user.stripe_account_id,
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
    b.completed_at = datetime.now(timezone.utc)
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
    """Retry a Stripe transfer for a booking where the automatic transfer failed."""
    b = await session.get(Booking, booking_id)
    if not b:
        raise HTTPException(404, "Booking not found")
    if b.paid_out and b.stripe_transfer_id:
        return {"detail": "Already transferred", "transfer_id": b.stripe_transfer_id}
    listing = await session.get(Listing, b.listing_id)
    host = await session.get(User, listing.seller_id)
    if not host or not host.stripe_account_id:
        raise HTTPException(422, "Host has no Stripe Connect account")
    await _transfer_to_host(b, host.stripe_account_id, session)
    return {
        "booking_id": b.id,
        "transfer_id": b.stripe_transfer_id,
        "amount_eur": b.seller_earnings_eur,
        "paid_out": b.paid_out,
    }


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


# ── Stripe Connect (host onboarding) ─────────────────────────────────────────

@app.post("/api/seller/stripe/onboard", response_model=StripeOnboardResponse)
async def stripe_onboard(
    current_user: User = Depends(require_role("seller", "admin")),
    session: AsyncSession = Depends(get_session),
):
    """Create or retrieve a Stripe Express account and return an onboarding link."""
    if not current_user.stripe_account_id:
        account = stripe_lib.Account.create(
            type="express",
            email=current_user.email,
            capabilities={"transfers": {"requested": True}},
            business_type="individual",
        )
        current_user.stripe_account_id = account.id
        await session.commit()

    link = stripe_lib.AccountLink.create(
        account=current_user.stripe_account_id,
        refresh_url=f"{settings.frontend_url}/sell/dashboard?stripe=refresh",
        return_url=f"{settings.frontend_url}/sell/dashboard?stripe=success",
        type="account_onboarding",
    )
    return StripeOnboardResponse(url=link.url)


@app.get("/api/seller/stripe/status", response_model=StripeStatusResponse)
async def stripe_status(
    current_user: User = Depends(require_role("seller", "admin")),
):
    """Check whether the host's Stripe Connect account is fully onboarded."""
    if not current_user.stripe_account_id:
        return StripeStatusResponse(
            onboarded=False, charges_enabled=False, payouts_enabled=False, account_id=None
        )
    try:
        acct = stripe_lib.Account.retrieve(current_user.stripe_account_id)
        return StripeStatusResponse(
            onboarded=acct.charges_enabled and acct.payouts_enabled,
            charges_enabled=acct.charges_enabled,
            payouts_enabled=acct.payouts_enabled,
            account_id=current_user.stripe_account_id,
        )
    except Exception as exc:
        logger.error("Stripe account retrieve failed: %s", exc)
        raise HTTPException(502, "Could not reach Stripe")


# ── Stripe checkout ───────────────────────────────────────────────────────────

@app.post("/api/checkout")
async def create_checkout(
    body: BookingCreate,
    current_user: User = Depends(require_role("buyer", "seller", "admin")),
    session: AsyncSession = Depends(get_session),
):
    if body.package_kwh not in PACKAGES_KWH:
        raise HTTPException(422, f"Package must be one of {PACKAGES_KWH} kWh")
    listing = await session.get(Listing, body.listing_id)
    if not listing or not listing.is_available:
        raise HTTPException(404, "Listing not found or unavailable")
    if listing.seller_id == current_user.id:
        raise HTTPException(403, "You cannot book your own listing")

    total, fee, earnings = _calc_amounts(body.package_kwh, listing.price_per_kwh)

    # Create pending booking (no PIN yet — assigned after payment)
    booking = Booking(
        listing_id=listing.id,
        buyer_id=current_user.id,
        package_kwh=body.package_kwh,
        price_per_kwh=listing.price_per_kwh,
        total_eur=total,
        seller_earnings_eur=earnings,
        platform_fee_eur=fee,
        status=BookingStatus.pending,
        notes=body.notes,
    )
    session.add(booking)
    await session.flush()  # get booking.id before commit

    # Create Stripe Checkout session
    checkout = stripe_lib.checkout.Session.create(
        payment_method_types=["card"],
        mode="payment",
        line_items=[{
            "price_data": {
                "currency": "eur",
                "unit_amount": int(total * 100),  # cents
                "product_data": {
                    "name": f"{body.package_kwh} kWh — {listing.title}",
                    "description": f"{listing.address}, {listing.city}",
                },
            },
            "quantity": 1,
        }],
        metadata={"booking_id": str(booking.id)},
        success_url=f"{settings.frontend_url}/charge/success?session_id={{CHECKOUT_SESSION_ID}}",
        cancel_url=f"{settings.frontend_url}/charge/{listing.id}?cancelled=1",
    )

    booking.stripe_session_id = checkout.id
    await session.commit()

    return {"checkout_url": checkout.url, "booking_id": booking.id}


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
    await websocket.accept(subprotocol="ocpp1.6")
    cp = ChargedEVChargePoint(charge_point_id, websocket)
    _ocpp_connections[charge_point_id] = cp
    logger.info("OCPP charge point connected: %s", charge_point_id)
    # Update DB status to online
    from .db import async_session as _as
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


@app.post("/api/webhooks/stripe")
async def stripe_webhook(request: Request, session: AsyncSession = Depends(get_session)):
    payload = await request.body()
    sig = request.headers.get("stripe-signature", "")

    try:
        event = stripe_lib.Webhook.construct_event(
            payload, sig, settings.stripe_webhook_secret
        )
    except Exception as exc:
        logger.error("Webhook signature verification failed: %s", exc)
        raise HTTPException(400, "Invalid webhook signature")

    event_type = event.get("type") if isinstance(event, dict) else getattr(event, "type", "unknown")
    logger.info("Stripe webhook received: type=%s", event_type)

    try:
        if event_type == "checkout.session.completed":
            # Support both dict-style (older Stripe lib) and attribute-style (newer)
            data_obj = event["data"]["object"] if isinstance(event, dict) else event.data.object
            metadata = data_obj.get("metadata", {}) if isinstance(data_obj, dict) else (getattr(data_obj, "metadata", None) or {})
            booking_id = int(metadata.get("booking_id", 0))
            logger.info("checkout.session.completed booking_id=%s metadata=%s", booking_id, metadata)
            # Cross-check session ID to prevent booking_id metadata tampering
            session_id_from_event = data_obj.get("id", "") if isinstance(data_obj, dict) else getattr(data_obj, "id", "")
            if booking_id:
                b = await session.get(Booking, booking_id)
                if b and b.stripe_session_id != session_id_from_event:
                    logger.warning("Booking %d session ID mismatch — ignoring event", booking_id)
                elif b and b.status == BookingStatus.pending and not b.stripe_transfer_id:
                    b.status = BookingStatus.confirmed
                    b.pin_code = _gen_pin()
                    await session.commit()
                    logger.info("Booking %d confirmed via webhook", booking_id)
                    buyer = await session.get(User, b.buyer_id)
                    listing = await session.get(Listing, b.listing_id)
                    host = await session.get(User, listing.seller_id) if listing else None
                    if buyer and listing:
                        await send_pin_email(buyer.email, buyer.full_name, b, listing)
                        if host:
                            await send_host_booking_email(host.email, host.full_name, b, listing, buyer)
                    # Immediately transfer 80% to host's Stripe Connect account
                    if host and host.stripe_account_id:
                        await _transfer_to_host(b, host.stripe_account_id, session)
                    else:
                        logger.warning(
                            "Booking %d: host has no Stripe account — transfer skipped", booking_id
                        )
                    # Auto-start: Shelly relay OR OCPP (mutually exclusive per listing)
                    if listing and listing.ocpp_charge_point_id:
                        asyncio.create_task(_ocpp_start_session(b, listing))
                    elif listing and listing.shelly_device_id and listing.shelly_auth_key and listing.shelly_server:
                        asyncio.create_task(_shelly_start_session(b, listing))
                else:
                    logger.warning("Booking %d status=%s transfer=%s", booking_id, b.status if b else "NOT FOUND", b.stripe_transfer_id if b else None)
            else:
                logger.warning("No booking_id in metadata: %s", metadata)
    except Exception as exc:
        logger.error("Webhook handler error: %s", exc, exc_info=True)
        return JSONResponse(status_code=500, content={"received": False})

    return JSONResponse({"received": True})


@app.get("/api/checkout/verify/{session_id}")
async def verify_checkout(
    session_id: str,
    current_user: User = Depends(require_role("buyer", "seller", "admin")),
    session: AsyncSession = Depends(get_session),
):
    """Called by success page — verifies payment with Stripe and confirms booking immediately."""
    b = (await session.execute(
        select(Booking).where(Booking.stripe_session_id == session_id)
    )).scalar_one_or_none()

    if not b or b.buyer_id != current_user.id:
        raise HTTPException(404, "Booking not found")

    # Already confirmed — just return it
    if b.status == BookingStatus.confirmed and b.pin_code:
        b.listing = await session.get(Listing, b.listing_id)
        b.buyer = current_user
        return _booking_out(b, show_pin=True)

    # Ask Stripe directly — don't wait for webhook
    try:
        stripe_session = stripe_lib.checkout.Session.retrieve(session_id)
        # Verify the Stripe session ID matches the booking
        if stripe_session.get("id") != session_id:
            raise HTTPException(400, "Session ID mismatch")
        if stripe_session.payment_status == "paid" and b.status == BookingStatus.pending and not b.stripe_transfer_id:
            b.status = BookingStatus.confirmed
            b.pin_code = _gen_pin()
            await session.commit()
            logger.info("Booking %d confirmed via verify endpoint", b.id)
            listing = await session.get(Listing, b.listing_id)
            await send_pin_email(current_user.email, current_user.full_name, b, listing)
            host = await session.get(User, listing.seller_id)
            if host:
                await send_host_booking_email(host.email, host.full_name, b, listing, current_user)
                if host.stripe_account_id:
                    await _transfer_to_host(b, host.stripe_account_id, session)
            if listing and listing.ocpp_charge_point_id:
                asyncio.create_task(_ocpp_start_session(b, listing))
            elif listing and listing.shelly_device_id and listing.shelly_auth_key and listing.shelly_server:
                asyncio.create_task(_shelly_start_session(b, listing))
    except Exception as exc:
        logger.error("Stripe verify error: %s", exc)

    b.listing = await session.get(Listing, b.listing_id)
    b.buyer = current_user
    return _booking_out(b, show_pin=True)


@app.get("/api/bookings/by-session/{session_id}")
async def booking_by_session(
    session_id: str,
    current_user: User = Depends(require_role("buyer", "seller", "admin")),
    session: AsyncSession = Depends(get_session),
):
    b = (await session.execute(
        select(Booking).where(Booking.stripe_session_id == session_id)
    )).scalar_one_or_none()
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
