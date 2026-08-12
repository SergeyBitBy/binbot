import logging
from aiogram import Router, F
from aiogram.filters import Command, CommandStart
from aiogram.types import CallbackQuery, Message

from app.bot.keyboards.main_kb import get_main_menu_keyboard
from app.services.health_service import HealthService

logger = logging.getLogger(__name__)
router = Router()

@router.message(CommandStart())
async def cmd_start(message: Message, is_authorized: bool = True):
    user = message.from_user
    chat = message.chat
    logger.info(f"Processing /start for user_id={user.id if user else 'None'}, username={user.username if user else 'None'}, is_authorized={is_authorized}")
    
    if not is_authorized:
        deny_text = (
            f"⛔ <b>Доступ ограничен.</b> Ваш аккаунт не авторизован для управления бота.\n\n"
            f"🆔 <b>Ваш Telegram User ID:</b> <code>{user.id if user else 'N/A'}</code>\n"
            f"💬 <b>Ваш Chat ID:</b> <code>{chat.id if chat else 'N/A'}</code>\n"
            f"👤 <b>Ваш Username:</b> <code>@{user.username if user and user.username else 'Не задан'}</code>\n\n"
            f"ℹ️ Передайте ваш User ID или Chat ID суперадминистратору для добавления в список разрешенных."
        )
        try:
            await message.answer(deny_text, parse_mode="HTML")
            logger.info("Access denied message sent successfully.")
        except Exception as e:
            logger.error(f"Error sending access denied message: {e}")
        return

    text = (
        "🤖 <b>Binance P2P Monitor Bot v1.0</b>\n\n"
        "Добро пожаловать в административную панель автоматического мониторинга Binance P2P.\n\n"
        "Используйте меню ниже для управления профилями сканирования, просмотра найденной базы мерчантов и логов."
    )
    try:
        await message.answer(text, reply_markup=get_main_menu_keyboard(), parse_mode="HTML")
        logger.info(f"Successfully sent /start main menu to user_id={user.id if user else 'None'}")
    except Exception as e:
        logger.error(f"Failed to send /start main menu message: {e}")

@router.callback_query(F.data == "menu_main")
async def cb_main_menu(call: CallbackQuery):
    try:
        await call.answer()
    except Exception:
        pass
    text = "🤖 <b>Главное Меню Администратора</b>"
    try:
        await call.message.edit_text(text, reply_markup=get_main_menu_keyboard(), parse_mode="HTML")
    except Exception as e:
        logger.error(f"Failed to edit main menu: {e}")

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
        try:
            await event.answer()
        except Exception:
            pass
        try:
            await event.message.edit_text(text, reply_markup=get_main_menu_keyboard(), parse_mode="HTML")
        except Exception as e:
            logger.error(f"Failed to edit status message: {e}")
    else:
        try:
            await event.answer(text, reply_markup=get_main_menu_keyboard(), parse_mode="HTML")
        except Exception as e:
            logger.error(f"Failed to send status message: {e}")
