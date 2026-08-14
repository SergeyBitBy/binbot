import asyncio
import logging
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import selectinload

from app.config.settings import settings
from app.db.database import AsyncSessionLocal
from app.db.models import (
    Contact,
    Merchant,
    MonitoringProfile,
    ScanHistory,
    SystemSetting,
)
from app.db.repositories.merchant_repo import MerchantRepository
from app.db.repositories.profile_repo import ProfileRepository
from app.providers.binance.models import BinanceSearchItem
from app.providers.binance.provider import BinanceP2PProvider
from app.services.notification_service import NotificationService
from app.services.sheets_service import GoogleSheetsService

logger = logging.getLogger(__name__)

class MonitoringService:
    def __init__(
        self,
        provider: BinanceP2PProvider | None = None,
        sheets_service: GoogleSheetsService | None = None,
    ):
        self.provider = provider or BinanceP2PProvider()
        self.sheets_service = sheets_service or GoogleSheetsService()
        self._dispatch_task: asyncio.Task | None = None
        self._notification_task: asyncio.Task | None = None
        self._running = False
        self._semaphore = asyncio.Semaphore(settings.monitoring_max_concurrency)
        self._persistence_lock = asyncio.Lock()

    async def start(self) -> None:
        if self._running:
            return
        self._running = True
        self._dispatch_task = asyncio.create_task(self._dispatch_loop(), name="monitoring-dispatch-loop")
        self._notification_task = asyncio.create_task(
            self._notification_worker_loop(), name="monitoring-notification-worker-loop"
        )
        logger.info("MonitoringService started background dispatch loop and notification worker loop")

    async def stop(self) -> None:
        if not self._running:
            return
        self._running = False
        for task in (self._dispatch_task, self._notification_task):
            if task:
                task.cancel()
                try:
                    await task
                except asyncio.CancelledError:
                    pass
        logger.info("MonitoringService stopped background workers")

    async def _is_global_monitoring_enabled(self) -> bool:
        async with AsyncSessionLocal() as session:
            res = await session.execute(
                select(SystemSetting).where(SystemSetting.key == "global_monitoring_enabled")
            )
            setting = res.scalar_one_or_none()
            if setting and setting.value:
                return setting.value.lower() == "true"
        return True

    async def _get_setting(self, key: str, default: str = "") -> str:
        async with AsyncSessionLocal() as session:
            res = await session.execute(
                select(SystemSetting).where(SystemSetting.key == key)
            )
            s = res.scalar_one_or_none()
            return s.value if s and s.value else default

    async def _dispatch_loop(self) -> None:
        while self._running:
            try:
                if await self._is_global_monitoring_enabled():
                    async with AsyncSessionLocal() as session:
                        repo = ProfileRepository(session)
                        due_profiles = await repo.get_due()
                    for profile in due_profiles:
                        asyncio.create_task(self._run_profile_scan(profile.id, trigger="AUTO"))
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.exception("Error in profile dispatch loop: %s", e)
            await asyncio.sleep(settings.monitoring_dispatch_interval_seconds)

    async def _notification_worker_loop(self) -> None:
        while self._running:
            try:
                async with AsyncSessionLocal() as session:
                    await NotificationService.process_pending(session)
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.exception("Error in notification worker loop: %s", e)
            await asyncio.sleep(settings.notification_worker_interval_seconds)

    async def _run_profile_scan(self, profile_id: int, trigger: str = "AUTO") -> None:
        async with self._semaphore:
            try:
                await self.scan_profile(profile_id, trigger=trigger)
            except asyncio.CancelledError:
                raise
            except Exception as e:
                logger.exception("Failed scan task for profile id=%s: %s", profile_id, e)

    async def _enrich_item(self, item: BinanceSearchItem) -> tuple[BinanceSearchItem, bool, datetime]:
        checked_at = datetime.now(timezone.utc)
        if item.adv.remarks and item.adv.autoReplyMsg:
            return item, True, checked_at
        user_no = item.advertiser.userNo
        detail = None
        if hasattr(self.provider, "fetch_advertiser_detail"):
            detail = await self.provider.fetch_advertiser_detail(user_no)
        elif hasattr(self.provider, "client") and hasattr(self.provider.client, "get_adv_detail"):
            res = await self.provider.client.get_adv_detail(item.adv.advNo)
            if res and getattr(res, "success", False) and getattr(res, "data", None):
                adv = res.data.get("adv", {})
                item.adv.remarks = item.adv.remarks or adv.get("remarks")
                item.adv.autoReplyMsg = item.adv.autoReplyMsg or adv.get("autoReplyMsg")
                return item, True, checked_at
        if not detail:
            return item, False, checked_at
        item.adv.remarks = item.adv.remarks or detail.remarks
        item.adv.autoReplyMsg = item.adv.autoReplyMsg or detail.auto_reply_msg
        return item, True, checked_at

    async def scan_profile(
        self,
        profile_id: int,
        trigger: str = "AUTO",
        force: bool = False,
    ) -> ScanHistory | None:
        profile: MonitoringProfile | None = None
        async with AsyncSessionLocal() as session:
            repo = ProfileRepository(session)
            profile = await repo.claim_for_scan(
                profile_id=profile_id,
                force=force,
                lease_seconds=settings.monitoring_lease_seconds,
            )
        if profile is None:
            logger.info("Profile id=%s is not due or is already leased", profile_id)
            return None

        started = datetime.now(timezone.utc)
        history = ScanHistory(profile_id=profile.id, started_at=started, status="RUNNING", trigger=trigger)
        persistence_acquired = False
        async with AsyncSessionLocal() as session:
            session.add(history)
            await session.commit()
            await session.refresh(history)

        try:
            # Handle BUY, SELL, or ALL (BUY + SELL) trade types
            trade_types = ["BUY", "SELL"] if profile.trade_type in ("ALL", "BOTH", "BUY_SELL") else [profile.trade_type]
            
            all_items: list[BinanceSearchItem] = []
            total_expected = 0
            total_pages = 0
            is_complete = True
            last_error = None

            for tt in trade_types:
                res = await self.provider.fetch_all_pages(
                    asset=profile.asset,
                    fiat=profile.fiat,
                    trade_type=tt,
                    pay_types=profile.pay_types,
                    trans_amount=profile.trans_amount,
                    merchant_check=profile.merchant_check,
                    rows_per_page=20,
                )
                all_items.extend(res.items)
                total_expected += res.expected_total
                total_pages += res.pages_fetched
                if not res.complete:
                    is_complete = False
                    last_error = res.error

            history.expected_ads = total_expected
            history.pages_fetched = total_pages
            history.total_ads_found = len(all_items)

            if not all_items and not is_complete:
                raise RuntimeError(last_error or "Binance returned no usable data")

            enriched = await asyncio.gather(*(self._enrich_item(item) for item in all_items))
            history.detail_success_count = sum(ok for _, ok, _ in enriched)
            history.detail_failure_count = len(enriched) - history.detail_success_count

            baseline = not profile.is_baseline_completed
            unique_users: set[str] = set()
            sheet_rows = []
            await self._persistence_lock.acquire()
            persistence_acquired = True
            async with AsyncSessionLocal() as session:
                repo = MerchantRepository(session)
                for item, _, checked_at in enriched:
                    unique_users.add(item.advertiser.userNo)
                    merchant, _, contacts, _, ad = await repo.process_binance_item(item, checked_at)
                    new_profile_merchant, _ = await repo.observe_for_profile(
                        profile.id, merchant.id, ad.id, started
                    )
                    if profile.merchant_check and (not merchant.user_type or "merchant" not in merchant.user_type.lower()):
                        continue
                    if new_profile_merchant:
                        history.new_merchants_count += 1
                        sheet_rows.append((merchant, contacts))
                    if contacts:
                        history.new_contacts_count += len(contacts)
                    if not baseline:
                        payload = self._payload(profile, merchant, contacts, item)
                        if new_profile_merchant:
                            await NotificationService.enqueue(
                                session,
                                event_type="NEW_MERCHANT",
                                profile_id=profile.id,
                                merchant_id=merchant.id,
                                payload=payload,
                                deduplication_key=f"new-merchant:{profile.id}:{merchant.id}",
                            )
                        elif contacts:
                            contact_ids = ",".join(sorted(f"{c.type}:{c.value.lower()}" for c in contacts))
                            await NotificationService.enqueue(
                                session,
                                event_type="NEW_CONTACTS",
                                profile_id=profile.id,
                                merchant_id=merchant.id,
                                payload=payload,
                                deduplication_key=f"new-contacts:{profile.id}:{merchant.id}:{contact_ids}",
                            )
                if is_complete:
                    await repo.deactivate_missing_for_profile(profile.id, started)
                await session.commit()
            self._persistence_lock.release()
            persistence_acquired = False

            # Auto-export to profile's dedicated worksheet
            if sheet_rows:
                auto_export = await self._get_setting("google_sheets_auto_export", "false")
                auto_contacts_only = await self._get_setting("google_sheets_auto_contacts_only", "false")
                if auto_export.lower() == "true":
                    export_rows = sheet_rows
                    if auto_contacts_only.lower() == "true":
                        export_rows = [(m, c) for m, c in sheet_rows if c]
                    if export_rows:
                        try:
                            if hasattr(self.sheets_service, "append_merchants"):
                                await self.sheets_service.append_merchants(export_rows, profile_name=profile.name)
                            elif hasattr(self.sheets_service, "sync_merchants_batch"):
                                await self.sheets_service.sync_merchants_batch(export_rows)
                        except Exception as e:
                            logger.error(f"Error during auto-export to worksheet '{profile.name}': {e}")

            history.unique_merchants_count = len(unique_users)
            history.status = "SUCCESS"
            history.error_message = None
            logger.info(
                f"Scan finished profile={profile.name} (type={profile.trade_type}) status=SUCCESS ads={len(all_items)}/{total_expected} pages={total_pages} duration_ms={round((datetime.now(timezone.utc) - started).total_seconds() * 1000)}"
            )
        except Exception as e:
            history.status = "ERROR"
            history.error_message = str(e)
            logger.exception(f"Scan failed for profile id={profile_id}: {e}")
        finally:
            if persistence_acquired:
                self._persistence_lock.release()
            finished = datetime.now(timezone.utc)
            history.finished_at = finished
            history.duration_ms = max(0, int((finished - started).total_seconds() * 1000))
            async with AsyncSessionLocal() as session:
                session.add(history)
                repo = ProfileRepository(session)
                if history.status == "SUCCESS":
                    await repo.mark_baseline_completed(profile.id)
                await repo.release_after_scan(profile.id, interval_seconds=profile.scan_interval_seconds)
            return history

    def _payload(
        self,
        profile: MonitoringProfile,
        merchant: Merchant,
        contacts: list[Contact],
        item: BinanceSearchItem,
    ) -> dict[str, Any]:
        return {
            "profile_name": profile.name,
            "profile_id": profile.id,
            "asset": profile.asset,
            "fiat": profile.fiat,
            "merchant_id": merchant.id,
            "user_no": merchant.user_no,
            "nickname": merchant.nickname,
            "user_type": merchant.user_type,
            "month_order_count": merchant.month_order_count,
            "month_finish_rate": merchant.month_finish_rate,
            "positive_rate": merchant.positive_rate,
            "price": str(item.adv.price),
            "min_amount": str(item.adv.minSingleTransAmount or ""),
            "max_amount": str(item.adv.maxSingleTransAmount or ""),
            "pay_methods": [
                p.get("tradeMethodName") or p.get("payTypeStr") or p.get("identifier") or p.get("payType")
                for p in (item.adv.payMethods or [])
            ],
            "remarks": item.adv.remarks,
            "auto_reply_msg": item.adv.autoReplyMsg,
            "contacts": [{"type": c.type, "value": c.value, "raw_match": c.raw_match} for c in (contacts or [])],
        }
