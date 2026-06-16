"""ChargedEV API — EV charging marketplace backend."""
from __future__ import annotations

import random
import string
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from typing import Optional

from fastapi import Depends, FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
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
    LoginRequest, PlatformStats, RegisterRequest, TokenResponse, UserOut,
)


@asynccontextmanager
async def lifespan(_: FastAPI):
    await init_db()
    yield


app = FastAPI(title="ChargedEV API", version="0.1.0", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.allowed_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
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
    listing = Listing(seller_id=current_user.id, **body.model_dump())
    session.add(listing)
    await session.commit()
    await session.refresh(listing)
    return ListingOut(
        **{c.key: getattr(listing, c.key) for c in listing.__table__.columns},
        seller_name=current_user.full_name,
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
        if not b.listing:
            b.listing = await session.get(Listing, b.listing_id)
        if not b.buyer:
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
    b.completed_at = datetime.now(timezone.utc)
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
    current_user: User = Depends(require_role("buyer", "admin")),
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
