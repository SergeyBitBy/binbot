import logging
from collections.abc import AsyncGenerator

from sqlalchemy import event, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.config.settings import settings
from app.db.models import AdminUser, AllowedChat, Base, MonitoringProfile, SystemSetting

logger = logging.getLogger(__name__)

# Configure engine based on SQLite or PostgreSQL
is_sqlite = settings.database_url.startswith("sqlite")

engine = create_async_engine(
    settings.database_url,
    echo=False,
    future=True,
    connect_args={"check_same_thread": False} if is_sqlite else {},
)

if is_sqlite:
    @event.listens_for(engine.sync_engine, "connect")
    def set_sqlite_pragma(dbapi_connection, connection_record):
        cursor = dbapi_connection.cursor()
        cursor.execute("PRAGMA journal_mode=WAL")
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.execute("PRAGMA synchronous=NORMAL")
        cursor.close()

AsyncSessionLocal = async_sessionmaker(
    engine, class_=AsyncSession, expire_on_commit=False, autoflush=False
)

async def get_db() -> AsyncGenerator[AsyncSession, None]:
    async with AsyncSessionLocal() as session:
        try:
            yield session
        finally:
            await session.close()

async def init_db():
    """Create all tables and seed initial defaults idempotently."""
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    
    async with AsyncSessionLocal() as session:
        # Seed Initial Admin Username
        if settings.initial_admin_username:
            admin_stmt = select(AdminUser).where(AdminUser.username == settings.initial_admin_username.lower().lstrip("@"))
            res = await session.execute(admin_stmt)
            if not res.scalar_one_or_none():
                new_admin = AdminUser(
                    username=settings.initial_admin_username.lower().lstrip("@"),
                    role="superadmin",
                )
                session.add(new_admin)
                logger.info(f"Seeded initial admin username: @{settings.initial_admin_username}")

        # Seed Initial Allowed Chat ID
        if settings.initial_allowed_chat_id:
            chat_stmt = select(AllowedChat).where(AllowedChat.chat_id == settings.initial_allowed_chat_id)
            res = await session.execute(chat_stmt)
            if not res.scalar_one_or_none():
                new_chat = AllowedChat(
                    chat_id=settings.initial_allowed_chat_id,
                    title="Default Admin Chat",
                )
                session.add(new_chat)
                logger.info(f"Seeded initial allowed chat ID: {settings.initial_allowed_chat_id}")

        # Seed Default Monitoring Profile if empty
        profile_stmt = select(MonitoringProfile).where(MonitoringProfile.name == "Default USDT/UAH")
        res = await session.execute(profile_stmt)
        if not res.scalar_one_or_none():
            default_profile = MonitoringProfile(
                name="Default USDT/UAH",
                asset="USDT",
                fiat="UAH",
                trade_type="BUY",
                pay_types=[],
                merchant_check=False,
                scan_interval_seconds=60,
                is_active=True,
            )
            session.add(default_profile)
            logger.info("Seeded default Monitoring Profile: Default USDT/UAH")

        # Seed System Settings
        settings_to_seed = {
            "timezone": settings.timezone,
            "quiet_hours_enabled": "false",
            "quiet_hours_start": "23:00",
            "quiet_hours_end": "07:00",
            "contact_parsing_enabled": "true",
        }
        for key, val in settings_to_seed.items():
            st_stmt = select(SystemSetting).where(SystemSetting.key == key)
            st_res = await session.execute(st_stmt)
            if not st_res.scalar_one_or_none():
                session.add(SystemSetting(key=key, value=val))

        await session.commit()
