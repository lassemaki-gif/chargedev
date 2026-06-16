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
    type2 = "Type 2"
    ccs = "CCS"
    chademo = "CHAdeMO"
    schuko = "Schuko"


class User(Base):
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    email: Mapped[str] = mapped_column(String(255), unique=True, index=True, nullable=False)
    hashed_password: Mapped[str] = mapped_column(String(255), nullable=False)
    full_name: Mapped[str] = mapped_column(String(120), nullable=False)
    phone: Mapped[Optional[str]] = mapped_column(String(40))
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
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())

    seller: Mapped[User] = relationship("User", back_populates="listings")
    bookings: Mapped[List[Booking]] = relationship("Booking", back_populates="listing")


PACKAGES_KWH = [20, 40, 60, 80]


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
    pin_code: Mapped[str] = mapped_column(String(6), nullable=False)
    scheduled_at: Mapped[Optional[datetime]] = mapped_column(DateTime)
    completed_at: Mapped[Optional[datetime]] = mapped_column(DateTime)
    notes: Mapped[Optional[str]] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())

    listing: Mapped[Listing] = relationship("Listing", back_populates="bookings")
    buyer: Mapped[User] = relationship("User", back_populates="bookings")
