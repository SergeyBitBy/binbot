import logging
from aiogram import Router, F
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup
from sqlalchemy import select

from app.bot.keyboards.main_kb import get_back_menu_keyboard
from app.db.database import AsyncSessionLocal
from app.db.models import MonitoringProfile, ScanHistory

logger = logging.getLogger(__name__)
router = Router()

@router.callback_query(F.data == "menu_scan_history")
async def cb_scan_history(call: CallbackQuery):
    try:
        await call.answer()
    except Exception:
        pass

    async with AsyncSessionLocal() as session:
        res = await session.execute(
            select(ScanHistory)
            .order_by(ScanHistory.id.desc())
            .limit(10)
        )
        scans = res.scalars().all()

        profiles_res = await session.execute(select(MonitoringProfile))
        profiles_map = {p.id: p.name for p in profiles_res.scalars().all()}

    text = "📜 <b>ИСТОРИЯ ПОСЛЕДНИХ 10 СКАННРОВАНИЙ</b>\n\n"
    if not scans:
        text += "<i>История сканирований пока пуста.</i>"
    else:
        for s in scans:
            p_name = profiles_map.get(s.profile_id, f"Профиль #{s.profile_id}")
            time_str = s.started_at.strftime("%H:%M:%S (%d.%m)") if s.started_at else "N/A"
            status_icon = "🟢" if s.status == "SUCCESS" else "🔴"
            text += (
                f"{status_icon} <b>{p_name}</b> [{time_str}]\n"
                f"   Объявлений: <code>{s.total_ads_found}</code> | Мерчантов: <code>{s.unique_merchants_found}</code> | "
                f"Новых: <code>{s.new_merchants_count}</code>\n"
            )
            if s.error_message:
                text += f"   ⚠️ <i>Ошибка: {s.error_message[:100]}</i>\n"
            text += "\n"

    buttons = [
        [InlineKeyboardButton(text="⚡ Запустить Сканирование Сейчас", callback_data="menu_scan_now")],
        [InlineKeyboardButton(text="⬅️ Главное Меню", callback_data="menu_main")],
    ]

    await call.message.edit_text(text, reply_markup=InlineKeyboardMarkup(inline_keyboard=buttons), parse_mode="HTML")
