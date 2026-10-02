from __future__ import annotations
from datetime import datetime
from enum import Enum as PyEnum
from typing import List, Optional

from sqlalchemy import (
    Boolean, DateTime, Enum, Float, ForeignKey,
    Integer, String, Text, func,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


class Base(DeclarativeBase):
    pass


class UserRole(str, PyEnum):
    buyer = "buyer"
    seller = "seller"
    admin = "admin"


class BookingStatus(str, PyEnum):
    pending = "pending"
    confirmed = "confirmed"
    active = "active"
    completed = "completed"
    cancelled = "cancelled"


class ChargerType(str, PyEnum):
    three_phase = "3-phase"
    type2 = "Type 2"
    ccs = "CCS"
    chademo = "CHAdeMO"
    cee = "CEE"


class User(Base):
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    email: Mapped[str] = mapped_column(String(255), unique=True, index=True, nullable=False)
    hashed_password: Mapped[str] = mapped_column(String(255), nullable=False)
    full_name: Mapped[str] = mapped_column(String(120), nullable=False)
    phone: Mapped[Optional[str]] = mapped_column(String(40))
    iban: Mapped[Optional[str]] = mapped_column(String(34))  # kept for legacy data, no longer used
    stripe_account_id: Mapped[Optional[str]] = mapped_column(String(255))
    role: Mapped[UserRole] = mapped_column(Enum(UserRole), default=UserRole.buyer, nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())

    listings: Mapped[List[Listing]] = relationship("Listing", back_populates="seller")
    bookings: Mapped[List[Booking]] = relationship("Booking", back_populates="buyer")


class Listing(Base):
    __tablename__ = "listings"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    seller_id: Mapped[int] = mapped_column(ForeignKey("users.id"), nullable=False)
    title: Mapped[str] = mapped_column(String(120), nullable=False)
    description: Mapped[Optional[str]] = mapped_column(Text)
    address: Mapped[str] = mapped_column(String(300), nullable=False)
    city: Mapped[str] = mapped_column(String(100), nullable=False)
    country: Mapped[str] = mapped_column(String(80), default="Finland")
    lat: Mapped[Optional[float]] = mapped_column(Float)
    lng: Mapped[Optional[float]] = mapped_column(Float)
    charger_type: Mapped[ChargerType] = mapped_column(Enum(ChargerType), default=ChargerType.type2)
    max_power_kw: Mapped[float] = mapped_column(Float, default=11.0)
    price_per_kwh: Mapped[float] = mapped_column(Float, default=0.25)
    is_available: Mapped[bool] = mapped_column(Boolean, default=True)
    instructions: Mapped[Optional[str]] = mapped_column(Text)
    availability_json: Mapped[Optional[str]] = mapped_column(Text)  # JSON weekly schedule
    # Shelly smart device integration (Premium hosts)
    shelly_device_id: Mapped[Optional[str]] = mapped_column(String(255))
    shelly_auth_key: Mapped[Optional[str]] = mapped_column(String(255))
    shelly_server: Mapped[Optional[str]] = mapped_column(String(255))
    # OCPP charger integration (Premium hosts — alternative to Shelly)
    ocpp_charge_point_id: Mapped[Optional[str]] = mapped_column(String(255), unique=True, index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())

    seller: Mapped[User] = relationship("User", back_populates="listings")
    bookings: Mapped[List[Booking]] = relationship("Booking", back_populates="listing")


PACKAGES_KWH = [20, 40, 60, 80]


class Review(Base):
    __tablename__ = "reviews"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    booking_id: Mapped[int] = mapped_column(ForeignKey("bookings.id"), unique=True, nullable=False)
    listing_id: Mapped[int] = mapped_column(ForeignKey("listings.id"), nullable=False)
    reviewer_id: Mapped[int] = mapped_column(ForeignKey("users.id"), nullable=False)
    rating: Mapped[int] = mapped_column(Integer, nullable=False)  # 1-5
    comment: Mapped[Optional[str]] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())


