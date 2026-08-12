import asyncio
import logging
from datetime import datetime, timezone
from typing import Optional
from sqlalchemy import select

from app.db.database import AsyncSessionLocal
from app.db.models import MonitoringProfile, ScanHistory, SystemSetting
from app.db.repositories.merchant_repo import MerchantRepository
from app.db.repositories.profile_repo import ProfileRepository
from app.providers.binance.provider import BinanceP2PProvider
from app.services.notification_service import NotificationService
from app.services.sheets_service import GoogleSheetsService

logger = logging.getLogger(__name__)

class MonitoringService:
    def __init__(
        self,
        provider: Optional[BinanceP2PProvider] = None,
        notification_service: Optional[NotificationService] = None,
        sheets_service: Optional[GoogleSheetsService] = None,
    ):
        self.provider = provider or BinanceP2PProvider()
        self.notification_service = notification_service or NotificationService()
        self.sheets_service = sheets_service or GoogleSheetsService()

    @staticmethod
    async def is_global_monitoring_enabled() -> bool:
        async with AsyncSessionLocal() as session:
            res = await session.execute(
                select(SystemSetting).where(SystemSetting.key == "global_monitoring_enabled")
            )
            setting = res.scalar_one_or_none()
            return setting is None or setting.value.lower() == "true"

    async def scan_profile(self, profile_id: int) -> Optional[ScanHistory]:
        if not await self.is_global_monitoring_enabled():
            logger.info("Global monitoring is currently DISABLED. Skipping scan.")
            return None

        async with AsyncSessionLocal() as session:
            profile_repo = ProfileRepository(session)
            profile = await profile_repo.get_by_id(profile_id)
            
            if not profile or not profile.is_active:
                logger.info(f"Profile ID {profile_id} is inactive or missing. Skipping scan.")
                return None

            if profile.is_locked:
                logger.warning(f"Profile '{profile.name}' is already locked. Concurrent scan skipped.")
                return None

            await profile_repo.update_lock(profile_id, True)

        started_at = datetime.now(timezone.utc)
        scan_record = ScanHistory(
            profile_id=profile_id,
            started_at=started_at,
            status="RUNNING",
        )

        total_ads = 0
        unique_merchants = 0
        new_merchants_count = 0
        new_contacts_count = 0

        # Accumulate newly discovered merchants for batch sync at end of scan
        merchants_to_sheet_sync = []
        auto_contacts_only = await self.sheets_service.is_auto_contacts_only_enabled()

        try:
            logger.info(f"Starting scan for profile '{profile.name}' ({profile.asset}/{profile.fiat} {profile.trade_type})...")
            
            items = await self.provider.fetch_all_pages(
                asset=profile.asset,
                fiat=profile.fiat,
                trade_type=profile.trade_type,
                pay_types=profile.pay_types,
                trans_amount=profile.trans_amount,
                merchant_check=profile.merchant_check,
                max_pages=5,
                rows_per_page=20,
            )

            total_ads = len(items)
            is_baseline_run = not profile.is_baseline_completed

            async with AsyncSessionLocal() as session:
                merchant_repo = MerchantRepository(session)
                processed_user_nos = set()

                for item in items:
                    user_no = item.advertiser.userNo
                    if user_no not in processed_user_nos:
                        processed_user_nos.add(user_no)

                    # Enrich ad with full description (remarks & autoReplyMsg) via GET detail endpoint
                    try:
                        detail_data = await self.provider.client.get_adv_detail(item.adv.advNo)
                        if detail_data and "adv" in detail_data:
                            enriched_adv = detail_data["adv"]
                            item.adv.remarks = enriched_adv.get("remarks") or item.adv.remarks
                            item.adv.autoReplyMsg = enriched_adv.get("autoReplyMsg") or item.adv.autoReplyMsg
                    except asyncio.CancelledError:
                        raise
                    except Exception as de:
                        logger.warning(f"Failed to enrich ad {item.adv.advNo}: {de}")

                    merchant, is_new_m, new_c, is_new_ad = await merchant_repo.process_binance_item(item)

                    if is_new_m:
                        new_merchants_count += 1
                    if new_c:
                        new_contacts_count += len(new_c)

                    if is_new_m or new_c:
                        if not auto_contacts_only or bool(new_c):
                            merchants_to_sheet_sync.append((merchant, new_c or []))

                    # Extract payment method names safely from payMethods
                    pay_method_names = []
                    if getattr(item.adv, "payMethods", None):
                        for pm in item.adv.payMethods:
                            if isinstance(pm, dict):
                                name = pm.get("tradeMethodName") or pm.get("identifier") or pm.get("payType")
                                if name:
                                    pay_method_names.append(name)
                            elif isinstance(pm, str):
                                pay_method_names.append(pm)

                    # Dispatch notifications ONLY IF baseline is already completed
                    if not is_baseline_run:
                        if is_new_m:
                            await self.notification_service.notify_new_merchant(
                                merchant=merchant,
                                contacts=new_c,
                                profile_name=profile.name,
                                asset=profile.asset,
                                fiat=profile.fiat,
                                price=str(item.adv.price),
                                remarks=item.adv.remarks,
                                auto_reply=item.adv.autoReplyMsg,
                                min_amount=str(item.adv.minSingleTransAmount) if item.adv.minSingleTransAmount else None,
                                max_amount=str(item.adv.maxSingleTransAmount) if item.adv.maxSingleTransAmount else None,
                                pay_methods=pay_method_names,
                            )
                        elif new_c:
                            await self.notification_service.notify_new_contacts(
                                merchant=merchant,
                                new_contacts=new_c,
                                profile_name=profile.name,
                            )

                await session.commit()

            # Batch sync ONLY NEW merchants to Google Sheets once at end of scan
            if merchants_to_sheet_sync and await self.sheets_service.is_auto_export_enabled():
                sheets_init, _ = await self.sheets_service.initialize_with_status()
                if sheets_init:
                    await self.sheets_service.sync_merchants_batch(merchants_to_sheet_sync)

            unique_merchants = len(processed_user_nos)

            # Mark baseline complete after first scan
            async with AsyncSessionLocal() as session:
                profile_repo = ProfileRepository(session)
                if is_baseline_run:
                    await profile_repo.mark_baseline_completed(profile_id)
                    logger.info(f"Baseline initial scan completed for profile '{profile.name}'. (Suppressed {new_merchants_count} baseline alerts).")

            scan_record.status = "SUCCESS"
            scan_record.finished_at = datetime.now(timezone.utc)
            scan_record.total_ads_found = total_ads
            scan_record.unique_merchants_found = unique_merchants
            scan_record.new_merchants_count = new_merchants_count
            scan_record.new_contacts_count = new_contacts_count

        except asyncio.CancelledError:
            logger.info(f"Scan for profile '{profile.name}' cancelled due to shutdown.")
            scan_record.status = "CANCELLED"
            scan_record.finished_at = datetime.now(timezone.utc)
            raise
        except Exception as e:
            logger.exception(f"Error executing scan for profile '{profile.name}': {e}")
            scan_record.status = "ERROR"
            scan_record.finished_at = datetime.now(timezone.utc)
            scan_record.error_message = str(e)[:500]

        finally:
            async with AsyncSessionLocal() as session:
                profile_repo = ProfileRepository(session)
                await profile_repo.update_lock(profile_id, False)
                if scan_record.status != "CANCELLED":
                    await profile_repo.add_scan_history(scan_record)

        logger.info(
            f"Finished scan for profile '{profile.name}'. Ads: {total_ads}, Merchants: {unique_merchants}, "
            f"New Merchants: {new_merchants_count}, New Contacts: {new_contacts_count}, Status: {scan_record.status}"
        )
        return scan_record

    async def scan_all_active_profiles(self):
        try:
            if not await self.is_global_monitoring_enabled():
                logger.info("Global monitoring is disabled. Skipping scheduled scan.")
                return

            async with AsyncSessionLocal() as session:
                profile_repo = ProfileRepository(session)
                profiles = await profile_repo.get_all(only_active=True)

            for p in profiles:
                await self.scan_profile(p.id)
                await asyncio.sleep(2.0)
        except asyncio.CancelledError:
            logger.info("Monitoring scan task cancelled during bot shutdown.")
