import asyncio
import logging
from datetime import datetime
from typing import List, Optional
from aiogram import Bot
from sqlalchemy import select

from app.db.database import AsyncSessionLocal
from app.db.models import AllowedChat, Contact, Merchant, SystemSetting

logger = logging.getLogger(__name__)

class NotificationService:
    def __init__(self, bot: Optional[Bot] = None):
        self.bot = bot

    def set_bot(self, bot: Bot):
        self.bot = bot

    async def is_quiet_hours(() -> bool:
        async with AsyncSessionLocal() as session:
            res_enabled = await session.execute(select(SystemSetting).where(SystemSetting.key == "quiet_hours_enabled"))
            setting_enabled = res_enabled.scalar_one_or_none()
            if not setting_enabled or setting_enabled.value.lower() != "true":
                return False

            res_start = await session.execute(select(SystemSetting).where(SystemSetting.key == "quiet_hours_start"))
            res_end = await session.execute(select(SystemSetting).where(SystemSetting.key == "quiet_hours_end"))
            st_val = res_start.scalar_one_or_none()
            end_val = res_end.scalar_one_or_none()
            
            if not st_val or not end_val:
                return False

            try:
                now_time = datetime.now().time()
                start_time = datetime.strptime(st_val.value, "%H:%M").time()
                end_time = datetime.strptime(end_val.value, "%H:%M").time()

                if start_time <= end_time:
                    return start_time <= now_time <= end_time
                else:
                    return now_time >= start_time or now_time <= end_time
            except Exception as e:
                logger.error(f"Error checking quiet hours: {e}")
                return False

    async def get_target_chat_ids(self) -> List[int]:
        async with AsyncSessionLocal() as session:
            res = await session.execute(select(AllowedChat.chat_id))
            return list(res.scalars().all())

    async def notify_new_merchant(
        self,
        merchant: Merchant,
        contacts: List[Contact],
        profile_name: str,
        asset: str,
        fiat: str,
        price: str,
    ):
        if not self.bot:
            logger.warning("NotificationService bot instance not attached.")
            return

        if await NotificationService.is_quiet_hours():
            logger.info("Quiet hours active. Suppressing Telegram notification.")
            return

        chats = await self.get_target_chat_ids()
        if not chats:
            logger.warning("No allowed chats found for notifications.")
            return

        profile_link = f"https://p2p.binance.com/advertiserDetail?advertiserNo={merchant.user_no}"
        
        contacts_str = "\n".join([f"• <b>{c.type.upper()}</b>: <code>{c.value}</code>" for c in contacts])
        if not contacts_str:
            contacts_str = "<i>Контакты не обнаружены в описании</i>"

        text = (
            f"🚨 <b>НОВЫЙ МЕРЧАНТ НА ВЫЧИСЛЕНИИ!</b>\n\n"
            f"👤 <b>Никнейм:</b> <a href='{profile_link}'>{merchant.nickname or 'Без ника'}</a>\n"
            f"🆔 <b>UserNo:</b> <code>{merchant.user_no}</code>\n"
            f"📊 <b>Профиль мониторинга:</b> {profile_name}\n"
            f"💰 <b>Пара:</b> {asset}/{fiat} | <b>Цена:</b> <code>{price}</code>\n"
            f"📈 <b>Ордеров/Месяц:</b> {merchant.month_order_count} ({merchant.month_finish_rate * 100:.1f}%)\n\n"
            f"📞 <b>Найденные контакты:</b>\n{contacts_str}\n"
        )
        if merchant.remarks:
            clean_remarks = merchant.remarks[:300].replace("<", "&lt;").replace(">", "&gt;")
            text += f"\n📝 <b>Заметки/Правила:</b>\n<i>{clean_remarks}</i>\n"

        for chat_id in chats:
            try:
                await self.bot.send_message(chat_id=chat_id, text=text, parse_mode="HTML", disable_web_page_preview=True)
                await asyncio.sleep(0.1)
            except Exception as e:
                logger.error(f"Failed to send notification to chat {chat_id}: {e}")

    async def notify_new_contacts(
        self,
        merchant: Merchant,
        new_contacts: List[Contact],
        profile_name: str,
    ):
        if not self.bot or not new_contacts:
            return

        if await NotificationService.is_quiet_hours():
            return

        chats = await self.get_target_chat_ids()
        if not chats:
            return

        profile_link = f"https://p2p.binance.com/advertiserDetail?advertiserNo={merchant.user_no}"
        contacts_str = "\n".join([f"• <b>{c.type.upper()}</b>: <code>{c.value}</code>" for c in new_contacts])

        text = (
            f"📞 <b>ОБНОВЛЕНИЕ КОНТАКТОВ МЕРЧАНТА!</b>\n\n"
            f"👤 <b>Мерчант:</b> <a href='{profile_link}'>{merchant.nickname}</a>\n"
            f"🆔 <b>UserNo:</b> <code>{merchant.user_no}</code>\n"
            f"📊 <b>Профиль:</b> {profile_name}\n\n"
            f"➕ <b>Новые контакты:</b>\n{contacts_str}\n"
        )

        for chat_id in chats:
            try:
                await self.bot.send_message(chat_id=chat_id, text=text, parse_mode="HTML", disable_web_page_preview=True)
                await asyncio.sleep(0.1)
            except Exception as e:
                logger.error(f"Failed to send contact update to chat {chat_id}: {e}")
