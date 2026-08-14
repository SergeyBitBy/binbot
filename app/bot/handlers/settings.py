import asyncio
import json
import logging
from pathlib import Path
from aiogram import Router, F
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import CallbackQuery, Message, InlineKeyboardButton, InlineKeyboardMarkup
from sqlalchemy import select

from app.bot.keyboards.main_kb import get_back_menu_keyboard, get_main_menu_keyboard
from app.config.settings import settings
from app.db.database import AsyncSessionLocal
from app.db.models import SystemSetting
from app.db.repositories.merchant_repo import MerchantRepository
from app.db.repositories.profile_repo import ProfileRepository
from app.services.sheets_service import GoogleSheetsService, DEFAULT_COLUMNS_CONFIG
from app.services.groq_extractor import GroqContactExtractor

logger = logging.getLogger(__name__)
router = Router()

class QuietHoursForm(StatesGroup):
    hours_range = State()

class GoogleSheetsForm(StatesGroup):
    sheet_id = State()
    service_account_file = State()

class ColumnRenameForm(StatesGroup):
    col_key = State()
    new_title = State()

class GroqApiKeyForm(StatesGroup):
    api_key = State()

async def get_setting(key: str, default: str = "") -> str:
    async with AsyncSessionLocal() as session:
        res = await session.execute(select(SystemSetting).where(SystemSetting.key == key))
        s = res.scalar_one_or_none()
        return s.value if s and s.value else default

async def set_setting(key: str, value: str):
    """Set system setting with retry logic against database locks."""
    for attempt in range(5):
        try:
            async with AsyncSessionLocal() as session:
                res = await session.execute(select(SystemSetting).where(SystemSetting.key == key))
                s = res.scalar_one_or_none()
                if s:
                    s.value = value
                else:
                    session.add(SystemSetting(key=key, value=value))
                await session.commit()
                return
        except Exception as e:
            logger.debug(f"set_setting retry attempt {attempt+1}/5: {e}")
            await asyncio.sleep(0.5 * (attempt + 1))

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
    groq_ai_on = (await get_setting("groq_ai_enabled", "true")).lower() == "true"

    mon_is_on = global_mon.lower() == "true"
    quiet_is_on = quiet_hours.lower() == "true"

    mon_status = "🟢 Включен" if mon_is_on else "🔴 Выключен"
    quiet_status = f"🟢 Включено ({q_start} - {q_end})" if quiet_is_on else "🔴 Выключено"
    groq_status = "🟢 Groq AI (Llama 3.3)" if groq_ai_on else "🔴 Выключен (Резервный Regex)"

    text = (
        "⚙️ <b>ГЛОБАЛЬНЫЕ НАСТРОЙКИ СИСТЕМЫ</b>\n\n"
        f"⏯ <b>Автоматический Мониторинг:</b> {mon_status}\n"
        f"🌙 <b>Тихое Время (Quiet Hours):</b> {quiet_status}\n"
        f"🤖 <b>ИИ Экстрактор Контактов:</b> {groq_status}\n"
    )

    btn_mon = "⏯ Анализ: 🟢 ВКЛ" if mon_is_on else "⏯ Анализ: 🔴 ВЫКЛ"
    btn_quiet = "🌙 Тихое Время: 🟢 ВКЛ" if quiet_is_on else "🌙 Тихое Время: 🔴 ВЫКЛ"

    buttons = [
        [
            InlineKeyboardButton(text=btn_mon, callback_data="toggle_global_monitoring"),
            InlineKeyboardButton(text=btn_quiet, callback_data="toggle_quiet_hours"),
        ],
        [
            InlineKeyboardButton(text="⏰ Интервал Тихого Времени", callback_data="set_quiet_hours_time"),
            InlineKeyboardButton(text="🤖 Groq AI Экстрактор", callback_data="menu_groq_settings"),
        ],
        [
            InlineKeyboardButton(text="📊 Настройки Google Sheets", callback_data="menu_google_sheets"),
        ],
        [
            InlineKeyboardButton(text="⬅️ Главное Меню", callback_data="menu_main"),
        ],
    ]

    await call.message.edit_text(text, reply_markup=InlineKeyboardMarkup(inline_keyboard=buttons), parse_mode="HTML")

