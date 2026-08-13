import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.orm import sessionmaker

from app.db.models import (
    AllowedChat,
    Base,
    MonitoringProfile,
    NotificationDelivery,
    NotificationOutbox,
)
from app.db.repositories.profile_repo import ProfileRepository
from app.providers.binance.models import BinanceSearchResponse
from app.providers.binance.client import DetailFetchResult
from app.providers.binance.provider import BinanceP2PProvider, FetchResult
from app.services.monitoring_service import MonitoringService
from app.services.notification_service import NotificationService


def _response(page: int, count: int, total: int = 3) -> BinanceSearchResponse:
    items = []
    for index in range(count):
        number = page * 100 + index
        items.append({
            "adv": {
                "advNo": str(number),
                "price": "40.0",
                "tradeType": "BUY",
                "asset": "USDT",
                "fiatUnit": "UAH",
                "payMethods": [],
            },
            "advertiser": {"userNo": f"user-{number}"},
        })
    return BinanceSearchResponse.model_validate({
        "code": "000000", "success": True, "total": total, "data": items,
    })


@pytest.mark.asyncio
async def test_pagination_reaches_reported_total(monkeypatch):
    monkeypatch.setattr("app.providers.binance.provider.settings.binance_rate_limit_delay", 0)
    class Client:
        async def search_ads(self, payload):
            return _response(payload["page"], 2 if payload["page"] == 1 else 1).model_dump(mode="json")

    result = await BinanceP2PProvider(Client()).fetch_all_pages(
        "USDT", "UAH", "BUY", max_pages=5, rows_per_page=2
    )
    assert result.complete is True
    assert result.expected_total == 3
    assert result.pages_fetched == 2
    assert len(result.items) == 3


@pytest.mark.asyncio
async def test_pagination_error_is_partial_not_success(monkeypatch):
    monkeypatch.setattr("app.providers.binance.provider.settings.binance_rate_limit_delay", 0)
    class Client:
        async def search_ads(self, payload):
            if payload["page"] == 2:
                raise RuntimeError("temporary failure")
            return _response(1, 2, total=5).model_dump(mode="json")

    result = await BinanceP2PProvider(Client()).fetch_all_pages(
        "USDT", "UAH", "BUY", max_pages=5, rows_per_page=2
    )
    assert result.complete is False
    assert len(result.items) == 2
    assert "temporary failure" in result.error


def test_notification_html_escapes_external_values():
    text = NotificationService._format({
        "nickname": "<b>attacker</b>",
        "user_no": "abc' onclick='x",
        "profile_name": "A & B",
        "contacts": [{"type": "telegram", "value": "<script>"}],
    }, "NEW_CONTACTS")
    assert "<script>" not in text
    assert "&lt;script&gt;" in text
    assert "&lt;b&gt;attacker&lt;/b&gt;" in text
    assert "A &amp; B" in text


@pytest.mark.asyncio
async def test_profile_lease_is_atomic_for_second_claim():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    sessions = sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    async with sessions() as session:
        profile = MonitoringProfile(name="lease", is_active=True, scan_interval_seconds=60)
        session.add(profile)
        await session.commit()
        profile_id = profile.id
    async with sessions() as first:
        claimed = await ProfileRepository(first).claim_for_scan(profile_id, force=True)
    async with sessions() as second:
        rejected = await ProfileRepository(second).claim_for_scan(profile_id, force=True)
    assert claimed is not None
    assert rejected is None
    await engine.dispose()


@pytest.mark.asyncio
async def test_outbox_creates_per_chat_delivery_and_deduplicates():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    sessions = sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    async with sessions() as session:
        profile = MonitoringProfile(name="outbox", is_active=True)
        session.add_all([profile, AllowedChat(chat_id=100), AllowedChat(chat_id=200)])
        await session.flush()
        kwargs = {
            "event_type": "NEW_MERCHANT",
            "profile_id": profile.id,
            "merchant_id": None,
            "payload": {"user_no": "u1"},
            "deduplication_key": "same-event",
        }
        await NotificationService.enqueue(session, **kwargs)
        await NotificationService.enqueue(session, **kwargs)
        await session.commit()
        events = list((await session.execute(select(NotificationOutbox))).scalars())
        deliveries = list((await session.execute(select(NotificationDelivery))).scalars())
    assert len(events) == 1
    assert {delivery.chat_id for delivery in deliveries} == {100, 200}
    await engine.dispose()


@pytest.mark.asyncio
async def test_monitoring_baseline_then_new_merchant_creates_outbox(monkeypatch):
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    sessions = sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    monkeypatch.setattr("app.services.monitoring_service.AsyncSessionLocal", sessions)

    async with sessions() as session:
        session.add_all([
            MonitoringProfile(
                name="integration",
                asset="USDT",
                fiat="UAH",
                trade_type="BUY",
                is_active=True,
                is_baseline_completed=False,
                scan_interval_seconds=60,
            ),
            AllowedChat(chat_id=123),
        ])
        await session.commit()

    first = _response(1, 1, total=1).data
    second = _response(2, 1, total=1).data

    class Client:
        async def get_adv_detail(self, adv_no):
            return DetailFetchResult(success=True, data={"adv": {}})

    class Provider:
        client = Client()
        calls = 0

        async def fetch_all_pages(self, **kwargs):
            self.calls += 1
            items = first if self.calls == 1 else first + second
            return FetchResult(
                items=items,
                expected_total=len(items),
                pages_fetched=1,
                complete=True,
            )

    class Sheets:
        async def is_auto_export_enabled(self):
            return False

    service = MonitoringService(provider=Provider(), sheets_service=Sheets())
    first_scan = await service.scan_profile(1, trigger="manual", force=True)
    second_scan = await service.scan_profile(1, trigger="manual", force=True)

    assert first_scan.status == "SUCCESS"
    assert second_scan.status == "SUCCESS"
    assert second_scan.new_merchants_count == 1
    async with sessions() as session:
        events = list((await session.execute(select(NotificationOutbox))).scalars())
        deliveries = list((await session.execute(select(NotificationDelivery))).scalars())
    assert len(events) == 1
    assert events[0].event_type == "NEW_MERCHANT"
    assert [delivery.chat_id for delivery in deliveries] == [123]
    await engine.dispose()
