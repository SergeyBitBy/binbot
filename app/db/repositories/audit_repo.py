import logging

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import AdminUser, AllowedChat, AuditLog

logger = logging.getLogger(__name__)

class AuditRepository:
    def __init__(self, session: AsyncSession):
        self.session = session

    async def log_action(self, action: str, user_id: int | None = None, username: str | None = None, details: str | None = None):
        log_entry = AuditLog(
            user_id=user_id,
            username=username,
            action=action,
            details=details,
        )
        self.session.add(log_entry)
        await self.session.commit()

    async def is_authorized_user(self, user_id: int | None, username: str | None) -> bool:
        if user_id:
            stmt = select(AdminUser).where(AdminUser.telegram_id == user_id)
            res = await self.session.execute(stmt)
            if res.scalar_one_or_none():
                return True

        # One-time bootstrap by username; bind the immutable Telegram ID immediately.
        if username and user_id:
            clean_username = username.lower().lstrip("@")
            stmt = select(AdminUser).where(
                AdminUser.username == clean_username,
                AdminUser.telegram_id.is_(None),
            )
            res = await self.session.execute(stmt)
            admin = res.scalar_one_or_none()
            if admin:
                admin.telegram_id = user_id
                await self.session.commit()
                return True

        return False

    async def get_user_role(self, user_id: int | None) -> str | None:
        if not user_id:
            return None
        return await self.session.scalar(select(AdminUser.role).where(AdminUser.telegram_id == user_id))

    async def is_allowed_chat(self, chat_id: int) -> bool:
        stmt = select(AllowedChat).where(AllowedChat.chat_id == chat_id)
        res = await self.session.execute(stmt)
        return res.scalar_one_or_none() is not None
