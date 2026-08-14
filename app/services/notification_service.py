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
            f"• <b>{esc(c.get('type', '')).upper()}</b>: <code>{esc(c.get('value'))}</code>"
            for c in contacts
        ) or "<i>Контакты не указаны</i>"
        pay = ", ".join(esc(x) for x in payload.get("pay_methods") or []) or "Не указаны"
        title = "📞 <b>НОВЫЕ КОНТАКТЫ МЕРЧАНТА</b>" if event_type == "NEW_CONTACTS" else "🚨 <b>НОВЫЙ МЕРЧАНТ</b>"
        contacts_title = "Новые контакты" if event_type == "NEW_CONTACTS" else "Контакты"

        minimum = esc(payload.get("min_amount")) or "—"
        maximum = esc(payload.get("max_amount")) or "—"
        fiat = esc(payload.get("fiat"))
        orders = payload.get("month_order_count")
        finish_rate = payload.get("month_finish_rate")
        stats_line = ""
        if orders is not None or finish_rate is not None:
            try:
                rate_text = f"{float(finish_rate) * 100:.1f}%" if finish_rate is not None else "—"
            except (TypeError, ValueError):
                rate_text = esc(finish_rate) or "—"
            stats_line = f"\n📈 <b>Ордеров/Месяц:</b> {esc(orders) or '—'} ({rate_text})"

        text = (
            f"{title}\n\n"
            f"👤 <a href='{link}'>{esc(payload.get('nickname') or 'Без ника')}</a>\n"
            f"🆔 <code>{esc(payload.get('user_no'))}</code>\n"
            f"📊 Профиль: {esc(payload.get('profile_name'))}\n"
            f"💱 <b>Пара:</b> {esc(payload.get('asset'))}/{fiat}\n"
            f"💰 <b>Цена:</b> {esc(payload.get('price'))} | <b>Лимиты:</b> {minimum} - {maximum} {fiat}"
            f"{stats_line}\n"
            f"💳 <b>Оплата:</b> {pay}\n\n📞 <b>{contacts_title}:</b>\n{contacts_text}"
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
        items_to_send = []

        # 1. Synchronize deliveries for new allowed chats & claim pending items atomically
        async with AsyncSessionLocal() as session:
            chat_ids = list((await session.execute(select(AllowedChat.chat_id))).scalars())
            open_events = list((await session.execute(
                select(NotificationOutbox.id).where(NotificationOutbox.status.in_(["PENDING", "RETRY"]))
            )).scalars())

            if open_events and chat_ids:
                existing_pairs = set((await session.execute(
                    select(NotificationDelivery.outbox_id, NotificationDelivery.chat_id).where(
                        NotificationDelivery.outbox_id.in_(open_events)
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
                await session.commit()

            deliveries = list((await session.execute(
                select(NotificationDelivery, NotificationOutbox)
                .join(NotificationOutbox, NotificationOutbox.id == NotificationDelivery.outbox_id)
                .where(
                    NotificationDelivery.status.in_(["PENDING", "RETRY"]),
                    NotificationDelivery.next_attempt_at <= now,
                    NotificationOutbox.status != "DEAD",
                )
                .order_by(NotificationDelivery.id)
                .limit(20)
            )).all())

            for d, ev in deliveries:
                d.status = "SENDING"
                items_to_send.append((d.id, d.chat_id, ev.payload, ev.event_type, ev.id))
            await session.commit()

        if not items_to_send:
            return

        # 2. Send each message independently and commit its status immediately with retry resilience
        touched_events = set()
        for delivery_id, chat_id, payload, event_type, outbox_id in items_to_send:
            touched_events.add(outbox_id)
            send_ok = False
            retry_delay = 30
            err_msg = None

            try:
                await self.bot.send_message(
                    chat_id=chat_id,
                    text=self._format(payload, event_type),
                    parse_mode="HTML",
                    disable_web_page_preview=True,
                )
                send_ok = True
                logger.info(f"Notification delivered to chat_id={chat_id} (outbox_id={outbox_id})")
            except asyncio.CancelledError:
                raise
            except TelegramRetryAfter as exc:
                err_msg = str(exc)[:500]
                retry_delay = exc.retry_after + 1
                logger.warning(f"Telegram rate limited chat_id={chat_id}: retry in {retry_delay}s")
            except Exception as exc:
                err_msg = str(exc)[:500]
                logger.warning(f"Telegram send failed for chat_id={chat_id}: {exc}")

            # Commit delivery result with retry against database locks
            for attempt in range(5):
                try:
                    async with AsyncSessionLocal() as session:
                        d = await session.get(NotificationDelivery, delivery_id)
                        if d:
                            if send_ok:
                                d.status = "SENT"
                                d.sent_at = datetime.now(timezone.utc)
                                d.last_error = None
                            else:
                                d.attempts += 1
                                d.last_error = err_msg
                                if d.attempts >= settings.notification_max_attempts:
                                    d.status = "DEAD"
                                else:
                                    d.status = "RETRY"
                                    delay = max(retry_delay, min(3600, 2 ** min(d.attempts, 10)))
                                    d.next_attempt_at = datetime.now(timezone.utc) + timedelta(seconds=delay)
                            await session.commit()
                        break
                except Exception as e:
                    logger.debug(f"Retry saving delivery status {delivery_id}: {e}")
                    await asyncio.sleep(0.3 * (attempt + 1))

        # 3. Update parent outbox status
        for event_id in touched_events:
            for attempt in range(5):
                try:
                    async with AsyncSessionLocal() as session:
                        event = await session.get(NotificationOutbox, event_id)
                        if not event:
                            break
                        states = list((await session.execute(
                            select(NotificationDelivery.status).where(NotificationDelivery.outbox_id == event_id)
                        )).scalars())
                        if states and all(state == "SENT" for state in states):
                            event.status = "SENT"
                            event.sent_at = datetime.now(timezone.utc)
                        elif states and all(state in ("SENT", "DEAD") for state in states):
                            event.status = "DEAD"
                        elif "SENDING" not in states:
                            event.status = "RETRY"
                        await session.commit()
                        break
                except Exception as e:
                    logger.debug(f"Retry updating outbox status {event_id}: {e}")
                    await asyncio.sleep(0.3 * (attempt + 1))

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
