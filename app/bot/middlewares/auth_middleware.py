import logging
from collections.abc import Awaitable, Callable
from typing import Any

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
        handler: Callable[[TelegramObject, dict[str, Any]], Awaitable[Any]],
        event: TelegramObject,
        data: dict[str, Any],
    ) -> Any:
        user_id = None
        username = None
        chat_id = None

        if isinstance(event, Message):
            user_id = event.from_user.id if event.from_user else None
            username = event.from_user.username if event.from_user else None
            chat_id = event.chat.id if event.chat else None
        elif isinstance(event, CallbackQuery):
            user_id = event.from_user.id if event.from_user else None
            username = event.from_user.username if event.from_user else None
            chat_id = event.message.chat.id if event.message and event.message.chat else None

        # Check default config bypasses (Section 139)
        clean_username = username.lower().lstrip("@") if username else ""
        initial_admin = settings.initial_admin_username.lower().lstrip("@")
        initial_chat = settings.initial_allowed_chat_id

        if (clean_username and clean_username == initial_admin) or (chat_id and chat_id == initial_chat):
            return await handler(event, data)

        async with AsyncSessionLocal() as session:
            repo = AuditRepository(session)
            is_user_ok = await repo.is_authorized_user(user_id, username)
            is_chat_ok = await repo.is_allowed_chat(chat_id) if chat_id else False

        if is_user_ok or is_chat_ok:
            return await handler(event, data)

        logger.warning(f"Unauthorized access attempt by user_id={user_id}, username={username}, chat_id={chat_id}")
        
        if isinstance(event, Message):
            await event.answer("⛔ <b>Доступ запрещен.</b> У вас нет прав для управления ботом.", parse_mode="HTML")
        elif isinstance(event, CallbackQuery):
            await event.answer("⛔ Доступ запрещен", show_alert=True)
            
        return None
