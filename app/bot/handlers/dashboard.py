import logging
from datetime import datetime, timezone

from aiogram import F, Router
from aiogram.filters import Command
from aiogram.types import CallbackQuery, Message
from sqlalchemy import update

from app.bot.keyboards.main_kb import get_main_menu_keyboard
from app.db.database import AsyncSessionLocal
from app.db.models import NotificationDelivery, NotificationOutbox
from app.services.health_service import HealthService

logger = logging.getLogger(__name__)
router = Router()


@router.message(Command("retry_notifications"))
async def retry_notifications(message: Message):
    now = datetime.now(timezone.utc)
    async with AsyncSessionLocal() as session:
        deliveries = await session.execute(
            update(NotificationDelivery)
            .where(NotificationDelivery.status == "DEAD")
            .values(status="RETRY", attempts=0, next_attempt_at=now, last_error=None)
        )
        await session.execute(
            update(NotificationOutbox)
            .where(NotificationOutbox.status == "DEAD")
            .values(status="RETRY", attempts=0, next_attempt_at=now, last_error=None)
        )
        await session.commit()
    await message.answer(f"Повторно поставлено в очередь: {deliveries.rowcount}")

@router.callback_query(F.data == "menu_dashboard")
async def cb_dashboard(call: CallbackQuery, role: str = "viewer"):
    try:
        await call.answer()
    except Exception:
        pass
        
    metrics = await HealthService.get_dashboard_metrics()
    text = (
        "📊 <b>ДАШБОРД МОНИТОРИНГА BINANCE P2P</b>\n\n"
        f"👥 <b>Сохранено Мерчантов:</b> <code>{metrics['total_merchants']}</code>\n"
        f"📞 <b>Извлечено Контактов:</b> <code>{metrics['total_contacts']}</code>\n"
        f"📢 <b>Снапшотов Объявлений:</b> <code>{metrics['total_ads']}</code>\n"
        f"⚙️ <b>Активных Профилей:</b> <code>{metrics['active_profiles']}</code>\n"
        f"🔄 <b>Выполнено Сканирований:</b> <code>{metrics['total_scans']}</code>\n"
        f"⏱ <b>Последний скан:</b> {metrics['last_scan_time']} [{metrics['last_scan_status']}]\n"
        f"⚠️ <b>Не доставлено уведомлений:</b> <code>{metrics['failed_deliveries']}</code>\n"
    )
    await call.message.edit_text(text, reply_markup=get_main_menu_keyboard(role=role), parse_mode="HTML")
