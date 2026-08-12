import logging
from aiogram import Router, F
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message, InlineKeyboardButton, InlineKeyboardMarkup

from app.bot.keyboards.main_kb import (
    get_main_menu_keyboard, get_profile_detail_keyboard, get_profiles_keyboard, get_paytypes_multiselect_keyboard
)
from app.bot.states.profile_states import ProfileForm
from app.db.database import AsyncSessionLocal
from app.db.repositories.profile_repo import ProfileRepository

logger = logging.getLogger(__name__)
router = Router()

async def safe_answer(call: CallbackQuery, text: str = None, show_alert: bool = False):
    try:
        await call.answer(text=text, show_alert=show_alert)
    except Exception:
        pass

@router.callback_query(F.data == "menu_profiles")
async def cb_profiles_list(call: CallbackQuery):
    await safe_answer(call)
    async with AsyncSessionLocal() as session:
        repo = ProfileRepository(session)
        profiles = await repo.get_all()

    text = "⚙️ <b>СПИСОК ПРОФИЛЕЙ МОНИТОРИНГА</b>\n\nВыберите профиль для просмотра или настройки:"
    await call.message.edit_text(text, reply_markup=get_profiles_keyboard(profiles), parse_mode="HTML")

@router.callback_query(F.data.startswith("prof_view_"))
async def cb_profile_view(call: CallbackQuery):
    prof_id = int(call.data.split("_")[2])
    async with AsyncSessionLocal() as session:
        repo = ProfileRepository(session)
        p = await repo.get_by_id(prof_id)

    if not p:
        await safe_answer(call, "Профиль не найден", show_alert=True)
        return

    await safe_answer(call)
    status = "🟢 Активен" if p.is_active else "🔴 Приостановлен"
    merchant_filter = "🛡️ Только Проверенные (Verified)" if p.merchant_check else "🌐 Все Мерчанты"
    baseline = "✅ Завершен" if p.is_baseline_completed else "⏳ Ожидает первого сканирования"
    pay_types_str = ", ".join(p.pay_types) if p.pay_types else "Все способы оплаты"

    text = (
        f"⚙️ <b>ПРОФИЛЬ МОНИТОРИНГА: {p.name}</b>\n\n"
        f"Статус: {status}\n"
        f"Фильтр мерчантов: {merchant_filter}\n"
        f"Базовая линия: {baseline}\n\n"
        f"🪙 <b>Ассет:</b> <code>{p.asset}</code>\n"
        f"💵 <b>Фиат:</b> <code>{p.fiat}</code>\n"
        f"🔄 <b>Тип сделки:</b> <code>{p.trade_type}</code>\n"
        f"💳 <b>Способы оплаты:</b> {pay_types_str}\n"
        f"⏱ <b>Интервал сканирования:</b> <code>{p.scan_interval_seconds} сек</code>\n"
    )

    await call.message.edit_text(text, reply_markup=get_profile_detail_keyboard(p.id, p.is_active, p.merchant_check), parse_mode="HTML")

@router.callback_query(F.data.startswith("prof_toggle_"))
async def cb_profile_toggle(call: CallbackQuery):
    prof_id = int(call.data.split("_")[2])
    async with AsyncSessionLocal() as session:
        repo = ProfileRepository(session)
        p = await repo.get_by_id(prof_id)
        if p:
            p.is_active = not p.is_active
            await session.commit()
            status_text = "активирован" if p.is_active else "приостановлен"
            await safe_answer(call, f"Профиль {status_text}!", show_alert=True)

    await cb_profile_view(call)

@router.callback_query(F.data.startswith("prof_check_"))
async def cb_profile_check_toggle(call: CallbackQuery):
    prof_id = int(call.data.split("_")[2])
    async with AsyncSessionLocal() as session:
        repo = ProfileRepository(session)
        p = await repo.get_by_id(prof_id)
        if p:
            p.merchant_check = not p.merchant_check
            await session.commit()
            status_text = "Только проверенные" if p.merchant_check else "Все мерчанты"
            await safe_answer(call, f"Фильтр изменен: {status_text}", show_alert=True)

    await cb_profile_view(call)

@router.callback_query(F.data.startswith("prof_paytypes_"))
async def cb_profile_paytypes(call: CallbackQuery):
    prof_id = int(call.data.split("_")[2])
    async with AsyncSessionLocal() as session:
        repo = ProfileRepository(session)
        p = await repo.get_by_id(prof_id)
        if not p:
            return

    await safe_answer(call)
    text = (
        f"💳 <b>НАСТРОЙКА БАНКОВ И СПОСОБОВ ОПЛАТЫ ({p.name})</b>\n\n"
        "Отмечайте нужные способы оплаты галочками `[✅]`. Изменения сохраняются сразу:"
    )
    await call.message.edit_text(text, reply_markup=get_paytypes_multiselect_keyboard(p.id, p.pay_types), parse_mode="HTML")

