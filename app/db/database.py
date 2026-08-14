import asyncio
import logging

from sqlalchemy import event, func, select, update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.config.settings import settings
from app.db.models import (
    AdminUser,
    AllowedChat,
    Base,
    MonitoringProfile,
    ScanHistory,
    SystemSetting,
)

logger = logging.getLogger(__name__)
database_write_lock = asyncio.Lock()

engine = create_async_engine(
    settings.database_url,
    echo=False,
    future=True,
    connect_args={"timeout": 60.0, "check_same_thread": False},
)

@event.listens_for(engine.sync_engine, "connect")
def set_sqlite_pragma(dbapi_connection, connection_record):
    cursor = dbapi_connection.cursor()
    cursor.execute("PRAGMA journal_mode=WAL")
    cursor.execute("PRAGMA synchronous=NORMAL")
    cursor.execute("PRAGMA busy_timeout=60000")
    cursor.execute("PRAGMA foreign_keys=ON")
    cursor.close()

AsyncSessionLocal = async_sessionmaker(
    bind=engine,
    class_=AsyncSession,
    expire_on_commit=False,
    autoflush=False,
)

async def init_db():
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    async with AsyncSessionLocal() as session:
        # Leases expire on their own; never clear locks belonging to another live process.
        await session.execute(
            update(ScanHistory)
            .where(ScanHistory.status == "RUNNING")
            .values(status="ABORTED", finished_at=func.now(), error_message="Application stopped before scan completion")
        )
        logger.info("Reset all profile locks to unlocked state.")

        # 2. Seed Initial Admin if provided
        if settings.initial_admin_username:
            username_clean = settings.initial_admin_username.lower().lstrip("@")
            res = await session.execute(
                select(AdminUser).where(AdminUser.username == username_clean)
            )
            admin = res.scalar_one_or_none()
            bootstrap_user_id = settings.initial_allowed_chat_id if settings.initial_allowed_chat_id > 0 else None
            if not admin:
                admin = AdminUser(
                    username=username_clean,
                    telegram_id=bootstrap_user_id,
                    role="superadmin",
                )
                session.add(admin)
                logger.info(f"Seeded initial admin username: @{username_clean}")
            elif admin.telegram_id is None and bootstrap_user_id:
                admin.telegram_id = bootstrap_user_id

        # 3. Seed Initial Allowed Chat if provided
        if settings.initial_allowed_chat_id:
            res = await session.execute(
                select(AllowedChat).where(AllowedChat.chat_id == settings.initial_allowed_chat_id)
            )
            if not res.scalar_one_or_none():
                chat = AllowedChat(chat_id=settings.initial_allowed_chat_id, title="Initial Admin Chat")
                session.add(chat)
                logger.info(f"Seeded initial allowed chat ID: {settings.initial_allowed_chat_id}")

        # 4. Seed Default Monitoring Profile if none exist
        res = await session.execute(select(MonitoringProfile))
        if not res.scalars().all():
            default_profile = MonitoringProfile(
                name="Default USDT/UAH",
                asset="USDT",
                fiat="UAH",
                trade_type="BUY",
                pay_types=["Monobank", "PrivatBank"],
                scan_interval_seconds=60,
                merchant_check=False,
                is_active=True,
                is_locked=False,
            )
            session.add(default_profile)
            logger.info("Seeded default Monitoring Profile: Default USDT/UAH")

        # 5. Seed Default System Settings if missing
        default_settings = {
            "global_monitoring_enabled": "true",
            "quiet_hours_enabled": "false",
            "quiet_hours_start": "23:00",
            "quiet_hours_end": "07:00",
            "contact_extraction_enabled": "true",
        }
        for key, val in default_settings.items():
            res_s = await session.execute(select(SystemSetting).where(SystemSetting.key == key))
            if not res_s.scalar_one_or_none():
                session.add(SystemSetting(key=key, value=val))

        await session.commit()
