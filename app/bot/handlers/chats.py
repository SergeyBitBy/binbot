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
from sqlalchemy import select
from sqlalchemy.exc import OperationalError

from app.bot.keyboards.main_kb import get_back_menu_keyboard
from app.db.database import AsyncSessionLocal, database_write_lock
from app.db.models import AllowedChat

logger = logging.getLogger(__name__)
router = Router()

class ChatForm(StatesGroup):
    input_chat = State()


async def add_allowed_chat(chat_id: int, max_attempts: int = 3) -> bool:
    for attempt in range(1, max_attempts + 1):
        try:
            async with database_write_lock:
                async with AsyncSessionLocal() as session:
                    existing = await session.scalar(
                        select(AllowedChat.id).where(AllowedChat.chat_id == chat_id)
                    )
                    if existing:
                        return False
                    session.add(AllowedChat(chat_id=chat_id, title=f"Чат {chat_id}"))
                    await session.commit()
                    return True
        except OperationalError as exc:
            if "database is locked" not in str(exc).lower() or attempt == max_attempts:
                raise
            delay = min(5.0, attempt * 1.5)
            logger.warning(
                "SQLite was busy while adding chat_id=%s; retry %s/%s in %.1fs",
                chat_id,
                attempt,
                max_attempts,
                delay,
            )
            await asyncio.sleep(delay)
    return False

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

    try:
        added = await add_allowed_chat(chat_id)
        if added:
            await message.answer(f"✅ <b>Чат ID `{chat_id}` успешно добавлен!</b>", reply_markup=get_back_menu_keyboard(), parse_mode="HTML")
        else:
            await message.answer("ℹ️ Этот чат уже находится в списке разрешенных.", reply_markup=get_back_menu_keyboard())
    except OperationalError:
        logger.exception("Could not add chat_id=%s after SQLite retries", chat_id)
        await message.answer(
            "⚠️ База данных занята длительным сканированием. Попробуйте еще раз через минуту.",
            reply_markup=get_back_menu_keyboard(),
        )

@router.callback_query(F.data.startswith("chat_del_"))
async def cb_chat_del(call: CallbackQuery):
    c_id = int(call.data.split("_")[2])
    async with database_write_lock:
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
