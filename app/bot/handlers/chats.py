import logging
from aiogram import Router, F
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import CallbackQuery, Message, InlineKeyboardButton, InlineKeyboardMarkup
from sqlalchemy import select

from app.bot.keyboards.main_kb import get_back_menu_keyboard
from app.db.database import AsyncSessionLocal
from app.db.models import AllowedChat

logger = logging.getLogger(__name__)
router = Router()

class ChatForm(StatesGroup):
    input_chat = State()

@router.callback_query(F.data == "menu_chats")
async def cb_chats_list(call: CallbackQuery):
    try:
        await call.answer()
    except Exception:
        pass

    async with AsyncSessionLocal() as session:
        res = await session.execute(select(AllowedChat))
        chats = res.scalars().all()

    text = "💬 <b>РАЗРЕШЕННЫЕ ЧАТЫ ДЛЯ УВЕДОМЛЕНИЙ</b>\n\n"
    buttons = []
    for c in chats:
        title_str = c.title or "Личный Чат"
        text += f"• 💬 <b>{title_str}</b> (ID: <code>{c.chat_id}</code>)\n"
        buttons.append([InlineKeyboardButton(text=f"❌ Удалить Чат {c.chat_id}", callback_data=f"chat_del_{c.id}")])

    buttons.append([InlineKeyboardButton(text="➕ Добавить Чат по ID", callback_data="chat_add")])
    buttons.append([InlineKeyboardButton(text="⬅️ Главное Меню", callback_data="menu_main")])

    await call.message.edit_text(text, reply_markup=InlineKeyboardMarkup(inline_keyboard=buttons), parse_mode="HTML")

@router.callback_query(F.data == "chat_add")
async def cb_chat_add(call: CallbackQuery, state: FSMContext):
    try:
        await call.answer()
    except Exception:
        pass
    await state.set_state(ChatForm.input_chat)
    text = (
        "➕ <b>ДОБАВЛЕНИЕ ЧАТА УВЕДОМЛЕНИЙ</b>\n\n"
        "Введите числовой `Telegram Chat ID` (например: <code>930460307</code> или <code>-100123456789</code>):"
    )
    await call.message.edit_text(text, reply_markup=get_back_menu_keyboard(), parse_mode="HTML")

@router.message(ChatForm.input_chat)
async def process_chat_input(message: Message, state: FSMContext):
    input_text = message.text.strip()
    await state.clear()

    try:
        chat_id = int(input_text)
    except ValueError:
        await message.answer("⚠️ Ошибка: Chat ID должен быть целым числом.", reply_markup=get_back_menu_keyboard())
        return

    async with AsyncSessionLocal() as session:
        chat = AllowedChat(chat_id=chat_id, title=f"Чат {chat_id}")
        session.add(chat)
        try:
            await session.commit()
            await message.answer(f"✅ <b>Чат ID `{chat_id}` успешно добавлен!</b>", reply_markup=get_back_menu_keyboard(), parse_mode="HTML")
        except Exception as e:
            await session.rollback()
            await message.answer(f"⚠️ Ошибка добавления (возможно, уже существует): {e}", reply_markup=get_back_menu_keyboard(), parse_mode="HTML")

@router.callback_query(F.data.startswith("chat_del_"))
async def cb_chat_del(call: CallbackQuery):
    c_id = int(call.data.split("_")[2])
    async with AsyncSessionLocal() as session:
        res = await session.execute(select(AllowedChat).where(AllowedChat.id == c_id))
        chat = res.scalar_one_or_none()
        if chat:
            await session.delete(chat)
            await session.commit()
            try:
                await call.answer("Чат удален из рассылки", show_alert=True)
            except Exception:
                pass
    await cb_chats_list(call)
