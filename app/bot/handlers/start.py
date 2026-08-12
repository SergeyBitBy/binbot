import logging
from aiogram import Router, F
from aiogram.filters import Command, CommandStart
from aiogram.types import CallbackQuery, Message

from app.bot.keyboards.main_kb import get_main_menu_keyboard
from app.services.health_service import HealthService

logger = logging.getLogger(__name__)
router = Router()

@router.message(CommandStart())
async def cmd_start(message: Message):
    user = message.from_user
    logger.info(f"Received /start command from user_id={user.id if user else 'None'}, username={user.username if user else 'None'}")
    text = (
        "🤖 <b>Binance P2P Monitor Bot v1.0</b>\n\n"
        "Добро пожаловать в административную панель автоматического мониторинга Binance P2P.\n\n"
        "Используйте меню ниже для управления профилями сканирования, просмотра найденой базы мерчантов и логов."
    )
    await message.answer(text, reply_markup=get_main_menu_keyboard(), parse_mode="HTML")

@router.callback_query(F.data == "menu_main")
async def cb_main_menu(call: CallbackQuery):
    text = "🤖 <b>Главное Меню Администратора</b>"
    await call.message.edit_text(text, reply_markup=get_main_menu_keyboard(), parse_mode="HTML")
    await call.answer()

@router.callback_query(F.data == "menu_status")
@router.message(Command("status"))
async def cmd_status(event: Message | CallbackQuery):
    metrics = await HealthService.get_dashboard_metrics()
    text = (
        "ℹ️ <b>СТАТУС И ЗДОРОВЬЕ СИСТЕМЫ</b>\n\n"
        f"🟢 <b>Статус бота:</b> Активен & Работает\n"
        f"🐍 <b>Python:</b> <code>{metrics['python_version']}</code>\n"
        f"⏱ <b>Последний скан:</b> {metrics['last_scan_time']} ({metrics['last_scan_status']})\n"
        f"👥 <b>Всего мерчантов:</b> <code>{metrics['total_merchants']}</code>\n"
        f"📞 <b>Извлечено контактов:</b> <code>{metrics['total_contacts']}</code>\n"
        f"⚙️ <b>Активных профилей:</b> <code>{metrics['active_profiles']}</code>\n"
    )
    if isinstance(event, CallbackQuery):
        await event.message.edit_text(text, reply_markup=get_main_menu_keyboard(), parse_mode="HTML")
        await event.answer()
    else:
        await event.answer(text, reply_markup=get_main_menu_keyboard(), parse_mode="HTML")