# --- GROQ AI SETTINGS SUBMENU ---

@router.callback_query(F.data == "menu_groq_settings")
async def cb_groq_settings_menu(call: CallbackQuery):
    try:
        await call.answer()
    except Exception:
        pass

    groq_enabled = (await get_setting("groq_ai_enabled", "true")).lower() == "true"
    api_key = await get_setting("groq_api_key", settings.groq_api_key)
    model = await get_setting("groq_model", settings.groq_model)

    status_str = "🟢 Включен (Нейросеть Groq LPU)" if groq_enabled else "🔴 Выключен (Резервный Regex)"
    masked_key = f"{api_key[:8]}...{api_key[-4:]}" if len(api_key) > 12 else (api_key or "Не задан")

    text = (
        "🤖 <b>НАСТРОЙКИ ИИ ЭКСТРАКТОРА КОНТАКТОВ (GROQ)</b>\n\n"
        "Нейросетевое извлечение контактов на базе моделей Llama 3 с пониманием любых языков, сленга и контекста без ложных срабатываний.\n\n"
        f"📊 <b>Статус ИИ:</b> {status_str}\n"
        f"🧠 <b>Модель ИИ:</b> <code>{model}</code>\n"
        f"🔑 <b>API Ключ:</b> <code>{masked_key}</code>\n"
    )

    btn_toggle = "🤖 ИИ: 🟢 ВКЛ" if groq_enabled else "🤖 ИИ: 🔴 ВЫКЛ (Regex)"
    btn_model_switch = "🧠 Модель: 70B Versatile" if "70b" in model.lower() else "🧠 Модель: 8B Instant"

    buttons = [
        [
            InlineKeyboardButton(text=btn_toggle, callback_data="toggle_groq_enabled"),
            InlineKeyboardButton(text=btn_model_switch, callback_data="toggle_groq_model"),
        ],
        [
            InlineKeyboardButton(text="🔑 Изменить Groq API Key", callback_data="set_groq_api_key"),
            InlineKeyboardButton(text="🧪 Проверить Связь с Groq", callback_data="test_groq_connection"),
        ],
        [
            InlineKeyboardButton(text="⬅️ Назад в Настройки", callback_data="menu_settings"),
        ],
    ]

    await call.message.edit_text(text, reply_markup=InlineKeyboardMarkup(inline_keyboard=buttons), parse_mode="HTML")

@router.callback_query(F.data == "toggle_groq_enabled")
async def cb_toggle_groq(call: CallbackQuery):
    current = await get_setting("groq_ai_enabled", "true")
    new_val = "false" if current.lower() == "true" else "true"
    await set_setting("groq_ai_enabled", new_val)

    status_msg = "🟢 ИИ Экстрактор Groq ВКЛЮЧЕН!" if new_val == "true" else "🔴 ИИ Экстрактор ВЫКЛЮЧЕН (работает Regex)!"
    try:
        await call.answer(status_msg, show_alert=True)
    except Exception:
        pass

    await cb_groq_settings_menu(call)

@router.callback_query(F.data == "toggle_groq_model")
async def cb_toggle_groq_model(call: CallbackQuery):
    current = await get_setting("groq_model", settings.groq_model)
    new_model = "llama-3.1-8b-instant" if "70b" in current.lower() else "llama-3.3-70b-versatile"
    await set_setting("groq_model", new_model)

    try:
        await call.answer(f"Модель изменена на: {new_model}", show_alert=True)
    except Exception:
        pass

    await cb_groq_settings_menu(call)

@router.callback_query(F.data == "set_groq_api_key")
async def cb_set_groq_key_start(call: CallbackQuery, state: FSMContext):
    try:
        await call.answer()
    except Exception:
        pass

    await state.set_state(GroqApiKeyForm.api_key)
    text = (
        "🔑 <b>ВВОД КЛЮЧА GROQ API KEY</b>\n\n"
        "Отправьте ваш API ключ от Groq (например: `gsk_...`):\n\n"
        "<i>Получить бесплатный ключ можно на сайте: <a href='https://console.groq.com/keys'>console.groq.com/keys</a></i>"
    )
    await call.message.edit_text(text, reply_markup=get_back_menu_keyboard(), parse_mode="HTML", disable_web_page_preview=True)

