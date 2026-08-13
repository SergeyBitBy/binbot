import asyncio
import logging
from datetime import datetime, timedelta, timezone

from sqlalchemy import select

from app.config.settings import settings
from app.db.database import AsyncSessionLocal
from app.db.models import Advertisement, ScanHistory, SystemSetting
from app.db.repositories.merchant_repo import MerchantRepository
from app.db.repositories.profile_repo import ProfileRepository
from app.providers.binance.models import BinanceSearchItem
from app.providers.binance.provider import BinanceP2PProvider
from app.services.notification_service import NotificationService
from app.services.sheets_service import GoogleSheetsService

logger = logging.getLogger(__name__)


class MonitoringService:
    def __init__(self, provider=None, notification_service=None, sheets_service=None):
        self.provider = provider or BinanceP2PProvider()
        self.notification_service = notification_service or NotificationService()
        self.sheets_service = sheets_service or GoogleSheetsService()
        self._semaphore = asyncio.Semaphore(settings.monitoring_max_concurrency)
        self._detail_semaphore = asyncio.Semaphore(settings.binance_detail_concurrency)
        self._persistence_lock = asyncio.Lock()
        self._tasks: set[asyncio.Task] = set()
        self._accepting = True

    @staticmethod
    async def is_global_monitoring_enabled() -> bool:
        async with AsyncSessionLocal() as session:
            setting = await session.scalar(
                select(SystemSetting).where(SystemSetting.key == "global_monitoring_enabled")
            )
            return setting is None or setting.value.lower() == "true"

    async def _enrich_item(self, item: BinanceSearchItem) -> tuple[BinanceSearchItem, bool, datetime | None]:
        async with AsyncSessionLocal() as session:
            cached = (await session.execute(
                select(Advertisement.detail_checked_at, Advertisement.remarks, Advertisement.auto_reply)
                .where(Advertisement.adv_no == item.adv.advNo)
            )).one_or_none()
            checked_at = cached[0] if cached else None
        now = datetime.now(timezone.utc)
        if checked_at is not None:
            if checked_at.tzinfo is None:
                checked_at = checked_at.replace(tzinfo=timezone.utc)
            if checked_at > now - timedelta(minutes=settings.binance_detail_refresh_minutes):
                item.adv.remarks = cached[1] or item.adv.remarks
                item.adv.autoReplyMsg = cached[2] or item.adv.autoReplyMsg
                return item, True, None

        async with self._detail_semaphore:
            result = await self.provider.client.get_adv_detail(item.adv.advNo)
        if result.success:
            detail = result.data or {}
            adv = detail.get("adv", {})
            item.adv.remarks = adv.get("remarks") or item.adv.remarks
            item.adv.autoReplyMsg = adv.get("autoReplyMsg") or item.adv.autoReplyMsg
            return item, True, now
        logger.warning("Detail fetch failed for advNo=%s: %s", item.adv.advNo, result.error)
        return item, False, None

    @staticmethod
    def _payload(profile, merchant, contacts, item) -> dict:
        pay_methods = []
        for method in item.adv.payMethods or []:
            if isinstance(method, dict):
                name = method.get("tradeMethodName") or method.get("payTypeStr") or method.get("identifier") or method.get("payType")
                if name:
                    pay_methods.append(name)
        return {
            "profile_name": profile.name,
            "nickname": merchant.nickname,
            "user_no": merchant.user_no,
            "asset": profile.asset,
            "fiat": profile.fiat,
            "price": str(item.adv.price),
            "min_amount": str(item.adv.minSingleTransAmount or ""),
            "max_amount": str(item.adv.maxSingleTransAmount or ""),
            "pay_methods": pay_methods,
            "remarks": item.adv.remarks,
            "auto_reply": item.adv.autoReplyMsg,
            "contacts": [{"type": c.type, "value": c.value} for c in contacts],
        }

    async def scan_profile(self, profile_id: int, *, trigger: str = "scheduled", force: bool = False):
        if not self._accepting or not await self.is_global_monitoring_enabled():
            return None
        async with self._semaphore:
            async with AsyncSessionLocal() as session:
                profile = await ProfileRepository(session).claim_for_scan(
                    profile_id,
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
                result = await self.provider.fetch_all_pages(
                    asset=profile.asset,
                    fiat=profile.fiat,
                    trade_type=profile.trade_type,
                    pay_types=profile.pay_types,
                    trans_amount=profile.trans_amount,
                    merchant_check=profile.merchant_check,
                    rows_per_page=20,
                )
                history.expected_ads = result.expected_total
                history.pages_fetched = result.pages_fetched
                history.total_ads_found = len(result.items)

                if not result.items and not result.complete:
                    raise RuntimeError(result.error or "Binance returned no usable data")

                enriched = await asyncio.gather(*(self._enrich_item(item) for item in result.items))
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
                    if result.complete:
                        await repo.deactivate_missing_for_profile(profile.id, started)
                    await session.commit()
                self._persistence_lock.release()
                persistence_acquired = False

                history.unique_merchants_found = len(unique_users)
                if baseline and result.complete:
                    async with AsyncSessionLocal() as session:
                        await ProfileRepository(session).mark_baseline_completed(profile.id)
                if sheet_rows and await self.sheets_service.is_auto_export_enabled():
                    initialized, _ = await self.sheets_service.initialize_with_status()
                    if initialized:
                        await self.sheets_service.sync_merchants_batch(sheet_rows)

                history.status = (
                    "SUCCESS" if result.complete and history.detail_failure_count == 0 else "PARTIAL"
                )
                errors = [message for message in (
                    result.error,
                    f"{history.detail_failure_count} detail requests failed" if history.detail_failure_count else None,
                ) if message]
                history.error_message = "; ".join(errors) or None
            except asyncio.CancelledError:
                history.status = "CANCELLED"
                history.error_message = "Application shutdown"
                raise
            except Exception as exc:
                history.status = "ERROR"
                history.error_message = str(exc)[:500]
                logger.exception("Scan failed for profile id=%s", profile.id)
            finally:
                if persistence_acquired:
                    self._persistence_lock.release()
                history.finished_at = datetime.now(timezone.utc)
                history.duration_ms = int((history.finished_at - started).total_seconds() * 1000)
                async with AsyncSessionLocal() as session:
                    stored = await session.get(ScanHistory, history.id)
                    for key in (
                        "finished_at", "status", "error_message", "expected_ads", "pages_fetched",
                        "total_ads_found", "unique_merchants_found", "new_merchants_count",
                        "new_contacts_count", "detail_success_count", "detail_failure_count", "duration_ms",
                    ):
                        setattr(stored, key, getattr(history, key))
                    await session.commit()
                async with AsyncSessionLocal() as session:
                    await ProfileRepository(session).release_after_scan(profile.id, profile.scan_interval_seconds)
                logger.info(
                    "Scan finished profile=%s status=%s ads=%s/%s pages=%s duration_ms=%s",
                    profile.name, history.status, history.total_ads_found, history.expected_ads,
                    history.pages_fetched, history.duration_ms,
                )
            return history

    def submit_scan(self, profile_id: int, *, trigger: str = "manual", force: bool = True) -> asyncio.Task | None:
        if not self._accepting:
            return None
        task = asyncio.create_task(self.scan_profile(profile_id, trigger=trigger, force=force))
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)
        return task

    async def dispatch_due_profiles(self) -> None:
        if not self._accepting or not await self.is_global_monitoring_enabled():
            return
        async with AsyncSessionLocal() as session:
            profiles = await ProfileRepository(session).get_due()
        for profile in profiles:
            self.submit_scan(profile.id, trigger="scheduled", force=False)

    async def scan_all_active_profiles(self, *, trigger: str = "manual", force: bool = True) -> None:
        async with AsyncSessionLocal() as session:
            profiles = await ProfileRepository(session).get_all(only_active=True)
        tasks = [self.submit_scan(profile.id, trigger=trigger, force=force) for profile in profiles]
        await asyncio.gather(*(task for task in tasks if task), return_exceptions=True)

    async def stop(self, timeout: float = 20) -> None:
        self._accepting = False
        if not self._tasks:
            return
        _, pending = await asyncio.wait(self._tasks, timeout=timeout)
        for task in pending:
            task.cancel()
        if pending:
            await asyncio.gather(*pending, return_exceptions=True)
