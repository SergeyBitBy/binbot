import asyncio
import logging

from aiogram import F, Router
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import (
    CallbackQuery,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    Message,
)
from sqlalchemy import func, select

from app.bot.access import VALID_ROLES, can_change_role, can_delete_user
from app.bot.keyboards.main_kb import get_back_menu_keyboard
from app.db.database import AsyncSessionLocal
from app.db.models import AdminUser

logger = logging.getLogger(__name__)
router = Router()
_admin_mutation_lock = asyncio.Lock()

class AdminForm(StatesGroup):
    role = State()
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
        superadmin_count = await session.scalar(
            select(func.count(AdminUser.id)).where(AdminUser.role == "superadmin")
        )

    text = "👥 <b>СПИСОК АДМИНИСТРАТОРОВ БОТА</b>\n\n"
    buttons = []
    for a in admins:
        user_str = f"@{a.username}" if a.username else f"ID: {a.telegram_id}"
        role_icon = {"superadmin": "👑", "admin": "🛠", "viewer": "👁"}.get(a.role, "❓")
        text += f"• {role_icon} <b>{user_str}</b> | Роль: <code>{a.role}</code>\n"
        
        is_self = a.telegram_id is not None and a.telegram_id == call.from_user.id
        role_buttons = []
        for target_role, label in (
            ("superadmin", "👑 Superadmin"),
            ("admin", "🛠 Admin"),
            ("viewer", "👁 Viewer"),
        ):
            if target_role != a.role and can_change_role(
                actor_user_id=call.from_user.id,
                target_user_id=a.telegram_id,
                current_role=a.role,
                new_role=target_role,
                superadmin_count=superadmin_count or 0,
            ):
                role_buttons.append(InlineKeyboardButton(
                    text=label,
                    callback_data=f"adm_role_{a.id}_{target_role}",
                ))
        if role_buttons:
            buttons.append(role_buttons)
        if not is_self and can_delete_user(
            actor_user_id=call.from_user.id,
            target_user_id=a.telegram_id,
            target_role=a.role,
            superadmin_count=superadmin_count or 0,
        ):
            buttons.append([InlineKeyboardButton(text=f"❌ Удалить {user_str}", callback_data=f"adm_del_{a.id}")])

    buttons.append([InlineKeyboardButton(text="➕ Добавить пользователя", callback_data="adm_add")])
    buttons.append([InlineKeyboardButton(text="⬅️ Главное Меню", callback_data="menu_main")])

    await call.message.edit_text(text, reply_markup=InlineKeyboardMarkup(inline_keyboard=buttons), parse_mode="HTML")

@router.callback_query(F.data == "adm_add")
async def cb_adm_add(call: CallbackQuery, state: FSMContext):
    try:
        await call.answer()
    except Exception:
        pass
    await state.set_state(AdminForm.role)
    text = (
        "➕ <b>ДОБАВЛЕНИЕ ПОЛЬЗОВАТЕЛЯ</b>\n\nВыберите роль:"
    )
    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="👑 Superadmin", callback_data="adm_add_role_superadmin")],
        [InlineKeyboardButton(text="🛠 Admin", callback_data="adm_add_role_admin")],
        [InlineKeyboardButton(text="👁 Viewer", callback_data="adm_add_role_viewer")],
        [InlineKeyboardButton(text="⬅️ Главное Меню", callback_data="menu_main")],
    ])
    await call.message.edit_text(text, reply_markup=keyboard, parse_mode="HTML")


@router.callback_query(AdminForm.role, F.data.startswith("adm_add_role_"))
async def cb_adm_add_role(call: CallbackQuery, state: FSMContext):
    role = call.data.removeprefix("adm_add_role_")
    if role not in VALID_ROLES:
        await call.answer("Недопустимая роль", show_alert=True)
        return
    await state.update_data(new_user_role=role)
    await state.set_state(AdminForm.input_user)
    await call.answer()
    text = (
        f"Роль: <b>{role}</b>\n\nВведите Telegram <code>@username</code> "
        "или числовой Telegram User ID пользователя:"
    )
    await call.message.edit_text(text, reply_markup=get_back_menu_keyboard(), parse_mode="HTML")

@router.message(AdminForm.input_user)
async def process_adm_input(message: Message, state: FSMContext):
    input_text = message.text.strip()
    data = await state.get_data()
    role = data.get("new_user_role", "viewer")
    await state.clear()

    async with AsyncSessionLocal() as session:
        if input_text.isdigit():
            user_id = int(input_text)
            admin = AdminUser(telegram_id=user_id, role=role)
        else:
            username_clean = input_text.lower().lstrip("@")
            admin = AdminUser(username=username_clean, role=role)

        session.add(admin)
        try:
            await session.commit()
            await message.answer(f"✅ <b>Пользователь `{input_text}` добавлен с ролью `{role}`.</b>", reply_markup=get_back_menu_keyboard(), parse_mode="HTML")
        except Exception as e:
            await session.rollback()
            await message.answer(f"⚠️ Ошибка добавления (возможно, уже существует): {e}", reply_markup=get_back_menu_keyboard(), parse_mode="HTML")

@router.callback_query(F.data.startswith("adm_del_"))
async def cb_adm_del(call: CallbackQuery):
    adm_id = int(call.data.split("_")[2])
    async with _admin_mutation_lock:
        async with AsyncSessionLocal() as session:
            res = await session.execute(select(AdminUser).where(AdminUser.id == adm_id))
            admin = res.scalar_one_or_none()
            if admin:
                superadmin_count = await session.scalar(
                    select(func.count(AdminUser.id)).where(AdminUser.role == "superadmin")
                )
                if not can_delete_user(
                    actor_user_id=call.from_user.id,
                    target_user_id=admin.telegram_id,
                    target_role=admin.role,
                    superadmin_count=superadmin_count or 0,
                ):
                    await call.answer(
                        "Нельзя удалить себя или последнего суперадминистратора",
                        show_alert=True,
                    )
                    return
                await session.delete(admin)
                await session.commit()
                try:
                    await call.answer("Администратор удален", show_alert=True)
                except Exception:
                    pass
    await cb_admins_list(call)


@router.callback_query(F.data.startswith("adm_role_"))
async def cb_adm_role(call: CallbackQuery):
    _, _, admin_id, role = call.data.split("_", 3)
    if role not in VALID_ROLES:
        await call.answer("Недопустимая роль", show_alert=True)
        return
    async with _admin_mutation_lock:
        async with AsyncSessionLocal() as session:
            admin = await session.get(AdminUser, int(admin_id))
            if not admin:
                await call.answer("Пользователь не найден", show_alert=True)
                return
            superadmin_count = await session.scalar(
                select(func.count(AdminUser.id)).where(AdminUser.role == "superadmin")
            )
            if not can_change_role(
                actor_user_id=call.from_user.id,
                target_user_id=admin.telegram_id,
                current_role=admin.role,
                new_role=role,
                superadmin_count=superadmin_count or 0,
            ):
                await call.answer(
                    "Нельзя понизить себя или последнего суперадминистратора",
                    show_alert=True,
                )
                return
            admin.role = role
            await session.commit()
    await call.answer(f"Новая роль: {role}", show_alert=True)
    await cb_admins_list(call)