@router.message(GroqApiKeyForm.api_key)
async def process_groq_key_input(message: Message, state: FSMContext):
    key = message.text.strip()
    await state.clear()

    await set_setting("groq_api_key", key)
    
    # Test key immediately
    extractor = GroqContactExtractor()
    success, msg, latency = await extractor.test_connection(key)

    if success:
        await message.answer(
            f"✅ <b>Groq API Ключ успешно сохранен и проверен!</b>\n\n⚡ Время отклика: <code>{latency}мс</code>",
            reply_markup=get_back_menu_keyboard(),
            parse_mode="HTML"
        )
    else:
        await message.answer(
            f"⚠️ <b>Ключ сохранен, но тест подключения завершился с ошибкой:</b>\n{msg}",
            reply_markup=get_back_menu_keyboard(),
            parse_mode="HTML"
        )

@router.callback_query(F.data == "test_groq_connection")
async def cb_test_groq_connection(call: CallbackQuery):
    api_key = await get_setting("groq_api_key", settings.groq_api_key)
    model = await get_setting("groq_model", settings.groq_model)

    if not api_key:
        try:
            await call.answer("⚠️ API-ключ Groq не задан!", show_alert=True)
        except Exception:
            pass
        return

    try:
        await call.answer("⏳ Тестирую связь с серверами Groq LPU...", show_alert=False)
    except Exception:
        pass

    extractor = GroqContactExtractor()
    success, msg, latency = await extractor.test_connection(api_key, model)

    alert_text = f"{msg}\n⚡ Пинг: {latency} мс\n🧠 Модель: {model}"
    try:
        await call.answer(alert_text, show_alert=True)
    except Exception:
        pass

# --- END GROQ AI SETTINGS ---

@router.callback_query(F.data == "menu_google_sheets")
async def cb_google_sheets_menu(call: CallbackQuery):
    try:
        await call.answer()
    except Exception:
        pass

    sheet_id = await get_setting("google_spreadsheet_id", "Не задан")
    sheets_auto = await get_setting("google_sheets_auto_export", "false")
    auto_contacts = await get_setting("google_sheets_auto_contacts_only", "false")

    json_path = Path("service_account.json")
    if json_path.exists():
        try:
            js_data = json.loads(json_path.read_text(encoding="utf-8"))
            client_email = js_data.get("client_email", "Файл загружен")
            json_status = f"🟢 Загружен (<code>{client_email}</code>)"
        except Exception:
            json_status = "🟢 Файл найден"
    else:
        json_status = "🔴 Файл отсутствует"

    sheets_auto_is_on = sheets_auto.lower() == "true"
    auto_contacts_is_on = auto_contacts.lower() == "true"

    sheets_auto_status = "🟢 Включен (Авто)" if sheets_auto_is_on else "🔴 Выключен (Ручной)"
    filter_status = "📞 Только с контактами" if auto_contacts_is_on else "🌐 Все найденные мерчанты"

    text = (
        "📊 <b>НАСТРОЙКИ ИНТЕГРАЦИИ GOOGLE SHEETS</b>\n\n"
        f"📊 <b>Google Таблица ID:</b> <code>{sheet_id[:25]}...</code>\n"
        f"📄 <b>Файл Ключа (service_account.json):</b> {json_status}\n"
        f"🔄 <b>Авто-Экспорт:</b> {sheets_auto_status}\n"
        f"🎯 <b>Фильтр Авто-Экспорта:</b> {filter_status}\n\n"
        "<i>💡 Данные каждого профиля автоматически сохраняются на отдельный лист с названием профиля.</i>"
    )

    btn_auto = "🔄 Авто-Экспорт: 🟢 ВКЛ" if sheets_auto_is_on else "🔄 Авто-Экспорт: 🔴 ВЫКЛ"
    btn_filter = "🎯 Фильтр Авто: 📞 С контактами" if auto_contacts_is_on else "🎯 Фильтр Авто: 🌐 Все"

    buttons = [
        [
            InlineKeyboardButton(text="📊 Ссылка/ID Google Таблицы", callback_data="set_google_sheet_id"),
            InlineKeyboardButton(text="📄 Загрузить service_account.json", callback_data="upload_service_account"),
        ],
        [
            InlineKeyboardButton(text=btn_auto, callback_data="toggle_sheets_auto"),
            InlineKeyboardButton(text=btn_filter, callback_data="toggle_sheets_auto_contacts_only"),
        ],
        [
            InlineKeyboardButton(text="📐 Настройка Колонок Таблицы", callback_data="menu_sheets_columns"),
            InlineKeyboardButton(text="📥 Запустить Ручной Экспорт", callback_data="run_google_sheets_export_prompt"),
        ],
        [
            InlineKeyboardButton(text="⬅️ Назад в Настройки", callback_data="menu_settings"),
        ],
    ]

    await call.message.edit_text(text, reply_markup=InlineKeyboardMarkup(inline_keyboard=buttons), parse_mode="HTML")

