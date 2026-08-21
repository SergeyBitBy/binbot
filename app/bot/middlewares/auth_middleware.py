import logging
import time
from typing import Any, Awaitable, Callable, Dict, Optional, Tuple

from aiogram import BaseMiddleware
from aiogram.types import CallbackQuery, Message, TelegramObject

from app.bot.access import is_action_allowed, normalize_role
from app.db.database import AsyncSessionLocal
from app.db.repositories.audit_repo import AuditRepository

logger = logging.getLogger(__name__)

# In-memory authorization cache with 60s TTL
_AUTH_USER_CACHE: Dict[int, Tuple[bool, Optional[str], float]] = {}
_AUTH_CHAT_CACHE: Dict[int, Tuple[bool, float]] = {}


class AuthMiddleware(BaseMiddleware):
    """Middleware to enforce strict Admin / Allowed Chat authorization with high-speed in-memory caching."""

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

            now_mono = time.monotonic()
            is_user_ok = False
            role = None
            is_chat_ok = False

            # Check user cache
            if user_id and user_id in _AUTH_USER_CACHE:
                cached_ok, cached_role, exp = _AUTH_USER_CACHE[user_id]
                if now_mono < exp:
                    is_user_ok = cached_ok
                    role = cached_role

            # Check chat cache
            if chat_id and chat_id in _AUTH_CHAT_CACHE:
                cached_chat_ok, exp = _AUTH_CHAT_CACHE[chat_id]
                if now_mono < exp:
                    is_chat_ok = cached_chat_ok

            # If cache miss, query DB
            if (user_id and user_id not in _AUTH_USER_CACHE) or (chat_id and chat_id not in _AUTH_CHAT_CACHE):
                try:
                    async with AsyncSessionLocal() as session:
                        repo = AuditRepository(session)
                        if user_id and (user_id not in _AUTH_USER_CACHE or now_mono >= _AUTH_USER_CACHE[user_id][2]):
                            is_user_ok = await repo.is_authorized_user(user_id, username)
                            role = await repo.get_user_role(user_id)
                            _AUTH_USER_CACHE[user_id] = (is_user_ok, role, now_mono + 60.0)

                        if chat_id and (chat_id not in _AUTH_CHAT_CACHE or now_mono >= _AUTH_CHAT_CACHE[chat_id][1]):
                            is_chat_ok = await repo.is_allowed_chat(chat_id)
                            _AUTH_CHAT_CACHE[chat_id] = (is_chat_ok, now_mono + 60.0)
                except Exception as dbe:
                    logger.error(f"Error checking authorization in DB: {dbe}")

            is_private = isinstance(event, Message) and event.chat.type == "private"
            if isinstance(event, CallbackQuery) and event.message:
                is_private = event.message.chat.type == "private"
            if is_user_ok and (is_private or is_chat_ok):
                data["is_authorized"] = True
                data["role"] = normalize_role(role)
                callback_data = event.data if isinstance(event, CallbackQuery) else ""
                command_text = text.split()[0].lower().split("@", 1)[0] if text else ""
                state = data.get("raw_state") or ""
                if not state and data.get("state"):
                    state = await data["state"].get_state() or ""
                if not is_action_allowed(
                    data["role"],
                    callback_data=callback_data,
                    command=command_text,
                    state=state,
                ):
                    if isinstance(event, CallbackQuery):
                        await event.answer("⛔ Недостаточно прав для этого действия", show_alert=True)
                    else:
                        await event.answer("⛔ Недостаточно прав для этого действия")
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
            try:
                error_text = "⚠️ Не удалось выполнить действие. Попробуйте ещё раз или откройте /start."
                if isinstance(event, Message):
                    await event.answer(error_text)
                elif isinstance(event, CallbackQuery):
                    await event.answer(error_text, show_alert=True)
            except Exception:
                logger.exception("Could not send handler error to Telegram")
            return None
