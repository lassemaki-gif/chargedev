from datetime import datetime
from typing import Optional
from pydantic import BaseModel, EmailStr, Field


# ── Auth ──────────────────────────────────────────────────────────────────────

class RegisterRequest(BaseModel):
    email: EmailStr
    password: str = Field(min_length=8)
    full_name: str = Field(min_length=2, max_length=120)
    phone: Optional[str] = None
    role: str = "buyer"  # buyer | seller


class LoginRequest(BaseModel):
    email: EmailStr
    password: str


class TokenResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"
    role: str
    full_name: str


class UserOut(BaseModel):
    id: int
    email: str
    full_name: str
    phone: Optional[str]
    iban: Optional[str]
    role: str
    is_active: bool
    created_at: datetime

    model_config = {"from_attributes": True}


class ProfileUpdate(BaseModel):
    full_name: Optional[str] = Field(None, min_length=2, max_length=120)
    phone: Optional[str] = Field(None, max_length=40)
    iban: Optional[str] = Field(None, max_length=34)


# ── Listings ──────────────────────────────────────────────────────────────────

class ListingCreate(BaseModel):
    title: str = Field(min_length=5, max_length=120)
    description: Optional[str] = None
    address: str = Field(min_length=5, max_length=300)
    city: str = Field(min_length=2, max_length=100)
    country: str = "Finland"
    charger_type: str = "Type 2"
    max_power_kw: float = Field(default=11.0, ge=1.4, le=350)
    price_per_kwh: float = Field(default=0.25, ge=0.05, le=2.00)
    instructions: Optional[str] = None


class ListingOut(BaseModel):
    id: int
    seller_id: int
    seller_name: str
    title: str
    description: Optional[str]
    address: str
    city: str
    country: str
    charger_type: str
    max_power_kw: float
    price_per_kwh: float
    is_available: bool
    instructions: Optional[str]
    created_at: datetime

    model_config = {"from_attributes": True}


# ── Bookings ──────────────────────────────────────────────────────────────────

class BookingCreate(BaseModel):
    listing_id: int
    package_kwh: int = Field(ge=20, le=80)
    scheduled_at: Optional[datetime] = None
    notes: Optional[str] = None


class BookingOut(BaseModel):
    id: int
    listing_id: int
    listing_title: str
    listing_address: str
    buyer_id: int
    buyer_name: str
    package_kwh: int
    price_per_kwh: float
    total_eur: float
    seller_earnings_eur: float
    platform_fee_eur: float
    status: str
    pin_code: Optional[str]
    paid_out: bool = False
    paid_out_at: Optional[datetime] = None
    scheduled_at: Optional[datetime]
    completed_at: Optional[datetime]
    notes: Optional[str]
    created_at: datetime

    model_config = {"from_attributes": True}


class SellerEarnings(BaseModel):
    pending_eur: float
    paid_out_eur: float
    total_eur: float
    next_payout_date: str
    iban: Optional[str]


# ── Admin stats ───────────────────────────────────────────────────────────────

class PlatformStats(BaseModel):
    total_users: int
    total_sellers: int
    total_buyers: int
    total_listings: int
    active_listings: int
    total_bookings: int
    completed_bookings: int
    total_kwh_delivered: float
    total_revenue_eur: float
    platform_earnings_eur: float