# --- COLUMN MANAGEMENT SUBMENU ---

@router.callback_query(F.data == "menu_sheets_columns")
async def cb_sheets_columns_menu(call: CallbackQuery):
    try:
        await call.answer()
    except Exception:
        pass

    sheets_service = GoogleSheetsService()
    config = await sheets_service.get_columns_config()

    text = "📐 <b>НАСТРОЙКА КОЛОНОК GOOGLE ТАБЛИЦЫ</b>\n\nПорядок и отображение столбцов:\n\n"
    buttons = []

    for idx, c in enumerate(config, 1):
        status_icon = "🟢" if c.get("enabled", True) else "🔴"
        title = c.get("title", c["key"])
        text += f"<b>{idx}.</b> {status_icon} <b>{title}</b> (<code>{c['key']}</code>)\n"
        
        btn_text = f"{idx}. {status_icon} {title}"
        buttons.append([InlineKeyboardButton(text=btn_text, callback_data=f"col_card_{c['key']}")])

    buttons.append([InlineKeyboardButton(text="🔄 Сбросить Порядок и Названия", callback_data="col_reset_default")])
    buttons.append([InlineKeyboardButton(text="⬅️ Назад в Google Sheets", callback_data="menu_google_sheets")])

    await call.message.edit_text(text, reply_markup=InlineKeyboardMarkup(inline_keyboard=buttons), parse_mode="HTML")

@router.callback_query(F.data.startswith("col_card_"))
async def cb_col_card(call: CallbackQuery):
    try:
        await call.answer()
    except Exception:
        pass

    key = call.data.replace("col_card_", "")
    sheets_service = GoogleSheetsService()
    config = await sheets_service.get_columns_config()

    col = None
    idx = -1
    for i, c in enumerate(config):
        if c["key"] == key:
            col = c
            idx = i
            break

    if not col:
        await call.answer("Колонка не найдена", show_alert=True)
        return

    is_enabled = col.get("enabled", True)
    status_str = "🟢 Включена" if is_enabled else "🔴 Выключена (Скрыта)"
    title = col.get("title", key)
    total_cols = len(config)

    text = (
        f"⚙️ <b>НАСТРОЙКА КОЛОНКИ: {title}</b>\n\n"
        f"🔑 <b>Ключ:</b> <code>{key}</code>\n"
        f"📝 <b>Заголовок:</b> {title}\n"
        f"📊 <b>Статус:</b> {status_str}\n"
        f"🔢 <b>Порядок (Позиция):</b> {idx + 1} из {total_cols}\n"
    )

    btn_toggle = "🔴 Выключить (Скрыть)" if is_enabled else "🟢 Включить (Показать)"

    buttons = [
        [InlineKeyboardButton(text=btn_toggle, callback_data=f"col_toggle_{key}")],
        [InlineKeyboardButton(text="✏️ Переименовать Заголовок", callback_data=f"col_rename_{key}")],
    ]

    move_row = []
    if idx > 0:
        move_row.append(InlineKeyboardButton(text="⬆️ Влево (Выше)", callback_data=f"col_move_up_{key}"))
    if idx < total_cols - 1:
        move_row.append(InlineKeyboardButton(text="⬇️ Вправо (Ниже)", callback_data=f"col_move_down_{key}"))
    if move_row:
        buttons.append(move_row)

    buttons.append([InlineKeyboardButton(text="⬅️ Назад к Колонкам", callback_data="menu_sheets_columns")])

    await call.message.edit_text(text, reply_markup=InlineKeyboardMarkup(inline_keyboard=buttons), parse_mode="HTML")

