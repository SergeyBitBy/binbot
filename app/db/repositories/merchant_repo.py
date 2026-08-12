import logging
from datetime import datetime, timezone

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.db.models import Advertisement, Contact, Merchant
from app.providers.binance.models import BinanceSearchItem
from app.services.contact_extractor import ContactExtractor, ExtractedContact

logger = logging.getLogger(__name__)

class MerchantRepository:
    def __init__(self, session: AsyncSession):
        self.session = session

    async def get_by_user_no(self, user_no: str) -> Merchant | None:
        stmt = (
            select(Merchant)
            .where(Merchant.user_no == user_no)
            .options(selectinload(Merchant.contacts), selectinload(Merchant.advertisements))
        )
        res = await self.session.execute(stmt)
        return res.scalar_one_or_none()

    async def process_binance_item(
        self, item: BinanceSearchItem
    ) -> tuple[Merchant, bool, list[Contact], bool]:
        adv_data = item.adv
        advertiser_data = item.advertiser

        user_no = advertiser_data.userNo
        existing_merchant = await self.get_by_user_no(user_no)

        is_new_merchant = False
        new_contacts: list[Contact] = []
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
        extracted: list[ExtractedContact] = ContactExtractor.extract_from_merchant_data(
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
        pay_methods = [
            {"payType": p.get("payType"), "payMethodName": p.get("payTypeStr")}
            for p in adv_data.payMethods
        ]

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

        await self.session.flush()
        return merchant, is_new_merchant, new_contacts, is_new_or_updated_ad

    async def get_all_merchants(
        self, limit: int = 50, offset: int = 0, search_query: str | None = None
    ) -> tuple[list[Merchant], int]:
        query = select(Merchant).options(selectinload(Merchant.contacts), selectinload(Merchant.advertisements))
        count_query = select(func.count(Merchant.id))

        if search_query:
            term = f"%{search_query}%"
            filter_clause = (
                Merchant.nickname.ilike(term)
                | Merchant.user_no.ilike(term)
                | Merchant.remarks.ilike(term)
            )
            query = query.where(filter_clause)
            count_query = count_query.where(filter_clause)

        total_res = await self.session.execute(count_query)
        total_count = total_res.scalar_one()

        query = query.order_by(Merchant.last_seen_at.desc()).offset(offset).limit(limit)
        res = await self.session.execute(query)
        merchants = list(res.scalars().all())

        return merchants, total_count
