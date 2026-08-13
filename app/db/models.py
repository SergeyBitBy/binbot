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
    detail_checked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

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
    last_scan_started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_scan_finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    next_scan_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), index=True)
    locked_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), index=True)
    
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
    trigger: Mapped[str] = mapped_column(String(16), default="scheduled")
    expected_ads: Mapped[int | None] = mapped_column(Integer)
    pages_fetched: Mapped[int] = mapped_column(Integer, default=0)
    detail_success_count: Mapped[int] = mapped_column(Integer, default=0)
    detail_failure_count: Mapped[int] = mapped_column(Integer, default=0)
    duration_ms: Mapped[int | None] = mapped_column(Integer)

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


class ProfileMerchant(Base):
    __tablename__ = "profile_merchants"
    __table_args__ = (UniqueConstraint("profile_id", "merchant_id", name="uq_profile_merchant"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    profile_id: Mapped[int] = mapped_column(Integer, ForeignKey("monitoring_profiles.id", ondelete="CASCADE"), index=True)
    merchant_id: Mapped[int] = mapped_column(Integer, ForeignKey("merchants.id", ondelete="CASCADE"), index=True)
    first_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)
    last_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)


class ProfileAdvertisement(Base):
    __tablename__ = "profile_advertisements"
    __table_args__ = (UniqueConstraint("profile_id", "advertisement_id", name="uq_profile_advertisement"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    profile_id: Mapped[int] = mapped_column(Integer, ForeignKey("monitoring_profiles.id", ondelete="CASCADE"), index=True)
    advertisement_id: Mapped[int] = mapped_column(Integer, ForeignKey("advertisements.id", ondelete="CASCADE"), index=True)
    first_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)
    last_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)


class NotificationOutbox(Base):
    __tablename__ = "notification_outbox"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    event_type: Mapped[str] = mapped_column(String(32), index=True)
    profile_id: Mapped[int | None] = mapped_column(Integer, ForeignKey("monitoring_profiles.id", ondelete="SET NULL"))
    merchant_id: Mapped[int | None] = mapped_column(Integer, ForeignKey("merchants.id", ondelete="SET NULL"))
    payload: Mapped[dict] = mapped_column(JSON, nullable=False)
    deduplication_key: Mapped[str] = mapped_column(String(255), unique=True, index=True)
    status: Mapped[str] = mapped_column(String(16), default="PENDING", index=True)
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    next_attempt_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now, index=True)
    last_error: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)
    sent_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class NotificationDelivery(Base):
    __tablename__ = "notification_deliveries"
    __table_args__ = (UniqueConstraint("outbox_id", "chat_id", name="uq_outbox_chat"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    outbox_id: Mapped[int] = mapped_column(Integer, ForeignKey("notification_outbox.id", ondelete="CASCADE"), index=True)
    chat_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    status: Mapped[str] = mapped_column(String(16), default="PENDING", index=True)
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    next_attempt_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now, index=True)
    last_error: Mapped[str | None] = mapped_column(Text)
    sent_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
