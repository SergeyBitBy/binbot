import logging
from typing import Any, Awaitable, Callable, Dict
from aiogram import BaseMiddleware
from aiogram.types import CallbackQuery, Message, TelegramObject

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

            logger.info("Incoming event user_id=%s chat_id=%s type=%s", user_id, chat_id, type(event).__name__)

            # Check DB authorization
            is_user_ok = False
            is_chat_ok = False
            role = None
            try:
                async with AsyncSessionLocal() as session:
                    repo = AuditRepository(session)
                    is_user_ok = await repo.is_authorized_user(user_id, username)
                    is_chat_ok = await repo.is_allowed_chat(chat_id) if chat_id else False
                    role = await repo.get_user_role(user_id)
            except Exception as dbe:
                logger.error(f"Error checking authorization in DB: {dbe}")

            is_private = isinstance(event, Message) and event.chat.type == "private"
            if isinstance(event, CallbackQuery) and event.message:
                is_private = event.message.chat.type == "private"
            if is_user_ok and (is_private or is_chat_ok):
                data["is_authorized"] = True
                data["role"] = role or "admin"
                callback_data = event.data if isinstance(event, CallbackQuery) else ""
                command_text = text.split()[0].lower() if text else ""
                superadmin_prefixes = (
                    "adm_", "chat_", "merch_delete_", "merch_clear_all_",
                    "prof_delete_", "menu_backup_db",
                )
                if callback_data.startswith(superadmin_prefixes) and data["role"] != "superadmin":
                    await event.answer("⛔ Требуются права superadmin", show_alert=True)
                    return None
                if command_text in ("/backup", "/retry_notifications") and data["role"] != "superadmin":
                    await event.answer("⛔ Требуются права superadmin")
                    return None
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
