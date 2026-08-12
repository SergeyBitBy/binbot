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

@router.callback_query(F.data == "menu_merchants")
async def cb_merchants_menu(call: CallbackQuery):
    await safe_answer(call)
    await cb_merchants_page(call, page=1)

@router.callback_query(F.data.startswith("merch_page_"))
async def cb_merchants_page(call: CallbackQuery, page: int = None):
    if page is None:
        page = int(call.data.split("_")[2])

    limit = 5
    offset = (page - 1) * limit

    async with AsyncSessionLocal() as session:
        repo = MerchantRepository(session)
        merchants = await repo.get_all(limit=limit, offset=offset)
        total_count = await repo.get_total_count()

    total_pages = max(1, (total_count + limit - 1) // limit)
    text = f"🔍 <b>БАЗА НАЙДЕННЫХ МЕРЧАНТОВ BINANCE P2P</b> (Всего: <code>{total_count}</code>)\n\n"

    buttons = []
    if not merchants:
        text += "<i>В базе данных пока нет сохраненных мерчантов.</i>"
    else:
        for m in merchants:
            c_count = len(m.contacts) if m.contacts else 0
            nick = m.nickname or "Без ника"
            text += f"👤 <b>{nick}</b> (UserNo: <code>{m.user_no}</code>) | Контактов: <code>{c_count}</code>\n"
            buttons.append([InlineKeyboardButton(text=f"🎴 Карточка: {nick}", callback_data=f"merch_card_{m.id}_{page}")])

    # Pagination row
    nav_row = []
    if page > 1:
        nav_row.append(InlineKeyboardButton(text="◀️ Назад", callback_data=f"merch_page_{page - 1}"))
    nav_row.append(InlineKeyboardButton(text=f"📄 {page}/{total_pages}", callback_data="ignore"))
    if page < total_pages:
        nav_row.append(InlineKeyboardButton(text="Вперед ▶️", callback_data=f"merch_page_{page + 1}"))
    buttons.append(nav_row)

    buttons.append([
        InlineKeyboardButton(text="🔎 Поиск Мерчанта", callback_data="merch_search_start"),
        InlineKeyboardButton(text="⬅️ Главное Меню", callback_data="menu_main"),
    ])

    await call.message.edit_text(text, reply_markup=InlineKeyboardMarkup(inline_keyboard=buttons), parse_mode="HTML")

@router.callback_query(F.data.startswith("merch_card_"))
async def cb_merchant_card(call: CallbackQuery):
    parts = call.data.split("_")
    m_id = int(parts[2])
    current_page = int(parts[3]) if len(parts) > 3 else 1

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

    remarks_text = m.remarks[:400] if m.remarks else "<i>(Описание отсутствует в базе)</i>"

    text = (
        f"🎴 <b>КАРТОЧКА МЕРЧАНТА P2P</b>\n\n"
        f"👤 <b>Никнейм:</b> <a href='{profile_url}'>{m.nickname or 'Без ника'}</a>\n"
        f"🆔 <b>UserNo:</b> <code>{m.user_no}</code>\n"
        f"📈 <b>Сделок за месяц:</b> <code>{m.month_order_count}</code> ({m.month_finish_rate * 100:.1f}%)\n"
        f"🕒 <b>Впервые замечен:</b> {m.first_seen_at.strftime('%Y-%m-%d %H:%M')}\n\n"
        f"📞 <b>Извлеченные контакты:</b>\n{contacts_str}\n"
        f"📝 <b>Описание / Условия:</b>\n{remarks_text}\n"
    )

    buttons = [
        [InlineKeyboardButton(text="⚡ Обновить Мерчанта", callback_data=f"merch_refresh_{m.id}_{current_page}")],
        [InlineKeyboardButton(text="🔗 Профиль на Binance P2P", url=profile_url)],
        [InlineKeyboardButton(text="⬅️ Назад к Списку", callback_data=f"merch_page_{current_page}")],
    ]

    await call.message.edit_text(text, reply_markup=InlineKeyboardMarkup(inline_keyboard=buttons), parse_mode="HTML", disable_web_page_preview=True)

@router.callback_query(F.data.startswith("merch_refresh_"))
async def cb_merchant_refresh(call: CallbackQuery):
    parts = call.data.split("_")
    m_id = int(parts[2])
    current_page = int(parts[3]) if len(parts) > 3 else 1

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
                adv_data = items[0].get("adv", {})
                remarks = adv_data.get("remarks") or ""
                auto_reply = adv_data.get("autoReplyMsg") or ""
                m.remarks = remarks or m.remarks
                m.auto_reply_msg = auto_reply or m.auto_reply_msg
                await session.commit()
                await safe_answer(call, "Данные мерчанта обновлены!", show_alert=True)
            else:
                await safe_answer(call, "Активные объявления не найдены", show_alert=True)
        except Exception as e:
            await safe_answer(call, f"Ошибка обновления: {e}", show_alert=True)
        finally:
            await client.close()

    await cb_merchant_card(call)

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
        merchants = await repo.search_by_query(query)

    if not merchants:
        await message.answer(f"🔍 Мерчанты по запросу `'{query}'` не найдены.", reply_markup=get_back_menu_keyboard(), parse_mode="HTML")
        return

    text = f"🔎 <b>РЕЗУЛЬТАТЫ ПОИСКА ПО ЗАПРОСУ: `{query}`</b>\n\n"
    buttons = []
    for m in merchants[:10]:
        text += f"• 👤 <b>{m.nickname or 'Без ника'}</b> (<code>{m.user_no}</code>)\n"
        buttons.append([InlineKeyboardButton(text=f"🎴 Карточка {m.nickname}", callback_data=f"merch_card_{m.id}_1")])

    buttons.append([InlineKeyboardButton(text="⬅️ Главное Меню", callback_data="menu_main")])
    await message.answer(text, reply_markup=InlineKeyboardMarkup(inline_keyboard=buttons), parse_mode="HTML")
