import logging
from aiogram import Router, F
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import CallbackQuery, Message, InlineKeyboardButton, InlineKeyboardMarkup

from app.bot.keyboards.main_kb import get_back_menu_keyboard
from app.db.database import AsyncSessionLocal
from app.db.repositories.merchant_repo import MerchantRepository
from app.bot.states.merchant_states import MerchantSearchForm
from app.providers.binance.client import BinanceClient
from app.services.sheets_service import GoogleSheetsService

logger = logging.getLogger(__name__)
router = Router()

class MerchantEditForm(StatesGroup):
    merchant_id = State()
    contact_type = State()
    contact_value = State()

async def safe_answer(call: CallbackQuery, text: str = None, show_alert: bool = False):
    try:
        await call.answer(text=text, show_alert=show_alert)
    except Exception:
        pass

async def send_split_message(event: Message | CallbackQuery, text: str, reply_markup: InlineKeyboardMarkup = None):
    """Safely split and send messages longer than Telegram's limit (4000 chars)."""
    MAX_LEN = 3900
    if len(text) <= MAX_LEN:
        if isinstance(event, CallbackQuery):
            await event.message.edit_text(text, reply_markup=reply_markup, parse_mode="HTML", disable_web_page_preview=True)
        else:
            await event.answer(text, reply_markup=reply_markup, parse_mode="HTML", disable_web_page_preview=True)
        return

    lines = text.split("\n")
    chunks = []
    current_chunk = ""

    for line in lines:
        if len(current_chunk) + len(line) + 1 > MAX_LEN:
            chunks.append(current_chunk)
            current_chunk = line + "\n"
        else:
            current_chunk += line + "\n"
    if current_chunk:
        chunks.append(current_chunk)

    for idx, chunk in enumerate(chunks):
        markup = reply_markup if idx == len(chunks) - 1 else None
        if isinstance(event, CallbackQuery) and idx == 0:
            await event.message.edit_text(chunk, reply_markup=markup, parse_mode="HTML", disable_web_page_preview=True)
        else:
            msg = event.message if isinstance(event, CallbackQuery) else event
            await msg.answer(chunk, reply_markup=markup, parse_mode="HTML", disable_web_page_preview=True)

@router.callback_query(F.data == "menu_merchants")
async def cb_merchants_menu(call: CallbackQuery):
    await safe_answer(call)
    await cb_merchants_page(call, page=1, mode="all")

@router.callback_query(F.data.startswith("merch_page_"))
async def cb_merchants_page(call: CallbackQuery, page: int = None, mode: str = "all"):
    if page is None:
        parts = call.data.split("_")
        page = int(parts[2])
        mode = parts[3] if len(parts) > 3 else "all"

    limit = 10
    offset = (page - 1) * limit

    only_verified = (mode == "verified")
    only_with_contacts = (mode == "contacts")

    async with AsyncSessionLocal() as session:
        repo = MerchantRepository(session)
        merchants, total_count = await repo.get_all_merchants(
            limit=limit, offset=offset, only_verified=only_verified, only_with_contacts=only_with_contacts
        )

    total_pages = max(1, (total_count + limit - 1) // limit)
    
    if mode == "verified":
        mode_label = "🛡️ Только проверенные"
    elif mode == "contacts":
        mode_label = "📞 Только с контактами"
    else:
        mode_label = "🌐 Все мерчанты"

    text = f"🔍 <b>БАЗА НАЙДЕННЫХ МЕРЧАНТОВ P2P</b> ({mode_label} | Всего: <code>{total_count}</code>)\n\n"

    buttons = []
    if not merchants:
        text += "<i>В базе данных не найдено мерчантов по данному фильтру.</i>"
    else:
        for m in merchants:
            c_count = len(m.contacts) if m.contacts else 0
            nick = m.nickname or "Без ника"
            badge = "🛡️" if m.user_type and "merchant" in m.user_type.lower() else "👤"
            text += f"{badge} <b>{nick}</b> (<code>{m.user_no}</code>) | Контактов: <code>{c_count}</code>\n"
            buttons.append([InlineKeyboardButton(text=f"🎴 Карточка: {nick}", callback_data=f"merch_card_{m.id}_{page}_{mode}")])

    # 3-Way Filter toggle row
    buttons.append([
        InlineKeyboardButton(text="🌐 Все", callback_data="merch_page_1_all"),
        InlineKeyboardButton(text="🛡️ Только проверенные", callback_data="merch_page_1_verified"),
        InlineKeyboardButton(text="📞 С контактами", callback_data="merch_page_1_contacts"),
    ])

    # Pagination row
    nav_row = []
    if page > 1:
        nav_row.append(InlineKeyboardButton(text="◀️ Назад", callback_data=f"merch_page_{page - 1}_{mode}"))
    nav_row.append(InlineKeyboardButton(text=f"📄 {page}/{total_pages}", callback_data="ignore"))
    if page < total_pages:
        nav_row.append(InlineKeyboardButton(text="Вперед ▶️", callback_data=f"merch_page_{page + 1}_{mode}"))
    buttons.append(nav_row)

    buttons.append([
        InlineKeyboardButton(text="📊 Экспорт в Google Sheets", callback_data="merch_export_sheets_prompt"),
        InlineKeyboardButton(text="🔎 Поиск", callback_data="merch_search_start"),
    ])
    buttons.append([
        InlineKeyboardButton(text="🗑 Очистить Всю Базу", callback_data="merch_clear_all_confirm"),
        InlineKeyboardButton(text="⬅️ Главное Меню", callback_data="menu_main"),
    ])

    await send_split_message(call, text, reply_markup=InlineKeyboardMarkup(inline_keyboard=buttons))

@router.callback_query(F.data == "merch_export_sheets_prompt")
async def cb_merch_export_prompt(call: CallbackQuery):
    await safe_answer(call)
    text = "📊 <b>ЭКСПОРТ ДАННЫХ В GOOGLE SHEETS</b>\n\nВыберите вариант экспорта:"
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="📊 Экспортировать всех (Обновить таблицу)", callback_data="merch_export_sheets_all")],
        [InlineKeyboardButton(text="📞 Экспортировать только с контактами", callback_data="merch_export_sheets_contacts")],
        [InlineKeyboardButton(text="⬅️ Назад в Базу", callback_data="menu_merchants")],
    ])
    await call.message.edit_text(text, reply_markup=kb, parse_mode="HTML")

