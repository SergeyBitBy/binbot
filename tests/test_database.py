import pytest
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.orm import sessionmaker

from app.db.models import Base
from app.db.repositories.merchant_repo import MerchantRepository
from app.providers.binance.models import BinanceAd, BinanceAdvertiser, BinanceSearchItem

@pytest.mark.asyncio
async def test_merchant_deduplication():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:", echo=False)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    async_session = sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)

    item = BinanceSearchItem(
        adv=BinanceAd(
            advNo="adv999",
            price=40.0,
            tradeType="BUY",
            asset="USDT",
            fiatUnit="UAH",
            remarks="Контакты: @merchant_tg",
            payMethods=[],
        ),
        advertiser=BinanceAdvertiser(
            userNo="user_unique_99",
            nickName="MerchantNinjas",
            monthOrderCount=50,
            monthFinishRate=0.99,
        ),
    )

    async with async_session() as session:
        repo = MerchantRepository(session)
        # First insertion
        m1, is_new1, contacts1, _ad1, _ = await repo.process_binance_item(item)
        assert is_new1 is True
        assert len(contacts1) == 1
        assert contacts1[0].value == "@merchant_tg"

        # Second insertion (Same userNo) -> should deduplicate
        m2, is_new2, contacts2, _ad2, _ = await repo.process_binance_item(item)
        assert is_new2 is False
        assert len(contacts2) == 0
        assert m1.id == m2.id

    await engine.dispose()
