import os
from datetime import datetime, timezone
from types import SimpleNamespace

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.orm import sessionmaker

from app.bot.access import (
    can_change_role,
    can_delete_user,
    is_action_allowed,
    normalize_role,
)
from app.bot.handlers.chats import add_allowed_chat
from app.bot.keyboards.main_kb import get_main_menu_keyboard
from app.config.logging import _prune_logs
from app.db.models import (
    AllowedChat,
    Base,
    MonitoringProfile,
    NotificationDelivery,
    NotificationOutbox,
)
from app.db.repositories.profile_repo import ProfileRepository
from app.providers.binance.client import DetailFetchResult
from app.providers.binance.models import BinanceSearchResponse
from app.providers.binance.provider import BinanceP2PProvider, FetchResult
from app.services.monitoring_service import MonitoringService
from app.services.notification_service import NotificationService
from app.services.sheets_service import GoogleSheetsService


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


def test_new_contact_notification_contains_full_advertisement_details():
    text = NotificationService._format({
        "nickname": "Trader",
        "user_no": "user-1",
        "profile_name": "USDT BUY",
        "asset": "USDT",
        "fiat": "UAH",
        "price": "46.50",
        "min_amount": "4000",
        "max_amount": "335000",
        "month_order_count": 208,
        "month_finish_rate": 0.986,
        "pay_methods": ["Monobank (Card)", "PrivatBank"],
        "remarks": "Новое описание объявления",
        "auto_reply": "Новый автоответ",
        "contacts": [{"type": "telegram", "value": "@trader"}],
    }, "NEW_CONTACTS")
    assert "46.50" in text
    assert "4000 - 335000 UAH" in text
    assert "208 (98.6%)" in text
    assert "Monobank (Card), PrivatBank" in text
    assert "Новое описание объявления" in text
    assert "Новый автоответ" in text


def test_sheets_dates_are_converted_from_utc_to_kyiv(monkeypatch):
    monkeypatch.setattr("app.services.sheets_service.settings.timezone", "Europe/Kyiv")
    merchant = SimpleNamespace(
        user_no="u1",
        nickname="Trader",
        user_type="merchant",
        month_order_count=1,
        month_finish_rate=1.0,
        remarks="",
        first_seen_at=datetime(2026, 8, 13, 22, 14, tzinfo=timezone.utc),
        last_seen_at=datetime(2026, 8, 13, 22, 14),  # noqa: DTZ001 - SQLite returns naive UTC
    )
    columns = [{"key": "first_seen"}, {"key": "last_seen"}]
    assert GoogleSheetsService()._build_row(merchant, [], columns) == [
        "2026-08-14 01:14",
        "2026-08-14 01:14",
    ]


def test_sheets_contact_column_contains_values_only():
    merchant = SimpleNamespace(
        user_no="u1",
        nickname="Trader",
        user_type="merchant",
        month_order_count=1,
        month_finish_rate=1.0,
        remarks="",
        first_seen_at=None,
        last_seen_at=None,
    )
    contacts = [
        SimpleNamespace(type="telegram", value="@trader"),
        SimpleNamespace(type="phone", value="+380501234567"),
    ]
    assert GoogleSheetsService()._build_row(merchant, contacts, [{"key": "contacts"}]) == [
        "@trader, +380501234567"
    ]


def test_role_permissions_are_enforced_fail_closed():
    assert normalize_role("unexpected") == "viewer"
    assert is_action_allowed("superadmin", callback_data="menu_settings")
    assert not is_action_allowed("admin", callback_data="menu_settings")
    assert is_action_allowed("admin", callback_data="prof_toggle_1")
    assert not is_action_allowed("viewer", callback_data="prof_toggle_1")
    assert is_action_allowed("viewer", callback_data="merch_card_1_1_all")
    assert not is_action_allowed("viewer", state="MerchantEditForm:contact_value")
    assert not is_action_allowed("admin", state="GoogleSheetsForm:sheet_id")


def test_main_menu_hides_actions_unavailable_to_role():
    viewer_callbacks = {
        button.callback_data
        for row in get_main_menu_keyboard(role="viewer").inline_keyboard
        for button in row
    }
    admin_callbacks = {
        button.callback_data
        for row in get_main_menu_keyboard(role="admin").inline_keyboard
        for button in row
    }
    superadmin_callbacks = {
        button.callback_data
        for row in get_main_menu_keyboard(role="superadmin").inline_keyboard
        for button in row
    }
    assert "menu_settings" not in viewer_callbacks
    assert "menu_scan_now" not in viewer_callbacks
    assert "menu_scan_now" in admin_callbacks
    assert "menu_settings" not in admin_callbacks
    assert {"menu_settings", "menu_admins", "menu_chats"} <= superadmin_callbacks


