import logging
from datetime import datetime, timezone
from typing import List, Optional, Tuple

from sqlalchemy import delete, exists, func, select, update
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.db.models import (
    Advertisement,
    Contact,
    Merchant,
    ProfileAdvertisement,
    ProfileMerchant,
)
from app.providers.binance.models import BinanceSearchItem
from app.services.contact_extractor import ContactExtractor, ExtractedContact

logger = logging.getLogger(__name__)

class MerchantRepository:
    def __init__(self, session: AsyncSession):
        self.session = session

    async def get_by_id(self, merchant_id: int) -> Optional[Merchant]:
        stmt = (
            select(Merchant)
            .where(Merchant.id == merchant_id)
            .options(selectinload(Merchant.contacts), selectinload(Merchant.advertisements))
        )
        res = await self.session.execute(stmt)
        return res.scalar_one_or_none()

    async def get_by_user_no(self, user_no: str) -> Optional[Merchant]:
        stmt = (
            select(Merchant)
            .where(Merchant.user_no == user_no)
            .options(selectinload(Merchant.contacts), selectinload(Merchant.advertisements))
        )
        res = await self.session.execute(stmt)
        return res.scalar_one_or_none()

    async def process_binance_item(
        self, item: BinanceSearchItem,
        detail_checked_at: datetime | None = None,
    ) -> Tuple[Merchant, bool, List[Contact], bool, Advertisement]:
        adv_data = item.adv
        advertiser_data = item.advertiser

        user_no = advertiser_data.userNo
        existing_merchant = await self.get_by_user_no(user_no)

        is_new_merchant = False
        new_contacts: List[Contact] = []
        now = datetime.now(timezone.utc)
        existing_contacts_map = {}

        if not existing_merchant:
            is_new_merchant = True
            merchant = Merchant(
                user_no=user_no,
                nickname=advertiser_data.nickName,
                user_type=advertiser_data.userType,
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
            
            existing_contacts_map = {
                f"{c.type}:{c.value.lower()}": c for c in (merchant.contacts or [])
            }

        # Extract Contacts from nickname, remarks, autoReplyMsg
        extracted: List[ExtractedContact] = ContactExtractor.extract_from_merchant_data(
            nickname=merchant.nickname,
            remarks=adv_data.remarks,
            auto_reply=adv_data.autoReplyMsg,
        )

        for ext in extracted:
            key = f"{ext.type}:{ext.value.lower()}"
            if key not in existing_contacts_map:
                contact = Contact(
                    merchant_id=merchant.id,
                    type=ext.type,
                    value=ext.value,
                    raw_match=ext.raw_match,
                    first_seen_at=now,
                )
                self.session.add(contact)
                new_contacts.append(contact)
                existing_contacts_map[key] = contact

        # Process Advertisement
        ad_stmt = select(Advertisement).where(Advertisement.adv_no == adv_data.advNo)
        ad_res = await self.session.execute(ad_stmt)
        existing_ad = ad_res.scalar_one_or_none()

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
        pm_res = await self.session.execute(
            select(ProfileMerchant).where(
                ProfileMerchant.profile_id == profile_id,
                ProfileMerchant.merchant_id == merchant_id,
            )
        )
        pm = pm_res.scalar_one_or_none()
        is_new_profile_merchant = pm is None
        if pm is None:
            self.session.add(ProfileMerchant(
                profile_id=profile_id,
                merchant_id=merchant_id,
                first_seen_at=seen_at,
                last_seen_at=seen_at,
                is_active=True,
            ))
        else:
            pm.last_seen_at = seen_at
            pm.is_active = True

        pa_res = await self.session.execute(
            select(ProfileAdvertisement).where(
                ProfileAdvertisement.profile_id == profile_id,
                ProfileAdvertisement.advertisement_id == advertisement_id,
            )
        )
        pa = pa_res.scalar_one_or_none()
        is_new_profile_ad = pa is None
        if pa is None:
            self.session.add(ProfileAdvertisement(
                profile_id=profile_id,
                advertisement_id=advertisement_id,
                first_seen_at=seen_at,
                last_seen_at=seen_at,
                is_active=True,
            ))
        else:
            pa.last_seen_at = seen_at
            pa.is_active = True

        await self.session.flush()
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