@router.callback_query(F.data.startswith("col_toggle_"))
async def cb_col_toggle(call: CallbackQuery):
    key = call.data.replace("col_toggle_", "")
    sheets_service = GoogleSheetsService()
    config = await sheets_service.get_columns_config()

    for c in config:
        if c["key"] == key:
            c["enabled"] = not c.get("enabled", True)
            break

    await sheets_service.save_columns_config(config)
    try:
        await call.answer("Статус колонки изменен!", show_alert=True)
    except Exception:
        pass
    await cb_col_card(call)

@router.callback_query(F.data.startswith("col_move_up_"))
async def cb_col_move_up(call: CallbackQuery):
    key = call.data.replace("col_move_up_", "")
    sheets_service = GoogleSheetsService()
    config = await sheets_service.get_columns_config()

    for i in range(len(config)):
        if config[i]["key"] == key and i > 0:
            config[i], config[i-1] = config[i-1], config[i]
            break

    await sheets_service.save_columns_config(config)
    try:
        await call.answer("Позиция сдвинута влево!", show_alert=True)
    except Exception:
        pass
    await cb_col_card(call)

@router.callback_query(F.data.startswith("col_move_down_"))
async def cb_col_move_down(call: CallbackQuery):
    key = call.data.replace("col_move_down_", "")
    sheets_service = GoogleSheetsService()
    config = await sheets_service.get_columns_config()

    for i in range(len(config)):
        if config[i]["key"] == key and i < len(config) - 1:
            config[i], config[i+1] = config[i+1], config[i]
            break

    await sheets_service.save_columns_config(config)
    try:
        await call.answer("Позиция сдвинута вправо!", show_alert=True)
    except Exception:
        pass
    await cb_col_card(call)

@router.callback_query(F.data.startswith("col_rename_"))
async def cb_col_rename_start(call: CallbackQuery, state: FSMContext):
    key = call.data.replace("col_rename_", "")
    try:
        await call.answer()
    except Exception:
        pass

    await state.set_state(ColumnRenameForm.new_title)
    await state.update_data(col_key=key)

    text = (
        f"✏️ <b>ПЕРЕИМЕНОВАНИЕ ЗАГОЛОВКА КОЛОНКИ (`{key}`)</b>\n\n"
        "Отправьте новое название для этой колонки:"
    )
    await call.message.edit_text(text, reply_markup=get_back_menu_keyboard(), parse_mode="HTML")

@router.message(ColumnRenameForm.new_title)
async def process_col_rename_input(message: Message, state: FSMContext):
    data = await state.get_data()
    key = data.get("col_key")
    new_title = message.text.strip()
    await state.clear()

    sheets_service = GoogleSheetsService()
    config = await sheets_service.get_columns_config()

    for c in config:
        if c["key"] == key:
            c["title"] = new_title
            break

    await sheets_service.save_columns_config(config)
    await message.answer(f"✅ <b>Заголовок колонки `{key}` изменен на: `{new_title}`</b>", reply_markup=get_back_menu_keyboard(), parse_mode="HTML")

@router.callback_query(F.data == "col_reset_default")
async def cb_col_reset(call: CallbackQuery):
    sheets_service = GoogleSheetsService()
    await sheets_service.save_columns_config(DEFAULT_COLUMNS_CONFIG)
    try:
        await call.answer("Конфигурация колонок сброшена на по умолчанию!", show_alert=True)
    except Exception:
        pass
    await cb_sheets_columns_menu(call)

# --- END COLUMN MANAGEMENT ---

