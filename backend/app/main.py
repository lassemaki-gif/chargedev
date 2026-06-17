"""ChargedEV API — EV charging marketplace backend."""
from __future__ import annotations

import logging
import random
import string
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from typing import Optional

import httpx
import resend
import stripe as stripe_lib
from fastapi import Depends, FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from .auth import (
    create_access_token, get_current_user, hash_password,
    require_role, verify_password,
)
from .config import settings
from .db import get_session, init_db
from .models import (
    Booking, BookingStatus, Listing, PACKAGES_KWH, User, UserRole,
)
from .schemas import (
    BookingCreate, BookingOut, ListingCreate, ListingOut,
    LoginRequest, PlatformStats, ProfileUpdate, RegisterRequest,
    SellerEarnings, TokenResponse, UserOut,
)


@asynccontextmanager
async def lifespan(_: FastAPI):
    await init_db()
    yield


stripe_lib.api_key = settings.stripe_secret_key
resend.api_key = settings.resend_api_key
logger = logging.getLogger(__name__)


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
                <tr><td style="padding:6px 0;border-bottom:1px solid #eee">Charger</td><td style="padding:6px 0;border-bottom:1px solid #eee;text-align:right;font-weight:600;color:#111">{listing.title}</td></tr>
                <tr><td style="padding:6px 0;border-bottom:1px solid #eee">Address</td><td style="padding:6px 0;border-bottom:1px solid #eee;text-align:right;color:#111">{listing.address}, {listing.city}</td></tr>
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
              <p style="color:#555">Hi {host_name}, someone has booked your charger and payment is confirmed.</p>
              <div style="background:#0A0F1E;border-radius:12px;padding:24px;margin:24px 0">
                <p style="color:#9CA3AF;font-size:12px;text-transform:uppercase;letter-spacing:0.1em;margin:0 0 8px">Session PIN</p>
                <p style="color:#22C55E;font-size:40px;font-family:monospace;font-weight:700;letter-spacing:0.2em;margin:0">{booking.pin_code}</p>
                <p style="color:#9CA3AF;font-size:12px;margin:8px 0 0">Share this PIN with the driver when they arrive.</p>
              </div>
              <table style="width:100%;font-size:14px;color:#555;border-collapse:collapse">
                <tr><td style="padding:6px 0;border-bottom:1px solid #eee">Driver</td><td style="padding:6px 0;border-bottom:1px solid #eee;text-align:right;color:#111">{buyer.full_name}</td></tr>
                <tr><td style="padding:6px 0;border-bottom:1px solid #eee">Package</td><td style="padding:6px 0;border-bottom:1px solid #eee;text-align:right;color:#111">{booking.package_kwh} kWh</td></tr>
                <tr><td style="padding:6px 0;border-bottom:1px solid #eee">Your earnings</td><td style="padding:6px 0;border-bottom:1px solid #eee;text-align:right;font-weight:700;color:#22C55E">€{booking.seller_earnings_eur:.2f}</td></tr>
                <tr><td style="padding:6px 0">Charger</td><td style="padding:6px 0;text-align:right;color:#111">{listing.address}, {listing.city}</td></tr>
              </table>
              <p style="color:#555;font-size:14px;margin-top:20px">Once the session is done, mark it as complete in your <a href="https://chargedev.io/sell/dashboard" style="color:#22C55E">host dashboard</a>.</p>
              <p style="color:#9CA3AF;font-size:12px;margin-top:24px">ChargedEV · chargedev.io</p>
            </div>
            """,
        })
        logger.info("Host booking email sent to %s", host_email)
    except Exception as exc:
        logger.error("Failed to send host booking email: %s", exc)

app = FastAPI(title="ChargedEV API", version="0.1.0", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.allowed_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.exception_handler(Exception)
async def global_exception_handler(request: Request, exc: Exception):
    origin = request.headers.get("origin", "")
    logger.error("Unhandled error on %s: %s: %s", request.url.path, type(exc).__name__, exc)
    return JSONResponse(
        status_code=500,
        content={"detail": f"{type(exc).__name__}: {exc}"},
        headers={"Access-Control-Allow-Origin": origin, "Access-Control-Allow-Credentials": "true"},
    )


def _gen_pin() -> str:
    return "".join(random.choices(string.digits, k=6))


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


# ── Health ────────────────────────────────────────────────────────────────────

@app.get("/api/health")
async def health() -> dict:
    return {"ok": True}


# ── Auth ──────────────────────────────────────────────────────────────────────

@app.post("/api/auth/register", response_model=TokenResponse)
async def register(body: RegisterRequest, session: AsyncSession = Depends(get_session)):
    if body.role not in ("buyer", "seller"):
        raise HTTPException(400, "role must be buyer or seller")
    existing = (await session.execute(select(User).where(User.email == body.email))).scalar_one_or_none()
    if existing:
        raise HTTPException(400, "Email already registered")
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
    return TokenResponse(
        access_token=create_access_token(user.id, user.role),
        role=user.role,
        full_name=user.full_name,
    )


@app.post("/api/auth/login", response_model=TokenResponse)
async def login(body: LoginRequest, session: AsyncSession = Depends(get_session)):
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
        result.append(ListingOut(
            **{c.key: getattr(r, c.key) for c in r.__table__.columns},
            seller_name=seller.full_name if seller else "—",
        ))
    return result


@app.get("/api/listings/{listing_id}", response_model=ListingOut)
async def get_listing(listing_id: int, session: AsyncSession = Depends(get_session)):
    r = await session.get(Listing, listing_id)
    if not r:
        raise HTTPException(404, "Listing not found")
    seller = await session.get(User, r.seller_id)
    return ListingOut(
        **{c.key: getattr(r, c.key) for c in r.__table__.columns},
        seller_name=seller.full_name if seller else "—",
    )


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
    return ListingOut(
        **{c.key: getattr(listing, c.key) for c in listing.__table__.columns},
        seller_name=current_user.full_name,
    )


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
    if body.iban is not None:
        current_user.iban = body.iban.upper().replace(" ", "")
    await session.commit()
    await session.refresh(current_user)
    return current_user


@app.get("/api/seller/earnings", response_model=SellerEarnings)
async def seller_earnings(
    current_user: User = Depends(require_role("seller", "admin")),
    session: AsyncSession = Depends(get_session),
):
    from datetime import date
    import calendar

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

    pending = sum(b.seller_earnings_eur for b in bookings if not b.paid_out)
    paid = sum(b.seller_earnings_eur for b in bookings if b.paid_out)

    # Next payout = 1st of next month
    today = date.today()
    if today.month == 12:
        next_payout = date(today.year + 1, 1, 1)
    else:
        next_payout = date(today.year, today.month + 1, 1)

    return SellerEarnings(
        pending_eur=round(pending, 2),
        paid_out_eur=round(paid, 2),
        total_eur=round(pending + paid, 2),
        next_payout_date=next_payout.isoformat(),
        iban=current_user.iban,
    )


@app.get("/api/seller/listings", response_model=list[ListingOut])
async def my_listings(
    current_user: User = Depends(require_role("seller", "admin")),
    session: AsyncSession = Depends(get_session),
):
    rows = (await session.execute(
        select(Listing).where(Listing.seller_id == current_user.id)
    )).scalars().all()
    return [
        ListingOut(**{c.key: getattr(r, c.key) for c in r.__table__.columns}, seller_name=current_user.full_name)
        for r in rows
    ]


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
    b.status = BookingStatus.completed
    b.completed_at = datetime.utcnow()
    await session.commit()
    return {"status": "completed"}


# ── Buyer endpoints ───────────────────────────────────────────────────────────

@app.post("/api/bookings", response_model=BookingOut)
async def create_booking(
    body: BookingCreate,
    current_user: User = Depends(require_role("buyer", "admin")),
    session: AsyncSession = Depends(get_session),
):
    if body.package_kwh not in PACKAGES_KWH:
        raise HTTPException(422, f"Package must be one of {PACKAGES_KWH} kWh")
    listing = await session.get(Listing, body.listing_id)
    if not listing or not listing.is_available:
        raise HTTPException(404, "Listing not found or unavailable")

    total = round(body.package_kwh * listing.price_per_kwh, 2)
    fee = round(total * settings.platform_fee_pct, 2)
    earnings = round(total - fee, 2)

    booking = Booking(
        listing_id=listing.id,
        buyer_id=current_user.id,
        package_kwh=body.package_kwh,
        price_per_kwh=listing.price_per_kwh,
        total_eur=total,
        seller_earnings_eur=earnings,
        platform_fee_eur=fee,
        status=BookingStatus.confirmed,
        pin_code=_gen_pin(),
        scheduled_at=body.scheduled_at,
        notes=body.notes,
    )
    session.add(booking)
    await session.commit()
    await session.refresh(booking)
    booking.listing = listing
    booking.buyer = current_user
    return _booking_out(booking, show_pin=True)


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


@app.put("/api/admin/sellers/{seller_id}/payout")
async def admin_payout(
    seller_id: int,
    _: User = Depends(require_role("admin")),
    session: AsyncSession = Depends(get_session),
):
    """Mark all completed, unpaid bookings for a seller as paid out."""
    seller = await session.get(User, seller_id)
    if not seller:
        raise HTTPException(404, "Seller not found")

    listing_ids = [
        r.id for r in (await session.execute(
            select(Listing.id).where(Listing.seller_id == seller_id)
        )).all()
    ]
    rows = (await session.execute(
        select(Booking).where(
            Booking.listing_id.in_(listing_ids),
            Booking.status == BookingStatus.completed,
            Booking.paid_out == False,
        )
    )).scalars().all()

    now = datetime.utcnow()
    total = 0.0
    for b in rows:
        b.paid_out = True
        b.paid_out_at = now
        total += b.seller_earnings_eur
    await session.commit()

    return {
        "seller": seller.full_name,
        "iban": seller.iban,
        "bookings_paid": len(rows),
        "amount_eur": round(total, 2),
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

    total = round(body.package_kwh * listing.price_per_kwh, 2)
    fee = round(total * settings.platform_fee_pct, 2)
    earnings = round(total - fee, 2)

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
        raise HTTPException(400, f"Webhook error: {exc}")

    event_type = event.get("type") if isinstance(event, dict) else getattr(event, "type", "unknown")
    logger.info("Stripe webhook received: type=%s", event_type)

    try:
        if event_type == "checkout.session.completed":
            # Support both dict-style (older Stripe lib) and attribute-style (newer)
            data_obj = event["data"]["object"] if isinstance(event, dict) else event.data.object
            metadata = data_obj.get("metadata", {}) if isinstance(data_obj, dict) else (getattr(data_obj, "metadata", None) or {})
            booking_id = int(metadata.get("booking_id", 0))
            logger.info("checkout.session.completed booking_id=%s metadata=%s", booking_id, metadata)
            if booking_id:
                b = await session.get(Booking, booking_id)
                if b and b.status == BookingStatus.pending:
                    b.status = BookingStatus.confirmed
                    b.pin_code = _gen_pin()
                    await session.commit()
                    logger.info("Booking %d confirmed PIN=%s", booking_id, b.pin_code)
                    buyer = await session.get(User, b.buyer_id)
                    listing = await session.get(Listing, b.listing_id)
                    if buyer and listing:
                        await send_pin_email(buyer.email, buyer.full_name, b, listing)
                        host = await session.get(User, listing.seller_id)
                        if host:
                            await send_host_booking_email(host.email, host.full_name, b, listing, buyer)
                else:
                    logger.warning("Booking %d status=%s", booking_id, b.status if b else "NOT FOUND")
            else:
                logger.warning("No booking_id in metadata: %s", metadata)
    except Exception as exc:
        logger.error("Webhook handler error: %s", exc, exc_info=True)
        return JSONResponse({"received": True, "error": str(exc)})

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
        if stripe_session.payment_status == "paid" and b.status == BookingStatus.pending:
            b.status = BookingStatus.confirmed
            b.pin_code = _gen_pin()
            await session.commit()
            logger.info("Booking %d confirmed via verify endpoint, PIN=%s", b.id, b.pin_code)
            listing = await session.get(Listing, b.listing_id)
            await send_pin_email(current_user.email, current_user.full_name, b, listing)
            # Notify host
            host = await session.get(User, listing.seller_id)
            if host:
                await send_host_booking_email(host.email, host.full_name, b, listing, current_user)
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
