import logging
from typing import Any, Awaitable, Callable, Dict
from aiogram import BaseMiddleware
from aiogram.types import CallbackQuery, Message, TelegramObject

from app.config.settings import settings
from app.db.database import AsyncSessionLocal
from app.db.repositories.audit_repo import AuditRepository

logger = logging.getLogger(__name__)

class AuthMiddleware(BaseMiddleware):
    """Middleware to enforce strict Admin / Allowed Chat authorization (Section 27, 29, 31, 139)."""

    async def __call__(
        self,
        handler: Callable[[TelegramObject, Dict[str, Any]], Awaitable[Any]],
        event: TelegramObject,
        data: Dict[str, Any],
    ) -> Any:
        try:
            user_id = None
            username = None
            chat_id = None
            text = None

            if isinstance(event, Message):
                user_id = event.from_user.id if event.from_user else None
                username = event.from_user.username if event.from_user else None
                chat_id = event.chat.id if event.chat else None
                text = event.text or ""
            elif isinstance(event, CallbackQuery):
                user_id = event.from_user.id if event.from_user else None
                username = event.from_user.username if event.from_user else None
                chat_id = event.message.chat.id if event.message and event.message.chat else None

            logger.info(f"Incoming event from user_id={user_id}, username={username}, chat_id={chat_id}, text={text}")

            clean_username = username.lower().lstrip("@") if username else ""
            initial_admin = settings.initial_admin_username.lower().lstrip("@")
            initial_chat = settings.initial_allowed_chat_id

            # Allow initial admin by username or allowed chat_id or user_id
            if (clean_username and clean_username == initial_admin) or (chat_id and chat_id == initial_chat) or (user_id and user_id == initial_chat):
                data["is_authorized"] = True
                return await handler(event, data)

            # Check DB authorization
            is_user_ok = False
            is_chat_ok = False
            try:
                async with AsyncSessionLocal() as session:
                    repo = AuditRepository(session)
                    is_user_ok = await repo.is_authorized_user(user_id, username)
                    is_chat_ok = await repo.is_allowed_chat(chat_id) if chat_id else False
            except Exception as dbe:
                logger.error(f"Error checking authorization in DB: {dbe}")

            if is_user_ok or is_chat_ok:
                data["is_authorized"] = True
                return await handler(event, data)

            logger.warning(f"Unauthorized access attempt by user_id={user_id}, username={username}, chat_id={chat_id}")
            
            # Always pass /start command through so start handler can process it
            if text and text.startswith("/start"):
                data["is_authorized"] = False
                return await handler(event, data)

            deny_text = (
                f"⛔ <b>Доступ запрещен.</b> У вас нет прав для управления этим ботом.\n\n"
                f"🆔 <b>Ваш Telegram User ID:</b> <code>{user_id}</code>\n"
                f"💬 <b>Ваш Chat ID:</b> <code>{chat_id}</code>\n"
                f"👤 <b>Ваш Username:</b> <code>@{username or 'Не задан'}</code>"
            )

            if isinstance(event, Message):
                await event.answer(deny_text, parse_mode="HTML")
            elif isinstance(event, CallbackQuery):
                await event.answer("⛔ Доступ запрещен", show_alert=True)

            return None
        except Exception as e:
            logger.exception(f"Unhandled error in AuthMiddleware: {e}")
            return None
