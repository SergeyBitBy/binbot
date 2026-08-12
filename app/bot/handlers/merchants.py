import logging
from aiogram import Router, F
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message, InlineKeyboardButton, InlineKeyboardMarkup

from app.bot.keyboards.main_kb import get_back_menu_keyboard
from app.db.database import AsyncSessionLocal
from app.db.repositories.merchant_repo import MerchantRepository
from app.bot.states.merchant_states import MerchantSearchForm
from app.providers.binance.client import BinanceClient
from app.services.contact_extractor import ContactExtractor

logger = logging.getLogger(__name__)
router = Router()

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

    # Split long text cleanly by line breaks
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
    await cb_merchants_page(call, page=1, only_verified=False)

@router.callback_query(F.data.startswith("merch_page_"))
async def cb_merchants_page(call: CallbackQuery, page: int = None, only_verified: bool = False):
    if page is None:
        parts = call.data.split("_")
        page = int(parts[2])
        only_verified = (parts[3] == "1") if len(parts) > 3 else False

    limit = 5
    offset = (page - 1) * limit

    async with AsyncSessionLocal() as session:
        repo = MerchantRepository(session)
        merchants, total_count = await repo.get_all_merchants(limit=limit, offset=offset, only_verified=only_verified)

    total_pages = max(1, (total_count + limit - 1) // limit)
    filter_label = "🛡️ Только Проверенные" if only_verified else "🌐 Все Мерчанты"
    text = f"🔍 <b>БАЗА НАЙДЕННЫХ МЕРЧАНТОВ P2P</b> ({filter_label} | Всего: <code>{total_count}</code>)\n\n"

    buttons = []
    if not merchants:
        text += "<i>В базе данных не найдено сохраненных мерчантов по данному фильтру.</i>"
    else:
        for m in merchants:
            c_count = len(m.contacts) if m.contacts else 0
            nick = m.nickname or "Без ника"
            badge = "🛡️" if m.user_type and "merchant" in m.user_type.lower() else "👤"
            text += f"{badge} <b>{nick}</b> (<code>{m.user_no}</code>) | Контактов: <code>{c_count}</code>\n"
            v_flag = "1" if only_verified else "0"
            buttons.append([InlineKeyboardButton(text=f"🎴 Карточка: {nick}", callback_data=f"merch_card_{m.id}_{page}_{v_flag}")])

    # Filter toggle row
    v_flag_next = "0" if only_verified else "1"
    v_text_toggle = "🌐 Показать Всех" if only_verified else "🛡️ Показать Только Проверенных"
    buttons.append([InlineKeyboardButton(text=v_text_toggle, callback_data=f"merch_page_1_{v_flag_next}")])

    # Pagination row
    nav_row = []
    v_flag_curr = "1" if only_verified else "0"
    if page > 1:
        nav_row.append(InlineKeyboardButton(text="◀️ Назад", callback_data=f"merch_page_{page - 1}_{v_flag_curr}"))
    nav_row.append(InlineKeyboardButton(text=f"📄 {page}/{total_pages}", callback_data="ignore"))
    if page < total_pages:
        nav_row.append(InlineKeyboardButton(text="Вперед ▶️", callback_data=f"merch_page_{page + 1}_{v_flag_curr}"))
    buttons.append(nav_row)

    buttons.append([
        InlineKeyboardButton(text="🔎 Поиск Мерчанта", callback_data="merch_search_start"),
        InlineKeyboardButton(text="🗑 Очистить Всю Базу", callback_data="merch_clear_all_confirm"),
    ])
    buttons.append([InlineKeyboardButton(text="⬅️ Главное Меню", callback_data="menu_main")])

    await send_split_message(call, text, reply_markup=InlineKeyboardMarkup(inline_keyboard=buttons))

@router.callback_query(F.data.startswith("merch_card_"))
async def cb_merchant_card(call: CallbackQuery):
    parts = call.data.split("_")
    m_id = int(parts[2])
    current_page = int(parts[3]) if len(parts) > 3 else 1
    v_flag = parts[4] if len(parts) > 4 else "0"

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

    badge = "🛡️ Проверенный Мерчант" if m.user_type and "merchant" in m.user_type.lower() else "👤 Обычный пользователь"

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
            InlineKeyboardButton(text="⚡ Обновить Мерчанта", callback_data=f"merch_refresh_{m.id}_{current_page}_{v_flag}"),
            InlineKeyboardButton(text="🗑 Удалить Мерчанта", callback_data=f"merch_delete_{m.id}_{current_page}_{v_flag}"),
        ],
        [InlineKeyboardButton(text="🔗 Профиль на Binance P2P", url=profile_url)],
        [InlineKeyboardButton(text="⬅️ Назад к Списку", callback_data=f"merch_page_{current_page}_{v_flag}")],
    ]

    await send_split_message(call, text, reply_markup=InlineKeyboardMarkup(inline_keyboard=buttons))

@router.callback_query(F.data.startswith("merch_refresh_"))
async def cb_merchant_refresh(call: CallbackQuery):
    parts = call.data.split("_")
    m_id = int(parts[2])

    async with AsyncSessionLocal() as session:
        repo = MerchantRepository(session)
        m = await repo.get_by_id(m_id)
        if not m:
            return

        client = BinanceClient()
        try:
            res = await client.search_ads({"publisherType": "merchant", "userNo": m.user_no, "page": 1, "rows": 10})
            items = res.get("data") or []
            if items:
                adv_no = items[0].get("adv", {}).get("advNo")
                detail_data = await client.get_adv_detail(adv_no)
                if detail_data and "adv" in detail_data:
                    adv_d = detail_data["adv"]
                    m.remarks = adv_d.get("remarks") or m.remarks
                    m.auto_reply_msg = adv_d.get("autoReplyMsg") or m.auto_reply_msg
                    await session.commit()
                    await safe_answer(call, "Данные и описание мерчанта обновлены!", show_alert=True)
                else:
                    await safe_answer(call, "Обновлено из поиска", show_alert=True)
            else:
                await safe_answer(call, "Активные объявления не найдены", show_alert=True)
        except Exception as e:
            await safe_answer(call, f"Ошибка обновления: {e}", show_alert=True)
        finally:
            await client.close()

    await cb_merchant_card(call)

@router.callback_query(F.data.startswith("merch_delete_"))
async def cb_merchant_delete(call: CallbackQuery):
    parts = call.data.split("_")
    m_id = int(parts[2])
    page = int(parts[3]) if len(parts) > 3 else 1
    v_flag = parts[4] if len(parts) > 4 else "0"

    async with AsyncSessionLocal() as session:
        repo = MerchantRepository(session)
        await repo.delete_merchant(m_id)

    await safe_answer(call, "Мерчант удален из базы!", show_alert=True)
    await cb_merchants_page(call, page=page, only_verified=(v_flag == "1"))

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
        buttons.append([InlineKeyboardButton(text=f"🎴 Карточка {m.nickname}", callback_data=f"merch_card_{m.id}_1_0")])

    buttons.append([InlineKeyboardButton(text="⬅️ Главное Меню", callback_data="menu_main")])
    await send_split_message(message, text, reply_markup=InlineKeyboardMarkup(inline_keyboard=buttons))
