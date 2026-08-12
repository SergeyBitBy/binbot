import logging
from aiogram import Router, F
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import CallbackQuery, Message, InlineKeyboardButton, InlineKeyboardMarkup
from sqlalchemy import select

from app.bot.keyboards.main_kb import get_back_menu_keyboard, get_main_menu_keyboard
from app.db.database import AsyncSessionLocal
from app.db.models import SystemSetting

logger = logging.getLogger(__name__)
router = Router()

class QuietHoursForm(StatesGroup):
    hours_range = State()

async def get_setting(key: str, default: str = "") -> str:
    async with AsyncSessionLocal() as session:
        res = await session.execute(select(SystemSetting).where(SystemSetting.key == key))
        s = res.scalar_one_or_none()
        return s.value if s else default

async def set_setting(key: str, value: str):
    async with AsyncSessionLocal() as session:
        res = await session.execute(select(SystemSetting).where(SystemSetting.key == key))
        s = res.scalar_one_or_none()
        if s:
            s.value = value
        else:
            session.add(SystemSetting(key=key, value=value))
        await session.commit()

@router.callback_query(F.data == "toggle_global_monitoring")
async def cb_toggle_monitoring(call: CallbackQuery):
    current = await get_setting("global_monitoring_enabled", "true")
    new_val = "false" if current.lower() == "true" else "true"
    await set_setting("global_monitoring_enabled", new_val)

    status_msg = "🟢 Анализ ЗАПУЩЕН!" if new_val == "true" else "⏸ Анализ ПРИОСТАНОВЛЕН!"
    try:
        await call.answer(status_msg, show_alert=True)
    except Exception:
        pass

    text = "🤖 <b>Главное Меню Администратора</b>"
    await call.message.edit_text(text, reply_markup=get_main_menu_keyboard(monitoring_enabled=(new_val == "true")), parse_mode="HTML")

@router.callback_query(F.data == "menu_settings")
async def cb_settings_menu(call: CallbackQuery):
    try:
        await call.answer()
    except Exception:
        pass

    global_mon = await get_setting("global_monitoring_enabled", "true")
    quiet_hours = await get_setting("quiet_hours_enabled", "false")
    q_start = await get_setting("quiet_hours_start", "23:00")
    q_end = await get_setting("quiet_hours_end", "07:00")
    contact_ext = await get_setting("contact_extraction_enabled", "true")

    mon_status = "🟢 Включен (Идет сканирование)" if global_mon.lower() == "true" else "🔴 Выключен (Пауза)"
    quiet_status = f"🟢 Включено ({q_start} - {q_end})" if quiet_hours.lower() == "true" else "🔴 Выключено"
    contact_status = "🟢 Включен" if contact_ext.lower() == "true" else "🔴 Выключен"

    text = (
        "⚙️ <b>ГЛОБАЛЬНЫЕ НАСТРОЙКИ СИСТЕМЫ</b>\n\n"
        f"⏯ <b>Автоматический Мониторинг:</b> {mon_status}\n"
        f"🌙 <b>Тихое Время (Quiet Hours):</b> {quiet_status}\n"
        f"🔍 <b>Извлечение Контактов:</b> {contact_status}\n"
    )

    buttons = [
        [
            InlineKeyboardButton(text="⏯ Вкл/Выкл Анализ", callback_data="toggle_global_monitoring"),
            InlineKeyboardButton(text="🌙 Вкл/Выкл Тихое Время", callback_data="toggle_quiet_hours"),
        ],
        [
            InlineKeyboardButton(text="⏰ Изменить Интервал Тихого Времени", callback_data="set_quiet_hours_time"),
        ],
        [
            InlineKeyboardButton(text="🔍 Вкл/Выкл Парсинг Контактов", callback_data="toggle_contact_extraction"),
        ],
        [
            InlineKeyboardButton(text="⬅️ Главное Меню", callback_data="menu_main"),
        ],
    ]

    await call.message.edit_text(text, reply_markup=InlineKeyboardMarkup(inline_keyboard=buttons), parse_mode="HTML")

@router.callback_query(F.data == "toggle_quiet_hours")
async def cb_toggle_quiet(call: CallbackQuery):
    current = await get_setting("quiet_hours_enabled", "false")
    new_val = "false" if current.lower() == "true" else "true"
    await set_setting("quiet_hours_enabled", new_val)
    await cb_settings_menu(call)

@router.callback_query(F.data == "toggle_contact_extraction")
async def cb_toggle_contacts(call: CallbackQuery):
    current = await get_setting("contact_extraction_enabled", "true")
    new_val = "false" if current.lower() == "true" else "true"
    await set_setting("contact_extraction_enabled", new_val)
    await cb_settings_menu(call)

@router.callback_query(F.data == "set_quiet_hours_time")
async def cb_set_quiet_time(call: CallbackQuery, state: FSMContext):
    try:
        await call.answer()
    except Exception:
        pass
    await state.set_state(QuietHoursForm.hours_range)
    text = (
        "⏰ <b>НАСТРОЙКА ИНТЕРВАЛА ТИХОГО ВРЕМЕНИ</b>\n\n"
        "Введите время начала и окончания в формате `ЧЧ:ММ-ЧЧ:ММ` (например: <code>23:00-07:00</code>):"
    )
    await call.message.edit_text(text, reply_markup=get_back_menu_keyboard(), parse_mode="HTML")

@router.message(QuietHoursForm.hours_range)
async def process_quiet_hours_input(message: Message, state: FSMContext):
    text = message.text.strip()
    await state.clear()

    if "-" in text:
        parts = text.split("-")
        if len(parts) == 2:
            st, end = parts[0].strip(), parts[1].strip()
            await set_setting("quiet_hours_start", st)
            await set_setting("quiet_hours_end", end)
            await message.answer(f"✅ <b>Интервал тихого времени установлен: `{st}` — `{end}`</b>", reply_markup=get_back_menu_keyboard(), parse_mode="HTML")
            return

    await message.answer("⚠️ Неверный формат. Используйте формат `23:00-07:00`.", reply_markup=get_back_menu_keyboard())