def test_superadmin_safety_guards():
    assert can_change_role(
        actor_user_id=1,
        target_user_id=2,
        current_role="admin",
        new_role="superadmin",
        superadmin_count=1,
    )
    assert not can_change_role(
        actor_user_id=1,
        target_user_id=1,
        current_role="superadmin",
        new_role="admin",
        superadmin_count=2,
    )
    assert not can_change_role(
        actor_user_id=1,
        target_user_id=2,
        current_role="superadmin",
        new_role="viewer",
        superadmin_count=1,
    )
    assert can_change_role(
        actor_user_id=1,
        target_user_id=2,
        current_role="superadmin",
        new_role="viewer",
        superadmin_count=2,
    )
    assert not can_delete_user(
        actor_user_id=1,
        target_user_id=1,
        target_role="superadmin",
        superadmin_count=2,
    )
    assert not can_delete_user(
        actor_user_id=1,
        target_user_id=2,
        target_role="superadmin",
        superadmin_count=1,
    )
    assert can_delete_user(
        actor_user_id=1,
        target_user_id=2,
        target_role="superadmin",
        superadmin_count=2,
    )


def test_log_pruning_removes_oldest_archives_first(tmp_path):
    active = tmp_path / "bot.log"
    old = tmp_path / "bot.log.2026-08-01.gz"
    recent = tmp_path / "bot.log.1.gz"
    active.write_bytes(b"a" * 40)
    old.write_bytes(b"b" * 40)
    recent.write_bytes(b"c" * 40)
    old.touch()
    recent.touch()
    old_mtime = old.stat().st_mtime - 100
    recent_mtime = recent.stat().st_mtime
    os.utime(old, (old_mtime, old_mtime))
    os.utime(recent, (recent_mtime, recent_mtime))

    _prune_logs(tmp_path, 90)

    assert active.exists()
    assert not old.exists()
    assert recent.exists()


@pytest.mark.asyncio
async def test_allowed_chat_addition_is_idempotent(monkeypatch):
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    sessions = sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    monkeypatch.setattr("app.bot.handlers.chats.AsyncSessionLocal", sessions)

    assert await add_allowed_chat(-5395511036) is True
    assert await add_allowed_chat(-5395511036) is False
    async with sessions() as session:
        chats = list((await session.execute(select(AllowedChat.chat_id))).scalars())
    assert chats == [-5395511036]
    await engine.dispose()


@pytest.mark.asyncio
async def test_notification_worker_backfills_new_allowed_chat(monkeypatch):
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    sessions = sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    monkeypatch.setattr("app.services.notification_service.AsyncSessionLocal", sessions)

    async with sessions() as session:
        profile = MonitoringProfile(name="backfill", is_active=True)
        session.add_all([profile, AllowedChat(chat_id=100)])
        await session.flush()
        await NotificationService.enqueue(
            session,
            event_type="NEW_MERCHANT",
            profile_id=profile.id,
            merchant_id=None,
            payload={"user_no": "u1"},
            deduplication_key="backfill-new-chat",
        )
        await session.commit()
    async with sessions() as session:
        session.add(AllowedChat(chat_id=200))
        await session.commit()

    class Bot:
        def __init__(self):
            self.chat_ids = []

        async def send_message(self, *, chat_id, **kwargs):
            self.chat_ids.append(chat_id)

    bot = Bot()
    await NotificationService(bot=bot).process_pending()
    assert set(bot.chat_ids) == {100, 200}
    await engine.dispose()


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
async def test_profile_get_or_create_is_idempotent():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    sessions = sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    async with sessions() as session:
        repo = ProfileRepository(session)
        first, first_created = await repo.get_or_create(
            name="USDT/EUR", asset="USDT", fiat="EUR", trade_type="BUY"
        )
        second, second_created = await repo.get_or_create(
            name="USDT/EUR", asset="USDT", fiat="EUR", trade_type="BUY"
        )
    assert first_created is True
    assert second_created is False
    assert first.id == second.id
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