@router.callback_query(F.data.startswith("merch_export_sheets_"))
async def cb_merch_export_do(call: CallbackQuery):
    target = call.data.replace("merch_export_sheets_", "")
    only_contacts = (target == "contacts")

    await safe_answer(call, "📊 Запущен экспорт в Google Таблицу...", show_alert=True)

    sheets_service = GoogleSheetsService()
    await sheets_service.initialize()
    if not sheets_service.is_configured():
        await safe_answer(call, "⚠️ Google Spreadsheet ID не настроен в Глобальных Настройках!", show_alert=True)
        return

    async with AsyncSessionLocal() as session:
        repo = MerchantRepository(session)
        merchants, _ = await repo.get_all_merchants(limit=1000, only_with_contacts=only_contacts)

    count = 0
    for m in merchants:
        await sheets_service.sync_merchant(m, m.contacts)
        count += 1

    await safe_answer(call, f"✅ Экспорт завершен! Экспортировано {count} мерчантов.", show_alert=True)
    await cb_merchants_page(call, page=1)

@router.callback_query(F.data.startswith("merch_card_"))
async def cb_merchant_card(call: CallbackQuery):
    parts = call.data.split("_")
    m_id = int(parts[2])
    current_page = int(parts[3]) if len(parts) > 3 else 1
    mode = parts[4] if len(parts) > 4 else "all"

    async with AsyncSessionLocal() as session:
        repo = MerchantRepository(session)
        m = await repo.get_by_id(m_id)

    if not m:
        await safe_answer(call, "Мерчант не найден", show_alert=True)
        return

    await safe_answer(call)
    profile_url = f"https://p2p.binance.com/advertiserDetail?advertiserNo={m.user_no}"
    
    contacts_str = ""
    if m.contacts:
        for c in m.contacts:
            icon = "📱" if c.type in ("phone", "viber", "whatsapp") else "💬"
            contacts_str += f"• {icon} <b>{c.type.upper()}:</b> <code>{c.value}</code>\n"
    else:
        contacts_str = "<i>⚠️ Контакты не извлечены</i>\n"

    remarks_text = m.remarks[:500] if m.remarks else "<i>(Не заполнены мерчантом на Binance P2P)</i>"
    badge = "🛡️ Только проверенные" if m.user_type and "merchant" in m.user_type.lower() else "👤 Обычный пользователь"

    text = (
        f"🎴 <b>КАРТОЧКА МЕРЧАНТА P2P</b>\n\n"
        f"👤 <b>Никнейм:</b> <a href='{profile_url}'>{m.nickname or 'Без ника'}</a>\n"
        f"Статус: {badge}\n"
        f"🆔 <b>UserNo:</b> <code>{m.user_no}</code>\n"
        f"📈 <b>Сделок за месяц:</b> <code>{m.month_order_count}</code> ({m.month_finish_rate * 100:.1f}%)\n"
        f"🕒 <b>Впервые замечен:</b> {m.first_seen_at.strftime('%Y-%m-%d %H:%M')}\n\n"
        f"📞 <b>Извлеченные контакты:</b>\n{contacts_str}\n"
        f"📝 <b>Описание / Условия:</b>\n<i>«{remarks_text}»</i>\n"
    )

    buttons = [
        [
            InlineKeyboardButton(text="✏️ Изменить мерчанта (Добавить контакт)", callback_data=f"merch_edit_{m.id}_{current_page}_{mode}"),
            InlineKeyboardButton(text="🗑 Удалить", callback_data=f"merch_delete_{m.id}_{current_page}_{mode}"),
        ],
        [InlineKeyboardButton(text="🔗 Профиль на Binance P2P", url=profile_url)],
        [InlineKeyboardButton(text="⬅️ Назад к Списку", callback_data=f"merch_page_{current_page}_{mode}")],
    ]

    await send_split_message(call, text, reply_markup=InlineKeyboardMarkup(inline_keyboard=buttons))

