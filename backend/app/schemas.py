import re
from datetime import datetime
from typing import Optional
from pydantic import BaseModel, EmailStr, Field, field_validator

_SHELLY_HOST_RE = re.compile(r'^[a-z0-9-]+\.shelly\.cloud$')


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
    stripe_account_id: Optional[str]
    role: str
    is_active: bool
    created_at: datetime

    model_config = {"from_attributes": True}


class ProfileUpdate(BaseModel):
    full_name: Optional[str] = Field(None, min_length=2, max_length=120)
    phone: Optional[str] = Field(None, max_length=40)


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
    lat: Optional[float]
    lng: Optional[float]
    charger_type: str
    max_power_kw: float
    price_per_kwh: float
    is_available: bool
    instructions: Optional[str]
    availability_json: Optional[str]
    avg_rating: Optional[float] = None
    review_count: int = 0
    shelly_enabled: bool = False
    created_at: datetime

    model_config = {"from_attributes": True}


class ShellyConfigRequest(BaseModel):
    device_id: str
    auth_key: str
    server: str = "shelly-91-cloud.shelly.cloud"

    @field_validator("server")
    @classmethod
    def validate_server(cls, v: str) -> str:
        if not _SHELLY_HOST_RE.match(v):
            raise ValueError("server must be a valid Shelly Cloud hostname (*.shelly.cloud)")
        return v


class ShellyStatusOut(BaseModel):
    connected: bool
    device_id: Optional[str] = None
    relay_on: Optional[bool] = None
    power_w: Optional[float] = None
    energy_total_wh: Optional[float] = None
    error: Optional[str] = None


class DayAvailability(BaseModel):
    enabled: bool = True
    start: Optional[str] = Field(None, pattern=r'^\d{2}:\d{2}$')
    end: Optional[str] = Field(None, pattern=r'^\d{2}:\d{2}$')

class WeeklyAvailabilitySchema(BaseModel):
    mon: Optional[DayAvailability] = None
    tue: Optional[DayAvailability] = None
    wed: Optional[DayAvailability] = None
    thu: Optional[DayAvailability] = None
    fri: Optional[DayAvailability] = None
    sat: Optional[DayAvailability] = None
    sun: Optional[DayAvailability] = None


# ── Bookings ──────────────────────────────────────────────────────────────────

class BookingCreate(BaseModel):
    listing_id: int
    package_kwh: int = Field(ge=20, le=80)
    scheduled_at: Optional[datetime] = None
    notes: Optional[str] = Field(None, max_length=500)


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


class ReviewCreate(BaseModel):
    booking_id: int
    rating: int = Field(ge=1, le=5)
    comment: Optional[str] = Field(None, max_length=500)


class ReviewOut(BaseModel):
    id: int
    booking_id: int
    listing_id: int
    reviewer_id: int
    reviewer_name: str
    rating: int
    comment: Optional[str]
    created_at: datetime

    model_config = {"from_attributes": True}


class SellerEarnings(BaseModel):
    pending_eur: float
    paid_out_eur: float
    total_eur: float
    stripe_onboarded: bool
    stripe_account_id: Optional[str]


class StripeOnboardResponse(BaseModel):
    url: str


class StripeStatusResponse(BaseModel):
    onboarded: bool
    charges_enabled: bool
    payouts_enabled: bool
    account_id: Optional[str]


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
