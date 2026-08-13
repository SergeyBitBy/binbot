import logging
from datetime import datetime, timedelta, timezone

from sqlalchemy import or_, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import MonitoringProfile, ScanHistory

logger = logging.getLogger(__name__)

class ProfileRepository:
    def __init__(self, session: AsyncSession):
        self.session = session

    async def get_all(self, only_active: bool = False) -> list[MonitoringProfile]:
        stmt = select(MonitoringProfile)
        if only_active:
            stmt = stmt.where(MonitoringProfile.is_active.is_(True))
        stmt = stmt.order_by(MonitoringProfile.id)
        res = await self.session.execute(stmt)
        return list(res.scalars().all())

    async def get_by_id(self, profile_id: int) -> MonitoringProfile | None:
        stmt = select(MonitoringProfile).where(MonitoringProfile.id == profile_id)
        res = await self.session.execute(stmt)
        return res.scalar_one_or_none()

    async def get_by_name(self, name: str) -> MonitoringProfile | None:
        stmt = select(MonitoringProfile).where(MonitoringProfile.name == name)
        res = await self.session.execute(stmt)
        return res.scalar_one_or_none()

    async def create(
        self,
        name: str,
        asset: str = "USDT",
        fiat: str = "UAH",
        trade_type: str = "BUY",
        pay_types: list[str] | None = None,
        trans_amount: str | None = None,
        merchant_check: bool = False,
        scan_interval_seconds: int = 60,
    ) -> MonitoringProfile:
        profile = MonitoringProfile(
            name=name,
            asset=asset,
            fiat=fiat,
            trade_type=trade_type,
            pay_types=pay_types or [],
            trans_amount=trans_amount,
            merchant_check=merchant_check,
            scan_interval_seconds=scan_interval_seconds,
            is_active=True,
            is_locked=False,
            is_baseline_completed=False,
        )
        self.session.add(profile)
        await self.session.commit()
        await self.session.refresh(profile)
        return profile

    async def update_lock(self, profile_id: int, is_locked: bool):
        profile = await self.get_by_id(profile_id)
        if profile:
            profile.is_locked = is_locked
            await self.session.commit()

    async def claim_for_scan(self, profile_id: int, *, force: bool = False, lease_seconds: int = 300) -> MonitoringProfile | None:
        now = datetime.now(timezone.utc)
        conditions = [
            MonitoringProfile.id == profile_id,
            MonitoringProfile.is_active.is_(True),
            or_(MonitoringProfile.locked_until.is_(None), MonitoringProfile.locked_until < now),
        ]
        if not force:
            conditions.append(or_(MonitoringProfile.next_scan_at.is_(None), MonitoringProfile.next_scan_at <= now))
        result = await self.session.execute(
            update(MonitoringProfile)
            .where(*conditions)
            .values(
                is_locked=True,
                locked_until=now + timedelta(seconds=lease_seconds),
                last_scan_started_at=now,
            )
        )
        if result.rowcount != 1:
            await self.session.rollback()
            return None
        await self.session.commit()
        return await self.get_by_id(profile_id)

    async def release_after_scan(self, profile_id: int, interval_seconds: int) -> None:
        now = datetime.now(timezone.utc)
        await self.session.execute(
            update(MonitoringProfile)
            .where(MonitoringProfile.id == profile_id)
            .values(
                is_locked=False,
                locked_until=None,
                last_scan_finished_at=now,
                next_scan_at=now + timedelta(seconds=max(10, interval_seconds)),
            )
        )
        await self.session.commit()

    async def get_due(self) -> list[MonitoringProfile]:
        now = datetime.now(timezone.utc)
        result = await self.session.execute(
            select(MonitoringProfile)
            .where(
                MonitoringProfile.is_active.is_(True),
                or_(MonitoringProfile.next_scan_at.is_(None), MonitoringProfile.next_scan_at <= now),
                or_(MonitoringProfile.locked_until.is_(None), MonitoringProfile.locked_until < now),
            )
            .order_by(MonitoringProfile.next_scan_at, MonitoringProfile.id)
        )
        return list(result.scalars().all())

    async def mark_baseline_completed(self, profile_id: int):
        profile = await self.get_by_id(profile_id)
        if profile:
            profile.is_baseline_completed = True
            await self.session.commit()

    async def delete(self, profile_id: int) -> bool:
        profile = await self.get_by_id(profile_id)
        if profile:
            await self.session.delete(profile)
            await self.session.commit()
            return True
        return False

    async def add_scan_history(self, history: ScanHistory):
        self.session.add(history)
        await self.session.commit()
