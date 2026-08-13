import asyncio
import html
import logging
from datetime import datetime, time, timedelta, timezone
from typing import Any
from zoneinfo import ZoneInfo

from aiogram import Bot
from aiogram.exceptions import TelegramRetryAfter
from sqlalchemy import select

from app.config.settings import settings
from app.db.database import AsyncSessionLocal
from app.db.models import (
    AllowedChat,
    NotificationDelivery,
    NotificationOutbox,
    SystemSetting,
)

logger = logging.getLogger(__name__)


class NotificationService:
    def __init__(self, bot: Bot | None = None):
        self.bot = bot
        self._worker_task: asyncio.Task | None = None
        self._stop = asyncio.Event()

    def set_bot(self, bot: Bot) -> None:
        self.bot = bot

    @staticmethod
    async def is_quiet_hours() -> bool:
        async with AsyncSessionLocal() as session:
            values = dict((await session.execute(
                select(SystemSetting.key, SystemSetting.value).where(
                    SystemSetting.key.in_(["quiet_hours_enabled", "quiet_hours_start", "quiet_hours_end"])
                )
            )).all())
        if values.get("quiet_hours_enabled", "false").lower() != "true":
            return False
        try:
            now_time = datetime.now(ZoneInfo(settings.timezone)).time()
            start = time.fromisoformat(values["quiet_hours_start"])
            end = time.fromisoformat(values["quiet_hours_end"])
            return start <= now_time <= end if start <= end else now_time >= start or now_time <= end
        except (KeyError, ValueError) as exc:
            logger.error("Invalid quiet-hours configuration: %s", exc)
            return False

    @staticmethod
    async def enqueue(
        session,
        *,
        event_type: str,
        profile_id: int,
        merchant_id: int,
        payload: dict[str, Any],
        deduplication_key: str,
    ) -> None:
        existing = await session.scalar(
            select(NotificationOutbox.id).where(NotificationOutbox.deduplication_key == deduplication_key)
        )
        if existing:
            return
        now = datetime.now(timezone.utc)
        event = NotificationOutbox(
            event_type=event_type,
            profile_id=profile_id,
            merchant_id=merchant_id,
            payload=payload,
            deduplication_key=deduplication_key,
            status="PENDING",
            next_attempt_at=now,
            created_at=now,
        )
        session.add(event)
        await session.flush()
        chat_ids = list((await session.execute(select(AllowedChat.chat_id))).scalars())
        for chat_id in chat_ids:
            session.add(NotificationDelivery(
                outbox_id=event.id,
                chat_id=chat_id,
                status="PENDING",
                next_attempt_at=now,
            ))

    @staticmethod
    def _format(payload: dict[str, Any], event_type: str) -> str:
        esc = lambda value: html.escape(str(value or ""), quote=True)
        link = f"https://p2p.binance.com/advertiserDetail?advertiserNo={esc(payload.get('user_no'))}"
        contacts = payload.get("contacts") or []
        contacts_text = "\n".join(
            f"• <b>{esc(c.get('type', '')).upper()}</b>: <code>{esc(c.get('value'))}</code>" for c in contacts
        ) or "<i>Контакты не указаны</i>"
        if event_type == "NEW_CONTACTS":
            return (
                "📞 <b>НОВЫЕ КОНТАКТЫ МЕРЧАНТА</b>\n\n"
                f"👤 <a href='{link}'>{esc(payload.get('nickname') or 'Без ника')}</a>\n"
                f"🆔 <code>{esc(payload.get('user_no'))}</code>\n"
                f"📊 Профиль: {esc(payload.get('profile_name'))}\n\n{contacts_text}"
            )
        pay = ", ".join(esc(x) for x in payload.get("pay_methods") or []) or "Все способы оплаты"
        text = (
            "🚨 <b>НОВЫЙ МЕРЧАНТ</b>\n\n"
            f"👤 <a href='{link}'>{esc(payload.get('nickname') or 'Без ника')}</a>\n"
            f"🆔 <code>{esc(payload.get('user_no'))}</code>\n"
            f"📊 Профиль: {esc(payload.get('profile_name'))}\n"
            f"💰 {esc(payload.get('asset'))}/{esc(payload.get('fiat'))}: <code>{esc(payload.get('price'))}</code>\n"
            f"💳 {pay}\n\n📞 <b>Контакты:</b>\n{contacts_text}"
        )
        remarks = str(payload.get("remarks") or "").strip()
        reply = str(payload.get("auto_reply") or "").strip()
        if remarks:
            text += f"\n\n📝 <b>Описание:</b>\n<i>{esc(remarks[:600])}</i>"
        if reply:
            text += f"\n\n💬 <b>Автоответ:</b>\n<i>{esc(reply[:300])}</i>"
        return text

    async def process_pending(self) -> None:
        if not self.bot or await self.is_quiet_hours():
            return
        now = datetime.now(timezone.utc)
        async with AsyncSessionLocal() as session:
            chat_ids = list((await session.execute(select(AllowedChat.chat_id))).scalars())
            open_events = list((await session.execute(
                select(NotificationOutbox.id).where(NotificationOutbox.status.in_(["PENDING", "RETRY"]))
            )).scalars())
            existing_pairs = set((await session.execute(
                select(NotificationDelivery.outbox_id, NotificationDelivery.chat_id).where(
                    NotificationDelivery.outbox_id.in_(open_events or [-1])
                )
            )).all())
            for event_id in open_events:
                for chat_id in chat_ids:
                    if (event_id, chat_id) not in existing_pairs:
                        session.add(NotificationDelivery(
                            outbox_id=event_id,
                            chat_id=chat_id,
                            status="PENDING",
                            next_attempt_at=now,
                        ))
            await session.flush()
            deliveries = list((await session.execute(
                select(NotificationDelivery, NotificationOutbox)
                .join(NotificationOutbox, NotificationOutbox.id == NotificationDelivery.outbox_id)
                .where(
                    NotificationDelivery.status.in_(["PENDING", "RETRY"]),
                    NotificationDelivery.next_attempt_at <= now,
                    NotificationOutbox.status != "DEAD",
                )
                .order_by(NotificationDelivery.id)
                .limit(50)
            )).all())

            for delivery, event in deliveries:
                try:
                    await self.bot.send_message(
                        chat_id=delivery.chat_id,
                        text=self._format(event.payload, event.event_type),
                        parse_mode="HTML",
                        disable_web_page_preview=True,
                    )
                    delivery.status = "SENT"
                    delivery.sent_at = now
                except asyncio.CancelledError:
                    raise
                except TelegramRetryAfter as exc:
                    delivery.status = "RETRY"
                    delivery.attempts += 1
                    delivery.next_attempt_at = now + timedelta(seconds=exc.retry_after + 1)
                    delivery.last_error = str(exc)[:500]
                except Exception as exc:
                    delivery.attempts += 1
                    delivery.last_error = str(exc)[:500]
                    if delivery.attempts >= settings.notification_max_attempts:
                        delivery.status = "DEAD"
                    else:
                        delivery.status = "RETRY"
                        delay = min(3600, 2 ** min(delivery.attempts, 10))
                        delivery.next_attempt_at = now + timedelta(seconds=delay)
                    logger.warning("Notification delivery %s failed: %s", delivery.id, exc)

            touched = {event.id for _, event in deliveries}
            # The session has autoflush disabled; persist delivery state before
            # aggregating it into the parent outbox event.
            await session.flush()
            for event_id in touched:
                event = await session.get(NotificationOutbox, event_id)
                states = list((await session.execute(
                    select(NotificationDelivery.status).where(NotificationDelivery.outbox_id == event_id)
                )).scalars())
                if states and all(state == "SENT" for state in states):
                    event.status = "SENT"
                    event.sent_at = now
                elif states and all(state in ("SENT", "DEAD") for state in states):
                    event.status = "DEAD"
                else:
                    event.status = "RETRY"
            await session.commit()

    async def run_worker(self) -> None:
        self._stop.clear()
        while not self._stop.is_set():
            try:
                await self.process_pending()
            except asyncio.CancelledError:
                break
            except Exception:
                logger.exception("Unhandled notification worker error")
            try:
                await asyncio.wait_for(self._stop.wait(), timeout=settings.notification_worker_interval_seconds)
            except asyncio.TimeoutError:
                pass

    def start(self) -> None:
        if not self._worker_task or self._worker_task.done():
            self._worker_task = asyncio.create_task(self.run_worker(), name="notification-worker")

    async def stop(self) -> None:
        self._stop.set()
        if self._worker_task:
            await self._worker_task
