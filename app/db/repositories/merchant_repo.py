import logging
import re
from datetime import datetime, timezone
from typing import List, Optional, Tuple

from sqlalchemy import delete, exists, func, select, update
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.config.settings import settings
from app.db.models import (
    Advertisement,
    Contact,
    Merchant,
    ProfileAdvertisement,
    ProfileMerchant,
    SystemSetting,
)
from app.providers.binance.models import BinanceSearchItem
from app.services.contact_extractor import ContactExtractor, ExtractedContact
from app.services.groq_extractor import GroqContactExtractor

logger = logging.getLogger(__name__)

def _normalize_contact_key(c_type: str, c_val: str) -> str:
    """Normalize contact type and value for robust deduplication."""
    t = str(c_type).lower().strip()
    v = str(c_val).lower().strip()
    if t in ("phone", "whatsapp", "viber"):
        digits = re.sub(r"\D", "", v)
        v = f"+{digits}"
    elif t == "telegram":
        v = v.lstrip("@").strip()
        v = f"@{v}"
    return f"{t}:{v}"

class MerchantRepository:
    def __init__(self, session: AsyncSession):
        self.session = session
        self._merchants_cache: dict[str, Merchant] = {}
        self._ads_cache: dict[str, Advertisement] = {}
        self._contacts_cache: dict[int, dict[str, Contact]] = {}
        self._profile_merchants_cache: dict[tuple[int, int], ProfileMerchant] = {}
        self._profile_ads_cache: dict[tuple[int, int], ProfileAdvertisement] = {}

    async def _get_setting(self, key: str, default: str = "") -> str:
        res = await self.session.execute(select(SystemSetting).where(SystemSetting.key == key))
        s = res.scalar_one_or_none()
        return s.value if s and s.value else default

    async def get_by_id(self, merchant_id: int) -> Optional[Merchant]:
        stmt = (
            select(Merchant)
            .where(Merchant.id == merchant_id)
            .options(selectinload(Merchant.contacts), selectinload(Merchant.advertisements))
        )
        res = await self.session.execute(stmt)
        return res.scalar_one_or_none()

    async def get_by_user_no(self, user_no: str) -> Optional[Merchant]:
        if user_no in self._merchants_cache:
            return self._merchants_cache[user_no]
        stmt = (
            select(Merchant)
            .where(Merchant.user_no == user_no)
            .options(selectinload(Merchant.contacts), selectinload(Merchant.advertisements))
        )
        res = await self.session.execute(stmt)
        m = res.scalar_one_or_none()
        if m:
            self._merchants_cache[user_no] = m
        return m

    async def process_binance_item(
        self,
        item: BinanceSearchItem,
        checked_at: Optional[datetime] = None,
        extracted_contacts: Optional[List[ExtractedContact]] = None,
    ) -> Tuple[Merchant, bool, List[Contact], bool, Advertisement]:
        """
        Process a single BinanceSearchItem:
        1. Upsert Merchant
        2. Extract & Upsert Contacts
        3. Upsert Advertisement
        """
        detail_checked_at = checked_at or datetime.now(timezone.utc)
        now = detail_checked_at
        advertiser_data = item.advertiser
        adv_data = item.adv

        existing_merchant = await self.get_by_user_no(advertiser_data.userNo)
        is_new_merchant = False
        new_contacts: List[Contact] = []
        is_new_or_updated_ad = False

        if existing_merchant is None:
            is_new_merchant = True
            merchant = Merchant(
                user_no=advertiser_data.userNo,
                user_type=advertiser_data.userType,
                nickname=advertiser_data.nickName,
                month_order_count=advertiser_data.monthOrderCount or 0,
                month_finish_rate=advertiser_data.monthFinishRate or 0.0,
                positive_rate=advertiser_data.positiveRate or 0.0,
                remarks=adv_data.remarks,
                auto_reply_msg=adv_data.autoReplyMsg,
                first_seen_at=now,
                last_seen_at=now,
                is_active=True,
            )
            self.session.add(merchant)
            await self.session.flush()
            self._merchants_cache[advertiser_data.userNo] = merchant
        else:
            merchant = existing_merchant
            merchant.nickname = advertiser_data.nickName or merchant.nickname
            merchant.user_type = advertiser_data.userType or merchant.user_type
            merchant.month_order_count = advertiser_data.monthOrderCount or merchant.month_order_count
            merchant.month_finish_rate = advertiser_data.monthFinishRate or merchant.month_finish_rate
            merchant.positive_rate = advertiser_data.positiveRate or merchant.positive_rate
            merchant.remarks = adv_data.remarks or merchant.remarks
            merchant.auto_reply_msg = adv_data.autoReplyMsg or merchant.auto_reply_msg
            merchant.last_seen_at = now
            merchant.is_active = True

        # Fetch all existing contacts from DB or cache to prevent duplicate insertions
        if merchant.id not in self._contacts_cache:
            c_stmt = select(Contact).where(Contact.merchant_id == merchant.id)
            c_res = await self.session.execute(c_stmt)
            all_existing_contacts = c_res.scalars().all()
            self._contacts_cache[merchant.id] = {
                _normalize_contact_key(c.type, c.value): c for c in all_existing_contacts
            }
        existing_contacts_map = self._contacts_cache[merchant.id]

        # Extract Contacts: use pre-computed if available, otherwise fetch
        extracted: List[ExtractedContact] = []
        if extracted_contacts is not None:
            extracted = extracted_contacts
        else:
            groq_enabled_setting = await self._get_setting("groq_ai_enabled", "true")
            groq_key_setting = await self._get_setting("groq_api_key", settings.groq_api_key)
            groq_model_setting = await self._get_setting("groq_model", settings.groq_model)

            if groq_enabled_setting.lower() == "true" and groq_key_setting:
                groq_extractor = GroqContactExtractor(api_key=groq_key_setting, model=groq_model_setting)
                extracted = await groq_extractor.extract_from_merchant_data(
                    remarks=adv_data.remarks or "",
                    auto_reply=adv_data.autoReplyMsg or "",
                )
            else:
                extracted = ContactExtractor.extract_from_merchant_data(
                    remarks=adv_data.remarks or "",
                    auto_reply=adv_data.autoReplyMsg or "",
                )

        for ext in extracted:
            val_clean = ext.value.strip()
            norm_key = _normalize_contact_key(ext.type, val_clean)
            if norm_key not in existing_contacts_map:
                contact = Contact(
                    merchant_id=merchant.id,
                    type=ext.type,
                    value=val_clean,
                    raw_match=ext.raw_match,
                    first_seen_at=now,
                )
                self.session.add(contact)
                new_contacts.append(contact)
                existing_contacts_map[norm_key] = contact

        # Process Advertisement
        if adv_data.advNo in self._ads_cache:
            existing_ad = self._ads_cache[adv_data.advNo]
        else:
            ad_stmt = select(Advertisement).where(Advertisement.adv_no == adv_data.advNo)
            ad_res = await self.session.execute(ad_stmt)
            existing_ad = ad_res.scalar_one_or_none()
            if existing_ad:
                self._ads_cache[adv_data.advNo] = existing_ad

        is_new_or_updated_ad = False
        pay_methods = []
        for method in adv_data.payMethods:
            pay_type = method.get("payType") or method.get("identifier")
            name = (
                method.get("tradeMethodName")
                or method.get("tradeMethodShortName")
                or method.get("payTypeStr")
                or method.get("identifier")
                or method.get("payType")
            )
            if pay_type or name:
                pay_methods.append({"payType": pay_type, "payMethodName": name})

        if not existing_ad:
            is_new_or_updated_ad = True
            ad = Advertisement(
                adv_no=adv_data.advNo,
                merchant_id=merchant.id,
                asset=adv_data.asset,
                fiat=adv_data.fiatUnit,
                trade_type=adv_data.tradeType,
                price=adv_data.price,
                min_amount=adv_data.minSingleTransAmount,
                max_amount=adv_data.maxSingleTransAmount,
                pay_methods=pay_methods,
                remarks=adv_data.remarks,
                auto_reply=adv_data.autoReplyMsg,
                first_seen_at=now,
                last_seen_at=now,
                is_active=True,
                detail_checked_at=detail_checked_at,
            )
            self.session.add(ad)
            self._ads_cache[adv_data.advNo] = ad
        else:
            if existing_ad.price != adv_data.price:
                is_new_or_updated_ad = True
            existing_ad.price = adv_data.price
            existing_ad.min_amount = adv_data.minSingleTransAmount
            existing_ad.max_amount = adv_data.maxSingleTransAmount
            existing_ad.pay_methods = pay_methods
            existing_ad.remarks = adv_data.remarks
            existing_ad.auto_reply = adv_data.autoReplyMsg
            existing_ad.last_seen_at = now
            existing_ad.is_active = True
            if detail_checked_at is not None:
                existing_ad.detail_checked_at = detail_checked_at
            ad = existing_ad

        await self.session.flush()
        return merchant, is_new_merchant, new_contacts, is_new_or_updated_ad, ad

    async def observe_for_profile(
        self,
        profile_id: int,
        merchant_id: int,
        advertisement_id: int,
        seen_at: datetime,
    ) -> tuple[bool, bool]:
        pm_key = (profile_id, merchant_id)
        if pm_key in self._profile_merchants_cache:
            pm = self._profile_merchants_cache[pm_key]
            is_new_profile_merchant = False
            pm.last_seen_at = seen_at
            pm.is_active = True
        else:
            pm_res = await self.session.execute(
                select(ProfileMerchant).where(
                    ProfileMerchant.profile_id == profile_id,
                    ProfileMerchant.merchant_id == merchant_id,
                )
            )
            pm = pm_res.scalar_one_or_none()
            is_new_profile_merchant = pm is None
            if pm is None:
                pm = ProfileMerchant(
                    profile_id=profile_id,
                    merchant_id=merchant_id,
                    first_seen_at=seen_at,
                    last_seen_at=seen_at,
                    is_active=True,
                )
                self.session.add(pm)
            else:
                pm.last_seen_at = seen_at
                pm.is_active = True
            self._profile_merchants_cache[pm_key] = pm

        pa_key = (profile_id, advertisement_id)
        if pa_key in self._profile_ads_cache:
            pa = self._profile_ads_cache[pa_key]
            is_new_profile_ad = False
            pa.last_seen_at = seen_at
            pa.is_active = True
        else:
            pa_res = await self.session.execute(
                select(ProfileAdvertisement).where(
                    ProfileAdvertisement.profile_id == profile_id,
                    ProfileAdvertisement.advertisement_id == advertisement_id,
                )
            )
            pa = pa_res.scalar_one_or_none()
            is_new_profile_ad = pa is None
            if pa is None:
                pa = ProfileAdvertisement(
                    profile_id=profile_id,
                    advertisement_id=advertisement_id,
                    first_seen_at=seen_at,
                    last_seen_at=seen_at,
                    is_active=True,
                )
                self.session.add(pa)
            else:
                pa.last_seen_at = seen_at
                pa.is_active = True
            self._profile_ads_cache[pa_key] = pa

        return is_new_profile_merchant, is_new_profile_ad

    async def deactivate_missing_for_profile(self, profile_id: int, scan_started_at: datetime) -> None:
        await self.session.execute(
            update(ProfileMerchant)
            .where(ProfileMerchant.profile_id == profile_id, ProfileMerchant.last_seen_at < scan_started_at)
            .values(is_active=False)
        )
        await self.session.execute(
            update(ProfileAdvertisement)
            .where(ProfileAdvertisement.profile_id == profile_id, ProfileAdvertisement.last_seen_at < scan_started_at)
            .values(is_active=False)
        )
        await self.session.execute(
            update(Merchant).values(
                is_active=exists().where(
                    ProfileMerchant.merchant_id == Merchant.id,
                    ProfileMerchant.is_active.is_(True),
                )
            )
        )
        await self.session.execute(
            update(Advertisement).values(
                is_active=exists().where(
                    ProfileAdvertisement.advertisement_id == Advertisement.id,
                    ProfileAdvertisement.is_active.is_(True),
                )
            )
        )

    async def get_all_merchants(
        self,
        limit: int = 10,
        offset: int = 0,
        search_query: Optional[str] = None,
        only_verified: bool = False,
        only_with_contacts: bool = False,
    ) -> Tuple[List[Merchant], int]:
        query = select(Merchant).options(selectinload(Merchant.contacts), selectinload(Merchant.advertisements))
        count_query = select(func.count(Merchant.id))

        filters = []
        if search_query:
            term = f"%{search_query}%"
            filters.append(
                Merchant.nickname.ilike(term)
                | Merchant.user_no.ilike(term)
                | Merchant.remarks.ilike(term)
            )

        if only_verified:
            filters.append(
                Merchant.user_type.ilike("%merchant%") | Merchant.user_type.ilike("%pro%")
            )

        if only_with_contacts:
            query = query.join(Merchant.contacts)
            count_query = count_query.join(Merchant.contacts)

        if filters:
            for f in filters:
                query = query.where(f)
                count_query = count_query.where(f)

        if only_with_contacts:
            query = query.distinct()
            count_query = select(func.count(func.distinct(Merchant.id))).join(Merchant.contacts)

        total_res = await self.session.execute(count_query)
        total_count = total_res.scalar_one()

        query = query.order_by(Merchant.last_seen_at.desc()).offset(offset).limit(limit)
        res = await self.session.execute(query)
        merchants = list(res.scalars().all())

        return merchants, total_count

    async def get_all(
        self, limit: int = 10, offset: int = 0, only_verified: bool = False, only_with_contacts: bool = False
    ) -> List[Merchant]:
        merchants, _ = await self.get_all_merchants(
            limit=limit, offset=offset, only_verified=only_verified, only_with_contacts=only_with_contacts
        )
        return merchants

    async def get_merchants_by_profile_id(
        self, profile_id: int, only_with_contacts: bool = False
    ) -> List[Merchant]:
        """Fetch all merchants observed for a specific monitoring profile."""
        query = (
            select(Merchant)
            .join(ProfileMerchant, ProfileMerchant.merchant_id == Merchant.id)
            .where(ProfileMerchant.profile_id == profile_id)
            .options(selectinload(Merchant.contacts), selectinload(Merchant.advertisements))
            .order_by(Merchant.last_seen_at.desc())
        )
        if only_with_contacts:
            query = query.join(Merchant.contacts).distinct()
        res = await self.session.execute(query)
        return list(res.scalars().all())

    async def add_manual_contact(self, merchant_id: int, contact_type: str, contact_value: str) -> Contact:
        now = datetime.now(timezone.utc)
        contact = Contact(
            merchant_id=merchant_id,
            type=contact_type,
            value=contact_value,
            raw_match="manual_entry",
            first_seen_at=now,
        )
        self.session.add(contact)
        await self.session.commit()
        return contact

    async def delete_merchant(self, merchant_id: int) -> bool:
        stmt = delete(Merchant).where(Merchant.id == merchant_id)
        res = await self.session.execute(stmt)
        await self.session.commit()
        return res.rowcount > 0

    async def clear_all_merchants(self) -> int:
        stmt = delete(Merchant)
        res = await self.session.execute(stmt)
        await self.session.commit()
        return res.rowcount
