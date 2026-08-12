import logging

from aiogram import F, Router
from aiogram.fsm.context import FSMContext
from aiogram.types import (
    BufferedInputFile,
    CallbackQuery,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    Message,
)

from app.bot.states.profile_states import MerchantSearchForm
from app.db.database import AsyncSessionLocal
from app.db.repositories.merchant_repo import MerchantRepository
from app.services.export_service import ExportService

logger = logging.getLogger(__name__)
router = Router()

@router.callback_query(F.data == "menu_merchants")
async def cb_merchants_list(call: CallbackQuery):
    async with AsyncSessionLocal() as session:
        repo = MerchantRepository(session)
        merchants, total_count = await repo.get_all_merchants(limit=5, offset=0)

    text = f"🔍 <b>БАЗА МЕРЧАНТОВ BINANCE P2P</b> (Всего: <code>{total_count}</code>)\n\n"
    if not merchants:
        text += "<i>Мерчанты пока не сохранены. Запустите сканирование!</i>"
    else:
        for m in merchants:
            contacts_str = ", ".join([f"<code>{c.value}</code>" for c in m.contacts]) or "Нет"
            profile_link = f"https://p2p.binance.com/advertiserDetail?advertiserNo={m.user_no}"
            text += (
                f"👤 <b><a href='{profile_link}'>{m.nickname or 'Без ника'}</a></b> (UserNo: <code>{m.user_no}</code>)\n"
                f"📊 Ордеров: {m.month_order_count} | Завершено: {m.month_finish_rate * 100:.1f}%\n"
                f"📞 Контакты: {contacts_str}\n\n"
            )

    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🔎 Искать по нику/UserNo", callback_data="search_merchant_start")],
        [InlineKeyboardButton(text="📥 Скачать CSV", callback_data="menu_export_csv")],
        [InlineKeyboardButton(text="🔙 Главное Меню", callback_data="menu_main")],
    ])
    await call.message.edit_text(text, reply_markup=kb, parse_mode="HTML", disable_web_page_preview=True)
    await call.answer()

@router.callback_query(F.data == "search_merchant_start")
async def cb_search_merchant_start(call: CallbackQuery, state: FSMContext):
    await state.set_state(MerchantSearchForm.query)
    await call.message.edit_text("🔎 <b>ВВЕДИТЕ ПОИСКОВЫЙ ЗАПРОС:</b>\n\nНикнейм, UserNo или ключевое слово из заметки:", parse_mode="HTML")
    await call.answer()

@router.message(MerchantSearchForm.query)
async def process_search_query(message: Message, state: FSMContext):
    query = message.text.strip()
    await state.clear()

    async with AsyncSessionLocal() as session:
        repo = MerchantRepository(session)
        merchants, total_count = await repo.get_all_merchants(limit=10, offset=0, search_query=query)

    text = f"🔎 <b>РЕЗУЛЬТАТЫ ПОИСКА: '{query}'</b> (Найдено: {total_count})\n\n"
    if not merchants:
        text += "<i>По вашему запросу ничего не найдено.</i>"
    else:
        for m in merchants:
            contacts_str = ", ".join([f"<code>{c.value}</code>" for c in m.contacts]) or "Нет"
            profile_link = f"https://p2p.binance.com/advertiserDetail?advertiserNo={m.user_no}"
            text += (
                f"👤 <b><a href='{profile_link}'>{m.nickname or 'Без ника'}</a></b> (UserNo: <code>{m.user_no}</code>)\n"
                f"📞 Контакты: {contacts_str}\n\n"
            )

    kb = InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="🔙 К Базе Мерчантов", callback_data="menu_merchants")]])
    await message.answer(text, reply_markup=kb, parse_mode="HTML", disable_web_page_preview=True)

@router.callback_query(F.data == "menu_export_csv")
async def cb_export_csv(call: CallbackQuery):
    await call.answer("Генерация CSV файла...", show_alert=False)
    csv_data = await ExportService.export_merchants_csv()
    
    input_file = BufferedInputFile(csv_data.encode("utf-8-sig"), filename="binance_merchants.csv")
    await call.message.answer_document(
        document=input_file,
        caption="📥 <b>Экспорт мерчантов и контактов в формате CSV</b>",
        parse_mode="HTML"
    )
