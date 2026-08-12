import logging
from aiogram import Router, F
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import CallbackQuery, Message, InlineKeyboardButton, InlineKeyboardMarkup
from sqlalchemy import select

from app.bot.keyboards.main_kb import get_back_menu_keyboard
from app.db.database import AsyncSessionLocal
from app.db.models import AdminUser

logger = logging.getLogger(__name__)
router = Router()

class AdminForm(StatesGroup):
    input_user = State()

@router.callback_query(F.data == "menu_admins")
async def cb_admins_list(call: CallbackQuery):
    try:
        await call.answer()
    except Exception:
        pass

    async with AsyncSessionLocal() as session:
        res = await session.execute(select(AdminUser))
        admins = res.scalars().all()

    text = "👥 <b>СПИСОК АДМИНИСТРАТОРОВ БОТА</b>\n\n"
    buttons = []
    for a in admins:
        user_str = f"@{a.username}" if a.username else f"ID: {a.telegram_id}"
        role_icon = "👑" if a.role == "superadmin" else "👤"
        text += f"• {role_icon} <b>{user_str}</b> | Роль: <code>{a.role}</code>\n"
        
        if a.role != "superadmin":
            buttons.append([InlineKeyboardButton(text=f"❌ Удалить {user_str}", callback_data=f"adm_del_{a.id}")])

    buttons.append([InlineKeyboardButton(text="➕ Добавить Админа", callback_data="adm_add")])
    buttons.append([InlineKeyboardButton(text="⬅️ Главное Меню", callback_data="menu_main")])

    await call.message.edit_text(text, reply_markup=InlineKeyboardMarkup(inline_keyboard=buttons), parse_mode="HTML")

@router.callback_query(F.data == "adm_add")
async def cb_adm_add(call: CallbackQuery, state: FSMContext):
    try:
        await call.answer()
    except Exception:
        pass
    await state.set_state(AdminForm.input_user)
    text = (
        "➕ <b>ДОБАВЛЕНИЕ АДМИНИСТРАТОРА</b>\n\n"
        "Введите Telegram `@username` (например: <code>@sergebybitp2p</code>) "
        "или числовой `Telegram User ID` нового администратора:"
    )
    await call.message.edit_text(text, reply_markup=get_back_menu_keyboard(), parse_mode="HTML")

@router.message(AdminForm.input_user)
async def process_adm_input(message: Message, state: FSMContext):
    input_text = message.text.strip()
    await state.clear()

    async with AsyncSessionLocal() as session:
        if input_text.isdigit():
            user_id = int(input_text)
            admin = AdminUser(telegram_id=user_id, role="admin")
        else:
            username_clean = input_text.lower().lstrip("@")
            admin = AdminUser(username=username_clean, role="admin")

        session.add(admin)
        try:
            await session.commit()
            await message.answer(f"✅ <b>Администратор `{input_text}` успешно добавлен!</b>", reply_markup=get_back_menu_keyboard(), parse_mode="HTML")
        except Exception as e:
            await session.rollback()
            await message.answer(f"⚠️ Ошибка добавления (возможно, уже существует): {e}", reply_markup=get_back_menu_keyboard(), parse_mode="HTML")

@router.callback_query(F.data.startswith("adm_del_"))
async def cb_adm_del(call: CallbackQuery):
    adm_id = int(call.data.split("_")[2])
    async with AsyncSessionLocal() as session:
        res = await session.execute(select(AdminUser).where(AdminUser.id == adm_id))
        admin = res.scalar_one_or_none()
        if admin:
            await session.delete(admin)
            await session.commit()
            try:
                await call.answer("Администратор удален", show_alert=True)
            except Exception:
                pass
    await cb_admins_list(call)