@router.callback_query(F.data == "toggle_sheets_auto_contacts_only")
async def cb_toggle_sheets_auto_contacts(call: CallbackQuery):
    current = await get_setting("google_sheets_auto_contacts_only", "false")
    new_val = "false" if current.lower() == "true" else "true"
    await set_setting("google_sheets_auto_contacts_only", new_val)

    status_msg = "📞 Фильтр Авто: ТОЛЬКО С КОНТАКТАМИ" if new_val == "true" else "🌐 Фильтр Авто: ВСЕ МЕРЧАНТЫ"
    try:
        await call.answer(status_msg, show_alert=True)
    except Exception:
        pass

    await cb_google_sheets_menu(call)

@router.callback_query(F.data == "toggle_sheets_auto")
async def cb_toggle_sheets_auto(call: CallbackQuery):
    current = await get_setting("google_sheets_auto_export", "false")
    new_val = "false" if current.lower() == "true" else "true"
    await set_setting("google_sheets_auto_export", new_val)

    status_msg = "🟢 Режим экспорта: АВТОМАТИЧЕСКИЙ" if new_val == "true" else "🔴 Режим экспорта: РУЧНОЙ"
    try:
        await call.answer(status_msg, show_alert=True)
    except Exception:
        pass

    await cb_google_sheets_menu(call)

@router.callback_query(F.data == "upload_service_account")
async def cb_upload_service_account_start(call: CallbackQuery, state: FSMContext):
    try:
        await call.answer()
    except Exception:
        pass
    await state.set_state(GoogleSheetsForm.service_account_file)
    
    text = (
        "📄 <b>ИНСТРУКЦИЯ ПО ПОЛУЧЕНИЮ КЛЮЧА GOOGLE SERVICE ACCOUNT</b>\n\n"
        "1️⃣ Откройте <a href='https://console.cloud.google.com/'>Google Cloud Console</a>.\n"
        "2️⃣ Создайте новый проект или выберите существующий.\n"
        "3️⃣ Перейдите в раздел <b>APIs & Services</b> ➔ <b>Library</b> и включите:\n"
        "   • <b>Google Sheets API</b>\n"
        "   • <b>Google Drive API</b>\n"
        "4️⃣ Перейдите в <b>APIs & Services</b> ➔ <b>Credentials</b> ➔ <b>Create Credentials</b> ➔ <b>Service Account</b>.\n"
        "5️⃣ В созданном сервисном аккаунте откройте вкладку <b>Keys</b> ➔ <b>Add Key</b> ➔ <b>Create new key</b> ➔ Выберите формат <b>JSON</b>.\n"
        "6️⃣ Скачанный `.json` файл просто <b>отправьте документом прямо в этот чат</b>.\n\n"
        "⬇️ <i>Жду отправки файла service_account.json...</i>"
    )
    await call.message.edit_text(text, reply_markup=get_back_menu_keyboard(), parse_mode="HTML", disable_web_page_preview=True)

@router.message(GoogleSheetsForm.service_account_file, F.document)
async def process_service_account_file(message: Message, state: FSMContext):
    doc = message.document
    if not doc.file_name.endswith(".json"):
        await message.answer("⚠️ <b>Пожалуйста, отправьте файл в формате .json!</b>", reply_markup=get_back_menu_keyboard(), parse_mode="HTML")
        return

    await state.clear()
    bot = message.bot
    target_path = Path("service_account.json")

    file_info = await bot.get_file(doc.file_id)
    await bot.download_file(file_info.file_path, destination=target_path)

    client_email = "Неизвестен"
    try:
        data = json.loads(target_path.read_text(encoding="utf-8"))
        client_email = data.get("client_email", client_email)
    except Exception as e:
        logger.error(f"Error parsing uploaded JSON: {e}")

    text = (
        "✅ <b>ФАЙЛ service_account.json УСПЕШНО ЗАГРУЖЕН И СОХРАНЕН!</b>\n\n"
        f"📧 <b>Email Сервисного Аккаунта:</b>\n<code>{client_email}</code>\n\n"
        "⚠️ <b>ВАЖНО:</b> Скопируйте этот Email и добавьте его с правами <b>Редактора</b> в вашей Google Таблице (кнопка <i>Поделиться / Share</i>)."
    )
    await message.answer(text, reply_markup=get_back_menu_keyboard(), parse_mode="HTML")

