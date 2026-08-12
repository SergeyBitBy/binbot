import asyncio
import logging
import sys
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from aiogram import Bot, Dispatcher

from app.config.logging import setup_logging
from app.config.settings import settings
from app.db.database import init_db
from app.bot.middlewares.auth_middleware import AuthMiddleware
from app.bot.handlers import (
    admins, backup, chats, dashboard, logs, merchants, profiles, scan_history, settings as bot_settings, start
)
from app.services.monitoring_service import MonitoringService
from app.services.notification_service import NotificationService
from app.services.sheets_service import GoogleSheetsService

logger = logging.getLogger(__name__)

async def main():
    setup_logging()
    logger.info("==========================================")
    logger.info("Starting Binance P2P Monitor Bot...")

    # 1. Initialize Database & Seed Defaults
    await init_db()
    logger.info("Database initialized successfully.")

    # 2. Initialize Services
    sheets_service = GoogleSheetsService()
    await sheets_service.initialize()

    notification_service = NotificationService()
    monitoring_service = MonitoringService(
        notification_service=notification_service,
        sheets_service=sheets_service,
    )

    # 3. Setup Telegram Bot & Dispatcher
    bot = Bot(token=settings.bot_token)
    notification_service.set_bot(bot)

    bot_info = await bot.get_me()
    logger.info(f"Connected to Telegram as @{bot_info.username} (ID: {bot_info.id})")

    # Send startup notification to initial chat if configured
    if settings.initial_allowed_chat_id:
        try:
            await bot.send_message(
                chat_id=settings.initial_allowed_chat_id,
                text="🚀 <b>Binance P2P Monitor Bot v1.0 успешно запущен на сервере!</b>\n\nОтправьте /start для открытия панели управления.",
                parse_mode="HTML",
            )
            logger.info(f"Sent startup message to chat_id={settings.initial_allowed_chat_id}")
        except Exception as se:
            logger.warning(f"Could not send startup message to chat_id={settings.initial_allowed_chat_id}: {se}")

    dp = Dispatcher()
    dp.message.outer_middleware(AuthMiddleware())
    dp.callback_query.outer_middleware(AuthMiddleware())

    # Include Routers
    dp.include_router(start.router)
    dp.include_router(dashboard.router)
    dp.include_router(profiles.router)
    dp.include_router(merchants.router)
    dp.include_router(admins.router)
    dp.include_router(chats.router)
    dp.include_router(bot_settings.router)
    dp.include_router(scan_history.router)
    dp.include_router(logs.router)
    dp.include_router(backup.router)

    # 4. Setup Scheduler for Periodic Monitoring & Daily Summary
    scheduler = AsyncIOScheduler()
    scheduler.add_job(
        monitoring_service.scan_all_active_profiles,
        "interval",
        seconds=60,
        id="p2p_monitoring_job",
        replace_existing=True,
    )
    scheduler.start()
    logger.info("APScheduler started successfully. Periodic monitoring enabled (60s interval).")

    # 5. Execute initial background scan after startup
    asyncio.create_task(monitoring_service.scan_all_active_profiles())

    try:
        logger.info(f"Bot @{bot_info.username} is now POLLING for incoming messages...")
        await dp.start_polling(bot)
    finally:
        scheduler.shutdown(wait=False)
        await monitoring_service.provider.close()
        await bot.session.close()
        logger.info("Bot stopped gracefully.")

if __name__ == "__main__":
    if sys.platform == "win32":
        asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())
    try:
        asyncio.run(main())
    except (KeyboardInterrupt, SystemExit):
        logger.info("Application exited.")
