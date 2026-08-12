import logging

from aiogram import F, Router
from aiogram.filters import Command
from aiogram.types import CallbackQuery, Message

from app.bot.keyboards.main_kb import get_main_menu_keyboard
from app.config.settings import LOGS_DIR

logger = logging.getLogger(__name__)
router = Router()

@router.callback_query(F.data == "menu_logs")
@router.message(Command("logs"))
async def cmd_view_logs(event: Message | CallbackQuery):
    log_file = LOGS_DIR / "bot.log"
    if not log_file.exists():
        text = "📝 <b>Лог-файл еще не создан.</b>"
        if isinstance(event, CallbackQuery):
            await event.message.edit_text(text, reply_markup=get_main_menu_keyboard(), parse_mode="HTML")
            await event.answer()
        else:
            await event.answer(text, reply_markup=get_main_menu_keyboard(), parse_mode="HTML")
        return

    try:
        with open(log_file, "r", encoding="utf-8", errors="ignore") as f:
            lines = f.readlines()
            tail_lines = "".join(lines[-40:])

        text = f"📝 <b>ПОСЛЕДНИЕ 40 СТРОК ЛОГА СИСТЕМЫ:</b>\n\n<pre>{tail_lines[-3500:]}</pre>"
        
        if isinstance(event, CallbackQuery):
            await event.message.edit_text(text, reply_markup=get_main_menu_keyboard(), parse_mode="HTML")
            await event.answer()
        else:
            await event.answer(text, reply_markup=get_main_menu_keyboard(), parse_mode="HTML")
    except Exception as e:
        logger.error(f"Error reading log file: {e}")
