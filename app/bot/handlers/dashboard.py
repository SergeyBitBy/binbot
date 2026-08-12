import logging

from aiogram import F, Router
from aiogram.types import CallbackQuery

from app.bot.keyboards.main_kb import get_main_menu_keyboard
from app.services.health_service import HealthService

logger = logging.getLogger(__name__)
router = Router()

@router.callback_query(F.data == "menu_dashboard")
async def cb_dashboard(call: CallbackQuery):
    metrics = await HealthService.get_dashboard_metrics()
    text = (
        "📊 <b>ДАШБОРД МОНИТОРИНГА BINANCE P2P</b>\n\n"
        f"👥 <b>Сохранено Мерчантов:</b> <code>{metrics['total_merchants']}</code>\n"
        f"📞 <b>Извлечено Контактов:</b> <code>{metrics['total_contacts']}</code>\n"
        f"📢 <b>Снапшотов Объявлений:</b> <code>{metrics['total_ads']}</code>\n"
        f"⚙️ <b>Активных Профилей:</b> <code>{metrics['active_profiles']}</code>\n"
        f"🔄 <b>Выполнено Сканирований:</b> <code>{metrics['total_scans']}</code>\n"
        f"⏱ <b>Последний скан:</b> {metrics['last_scan_time']} [{metrics['last_scan_status']}]\n"
    )
    await call.message.edit_text(text, reply_markup=get_main_menu_keyboard(), parse_mode="HTML")
    await call.answer()