@router.callback_query(F.data.startswith("merch_edit_"))
async def cb_merchant_edit_start(call: CallbackQuery, state: FSMContext):
    parts = call.data.split("_")
    m_id = int(parts[2])
    await safe_answer(call)
    await state.update_data(edit_m_id=m_id)
    await state.set_state(MerchantEditForm.contact_value)
    text = (
        "✏️ <b>РУЧНОЕ ДОБАВЛЕНИЕ КОНТАКТА МЕРЧАНТА</b>\n\n"
        "Отправьте контакт мерчанта (например: `@telegram_user` или `+380971234567` или любую заметку):"
    )
    await call.message.edit_text(text, reply_markup=get_back_menu_keyboard(), parse_mode="HTML")

@router.message(MerchantEditForm.contact_value)
async def process_manual_contact_input(message: Message, state: FSMContext):
    data = await state.get_data()
    m_id = data.get("edit_m_id")
    c_val = message.text.strip()
    await state.clear()

    c_type = "telegram" if c_val.startswith("@") or "t.me" in c_val else ("phone" if c_val.startswith("+") or c_val.isdigit() else "custom")

    async with AsyncSessionLocal() as session:
        repo = MerchantRepository(session)
        await repo.add_manual_contact(m_id, c_type, c_val)

    await message.answer(f"✅ <b>Контакт `{c_val}` успешно добавлен к мерчанту!</b>", reply_markup=get_back_menu_keyboard(), parse_mode="HTML")

@router.callback_query(F.data.startswith("merch_delete_"))
async def cb_merchant_delete(call: CallbackQuery):
    parts = call.data.split("_")
    m_id = int(parts[2])
    page = int(parts[3]) if len(parts) > 3 else 1
    mode = parts[4] if len(parts) > 4 else "all"

    async with AsyncSessionLocal() as session:
        repo = MerchantRepository(session)
        await repo.delete_merchant(m_id)

    await safe_answer(call, "Мерчант удален из базы!", show_alert=True)
    await cb_merchants_page(call, page=page, mode=mode)

@router.callback_query(F.data == "merch_clear_all_confirm")
async def cb_merchants_clear_confirm(call: CallbackQuery):
    await safe_answer(call)
    text = "⚠️ <b>ВНИМАНИЕ! Вы действительно хотите ПОЛНОСТЬЮ ОЧИСТИТЬ БАЗУ ВСЕХ НАЙДЕННЫХ МЕРЧАНТОВ?</b>\n\nЭто действие нельзя отменить!"
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(text="✅ ДА, ОЧИСТИТЬ ВСЕ", callback_data="merch_clear_all_do"),
            InlineKeyboardButton(text="❌ Отмена", callback_data="menu_merchants"),
        ]
    ])
    await call.message.edit_text(text, reply_markup=kb, parse_mode="HTML")

@router.callback_query(F.data == "merch_clear_all_do")
async def cb_merchants_clear_do(call: CallbackQuery):
    async with AsyncSessionLocal() as session:
        repo = MerchantRepository(session)
        count = await repo.clear_all_merchants()

    await safe_answer(call, f"База очищена ({count} мерчантов удалено)!", show_alert=True)
    await cb_merchants_page(call, page=1)

@router.callback_query(F.data == "merch_search_start")
async def cb_merchant_search_start(call: CallbackQuery, state: FSMContext):
    await safe_answer(call)
    await state.set_state(MerchantSearchForm.query)
    await call.message.edit_text("🔎 <b>ПОИСК МЕРЧАНТА</b>\n\nВведите никнейм или UserNo для поиска:", reply_markup=get_back_menu_keyboard(), parse_mode="HTML")

@router.message(MerchantSearchForm.query)
async def process_merchant_search(message: Message, state: FSMContext):
    query = message.text.strip()
    await state.clear()

    async with AsyncSessionLocal() as session:
        repo = MerchantRepository(session)
        merchants, total = await repo.get_all_merchants(search_query=query, limit=10)

    if not merchants:
        await message.answer(f"🔍 Мерчанты по запросу `{query}` не найдены.", reply_markup=get_back_menu_keyboard(), parse_mode="HTML")
        return

    text = f"🔎 <b>РЕЗУЛЬТАТЫ ПОИСКА ПО ЗАПРОСУ: `{query}`</b>\n\n"
    buttons = []
    for m in merchants:
        text += f"• 👤 <b>{m.nickname or 'Без ника'}</b> (<code>{m.user_no}</code>)\n"
        buttons.append([InlineKeyboardButton(text=f"🎴 Карточка {m.nickname}", callback_data=f"merch_card_{m.id}_1_all")])

    buttons.append([InlineKeyboardButton(text="⬅️ Главное Меню", callback_data="menu_main")])
    await send_split_message(message, text, reply_markup=InlineKeyboardMarkup(inline_keyboard=buttons))
