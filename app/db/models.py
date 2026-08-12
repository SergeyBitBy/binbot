from datetime import datetime, timezone
from decimal import Decimal

from sqlalchemy import (
    JSON,
    BigInteger,
    Boolean,
    DateTime,
    Float,
    ForeignKey,
    Integer,
    Numeric,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


def utc_now() -> datetime:
    return datetime.now(timezone.utc)

class Base(DeclarativeBase):
    pass

class Merchant(Base):
    __tablename__ = "merchants"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_no: Mapped[str] = mapped_column(String(64), unique=True, index=True, nullable=False)
    nickname: Mapped[str | None] = mapped_column(String(128), index=True)
    user_type: Mapped[str | None] = mapped_column(String(32))
    month_order_count: Mapped[int] = mapped_column(Integer, default=0)
    month_finish_rate: Mapped[float] = mapped_column(Float, default=0.0)
    positive_rate: Mapped[float] = mapped_column(Float, default=0.0)
    remarks: Mapped[str | None] = mapped_column(Text)
    auto_reply_msg: Mapped[str | None] = mapped_column(Text)
    
    first_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)
    last_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now, onupdate=utc_now)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)

    contacts: Mapped[list["Contact"]] = relationship("Contact", back_populates="merchant", cascade="all, delete-orphan")
    advertisements: Mapped[list["Advertisement"]] = relationship("Advertisement", back_populates="merchant", cascade="all, delete-orphan")

class Contact(Base):
    __tablename__ = "contacts"
    __table_args__ = (
        UniqueConstraint("merchant_id", "type", "value", name="uq_merchant_contact"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    merchant_id: Mapped[int] = mapped_column(Integer, ForeignKey("merchants.id", ondelete="CASCADE"), nullable=False)
    type: Mapped[str] = mapped_column(String(32), index=True, nullable=False)  # telegram, whatsapp, phone, email, etc.
    value: Mapped[str] = mapped_column(String(256), index=True, nullable=False)
    raw_match: Mapped[str | None] = mapped_column(String(256))
    first_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)

    merchant: Mapped["Merchant"] = relationship("Merchant", back_populates="contacts")

class Advertisement(Base):
    __tablename__ = "advertisements"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    adv_no: Mapped[str] = mapped_column(String(64), unique=True, index=True, nullable=False)
    merchant_id: Mapped[int] = mapped_column(Integer, ForeignKey("merchants.id", ondelete="CASCADE"), nullable=False)
    
    asset: Mapped[str] = mapped_column(String(16), index=True, nullable=False)
    fiat: Mapped[str] = mapped_column(String(16), index=True, nullable=False)
    trade_type: Mapped[str] = mapped_column(String(16), index=True, nullable=False)  # BUY or SELL
    price: Mapped[Decimal] = mapped_column(Numeric(18, 8), nullable=False)
    min_amount: Mapped[Decimal | None] = mapped_column(Numeric(18, 8))
    max_amount: Mapped[Decimal | None] = mapped_column(Numeric(18, 8))
    pay_methods: Mapped[dict | None] = mapped_column(JSON)
    remarks: Mapped[str | None] = mapped_column(Text)
    auto_reply: Mapped[str | None] = mapped_column(Text)

    first_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)
    last_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now, onupdate=utc_now)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)

    merchant: Mapped["Merchant"] = relationship("Merchant", back_populates="advertisements")

class MonitoringProfile(Base):
    __tablename__ = "monitoring_profiles"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column(String(64), unique=True, nullable=False)
    asset: Mapped[str] = mapped_column(String(16), default="USDT")
    fiat: Mapped[str] = mapped_column(String(16), default="UAH")
    trade_type: Mapped[str] = mapped_column(String(16), default="BUY")
    pay_types: Mapped[list | None] = mapped_column(JSON, default=list)
    trans_amount: Mapped[str | None] = mapped_column(String(32))
    merchant_check: Mapped[bool] = mapped_column(Boolean, default=False)
    scan_interval_seconds: Mapped[int] = mapped_column(Integer, default=60)
    
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    is_locked: Mapped[bool] = mapped_column(Boolean, default=False)  # True when scan in progress
    is_baseline_completed: Mapped[bool] = mapped_column(Boolean, default=False)
    
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now, onupdate=utc_now)

    scan_history: Mapped[list["ScanHistory"]] = relationship("ScanHistory", back_populates="profile", cascade="all, delete-orphan")

class ScanHistory(Base):
    __tablename__ = "scan_history"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    profile_id: Mapped[int] = mapped_column(Integer, ForeignKey("monitoring_profiles.id", ondelete="CASCADE"), nullable=False)
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    total_ads_found: Mapped[int] = mapped_column(Integer, default=0)
    unique_merchants_found: Mapped[int] = mapped_column(Integer, default=0)
    new_merchants_count: Mapped[int] = mapped_column(Integer, default=0)
    new_contacts_count: Mapped[int] = mapped_column(Integer, default=0)
    status: Mapped[str] = mapped_column(String(32), default="RUNNING")
    error_message: Mapped[str | None] = mapped_column(Text)

    profile: Mapped["MonitoringProfile"] = relationship("MonitoringProfile", back_populates="scan_history")

class SystemSetting(Base):
    __tablename__ = "system_settings"

    key: Mapped[str] = mapped_column(String(64), primary_key=True)
    value: Mapped[str] = mapped_column(Text, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now, onupdate=utc_now)

class AdminUser(Base):
    __tablename__ = "admin_users"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    telegram_id: Mapped[int | None] = mapped_column(BigInteger, unique=True, index=True)
    username: Mapped[str | None] = mapped_column(String(64), unique=True, index=True)
    role: Mapped[str] = mapped_column(String(32), default="admin")
    added_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)

class AllowedChat(Base):
    __tablename__ = "allowed_chats"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    chat_id: Mapped[int] = mapped_column(BigInteger, unique=True, index=True, nullable=False)
    title: Mapped[str | None] = mapped_column(String(128))
    added_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)

class AuditLog(Base):
    __tablename__ = "audit_logs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[int | None] = mapped_column(BigInteger)
    username: Mapped[str | None] = mapped_column(String(64))
    action: Mapped[str] = mapped_column(String(64), nullable=False)
    details: Mapped[str | None] = mapped_column(Text)
    timestamp: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)
