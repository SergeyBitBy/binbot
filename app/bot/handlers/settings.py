import logging
from aiogram import Router, F
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import CallbackQuery, Message, InlineKeyboardButton, InlineKeyboardMarkup
from sqlalchemy import select

from app.bot.keyboards.main_kb import get_back_menu_keyboard, get_main_menu_keyboard
from app.db.database import AsyncSessionLocal
from app.db.models import SystemSetting
from app.services.sheets_service import GoogleSheetsService

logger = logging.getLogger(__name__)
router = Router()

class QuietHoursForm(StatesGroup):
    hours_range = State()

class GoogleSheetsForm(StatesGroup):
    sheet_id = State()

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
    sheet_id = await get_setting("google_spreadsheet_id", "Не задан")
    sheets_auto = await get_setting("google_sheets_auto_export", "false")

    mon_status = "🟢 Включен" if global_mon.lower() == "true" else "🔴 Выключен"
    quiet_status = f"🟢 Включено ({q_start} - {q_end})" if quiet_hours.lower() == "true" else "🔴 Выключено"
    contact_status = "🟢 Включен" if contact_ext.lower() == "true" else "🔴 Выключен"
    sheets_auto_status = "🟢 Автоматический" if sheets_auto.lower() == "true" else "🔴 Ручной"

    text = (
        "⚙️ <b>ГЛОБАЛЬНЫЕ НАСТРОЙКИ СИСТЕМЫ</b>\n\n"
        f"⏯ <b>Автоматический Мониторинг:</b> {mon_status}\n"
        f"🌙 <b>Тихое Время (Quiet Hours):</b> {quiet_status}\n"
        f"🔍 <b>Извлечение Контактов:</b> {contact_status}\n\n"
        f"📊 <b>Google Таблица ID:</b> <code>{sheet_id[:25]}...</code>\n"
        f"🔄 <b>Режим Экспорта Таблицы:</b> {sheets_auto_status}\n"
    )

    buttons = [
        [
            InlineKeyboardButton(text="⏯ Вкл/Выкл Анализ", callback_data="toggle_global_monitoring"),
            InlineKeyboardButton(text="🌙 Вкл/Выкл Тихое Время", callback_data="toggle_quiet_hours"),
        ],
        [
            InlineKeyboardButton(text="⏰ Интервал Тихого Времени", callback_data="set_quiet_hours_time"),
            InlineKeyboardButton(text="🔍 Вкл/Выкл Контакты", callback_data="toggle_contact_extraction"),
        ],
        [
            InlineKeyboardButton(text="📊 Ссылка/ID Google Таблицы", callback_data="set_google_sheet_id"),
            InlineKeyboardButton(text="🔄 Авто/Ручной Экспорт", callback_data="toggle_sheets_auto"),
        ],
        [
            InlineKeyboardButton(text="📥 Запустить Экспорт в Google Таблицу", callback_data="run_google_sheets_export"),
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

@router.callback_query(F.data == "toggle_sheets_auto")
async def cb_toggle_sheets_auto(call: CallbackQuery):
    current = await get_setting("google_sheets_auto_export", "false")
    new_val = "false" if current.lower() == "true" else "true"
    await set_setting("google_sheets_auto_export", new_val)
    await cb_settings_menu(call)

@router.callback_query(F.data == "set_google_sheet_id")
async def cb_set_google_sheet(call: CallbackQuery, state: FSMContext):
    try:
        await call.answer()
    except Exception:
        pass
    await state.set_state(GoogleSheetsForm.sheet_id)
    text = (
        "📊 <b>НАСТРОЙКА GOOGLE ТАБЛИЦЫ</b>\n\n"
        "Отправьте ссылку на Google Таблицу или её ID (например: `1BxiMVs0XRra5nFMdKbB...`):"
    )
    await call.message.edit_text(text, reply_markup=get_back_menu_keyboard(), parse_mode="HTML")

@router.message(GoogleSheetsForm.sheet_id)
async def process_google_sheet_input(message: Message, state: FSMContext):
    raw_input = message.text.strip()
    await state.clear()

    # Extract ID if full URL provided
    sheet_id = raw_input
    if "docs.google.com/spreadsheets/d/" in raw_input:
        sheet_id = raw_input.split("/d/")[1].split("/")[0]

    await set_setting("google_spreadsheet_id", sheet_id)
    await message.answer(f"✅ <b>Google Spreadsheet ID сохранен: `{sheet_id}`</b>", reply_markup=get_back_menu_keyboard(), parse_mode="HTML")

@router.callback_query(F.data == "run_google_sheets_export")
async def cb_run_sheets_export(call: CallbackQuery):
    sheet_id = await get_setting("google_spreadsheet_id")
    if not sheet_id:
        try:
            await call.answer("⚠️ Google Spreadsheet ID не настроен!", show_alert=True)
        except Exception:
            pass
        return

    try:
        await call.answer("📊 Выполняется экспорт данных в Google Таблицу...", show_alert=True)
    except Exception:
        pass

    sheets_service = GoogleSheetsService()
    await sheets_service.initialize()
    # Trigger export