@router.callback_query(F.data == "toggle_quiet_hours")
async def cb_toggle_quiet(call: CallbackQuery):
    current = await get_setting("quiet_hours_enabled", "false")
    new_val = "false" if current.lower() == "true" else "true"
    await set_setting("quiet_hours_enabled", new_val)
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
        "Отправьте ссылку на Google Таблицу или её ID (например: `1fhTvlPkjwHQG0NIEOefA...`):"
    )
    await call.message.edit_text(text, reply_markup=get_back_menu_keyboard(), parse_mode="HTML")

@router.message(GoogleSheetsForm.sheet_id)
async def process_google_sheet_input(message: Message, state: FSMContext):
    raw_input = message.text.strip()
    await state.clear()

    sheet_id = raw_input
    if "docs.google.com/spreadsheets/d/" in raw_input:
        sheet_id = raw_input.split("/d/")[1].split("/")[0]

    await set_setting("google_spreadsheet_id", sheet_id)
    await message.answer(f"✅ <b>Google Spreadsheet ID сохранен: `{sheet_id}`</b>", reply_markup=get_back_menu_keyboard(), parse_mode="HTML")

@router.callback_query(F.data == "run_google_sheets_export_prompt")
async def cb_run_sheets_prompt(call: CallbackQuery):
    sheet_id = await get_setting("google_spreadsheet_id")
    if not sheet_id or sheet_id == "Не задан":
        try:
            await call.answer("⚠️ Google Spreadsheet ID не настроен!", show_alert=True)
        except Exception:
            pass
        return

    text = (
        "📊 <b>ЭКСПОРТ ДАННЫХ В GOOGLE SHEETS</b>\n\n"
        "Данные каждого профиля будут выгружены на его собственный лист в таблице.\n\n"
        "Выберите режим экспорта:"
    )
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="📊 Экспортировать все профили (по отдельным листам)", callback_data="run_google_sheets_all")],
        [InlineKeyboardButton(text="📞 Экспортировать только с контактами", callback_data="run_google_sheets_contacts")],
        [InlineKeyboardButton(text="⬅️ Назад в Настройки", callback_data="menu_google_sheets")],
    ])
    await call.message.edit_text(text, reply_markup=kb, parse_mode="HTML")

@router.callback_query(F.data.startswith("run_google_sheets_"))
async def cb_run_sheets_export_do(call: CallbackQuery):
    target = call.data.replace("run_google_sheets_", "")
    only_contacts = (target == "contacts")

    try:
        await call.answer("📊 Экспорт профилей в Google Таблицу запущен...", show_alert=True)
    except Exception:
        pass

    sheets_service = GoogleSheetsService()
    await sheets_service.initialize()
    if not sheets_service.is_configured():
        try:
            await call.answer("⚠️ Google Sheets не настроен или отсутствует файл service_account.json", show_alert=True)
        except Exception:
            pass
        return

    total_exported = 0
    async with AsyncSessionLocal() as session:
        p_repo = ProfileRepository(session)
        m_repo = MerchantRepository(session)
        profiles = await p_repo.get_all()
        all_merchants, _ = await m_repo.get_all_merchants(limit=1000, only_with_contacts=only_contacts)

        # 1. Overwrite General Worksheet
        all_pairs = [(m, m.contacts) for m in all_merchants]
        await sheets_service.overwrite_profile_merchants(all_pairs, profile_name="Общий")
        total_exported += len(all_pairs)

        # 2. Overwrite each Profile's Worksheet
        for p in profiles:
            p_merchants = await m_repo.get_merchants_by_profile_id(p.id, only_with_contacts=only_contacts)
            p_pairs = [(m, m.contacts) for m in p_merchants]
            await sheets_service.overwrite_profile_merchants(p_pairs, profile_name=p.name)

    try:
        await call.answer(f"✅ Экспорт завершен! Обновлены листы для {len(profiles)} профилей.", show_alert=True)
    except Exception:
        pass
    await cb_google_sheets_menu(call)