@router.callback_query(F.data.startswith("pay_toggle_"))
async def cb_paytype_toggle(call: CallbackQuery):
    parts = call.data.split("_")
    prof_id = int(parts[2])
    bank_name = "_".join(parts[3:])

    async with AsyncSessionLocal() as session:
        repo = ProfileRepository(session)
        p = await repo.get_by_id(prof_id)
        if p:
            current_pays = list(p.pay_types or [])
            if bank_name in current_pays:
                current_pays.remove(bank_name)
            else:
                current_pays.append(bank_name)
            p.pay_types = current_pays
            await session.commit()
            await safe_answer(call, f"Способ оплаты {bank_name} обновлен")

    await cb_profile_paytypes(call)

@router.callback_query(F.data.startswith("prof_delete_confirm_"))
async def cb_profile_delete_confirm(call: CallbackQuery):
    prof_id = int(call.data.split("_")[3])
    await safe_answer(call)
    text = "⚠️ <b>Вы уверены, что хотите полностью удалить этот профиль мониторинга?</b>"
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(text="✅ Да, Удалить", callback_data=f"prof_delete_do_{prof_id}"),
            InlineKeyboardButton(text="❌ Отмена", callback_data=f"prof_view_{prof_id}"),
        ]
    ])
    await call.message.edit_text(text, reply_markup=kb, parse_mode="HTML")

@router.callback_query(F.data.startswith("prof_delete_do_"))
async def cb_profile_delete_do(call: CallbackQuery):
    prof_id = int(call.data.split("_")[3])
    async with AsyncSessionLocal() as session:
        repo = ProfileRepository(session)
        await repo.delete(prof_id)
    await safe_answer(call, "Профиль удален!", show_alert=True)
    await cb_profiles_list(call)

# FSM Profile Creation
@router.callback_query(F.data == "prof_create")
async def cb_prof_create_start(call: CallbackQuery, state: FSMContext):
    await safe_answer(call)
    await state.set_state(ProfileForm.name)
    await call.message.edit_text(
        "➕ <b>СОЗДАНИЕ ПРОФИЛЯ</b>\n\nШаг 1/5: Введите название профиля (например: <code>UAH Monobank Buy</code>):",
        parse_mode="HTML"
    )

@router.message(ProfileForm.name)
async def process_prof_name(message: Message, state: FSMContext):
    await state.update_data(name=message.text.strip())
    await state.set_state(ProfileForm.asset)
    await message.answer("Шаг 2/5: Введите криптовалюту/ассет (по умолчанию <code>USDT</code>, <code>BTC</code>, <code>ETH</code>):", parse_mode="HTML")

@router.message(ProfileForm.asset)
async def process_prof_asset(message: Message, state: FSMContext):
    await state.update_data(asset=message.text.strip().upper())
    await state.set_state(ProfileForm.fiat)
    await message.answer("Шаг 3/5: Введите фиатную валюту (по умолчанию <code>UAH</code>, <code>USD</code>, <code>EUR</code>):", parse_mode="HTML")

@router.message(ProfileForm.fiat)
async def process_prof_fiat(message: Message, state: FSMContext):
    await state.update_data(fiat=message.text.strip().upper())
    await state.set_state(ProfileForm.trade_type)
    
    kb = InlineKeyboardMarkup(inline_keyboard=[[
        InlineKeyboardButton(text="ПОКУПКА (BUY)", callback_data="type_BUY"),
        InlineKeyboardButton(text="ПРОДАЖА (SELL)", callback_data="type_SELL"),
    ]])
    await message.answer("Шаг 4/5: Выберите направление сделки (BUY/SELL):", reply_markup=kb)

@router.callback_query(ProfileForm.trade_type, F.data.startswith("type_"))
async def process_prof_trade_type(call: CallbackQuery, state: FSMContext):
    await safe_answer(call)
    trade_type = call.data.split("_")[1]
    await state.update_data(trade_type=trade_type)
    await state.set_state(ProfileForm.scan_interval)
    await call.message.edit_text("Шаг 5/5: Введите интервал сканирования в секундах (например: <code>60</code>):", parse_mode="HTML")

@router.message(ProfileForm.scan_interval)
async def process_prof_interval(message: Message, state: FSMContext):
    try:
        interval = int(message.text.strip())
        if interval < 10:
            interval = 10
    except ValueError:
        interval = 60

    data = await state.get_data()
    async with AsyncSessionLocal() as session:
        repo = ProfileRepository(session)
        await repo.create(
            name=data["name"],
            asset=data["asset"],
            fiat=data["fiat"],
            trade_type=data["trade_type"],
            scan_interval_seconds=interval,
        )

    await state.clear()
    await message.answer("🎉 <b>Профиль успешно создан и добавлен в систему!</b>", reply_markup=get_main_menu_keyboard(), parse_mode="HTML")