class Booking(Base):
    __tablename__ = "bookings"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    listing_id: Mapped[int] = mapped_column(ForeignKey("listings.id"), nullable=False)
    buyer_id: Mapped[int] = mapped_column(ForeignKey("users.id"), nullable=False)
    package_kwh: Mapped[int] = mapped_column(Integer, nullable=False)  # 20/40/60/80
    price_per_kwh: Mapped[float] = mapped_column(Float, nullable=False)
    total_eur: Mapped[float] = mapped_column(Float, nullable=False)
    seller_earnings_eur: Mapped[float] = mapped_column(Float, nullable=False)
    platform_fee_eur: Mapped[float] = mapped_column(Float, nullable=False)
    status: Mapped[BookingStatus] = mapped_column(Enum(BookingStatus), default=BookingStatus.pending)
    stripe_session_id: Mapped[Optional[str]] = mapped_column(String(255), unique=True, index=True)
    stripe_transfer_id: Mapped[Optional[str]] = mapped_column(String(255))
    pin_code: Mapped[Optional[str]] = mapped_column(String(6))
    scheduled_at: Mapped[Optional[datetime]] = mapped_column(DateTime)
    completed_at: Mapped[Optional[datetime]] = mapped_column(DateTime)
    notes: Mapped[Optional[str]] = mapped_column(Text)
    paid_out: Mapped[bool] = mapped_column(Boolean, default=False)
    paid_out_at: Mapped[Optional[datetime]] = mapped_column(DateTime)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())

    listing: Mapped[Listing] = relationship("Listing", back_populates="bookings")
    buyer: Mapped[User] = relationship("User", back_populates="bookings")


class OcppChargePoint(Base):
    """Registered OCPP charge point (wallbox) linked to a listing."""
    __tablename__ = "ocpp_charge_points"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    listing_id: Mapped[int] = mapped_column(ForeignKey("listings.id"), unique=True, nullable=False)
    charge_point_id: Mapped[str] = mapped_column(String(255), unique=True, nullable=False)
    vendor: Mapped[Optional[str]] = mapped_column(String(255))
    model: Mapped[Optional[str]] = mapped_column(String(255))
    status: Mapped[str] = mapped_column(String(50), default="offline")
    last_heartbeat: Mapped[Optional[datetime]] = mapped_column(DateTime)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())


class OcppSession(Base):
    """Active or completed OCPP charging session."""
    __tablename__ = "ocpp_sessions"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    booking_id: Mapped[int] = mapped_column(ForeignKey("bookings.id"), unique=True, nullable=False)
    charge_point_id: Mapped[str] = mapped_column(String(255), nullable=False)
    transaction_id: Mapped[Optional[int]] = mapped_column(Integer)
    id_tag: Mapped[str] = mapped_column(String(20), nullable=False)  # token sent to charger
    target_kwh: Mapped[float] = mapped_column(Float, nullable=False)
    energy_start_wh: Mapped[float] = mapped_column(Float, default=0.0)
    energy_consumed_wh: Mapped[float] = mapped_column(Float, default=0.0)
    status: Mapped[str] = mapped_column(String(20), default="starting")  # starting|active|completed|error
    started_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
    stopped_at: Mapped[Optional[datetime]] = mapped_column(DateTime)


class ShellySession(Base):
    """Tracks an active Shelly-controlled charging session."""
    __tablename__ = "shelly_sessions"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    booking_id: Mapped[int] = mapped_column(ForeignKey("bookings.id"), unique=True, nullable=False)
    listing_id: Mapped[int] = mapped_column(ForeignKey("listings.id"), nullable=False)
    device_id: Mapped[str] = mapped_column(String(255), nullable=False)
    auth_key: Mapped[str] = mapped_column(String(255), nullable=False)
    server: Mapped[str] = mapped_column(String(255), nullable=False)
    target_kwh: Mapped[float] = mapped_column(Float, nullable=False)
    energy_start_wh: Mapped[float] = mapped_column(Float, default=0.0)
    status: Mapped[str] = mapped_column(String(20), default="active")  # active | completed | error
    started_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
    stopped_at: Mapped[Optional[datetime]] = mapped_column(DateTime)
