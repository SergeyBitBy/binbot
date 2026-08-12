import logging
from sqlalchemy import event, select, update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.config.settings import settings
from app.db.models import AdminUser, AllowedChat, Base, MonitoringProfile, SystemSetting

logger = logging.getLogger(__name__)

engine = create_async_engine(
    settings.database_url,
    echo=False,
    future=True,
    connect_args={"timeout": 30.0},
)

@event.listens_for(engine.sync_engine, "connect")
def set_sqlite_pragma(dbapi_connection, connection_record):
    cursor = dbapi_connection.cursor()
    cursor.execute("PRAGMA journal_mode=WAL")
    cursor.execute("PRAGMA synchronous=NORMAL")
    cursor.execute("PRAGMA busy_timeout=30000")
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
        # 1. Reset all stale profile locks from previous runs/crashes
        await session.execute(update(MonitoringProfile).values(is_locked=False))
        logger.info("Reset all profile locks to unlocked state.")

        # 2. Seed Initial Admin if provided
        if settings.initial_admin_username:
            username_clean = settings.initial_admin_username.lower().lstrip("@")
            res = await session.execute(
                select(AdminUser).where(AdminUser.username == username_clean)
            )
            if not res.scalar_one_or_none():
                admin = AdminUser(username=username_clean, role="superadmin")
                session.add(admin)
                logger.info(f"Seeded initial admin username: @{username_clean}")

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
